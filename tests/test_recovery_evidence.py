import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.cloud import CloudTools
from core.database import WorkspaceDatabase
from core.maintenance import archive_task
from scripts.colab_timed_probe import check

ROOT = Path(__file__).resolve().parent.parent


class RecoveryEvidenceTests(unittest.TestCase):
    def test_agent_cannot_start_a_restricted_copy_of_host_bridge(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'WORKFLOW_ROLE':'operator'}), patch('core.cloud._is_port_open', return_value=False), patch('core.cloud.subprocess.Popen') as spawn:
            with self.assertRaisesRegex(RuntimeError, 'host controller'):
                CloudTools(tmp)._ensure_colab_bridge('127.0.0.1',8765)
            spawn.assert_not_called()

    def test_colab_backend_is_prepared_by_controller_before_operator(self):
        from core.logger import ContextLogManager
        from core.orchestrator import HybridOrchestrator
        from test_workflow import FakeRunner
        for backend in ('local', 'colab'):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                db = WorkspaceDatabase(root / 'events.db')
                plan = '```research_plan\n' + json.dumps({'mode':'direct', 'backend':backend}) + '\n```'
                runner = FakeRunner(plan=plan)
                orch = HybridOrchestrator(root, {'isolation':{'enabled':False}}, db,
                    ContextLogManager(root / 'logs'), runner)
                def prepared():
                    self.assertNotIn('operator', runner.calls)
                    return {'status':'ready','tool_count':1}
                with patch.dict('os.environ', {'COLAB_BRIDGE_TOKEN': 'fixture-token'}, clear=False), \
                     patch.object(CloudTools, '_ensure_colab_bridge', side_effect=AssertionError('Must not start host bridge in unit test')), \
                     patch.object(CloudTools, 'prepare_colab_bridge', side_effect=prepared) as prepare, \
                     patch.object(CloudTools, 'colab_release', return_value={'status': 'released', 'runtime_stopped': True}) as release:
                    final = orch.run_task_to_completion(orch.submit_task('experiment').id)
                self.assertEqual(final.status, 'COMPLETED')
                self.assertEqual(prepare.call_count, int(backend == 'colab'))
                self.assertEqual(release.call_count, int(backend == 'colab'))

    def test_colab_release_does_not_bootstrap_bridge_and_tests_do_not_load_controller_env(self):
        import os
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict('os.environ', {'COLAB_BRIDGE_TOKEN': 'fixture-token'}, clear=True), \
             patch('core.cloud._is_port_open', return_value=False), \
             patch.object(CloudTools, '_ensure_colab_bridge', side_effect=AssertionError('release must not bootstrap bridge')), \
             patch('core.cloud.subprocess.Popen', side_effect=AssertionError('release must not spawn subprocess')):
            tools = CloudTools(tmp, {'cloud': {'colab_bridge_url': 'http://127.0.0.1:8765'}})
            tools._load_env_if_needed()
            self.assertEqual(os.environ.get('COLAB_BRIDGE_TOKEN'), 'fixture-token')
            self.assertNotIn('DISCORD_BOT_TOKEN', os.environ)
            result = tools.colab_release({'force': True})
            self.assertEqual(result['status'], 'bridge_not_running')
            self.assertFalse(result['runtime_stopped'])

    def test_verification_database_does_not_replace_real_task_index(self):
        from core.logger import ContextLogManager
        from core.orchestrator import HybridOrchestrator
        from test_workflow import FakeRunner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            board = root / 'storage/TASKS.md'
            board.parent.mkdir(); board.write_text('real task index')
            db = WorkspaceDatabase(root / 'verification/events.db')
            orch = HybridOrchestrator(root, {'isolation':{'enabled':False}}, db,
                ContextLogManager(root / 'logs'), FakeRunner())
            final = orch.run_task_to_completion(orch.submit_task('verification only').id)
            self.assertEqual(final.status, 'COMPLETED')
            self.assertEqual(board.read_text(), 'real task index')
            self.assertTrue((root / 'verification/TASKS.md').is_file())

    def test_authentication_failure_is_reported_before_hook_symptoms(self):
        from core.providers import EphemeralAgentRunner
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {'providers': {'fixture': {'type':'custom_cli','command_template':[
                sys.executable, '-c', "import sys;sys.stderr.write('Authentication required');sys.exit(1)"]}},
                'roles':{'operator':{'provider':'fixture'}}}
            result = EphemeralAgentRunner(root, cfg).run_role('operator','test')
            self.assertFalse(result.success)
            self.assertIn('CLI authentication required', result.error)

    def test_shutdown_observation_flag_is_not_os_shutdown(self):
        from scripts.pre_tool_approval_hook import is_dangerous_command
        self.assertFalse(is_dangerous_command('python colab_timed_probe.py --verify-shutdown')[0])
        self.assertTrue(is_dangerous_command('shutdown /s')[0])
        self.assertTrue(is_dangerous_command('python colab_timed_probe.py --verify-shutdown; shutdown /s')[0])

    def test_report_rejects_nonexistent_file_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'core').mkdir()
            (root / 'core/actual.py').write_text('')
            command = [sys.executable, str(ROOT / 'scripts/workspace_report.py'), '--workspace', str(root)]
            subprocess.run(command, check=True, capture_output=True)
            subprocess.run(command + ['--check'], check=True, capture_output=True)
            p = root / 'output/workspace_state.json'
            data = json.loads(p.read_text()); data['core_files'].append('core/invented.py')
            p.write_text(json.dumps(data))
            self.assertNotEqual(subprocess.run(command + ['--check'], capture_output=True).returncode, 0)

    def test_archive_preserves_bytes_and_rejects_controller_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / 'storage/events.db')
            task = db.create_task('old failure')
            db.update_task(task.id, status='ESCALATED', workspace_path=str(root))
            with self.assertRaises(ValueError):
                archive_task(db, root, task.id, 'batch')
            source = root / f'storage/workspaces/task_{task.id:05d}'
            source.mkdir(parents=True); (source / 'evidence.txt').write_bytes(b'original evidence')
            db.update_task(task.id, workspace_path=str(source))
            with patch.object(Path, 'rename', side_effect=PermissionError('workspace is open')):
                with self.assertRaises(PermissionError):
                    archive_task(db, root, task.id, 'batch')
            self.assertTrue(source.is_dir())
            manifest = archive_task(db, root, task.id, 'batch')
            self.assertEqual(manifest['previous_attempt']['operation_state'], 'deferred')
            archived = Path(manifest['archive_workspace']) / 'evidence.txt'
            self.assertEqual(hashlib.sha256(archived.read_bytes()).hexdigest(), manifest['files_sha256']['evidence.txt'])
            self.assertFalse(source.exists())
            self.assertEqual(db.get_task(task.id).workspace_path, str(archived.parent))

    def test_failed_colab_handshake_resets_transport(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp); tools.controller = Path(tmp)
            with patch.object(tools, 'colab_tools', return_value={'tools':[]}), patch.object(tools, '_colab', return_value={'structuredContent':{'result':False}}), patch.object(tools, 'colab_release') as release, patch('core.cloud._is_port_open', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'transport reset'):
                    tools.colab_connect({})
            release.assert_called_once_with({'force':True})

    def test_gpu_check_rejects_claim_without_disconnect_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'artifacts').mkdir()
            data = {'status':'completed','requested_seconds':300,'run_id':'unique',
                    'measurement':{'run_id':'unique','gpu_name':'Tesla T4','elapsed_seconds':300.1},
                    'shutdown':{'status':'disconnected'},'shutdown_verified':False}
            (root / 'artifacts/colab_timed_result.json').write_text(json.dumps(data))
            with self.assertRaisesRegex(AssertionError, 'disconnected-state'):
                check(root, 300)
            data['shutdown_verified'] = True
            data['shutdown_state'] = {'connected':False, 'label':'Reconnect T4'}
            (root / 'artifacts/colab_timed_result.json').write_text(json.dumps(data))
            with self.assertRaisesRegex(AssertionError, 'Stopped VM'):
                check(root, 300)
