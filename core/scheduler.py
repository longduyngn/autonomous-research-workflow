"""A bounded worker pool backed by cross-process SQLite leases."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor


class TaskScheduler:
    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.db = orchestrator.db
        self.capacity = int(orchestrator.config.get("orchestrator", {}).get("max_concurrent_tasks", 2))
        if not 1 <= self.capacity <= 3:
            raise ValueError("max_concurrent_tasks must be between 1 and 3")
        self.pool = ThreadPoolExecutor(max_workers=self.capacity, thread_name_prefix="workflow")
        self.delivery_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="delivery")
        self.delivery_future = None
        self.futures = {}
        self.stopping = threading.Event()
        self.thread = None

    def tick(self):
        control = getattr(self.orchestrator, 'control', None)
        if control:
            control.advance_waits()
        delivery = getattr(self.orchestrator, "delivery_tick", None)
        if delivery and (self.delivery_future is None or self.delivery_future.done()):
            if self.delivery_future is not None:
                try:
                    self.delivery_future.result()
                except Exception as exc:
                    self.db.log_event("DELIVERY_ERROR", "scheduler", str(exc), severity="WARN")
            # Slow network delivery must not stall task claims or worker cleanup.
            self.delivery_future = self.delivery_pool.submit(delivery)
        for future, task_id in list(self.futures.items()):
            if future.done():
                try:
                    future.result()
                except Exception as exc:
                    self.db.log_event("WORKER_ERROR", "scheduler", str(exc), task_id=task_id, severity="ERROR")
                del self.futures[future]
        while len(self.futures) < self.capacity and not self.stopping.is_set():
            owner = uuid.uuid4().hex
            task = self.db.claim_task(owner, self.capacity)
            if task is None:
                break
            future = self.pool.submit(self.orchestrator.run_task_to_completion, task.id, owner)
            self.futures[future] = task.id

    def start(self):
        if self.thread:
            return
        def loop():
            while not self.stopping.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    self.db.log_event("SCHEDULER_ERROR", "scheduler", str(exc), severity="ERROR")
                self.stopping.wait(0.25)
        self.thread = threading.Thread(target=loop, daemon=True, name="scheduler")
        self.thread.start()

    def stop(self, wait=True, cancel_running=False):
        self.stopping.set()
        if cancel_running:
            self.orchestrator.cancel_running()
        if self.thread:
            self.thread.join(timeout=5)
        self.pool.shutdown(wait=wait, cancel_futures=True)
        self.delivery_pool.shutdown(wait=wait, cancel_futures=True)
