import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.cloud import CloudTools


class CloudTests(unittest.TestCase):
    def test_failed_runtime_selection_never_runs_the_notebook(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            self.notebook(tools, ["print('must not run on substitute hardware')"])
            original = tools.colab_call
            def call(args):
                if args["name"] == "set_runtime":
                    return {"status": "error", "error": "Requested T4 unavailable"}
                return original(args)
            with patch.object(tools, "colab_connect", return_value={}), patch.object(tools, "colab_call", side_effect=call), patch.object(tools, "_colab", side_effect=AssertionError("No remote execution allowed")):
                with self.assertRaisesRegex(RuntimeError, "could not be selected"):
                    original({"name": "run_notebook", "arguments": {"notebook_path": "run.ipynb", "hardware": "T4"}})
    def test_single_background_cell_is_not_executed_synchronously(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            self.notebook(tools, ["print('only once')"])
            def remote(endpoint, payload):
                if endpoint == '/claim':
                    return {'status': 'claimed'}
                if endpoint == '/browser' and payload.get('name')=='trigger_cell_run':
                    return {'status':'clicked_run_button'}
                if payload.get('name') == 'get_cells':
                    return {'structuredContent': {'cells': []}}
                if payload.get('name') == 'add_code_cell':
                    return {'structuredContent': {'newCellId': 'cell1'}}
                self.fail('Unexpected synchronous call: ' + str(payload))
            with patch.object(tools, 'colab_connect', return_value={}), patch.object(tools, '_colab', side_effect=remote), patch.object(tools, 'colab_call', wraps=tools.colab_call) as call:
                # Patch the skill trigger separately, keeping the real run loop.
                import importlib.util
                with patch('importlib.util.module_from_spec') as module, patch('importlib.util.spec_from_file_location'):
                    module.return_value.trigger_cell_run.return_value = {'status': 'clicked_run_button'}
                    result = tools.colab_call({'name':'run_notebook','arguments':{'notebook_path':'run.ipynb','background_last_cell':True}})
            self.assertEqual(result['status'], 'running_background')
            self.assertEqual(result['sync_outputs'], [])

    def notebook(self, tools, sources):
        for source in sources:
            tools.call("notebook_modify_cell", {"notebook_path": "run.ipynb", "action": "append", "source": source})

    def test_persistent_local_kernel_and_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            self.notebook(tools, ["x = 41", "from pathlib import Path\nPath('result.txt').write_text(str(x + 1))\nprint(x + 1)"])
            result = tools.call("local_execute_notebook", {"notebook_path": "run.ipynb"})
            self.assertEqual(result["status"], "completed")
            self.assertEqual((Path(tmp) / "artifacts/result.txt").read_text(), "42")
            nb = json.loads((Path(tmp) / "run.ipynb").read_text())
            self.assertIn("42", str(nb["cells"][1]["outputs"]))

    def test_failed_cell_stops_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            self.notebook(tools, ["raise ValueError('bad experiment')", "print('should not run')"])
            result = tools.local_execute_notebook({"notebook_path": "run.ipynb"})
            self.assertEqual(result["status"], "error")
            nb = json.loads((Path(tmp) / "run.ipynb").read_text())
            self.assertIsNone(nb["cells"][1]["execution_count"])

    def test_colab_never_falls_back_to_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = CloudTools(tmp).call("colab_execute_notebook", {"notebook_path": "fake.ipynb"})
            self.assertEqual(result["status"], "error")

    def test_kaggle_submission_poll_export_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            self.notebook(tools, ["print('remote')"])
            with patch.object(tools, "_kaggle", side_effect=["pushed", "status: complete", "downloaded"]) as cli:
                job = tools.kaggle_execute_notebook({"notebook_path": "run.ipynb", "hardware": "T4", "kaggle_kernel_slug": "owner/experiment"})
                self.assertEqual(job["state"], "submitted")
                self.assertIn("--accelerator", cli.call_args_list[0].args[0])
                meta = json.loads((Path(tmp) / f".workflow/cloud_jobs/{job['id']}/stage/kernel-metadata.json").read_text())
                self.assertEqual(meta["code_file"], "run.ipynb")
                result = tools.kaggle_export_data({"job_id": job["id"]})
                self.assertEqual(result["status"], "completed")

    def test_cpu_metadata_clears_previous_gpu_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            self.notebook(tools, ["print('cpu')"])
            tools.notebook_select_hardware({"notebook_path": "run.ipynb", "hardware": "T4"})
            result = tools.notebook_select_hardware({"notebook_path": "run.ipynb", "hardware": "CPU"})
            meta = json.loads((Path(tmp) / "run.ipynb").read_text())["metadata"]
            self.assertEqual(meta["workflow_hardware_intent"], "CPU")
            self.assertNotIn("accelerator", meta)
            self.assertIsNone(meta["colab"]["gpuType"])
            self.assertFalse(result["allocated"])

    def test_kaggle_cpu_omits_accelerator_even_with_custom_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            tools.config["kaggle_accelerators"] = {"CPU": "invalid-cpu-accelerator"}
            self.notebook(tools, ["print('cpu')"])
            with patch.object(tools, "_kaggle", return_value="pushed") as cli:
                job = tools.kaggle_execute_notebook({"notebook_path": "run.ipynb", "hardware": "CPU",
                                                    "kaggle_kernel_slug": "owner/cpu-probe"})
            self.assertNotIn("--accelerator", cli.call_args.args[0])
            meta = json.loads((tools._job_path(job["id"]).parent / "stage/kernel-metadata.json").read_text())
            self.assertFalse(meta["enable_gpu"])
            self.assertFalse(meta["enable_tpu"])

    def test_colab_cpu_runs_without_accelerator_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            self.notebook(tools, ["print('remote cpu')"])
            tools.notebook_select_hardware({"notebook_path": "run.ipynb", "hardware": "CPU"})
            original = tools.colab_call
            def high_level(args):
                if args["name"] == "set_runtime":
                    self.assertEqual(args["arguments"]["hardware"], "CPU")
                    return {"status": "selected", "saved": True, "radioValue": ""}
                return original(args)
            with patch.object(tools, "colab_connect", return_value={"status": "connected"}), \
                 patch.object(tools, "colab_call", side_effect=high_level), \
                 patch.object(tools, "_colab", side_effect=[
                     {"structuredContent": {"cells": [{"id": "cpu-cell"}]}},
                     {}, {"content": [{"type": "text", "text": "remote cpu"}]}]):
                result = original({"name": "run_notebook", "arguments": {"notebook_path": "run.ipynb"}})
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["hardware_requested"], "CPU")
            self.assertEqual(result["sync_outputs"][0]["cellId"], "cpu-cell")

    def test_kaggle_fills_missing_python_kernel_before_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            path = Path(tmp) / "minimal.ipynb"
            original = {"nbformat": 4, "nbformat_minor": 5, "metadata": {},
                        "cells": [{"cell_type": "code", "metadata": {}, "source": ["print(42)"],
                                   "outputs": [], "execution_count": None}]}
            path.write_text(json.dumps(original))
            with patch.object(tools, "_kaggle", return_value="pushed"):
                job = tools.kaggle_execute_notebook({"notebook_path": path.name,
                                                    "kaggle_kernel_slug": "owner/minimal"})
            staged = json.loads((Path(tmp) / f".workflow/cloud_jobs/{job['id']}/stage/minimal.ipynb").read_text())
            self.assertEqual(staged["metadata"]["kernelspec"]["name"], "python3")
            self.assertTrue(staged["cells"][0]["id"])
            self.assertEqual(json.loads(path.read_text()), original)

    def test_kaggle_rejects_overlong_slug_before_push(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            self.notebook(tools, ["print(42)"])
            with patch.object(tools, "_kaggle") as cli:
                with self.assertRaisesRegex(ValueError, "5 to 50"):
                    tools.kaggle_execute_notebook({"notebook_path": "run.ipynb",
                                                   "kaggle_kernel_slug": "owner/" + "x" * 51})
                cli.assert_not_called()

    def test_uncertain_submission_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CloudTools(tmp)
            self.notebook(tools, ["print('remote')"])
            with patch.object(tools, "_kaggle", side_effect=TimeoutError("network uncertain")):
                job = tools.kaggle_execute_notebook({"notebook_path": "run.ipynb", "kaggle_kernel_slug": "owner/experiment"})
                self.assertEqual(job["state"], "submission_unknown")
                self.assertTrue(tools._job_path(job["id"]).exists())
                again = tools.kaggle_execute_notebook({"notebook_path": "run.ipynb", "kaggle_kernel_slug": "owner/experiment"})
                self.assertEqual(again["id"], job["id"])
                self.assertTrue(again["reused_submission"])

    def test_cloud_paths_confined(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PermissionError):
                CloudTools(tmp).notebook_modify_cell({"notebook_path": "../outside.ipynb", "action": "append"})

    def test_stdio_mcp_with_notifications(self):
        from core.mcp_client import StdioMCPClient
        script = '''import sys,json
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 result={'protocolVersion':'2024-11-05','capabilities':{}} if m['method']=='initialize' else {'tools':[{'name':'remote_execute','inputSchema':{'type':'object'}}]}
 print(json.dumps({'jsonrpc':'2.0','method':'notifications/tools/list_changed'}),flush=True)
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}),flush=True)
'''
        client = StdioMCPClient([sys.executable, "-c", script])
        try:
            client.start()
            self.assertEqual(client.tools()[0]["name"], "remote_execute")
        finally:
            client.close()
