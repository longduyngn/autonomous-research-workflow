import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / '.agents/skills/colab-coccoc-playwright/scripts/drive_checkpoint.py'
spec = importlib.util.spec_from_file_location('colab_drive_skill', SCRIPT)
drive_skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drive_skill)


class DriveDestinationChecks(unittest.TestCase):
    def test_explicit_private_destination_is_allowed(self):
        p='/content/drive/MyDrive/private-research/campaign'
        self.assertEqual(drive_skill.validate_destination(p),p)

    def test_root_traversal_and_other_mounts_are_rejected(self):
        for p in ['/content/drive/MyDrive','/content/tmp','/content/drive/MyDrive-other/x',
                  '/content/drive/MyDrive/a/../../other','relative/path',
                  '/content/drive/MyDrive/a\\..\\other']:
            with self.subTest(path=p), self.assertRaises(ValueError):
                drive_skill.validate_destination(p)

    def test_probe_requires_explicit_destination(self):
        with self.assertRaises(ValueError):drive_skill.build_body(probe=True)

    def test_generated_code_compiles_with_paths_containing_quotes(self):
        body=drive_skill.build_body("/content/drive/MyDrive/private-research/author's campaign",probe=True,expected_boot='known-boot')
        compile(body,'<drive-checkpoint-probe>','exec')


if __name__=='__main__':unittest.main()
