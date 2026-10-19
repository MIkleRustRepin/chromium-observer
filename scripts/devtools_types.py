"""Keep Recorder's type-only Puppeteer import inside the Chromium checkout."""
import json
import os
from pathlib import Path


def prepare_devtools_types(src: Path) -> None:
    devtools = src / "third_party/devtools-frontend/src"
    replay = devtools / "front_end/third_party/puppeteer-replay/package"
    puppeteer = devtools / "front_end/third_party/puppeteer/puppeteer.ts"
    if not puppeteer.is_file() or not (replay / "lib/main.d.ts").is_file():
        raise RuntimeError("Missing DevTools dependencies; run bootstrap.py and gclient runhooks first.")

    # Replay imports 'puppeteer', while Chromium vendors a modified puppeteer-core.
    # Without a local alias TypeScript can pick up an unrelated ancestor module.
    # Re-export the very same classes used by DevTools, preserving private types.
    alias = replay / "node_modules/puppeteer"
    metadata = {"name": "puppeteer", "private": True, "types": "index.d.ts",
                "chromiumObserverTypeAlias": 1}
    relative = os.path.relpath(puppeteer.with_suffix(".js"), alias).replace(os.sep, "/")
    contents = {"package.json": json.dumps(metadata, indent=2) + "\n",
                "index.d.ts": f'export * from "{relative}";\n'}
    if alias.is_symlink():
        raise RuntimeError(f"Refusing to replace an existing dependency symlink: {alias}")
    if alias.exists():
        manifest = alias / "package.json"
        if not manifest.is_file() or json.loads(manifest.read_text()) != metadata:
            raise RuntimeError(f"Refusing to replace an existing dependency: {alias}")
    alias.mkdir(parents=True, exist_ok=True)
    for name, content in contents.items():
        destination = alias / name
        if not destination.is_file() or destination.read_text() != content:
            destination.write_text(content)
