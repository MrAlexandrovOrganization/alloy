"""Receiver probes must work without a VPN route on the administrative host."""

import importlib.util
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "receiver_http", Path(__file__).parents[1] / "scripts/receiver_http.py"
)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


class ReceiverHTTP(unittest.TestCase):
    def test_container_probe_never_uses_host_network(self):
        with patch.dict(os.environ, {"QUERY_CONTAINER": "fixture-vpn", "DOCKER": "docker"}), \
                patch.object(receiver.urllib.request, "urlopen") as host, \
                patch.object(receiver.subprocess, "check_output", return_value=b"ready") as docker:
            self.assertEqual(receiver.read_url("http://receiver/ready", timeout=5), b"ready")
            host.assert_not_called()
            self.assertEqual(docker.call_args.args[0], [
                "docker", "exec", "fixture-vpn", "wget", "-q", "-T", "5", "-O", "-", "http://receiver/ready",
            ])

    def test_failed_container_probe_is_not_reported_as_success(self):
        with patch.dict(os.environ, {"QUERY_CONTAINER": "fixture-vpn"}), \
                patch.object(receiver.subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "wget")):
            with self.assertRaises(OSError):
                receiver.read_url("http://receiver/ready")

    def test_direct_probes_remain_available_on_other_hosts(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(receiver.urllib.request, "urlopen") as host, \
                patch.object(receiver.subprocess, "check_output") as docker:
            host.return_value.__enter__.return_value.read.return_value = b"ready"
            self.assertEqual(receiver.read_url("http://receiver/ready"), b"ready")
            docker.assert_not_called()
