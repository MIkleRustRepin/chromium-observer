"""Check macOS builder orchestration using fake platform/download/build tools.

These tests do not compile Chromium or validate the macOS binary.
Run: python3 -m unittest discover -s scripts -p test_build_mac.py -v
"""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "build-mac.sh"


class MacBuilderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mac-builder-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.repo = self.folder / "repo"
        self.bin = self.folder / "bin"
        self.repo.mkdir()
        self.bin.mkdir()
        (self.repo / "scripts").mkdir()
        (self.repo / "upstream.json").write_text("{}")
        (self.repo / "scripts/bootstrap.py").touch()
        shutil.copy2(SCRIPT, self.repo / "build-mac.sh")
        self.trace = self.folder / "trace"
        sdk = self.folder / "sdk"
        sdk.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        DEVELOPER_DIR=str(self.folder / "Xcode.app/Contents/Developer"),
                        FAKE_SDK=str(sdk), TRACE_LOG=str(self.trace),
                        FAKE_TEMPLATE=str(self.repo),
                        CHROMIUM_OBSERVER_DIR=str(self.folder / "cloned"))
        self.tool("uname", 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac')
        self.tool("xcodebuild", "exit 0")
        self.tool("xcrun", 'case "$*" in *--show-sdk-path*) echo "$FAKE_SDK";; *) echo 26.5;; esac')
        self.tool("df", "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/test 314572800 104857600 209715200 34%% /\\n'")
        self.tool("diskutil", "printf '<?xml version=\"1.0\"?><plist version=\"1.0\"><dict><key>FilesystemType</key><string>apfs</string></dict></plist>'")
        self.tool("sysctl", 'case "$*" in *hw.memsize) echo 17179869184;; *) echo 8;; esac')
        self.tool("caffeinate", "exec sleep 30")
        self.tool("gclient", 'echo "gclient $*" >> "$TRACE_LOG"; exit "${FAIL_HOOKS:-0}"')
        self.tool("gn", 'echo "gn $*" >> "$TRACE_LOG"')
        self.tool("git", "exec " + shlex.quote(sys.executable) + " - \"$@\" <<'PY'\n"
                  "import os,shutil,sys\n"
                  "assert sys.argv[1] == 'clone', sys.argv\n"
                  "shutil.copytree(os.environ['FAKE_TEMPLATE'], sys.argv[-1])\n"
                  "with open(os.environ['TRACE_LOG'],'a') as f: f.write('git clone\\n')\nPY")
        self.tool("python3", f'''if [[ "$1" == -c ]]; then exec {shlex.quote(sys.executable)} "$@"; fi
echo "python $*" >> "$TRACE_LOG"
case "$1" in
  scripts/bootstrap.py)
    mkdir -p tools/depot_tools/python-bin chromium/src
    cp "$0" tools/depot_tools/python-bin/python3
    ;;
  scripts/apply_patch.py) ;;
  scripts/build.py)
    mkdir -p chromium/src/out/Observer/Chromium.app/Contents/MacOS
    touch chromium/src/out/Observer/Chromium.app/Contents/MacOS/Chromium
    chmod +x chromium/src/out/Observer/Chromium.app/Contents/MacOS/Chromium
    ;;
  scripts/package.py)
    mkdir -p artifacts
    touch artifacts/chromium-observer-macos-arm64.tar.gz
    ;;
  *) exit 97 ;;
esac''')

    def tool(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        path.chmod(0o755)

    def run_script(self, *args, script=None, extra_env=None):
        env = self.env | (extra_env or {})
        return subprocess.run(["bash", str(script or self.repo / "build-mac.sh"), *args],
                              env=env, capture_output=True, text=True, timeout=15)

    def test_pipeline_auto_jobs_and_custom_options(self):
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([
            "python scripts/bootstrap.py", "gclient runhooks",
            "python scripts/apply_patch.py",
            "python scripts/build.py --jobs 2 --endpoint http://127.0.0.1:8765/api/frame",
            "python scripts/package.py",
        ], self.trace.read_text().splitlines())
        self.assertTrue((self.repo / "artifacts/chromium-observer-macos-arm64.tar.gz").exists())
        self.assertFalse((self.repo / ".mac-build.lock").exists())
        self.trace.write_text("")
        (self.repo / "chromium/src/out/Observer/build.ninja").touch()
        result = self.run_script("--jobs", "4", "--endpoint", "http://127.0.0.1:9999/api/frame", "--tests", "--clean")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        calls = self.trace.read_text().splitlines()
        self.assertIn("gn clean out/Observer", calls)
        self.assertIn("python scripts/build.py --jobs 4 --endpoint http://127.0.0.1:9999/api/frame --tests", calls)

    def test_failed_hook_stops_build_and_unlocks_for_retry(self):
        result = self.run_script(extra_env={"FAIL_HOOKS": "23"})
        self.assertEqual(23, result.returncode, result.stdout + result.stderr)
        self.assertIn("Chromium build hooks", result.stdout + result.stderr)
        self.assertNotIn("scripts/build.py", self.trace.read_text())
        self.assertFalse((self.repo / ".mac-build.lock").exists())
        self.assertEqual(0, self.run_script().returncode)

    def test_standalone_script_clones_project_and_builds_without_arguments(self):
        standalone = self.folder / "downloaded-build-mac.sh"
        shutil.copy2(SCRIPT, standalone)
        result = self.run_script(script=standalone)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue(self.trace.read_text().startswith("git clone\n"))
        self.assertTrue((self.folder / "cloned/artifacts/chromium-observer-macos-arm64.tar.gz").exists())

    def test_help_and_platform_guard(self):
        result = subprocess.run(["bash", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(0, result.returncode)
        if sys.platform != "darwin":
            result = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("on macOS", result.stderr)


if __name__ == "__main__":
    unittest.main()
