import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.role_memory import atomic_json


class AtomicMemoryTests(unittest.TestCase):
    def test_transient_watcher_lock_retries_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            atomic_json(path, {'generation': 1})
            from os import replace
            calls = []

            def locked_once(source, target):
                calls.append(source)
                self.assertEqual(json.loads(path.read_text()), {'generation': 1})
                if len(calls) == 1:
                    raise PermissionError('watcher lock')
                replace(source, target)

            with patch('core.role_memory.os.replace', side_effect=locked_once), patch('time.sleep'):
                atomic_json(path, {'generation': 2})
            self.assertEqual(json.loads(path.read_text()), {'generation': 2})
            self.assertEqual(len(calls), 2)
            self.assertEqual(list(Path(tmp).glob('*.tmp')), [])

    def test_persistent_lock_preserves_old_state_and_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            atomic_json(path, {'generation': 1})
            original = path.read_bytes()
            with patch('core.role_memory.os.replace', side_effect=PermissionError('locked')) as replace, patch('time.sleep'):
                with self.assertRaises(PermissionError):
                    atomic_json(path, {'generation': 2})
            self.assertEqual(replace.call_count, 10)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(tmp).glob('*.tmp')), [])
