#!/usr/bin/env python3
"""Fetch the pinned Chromium revision and native-platform dependencies."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
pin = json.loads((ROOT / "upstream.json").read_text())
checkout = ROOT / "chromium"
src = checkout / "src"
depot = ROOT / "tools" / "depot_tools"


def run(*args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True, env=env)


minimum_free = 15 if (src / ".git").exists() else 100
if shutil.disk_usage(ROOT).free < minimum_free * 1024**3:
    sys.exit(f"Need at least {minimum_free} GiB free to continue bootstrap.")
env = os.environ.copy()
env["PATH"] = str(depot) + os.pathsep + env["PATH"]
env["DEPOT_TOOLS_UPDATE"] = "0"
if not depot.exists():
    run("git", "clone", "--depth=1", "https://chromium.googlesource.com/chromium/tools/depot_tools.git", str(depot))
depot_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=depot, text=True).strip()
if depot_head != pin["depot_tools_revision"]:
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=depot):
        sys.exit("depot_tools has local modifications; refusing to replace them.")
    run("git", "fetch", "--depth=1", "origin", pin["depot_tools_revision"], cwd=depot)
    run("git", "checkout", "--detach", pin["depot_tools_revision"], cwd=depot)
if not (depot / "python3_bin_reldir.txt").exists():
    run(str(depot / "ensure_bootstrap"), cwd=depot)
    if not (depot / "python3_bin_reldir.txt").exists():
        sys.exit("depot_tools Python bootstrap did not complete; inspect ensure_bootstrap output.")
checkout.mkdir(exist_ok=True)
if not (src / ".git").exists():
    src.mkdir(exist_ok=True)
    run("git", "init", cwd=src)
    run("git", "remote", "add", "origin", pin["repository"], cwd=src)
revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True)
if revision.returncode or revision.stdout.strip() != pin["revision"]:
    if not revision.returncode:
        sys.exit("Existing Chromium checkout differs from pin; preserve local changes and inspect it first.")
    run("git", "fetch", "--depth=1", "origin", "refs/tags/" + pin["version"], cwd=src)
    run("git", "checkout", "-b", "observer", "FETCH_HEAD", cwd=src)
actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=src, text=True).strip()
if actual != pin["revision"]:
    sys.exit(f"Unexpected revision {actual}; expected {pin['revision']}")
config = "solutions = " + repr([{
    "name": "src", "url": pin["repository"] + "@" + pin["revision"], "managed": False,
    "custom_deps": {}, "custom_vars": {"checkout_pgo_profiles": False},
}]) + "\ntarget_os = []\n"
path = checkout / ".gclient"
previous_config = config.replace(pin["repository"] + "@" + pin["revision"], pin["repository"])
if path.exists() and path.read_text() not in (config, previous_config):
    sys.exit("Existing .gclient differs; refusing to overwrite it.")
path.write_text(config)
run("gclient", "sync", "--no-history", "--nohooks", "-j", "4", cwd=checkout)
print("Dependencies fetched. Install native build dependencies, then run gclient runhooks.")
