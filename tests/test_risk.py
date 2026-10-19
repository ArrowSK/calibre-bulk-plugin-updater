import io
import importlib.util
import os
import stat
import unittest
import zipfile
from types import SimpleNamespace

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
spec = importlib.util.spec_from_file_location('risk', os.path.join(ROOT, 'risk.py'))
risk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(risk)


def sample_zip(name='Example Plugin', version=(1, 2, 0), other=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('__init__.py',
                    "from calibre.customize import InterfaceActionBase\n"
                    f"class Example(InterfaceActionBase):\n    name = {name!r}\n    version = {version!r}\n")
        for path, data in (other or {}).items():
            z.writestr(path, data)
    return b.getvalue()


def mock(**kwargs):
    props = dict(qname='Example Plugin', installed_version=(1, 1, 0),
                 available_version=(1, 2, 0), is_deprecated=False,
                 uninstall_plugins=[], calibre_required_version=(8, 0, 0),
                 zip_url='https://code.calibre-ebook.com/plugins/example.zip')
    props.update(kwargs)
    obj = SimpleNamespace(**props)
    obj.is_valid_platform = lambda: props.get('valid_platform', True)
    obj.is_valid_calibre = lambda: props.get('valid_calibre', True)
    obj.is_installed = lambda: props.get('installed', True)
    obj.is_upgrade_available = lambda: props.get('upgrade_available', True)
    return obj


class RiskTests(unittest.TestCase):
    def codes(self, issues):
        return {x['code'] for x in issues}

    def test_clean_staged_update(self):
        result = risk.inspect_archive(sample_zip(), 'Example Plugin', (1, 2, 0))
        self.assertEqual(result['members'], 1)
        self.assertEqual(len(result['sha256']), 64)
        self.assertFalse(risk.has_blockers(result['issues']))
        self.assertEqual(risk.severity(result['issues']), 'CHECKED')

    def test_name_mismatch_blocked(self):
        issues = risk.inspect_archive(sample_zip('Unexpected Plugin'), 'Example Plugin', (1, 2, 0))['issues']
        self.assertIn('NAME_MISMATCH', self.codes(issues))
        self.assertTrue(risk.has_blockers(issues))

    def test_version_mismatch_blocked(self):
        issues = risk.inspect_archive(sample_zip(version=(9, 9, 9)), 'Example Plugin', (1, 2, 0))['issues']
        self.assertIn('VERSION_MISMATCH', self.codes(issues))

    def test_traversal_blocked(self):
        issues = risk.inspect_archive(sample_zip(other={'../hack.py': b'danger'}))['issues']
        self.assertIn('UNSAFE_PATH', self.codes(issues))

    def test_member_count_limit(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w', zipfile.ZIP_STORED) as z:
            z.writestr('__init__.py', 'x=1')
            for i in range(risk.MAX_MEMBERS):
                z.writestr(f'{i}.txt', b'')
        self.assertIn('TOO_MANY_MEMBERS', self.codes(risk.inspect_archive(b.getvalue())['issues']))

    def test_case_collision_blocked(self):
        issues = risk.inspect_archive(sample_zip(other={'A.txt': b'a', 'a.TXT': b'b'}))['issues']
        self.assertIn('DUPLICATE_PATH', self.codes(issues))

    def test_missing_root_metadata_blocked(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('nested/__init__.py', 'hello')
        self.assertIn('NO_ROOT_INIT', self.codes(risk.inspect_archive(b.getvalue())['issues']))

    def test_symlink_blocked(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('__init__.py', 'x=2')
            info = zipfile.ZipInfo('sym')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, 'target')
        self.assertIn('SYMLINK', self.codes(risk.inspect_archive(b.getvalue())['issues']))

    def test_native_code_review(self):
        issues = risk.inspect_archive(sample_zip(other={'lib/libx.dylib': b'data'}))['issues']
        self.assertIn('NATIVE_CODE', self.codes(issues))
        self.assertEqual(risk.severity(issues), 'REVIEW')

    def test_bad_zip_blocked(self):
        self.assertIn('INVALID_ZIP', self.codes(risk.inspect_archive(b'not a zip')['issues']))

    def test_catalogue_blockers(self):
        issues = risk.metadata_issues(mock(valid_platform=False, valid_calibre=False, uninstall_plugins=['Older Name'],
                                           is_deprecated=True, zip_url='https://evil.invalid/f.zip'))
        for code in ('PLATFORM', 'CALIBRE_VERSION', 'UNINSTALL_DEPENDENCIES', 'DEPRECATED', 'UNEXPECTED_URL'):
            self.assertIn(code, self.codes(issues))
        self.assertTrue(risk.has_blockers(issues))

    def test_major_jump_review(self):
        issues = risk.metadata_issues(mock(available_version=(2, 0, 0)))
        self.assertIn('MAJOR_VERSION', self.codes(issues))
        self.assertFalse(risk.has_blockers(issues))

    def test_cleanup_policy_no_changes_leaves_no_backups(self):
        self.assertFalse(risk.should_retain_backups(0, 3, False, False))
        self.assertFalse(risk.should_retain_backups(0, 3, True, False))

    def test_cleanup_policy_success_default_and_opt_in(self):
        self.assertFalse(risk.should_retain_backups(3, 3, False, True))
        self.assertTrue(risk.should_retain_backups(3, 3, True, True))

    def test_cleanup_policy_partial_and_recovery_failure(self):
        self.assertTrue(risk.should_retain_backups(1, 3, False, True))
        self.assertTrue(risk.should_retain_backups(0, 3, False, True))

    def test_deprecated_not_upgrade_even_if_flag(self):
        issues = risk.metadata_issues(mock(is_deprecated=True, upgrade_available=True))
        self.assertIn('DEPRECATED', self.codes(issues))
        self.assertTrue(risk.has_blockers(issues))

    def test_unparseable_class_metadata_review(self):
        b = io.BytesIO()
        with zipfile.ZipFile(b, 'w') as z:
            z.writestr('__init__.py', 'class Hidden:\n    pass\n')
        issues = risk.inspect_archive(b.getvalue(), 'Name', (2, 0))['issues']
        self.assertIn('IDENTITY_NOT_STATIC', self.codes(issues))
        self.assertFalse(risk.has_blockers(issues))


if __name__ == '__main__':
    unittest.main()
