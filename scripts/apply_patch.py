#!/usr/bin/env python3
"""Apply the observer patch to the exact pinned Chromium revision, idempotently."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "chromium/src"
pin = json.loads((ROOT / "upstream.json").read_text())
patch = ROOT / "patches/observer.patch"
if not patch.is_file():
    sys.exit("Missing patches/observer.patch")
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SRC, text=True).strip()
if head != pin["revision"]:
    sys.exit(f"Patch expects {pin['revision']}; checkout is at {head}.")
command = ["git", "apply", "--check", str(patch)]
if subprocess.run(command, cwd=SRC, capture_output=True).returncode == 0:
    subprocess.run(["git", "apply", str(patch)], cwd=SRC, check=True)
    print("Observer patch applied.")
elif subprocess.run(["git", "apply", "--reverse", "--check", str(patch)], cwd=SRC, capture_output=True).returncode == 0:
    print("Observer patch is already applied.")
else:
    subprocess.run(command, cwd=SRC, check=True)
