"""Local safety regressions. No Docker, network, SSH, or real DB access."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('api_local', ROOT / 'scripts/api_local.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def config():
    return {'name': 'correspondencia-deploy', 'services': {'backend': {'expose': ['8000'], 'environment': {'SECRET': 'synthetic_secret'}}, 'postgres': {}, 'web': {}}, 'volumes': {}}


def identity():
    return {**{s: {'id': s + '-id', 'started': 'unchanged', 'image': s + '-image', 'mounts': [['volume', s + '-volume', '/data']]} for s in ('backend', 'postgres', 'web')}, 'database': {'POSTGRES_DB': 'qa', 'POSTGRES_USER': 'qa_user'}}


class ApiLocalTests(unittest.TestCase):
    def test_only_loopback_port_is_allowed(self):
        before = config()
        after = copy.deepcopy(before)
        after['services']['backend']['ports'] = [dict(m.PORT)]
        m.check_change(before, after)
        self.assertNotIn('ports', before['services']['backend'])

    def test_all_interfaces_and_extra_ports_rejected(self):
        for ports in ([{**m.PORT, 'host_ip': '0.0.0.0'}], [dict(m.PORT), dict(m.PORT)]):
            after = config()
            after['services']['backend']['ports'] = ports
            with self.assertRaises(RuntimeError):
                m.check_change(config(), after)

    def test_changes_to_database_web_network_env_or_volume_rejected(self):
        for resource in ('postgres', 'web', 'backend'):
            after = config()
            after['services']['backend']['ports'] = [dict(m.PORT)]
            after['services'][resource]['environment'] = {'changed': 'yes'}
            with self.assertRaises(RuntimeError):
                m.check_change(config(), after)
        after = config()
        after['services']['backend']['ports'] = [dict(m.PORT)]
        after['volumes']['other'] = {'name': 'new-volume'}
        with self.assertRaises(RuntimeError):
            m.check_change(config(), after)

    def test_original_backend_port_rejected(self):
        before = config()
        before['services']['backend']['ports'] = [dict(m.PORT)]
        with self.assertRaises(RuntimeError):
            m.check_change(before, before)

    def test_postgres_and_web_identity_must_remain_identical(self):
        for service in ('postgres', 'web'):
            for field in ('id', 'started', 'image', 'mounts'):
                before = identity()
                after = copy.deepcopy(before)
                after[service][field] = 'changed'
                with self.assertRaises(RuntimeError):
                    m.check_identity(before, after)

    def test_backend_id_can_change_but_image_storage_cannot(self):
        before, after = identity(), identity()
        after['backend']['id'] = 'new-backend'
        m.check_identity(before, after)
        for key in ('image', 'mounts'):
            after = identity()
            after['backend'][key] = 'changed'
            with self.assertRaises(RuntimeError):
                m.check_identity(before, after)

    def test_catalog_requires_real_correspondence_tables(self):
        m.check_catalog('\n'.join('1; 0 0 TABLE public ' + t + ' owner' for t in ('users', 'employees', 'correspondences')))
        with self.assertRaises(RuntimeError):
            m.check_catalog('readable but unrelated dump')

    def test_backup_writes_checkpoint_only_after_catalog_validation(self):
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as temp:
                qa = m.QA.__new__(m.QA)
                qa.root, qa.config = Path(temp), config()
                (qa.root / '.env').write_text('SECRET=synthetic_secret')
                (qa.root / 'docker-compose.yml').write_text('original compose')
                qa.snapshot = Mock(return_value=identity())
                qa.counts = Mock(return_value='users|3\n')
                qa.run = Mock(return_value='synthetic metadata')
                qa.catalog = Mock(side_effect=None if valid else RuntimeError('bad catalog'))
                def dc(*args, **kwargs):
                    if kwargs.get('output'):
                        kwargs['output'].write(b'synthetic pg_dump')
                    return ''
                qa.dc = Mock(side_effect=dc)
                out = io.StringIO()
                with patch.object(m.shutil, 'disk_usage', return_value=Mock(free=10**12)), contextlib.redirect_stdout(out):
                    if valid:
                        qa.audit_backup()
                    else:
                        with self.assertRaises(RuntimeError):
                            qa.audit_backup()
                checkpoints = list(qa.root.rglob('checkpoint.json'))
                self.assertEqual(len(checkpoints), int(valid))
                self.assertNotIn('synthetic_secret', out.getvalue())
                self.assertFalse(any('up' in call.args or 'stop' in call.args for call in qa.dc.call_args_list))
                if valid:
                    data = json.loads(checkpoints[0].read_text())
                    self.assertEqual(data['sha256'], m.digest(checkpoints[0].parent / 'database.dump'))

    def test_recreate_is_backend_only_without_build_or_pull(self):
        qa = m.QA.__new__(m.QA)
        qa.config = config()
        qa.run = Mock(return_value='backend-image\n')
        qa.dc = Mock()
        qa.snapshot = Mock(return_value=identity())
        qa.recreate(identity())
        qa.dc.assert_called_once_with('up', '-d', '--no-deps', '--force-recreate', '--no-build', '--pull', 'never', 'backend')

    def test_changed_image_tag_aborts_before_recreation(self):
        qa = m.QA.__new__(m.QA)
        qa.config = config()
        qa.run, qa.dc = Mock(return_value='different-image'), Mock()
        with self.assertRaises(RuntimeError):
            qa.recreate(identity())
        qa.dc.assert_not_called()

    def test_rollback_allows_failed_backend_and_preserves_env(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / 'backups' / 'pre-api-local-test'
            (folder / 'infrastructure').mkdir(parents=True)
            (folder / 'infrastructure' / 'docker-compose.yml').write_text('original')
            (folder / 'compose.private.json').write_text(json.dumps(config()))
            (root / 'docker-compose.yml').write_text('new')
            (root / '.env').write_text('untouched')
            qa = m.QA.__new__(m.QA)
            qa.root, qa.config = root, config()
            qa.config['services']['backend']['ports'] = [dict(m.PORT)]
            qa.checkpoint = Mock(return_value=(folder, {'before': identity()}))
            qa.snapshot = Mock(return_value=identity())
            qa.counts, qa.recreate = Mock(), Mock()
            qa.dc = Mock(return_value=json.dumps(config()))
            with contextlib.redirect_stdout(io.StringIO()):
                qa.apply(folder, rollback=True)
            self.assertEqual(qa.snapshot.call_args_list[0].kwargs, {'allow_unhealthy_backend': True})
            qa.counts.assert_not_called()
            qa.recreate.assert_called_once_with(identity())
            self.assertEqual((root / '.env').read_text(), 'untouched')
            self.assertEqual((root / 'docker-compose.yml').read_text(), 'original')

    def test_runtime_backend_drift_aborts_snapshot(self):
        for drift in ('env', 'command', 'network'):
            qa = m.QA.__new__(m.QA)
            qa.config = config()
            qa.config['services']['backend'].update(command=['uvicorn'], networks={'edge': {}})
            qa.config['networks'] = {'edge': {'name': 'qa-edge'}}
            def row(service, **kw):
                data = {'Id': service, 'Image': service + '-image', 'Mounts': [],
                        'State': {'StartedAt': 'now'},
                        'Config': {'Env': ['POSTGRES_DB=qa', 'POSTGRES_USER=qa_user', 'SECRET=synthetic_secret'], 'Cmd': ['uvicorn']},
                        'NetworkSettings': {'Networks': {'qa-edge': {}}}}
                if service == 'backend':
                    if drift == 'env':
                        data['Config']['Env'] = []
                    elif drift == 'command':
                        data['Config']['Cmd'] = ['changed']
                    else:
                        data['NetworkSettings']['Networks'] = {'different': {}}
                return data
            qa.inspect = Mock(side_effect=row)
            with self.subTest(drift=drift), self.assertRaises(RuntimeError):
                qa.snapshot()

    def test_dump_tampering_blocks_apply_before_any_docker_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            qa = m.QA.__new__(m.QA)
            qa.root = Path(temp)
            folder = qa.root / 'backups' / 'pre-api-local-test'
            folder.mkdir(parents=True)
            (folder / 'database.dump').write_bytes(b'changed')
            (folder / 'checkpoint.json').write_text(json.dumps({'sha256': 'wrong'}))
            qa.catalog = Mock()
            qa.dc = Mock()
            # Windows mode bits do not implement POSIX privacy; bypass only that check.
            real_stat = Path.stat
            def stat(path, *a, **kw):
                value = real_stat(path, *a, **kw)
                if path == folder:
                    from types import SimpleNamespace
                    return SimpleNamespace(st_mode=0o40700)
                return value
            with patch.object(Path, 'stat', stat), self.assertRaisesRegex(RuntimeError, 'Hash'):
                qa.checkpoint(folder)
            qa.catalog.assert_not_called()
            qa.dc.assert_not_called()

    def test_repo_compose_has_exact_backend_loopback_mapping(self):
        text = (ROOT / 'docker-compose.yml').read_text()
        self.assertEqual(text.count("ports: ['127.0.0.1:8100:8000']"), 1)
        self.assertNotIn('0.0.0.0:8100', text)


if __name__ == '__main__':
    unittest.main()
