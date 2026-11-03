import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.orchestrator import HybridOrchestrator
from gateways.base import MessagingRouter
from gateways.webhook_server import InboundWebhookServer
from test_workflow import FakeRunner


class IngressTests(unittest.TestCase):
    def test_policy_words_in_plain_read_commands_are_not_execution(self):
        from scripts.pre_tool_approval_hook import is_dangerous_command
        self.assertFalse(is_dangerous_command('Get-Content notes.md; rg -n "shutdown" notes.md')[0])
        for command in ('Get-Content notes.md; shutdown /s', 'rg shutdown notes.md; rm output',
                        'Get-Content $(shutdown /s)', 'python -c "import os; os.system(\'shutdown /s\')"'):
            self.assertTrue(is_dangerous_command(command)[0], command)
    def test_webhook_auth_and_duplicate_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = WorkspaceDatabase(root / "db.sqlite")
            orch = HybridOrchestrator(root, {}, db, ContextLogManager(root / "logs"), FakeRunner())
            server = InboundWebhookServer("127.0.0.1", 0, db,
                lambda p,c,u,k="": orch.submit_task(p, source_channel=c, source_user=u, event_key=k).id, auth_token="test-only")
            server.start_background()
            try:
                url = f"http://127.0.0.1:{server._server.server_port}/webhook/task"
                def request(token):
                    req = urllib.request.Request(url, data=json.dumps({"prompt": "experiment"}).encode(),
                        headers={"Authorization": token, "Idempotency-Key": "same-event"})
                    with urllib.request.urlopen(req) as response:
                        return json.loads(response.read())
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request("Bearer wrong")
                self.assertEqual(error.exception.code, 401)
                self.assertEqual(request("Bearer test-only")["task_id"], request("Bearer test-only")["task_id"])
                self.assertEqual(len(db.get_all_tasks()), 1)
            finally:
                server.stop()

    def test_outbox_survives_delivery_failure(self):
        class Gateway:
            channel_name = "test"
            success = False
            def send_message(self, *args, **kwargs):
                return self.success
        with tempfile.TemporaryDirectory() as tmp:
            db = WorkspaceDatabase(Path(tmp) / "db.sqlite")
            task = db.create_task("task", source_channel="test")
            db.finalize_task(task.id, "COMPLETED", "actual results")
            router, gateway = MessagingRouter(db), Gateway()
            router.register(gateway)
            router.flush_outbox()
            with db._connect() as conn:
                self.assertEqual(conn.execute("SELECT delivered FROM message_outbox").fetchone()[0], 0)
                conn.execute("UPDATE message_outbox SET next_attempt=0")
            gateway.success = True
            router.flush_outbox()
            with db._connect() as conn:
                row = conn.execute("SELECT delivered,attempts FROM message_outbox").fetchone()
                self.assertEqual(tuple(row), (1,2))

    def test_hook_malformed_payload_denied(self):
        import io
        from scripts import pre_tool_approval_hook as hook
        output = io.StringIO()
        with patch("sys.stdin", io.StringIO("not-json")), patch("sys.stdout", output):
            hook.main()
        self.assertEqual(json.loads(output.getvalue())["decision"], "deny")

    def test_hook_approval_binds_user_and_request(self):
        from scripts import pre_tool_approval_hook as hook
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps([
                    {"author": {"id":"owner", "username":"owner"}, "content":"approve current"},
                    {"author": {"id":"intruder"}, "content":"approve current"},
                    {"author": {"id":"owner"}, "content":"approve wrong"},
                ]).encode()
        with patch.dict("os.environ", {"DISCORD_ALLOWED_USER_IDS":"owner"}), patch.object(hook.time, "sleep"), patch.object(hook.urllib.request, "urlopen", return_value=Response()), patch.object(hook, "send_discord_msg"):
            decision, reason = hook.poll_discord_decision("token", "channel", "message", timeout_sec=1, request_id="current")
            self.assertEqual(decision, "allow")
            self.assertIn("owner", reason)
