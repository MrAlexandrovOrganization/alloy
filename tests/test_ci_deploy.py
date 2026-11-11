import importlib.util
import json
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
        policy = {"gateway": {"alias": "gateway", "host": "192.0.2.1", "user": "transport", "key_alias": "gateway-key"},
                  "targets": [{"alias": "target", "host": "192.0.2.2", "port": 2222, "user": "deploy", "key_alias": "target-key"}]}
        return {"DEPLOY_SHA": "a" * 40, "DEPLOY_SSH_KEY": "synthetic-test-key", "DEPLOY_KNOWN_HOSTS": "synthetic-host-key", "DEPLOY_TRANSPORT_JSON": json.dumps(policy)}

    def test_uses_private_pinned_policy_and_cleans_up_key(self):
        paths = []
        def ssh(args, **kwargs):
            config = Path(args[2]); paths.append(config.parent)
            text = config.read_text()
            self.assertIn("StrictHostKeyChecking yes", text)
            self.assertIn("ForwardAgent no", text)
            self.assertIn("ProxyJump gateway", text)
            self.assertIn("HostName 192.0.2.2", text)
            self.assertIn("Port 2222", text)
            self.assertEqual(stat.S_IMODE((config.parent / "key").stat().st_mode), 0o600)
            self.assertEqual(args[-1], "deploy " + "a" * 40)
            self.assertEqual(args[-2], "target")
            self.assertTrue(kwargs['capture_output'])
            return type('Result', (), {'returncode': 0})()
        with patch.dict(os.environ, self.environment(), clear=True), patch.object(client.subprocess, "run", side_effect=ssh):
            client.main()
        self.assertTrue(paths)
        self.assertFalse(paths[0].exists())

    def test_missing_private_policy_and_invalid_sha_are_rejected_before_ssh(self):
        for env in ({**self.environment(), 'DEPLOY_TRANSPORT_JSON':'{}'}, {**self.environment(), 'DEPLOY_SHA':'main; command'}):
            with patch.dict(os.environ, env, clear=True), patch.object(client.subprocess, 'run') as ssh:
                with self.assertRaises(ValueError): client.main()
                ssh.assert_not_called()

    def test_ssh_policy_rejects_config_injection(self):
        env = self.environment(); config = json.loads(env['DEPLOY_TRANSPORT_JSON'])
        config['targets'][0]['host'] = '192.0.2.2\nProxyCommand malicious'
        with self.assertRaises(ValueError): client.configuration(json.dumps(config))

if __name__ == '__main__': unittest.main()
