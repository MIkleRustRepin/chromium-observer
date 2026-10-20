"""Exercise namespace isolation while preserving host files and checkout packages."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from build_isolation import isolate_build_command


@unittest.skipUnless(sys.platform == "linux", "Linux build isolation")
class BuildIsolationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.checkout = root / "checkout"
        self.checkout.mkdir()
        self.parent_modules = root / "node_modules"
        self.parent_modules.mkdir()
        self.external = self.parent_modules / "external.txt"
        self.external.write_text("keep this package")
        self.internal = self.checkout / "third_party/node/node_modules/internal.txt"
        self.internal.parent.mkdir(parents=True)
        self.internal.write_text("vendored package")

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap is required")
    def test_real_namespace_hides_parent_and_preserves_host_and_vendor(self):
        code = ("from pathlib import Path; import sys, subprocess; "
                "assert not Path(sys.argv[1]).exists(); "
                "assert Path(sys.argv[2]).read_text() == 'vendored package'; "
                "subprocess.run(['/bin/true'], stdin=subprocess.DEVNULL, check=True)")
        command = isolate_build_command(
            [sys.executable, "-c", code, str(self.external), str(self.internal)], self.checkout)
        subprocess.run(command, check=True)
        self.assertEqual(self.external.read_text(), "keep this package")

    def test_missing_bubblewrap_fails_before_build(self):
        with patch("build_isolation.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Install bubblewrap"):
                isolate_build_command(["autoninja"], self.checkout)

    def test_macos_command_is_unchanged(self):
        with patch("build_isolation.sys.platform", "darwin"):
            self.assertEqual(isolate_build_command(["autoninja"], self.checkout), ["autoninja"])


if __name__ == "__main__":
    unittest.main()
