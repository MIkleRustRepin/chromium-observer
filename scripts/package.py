#!/usr/bin/env python3
"""Package a successful native out/Observer build without object files."""
import platform
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
output = ROOT / "chromium/src/out/Observer"
artifacts = ROOT / "artifacts"
artifacts.mkdir(exist_ok=True)
if platform.system() == "Darwin":
    paths = [output / "Chromium.app"]
    name = "chromium-observer-macos-arm64.tar.gz"
else:
    paths = [output / "chrome"]
    for pattern in ("*.pak", "*.bin", "*.dat", "*.so", "*.so.1", "*_icd.json"):
        paths.extend(sorted(output.glob(pattern)))
    for entry in ("locales", "resources", "chrome_crashpad_handler", "chrome_sandbox", "xdg-mime", "xdg-settings"):
        if (output / entry).exists():
            paths.append(output / entry)
    name = "chromium-observer-linux-x64.tar.gz"
for path in paths:
    if not path.exists():
        raise SystemExit(f"Missing build output: {path}")
with tarfile.open(artifacts / name, "w:gz", compresslevel=3) as archive:
    for path in dict.fromkeys(paths):
        archive.add(path, arcname="chromium-observer/" + path.name)
    archive.add(ROOT / "chromium/src/LICENSE", arcname="chromium-observer/LICENSE")
print(artifacts / name)
