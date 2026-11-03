"""Copy explicit task inputs before workers can claim the request."""
import hashlib
import shutil
from pathlib import Path

MAX_INPUT_BYTES = 50_000_000


def validate_inputs(paths):
    files = []
    total = 0
    names = set()
    for value in paths:
        original = Path(value)
        source = original.resolve()
        if (original.is_symlink() or not source.is_file()
                or any(p.startswith(".env") or p.endswith(".env")
                       or p in {".ssh", ".aws", ".kaggle", ".config"} for p in source.parts)
                or source.name.lower() in {"credentials.json", "kaggle.json", "token.json", "access_token"}
                or source.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}):
            raise ValueError("Task input must be a regular, non-credential file")
        if source.name.casefold() in names:
            raise ValueError("Task input filenames must be unique")
        names.add(source.name.casefold())
        total += source.stat().st_size
        if total > MAX_INPUT_BYTES:
            raise ValueError("Task inputs exceed the 50 MB total limit; use a dataset or source directory")
        files.append(source)
    return files


def store_inputs(controller, task_id, paths):
    records = []
    for source in paths:
        destination = Path(controller) / "storage/inputs" / f"task_{task_id:05d}" / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError("Task input snapshot already exists")
        shutil.copy2(source, destination)
        records.append({"snapshot": str(destination.resolve()), "name": source.name,
                        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
    return records
