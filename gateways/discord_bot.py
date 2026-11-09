"""Bidirectional Discord Bot Gateway.

Allows users to control the autonomous workspace from Discord:
- Mention the bot: `@assistance <your task instructions>`
- Command prefix: `!task <your task instructions>`
- Direct Message (DM): Send any instruction directly to the bot in DM
- `!status`: Check active and recent tasks in SQLite
- `!help`: Show available bot commands

When a task completes, the Communicator sends the verified response directly
back to the Discord channel or DM where the request originated.
"""

import asyncio
import os
import sys
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    import discord
    from discord.ext import commands
    HAS_DISCORD_PY = True
except ImportError:
    HAS_DISCORD_PY = False


def is_approval_reply(content: str) -> bool:
    """Approval replies belong to the approval gate, never the task queue."""
    return bool(re.fullmatch(r"!?(?:approve|deny|yes|no|allow|reject|y|n)(?:\s+[a-f0-9]{6,64})?", content.strip(), re.I))


async def save_attachments(attachments, directory):
    """Keep explicit attachments as local inputs, never extract or execute them."""
    from core.inputs import MAX_INPUT_BYTES, validate_inputs
    directory = Path(directory).resolve()
    files, total, names = [], 0, set()
    # Validate all names/sizes before reading even the first attachment.
    for attachment in attachments:
        name = attachment.filename
        if (not name or name in {".", ".."} or re.search(r"[\\/:]", name)
                or name.startswith(".env") or name.endswith(".env")
                or name.casefold() in {"credentials.json", "kaggle.json", "token.json", "access_token"}
                or Path(name).suffix.lower() in {".pem", ".key", ".p12", ".pfx"}
                or name.casefold() in names):
            raise ValueError("Attachment names must be unique, plain filenames without credentials")
        names.add(name.casefold())
        total += attachment.size
        if total > MAX_INPUT_BYTES:
            raise ValueError("Attachments exceed the 50 MB total limit")
    directory.mkdir(parents=True, exist_ok=True)
    total = 0
    for attachment in attachments:
        content = await attachment.read()
        total += len(content)
        if total > MAX_INPUT_BYTES:
            raise ValueError("Downloaded attachments exceed the 50 MB total limit")
        path = directory / attachment.filename
        path.write_bytes(content)
        files.append(path)
    return validate_inputs(files)


class DiscordBotRunner:
    """Manages the Discord Bot connection, listens for tasks, and dispatches them to the workspace."""

    def __init__(
        self,
        bot_token: str,
        enqueue_task_fn: Callable[[str, str, str], Any],
        get_status_fn: Callable[[], Dict[str, Any]],
        conversation=None,
    ) -> None:
        self.bot_token = bot_token
        self.enqueue_task_fn = enqueue_task_fn
        self.get_status_fn = get_status_fn
        self.conversation = conversation
        self.allowed_user_ids = {value.strip() for value in os.environ.get("DISCORD_ALLOWED_USER_IDS", "").split(",") if value.strip()}
        self.heartbeat_task = None
        self.playground_channels: Dict[str, str] = {}
        self._announced_approvals: set[str] = set()

    @staticmethod
    def _norm_name(name: Any) -> str:
        return re.sub(r"[\s_-]+", "_", str(name).strip().lower()).strip("_") if isinstance(name, str) else ""

    def _resolve_guild_channels(self, guilds) -> Dict[str, str]:
        from core.discord_channels import ALIASES
        roles_aliases = ALIASES
        found: Dict[str, str] = {}
        for g in guilds or []:
            channels = getattr(g, "channels", []) or []
            cat_ids = {
                str(ch.id)
                for ch in channels
                if getattr(getattr(ch, "type", None), "name", "") == "category"
                and self._norm_name(getattr(ch, "name", "")) == "antigravity_playground"
            }
            for pass_in_cat in (True,):
                if not cat_ids:
                    continue
                for ch in channels:
                    cname = self._norm_name(getattr(ch, "name", ""))
                    if not cname or cname == "antigravity_playground":
                        continue
                    parent_id = str(getattr(ch, "category_id", "") or "")
                    if pass_in_cat and cat_ids and parent_id not in cat_ids:
                        continue
                    for key, names in roles_aliases.items():
                        if key not in found and cname in names:
                            found[key] = str(ch.id)
            if found:
                break
        return found

    def create_bot(self):
        """Build the production handlers separately from the gateway connection.

        This also permits explicit integration probes using real Discord messages
        without creating a second Gateway listener or weakening authorization.
        """
        if not self.allowed_user_ids:
            raise ValueError("Set DISCORD_ALLOWED_USER_IDS to the user IDs permitted to submit tasks")
        if not HAS_DISCORD_PY:
            raise RuntimeError("discord.py is not installed in the active environment")

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.dm_messages = True

        bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

        @bot.event
        async def on_ready() -> None:
            print(f"[+] Discord Bot connected as: {bot.user} (ID: {bot.user.id})")
            print(f"[+] Ready to receive tasks via:")
            print(f"    1. `!task <prompt>` in any accessible channel")
            print(f"    2. `@assistance <prompt>` mention in any channel")
            print(f"    3. Direct Message (DM) to {bot.user.name}")
            if self.heartbeat_task is None or self.heartbeat_task.done():
                self.heartbeat_task = bot.loop.create_task(heartbeat_loop())

        async def heartbeat_loop() -> None:
            hb_path = Path("storage/bot_heartbeat.json")
            hb_path.parent.mkdir(parents=True, exist_ok=True)
            import json, time
            while not bot.is_closed():
                try:
                    resolved=self._resolve_guild_channels(bot.guilds)
                    if resolved and resolved!=self.playground_channels:
                        if os.environ.get('WORKFLOW_NO_ENV')!='1':
                            from core.discord_channels import update_env
                            update_env(Path(__file__).resolve().parents[1]/'.env',resolved)
                        self.playground_channels=resolved
                    data = {
                        "status": "listening",
                        "bot_user": f"{bot.user.name}#{bot.user.discriminator}",
                        "bot_id": str(bot.user.id),
                        "guilds": [g.name for g in bot.guilds],
                        "playground_channels": self.playground_channels,
                        "pid": os.getpid(),
                        "timestamp": time.time(),
                    }
                    hb_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                except Exception:
                    pass
                if self.conversation:
                    try:
                        await asyncio.to_thread(self.conversation.recover)
                    except Exception as exc:
                        self.conversation.db.log_event('INTAKE_RECOVERY_ERROR', 'discord', str(exc), severity='WARN')
                    for item in self.conversation.pending():
                        delivery_owner=self.conversation.claim_delivery(item['event_key'])
                        if not delivery_owner:continue
                        try:
                            target=item.get('delivery_channel') or item['scope']
                            if item['event_key'].startswith('approval:'):
                                target=self.playground_channels.get('approval_list') or target
                            channel = bot.get_channel(int(target)) or await bot.fetch_channel(int(target))
                            sent = await channel.send(item['response'][:1900], allowed_mentions=discord.AllowedMentions.none())
                            self.conversation.delivered(item['event_key'], f'discord:{sent.id}',delivery_owner)
                        except Exception:
                            # Unknown send outcome: retain lease until expiry before retry.
                            pass
                    approval_ch_id = self.playground_channels.get("approval_list")
                    if approval_ch_id and approval_ch_id.isdigit():
                        try:
                            with self.conversation.db._connect() as c:
                                rows = [dict(r) for r in c.execute("SELECT p.*,a.scope FROM approvals p JOIN task_actors a ON a.task_id=p.task_id WHERE p.decision='PENDING' AND p.expires>? AND a.scope=? ORDER BY p.created DESC LIMIT 10", (time.time(),self.playground_channels.get('task_proposal','')))]
                            for r in rows:
                                if r["id"] in self._announced_approvals:
                                    continue
                                with self.conversation.db._connect() as c:
                                    exists=c.execute('SELECT 1 FROM intake_messages WHERE event_key=?',('approval:'+r['id'],)).fetchone()
                                if exists:
                                    self._announced_approvals.add(r['id']);continue
                                msg = (
                                    f"🛡️ **Approval Required (Task #{r['task_id']})**\n"
                                    f"• **Approval ID**: `{r['id']}`\n"
                                    f"• **Action**: `{r['action'][:1200]}`\n"
                                    f"👉 Gửi `!approve {r['id']}` hoặc `!deny {r['id']}` tại đây để quyết định."
                                )
                                with self.conversation.db._connect() as c:
                                    c.execute('INSERT OR IGNORE INTO intake_messages(event_key,scope,actor,message,response,task_id,delivery_channel) VALUES(?,?,?,?,?,?,?)',
                                        ('approval:'+r['id'],r['scope'],r['actor'],'[approval request]',msg,r['task_id'],approval_ch_id))
                                self._announced_approvals.add(r["id"])
                        except Exception:
                            pass
                await asyncio.sleep(5)

        @bot.event
        async def on_message(message: discord.Message) -> None:
            # Never respond to other bots (or ourselves)
            if message.author.bot:
                return
            if str(message.author.id) not in self.allowed_user_ids:
                return

            channel_id = str(message.channel.id)
            user_tag = str(message.author.id)
            content = message.content.strip()

            # Ignore standalone approval/denial replies handled by PreToolUse hook
            if is_approval_reply(content) and not self.conversation:
                return

            # 1. Direct Message (DM) to the bot: treat entire text as task
            is_dm = isinstance(message.channel, discord.DMChannel)

            # 2. Bot Mentioned: `@assistance <prompt>`
            is_mentioned = bot.user in message.mentions
            ch_name = self._norm_name(getattr(message.channel, "name", None))
            is_proposal_ch = channel_id == self.playground_channels.get("task_proposal")
            is_approval_ch = channel_id == self.playground_channels.get("approval_list")
            is_log_ch = channel_id == self.playground_channels.get("task_log")
            is_playground_ch = is_proposal_ch or is_approval_ch or is_log_ch

            if self.conversation and (is_dm or is_mentioned or is_playground_ch or content.startswith(('!task', '!status', '!search', '!feedback', '!retry', '!cancel', '!approve', '!deny', '!approvals')) or message.reference):
                # A guild reply is relevant only if it points to one of our stored task messages.
                reply_key = f'discord:{message.reference.message_id}' if message.reference else ''
                if not (is_dm or is_mentioned or is_playground_ch or content.startswith('!')):
                    with self.conversation.db._connect() as c:
                        known = c.execute('SELECT 1 FROM intake_messages WHERE event_key=? AND scope=? AND actor=?',
                                          (reply_key, channel_id, user_tag)).fetchone()
                    if not known:
                        return
                bot_id = getattr(bot.user, 'id', None)
                clean = (content.replace(f'<@{bot_id}>', '').replace(f'<@!{bot_id}>', '').strip()
                         if bot_id is not None else content)
                if is_approval_ch and is_approval_reply(clean) and not clean.startswith('!'):
                    clean = '!' + clean
                if is_approval_ch and not clean.startswith(('!approve ','!deny ','!approvals')):
                    await message.channel.send('Kênh này chỉ dùng !approvals, !approve <id> hoặc !deny <id>. Trao đổi task tại task-proposal.',allowed_mentions=discord.AllowedMentions.none())
                    return
                if is_log_ch:
                    if clean.startswith('!') and not clean.startswith(('!status','!search')):
                        await message.channel.send('Kênh này dùng !status hoặc !search <từ khóa> để xem tiến độ.',allowed_mentions=discord.AllowedMentions.none())
                        return
                    if not clean.startswith('!'):clean='!status'
                effective_scope = channel_id
                if is_approval_ch or is_log_ch:
                    proposal_scope = self.playground_channels.get("task_proposal")
                    if proposal_scope:
                        effective_scope = proposal_scope
                    else:
                        with self.conversation.db._connect() as c:
                            row = c.execute('SELECT scope FROM task_actors WHERE actor=? ORDER BY task_id DESC LIMIT 1', (user_tag,)).fetchone()
                            if row:
                                effective_scope = row['scope']
                files = []
                try:
                    if message.attachments:
                        import uuid
                        files = await save_attachments(message.attachments, Path(__file__).resolve().parent.parent / 'storage/ingress' / uuid.uuid4().hex)
                    item = await asyncio.to_thread(self.conversation.receive, clean, effective_scope, user_tag,
                        f'discord:{message.id}', reply_key, explicit=False, input_files=files,delivery_channel=channel_id)
                    delivery_owner=self.conversation.claim_delivery(item['event_key'])
                    if delivery_owner:
                        sent = await message.channel.send(item['response'][:1900], allowed_mentions=discord.AllowedMentions.none())
                        self.conversation.delivered(item['event_key'], f'discord:{sent.id}',delivery_owner)
                except ValueError as exc:
                    await message.channel.send(str(exc), allowed_mentions=discord.AllowedMentions.none())
                return

            if is_dm and not content.startswith("!"):
                await handle_incoming_prompt(message.channel, content, user_tag, str(message.id), message.attachments)
                return

            if is_mentioned and not content.startswith("!"):
                # Strip out the mention tag (e.g. `<@1563577856318119956>`)
                clean_prompt = content.replace(f"<@{bot.user.id}>", "").replace(f"<@!{bot.user.id}>", "").strip()
                if clean_prompt:
                    await handle_incoming_prompt(message.channel, clean_prompt, user_tag, str(message.id), message.attachments)
                    return

            # Process registered bot commands (!task, !status, !help)
            await bot.process_commands(message)

        async def handle_incoming_prompt(channel: Any, prompt: str, user_tag: str, message_id: str, attachments=()) -> None:
            if not prompt.strip() or is_approval_reply(prompt):
                return
            try:
                files = []
                if attachments:
                    import uuid
                    directory = Path(__file__).resolve().parent.parent / "storage/ingress" / uuid.uuid4().hex
                    files = await save_attachments(attachments, directory)
                options = {"input_files": files} if files else {}
                task = await asyncio.to_thread(self.enqueue_task_fn, prompt, "discord", str(channel.id), f"discord:{message_id}", **options)
            except ValueError as exc:
                await channel.send(f"Request could not be queued: {exc}", allowed_mentions=discord.AllowedMentions.none())
                return
            await channel.send(
                f"📥 **Task #{task.id} Forwarded to Workspace**\n"
                f"> **Prompt**: {prompt[:400]}\n"
                f"Pipeline: `Researcher` ➔ `Operator` ➔ `Validator` ➔ `Communicator`.\n"
                f"Status: `{task.status}`. Excess requests remain queued. Results will be posted here.",
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @bot.command(name="task")
        async def cmd_task(ctx: commands.Context, *, prompt: str) -> None:
            await handle_incoming_prompt(ctx.channel, prompt, str(ctx.author), str(ctx.message.id), ctx.message.attachments)

        @bot.command(name="status")
        async def cmd_status(ctx: commands.Context) -> None:
            data = self.get_status_fn()
            tasks = data.get("tasks", [])
            lines = []
            if not tasks:
                lines.append("No active or recent tasks.")
            for t in tasks[:6]:
                status_emoji = "✅" if t.status == "COMPLETED" else "⏳" if t.status in ("RESEARCHING", "OPERATING", "VALIDATING") else "⚠️"
                from core.monitoring import status_line
                line=f"{status_emoji} **Task #{t.id:03d}** `[{t.status}]`: {t.title[:55]}"
                if self.conversation:
                    line+=status_line(self.conversation.db,t.id)
                lines.append(line)

            embed = discord.Embed(
                title="Antigravity Workspace Status",
                description="\n".join(lines),
                color=0x2ECC71,
            )
            embed.set_footer(text=f"Database: storage/events.db")
            await ctx.send(embed=embed)

        @bot.command(name="retry")
        async def cmd_retry(ctx: commands.Context, task_id: int) -> None:
            await handle_incoming_prompt(ctx.channel, f"retry task {task_id}", str(ctx.author), str(ctx.message.id))

        @bot.command(name="help")
        async def cmd_help(ctx: commands.Context) -> None:
            embed = discord.Embed(
                title="Antigravity Autonomous Workspace - Discord Control",
                description="Control the multi-agent coding framework directly from Discord.",
                color=0x3498DB,
            )
            embed.add_field(
                name="💬 Submitting a Task",
                value=(
                    "• `!task <your instructions>`\n"
                    "• DM or mention the bot for conversation; this does not automatically start work.\n"
                    "• Reply to a task message with feedback, or use `!feedback <id> <change>`."
                ),
                inline=False,
            )
            embed.add_field(
                name="📊 Status & Monitoring",
                value="• `!status` / `!search <words>` - Your tasks in this channel.\n• `!retry <id>` - New run; old logs stay intact.\n• `!cancel <id>` - Stop at a role boundary.\n• `!approvals` / `!approve <id>` / `!deny <id>` - Exact-action decisions.",
                inline=False,
            )
            await ctx.send(embed=embed)

        return bot

    def run(self) -> None:
        self.create_bot().run(self.bot_token)
