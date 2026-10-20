#!/usr/bin/env python3
"""Apply the observer patch to the exact pinned Chromium revision, idempotently."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "chromium/src"


def apply_observer_patch(src, patch, legacy_dir):
    def check(candidate, reverse=False):
        options = ["--reverse"] if reverse else []
        return subprocess.run(["git", "apply", *options, "--check", str(candidate)],
                              cwd=src, capture_output=True).returncode == 0

    if check(patch, reverse=True):
        print("Observer patch is already applied.")
        return
    if check(patch):
        subprocess.run(["git", "apply", str(patch)], cwd=src, check=True)
        print("Observer patch applied.")
        return
    for previous in sorted(legacy_dir.glob("*.patch")):
        if not check(previous, reverse=True):
            continue
        subprocess.run(["git", "apply", "--reverse", str(previous)], cwd=src, check=True)
        try:
            subprocess.run(["git", "apply", "--check", str(patch)], cwd=src, check=True)
            subprocess.run(["git", "apply", str(patch)], cwd=src, check=True)
        except subprocess.CalledProcessError:
            # Restore the working tree to its previous fork version on failure.
            subprocess.run(["git", "apply", str(previous)], cwd=src, check=True)
            raise
        print(f"Observer patch updated from {previous.name}.")
        return
    # Show Git's diagnostics; preserve edits that do not match a known version.
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=src, check=True)


def main():
    pin = json.loads((ROOT / "upstream.json").read_text())
    patch = ROOT / "patches/observer.patch"
    if not patch.is_file():
        sys.exit("Missing patches/observer.patch")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SRC, text=True).strip()
    if head != pin["revision"]:
        sys.exit(f"Patch expects {pin['revision']}; checkout is at {head}.")
    apply_observer_patch(SRC, patch, ROOT / "patches/legacy")


if __name__ == "__main__":
    main()
