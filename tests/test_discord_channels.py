import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.discord_channels import update_env,discover


class DiscordChannelTests(unittest.TestCase):
    def test_env_only_updates_allowlisted_ids_preserving_secret_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'.env'
            original=b'\xef\xbb\xbfDISCORD_TASK_LOG_CHANNEL_ID=1\r\n# comment\r\nSECRET="fixture=unchanged"\r\nOTHER=value'
            path.write_bytes(original)
            update_env(path,{'task_log':'42','guild_id':'33','SECRET':'99'})
            data=path.read_bytes()
            self.assertIn(b'SECRET="fixture=unchanged"\r\nOTHER=value',data)
            self.assertTrue(data.startswith(b'\xef\xbb\xbfDISCORD_TASK_LOG_CHANNEL_ID=42\r\n'))
            self.assertEqual(data.count(b'DISCORD_TASK_LOG_CHANNEL_ID='),1)
            update_env(path,{'task_log':'42','guild_id':'33'})
            self.assertEqual(path.read_bytes(),data)

    def test_ambiguous_category_never_sends_a_create_request(self):
        categories=[{'id':'10','type':4,'name':'antigravity_playground'},{'id':'11','type':4,'name':'antigravity-playground'}]
        with patch('core.discord_channels.request',side_effect=[[{'id':'1'}],categories]) as call:
            with self.assertRaises(ValueError):discover('fixture',create=True)
            self.assertEqual(call.call_count,2)
