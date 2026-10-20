"""Validate build arguments and GN serialization without compiling Chromium."""
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("build.py")


class BuildScaleTest(unittest.TestCase):
    def test_invalid_values_stop_before_checkout_changes(self):
        for value in ("0", "-0.5", "1.01", "nan", "inf", "bad"):
            with self.subTest(value=value), patch.object(sys, "argv", [str(SCRIPT), "--scale", value]), \
                    patch("devtools_types.prepare_devtools_types") as prepare:
                with self.assertRaises(SystemExit) as error:
                    runpy.run_path(str(SCRIPT), run_name="__main__")
                self.assertEqual(error.exception.code, 2)
                prepare.assert_not_called()

    def test_scale_reaches_gn_and_generate_only_never_starts_compiler(self):
        for value in ("1.0", "0.75", "0.5", "0.125"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                script_path = root / "scripts/build.py"
                source = root / "chromium/src/chrome/browser/observer/page_frame_streamer.cc"
                source.parent.mkdir(parents=True)
                source.touch()
                argv = [str(SCRIPT), "--scale", value, "--generate-only"]
                with patch.object(sys, "argv", argv), \
                        patch.object(Path, "resolve", return_value=script_path), \
                        patch("devtools_types.prepare_devtools_types"), \
                        patch("shutil.disk_usage") as disk, \
                        patch("subprocess.run") as run, patch("subprocess.Popen") as popen:
                    disk.return_value.free = 100 * 1024**3
                    with self.assertRaises(SystemExit) as error:
                        runpy.run_path(str(SCRIPT), run_name="__main__")
                    self.assertEqual(error.exception.code, 0)
                    config = (root / "chromium/src/out/Observer/args.gn").read_text()
                    self.assertIn('page_frame_stream_scale = "' + value + '"', config)
                    self.assertEqual(run.call_args.args[0][0], "gn")
                    popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
