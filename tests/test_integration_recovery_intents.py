"""Local combined candidate acceptance; no native providers or messaging."""
import json
import tempfile
import unittest
from pathlib import Path

from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.models import AgentRunResult
from core.orchestrator import HybridOrchestrator
from test_workflow import FakeRunner


class IntegratedRecoveryIntentTests(unittest.TestCase):
    def test_natural_task_status_and_feedback_keep_identity_through_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=WorkspaceDatabase(root/'db')
            runner=FakeRunner()
            orch=HybridOrchestrator(root,{'logs_dir':'logs','isolation':{'enabled':False}},
                                    db,ContextLogManager(root/'logs'),runner)
            def propose(kind,task=None):
                return lambda *args:{'version':1,'intent':kind,'task_id':task,'reply':'model prose is not a receipt'}
            chat=orch.conversation
            chat.reply=propose('task')
            original='Please implement a parser and preserve empty-input checks.'
            submitted=chat.receive(original,'channel','owner','submit')
            task_id=submitted['task_id']
            self.assertEqual(chat.receive(original,'channel','owner','submit')['task_id'],task_id)
            self.assertEqual(len(db.get_all_tasks()),1)
            db.update_task(task_id,status='OPERATING',workspace_path=str(root))
            calls=[]
            def run(role,**kwargs):
                calls.append(role)
                if role=='operator':
                    return AgentRunResult(role,'fake',False,'',error='Timed out after 5s')
                return AgentRunResult(role,'fake',True,json.dumps({
                    'action':'needs_input','reason':'External effect identity cannot be established safely'}))
            runner.run_role=run
            current=orch._execute_operator(db.get_task(task_id))
            self.assertEqual(current.status,'RESEARCHING')
            chat.reply=propose('status',task_id)
            status=chat.receive(f'How is task #{task_id} progressing?','channel','owner','status')['response']
            self.assertIn('recovery REVIEW_QUEUED',status)
            self.assertNotIn('model prose',status)
            foreign=chat.receive(f'Status of task #{task_id}?','channel','intruder','foreign')['response']
            self.assertNotIn('REVIEW_QUEUED',foreign)
            chat.reply=propose('feedback',task_id)
            feedback=f'For task #{task_id}, also preserve Unicode input.'
            chat.receive(feedback,'channel','owner','feedback')
            final=orch.run_task_to_completion(task_id)
            self.assertEqual(final.status,'WAITING_INPUT')
            self.assertEqual(final.feedback_revision,1)
            self.assertEqual(final.user_prompt,original)
            self.assertEqual(chat.feedback(task_id)[0]['message'],feedback)
            self.assertEqual(calls,['operator','researcher'])
            chat.reply=propose('status',task_id)
            status=chat.receive(f'Current status of task #{task_id}?','channel','owner','status-final')['response']
            self.assertIn('WAITING_INPUT',status)
            self.assertIn('recovery WAIT_INPUT',status)
            with db._connect() as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM recovery_episodes').fetchone()[0],1)
                self.assertEqual(c.execute('SELECT COUNT(*) FROM message_outbox').fetchone()[0],1)


if __name__=='__main__':unittest.main()
