"""Local fake-only natural-language intake and crash/concurrency acceptance."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core.conversation import Conversation
from core.conversation_intents import parse_intent
from core.database import WorkspaceDatabase
from core.logger import ContextLogManager
from core.orchestrator import HybridOrchestrator
from test_workflow import FakeRunner


def intent(kind, task=None, reply='Understood'):
    return {'version':1,'intent':kind,'task_id':task,'reply':reply}


class NaturalLanguageIntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.db=WorkspaceDatabase(self.root/'db')
        self.orch=HybridOrchestrator(self.root,{'isolation':{'enabled':False}},self.db,
                                     ContextLogManager(self.root/'logs'),FakeRunner())
        self.chat=self.orch.conversation

    def send(self,text,key,actor='owner',scope='channel',reply_key=''):
        return self.chat.receive(text,scope,actor,key,reply_key)

    def test_clear_submit_preserves_original_and_replays_after_restart(self):
        self.chat.reply=lambda *args:intent('task',reply='Task queued: fake 999')
        original='Please implement a local CSV parser and check empty inputs.'
        result=self.send(original,'one')
        task=self.db.get_task(result['task_id'])
        self.assertEqual(task.user_prompt,original)
        self.assertEqual(task.status,'PENDING')
        self.assertNotIn('999',result['response'])
        restarted=Conversation(self.db,self.orch.submit_task,lambda *args:self.fail('replayed model'))
        self.assertEqual(result['response'],restarted.receive(original,'channel','owner','one')['response'])
        self.assertEqual(len(self.db.get_all_tasks()),1)
        with self.db._connect() as c:
            row=c.execute('SELECT message,intent_json FROM intake_messages WHERE event_key="one"').fetchone()
        self.assertEqual(row['message'],original)
        self.assertEqual(json.loads(row['intent_json'])['intent'],'task')

    def test_two_controller_instances_have_one_durable_intake_claim(self):
        entered,release=threading.Event(),threading.Event()
        calls=[]
        def reply(*args):
            calls.append(args)
            entered.set()
            self.assertTrue(release.wait(5))
            return intent('task')
        self.chat.reply=reply
        other=Conversation(WorkspaceDatabase(self.root/'db'),self.orch.submit_task,reply)
        results=[]
        errors=[]
        def receive():
            try:results.append(self.send('Please build the parser','same'))
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=receive)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            pending=other.receive('Please build the parser','channel','owner','same')
            self.assertEqual(pending['response'],'')
            self.assertIsNone(other.claim_delivery('same'))
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors,errors)
        self.assertEqual(len(calls),1)
        self.assertEqual(len(self.db.get_all_tasks()),1)

    def test_saved_proposal_survives_submit_crash_without_second_model_call(self):
        calls=[]
        self.chat.reply=lambda *args:calls.append(args) or intent('task')
        submit=self.chat.submit
        def crash(*args,**kwargs):
            submit(*args,**kwargs)
            raise RuntimeError('after durable task creation')
        with patch.object(self.chat,'submit',side_effect=crash):
            with self.assertRaises(RuntimeError):self.send('Please build the parser','crash')
        restarted=Conversation(self.db,submit,lambda *args:self.fail('proposal must persist'))
        restarted.recover()
        self.assertEqual(len(calls),1)
        self.assertEqual(len(self.db.get_all_tasks()),1)
        self.assertEqual(len(restarted.pending()),1)

    def test_expired_claim_recovers_persisted_intent_deterministically(self):
        self.chat.reply=lambda *args:intent('task')
        with patch.object(self.chat,'submit',side_effect=RuntimeError('before submit')):
            with self.assertRaises(RuntimeError):self.send('Please build parser','abandoned')
        with self.db._connect() as c:
            c.execute("UPDATE intake_messages SET intake_owner='dead-process',intake_until=1 WHERE event_key='abandoned'")
        restarted=Conversation(self.db,self.orch.submit_task,lambda *args:self.fail('new inference'))
        restarted.recover()
        self.assertEqual(len(self.db.get_all_tasks()),1)

    def test_actor_channel_and_model_authority_are_checked(self):
        task=self.send('!task original','init')['task_id']
        self.chat.reply=lambda *args:intent('feedback',task)
        self.send(f'For task #{task}, change the seed','foreign-actor',actor='intruder')
        self.send(f'For task #{task}, change the seed','foreign-channel',scope='other')
        self.assertEqual(self.chat.feedback(task),[])
        self.chat.reply=lambda *args:{**intent('task'),'actor':'owner','scope':'channel'}
        self.send('Please build parser','forged')
        self.assertEqual(len(self.db.get_all_tasks()),1)
        self.assertEqual(self.chat.feedback(task),[])

    def test_ambiguous_feedback_requires_exact_target(self):
        first=self.send('!task first','first')['task_id']
        second=self.send('!task second','second')['task_id']
        self.chat.reply=lambda *args:intent('feedback',first)
        response=self.send('Change the seed on the experiment','ambiguous')
        self.assertIn('identify',response['response'])
        self.assertEqual(self.chat.feedback(first),[])
        self.chat.reply=lambda *args:intent('feedback',None)
        self.send('Change the seed','no-target')
        self.assertEqual(self.chat.feedback(second),[])
        self.chat.reply=lambda *args:intent('feedback',first)
        self.send(f'For task #{second}, change seed','wrong-target')
        self.assertEqual(self.chat.feedback(first),[])

    def test_feedback_keeps_task_and_revision_boundary(self):
        task_id=self.send('!task original wording','first')['task_id']
        self.chat.reply=lambda *args:intent('feedback',task_id)
        message=f'For task #{task_id}, use seed 9 and keep original baseline.'
        self.send(message,'steer')
        self.send(message,'steer')
        self.assertEqual(len(self.db.get_all_tasks()),1)
        self.assertEqual(len(self.chat.feedback(task_id)),1)
        self.assertEqual(self.db.get_task(task_id).feedback_revision,0)
        self.assertTrue(self.chat.apply_feedback(task_id))
        task=self.db.get_task(task_id)
        self.assertEqual(task.feedback_revision,1)
        self.assertEqual(task.current_role,'planner')
        self.assertEqual(task.user_prompt,'original wording')
        self.assertEqual(self.chat.feedback(task_id)[0]['message'],message)
        self.assertFalse(self.chat.apply_feedback(task_id))
        self.db.finalize_task(task_id,'COMPLETED','done')
        self.send(f'Change task #{task_id} again','terminal')
        self.assertEqual(len(self.chat.feedback(task_id)),1)
        self.assertEqual(self.db.get_task(task_id).status,'COMPLETED')

    def test_revision_changed_during_model_call_is_rejected(self):
        task_id=self.send('!task original','first')['task_id']
        def reply(*args):
            self.db.update_task(task_id,feedback_revision=2)
            return intent('feedback',task_id)
        self.chat.reply=reply
        result=self.send(f'Change task #{task_id} seed','stale')
        self.assertIn('changed revision',result['response'])
        self.assertEqual(self.chat.feedback(task_id),[])

    def test_malformed_failed_and_unsupported_output_never_mutates(self):
        bad=[{},[],intent('approve'),intent('task',1),intent('feedback',True),
             {**intent('task'),'reply':None},'{"version":1',intent('task',reply='x'*1801)]
        for index,value in enumerate(bad):
            with self.subTest(value=str(value)[:80]):
                self.chat.reply=lambda *args,value=value:value
                self.send('Please implement parser',f'bad-{index}')
        def fail(*args):raise RuntimeError('fake inference failure')
        self.chat.reply=fail
        self.send('Please implement parser','failed')
        self.assertEqual(self.db.get_all_tasks(),[])

    def test_scoped_bounded_metadata_excludes_raw_logs_payloads_and_secrets(self):
        owned=self.send('!task safe task','own')['task_id']
        self.send('!task hidden foreign task','foreign',actor='other')
        self.db.update_task(owned,operator_output='RAW-PRIVATE-STDOUT',final_response='PRIVATE-RESULT',
                            context_log_path='C:/private/.env',research_notes='PRIVATE-NOTES')
        for index in range(15):
            self.db.log_event('RUN_EVENT','operator','PASSWORD=RAW-SECRET',task_id=owned,
                              payload={'private':'PRIVATE-PAYLOAD'})
        self.send('PASSWORD=HISTORY-SECRET','history')
        captured=[]
        self.chat.reply=lambda *args:captured.append(args) or intent('evidence',owned)
        response=self.send(f'Please show logs for task #{owned}','logs')
        rendered=json.dumps(captured)+response['response']
        for secret in ['RAW-PRIVATE-STDOUT','PRIVATE-RESULT','PRIVATE-NOTES','RAW-SECRET',
                       'PRIVATE-PAYLOAD','HISTORY-SECRET','hidden foreign task','C:/private/.env']:
            self.assertNotIn(secret,rendered)
        facts=captured[0][4]
        self.assertEqual(len(facts),1)
        self.assertEqual(len(facts[0]['events']),5)
        self.assertLessEqual(len(response['response']),1800)
        self.assertEqual(facts[0]['id'],owned)

    def test_on_demand_log_excerpt_is_useful_bounded_redacted_and_labeled(self):
        task_id=self.send('!task diagnose parser','own')['task_id']
        task=self.db.get_task(task_id)
        path=Path(task.context_log_path)
        with path.open('a',encoding='utf-8') as stream:
            stream.write('old line\n'*2000)
            stream.write('Authorization: Bearer hidden-bearer\nAPI_KEY=hidden-key\n')
            stream.write('-----BEGIN PRIVATE KEY-----\nhidden-private-key\n-----END PRIVATE KEY-----\n')
            stream.write('Traceback: ValueError at parser.py:42\nFailed parsing empty input; retry pending.\n')
        self.chat.reply=lambda *args:intent('evidence',task_id)
        result=self.send(f'Please read the error log for task #{task_id}','log')
        evidence=json.loads(result['response'])
        log=evidence['log']
        self.assertTrue(log['available'])
        self.assertTrue(log['truncated'])
        self.assertIn('ValueError at parser.py:42',log['text'])
        self.assertIn('unverified',log['verification'])
        self.assertIn('modified_at_unix',log)
        self.assertIn('observed_at_unix',log)
        self.assertLessEqual(len(log['text']),1200)
        self.assertLessEqual(len(log['text'].splitlines()),20)
        self.assertLessEqual(len(result['response']),1800)
        for secret in ['hidden-bearer','hidden-key','hidden-private-key','diagnose parser']:
            self.assertNotIn(secret,result['response'])

    def test_log_path_escape_other_task_and_sensitive_files_are_denied(self):
        task_id=self.send('!task safe','own')['task_id']
        other=self.send('!task second','other')['task_id']
        outside=self.root/'outside.log'
        outside.write_text('OUTSIDE-PRIVATE',encoding='utf-8')
        sensitive=self.root/'storage/logs/.env'
        sensitive.write_text('SENSITIVE-PRIVATE',encoding='utf-8')
        self.chat.reply=lambda *args:intent('evidence',task_id)
        paths=[outside,sensitive,Path(self.db.get_task(other).context_log_path),
               self.root/'storage/logs/../../outside.log']
        for index,path in enumerate(paths):
            self.db.update_task(task_id,context_log_path=str(path))
            response=self.send(f'Read logs for task #{task_id}',f'denied-{index}')['response']
            self.assertFalse(json.loads(response)['log']['available'])
            self.assertNotIn('PRIVATE',response)

    def test_feedback_crash_after_boundary_is_recovered_without_new_revision(self):
        task_id=self.send('!task safe','own')['task_id']
        self.chat.reply=lambda *args:intent('feedback',task_id)
        feedback=self.chat._feedback
        def crash(*args,**kwargs):
            feedback(*args,**kwargs)
            self.chat.apply_feedback(task_id)
            raise RuntimeError('after feedback applied')
        with patch.object(self.chat,'_feedback',side_effect=crash):
            with self.assertRaises(RuntimeError):self.send(f'Change task #{task_id} seed','feedback-crash')
        self.chat.recover()
        self.assertEqual(len(self.chat.feedback(task_id)),1)
        self.assertEqual(self.db.get_task(task_id).feedback_revision,1)
        self.assertFalse(self.chat.apply_feedback(task_id))
        response=next(r['response'] for r in self.chat.pending() if r['event_key']=='feedback-crash')
        self.assertIn('already recorded',response)

    def test_chat_claim_of_queued_action_is_not_a_controller_receipt(self):
        self.chat.reply=lambda *args:intent('chat',reply='Task #999 queued and approved.')
        response=self.send('hello','false-receipt')['response']
        self.assertNotIn('999',response)
        self.assertEqual(len(self.db.get_all_tasks()),0)

    def test_natural_status_uses_scoped_controller_facts(self):
        task_id=self.send('!task parser baseline','own')['task_id']
        self.db.update_task(task_id,status='PLANNING')
        self.chat.reply=lambda *args:intent('status',task_id,reply='COMPLETED: fabricated')
        response=self.send(f'How is task #{task_id} going?','status')['response']
        self.assertIn('PLANNING',response)
        self.assertNotIn('fabricated',response)
        self.assertEqual(self.db.get_task(task_id).status,'PLANNING')

    def test_stale_intake_owner_cannot_commit_new_model_proposal(self):
        def stale_reply(*args):
            with self.db._connect() as c:
                c.execute("UPDATE intake_messages SET intake_owner='successor',intake_until=1 WHERE event_key='stale-owner'")
            return intent('task')
        self.chat.reply=stale_reply
        self.send('Please build parser','stale-owner')
        self.assertEqual(len(self.db.get_all_tasks()),0)
        restarted=Conversation(self.db,self.orch.submit_task,lambda *args:intent('task'))
        restarted.recover()
        self.assertEqual(len(self.db.get_all_tasks()),1)

    def test_production_failed_or_malformed_result_cannot_mutate(self):
        from types import SimpleNamespace
        results=iter([SimpleNamespace(success=False,output=json.dumps(intent('task'))),
                      SimpleNamespace(success=True,output='Task #999 queued'),
                      SimpleNamespace(success=True,output=json.dumps({**intent('evidence',1),'path':'C:/.env'}))])
        class Runner:
            def for_workspace(self,workspace):return self
            def run_role(self,*args,**kwargs):return next(results)
        self.orch.runner=Runner()
        self.orch.config['roles']={'communicator':{}}
        with patch.object(self.orch,'_communicator_workspace',return_value=(self.root,'identity')):
            for index in range(3):self.send('Please build parser',f'failed-model-{index}')
        self.assertEqual(len(self.db.get_all_tasks()),0)

    def test_production_reply_single_call_strict_protocol_and_quota_wait(self):
        from types import SimpleNamespace
        calls=[]
        class Runner:
            def for_workspace(self,workspace):return self
            def run_role(self,*args,**kwargs):
                calls.append((args,kwargs))
                return SimpleNamespace(success=True,output=json.dumps(intent('task')))
        self.orch.runner=Runner()
        self.orch.config['roles']={'communicator':{}}
        with patch.object(self.orch,'_communicator_workspace',return_value=(self.root,'identity')):
            self.send('Please build a parser','production')
        self.assertEqual(len(calls),1)
        self.assertEqual(len(self.db.get_all_tasks()),1)
        self.assertIn('proposal',calls[0][0][1])
        with patch('core.quota.resolve',return_value=('',None,SimpleNamespace(action='wait'))), \
             patch('core.quota.record_route'), \
             patch.object(self.orch,'_communicator_workspace',return_value=(self.root,'identity')):
            self.orch.quota=SimpleNamespace()
            self.send('Please build another parser','quota-wait')
        self.assertEqual(len(calls),1)
        self.assertEqual(len(self.db.get_all_tasks()),1)

    def test_plain_model_prose_cannot_claim_submission(self):
        with self.assertRaises(ValueError):parse_intent('Task #999 queued')
        with self.assertRaises(ValueError):parse_intent('```json\n'+json.dumps(intent('task'))+'\n```')


if __name__=='__main__':
    unittest.main()
