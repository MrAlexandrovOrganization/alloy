import importlib.util
import os
from pathlib import Path
import stat
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("client", Path(__file__).parents[1] / "scripts/ci-deploy.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class SSHTransportTests(unittest.TestCase):
    def environment(self):
        return {"DEPLOY_TARGET": "wsl", "DEPLOY_SHA": "a" * 40,
                "DEPLOY_SSH_KEY": "synthetic-test-key", "DEPLOY_KNOWN_HOSTS": "synthetic-host-key"}

    def test_pins_hosts_disables_agent_and_cleans_up_key(self):
        paths = []

        def ssh(args, **kwargs):
            config = Path(args[2])
            paths.append(config.parent)
            text = config.read_text()
            self.assertIn("StrictHostKeyChecking yes", text)
            self.assertIn("ForwardAgent no", text)
            self.assertIn("ProxyJump obs-gateway", text)
            self.assertIn("HostName 100.64.0.11", text)
            self.assertIn("HostKeyAlias obs-asus-server", text)
            self.assertEqual(stat.S_IMODE((config.parent / "key").stat().st_mode), 0o600)
            self.assertEqual(args[-1], "deploy " + "a" * 40)
            self.assertEqual(args[-2], "obs-asus-server")

        env = self.environment()
        env["DEPLOY_TARGET"] = "asus-server"
        with patch.dict(os.environ, env, clear=True), patch.object(client.subprocess, "run", side_effect=ssh):
            client.main()
        self.assertTrue(paths)
        self.assertFalse(paths[0].exists())

    def test_central_project_cannot_target_old_server(self):
        env = self.environment()
        for target in ("server", "asus-server"):
            env["DEPLOY_TARGET"] = target
            with self.subTest(target=target), patch.object(client, "__file__", "/fixture/grafana/scripts/ci-deploy.py"), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(SystemExit):
                    client.main()

    def test_non_sha_is_rejected_before_ssh(self):
        env = self.environment()
        env["DEPLOY_SHA"] = "main; command"
        with patch.dict(os.environ, env, clear=True), patch.object(client.subprocess, "run") as ssh:
            with self.assertRaises(SystemExit):
                client.main()
            ssh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
