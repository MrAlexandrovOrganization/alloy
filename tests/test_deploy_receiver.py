import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("receiver", Path(__file__).parents[1] / "scripts/deploy-receiver.py")
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)
SHA = "a" * 40
OLD = "b" * 40


class DeployReceiverTests(unittest.TestCase):
    def test_rejects_shell_commands_and_non_sha_refs(self):
        for value in ("", "deploy main", "deploy " + SHA + "; id", "deploy " + SHA + " extra", "sh " + SHA):
            with self.subTest(value=value), self.assertRaises(ValueError):
                receiver.request(value)

    def exercise(self, *, dirty=False, stale=False, allowed=True, sudo=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        directory = root / "alloy"
        directory.mkdir()
        calls = []

        def run(args, cwd, capture=False):
            calls.append(args)
            if args[:3] == ["git", "rev-parse", "--show-toplevel"]:
                return str(directory.resolve())
            if args[:2] == ["git", "branch"]:
                return "main"
            if args[:2] == ["git", "status"]:
                return " M config.alloy" if dirty else ""
            if args == ["git", "rev-parse", "FETCH_HEAD"]:
                return OLD if stale else SHA
            if args == ["git", "rev-parse", "HEAD"]:
                return SHA if any(c[:2] == ["git", "merge"] for c in calls) else OLD
            return ""

        config = {"host": "fixture", "projects": ["alloy"] if allowed else [], "sudo_make": sudo}
        with patch.object(receiver, "run", side_effect=run):
            try:
                receiver.deploy("alloy", SHA, config, root, root)
            except ValueError:
                return calls, None
        return calls, json.loads((root / "alloy.json").read_text())

    def test_dirty_checkout_is_not_fetched_or_activated(self):
        calls, receipt = self.exercise(dirty=True)
        self.assertFalse(any(c[:2] == ["git", "fetch"] or "make" in c for c in calls))
        self.assertIsNone(receipt)

    def test_stale_workflow_is_not_merged_or_activated(self):
        calls, receipt = self.exercise(stale=True)
        self.assertFalse(any(c[:2] == ["git", "merge"] or "make" in c for c in calls))
        self.assertIsNone(receipt)

    def test_wrong_host_has_no_side_effects(self):
        calls, receipt = self.exercise(allowed=False)
        self.assertEqual(calls, [])
        self.assertIsNone(receipt)

    def test_verified_deployment_records_exact_revision(self):
        calls, receipt = self.exercise(sudo=True)
        self.assertIn(["sudo", "-n", "make", "deploy"], calls)
        self.assertEqual(receipt["sha"], SHA)
        self.assertEqual(receipt["previous_sha"], OLD)


if __name__ == "__main__":
    unittest.main()
