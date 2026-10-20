"""Hide ancestor Node packages from Linux build processes without modifying them."""
from pathlib import Path
import shutil
import sys


def isolate_build_command(command: list, checkout: Path) -> list:
    if sys.platform != "linux":
        return command
    modules = [parent / "node_modules" for parent in checkout.resolve().parents
               if (parent / "node_modules").is_dir()]
    if not modules:
        return command
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise RuntimeError("Ancestor node_modules can leak into Chromium's type checks. "
                           "Install bubblewrap (sudo apt install bubblewrap), then rerun.")
    print("Hiding ancestor Node packages from this build: " + ", ".join(map(str, modules)), flush=True)
    # Preserve device access: Ninja redirects child stdin from /dev/null.
    prefix = [bwrap, "--bind", "/", "/", "--dev-bind", "/dev", "/dev"]
    for directory in modules:
        prefix.extend(["--tmpfs", str(directory)])
    return [*prefix, "--", *command]
