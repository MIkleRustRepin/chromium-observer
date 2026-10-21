"""Exercise Docker orchestration and the Linux worker without compiling Chromium."""
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class UbuntuBuilderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ubuntu-builder-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.repo = self.folder / "repo with spaces"
        self.bin = self.folder / "bin"
        self.repo.mkdir()
        self.bin.mkdir()
        self.trace = self.folder / "trace.jsonl"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        TRACE_LOG=str(self.trace), FAKE_TEMPLATE=str(self.repo),
                        CHROMIUM_OBSERVER_DIR=str(self.folder / "cloned"),
                        PYTHONDONTWRITEBYTECODE="1")
        shutil.copy2(ROOT / "build-ubuntu-on-mac.sh", self.repo)
        shutil.copy2(ROOT / "upstream.json", self.repo)
        shutil.copytree(ROOT / "docker", self.repo / "docker")
        self.git = shutil.which("git")
        subprocess.run([self.git, "init", "--quiet", str(self.repo)], check=True)
        subprocess.run([self.git, "-C", str(self.repo), "add", "."], check=True)
        # These represent a macOS checkout and artifacts; neither may enter Docker.
        (self.repo / "chromium/src/out/Observer").mkdir(parents=True)
        self.mac_binary = self.repo / "chromium/src/out/Observer/Chromium.app"
        self.mac_binary.write_text("existing Mac build")
        self.tool("uname", 'case "$1" in -s) echo Darwin;; -m) echo arm64;; esac')
        self.tool("git", f'''if [[ "$1" == clone ]]; then
  exec {shlex.quote(sys.executable)} -c 'import os,shutil,sys; shutil.copytree(os.environ["FAKE_TEMPLATE"],sys.argv[-1])' "$@"
fi
exec {shlex.quote(self.git)} "$@"''')
        self.python_tool("docker", '''import json,os,pathlib,sys,tarfile
args=sys.argv[1:]
with open(os.environ["TRACE_LOG"],"a") as f:
    f.write(json.dumps({"tool":"docker","args":args})+"\\n")
if args[0]=="info":
    print("linux",os.environ.get("DOCKER_MEMORY","17179869184"),8)
elif args[0]=="create":
    print("test-build-container")
elif args[0]=="start":
    with tarfile.open(fileobj=sys.stdin.buffer,mode="r|*") as archive:
        names=[member.name for member in archive]
    with open(os.environ["TRACE_LOG"],"a") as f:
        f.write(json.dumps({"snapshot":names})+"\\n")
    sys.exit(int(os.environ.get("FAIL_START","0")))
elif args[0]=="cp":
    destination=pathlib.Path(args[-1])
    destination.mkdir(parents=True,exist_ok=True)
    (destination/pathlib.Path(args[1]).name).write_text("fake container output")
elif args[0] not in ("build","rm"):
    sys.exit("unexpected Docker command")
''')

    def tool(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/bash\nset -eu\n" + body + "\n")
        path.chmod(0o755)

    def python_tool(self, name, body):
        source = self.bin / (name + ".py")
        source.write_text(body)
        self.tool(name, "exec " + shlex.quote(sys.executable) + " " + shlex.quote(str(source)) + ' "$@"')

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def run_host(self, *args, script=None, extra_env=None):
        return subprocess.run(["bash", str(script or self.repo / "build-ubuntu-on-mac.sh"), *args],
                              env=self.env | (extra_env or {}), capture_output=True, text=True, timeout=20)

    def test_host_exports_linux_archive_and_preserves_mac_checkout(self):
        result = self.run_host()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        calls = self.calls()
        docker = [call["args"] for call in calls if call.get("tool") == "docker"]
        build = next(call for call in docker if call[0] == "build")
        self.assertEqual("linux/amd64", build[build.index("--platform") + 1])
        self.assertEqual(str(self.repo / "docker/ubuntu"), build[-1])
        create = next(call for call in docker if call[0] == "create")
        self.assertEqual("linux/amd64", create[create.index("--platform") + 1])
        self.assertEqual(["--jobs", "4", "--endpoint", "udp://127.0.0.1:8766", "--scale", "0.75"], create[-6:])
        names = next(call["snapshot"] for call in calls if "snapshot" in call)
        self.assertIn("docker/ubuntu/build.sh", names)
        self.assertFalse(any(name.startswith("chromium/") for name in names))
        self.assertTrue((self.repo / "artifacts/chromium-observer-linux-x64.tar.gz").is_file())
        self.assertEqual("existing Mac build", self.mac_binary.read_text())
        self.assertEqual(["rm", "-f", "test-build-container"], docker[-1])

    def test_host_forwards_options_and_does_not_remove_cache_on_failure(self):
        result = self.run_host("--jobs", "2", "--endpoint", "udp://[::1]:9000", "--scale", "0.5",
                               "--tests", "--clean", "--volume", "custom-cache",
                               extra_env={"FAIL_START": "42"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Cached work is preserved", result.stderr)
        docker = [call["args"] for call in self.calls() if call.get("tool") == "docker"]
        create = next(call for call in docker if call[0] == "create")
        self.assertIn("type=volume,source=custom-cache,target=/workspace", create)
        self.assertEqual(["--jobs", "2", "--endpoint", "udp://[::1]:9000", "--scale", "0.5", "--tests", "--clean"], create[-8:])
        self.assertTrue(any(call[0] == "cp" and call[1].endswith("/logs") for call in docker))
        self.assertFalse(any(call[0] == "volume" or "--volumes" in call or "-v" in call for call in docker))
        self.assertEqual(["rm", "-f", "test-build-container"], docker[-1])
        self.assertEqual("existing Mac build", self.mac_binary.read_text())

    def test_standalone_download_clones_control_repository(self):
        standalone = self.folder / "downloaded.sh"
        shutil.copy2(ROOT / "build-ubuntu-on-mac.sh", standalone)
        result = self.run_host(script=standalone)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.folder / "cloned/artifacts/chromium-observer-linux-x64.tar.gz").is_file())

    def test_low_memory_stops_before_image_build(self):
        result = self.run_host(extra_env={"DOCKER_MEMORY": "4294967296"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("8 GiB", result.stderr)
        self.assertEqual(["info"], [call["args"][0] for call in self.calls()])

    def prepare_worker(self):
        if not shutil.which("rsync") or not shutil.which("flock"):
            self.skipTest("worker tests require rsync and flock")
        self.workspace = self.folder / "linux-cache"
        self.project = self.workspace / "project"
        (self.project / "chromium/src/.git").mkdir(parents=True)
        (self.project / "chromium/src/out/Observer").mkdir(parents=True)
        (self.project / "chromium/src/out/Observer/build.ninja").touch()
        (self.project / "stale-control-file").touch()
        self.tool("df", "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/test 314572800 104857600 209715200 34%% /\\n'")
        self.tool("gclient", 'echo "gclient $*" >> "$WORKER_TRACE"; exit "${FAIL_HOOKS:-0}"')
        self.tool("gn", 'echo "gn $*" >> "$WORKER_TRACE"')
        self.tool("python3", f'''if [[ "$1" == -c ]]; then exec {shlex.quote(sys.executable)} "$@"; fi
echo "python $*" >> "$WORKER_TRACE"
case "$1" in
 scripts/bootstrap.py)
   mkdir -p tools/depot_tools/python-bin chromium/src
   cp "$0" tools/depot_tools/python-bin/python3 ;;
 scripts/apply_patch.py) ;;
 scripts/build.py)
   mkdir -p chromium/src/out/Observer
   touch chromium/src/out/Observer/chrome
   chmod +x chromium/src/out/Observer/chrome ;;
 scripts/package.py)
   mkdir -p artifacts
   touch artifacts/chromium-observer-linux-x64.tar.gz ;;
 *) exit 97 ;;
esac''')
        self.worker_trace = self.folder / "worker-trace"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            content = b"new snapshot\n"
            item = tarfile.TarInfo("control.txt")
            item.size = len(content)
            tar.addfile(item, io.BytesIO(content))
        self.snapshot = archive.getvalue()

    def run_worker(self, *args, extra_env=None):
        env = self.env | {"WORKER_TRACE": str(self.worker_trace)} | (extra_env or {})
        return subprocess.run(["bash", str(ROOT / "docker/ubuntu/build.sh"), str(self.workspace), *args],
                              input=self.snapshot, env=env, capture_output=True, timeout=20)

    def test_worker_uses_linux_cache_and_cleans_only_linux_output(self):
        self.prepare_worker()
        result = self.run_worker("--jobs", "2", "--endpoint", "udp://192.0.2.1:9000", "--scale", "0.75", "--clean", "--tests")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([
            "python scripts/bootstrap.py", "gclient runhooks", "python scripts/apply_patch.py",
            "gn clean out/Observer",
            "python scripts/build.py --jobs 2 --endpoint udp://192.0.2.1:9000 --scale 0.75 --tests",
            "python scripts/package.py",
        ], self.worker_trace.read_text().splitlines())
        self.assertFalse((self.project / "stale-control-file").exists())
        self.assertEqual("new snapshot\n", (self.project / "control.txt").read_text())
        self.assertTrue((self.project / "chromium/src/.git").is_dir())
        self.assertTrue((self.project / "artifacts/chromium-observer-linux-x64.tar.gz").is_file())
        self.assertFalse(list(self.workspace.glob("snapshot.*")))
        self.assertEqual("existing Mac build", self.mac_binary.read_text())

    def test_worker_stops_after_failed_hook_and_can_retry(self):
        self.prepare_worker()
        result = self.run_worker(extra_env={"FAIL_HOOKS": "23"})
        self.assertEqual(23, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("scripts/build.py", self.worker_trace.read_text())
        result = self.run_worker()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.project / "artifacts/chromium-observer-linux-x64.tar.gz").is_file())

    def test_worker_refuses_concurrent_build_before_changing_cache(self):
        self.prepare_worker()
        import fcntl

        with (self.workspace / ".observer-build.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_worker()
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b"Another build", result.stderr)
        self.assertTrue((self.project / "stale-control-file").exists())
        self.assertFalse(self.worker_trace.exists())

    def test_worker_checks_linux_disk_before_downloading(self):
        self.prepare_worker()
        self.tool("df", "printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\n/dev/test 10485760 9437184 1048576 90%% /\\n'")
        result = self.run_worker()
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b"Linux disk needs 15 GiB free", result.stderr)
        self.assertFalse(self.worker_trace.exists())

    def test_worker_rejects_invalid_config_before_changing_cache(self):
        self.prepare_worker()
        for arguments in (("--scale", "nan"), ("--scale", "0"), ("--scale", "1.1"),
                          ("--endpoint", "http://127.0.0.1:8765/api/frame"),
                          ("--endpoint", "udp://example.com:8766")):
            with self.subTest(arguments=arguments):
                result = self.run_worker(*arguments)
                self.assertNotEqual(0, result.returncode)
                self.assertFalse(self.worker_trace.exists())
                self.assertTrue((self.project / "stale-control-file").exists())


if __name__ == "__main__":
    unittest.main()
