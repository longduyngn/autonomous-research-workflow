import tempfile
import unittest
from pathlib import Path
from core.portability import environment_python,site_packages,resolve_command


class PortabilityTests(unittest.TestCase):
    def test_posix_and_windows_environment_layouts(self):
        for executable,packages in [('bin/python','lib/python3.11/site-packages'),('Scripts/python.exe','Lib/site-packages')]:
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);exe=root/executable;exe.parent.mkdir(parents=True);exe.touch()
                (root/packages).mkdir(parents=True)
                self.assertEqual(environment_python(root),exe)
                self.assertEqual(site_packages(root),[root/packages])

    def test_configured_windows_runtime_resolves_posix_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);exe=root/'storage/tools/colab-runtime/bin/python';exe.parent.mkdir(parents=True);exe.touch()
            command=resolve_command(['{controller}/storage/tools/colab-runtime/Scripts/python.exe','-c','print(1)'],root)
            self.assertEqual(command[0],str(exe))
