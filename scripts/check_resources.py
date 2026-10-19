#!/usr/bin/env python3
"""Exit nonzero before a download/build when disk headroom is insufficient."""
import argparse
import pathlib
import shutil
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--min-free-gib", type=int, default=15)
parser.add_argument("--path", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[1])
args = parser.parse_args()
free = shutil.disk_usage(args.path).free / 1024**3
print(f"Free disk: {free:.1f} GiB (required reserve: {args.min_free_gib} GiB)")
sys.exit(0 if free >= args.min_free_gib else 1)
