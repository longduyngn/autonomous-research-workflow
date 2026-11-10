"""In-memory job adapter for compute contract tests (no network, no browser, no CLI)."""
import itertools


class FakeJobAdapter:
    def __init__(self, submit_error=None, states=None, cancel_result=None):
        self.submit_error = submit_error
        self.states = list(states or ['RUNNING', 'COMPLETED'])
        self.cancel_result = cancel_result or {'state': 'STOPPED', 'verified': True}
        self.calls = []
        self._ids = itertools.count(1)
        self.effects = 0

    def submit(self, spec, request_key, job_id=None):
        self.calls.append(('submit', request_key))
        self.effects += 1
        if self.submit_error:
            raise self.submit_error
        return {'external_id': f'ext-{next(self._ids)}', 'details': {'version': 1}}

    def status(self, handle):
        self.calls.append(('status', handle.external_id))
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {'state': state, 'completed': 1 if state == 'COMPLETED' else 0}

    def logs_tail(self, handle, cursor, limit):
        self.calls.append(('logs_tail', cursor, limit))
        return {'cursor': cursor + 1, 'lines': ['step 1'][:limit]}

    def fetch_outputs(self, handle, manifest, policy):
        self.calls.append(('fetch_outputs', policy))
        return {'fetched': [f for f in manifest.get('files', []) if f.get('local_download')]}

    def cancel(self, handle, reason):
        self.calls.append(('cancel', reason))
        if isinstance(self.cancel_result, Exception):
            raise self.cancel_result
        return dict(self.cancel_result)
