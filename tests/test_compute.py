import unittest
from core.compute import ComputeRegistry, ComputeRequest, PLATFORMS


class ComputeTests(unittest.TestCase):
    def test_disabled_even_when_configured_without_installed_adapter(self):
        registry=ComputeRegistry({p:{'enabled':True} for p in PLATFORMS})
        for name in PLATFORMS:
            with self.assertRaises(RuntimeError):registry.require(name)
        self.assertFalse(any(r['enabled'] for r in registry.capabilities()))
        with self.assertRaises(TypeError):registry.register('ssh',object())

    def test_request_requires_bounded_cost_time_and_checkpoint_destination(self):
        request=ComputeRequest(1,'T4',2,1.0,'private://checkpoint/task1')
        self.assertEqual(request.max_hours,2)
        for hours,cost,destination in [(0,1,'d'),(1,-1,'d'),(float('inf'),1,'d'),(1,0,'')]:
            with self.assertRaises(ValueError):ComputeRequest(1,'CPU',hours,cost,destination)
