import tempfile
import unittest
from pathlib import Path
from core.models import AgentRunResult
from core.providers import EphemeralAgentRunner


class RegistryTests(unittest.TestCase):
    def test_plugin_survives_workspace_binding(self):
        calls=[]
        class Adapter:
            def run(self,**kwargs):
                calls.append(kwargs)
                return AgentRunResult(kwargs['role'],'fixture',True,'review complete')
        with tempfile.TemporaryDirectory() as tmp:
            cfg={'roles':{'communicator':{'provider':'new'}},'providers':{'new':{'type':'fixture_plugin'}}}
            runner=EphemeralAgentRunner(tmp,cfg)
            runner.extensions.register('fixture_plugin',Adapter())
            root=Path(tmp)/'child'
            result=runner.for_workspace(root).run_role('communicator','message')
            self.assertTrue(result.success)
            self.assertEqual(calls[0]['workspace'],root.resolve())
