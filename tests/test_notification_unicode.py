import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.database import WorkspaceDatabase
from gateways.base import MessagingRouter
from gateways.discord_gateway import DiscordGateway


class NotificationUnicodeTests(unittest.TestCase):
    def test_database_outbox_discord_json_preserves_vietnamese(self):
        message='Đã xác minh tin nhắn. Bạn không cần làm gì thêm.'
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
        with tempfile.TemporaryDirectory() as tmp:
            db=WorkspaceDatabase(Path(tmp)/'db')
            task=db.create_task('probe',source_channel='discord',source_user='123')
            db.finalize_task(task.id,'COMPLETED',message)
            router=MessagingRouter(db)
            with patch.dict('os.environ',{'DISCORD_BOT_TOKEN':'fixture','DISCORD_WEBHOOK_URL':''}),patch('urllib.request.urlopen',return_value=Response()) as send:
                router.register(DiscordGateway({}))
                router.flush_outbox()
                sent=json.loads(send.call_args.args[0].data.decode('utf8'))
            self.assertEqual(sent['content'],message)
            self.assertEqual(db.get_task(task.id).final_response,message)
            self.assertIsNone(db.claim_outbound())
