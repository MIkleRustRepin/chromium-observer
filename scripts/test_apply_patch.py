"""Exercise fresh installation, upgrades, and rollback in a real temporary Git repo."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from apply_patch import apply_observer_patch


class PatchUpgradeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.src = root / "src"
        self.src.mkdir()
        self.legacy = root / "legacy"
        self.legacy.mkdir()
        self.current = root / "current.patch"
        self.config = self.src / "config.txt"
        self.source = self.src / "capture.cc"
        self.git("init", "-q")
        self.config.write_text("upstream\n")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "base")
        self.config.write_text("observer\n")
        self.source.write_text("native capture v1\n")
        self.git("add", "-N", "capture.cc")
        (self.legacy / "v1.patch").write_bytes(self.git("diff", "--binary", "HEAD"))
        self.source.write_text("native capture v2 with scale\n")
        self.current.write_bytes(self.git("diff", "--binary", "HEAD"))
        self.git("reset", "--hard", "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.src, stderr=subprocess.PIPE)

    def apply(self):
        apply_observer_patch(self.src, self.current, self.legacy)

    def test_fresh_apply_and_repeat(self):
        self.apply()
        before = self.git("status", "--porcelain")
        self.apply()
        self.assertEqual(self.source.read_text(), "native capture v2 with scale\n")
        self.assertEqual(before, self.git("status", "--porcelain"))

    def test_upgrade_preserves_unrelated_changes(self):
        self.git("apply", str(self.legacy / "v1.patch"))
        other = self.src / "user.txt"
        other.write_text("user data\n")
        self.apply()
        self.assertEqual(self.source.read_text(), "native capture v2 with scale\n")
        self.assertEqual(other.read_text(), "user data\n")

    def test_edited_capture_is_preserved_and_upgrade_rejected(self):
        self.git("apply", str(self.legacy / "v1.patch"))
        self.source.write_text("user edited capture\n")
        with self.assertRaises(subprocess.CalledProcessError):
            self.apply()
        self.assertEqual(self.source.read_text(), "user edited capture\n")
        self.assertEqual(self.config.read_text(), "observer\n")

    def test_failed_new_patch_restores_previous_version(self):
        self.git("apply", str(self.legacy / "v1.patch"))
        self.current.write_bytes(self.current.read_bytes().replace(b"-upstream", b"-wrong base"))
        with self.assertRaises(subprocess.CalledProcessError):
            self.apply()
        self.assertEqual(self.source.read_text(), "native capture v1\n")
        self.assertEqual(self.config.read_text(), "observer\n")


if __name__ == "__main__":
    unittest.main()
