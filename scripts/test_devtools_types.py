"""Verify dependency isolation without replacing user-managed packages."""
from pathlib import Path
import tempfile
import unittest

from devtools_types import prepare_devtools_types


class DevtoolsTypesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.src = Path(self.temp.name)
        third_party = self.src / "third_party/devtools-frontend/src/front_end/third_party"
        self.vendor = third_party / "puppeteer/puppeteer.ts"
        self.vendor.parent.mkdir(parents=True)
        self.vendor.touch()
        self.replay = third_party / "puppeteer-replay/package"
        (self.replay / "lib").mkdir(parents=True)
        (self.replay / "lib/main.d.ts").touch()
        self.alias = self.replay / "node_modules/puppeteer"

    def test_alias_resolves_to_vendored_classes_and_repeat_preserves_mtime(self):
        prepare_devtools_types(self.src)
        declaration = self.alias / "index.d.ts"
        relative = declaration.read_text().split('"')[1]
        self.assertEqual((self.alias / relative).resolve(), self.vendor.with_suffix(".js"))
        before = declaration.stat().st_mtime_ns
        prepare_devtools_types(self.src)
        self.assertEqual(before, declaration.stat().st_mtime_ns)

    def test_existing_dependency_or_symlink_is_not_overwritten(self):
        self.alias.mkdir(parents=True)
        manifest = self.alias / "package.json"
        manifest.write_text('{"name":"user-package"}')
        with self.assertRaisesRegex(RuntimeError, "existing dependency"):
            prepare_devtools_types(self.src)
        self.assertEqual(manifest.read_text(), '{"name":"user-package"}')
        manifest.unlink()
        self.alias.rmdir()
        self.alias.symlink_to(self.vendor.parent, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            prepare_devtools_types(self.src)
        self.assertTrue(self.vendor.exists())

    def test_missing_dependencies_fail_without_creating_alias(self):
        self.vendor.unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing DevTools"):
            prepare_devtools_types(self.src)
        self.assertFalse(self.alias.exists())


if __name__ == "__main__":
    unittest.main()
