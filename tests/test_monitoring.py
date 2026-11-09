import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from core.database import WorkspaceDatabase
from core.monitoring import ProgressStore, task_jobs
from core.experiment_progress import ExperimentProgress


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.db=WorkspaceDatabase(self.root/'db')
        self.task=self.db.create_task('experiment',logs_dir=self.root/'logs')
        self.store=ProgressStore(self.db)

    def register(self,**kwargs):
        return self.store.register(self.task.id,'run1','colab',total=100,now=100,**kwargs)[0]

    def test_job_identity_survives_restart_and_changed_request_is_rejected(self):
        job=self.register()
        second,new=ProgressStore(self.db).register(self.task.id,'run1','colab',total=100,now=110)
        self.assertFalse(new);self.assertEqual(second['id'],job['id'])
        self.assertEqual(self.db.get_task(self.task.id).remote_job_id,job['id'])
        with self.assertRaises(ValueError):self.store.register(self.task.id,'run1','colab',total=99)

    def test_live_heartbeat_does_not_count_as_work_or_scope_change(self):
        job=self.register()
        r=self.store.observe(job['id'],{'completed':0,'heartbeat_at':1000},now=1000)
        self.assertEqual(r['progress_at'],100)
        self.assertTrue(self.store.claim_diagnostic(job['id'],'diagnostician',now=1001))
        with self.assertRaises(ValueError):self.store.observe(job['id'],{'total':1})

    def test_diagnostic_is_single_claim_bounded_and_invalidated_by_progress(self):
        job=self.register(stall_seconds=10)
        self.assertTrue(self.store.claim_diagnostic(job['id'],'one',now=111))
        self.assertFalse(ProgressStore(self.db).claim_diagnostic(job['id'],'two',now=111))
        self.store.observe(job['id'],{'completed':1},now=112)
        self.assertFalse(self.store.finish_diagnostic(job['id'],'one',{'assessment':'STALLED'}))
        self.assertFalse(self.store.get(job['id'])['diagnosis'])

    def test_unknown_does_not_resubmit_or_clear_task_and_terminal_is_fenced(self):
        job=self.register()
        self.store.unavailable(job['id'],'TimeoutError',now=200)
        self.assertEqual(self.db.get_task(self.task.id).remote_job_id,job['id'])
        self.assertEqual(self.store.get(job['id'])['state'],'REGISTERED')
        self.store.observe(job['id'],{'completed':100,'state':'COMPLETED'},now=201)
        self.store.observe(job['id'],{'completed':0,'state':'RUNNING'},now=202)
        self.assertEqual(self.store.get(job['id'])['state'],'COMPLETED')

    def test_metrics_reject_secrets_and_nonfinite_values(self):
        job=self.register()
        for metrics in [{'WANDB_API_KEY':123},{'loss':float('nan')}]:
            with self.assertRaises(ValueError):self.store.observe(job['id'],{'metrics':metrics})

    def test_portable_writer_offline_receipt_and_monotonicity(self):
        sdk=MagicMock();sdk.init.return_value.path=['entity','project','run']
        writer=ExperimentProgress('job',self.root/'progress',total=10,mode='offline',sdk=sdk)
        writer.update(2,stage='detect',metrics={'loss':.5})
        self.assertEqual(json.loads(writer.path.read_text())['completed'],2)
        with self.assertRaises(ValueError):writer.update(1)
        with self.assertRaises(ValueError):writer.update(2,total=2)
        writer.finish(); self.assertEqual(json.loads(writer.path.read_text())['state'],'COMPLETED')
        self.assertFalse(writer.data.get('wandb_url'))

    def test_sdk_failure_keeps_local_progress(self):
        sdk=MagicMock();sdk.init.side_effect=RuntimeError('do not leak this exception')
        writer=ExperimentProgress('job',self.root/'progress',total=10,mode='online',sdk=sdk)
        self.addCleanup(writer.finish)
        writer.update(3)
        saved=json.loads(writer.path.read_text())
        self.assertEqual(saved['completed'],3)
        self.assertEqual(saved['tracking_status'],'unavailable:RuntimeError')
        self.assertNotIn('leak',writer.path.read_text())

    def test_readonly_view_does_not_make_network_calls(self):
        self.register();self.assertEqual(len(task_jobs(self.db,self.task.id)),1)

    def test_wandb_outage_is_not_job_failure_or_resubmission(self):
        from core.monitoring import poll_job
        from unittest.mock import patch
        job=self.register();self.store.attach_wandb(job['id'],'entity/project/run')
        self.store.observe(job['id'],{'completed':1,'state':'RUNNING'})
        with patch('core.monitoring.wandb_observation',side_effect=TimeoutError),patch('core.cloud._is_port_open',return_value=False):
            result=poll_job(self.store,self.store.get(job['id']),self.root,{})
        self.assertEqual(result['state'],'RUNNING');self.assertEqual(result['completed'],1)
        self.assertTrue(result['telemetry_status'].startswith('unavailable:'))
        self.assertEqual(len(self.store.jobs()),1)

    def test_invalid_diagnostic_result_is_unknown_and_deduplicated(self):
        job=self.register(stall_seconds=1)
        self.assertTrue(self.store.claim_diagnostic(job['id'],'one',now=102))
        self.store.finish_diagnostic(job['id'],'one',{'assessment':'KILL','reason':'unsafe advice'})
        self.assertEqual(self.store.get(job['id'])['diagnosis']['assessment'],'UNKNOWN')
        self.assertFalse(self.store.claim_diagnostic(job['id'],'two',now=1000))

    def test_job_budget_checkpoints_at_main_boundary_before_stopping(self):
        import time
        callback=MagicMock()
        writer=ExperimentProgress('budget',self.root/'progress',total=10,mode='disabled',
                                  heartbeat_seconds=0,budget_seconds=1,checkpoint_callback=callback)
        writer.data['created_at']=time.time()-2
        with self.assertRaises(TimeoutError):writer.update(1)
        callback.assert_called_once()
        self.assertEqual(writer.data['exit_code'],124)
        self.assertEqual(json.loads(writer.path.read_text())['state'],'FAILED')

    def test_gpu_heartbeat_updates_signal_but_not_completed_work(self):
        from unittest.mock import patch
        writer=ExperimentProgress('gpu',self.root/'progress',total=10,mode='disabled',heartbeat_seconds=0)
        writer.update(2)
        with patch('subprocess.check_output',return_value='73, 12345\n'):
            writer._heartbeat_once()
        self.assertEqual(writer.data['completed'],2)
        self.assertEqual(writer.data['metrics']['gpu_util_pct'],73)
        writer.finish()


if __name__=='__main__':unittest.main()
