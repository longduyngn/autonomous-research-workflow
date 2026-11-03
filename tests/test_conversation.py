import tempfile
import unittest
from pathlib import Path
from core.database import WorkspaceDatabase
from core.orchestrator import HybridOrchestrator
from core.logger import ContextLogManager
from core.conversation import Conversation
from test_workflow import FakeRunner


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.db = WorkspaceDatabase(root/'db')
        self.runner = FakeRunner()
        self.orch = HybridOrchestrator(root, {'isolation': {'enabled': False}}, self.db, ContextLogManager(root/'logs'), self.runner)
        self.chat = self.orch.conversation

    def send(self, text, key, actor='123', scope='456', reply=''):
        return self.chat.receive(text, scope, actor, key, reply)

    def test_chat_dedup_retry_and_scope(self):
        self.send('hello', '1')
        self.assertEqual(len(self.db.get_all_tasks()), 0)
        a = self.send('!task reproduce experiment', '2')
        self.assertEqual(a, self.send('!task reproduce experiment', '2'))
        with self.assertRaises(ValueError):
            self.send('!task replace', '2')
        self.assertIn('reproduce', self.send('!search experiment', '3')['response'])
        self.assertNotIn('reproduce', self.send('!status', '4', actor='intruder')['response'])
        self.send(f"!retry {a['task_id']}", '5')
        self.assertEqual(len(self.db.get_all_tasks()), 2)
        self.assertFalse(self.runner.calls)

    def test_reply_feedback_survives_restart_and_boundary(self):
        a = self.send('!task experiment', '1')
        self.chat.delivered('1', 'bot:1')
        self.send('use seed 42', '2', reply='bot:1')
        self.chat = Conversation(self.db, self.orch.submit_task)
        task = self.db.get_task(a['task_id'])
        self.assertEqual(task.user_prompt, 'experiment')
        self.assertEqual(task.feedback_revision, 0)
        task = self.orch.process_next_step(task)
        self.assertEqual(task.feedback_revision, 1)
        self.assertEqual(task.status, 'OPERATING')
        self.assertFalse(self.chat.apply_feedback(task.id))
        self.assertIn('use seed 42', self.orch._context(task))
        self.assertTrue(self.chat.pending())

    def test_foreign_feedback_and_remote_wait_do_not_mutate(self):
        a = self.send('!task experiment', '1')
        task_id = a['task_id']
        self.send(f'!feedback {task_id} hijack', '2', actor='intruder')
        self.assertEqual(self.chat.feedback(task_id), [])
        self.send(f'!feedback {task_id} change', '3')
        self.db.update_task(task_id, status='WAITING_REMOTE')
        self.assertFalse(self.chat.apply_feedback(task_id))
        self.db.update_task(task_id, status='VALIDATING')
        self.assertTrue(self.chat.apply_feedback(task_id))
        self.assertEqual(self.db.get_task(task_id).status, 'RESEARCHING')

    def test_crash_after_submit_is_recovered_without_duplicate_task(self):
        from unittest.mock import patch
        original=self.chat.submit
        def crash(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('crash after durable submission')
        with patch.object(self.chat,'submit',side_effect=crash):
            with self.assertRaises(RuntimeError):self.send('!task experiment','1')
        self.chat.recover()
        self.assertEqual(len(self.db.get_all_tasks()),1)
        self.assertEqual(len(self.chat.pending()),1)

    def test_feedback_arriving_during_report_prevents_stale_completion(self):
        a=self.send('!task experiment','1');task_id=a['task_id']
        self.db.update_task(task_id,status='COMMUNICATING',outcome='COMPLETED')
        self.send(f'!feedback {task_id} also check edge cases','2')
        task=self.db.finalize_task(task_id,'COMPLETED','stale report',expected_feedback_revision=0)
        self.assertNotEqual(task.status,'COMPLETED')
        self.assertTrue(self.chat.apply_feedback(task_id))
        self.assertEqual(self.db.get_task(task_id).status,'RESEARCHING')

    def test_optional_chat_reply_gets_scoped_memory_without_creating_work(self):
        calls=[]
        self.chat.reply=lambda *args:calls.append(args) or 'What would you like to investigate?'
        self.send('hello','1')
        self.send('we discussed RSA','2')
        self.assertEqual(len(calls[1][3]),1)
        self.send('hello','3',actor='other')
        self.assertFalse(calls[2][3])
        self.assertEqual(len(self.db.get_all_tasks()),0)

    def test_production_discord_handler_routes_chat_task_and_reply(self):
        import asyncio
        import discord
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from gateways.discord_bot import DiscordBotRunner
        channel=MagicMock(spec=discord.DMChannel);channel.id=456
        channel.send=AsyncMock(side_effect=[SimpleNamespace(id=1001),SimpleNamespace(id=1002),SimpleNamespace(id=1003)])
        def message(text,key,reply=None):
            return SimpleNamespace(author=SimpleNamespace(bot=False,id=123),channel=channel,
                content=text,id=key,attachments=[],mentions=[],reference=reply)
        with patch.dict('os.environ',{'DISCORD_ALLOWED_USER_IDS':'123'}):
            bot=DiscordBotRunner('fixture',self.orch.submit_task,lambda:{},conversation=self.chat).create_bot()
        bot._connection.user=SimpleNamespace(id=999999,name='assistance',discriminator='0000',bot=True)
        asyncio.run(bot.on_message(message('hello',1)))
        self.assertFalse(self.db.get_all_tasks())
        asyncio.run(bot.on_message(message('!task experiment',2)))
        task=self.db.get_all_tasks()[0]
        asyncio.run(bot.on_message(message('use seed 9',3,SimpleNamespace(message_id=1002))))
        self.assertEqual(len(self.db.get_all_tasks()),1)
        self.assertEqual(self.chat.feedback(task.id)[0]['message'],'use seed 9')

    def test_unapplied_feedback_excluded_from_role_prompt_until_boundary(self):
        import json
        a=self.send('!task experiment','1');task_id=a['task_id']
        self.send(f'!feedback {task_id} unapplied mid-step note','2')
        task=self.db.get_task(task_id)
        prompt=self.orch._context(task)
        self.assertNotIn('unapplied mid-step note',prompt)
        handoff=json.loads((Path(self.tmp.name)/'.workflow/handoff.json').read_text(encoding='utf-8'))
        self.assertEqual(handoff['user_feedback'],[])
        self.assertEqual(handoff['pending_user_feedback'][0]['message'],'unapplied mid-step note')
        self.assertTrue(self.chat.apply_feedback(task_id))
        task=self.db.get_task(task_id)
        self.assertIn('unapplied mid-step note',self.orch._context(task))

    def test_chat_sentences_starting_with_command_words_do_not_trigger_commands(self):
        from core.control import ControlPlane
        created = self.send('!task initial experiment', 'init-1')
        task_id = created['task_id']
        cp = ControlPlane(self.db)
        approval_key = cp.request_approval(task_id, '123', {'exact': 'action'})
        utterances = [
            'task 19 failed again; what happened?',
            f'task {task_id} failed again; what happened?',
            f'retry {task_id} seems risky right now',
            f'cancel {task_id} if you think it is broken?',
            f'feedback {task_id} should we change learning rate?',
            f'approve {approval_key}',
            f'deny {approval_key}',
            'status of the cluster looks fine',
            'search results were inconclusive',
        ]
        for idx, text in enumerate(utterances, start=10):
            res = self.send(text, f'chat-{idx}')
            self.assertIsNone(res['task_id'], text)
            self.assertIn('no new task was started', res['response'].lower())
        self.assertEqual(len(self.db.get_all_tasks()), 1)
        self.assertEqual(self.chat.feedback(task_id), [])
        self.assertEqual(cp.approval(approval_key)['decision'], 'PENDING')
        explicit_res = self.chat.receive('run explicit caller task', '456', '123', 'explicit-1', explicit=True)
        self.assertIsNotNone(explicit_res['task_id'])
        self.assertEqual(len(self.db.get_all_tasks()), 2)

    def test_playground_channel_group_routes_proposal_approval_and_log(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from core.control import ControlPlane
        from gateways.discord_bot import DiscordBotRunner

        cat = SimpleNamespace(id=900, name='antigravity_playground', type=SimpleNamespace(name='category'))
        ch_proposal = SimpleNamespace(id=901, name='task_proposal', category_id=900, send=AsyncMock(return_value=SimpleNamespace(id=2001)))
        ch_approval = SimpleNamespace(id=902, name='approval list', category_id=900, send=AsyncMock(return_value=SimpleNamespace(id=2002)))
        ch_log = SimpleNamespace(id=903, name='task_log', category_id=900, send=AsyncMock(return_value=SimpleNamespace(id=2003)))
        guild = SimpleNamespace(id=1, name='WorkspaceServer', channels=[cat, ch_proposal, ch_approval, ch_log])

        with patch.dict('os.environ', {'DISCORD_ALLOWED_USER_IDS': '123'}):
            runner = DiscordBotRunner('fixture', self.orch.submit_task, lambda: {}, conversation=self.chat)
            bot = runner.create_bot()
        bot._connection.user = SimpleNamespace(id=999999, name='assistance', discriminator='0000', bot=True)
        runner.playground_channels = runner._resolve_guild_channels([guild])
        self.assertEqual(runner.playground_channels, {'task_proposal': '901', 'approval_list': '902', 'task_log': '903'})

        def msg(channel, text, mid):
            return SimpleNamespace(author=SimpleNamespace(bot=False, id=123), channel=channel,
                                   content=text, id=mid, attachments=[], mentions=[], reference=None)

        # 1. Submitting a new task directly in #task_proposal
        asyncio.run(bot.on_message(msg(ch_proposal, '!task reproduce transformer baseline', 10)))
        tasks = self.db.get_all_tasks()
        self.assertEqual(len(tasks), 1)
        task_id = tasks[0].id

        # 2. Approving a pending action in #approval-list
        cp = ControlPlane(self.db)
        key = cp.request_approval(task_id, '123', {'tool': 'Bash', 'command': 'python run.py'})
        asyncio.run(bot.on_message(msg(ch_approval, f'approve {key}', 11)))
        self.assertEqual(cp.approval(key)['decision'], 'APPROVED')

        # 3. Querying progress in #task_log
        asyncio.run(bot.on_message(msg(ch_log, '!status', 12)))
        sent_log_text = ch_log.send.call_args.args[0]
        self.assertIn('reproduce transformer baseline', sent_log_text)

    def test_channel_discovery_ignores_other_categories(self):
        from types import SimpleNamespace as S
        from unittest.mock import patch
        from gateways.discord_bot import DiscordBotRunner
        with patch.dict('os.environ', {'DISCORD_ALLOWED_USER_IDS':'123'}):
            runner=DiscordBotRunner('fixture',self.orch.submit_task,lambda:{})
        category=S(id=900,name='antigravity_playground',type=S(name='category'))
        unrelated=S(id=800,name='other',type=S(name='category'))
        wrong=S(id=801,name='task_proposal',category_id=800)
        right=S(id=901,name='task_proposal',category_id=900)
        self.assertEqual(runner._resolve_guild_channels([S(channels=[unrelated,wrong])]),{})
        self.assertEqual(runner._resolve_guild_channels([S(channels=[unrelated,wrong,category,right])]),{'task_proposal':'901'})

    def test_approval_requires_original_actor_and_channel(self):
        from core.control import ControlPlane
        task_id=self.send('!task experiment','1')['task_id'];cp=ControlPlane(self.db)
        key=cp.request_approval(task_id,'123',{'exact':'action'})
        self.send('!approve '+key,'2',scope='foreign')
        self.send('!approve '+key,'3',actor='intruder')
        self.assertEqual(cp.approval(key)['decision'],'PENDING')
        self.send('!approve '+key,'4')
        self.assertEqual(cp.approval(key)['decision'],'APPROVED')
