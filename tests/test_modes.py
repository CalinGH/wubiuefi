# Tests for the installation-mode features: sha512_crypt password hashing, the
# real-partition pre-flight precondition checker, the autoinstall config
# generation and the per-mode tasklist routing.
#
# Unlike tests/test_backend.py these exercise only the OS-independent
# (backends/common) logic, so they run on a plain Python 3 interpreter without
# Wine or the Windows-specific backend. Run directly with:
#     python3 tests/test_modes.py
import os
import sys
import shutil
import tempfile
import types
import unittest

# Make the wubi package importable both from a dev checkout (src/) and from the
# staged build tree used by tests/run under Wine (build/wubi/lib).
sys.path.insert(0, os.path.join('build', 'wubi', 'lib'))
sys.path.insert(0, 'src')

from wubi.backends.common.utils import sha512_crypt
from wubi.backends.common import backend as backend_mod
from wubi.backends.common.backend import (
    Backend,
    check_real_partition_preconditions,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    REAL_PARTITION_HEADROOM_MB,
)


class Sha512CryptTests(unittest.TestCase):

    def test_known_spec_vector(self):
        # Ulrich Drepper's published $6$ test vector.
        expected = ('$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uB'
                    'nIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1')
        self.assertEqual(sha512_crypt('Hello world!', 'saltstring'), expected)

    def test_format_and_prefix(self):
        h = sha512_crypt('hunter2', 'abcdEFGH')
        self.assertTrue(h.startswith('$6$abcdEFGH$'))
        # $6$ + salt + $ + 86-char hash
        self.assertEqual(len(h.rsplit('$', 1)[1]), 86)

    def test_deterministic_with_same_salt(self):
        self.assertEqual(sha512_crypt('pw', 'samesalt'),
                         sha512_crypt('pw', 'samesalt'))

    def test_random_salt_differs(self):
        a = sha512_crypt('pw')
        b = sha512_crypt('pw')
        self.assertTrue(a.startswith('$6$'))
        self.assertNotEqual(a, b)

    def test_empty_password(self):
        h = sha512_crypt('', 'somesalt')
        self.assertTrue(h.startswith('$6$somesalt$'))


class PreconditionTests(unittest.TestCase):

    def codes(self, findings):
        return [code for _sev, code, _ctx in findings]

    def test_wubi_mode_never_blocks(self):
        findings = check_real_partition_preconditions(
            'wubi', resize_free_mb=0, required_free_mb=999999,
            fast_startup_enabled=True, bitlocker_drives={'C'},
            volume_dirty=True)
        self.assertEqual(findings, [])

    def test_sufficient_space_no_findings(self):
        findings = check_real_partition_preconditions(
            'autoinstall', resize_free_mb=50000, required_free_mb=12000,
            fast_startup_enabled=False)
        self.assertEqual(findings, [])

    def test_insufficient_space_is_error(self):
        findings = check_real_partition_preconditions(
            'autoinstall', resize_free_mb=1000, required_free_mb=12000,
            fast_startup_enabled=False)
        self.assertEqual(len(findings), 1)
        sev, code, ctx = findings[0]
        self.assertEqual(sev, SEVERITY_ERROR)
        self.assertEqual(code, 'insufficient_space')
        self.assertEqual(ctx['free_mb'], 1000)
        self.assertEqual(ctx['required_mb'], 12000)

    def test_insufficient_space_guided_is_warning(self):
        # Guided manual installs let the user pick another disk in the Ubuntu
        # installer, so a system-drive shortfall is only a warning.
        findings = check_real_partition_preconditions(
            'guided', resize_free_mb=1000, required_free_mb=12000,
            fast_startup_enabled=False)
        self.assertEqual(len(findings), 1)
        sev, code, ctx = findings[0]
        self.assertEqual(sev, SEVERITY_WARNING)
        self.assertEqual(code, 'insufficient_space')

    def test_fast_startup_is_warning(self):
        findings = check_real_partition_preconditions(
            'guided', resize_free_mb=50000, required_free_mb=12000,
            fast_startup_enabled=True)
        self.assertEqual(findings, [(SEVERITY_WARNING, 'fast_startup', {})])

    def test_dirty_volume_is_warning(self):
        findings = check_real_partition_preconditions(
            'guided', resize_free_mb=50000, required_free_mb=12000,
            fast_startup_enabled=False, volume_dirty=True)
        self.assertEqual(self.codes(findings), ['volume_dirty'])

    def test_bitlocker_warning_sorted(self):
        findings = check_real_partition_preconditions(
            'autoinstall', resize_free_mb=50000, required_free_mb=12000,
            fast_startup_enabled=False, bitlocker_drives={'D', 'C'})
        sev, code, ctx = findings[0]
        self.assertEqual(code, 'bitlocker')
        self.assertEqual(ctx['drives'], ['C', 'D'])

    def test_multiple_findings_order(self):
        findings = check_real_partition_preconditions(
            'autoinstall', resize_free_mb=10, required_free_mb=12000,
            fast_startup_enabled=True, bitlocker_drives={'C'},
            volume_dirty=True)
        # error first, then volume_dirty, fast_startup, bitlocker
        self.assertEqual(self.codes(findings),
                         ['insufficient_space', 'volume_dirty',
                          'fast_startup', 'bitlocker'])

    def test_non_numeric_inputs_safe(self):
        findings = check_real_partition_preconditions(
            'autoinstall', resize_free_mb=None, required_free_mb=None,
            fast_startup_enabled=False)
        self.assertEqual(findings, [])


def make_backend(tmp):
    '''Construct a Backend whose platform-specific methods are stubbed out.'''
    class StubBackend(Backend):
        def __getattr__(self, name):
            def _stub(*a, **k):
                return None
            _stub.__name__ = name
            return _stub

    app = types.SimpleNamespace()
    info = types.SimpleNamespace()
    info.root_dir = tmp
    info.locale = None
    info.application_name = 'wubi'
    app.info = info
    return StubBackend(app)


class AutoinstallConfigTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.back = make_backend(self.tmp)
        info = self.back.info
        info.install_dir = os.path.join(self.tmp, 'install')
        os.makedirs(info.install_dir)
        info.password = 'sup3rs3cret'
        info.username = 'testuser'
        info.keyboard_layout = 'gb'
        info.keyboard_variant = ''
        info.locale = 'en_GB.UTF-8'

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_user_and_meta_data(self):
        self.back.create_autoinstall_config()
        adir = os.path.join(self.tmp, 'install', 'autoinstall')
        user_data = open(os.path.join(adir, 'user-data')).read()
        meta_data = open(os.path.join(adir, 'meta-data')).read()
        self.assertEqual(meta_data, '')
        self.assertIn('autoinstall:', user_data)
        self.assertIn('name: alongside', user_data)
        self.assertIn('username: testuser', user_data)
        self.assertIn('hostname: testuser-desktop', user_data)
        self.assertIn('layout: gb', user_data)
        # encoding suffix stripped from locale
        self.assertIn('locale: en_GB', user_data)
        self.assertNotIn('UTF-8', user_data)
        # password hashed, not stored in clear
        self.assertIn("$6$", user_data)
        self.assertNotIn('sup3rs3cret', user_data)
        self.assertEqual(self.back.info.autoinstall_dir, adir)


class TasklistRoutingTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.back = make_backend(self.tmp)
        prov = types.SimpleNamespace(install_method='diskimage-script')
        self.back.info.distro = types.SimpleNamespace(
            provider=prov, diskimage=None, name='Ubuntu', min_disk_space_mb=7000)
        self.back.info.version = '24.04'
        self.back.info.target_drive = types.SimpleNamespace(
            is_fat=lambda: False, free_space_mb=50000)
        self.back.info.cd_path = None
        self.back.info.cd_distro = None
        self.back.info.iso_path = None
        self.back.info.iso_distro = None
        # cache_cd_path() walks the filesystem looking for CDs/ISOs; not relevant
        # to tasklist routing, so neutralize it.
        self.back.cache_cd_path = lambda: None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self, tasklist):
        return [t.name for t in tasklist.subtasks]

    def test_autoinstall_tasklist(self):
        names = self.names(self.back.get_autoinstall_tasklist())
        self.assertIn('create_autoinstall_config', names)
        self.assertIn('modify_grub_configuration', names)
        # real-partition mode: no loop-file-only steps
        self.assertNotIn('create_virtual_disks', names)
        self.assertNotIn('create_preseed', names)
        self.assertNotIn('choose_disk_sizes', names)

    def test_dualboot_tasklist(self):
        names = self.names(self.back.get_dualboot_tasklist())
        self.assertNotIn('create_autoinstall_config', names)
        self.assertNotIn('create_virtual_disks', names)
        self.assertNotIn('create_preseed', names)
        self.assertNotIn('choose_disk_sizes', names)

    def test_get_real_partition_findings_uses_info(self):
        info = self.back.info
        info.install_mode = 'autoinstall'
        info.system_drive = types.SimpleNamespace(free_space_mb=1000, path='C:')
        info.fast_startup_enabled = True
        info.volume_dirty = False
        info.bitlocker_drives = {'C'}  # must NOT be re-emitted as a finding
        findings = self.back.get_real_partition_findings()
        codes = [c for _s, c, _ctx in findings]
        self.assertIn('insufficient_space', codes)
        self.assertIn('fast_startup', codes)
        self.assertNotIn('bitlocker', codes)

    def test_get_real_partition_findings_wubi_empty(self):
        self.back.info.install_mode = 'wubi'
        self.assertEqual(self.back.get_real_partition_findings(), [])


if __name__ == '__main__':
    unittest.main()
