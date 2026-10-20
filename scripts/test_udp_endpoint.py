"""UDP-only endpoint validation before downloads or compilation."""
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch


class EndpointTest(unittest.TestCase):
    def test_rejects_http_hostnames_paths_missing_and_invalid_ports(self):
        script = Path(__file__).with_name("build.py")
        for endpoint in ("http://127.0.0.1:8765/api/frame", "udp://example.com:8766",
                         "udp://127.0.0.1", "udp://127.0.0.1:0", "udp://127.0.0.1:65536",
                         "udp://127.0.0.1:8766/path", "udp://[bad", "udp://user@127.0.0.1:8766",
                         "udp://127.0.0.1:8766?q=1"):
            with self.subTest(endpoint=endpoint), patch.object(sys, "argv", [str(script), "--endpoint", endpoint]), \
                    patch("devtools_types.prepare_devtools_types") as prepare:
                with self.assertRaises(SystemExit):
                    runpy.run_path(str(script), run_name="__main__")
                prepare.assert_not_called()


if __name__ == "__main__":
    unittest.main()
