"""External World Watcher Daemon.

Polls configured external sources (RSS feeds, JSON APIs) in the background,
deduplicates items in SQLite (`world_seen_items`), filters by keywords, and
spawns the ephemeral `world_watcher` LLM agent ONLY when new matching external
information arrives.
"""

import hashlib
import json
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import RoleName
from core.providers import EphemeralAgentRunner


class WorldWatcherDaemon:
    """Lightweight external world monitor that spawns `world_watcher` on new signal."""

    def __init__(
        self,
        workspace_root: Path,
        config: Dict[str, Any],
        db: WorkspaceDatabase,
        log_manager: ContextLogManager,
        runner: EphemeralAgentRunner,
        notify_callback: Optional[Callable[[str], None]] = None,
        task_spawn_callback: Optional[Callable[[str, str], None]] = None,
        provider_override: Optional[str] = None,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.config = config
        self.world_cfg = config.get("watchers", {}).get("world", {})
        self.db = db
        self.log_manager = log_manager
        self.runner = runner
        self.notify_callback = notify_callback
        self.task_spawn_callback = task_spawn_callback
        self.provider_override = provider_override

    def check_once(self, force_items: Optional[List[Dict[str, str]]] = None) -> Optional[str]:
        """Poll enabled external sources (or injected items) and spawn `world_watcher` if new."""
        new_items: List[Dict[str, str]] = list(force_items or [])

        for src in self.world_cfg.get("sources", []):
            if not src.get("enabled", False):
                continue
            src_name = src.get("name", "external_source")
            src_type = src.get("type", "rss")
            url = src.get("url", "")
            keywords = [k.lower() for k in src.get("keywords", [])]
            if not url:
                continue

            try:
                fetched = self._fetch_source(src_name, src_type, url, keywords)
                new_items.extend(fetched)
            except Exception as exc:
                self.db.log_event(
                    event_type="WORLD_SOURCE_ERROR",
                    actor=f"watcher:world:{src_name}",
                    severity="WARN",
                    summary=f"Failed polling external source '{src_name}': {exc}",
                )

        if not new_items:
            return None

        self.db.log_event(
            event_type="WORLD_NEW_ITEMS_DISCOVERED",
            actor="watcher:world",
            summary=f"Discovered {len(new_items)} new external world item(s); spawning world_watcher agent.",
            payload={"items": new_items[:10]},
        )

        items_text = "\n".join(
            f"- [{item.get('source', 'world')}] **{item['title']}** ({item['url']})"
            for item in new_items
        )
        role = RoleName.WORLD_WATCHER.value
        role_cfg = self.config.get("roles", {}).get(role, {})
        provider_name = self.provider_override or role_cfg.get("provider", "agy_headless_safe")
        cli_log = self.log_manager.create_agent_cli_log_path(0, role)

        run_id = self.db.record_agent_spawn(
            role=role,
            provider_name=provider_name,
            task_id=None,
            cli_log_path=str(cli_log),
        )
        res = self.runner.run_role(
            role=role,
            task_prompt=f"### Newly Discovered Outside-World Items\n{items_text}\n",
            cli_log_path=cli_log,
            provider_override=self.provider_override,
        )
        self.db.record_agent_shutdown(
            run_id=run_id,
            role=role,
            task_id=None,
            success=res.success,
            duration_seconds=res.duration_seconds,
            exit_code=res.exit_code,
            output_preview=res.output,
            error_text=res.error,
        )

        if res.success:
            upper = res.output.upper()
            if "ACTION: DIGEST" in upper and self.notify_callback:
                self.notify_callback(f"🌍 **World Watcher Digest**\n{res.output}")
            elif "ACTION: CREATE_TASK" in upper and self.task_spawn_callback:
                self.task_spawn_callback(
                    f"Follow-up from World Watcher:\n{res.output}",
                    "world_watcher",
                )
        return res.output

    def _fetch_source(
        self,
        src_name: str,
        src_type: str,
        url: str,
        keywords: List[str],
    ) -> List[Dict[str, str]]:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AntigravityWorkspaceWatcher/1.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")

        discovered: List[Dict[str, str]] = []
        if src_type == "rss":
            root = ET.fromstring(raw)
            for item in root.findall(".//item")[:15]:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not title:
                    continue
                if keywords and not any(kw in title.lower() for kw in keywords):
                    continue
                item_hash = hashlib.sha256(f"{src_name}:{link or title}".encode("utf-8")).hexdigest()[:32]
                if not self.db.is_world_item_seen(item_hash):
                    self.db.mark_world_item_seen(item_hash, src_name, title, link)
                    discovered.append({"source": src_name, "title": title, "url": link})
        elif src_type == "json_api":
            data = json.loads(raw)
            title = str(data.get("name") or data.get("title") or data.get("tag_name") or "API Update")
            link = str(data.get("html_url") or data.get("url") or url)
            item_hash = hashlib.sha256(f"{src_name}:{title}:{link}".encode("utf-8")).hexdigest()[:32]
            if not self.db.is_world_item_seen(item_hash):
                self.db.mark_world_item_seen(item_hash, src_name, title, link)
                discovered.append({"source": src_name, "title": title, "url": link})

        return discovered
