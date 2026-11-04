import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("network_metrics", Path(__file__).resolve().parents[1] / "scripts/network_metrics.py")
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


class NetworkTargetTests(unittest.TestCase):
    def test_public_wildcard_and_invalid_endpoints_are_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for address in ("0.0.0.0:19100", "193.84.2.141:19100", "100.64.0.5:0", "100.64.0.5:65536", "localhost:9100"):
                with self.assertRaises(ValueError):
                    network.register(directory, address)
                self.assertEqual(list(directory.iterdir()), [])

    def test_registration_is_readable_and_does_not_remove_other_services(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            other = directory / "application.json"
            other.write_text("unchanged")
            network.register(directory, "100.64.0.5:19100")
            target = directory / "host-network.json"
            self.assertEqual(json.loads(target.read_text())[0]["labels"]["scope"], "host")
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            network.register(directory, "")
            self.assertFalse(target.exists())
            self.assertEqual(other.read_text(), "unchanged")


if __name__ == "__main__":
    unittest.main()
