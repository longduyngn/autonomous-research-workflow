"""Find tools installed alongside the chosen Python without requiring activation."""
import os
import shutil
import sys
from pathlib import Path


def find_executable(name):
    explicit = Path(name)
    if explicit.is_absolute() and explicit.is_file():
        return str(explicit)
    if name in {"codex", "claude", "agy", "opencode"}:
        override = os.environ.get(f"WORKFLOW_{name.upper()}_EXECUTABLE", "")
        if override:
            return override if Path(override).is_file() else None
    found = shutil.which(name)
    if found:
        return found
    suffix = ".exe" if os.name == "nt" else ""
    for directory in [Path(sys.executable).parent / "Scripts", Path(sys.executable).parent]:
        candidate = directory / (name + suffix)
        if candidate.is_file():
            return str(candidate)
    if name == "codex" and os.name == "nt":
        home = Path(os.environ.get("USERPROFILE", str(Path.home())))
        releases = home / ".codex/packages/standalone/releases"
        candidates = list(releases.glob("*/bin/codex.exe"))
        def version(path):
            try:
                return tuple(int(x) for x in path.parents[1].name.split("-")[0].split("."))
            except ValueError:
                return (0,)
        if candidates:
            return str(max(candidates, key=version))
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
        for candidate in [local / "Programs/OpenAI/Codex/bin/codex.exe"]:
            if candidate.is_file():
                return str(candidate)
    return None
