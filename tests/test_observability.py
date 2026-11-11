"""Check package trust boundaries, generated coverage and safe live-asset migration."""
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import observability as assembly
import observability_package as contract


class Packages(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        (self.source / 'monitoring').mkdir(parents=True)
        subprocess.run(['git', 'init', '-q', str(self.source)], check=True)
        subprocess.run(['git', '-C', str(self.source), 'remote', 'add', 'origin', 'https://example.invalid/owner.git'], check=True)
        subprocess.run(['git', '-C', str(self.source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        self.collector = self.root / 'collector'
        (self.collector / 'observability').mkdir(parents=True)
        (self.collector / 'observability/base.alloy').write_text('loki.process "docker" {\n\t// @service-stages:parse@\n\t// @service-stages:normalize@\n}\n')
        self.doc = {'schema': 1, 'service': 'fixture', 'components': ['fixture/*'], 'logs': [], 'assets': [{'kind': 'metrics', 'file': 'monitoring/targets.json', 'destination': 'fixture.json', 'location': 'local'}]}
        (self.source / 'monitoring/targets.json').write_text('[{"targets":["fixture:9090"]}]\n')
        self.write_manifest()
        private = self.collector / '.observability/inputs'
        private.mkdir(parents=True)
        (private / 'deployment.json').write_bytes(contract.encoded({'schema': 1, 'hosts': ['node-a'], 'central': ['node-a'], 'services': {'fixture': {'local': ['node-a']}, 'other': {'local': ['node-a']}}, 'inventory': {'node-a': ['fixture/app']}}))

    def write_manifest(self):
        (self.source / contract.MANIFEST).write_bytes(contract.encoded(self.doc))

    def pin(self):
        assembly.sync(self.collector, self.source)

    def test_archive_is_deterministic_and_self_contained(self):
        a, b = self.root / 'a.tar', self.root / 'b.tar'
        pin = contract.export(self.source, a)
        contract.export(self.source, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(pin['source_state'], 'worktree')
        assembly.sync(self.collector, archive=a)
        # No source checkout is used while rendering pinned packages.
        (self.source / 'monitoring/targets.json').unlink()
        first = assembly.render(self.collector)
        self.assertIn('hosts/node-a/prometheus-targets/fixture.json', first)
        self.assertEqual(first, assembly.render(self.collector))

    def test_ignored_input_is_not_reported_as_committed(self):
        (self.source / '.gitignore').write_text('monitoring/targets.json\n')
        subprocess.run(['git', '-C', str(self.source), 'add', '.gitignore', contract.MANIFEST], check=True)
        subprocess.run(['git', '-C', str(self.source), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'manifest without ignored target'], check=True)
        status = contract.git(self.source, 'status', '--porcelain')
        self.assertFalse(status)
        self.assertEqual(contract.package(self.source)[0]['source_state'], 'worktree')

    def test_archive_traversal_rejected(self):
        path = self.root / 'unsafe.tar'
        with tarfile.open(path, 'w') as tar:
            info = tarfile.TarInfo('../escape.json')
            info.size = 2
            tar.addfile(info, io.BytesIO(b'{}'))
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            assembly.sync(self.collector, archive=path)
        self.assertFalse((self.root / 'escape.json').exists())

    def test_pinned_tampering_is_rejected(self):
        self.pin()
        path = next((self.collector / '.observability/inputs/packages').rglob('targets.json'))
        path.write_text('[]')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            assembly.render(self.collector)

    def test_host_retirement_and_unsafe_paths_rejected(self):
        for host in ['retired-node', 'unknown-node']:
            self.doc['assets'][0]['hosts'] = [host]
            self.write_manifest()
            with self.assertRaises(ValueError):
                contract.validate(self.source)
            with self.assertRaisesRegex(ValueError, 'inactive'):
                assembly.install(self.collector, None, self.root / 'live', host)
        self.doc['assets'][0].pop('hosts', None)
        self.doc['assets'][0]['destination'] = '../private.env'
        self.write_manifest()
        with self.assertRaises(ValueError):
            contract.validate(self.source)

    def test_local_edits_stop_entire_install(self):
        self.pin()
        live = self.root / 'live'
        path = live / 'prometheus-targets/fixture.json'
        path.parent.mkdir(parents=True)
        path.write_text('private local override')
        with self.assertRaisesRegex(ValueError, 'locally edited'):
            assembly.install(self.collector, None, live, 'node-a')
        self.assertEqual(path.read_text(), 'private local override')
        self.assertFalse((live / 'observability-managed.json').exists())

    def test_adoption_and_retirement_preserve_unmanaged_files(self):
        old = b'legacy YAML\n'
        old_retired = b'old remote scrape\n'
        self.doc['assets'][0]['adopt_sha256'] = [contract.digest(old)]
        self.doc['retire'] = [{'kind': 'metrics', 'destination': 'stale.yml', 'location': 'local', 'sha256': [contract.digest(old_retired)]}]
        self.write_manifest()
        self.pin()
        live = self.root / 'live'
        sd = live / 'prometheus-targets'
        sd.mkdir(parents=True)
        (sd / 'fixture.json').write_bytes(old)
        (sd / 'stale.yml').write_bytes(old_retired)
        (sd / 'private.json').write_text('keep me')
        mask = os.umask(0o077)
        try:
            assembly.install(self.collector, None, live, 'node-a')
        finally:
            os.umask(mask)
        self.assertFalse((sd / 'stale.yml').exists())
        self.assertEqual((sd / 'private.json').read_text(), 'keep me')
        self.assertEqual(stat.S_IMODE(sd.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((sd / 'fixture.json').stat().st_mode), 0o644)
        self.assertEqual(json.loads((live / 'observability-managed.json').read_text())['host'], 'node-a')
        assembly.install(self.collector, None, live, 'node-a')

    def test_write_failure_rolls_back_previous_assets(self):
        self.pin()
        live = self.root / 'live'
        assembly.install(self.collector, None, live, 'node-a')
        target = live / 'prometheus-targets/fixture.json'
        before, ledger = target.read_bytes(), (live / 'observability-managed.json').read_bytes()
        (self.source / 'monitoring/targets.json').write_text('[{"targets":["replacement:9090"]}]')
        self.pin()
        real = assembly.atomic
        def failing(path, data):
            if path.name == 'observability-managed.json' and data != ledger:
                raise OSError('synthetic disk failure')
            real(path, data)
        with patch.object(assembly, 'atomic', side_effect=failing), self.assertRaises(OSError):
            assembly.install(self.collector, None, live, 'node-a')
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual((live / 'observability-managed.json').read_bytes(), ledger)

    def test_destination_conflicts_rejected_across_owners(self):
        self.pin()
        self.doc['service'] = 'other'
        self.doc['components'] = ['other/*']
        self.write_manifest()
        self.pin()
        with self.assertRaisesRegex(ValueError, 'output conflict'):
            assembly.render(self.collector)

    def test_inventory_coverage_and_public_assembly_are_separate(self):
        self.pin()
        files = assembly.render(self.collector)
        self.assertEqual(json.loads(files['assets.json'])['components'], {'fixture/*':'fixture'})
        public = assembly.render(ROOT, public=True)
        self.assertNotIn(b'Source:', public['config.alloy'])
        assembly.generate(ROOT, self.root / 'output', check=True, public=True)
        plan = self.collector / '.observability/inputs/deployment.json'
        doc = json.loads(plan.read_text());doc['inventory']['node-a'].append('unowned/app')
        plan.write_bytes(contract.encoded(doc))
        with self.assertRaisesRegex(ValueError, 'owner'):
            assembly.render(self.collector)

    def test_private_bundle_transfer_rejects_worktree_and_traversal(self):
        self.pin()
        archive = self.root / 'inputs.tar.gz'
        assembly.bundle(self.collector, archive)
        with self.assertRaisesRegex(ValueError, 'committed'):
            assembly.receive(self.collector, archive.read_bytes())
        subprocess.run(['git','-C',str(self.source),'add','.'], check=True)
        subprocess.run(['git','-C',str(self.source),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','resources'], check=True)
        self.pin()
        assembly.bundle(self.collector, archive)
        assembly.receive(self.collector, archive.read_bytes())
        self.assertTrue((self.collector / '.observability/input.sha256').exists())
        self.assertEqual(assembly.render(self.collector)['hosts/node-a/prometheus-targets/fixture.json'], b'[{"targets":["fixture:9090"]}]\n')


if __name__ == '__main__':
    unittest.main()
