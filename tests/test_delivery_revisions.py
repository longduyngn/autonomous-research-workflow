import tempfile
import unittest
from pathlib import Path
from core.database import WorkspaceDatabase


class DeliveryRevisionTests(unittest.TestCase):
    def test_new_terminal_message_is_delivered_and_stale_ack_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=WorkspaceDatabase(Path(tmp)/'db')
            task=db.create_task('experiment')
            db.finalize_task(task.id,'ESCALATED','needs recovery')
            first=db.claim_outbound()
            db.finalize_task(task.id,'COMPLETED','verified result')
            db.finish_outbound(task.id,True,first['revision'])
            second=db.claim_outbound()
            self.assertEqual(second['message'],'verified result')
            self.assertGreater(second['revision'],first['revision'])
            db.finish_outbound(task.id,True,second['revision'])
            db.finalize_task(task.id,'COMPLETED','verified result')
            self.assertIsNone(db.claim_outbound())
