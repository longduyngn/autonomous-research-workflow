import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from core.database import WorkspaceDatabase
from core.conversation import Conversation


class ConversationDeliveryTests(unittest.TestCase):
    def test_handler_and_heartbeat_cannot_claim_same_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=WorkspaceDatabase(Path(tmp)/'db');chat=Conversation(db)
            chat.receive('hello','channel','owner','message')
            def claim(_):return Conversation(WorkspaceDatabase(db.db_path)).claim_delivery('message')
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                owners=[x for x in pool.map(claim,range(2)) if x]
            self.assertEqual(len(owners),1)
            self.assertTrue(chat.delivered('message','reply',owners[0]))
            self.assertIsNone(chat.claim_delivery('message'))

    def test_expired_sender_cannot_ack_new_delivery_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=WorkspaceDatabase(Path(tmp)/'db');chat=Conversation(db)
            chat.receive('hello','channel','owner','message')
            old=chat.claim_delivery('message')
            with db._connect() as c:c.execute('UPDATE intake_messages SET delivery_until=0')
            new=chat.claim_delivery('message')
            self.assertFalse(chat.delivered('message','stale',old))
            self.assertTrue(chat.delivered('message','current',new))
