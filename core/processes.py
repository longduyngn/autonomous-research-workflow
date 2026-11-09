"""Bounded subprocess execution and descendant cleanup."""
import os
import signal
import subprocess
import time
from pathlib import Path
from contextlib import ExitStack


def stop_process_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()


def run_process(argv, cwd, timeout=120, env=None, input_text=None, cancel_event=None,
                stdout_path=None, stderr_path=None, timeout_callback=None, hard_timeout=None):
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise ValueError("Command must be a nonempty argv list of strings")
    streams = ExitStack()
    def destination(path):
        if path is None:
            return subprocess.PIPE
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        return streams.enter_context(Path(path).open("w", encoding="utf-8"))
    def tail(path, value, limit):
        if path is None:
            return (value or "")[-limit:]
        with Path(path).open("rb") as source:
            source.seek(max(0, source.seek(0, 2) - limit * 4))
            return source.read().decode("utf-8", errors="replace")[-limit:]
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=destination(stdout_path), stderr=destination(stderr_path), text=True, encoding="utf-8", errors="replace",
        start_new_session=os.name != "nt",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    except BaseException:
        streams.close()
        raise
    try:
        deadline = time.monotonic() + timeout
        hard_deadline = time.monotonic() + (hard_timeout or timeout)
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Task execution cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    extension = timeout_callback() if timeout_callback else 0
                except Exception:
                    # A transient observer outage does not justify killing work.
                    # The hard ceiling still bounds this conservative grace.
                    extension = 60
                if extension > 0 and time.monotonic() < hard_deadline:
                    deadline = min(hard_deadline, time.monotonic()+extension)
                    continue
                raise subprocess.TimeoutExpired(argv, timeout)
            try:
                stdout, stderr = proc.communicate(input=input_text, timeout=min(1, remaining) if cancel_event is not None else remaining)
                break
            except subprocess.TimeoutExpired:
                input_text = None
                if time.monotonic() >= deadline:
                    if timeout_callback:
                        continue
                    raise
        return {"exit_code": proc.returncode, "stdout": tail(stdout_path, stdout, 24000),
                "stderr": tail(stderr_path, stderr, 12000)}
    except BaseException as exc:
        stop_process_tree(proc)
        stdout, stderr = proc.communicate(timeout=10)
        if not isinstance(exc, subprocess.TimeoutExpired):
            raise
        return {"exit_code": -1, "stdout": tail(stdout_path, stdout, 24000),
                "stderr": f"Timed out after {timeout}s\n{tail(stderr_path, stderr, 12000)}"}
    finally:
        streams.close()
