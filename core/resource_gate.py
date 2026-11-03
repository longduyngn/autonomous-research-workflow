"""Serialize managed Colab effects across bridge processes; durable fencing."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
from core.control import ControlPlane
from core.database import WorkspaceDatabase
from core.role_memory import RoleMemory


class ResourceBusy(RuntimeError):
    pass


class ColabGate:
    def __init__(self, directory, observe):
        self.directory=Path(directory)
        self.control=ControlPlane(WorkspaceDatabase(self.directory/'resources.db'))
        self.observe=observe
        with self.control.db._connect() as c:
            c.execute("INSERT OR IGNORE INTO resources(name,state) VALUES('colab','UNKNOWN')")

    @contextmanager
    def locked(self):
        # OS lock covers the entire external operation, not just token validation.
        with ExitStack() as stack:
            try:
                stack.enter_context(RoleMemory(self.directory,'operator').locked())
            except OSError as exc:
                raise ResourceBusy('Another managed Colab operation is in progress') from exc
            yield

    def claim(self, owner):
        with self.locked():
            row=self.control.resource('colab')
            if row['state'] in {'UNKNOWN','QUARANTINED'} and not row['owner']:
                try:
                    observation=self.observe()
                except Exception as exc:
                    observation={'runtime_stopped':False,'error':type(exc).__name__}
                self.control.observe_resource('colab',observation)
            generation=self.control.claim_resource('colab',owner)
            if generation is None:
                raise ResourceBusy('Colab is owned or its runtime state is unknown; reconcile before another task uses it')
            return {'status':'claimed','generation':generation,'protocol':2}

    def execute(self, owner, generation, action):
        with self.locked():
            self.control.check_resource('colab',owner,generation)
            return action()

    def release(self, owner, generation, before_release=None):
        with self.locked():
            self.control.check_resource('colab',owner,generation)
            if before_release:
                before_release()
            try:
                observation=self.observe()
            except Exception as exc:
                observation={'runtime_stopped':False,'error':type(exc).__name__}
            if observation.get('runtime_stopped') is True and observation.get('active_session_count')==0:
                self.control.observe_resource('colab',observation,owner,generation)
                return {'status':'released','observation':observation,'runtime_stopped':True}
            # Retain owner for explicit cleanup; another task never inherits a live VM.
            with self.control.db._connect() as c:
                import json
                c.execute("UPDATE resources SET observation=? WHERE name='colab'",(json.dumps(observation),))
            return {'status':'ownership_retained','runtime_stopped':False,'observation':observation}

    def reconcile(self):
        with self.locked():
            observation=self.observe()
            row=self.control.resource('colab')
            if not row['owner'] or (observation.get('runtime_stopped') is True and observation.get('active_session_count')==0):
                self.control.observe_resource('colab',observation)
            else:
                import json
                with self.control.db._connect() as c:
                    c.execute("UPDATE resources SET observation=? WHERE name='colab'",(json.dumps(observation),))
            return self.control.resource('colab')
