"""A bounded worker pool backed by cross-process SQLite leases."""
import threading
import time
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
        self.monitor_pool = ThreadPoolExecutor(max_workers=2,thread_name_prefix='supervision')
        self.monitor_futures = {}
        self.monitor_due = 0
        self.quota_pool = None
        self.quota_future = None
        self.quota_due = 0
        self.futures = {}
        self.stopping = threading.Event()
        self.thread = None

    def tick(self):
        supervisor = getattr(self.orchestrator,'supervisor',None)
        if supervisor and time.time() >= self.monitor_due:
            self.monitor_due = time.time()+15
            for name in ('poll','diagnose'):
                old = self.monitor_futures.get(name)
                if old is None or old.done():
                    if old:
                        try: old.result()
                        except Exception as exc:
                            self.db.log_event('SUPERVISION_ERROR','scheduler',type(exc).__name__,severity='WARN')
                    self.monitor_futures[name] = self.monitor_pool.submit(getattr(supervisor,name))
        monitor = getattr(self.orchestrator, 'quota_monitor', None)
        if monitor and time.time() >= self.quota_due:
            # Probes enforce their own throttles; Claude skips its tiny active
            # turn when recent passive stream usage is available.
            self.quota_due = time.time() + float(self.orchestrator.config.get('quota_routing', {}).get('poll_seconds', 120))
            if self.quota_future is not None and self.quota_future.done() and self.quota_future.exception():
                self.db.log_event('QUOTA_POLL_ERROR', 'scheduler', type(self.quota_future.exception()).__name__, severity='WARN')
            if self.quota_pool is None:
                self.quota_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='quota')
            if self.quota_future is None or self.quota_future.done():
                self.quota_future = self.quota_pool.submit(monitor.refresh)
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
        self.monitor_pool.shutdown(wait=wait, cancel_futures=True)
        if self.quota_pool:
            self.quota_pool.shutdown(wait=wait, cancel_futures=True)
