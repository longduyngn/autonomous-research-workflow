"""Job contract for managed notebook/batch backends (P2.5-01): Colab, Kaggle, local.

Separate from ``core.compute.ProvisioningAdapter`` (rented VMs, still disabled). A job adapter
implements five operations; the controller (``JobController``) owns durable identity:

* ``submit``  - the request is journaled as SUBMITTING before the effect; an exception or timeout
  leaves SUBMISSION_UNKNOWN and is never resubmitted automatically.
* ``status``  - observation only: no runtime allocation, hardware switch, reconnect or cancel.
* ``logs_tail`` / ``fetch_outputs`` - bounded reads; fetching honours the output policy.
* ``cancel``  - a separate command; only a verified stop of the same job/version is STOPPED.

Budgets are optional (D6): ``budget_seconds`` 0 means none; a value comes from the user.
"""
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

BACKENDS = ('colab', 'kaggle', 'local')
OBSERVED_STATES = ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED', 'UNKNOWN')


@dataclass(frozen=True)
class OutputDecl:
    path: str
    kind: str = 'artifact'          # artifact | checkpoint | metrics | log | report | sample
    local_download: bool = False    # heavy outputs stay remote; only light ones come home
    max_bytes: int = 50 * 1024 * 1024


@dataclass(frozen=True)
class JobSpec:
    task_id: int
    backend: str
    entry: str                       # script or notebook path inside the task workspace
    entry_sha256: str = ''
    config: dict = field(default_factory=dict)
    environment: str = ''            # environment lock identity (requirements hash, image, ...)
    hardware: str = 'CPU'
    allowed_backends: tuple = ()     # from the approved plan; empty = only ``backend``
    checkpoint_uri: str = ''         # off-VM durable store (e.g. gdrive:workflow-runs/...)
    outputs: tuple = ()
    budget_seconds: float = 0
    total: float = 0
    stall_seconds: float = 900
    parent_job_id: str = ''          # resume: the job whose checkpoint this continues

    def __post_init__(self):
        if self.task_id < 1 or self.backend not in BACKENDS or not self.entry:
            raise ValueError('JobSpec needs a task, a known backend and an entry point')
        if self.allowed_backends and self.backend not in self.allowed_backends:
            raise ValueError('Backend is not allowed by the approved plan')
        if self.backend != 'local' and not self.checkpoint_uri:
            raise ValueError('Remote jobs need an explicit off-VM checkpoint_uri (checkpoint/resume is mandatory)')
        if self.budget_seconds < 0 or self.stall_seconds < 1 or self.total < 0:
            raise ValueError('Invalid budget/stall/total')
        for item in self.outputs:
            if not isinstance(item, OutputDecl):
                raise TypeError('outputs must be OutputDecl items')

    def fingerprint(self):
        data = asdict(self)
        data['outputs'] = [asdict(o) for o in self.outputs]
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=list).encode()).hexdigest()


@dataclass
class JobHandle:
    job_id: str                      # controller logical job id (experiment_jobs.id)
    attempt_id: str
    request_key: str
    backend: str
    external_id: str = ''
    generation: int = 0
    details: dict = field(default_factory=dict)   # backend identity: slug/version, notebook/cell/boot


class JobAdapter(Protocol):
    def submit(self, spec: JobSpec, request_key: str, job_id: str) -> dict: ...  # -> {'external_id', 'details'}
    def status(self, handle: JobHandle) -> dict: ...                       # -> {'state', 'completed'?, ...}
    def logs_tail(self, handle: JobHandle, cursor: int, limit: int) -> dict: ...
    def fetch_outputs(self, handle: JobHandle, manifest: dict, policy: dict) -> dict: ...
    def cancel(self, handle: JobHandle, reason: str) -> dict: ...          # -> {'state', 'verified'}


class JobRegistry:
    METHODS = ('submit', 'status', 'logs_tail', 'fetch_outputs', 'cancel')

    def __init__(self):
        self.adapters = {}

    def register(self, backend, adapter):
        if backend not in BACKENDS:
            raise ValueError('Unknown job backend')
        missing = [m for m in self.METHODS if not callable(getattr(adapter, m, None))]
        if missing:
            raise TypeError('Job adapter misses ' + ', '.join(missing))
        self.adapters[backend] = adapter

    def get(self, backend):
        if backend not in self.adapters:
            raise RuntimeError(f'No job adapter installed for {backend}')
        return self.adapters[backend]


class JobController:
    """Durable submission journal + observation mapping onto ``ProgressStore``."""

    def __init__(self, db, registry, store=None, clock=time.time):
        from core.monitoring import ProgressStore
        self.db, self.registry, self.clock = db, registry, clock
        self.store = store or ProgressStore(db)
        with db._connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS compute_submissions(request_key TEXT PRIMARY KEY,
              task_id INTEGER NOT NULL, fingerprint TEXT NOT NULL, job_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
              backend TEXT NOT NULL, state TEXT NOT NULL, handle_json TEXT NOT NULL DEFAULT '{}',
              parent_job_id TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL)''')

    def _row(self, request_key):
        with self.db._connect() as c:
            row = c.execute('SELECT * FROM compute_submissions WHERE request_key=?', (request_key,)).fetchone()
        return dict(row) if row else None

    def _handle(self, row):
        data = json.loads(row['handle_json'] or '{}')
        return JobHandle(row['job_id'], row['attempt_id'], row['request_key'], row['backend'],
                         data.get('external_id', ''), data.get('generation', 0), data.get('details', {}))

    def submit(self, spec, request_key):
        """-> (handle, outcome) with outcome submitted | existing | unknown. Never resubmits an uncertain effect."""
        import re
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}', request_key):
            raise ValueError('Invalid request key')
        fingerprint = spec.fingerprint()
        existing = self._row(request_key)
        if existing:
            if existing['fingerprint'] != fingerprint:
                raise ValueError('Request key reused with a changed job specification')
            return self._handle(existing), ('existing' if existing['state'] == 'SUBMITTED' else 'unknown')
        adapter = self.registry.get(spec.backend)
        job, _ = self.store.register(spec.task_id, request_key, spec.backend,
                                     external_id=str(spec.config.get('register_external_id', '')), total=spec.total,
                                     stall_seconds=spec.stall_seconds, budget_seconds=spec.budget_seconds)
        attempt = hashlib.sha256((request_key + fingerprint).encode()).hexdigest()[:16]
        now = self.clock()
        with self.db._connect() as c:
            c.execute('INSERT INTO compute_submissions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                      (request_key, spec.task_id, fingerprint, job['id'], attempt, spec.backend, 'SUBMITTING', '{}',
                       spec.parent_job_id, now, now))
        try:
            result = adapter.submit(spec, request_key, job['id']) or {}
        except Exception as exc:
            self._set(request_key, 'SUBMISSION_UNKNOWN', {'error': type(exc).__name__})
            self.store.observe(job['id'], {'state': 'SUBMISSION_UNKNOWN', 'completed': 0}, spec.backend)
            self.db.log_event('JOB_SUBMISSION_UNKNOWN', 'compute', f'{spec.backend} submit outcome unknown; reconcile, do not resubmit',
                              task_id=spec.task_id, severity='WARN', payload={'job_id': job['id'], 'error': type(exc).__name__})
            return self._handle(self._row(request_key)), 'unknown'
        handle_data = {'external_id': str(result.get('external_id', '')), 'generation': int(result.get('generation', 0)),
                       'details': result.get('details', {})}
        self._set(request_key, 'SUBMITTED', handle_data)
        return self._handle(self._row(request_key)), 'submitted'

    def _set(self, request_key, state, handle_data):
        with self.db._connect() as c:
            c.execute('UPDATE compute_submissions SET state=?, handle_json=?, updated=? WHERE request_key=?',
                      (state, json.dumps(handle_data), self.clock(), request_key))

    def status(self, handle):
        """Observe and record; adapters must not cause effects here. Transport errors are UNKNOWN."""
        adapter = self.registry.get(handle.backend)
        try:
            observation = dict(adapter.status(handle) or {})
        except Exception as exc:
            self.store.unavailable(handle.job_id, type(exc).__name__)
            return {'state': 'UNKNOWN', 'telemetry': 'unavailable:' + type(exc).__name__}
        state = observation.get('state', 'UNKNOWN')
        if state not in OBSERVED_STATES:
            state = 'UNKNOWN'
        mapped = {'QUEUED': 'RUNNING', 'CANCELLED': 'FAILED'}.get(state, state)
        record = {k: v for k, v in observation.items()
                  if k in ('completed', 'total', 'metrics', 'stage', 'heartbeat_at') and v not in (None, '')}
        record['state'] = mapped
        job = self.store.get(handle.job_id)
        record.setdefault('completed', job['completed'])
        self.store.observe(handle.job_id, record, handle.backend)
        return {**observation, 'state': state}

    def logs_tail(self, handle, cursor=0, limit=200):
        return self.registry.get(handle.backend).logs_tail(handle, cursor, max(1, min(int(limit), 2000)))

    def fetch_outputs(self, handle, manifest, policy=None):
        policy = {'local_only_declared_light': True, **(policy or {})}
        return self.registry.get(handle.backend).fetch_outputs(handle, manifest, policy)

    def cancel(self, handle, reason):
        """Separate command. Only a verified stop of this exact job is recorded as stopped."""
        adapter = self.registry.get(handle.backend)
        try:
            receipt = dict(adapter.cancel(handle, reason) or {})
        except Exception as exc:
            receipt = {'state': 'UNKNOWN', 'verified': False, 'error': type(exc).__name__}
        if receipt.get('state') == 'STOPPED' and receipt.get('verified') is True:
            self.store.observe(handle.job_id, {'state': 'FAILED', 'completed': self.store.get(handle.job_id)['completed'],
                                               'stage': 'cancelled'}, handle.backend)
        elif receipt.get('state') not in ('CANCEL_REQUESTED', 'UNKNOWN'):
            receipt['state'] = 'UNKNOWN'
        self.db.log_event('JOB_CANCEL', 'compute', f"{handle.backend} cancel: {receipt.get('state')} ({reason[:80]})",
                          payload={'job_id': handle.job_id, **{k: v for k, v in receipt.items() if k != 'raw'}})
        return receipt
