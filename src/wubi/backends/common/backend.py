# Copyright (c) 2008 Agostino Russo
#
# Written by Agostino Russo <agostino.russo@gmail.com>
#
# This file is part of Wubi the Win32 Ubuntu Installer.
#
# Wubi is free software; you can redistribute it and/or modify
# it under 5the terms of the GNU Lesser General Public License as
# published by the Free Software Foundation; either version 2.1 of
# the License, or (at your option) any later version.
#
# Wubi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

import sys
import os
import tempfile
import locale
import struct
import logging
import time
import gettext
import glob
import re
import shutil
import configparser as ConfigParser
from . import btdownloader
from . import downloader
import subprocess

from .metalink import parse_metalink
from .tasklist import ThreadedTaskList, Task
from .distro import Distro
from .mappings import lang_country2linux_locale
from .utils import join_path, run_nonblocking_command, md5_password, sha512_crypt, copy_file, read_file, write_file, get_file_hash, reversed, find_line_in_file, unix_path, rm_tree, spawn_command
from .signature import verify_gpg_signature
from wubi import errors
from os.path import abspath

log = logging.getLogger("CommonBackend")

# Severities returned by check_real_partition_preconditions().
SEVERITY_ERROR = 'error'      # blocks the install outright
SEVERITY_WARNING = 'warning'  # needs explicit user confirmation

# Extra free space (MB) to require on the partition that will be shrunk, on top
# of the distro's stated minimum, so the resized Windows volume keeps headroom.
REAL_PARTITION_HEADROOM_MB = 5120


def check_real_partition_preconditions(install_mode, resize_free_mb,
                                       required_free_mb, fast_startup_enabled,
                                       bitlocker_drives=None, volume_dirty=False):
    '''
    Evaluate the safety preconditions for the real-partition install modes
    ('autoinstall' and 'guided').

    This is a pure function over primitive inputs (no Windows calls) so it can be
    unit tested. It returns an ordered list of ``(severity, code, context)``
    findings; the frontend renders them into localized messages. ``error``
    findings should block the install, ``warning`` findings should be confirmed.

    For modes other than the real-partition ones an empty list is returned, so it
    is always safe to call.
    '''
    findings = []
    if install_mode not in ('autoinstall', 'guided'):
        return findings
    try:
        resize_free_mb = float(resize_free_mb)
    except (TypeError, ValueError):
        resize_free_mb = 0.0
    try:
        required_free_mb = float(required_free_mb)
    except (TypeError, ValueError):
        required_free_mb = 0.0
    if resize_free_mb < required_free_mb:
        # Automatic ("alongside") installs let subiquity shrink the Windows
        # system partition itself, so a shortfall there is fatal. The guided
        # manual install lets the user pick any disk in the Ubuntu installer, so
        # it is only a warning they can override.
        severity = SEVERITY_ERROR if install_mode == 'autoinstall' else SEVERITY_WARNING
        findings.append((severity, 'insufficient_space', {
            'free_mb': int(resize_free_mb),
            'required_mb': int(required_free_mb),
        }))
    if volume_dirty:
        findings.append((SEVERITY_WARNING, 'volume_dirty', {}))
    if fast_startup_enabled:
        findings.append((SEVERITY_WARNING, 'fast_startup', {}))
    if bitlocker_drives:
        findings.append((SEVERITY_WARNING, 'bitlocker', {
            'drives': sorted(bitlocker_drives),
        }))
    return findings


class Backend(object):
    '''
    Implements non-platform-specific functionality
    Subclasses need to implement platform-specific getters
    '''
    def __init__(self, application):
        self.application = application
        self.info = application.info
        #~ if hasattr(sys,'frozen') and sys.frozen:
            #~ root_dir = dirname(abspath(sys.executable))
        #~ else:
            #~ root_dir = ''
        #~ self.info.root_dir = abspath(root_dir)
        self.info.temp_dir = join_path(self.info.root_dir, 'temp')
        self.info.data_dir = join_path(self.info.root_dir, 'data')
        self.info.bin_dir = join_path(self.info.root_dir, 'bin')
        self.info.image_dir = join_path(self.info.data_dir, 'images')
        self.info.translations_dir = join_path(self.info.root_dir, 'translations')
        self.info.trusted_keys = join_path(self.info.data_dir, 'trustedkeys.gpg')
        self.info.application_icon = join_path(self.info.image_dir, self.info.application_name.capitalize() + ".ico")
        self.info.icon = self.info.application_icon
        self.info.iso_md5_hashes = {}
        log.debug('data_dir=%s' % self.info.data_dir)
        if self.info.locale:
            locale.setlocale(locale.LC_ALL, self.info.locale)
            log.debug('user defined locale = %s' % self.info.locale)
        gettext.install(self.info.application_name, localedir=self.info.translations_dir, names=['ngettext'])

    def get_installation_tasklist(self):
        self.cache_cd_path()
        dimage = self.info.distro.diskimage
        install_method = getattr(self.info.distro.provider, 'install_method', None)
        # Modern Ubuntu (24.04+) has no ubiquity/lupin: we ship a self-contained
        # installer that runs in the live session and populates root.disk
        # itself. Boot it via the casper preseed trigger (no automatic-ubiquity)
        # and our modern install assets, leaving the legacy path untouched.
        if install_method == 'diskimage-script':
            tasks = [
            Task(self.select_target_dir, description=_("Selecting the target directory")),
            Task(self.create_dir_structure, description=_("Creating the installation directories")),
            Task(self.uncompress_target_dir, description=_("Uncompressing files")),
            Task(self.create_uninstaller, description=_("Creating the uninstaller")),
            Task(self.copy_installation_files, description=_("Copying installation files")),
            Task(self.get_iso, description=_("Retrieving installation files")),
            Task(self.extract_kernel, description=_("Extracting the kernel")),
            Task(self.choose_disk_sizes, description=_("Choosing disk sizes")),
            Task(self.create_install_script_config, description=_("Configuring the installer")),
            Task(self.build_modern_initrd, description=_("Preparing the boot image")),
            Task(self.modify_bootloader, description=_("Adding a new bootloader entry")),
            Task(self.modify_grub_configuration, description=_("Setting up installation boot menu")),
            Task(self.eject_cd, description=_("Ejecting the CD")),
            ]
        # don't use diskimage for a FAT32 target directory
        elif dimage and not self.cd_path and not self.iso_path and not self.info.target_drive.is_fat():
            tasks = [
            Task(self.select_target_dir,
                 description=_("Selecting the target directory")),
            Task(self.create_dir_structure,
                 description=_("Creating the directories")),
            Task(self.create_uninstaller,
                 description=_("Creating the uninstaller")),
            Task(self.create_preseed_diskimage,
                 description=_("Creating a preseed file")),
            Task(self.get_diskimage,
                 description=_("Retrieving installation files")),
            Task(self.extract_diskimage, description=_("Extracting")),
            Task(self.choose_disk_sizes, description=_("Choosing disk sizes")),
            Task(self.expand_diskimage,
                 description=_("Expanding")),
            Task(self.create_swap_diskimage,
                 description=_("Creating virtual memory")),
            Task(self.modify_bootloader,
                 description=_("Adding a new bootloader entry")),
            Task(self.diskimage_bootloader,
                 description=_("Installing the bootloader")),
            ]
        else:
            tasks = [
            Task(self.select_target_dir, description=_("Selecting the target directory")),
            Task(self.create_dir_structure, description=_("Creating the installation directories")),
            Task(self.uncompress_target_dir, description=_("Uncompressing files")),
            Task(self.create_uninstaller, description=_("Creating the uninstaller")),
            Task(self.copy_installation_files, description=_("Copying installation files")),
            Task(self.get_iso, description=_("Retrieving installation files")),
            Task(self.extract_kernel, description=_("Extracting the kernel")),
            Task(self.choose_disk_sizes, description=_("Choosing disk sizes")),
            Task(self.create_preseed, description=_("Creating a preseed file")),
            Task(self.modify_bootloader, description=_("Adding a new bootloader entry")),
            Task(self.modify_grub_configuration, description=_("Setting up installation boot menu")),
            Task(self.create_virtual_disks, description=_("Creating the virtual disks")),
            Task(self.uncompress_files, description=_("Uncompressing files")),
            Task(self.eject_cd, description=_("Ejecting the CD")),
            ]
        description = _("Installing %(distro)s-%(version)s") % dict(distro=self.info.distro.name, version=self.info.version)
        tasklist = ThreadedTaskList(description=description, tasks=tasks)
        return tasklist

    def get_dualboot_tasklist(self):
        '''
        Guided dual-boot: rather than installing into a Wubi loopfile, stage the
        ISO + kernel/initrd and add a boot entry that reboots into the *live*
        Ubuntu installer (no preseed, no automatic-ubiquity). The user then runs
        the distribution's guided "Install alongside Windows", letting Ubuntu
        resize the disk and create a real partition.

        This omits the loopfile-only steps: disk-size selection, the preseed and
        the virtual-disk creation. modify_grub_configuration() detects dual-boot
        and writes a live-installer boot menu.
        '''
        self.cache_cd_path()
        tasks = [
            Task(self.select_target_dir, description=_("Selecting the target directory")),
            Task(self.create_dir_structure, description=_("Creating the installation directories")),
            Task(self.uncompress_target_dir, description=_("Uncompressing files")),
            Task(self.create_uninstaller, description=_("Creating the uninstaller")),
            Task(self.copy_installation_files, description=_("Copying installation files")),
            Task(self.get_iso, description=_("Retrieving installation files")),
            Task(self.extract_kernel, description=_("Extracting the kernel")),
            Task(self.modify_bootloader, description=_("Adding a new bootloader entry")),
            Task(self.modify_grub_configuration, description=_("Setting up installation boot menu")),
            Task(self.uncompress_files, description=_("Uncompressing files")),
            Task(self.eject_cd, description=_("Ejecting the CD")),
            ]
        description = _("Preparing to install %(distro)s-%(version)s alongside Windows") % dict(distro=self.info.distro.name, version=self.info.version)
        tasklist = ThreadedTaskList(description=description, tasks=tasks)
        return tasklist

    def get_autoinstall_tasklist(self):
        '''
        Automated real-partition install alongside Windows.

        Stages the ISO, kernel and initrd exactly like the guided dual-boot path,
        then also writes a subiquity autoinstall.yaml (``user-data`` + empty
        ``meta-data``) into the staging directory so the live session can pick it
        up via the ``ds=nocloud`` cloud-init datasource.  The resulting GRUB menu
        boots the live ISO with ``autoinstall ds=nocloud;s=<path>`` on the kernel
        cmdline, which makes subiquity run unattended and install Ubuntu alongside
        Windows on a real partition using the ``alongside`` storage layout.

        Virtual disk creation, preseed files and the modern initrd hook are all
        omitted; the Ubuntu installer handles partitioning and formatting itself.
        '''
        self.cache_cd_path()
        tasks = [
            Task(self.select_target_dir, description=_("Selecting the target directory")),
            Task(self.create_dir_structure, description=_("Creating the installation directories")),
            Task(self.uncompress_target_dir, description=_("Uncompressing files")),
            Task(self.create_uninstaller, description=_("Creating the uninstaller")),
            Task(self.copy_installation_files, description=_("Copying installation files")),
            Task(self.get_iso, description=_("Retrieving installation files")),
            Task(self.extract_kernel, description=_("Extracting the kernel")),
            Task(self.create_autoinstall_config, description=_("Creating the autoinstall configuration")),
            Task(self.modify_bootloader, description=_("Adding a new bootloader entry")),
            Task(self.modify_grub_configuration, description=_("Setting up installation boot menu")),
            Task(self.uncompress_files, description=_("Uncompressing files")),
            Task(self.eject_cd, description=_("Ejecting the CD")),
        ]
        description = _("Preparing to install %(distro)s-%(version)s alongside Windows") % dict(distro=self.info.distro.name, version=self.info.version)
        tasklist = ThreadedTaskList(description=description, tasks=tasks)
        return tasklist

    def create_autoinstall_config(self, associated_task=None):
        '''
        Write a subiquity autoinstall ``user-data`` file (and empty ``meta-data``)
        into ``<target>/install/autoinstall/`` so the live session can discover
        them through the ``ds=nocloud;s=/isodevice/<path>/autoinstall/`` cloud-init
        source URI passed on the kernel command line.

        The ``storage.layout.name: alongside`` directive tells subiquity to
        automatically resize the largest Windows (NTFS) partition and create the
        required Linux partitions in the freed space, with no manual interaction.
        '''
        autoinstall_dir = join_path(self.info.install_dir, 'autoinstall')
        if not os.path.isdir(autoinstall_dir):
            os.makedirs(autoinstall_dir)

        # subiquity's identity.password expects a SHA-512 ($6$) crypted password.
        hashed_password = sha512_crypt(self.info.password or '')

        # Keyboard: subiquity uses layout/variant directly.
        keyboard_layout = getattr(self.info, 'keyboard_layout', 'us') or 'us'
        keyboard_variant = getattr(self.info, 'keyboard_variant', '') or ''

        # Locale: strip encoding suffix (e.g. "en_US.UTF-8" → "en_US").
        locale = (getattr(self.info, 'locale', '') or 'en_US').split('.')[0]

        username = self.info.username or 'ubuntu'
        hostname = username + '-desktop'

        user_data = (
            "#cloud-config\n"
            "autoinstall:\n"
            "  version: 1\n"
            "  locale: {locale}\n"
            "  keyboard:\n"
            "    layout: {keyboard_layout}\n"
            "    variant: {keyboard_variant}\n"
            "  identity:\n"
            "    hostname: {hostname}\n"
            "    username: {username}\n"
            "    password: '{hashed_password}'\n"
            "  storage:\n"
            "    layout:\n"
            "      name: alongside\n"
            "  ssh:\n"
            "    install-server: false\n"
            "  updates: security\n"
            "  shutdown: reboot\n"
        ).format(
            locale=locale,
            keyboard_layout=keyboard_layout,
            keyboard_variant=keyboard_variant,
            hostname=hostname,
            username=username,
            hashed_password=hashed_password,
        )

        write_file(join_path(autoinstall_dir, 'user-data'), user_data)
        write_file(join_path(autoinstall_dir, 'meta-data'), '')
        self.info.autoinstall_dir = autoinstall_dir
        log.debug("Autoinstall config written to %s" % autoinstall_dir)

    def get_real_partition_findings(self):
        '''
        Gather the safety preconditions for the selected real-partition install
        mode from ``self.info`` and return the findings list (see
        ``check_real_partition_preconditions``). Returns an empty list for the
        loop-file Wubi mode.
        '''
        install_mode = getattr(self.info, 'install_mode', 'wubi') or 'wubi'
        # subiquity's "alongside" layout shrinks the largest partition, which in
        # practice is the Windows system drive; require headroom there. Fall back
        # to the staging target drive when the system drive is unknown.
        resize_drive = getattr(self.info, 'system_drive', None) or getattr(self.info, 'target_drive', None)
        resize_free_mb = getattr(resize_drive, 'free_space_mb', 0) or 0
        distro = getattr(self.info, 'distro', None)
        min_disk_space_mb = getattr(distro, 'min_disk_space_mb', 0) or 0
        required_free_mb = min_disk_space_mb + REAL_PARTITION_HEADROOM_MB
        # BitLocker is intentionally not passed here: the frontend already shows
        # a dedicated BitLocker warning for every install mode, so re-emitting it
        # as a finding would double up the dialog.
        return check_real_partition_preconditions(
            install_mode,
            resize_free_mb,
            required_free_mb,
            getattr(self.info, 'fast_startup_enabled', False),
            bitlocker_drives=None,
            volume_dirty=getattr(self.info, 'volume_dirty', False),
        )

    def get_cdboot_tasklist(self):
        self.cache_cd_path()
        tasks = [
            Task(self.select_target_dir, description=_("Selecting the target directory")),
            Task(self.create_dir_structure, description=_("Creating the installation directories")),
            Task(self.uncompress_target_dir, description=_("Uncompressing files")),
            Task(self.create_uninstaller, description=_("Creating the uninstaller")),
            Task(self.copy_installation_files, description=_("Copying installation files")),
            Task(self.use_cd, description=_("Extracting CD content")),
            Task(self.extract_kernel, description=_("Extracting the kernel")),
            Task(self.create_preseed_cdboot, description=_("Creating a preseed file")),
            Task(self.modify_bootloader, description=_("Adding a new bootloader entry")),
            Task(self.modify_grub_configuration, description=_("Setting up installation boot menu")),
            Task(self.uncompress_files, description=_("Uncompressing files")),
            Task(self.eject_cd, description=_("Ejecting the CD")),
            ]
        tasklist = ThreadedTaskList(description=_("Installing CD boot helper"), tasks=tasks)
        return tasklist

    def get_reboot_tasklist(self):
        tasks = [
            Task(self.reboot, description=_("Rebooting")),
            ]
        tasklist = ThreadedTaskList(description=_("Rebooting"), tasks=tasks)
        return tasklist

    def get_uninstallation_tasklist(self):
        tasks = [
            Task(self.undo_bootloader, _("Remove bootloader entry")),
            Task(self.remove_target_dir, _("Remove target dir")),
            Task(self.remove_registry_key, _("Remove registry key")),]
        tasklist = ThreadedTaskList(description=_("Uninstalling %s") % self.info.previous_distro_name, tasks=tasks)
        return tasklist

    def show_info(self):
        log.debug("Showing info")
        os.startfile(self.info.cd_distro.website)

    def fetch_basic_info(self):
        '''
        Basic information required by the application dispatcher select_task()
        '''
        log.debug("Fetching basic info...")
        self.info.uninstall_before_install = False
        self.info.original_exe = self.get_original_exe()
        self.info.platform = self.get_platform()
        self.info.osname = self.get_osname()
        if not self.info.language:
            self.info.language, self.info.encoding = self.get_language_encoding()
        self.info.environment_variables = os.environ
        self.info.arch = self.get_arch()
        if self.info.force_i386:
            log.debug("Forcing 32 bit arch")
            self.info.arch = "i386"
        self.info.check_arch = (self.info.arch == "i386")
        self.info.distro = None
        self.info.distros = self.get_distros()
        distros = [((d.name.lower(), d.arch), d) for d in  self.info.distros]
        self.info.distros_dict = dict(distros)
        self.fetch_host_info()
        self.info.previous_uninstaller_path = self.get_uninstaller_path()
        self.info.previous_target_dir = self.get_previous_target_dir()
        self.info.previous_distro_name = self.get_previous_distro_name()
        self.info.keyboard_layout, self.info.keyboard_variant = self.get_keyboard_layout()
        if not self.info.locale:
            self.info.locale = self.get_locale(self.info.language)
        self.info.total_memory_mb = self.get_total_memory_mb()
        self.info.iso_path, self.info.iso_distro = self.find_any_iso()
        self.info.cd_path, self.info.cd_distro = self.find_any_cd()

    def get_distros(self):
        isolist_path = join_path(self.info.data_dir, 'isolist.ini')
        distros = self.parse_isolist(isolist_path)
        return distros

    def get_original_exe(self):
        if self.info.original_exe:
            original_exe = self.info.original_exe
        else:
            original_exe = abspath(sys.argv[0])
        log.debug("original_exe=%s" % original_exe)
        return original_exe

    def get_locale(self, language_country, fallback="en_US"):
        _locale = lang_country2linux_locale.get(language_country, None)
        if not _locale:
            _locale = lang_country2linux_locale.get(fallback)
        log.debug("python locale=%s" % str(locale.getdefaultlocale()))
        log.debug("locale=%s" % _locale)
        return _locale

    def get_platform(self):
        platform = sys.platform
        log.debug("platform=%s" % platform)
        return platform

    def get_osname(self):
        osname = os.name
        log.debug("osname=%s" % osname)
        return osname

    def get_language_encoding(self):
        language, encoding = locale.getdefaultlocale()
        log.debug("language=%s" % language)
        log.debug("encoding=%s" % encoding)
        return language, encoding

    def get_arch(self):
        #detects python/os arch not processor arch
        #overridden by platform specific backends
        arch = struct.calcsize('P') == 8 and "amd64" or "i386"
        log.debug("arch=%s" % arch)
        return arch

    def create_dir_structure(self, associated_task=None):
        self.info.disks_dir = join_path(self.info.target_dir, "disks")
        self.info.install_dir = join_path(self.info.target_dir, "install")
        self.info.install_boot_dir = join_path(self.info.install_dir, "boot")
        self.info.disks_boot_dir = join_path(self.info.disks_dir, "boot")
        dirs = [
            self.info.target_dir,
            self.info.disks_dir,
            self.info.install_dir,
            self.info.install_boot_dir,
            self.info.disks_boot_dir,
            join_path(self.info.disks_boot_dir, "grub"),
            join_path(self.info.install_boot_dir, "grub"),]
        for d in dirs:
            if not os.path.isdir(d):
                log.debug("Creating dir %s" % d)
                os.mkdir(d)

    def fetch_installer_info(self):
        '''
        Fetch information required by the installer
        '''

    def dummy_function(self):
        time.sleep(1)

    def check_metalink(self, metalink, base_url, associated_task=None):
        if self.info.skip_md5_check:
            return True
        url = base_url +"/" + self.info.distro.metalink_md5sums
        metalink_md5sums = downloader.download(url, self.info.install_dir, web_proxy=self.info.web_proxy)
        url = base_url +"/" + self.info.distro.metalink_md5sums_signature
        metalink_md5sums_signature = downloader.download(url, self.info.install_dir, web_proxy=self.info.web_proxy)
        if not verify_gpg_signature(metalink_md5sums, metalink_md5sums_signature, self.info.trusted_keys):
            log.error("Could not verify signature for metalink md5sums")
            return False
        md5sums = read_file(metalink_md5sums)
        log.debug("metalink md5sums:\n%s" % md5sums)
        md5sums = dict([reversed(line.split()) for line in md5sums.replace('*','').split('\n') if line])
        hashsum = md5sums.get(os.path.basename(metalink))
        if not hashsum:
            log.error("Could not find %s in metalink md5sums)" % os.path.basename(metalink))
            return False
        hash_len = len(hashsum)*4
        if hash_len == 160:
            hash_name = 'sha1'
        elif hash_len in [224, 256, 384, 512]:
            hash_name = 'sha' + str(hash_len)
        else:
            hash_name = 'md5'
        if self.info.distro.metalink:
           self.info.distro.metalink.files[0].hashes[0].type = hash_name
           self.info.distro.metalink.files[0].hashes[0].hash = hashsum
           return True
        hashsum2 = get_file_hash(metalink, hash_name)
        if hashsum != hashsum2:
            log.error("The %s of the metalink does not match (%s != %s)" % (hash_name, hashsum, hashsum2))
            return False
        return True

    def check_cd(self, cd_path, associated_task=None):
        associated_task.description = _("Checking CD %s") % cd_path
        if not self.info.distro.is_valid_cd(cd_path, check_arch=False):
            return False
        self.set_distro_from_arch(cd_path)
        if self.info.skip_md5_check:
            return True
        md5sums_file = join_path(cd_path, self.info.distro.md5sums)
        for rel_path in self.info.distro.get_required_files():
            if rel_path == self.info.distro.md5sums:
                continue
            check_file = associated_task.add_subtask(self.check_file)
            file_path = join_path(cd_path, rel_path)
            if not check_file(file_path, rel_path, md5sums_file):
                return False
        return True

    def check_iso(self, iso_path, associated_task=None):
        log.debug("Checking %s" % iso_path)
        if not self.info.distro.is_valid_iso(iso_path, check_arch=False):
            return False
        self.set_distro_from_arch(iso_path)
        if self.info.skip_md5_check:
            return True
        hashsum = None
        if not self.info.distro.metalink:
            get_metalink = associated_task.add_subtask(
                self.get_metalink, description=_("Downloading information on installation files"))
            get_metalink()
            if not self.info.distro.metalink:
                log.error("ERROR: the metalink file is not available, cannot check the md5 for %s, ignoring" % iso_path)
                return True
        for hash in self.info.distro.metalink.files[0].hashes:
            if hash.type in ['md5','sha1','sha224','sha256','sha384','sha512']:
                hashsum = hash.hash
                hash_name = hash.type
        if not hashsum:
            log.error("ERROR: Could not find any md5 hash in the metalink for the ISO %s, ignoring" % iso_path)
            return True
        hashsum2 = self.info.iso_md5_hashes.get(iso_path, None)
        if not hashsum2:
            get_hash = associated_task.add_subtask(
                get_file_hash,
                description = _("Checking installation files") )
            hashsum2 = get_hash(iso_path, hash_name)
            if not iso_path.startswith(self.info.install_dir):
                self.info.iso_md5_hashes[iso_path] = hashsum2
        if hashsum != hashsum2:
            log.exception("Invalid %s for ISO %s (%s != %s)" % (hash_name, iso_path, hashsum, hashsum2))
            return False
        return True

    def select_mirrors(self, urls):
        '''
        Sort urls by preference giving a "boost" to the urls in the
        same country as the client
        '''
        urls = list(urls)
        for url in urls:
            url.score = url.preference
            if self.info.country == url.location:
                url.score += 50
        urls.sort(key=lambda u: u.score, reverse=True) #reverse order
        return urls

    def cache_cd_path(self):
        self.iso_path = None
        self.cd_path = None
        if self.info.cd_distro \
        and self.info.distro == self.info.cd_distro \
        and self.info.cd_path \
        and os.path.isdir(self.info.cd_path):
            self.cd_path = self.info.cd_path
        else:
            self.cd_path = self.find_cd()

        if not self.cd_path:
            if self.info.iso_distro \
            and self.info.distro == self.info.iso_distro \
            and os.path.isfile(self.info.iso_path):
                self.iso_path = self.info.iso_path
            else:
                self.iso_path = self.find_iso()

    def create_diskimage_dirs(self, associated_task=None):
        self.info.disks_dir = join_path(self.info.target_dir, "disks")
        self.info.disks_boot_dir = join_path(self.info.disks_dir, "boot")
        dirs = [
            self.info.target_dir,
            self.info.disks_dir,
            self.info.disks_boot_dir,
            join_path(self.info.disks_boot_dir, "grub"),
            ]
        for d in dirs:
            if not os.path.isdir(d):
                log.debug("Creating dir %s" % d)
                os.mkdir(d)

    def download_diskimage(self, diskimage, associated_task=None):
        proxy = self.info.web_proxy
        save_as = join_path(self.info.disks_dir, diskimage.split('/')[-1])
        if os.path.isfile(save_as):
            os.unlink(save_as)
        try:
            download = associated_task.add_subtask(
                downloader.download,
                is_required = False)
            self.dimage_path = download(diskimage, save_as,
                    web_proxy=proxy)
            return self.dimage_path is not None
        except Exception:
            log.exception('Cannot download disk image file %s:' % diskimage)
            return False

    def download_iso(self, associated_task=None):
        log.debug("Could not find any ISO or CD, downloading one now")
        self.info.cd_path = None
        if not self.info.distro.metalink:
            get_metalink = associated_task.add_subtask(
                self.get_metalink, description=_("Downloading information on installation files"))
            get_metalink()
            if not self.info.distro.metalink:
                raise Exception("Cannot download the metalink and therefore the ISO")
        file = self.info.distro.metalink.files[0]
        save_as = join_path(self.info.install_dir, file.name)
        urls = self.select_mirrors(file.urls)
        for url in urls[:5]:
            if url.type == 'bittorrent':
                # BitTorrent is opt-in: the vendored client is not yet
                # Python 3 ready (bencode/wire-protocol bytes handling), and
                # a failure mid-protocol could stall instead of falling back,
                # so by default we skip straight to the HTTP mirrors.
                if not getattr(self.info, 'use_bittorrent', False) or self.info.no_bittorrent:
                    continue
                if os.path.exists(save_as):
                    try:
                        os.unlink(save_as)
                    except OSError:
                        logging.exception('Could not remove: %s' % save_as)
                btdownload = associated_task.add_subtask(
                    btdownloader.download,
                    is_required = False)
                iso_path = btdownload(url.url, save_as)
            else:
                if os.path.exists(save_as):
                    try:
                        os.unlink(save_as)
                    except OSError:
                        logging.exception('Could not remove: %s' % save_as)
                download = associated_task.add_subtask(
                    downloader.download,
                    is_required = True)
                iso_path = download(url.url, save_as, web_proxy=self.info.web_proxy)
            if iso_path:
                check_iso = associated_task.add_subtask(
                    self.check_iso,
                    description = _("Checking installation files"))
                if check_iso(iso_path):
                    self.info.iso_path = iso_path
                    return True
                else:
                    os.unlink(iso_path)

    def get_metalink(self, associated_task=None):
        associated_task.description = _("Downloading information on installation files")
        try:
            url = self.info.distro.metalink_url
            metalink = downloader.download(url, self.info.install_dir, web_proxy=self.info.web_proxy)
            base_url = os.path.dirname(url)
        except Exception as err:
            log.error("Cannot download metalink file %s err=%s" % (url, err))
            try:
                url = self.info.distro.metalink_url2
                metalink = downloader.download(url, self.info.install_dir, web_proxy=self.info.web_proxy)
                base_url = os.path.dirname(url)
            except Exception as err:
                log.error("Cannot download metalink file2 %s err=%s" % (url, err))
                return
        metalink_filename, metalink_extension = os.path.splitext(metalink)
        if metalink_extension == '.list':
            self.info.distro.metalink = parse_metalink(join_path(self.info.data_dir, 'list.metalink'))
            metalink = metalink_filename + ".iso"
            self.info.distro.metalink.files[0].name = os.path.basename(metalink)
            self.info.distro.metalink.files[0].urls[0].url = base_url + "/" + self.info.distro.metalink.files[0].name + ".torrent"
            self.info.distro.metalink.files[0].urls[1].url = base_url + "/" + self.info.distro.metalink.files[0].name
        if not self.check_metalink(metalink, base_url):
            log.exception("Cannot authenticate the metalink file, it might be corrupt")
        if not self.info.distro.metalink:
            self.info.distro.metalink = parse_metalink(metalink)

    def get_prespecified_diskimage(self, associated_task):
        '''
        Use a local disk image specificed on the command line
        '''
        if self.info.dimage_path \
        and os.path.exists(self.info.dimage_path):
            #TBD shall we do md5 check? Doesn't work well with daylies
            #TBD if a specified disk image cannot be used notify the user
            self.dimage_path = self.info.dimage_path
            log.debug("Trying to use pre-specified disk image %s" % self.info.dimage_path)
            is_valid_dimage = associated_task.add_subtask(
                self.info.distro.is_valid_dimage,
                description = _("Validating %s") % self.info.dimage_path)
            if is_valid_dimage(self.info.dimage_path, self.info.check_arch):
                self.info.cd_path = None
                return True

    def get_prespecified_iso(self, associated_task):
        if self.info.iso_path \
        and os.path.exists(self.info.iso_path):
            #TBD shall we do md5 check? Doesn't work well with daylies
            #TBD if a specified ISO cannot be used notify the user
            log.debug("Trying to use pre-specified ISO %s" % self.info.iso_path)
            is_valid_iso = associated_task.add_subtask(
                self.info.distro.is_valid_iso,
                description = _("Validating %s") % self.info.iso_path)
            if is_valid_iso(self.info.iso_path, self.info.check_arch):
                self.info.cd_path = None
            return self.copy_iso(self.info.iso_path, associated_task)

    def set_distro_from_arch(self, cd_or_iso_path):
        '''
        Make sure that the distro is in line with the arch
        This is to make sure that a 32 bit local CD or ISO
        is used even though the arch is 64 bits
        '''
        if self.info.check_arch:
            return
        arch = self.info.distro.get_info(cd_or_iso_path)[3]
        if self.info.distro.arch == arch:
            return
        name = self.info.distro.name
        log.debug("Using distro %s %s instead of %s %s" % \
            (name, arch, name, self.info.distro.arch))
        distro = self.info.distros_dict.get((name.lower(), arch))
        self.info.distro = distro

    def copy_diskimage(self, dimage_path, associated_task):
        if not dimage_path:
            return
        dimage_name = self.info.distro.diskimage.split('/')[-1]
        dest = os.path.join(self.info.disks_dir, dimage_name)
        copy_dimage = associated_task.add_subtask(
            copy_file,
            description = _("Copying installation files"))
        log.debug("Copying %s > %s" % (dimage_path, dest))
        copy_dimage(dimage_path, dest)
        return True

    def copy_iso(self, iso_path, associated_task):
        if not iso_path:
            return
        dest = join_path(self.info.install_dir, "installation.iso")
        check_iso = associated_task.add_subtask(
            self.check_iso,
            description = _("Checking installation files"))
        if check_iso(iso_path):
            if os.path.dirname(iso_path) == dest:
                move_iso = associated_task.add_subtask(
                    shutil.move,
                    description = _("Copying installation files"))
                log.debug("Moving %s > %s" % (iso_path, dest))
                move_iso(iso_path, dest)
            else:
                copy_iso = associated_task.add_subtask(
                    copy_file,
                    description = _("Copying installation files"))
                log.debug("Copying %s > %s" % (iso_path, dest))
                copy_iso(iso_path, dest)
            self.info.cd_path = None
            self.info.iso_path = dest
            return True

    def use_cd(self, associated_task):
        if self.cd_path:
            extract_iso = associated_task.add_subtask(
                copy_file,
                description = _("Extracting files from %s") % self.cd_path)
            self.info.iso_path = join_path(self.info.install_dir, "installation.iso")
            try:
                extract_iso(self.cd_path, self.info.iso_path)
            except Exception as err:
                log.error(err)
                self.info.cd_path = None
                self.info.iso_path = None
                return False
            self.info.cd_path = self.cd_path
            #This will often fail before release as the CD might not match the latest daily ISO
            check_iso = associated_task.add_subtask(
                self.check_iso,
                description = _("Checking installation files"))
            if not check_iso(self.info.iso_path):
                subversion = self.info.cd_distro.get_info(self.info.cd_path)[2]
                if subversion.lower() in ("alpha", "beta", "release candidate"):
                    log.error("CD check failed, but ignoring because CD is %s" % subversion)
                else:
                    self.info.cd_path = None
                    self.info.iso_path = None
                    return False
            return True

    def use_iso(self, associated_task):
        if self.iso_path:
            log.debug("Trying to use ISO %s" % self.iso_path)
            return self.copy_iso(self.iso_path, associated_task)

    def get_diskimage(self, associated_task=None):
        '''
        Get a diskimage either locally or from the mirror
        '''
        if self.get_prespecified_diskimage(associated_task):
            return associated_task.finish()
        dimage = self.info.distro.diskimage
        if self.download_diskimage(dimage, associated_task):
            return associated_task.finish()
        else:
            dimage2 = self.info.distro.diskimage2
            if self.download_diskimage(dimage2, associated_task):
                return associated_task.finish()

        raise Exception("Could not retrieve the required disk image files")

    def get_iso(self, associated_task=None):
        if self.get_prespecified_iso(associated_task) \
        or self.use_cd(associated_task) \
        or self.use_iso(associated_task) \
        or self.download_iso(associated_task):
            return associated_task.finish()
        raise Exception("Could not retrieve the required installation files")

    def extract_kernel(self):
        bootdir = self.info.install_boot_dir
        # Extract kernel, initrd, md5sums
        if self.info.cd_path:
            log.debug("Copying files from CD %s" % self.info.cd_path)
            for src in [
            join_path(self.info.cd_path, self.info.distro.md5sums),
            join_path(self.info.cd_path, self.info.distro.kernel),
            join_path(self.info.cd_path, self.info.distro.initrd),]:
                shutil.copy(src, bootdir)
        elif self.info.iso_path:
            log.debug("Extracting files from ISO %s" % self.info.iso_path)
            self.extract_file_from_iso(self.info.iso_path, self.info.distro.md5sums, output_dir=bootdir)
            self.extract_file_from_iso(self.info.iso_path, self.info.distro.kernel, output_dir=bootdir)
            self.extract_file_from_iso(self.info.iso_path, self.info.distro.initrd, output_dir=bootdir)
        else:
            raise Exception("Could not retrieve the required installation files")
        # Check the files
        log.debug("Checking kernel, initrd and md5sums")
        self.info.kernel = join_path(bootdir, os.path.basename(self.info.distro.kernel))
        self.info.initrd = join_path(bootdir, os.path.basename(self.info.distro.initrd))
        md5sums = join_path(bootdir, os.path.basename(self.info.distro.md5sums))
        paths = [
            (self.info.kernel, self.info.distro.kernel),
            (self.info.initrd, self.info.distro.initrd),]
        for file_path, rel_path in paths:
                if not self.check_file(file_path, rel_path, md5sums):
                    raise Exception("File %s is corrupted" % file_path)

    def check_file(self, file_path, relpath, md5sums, associated_task=None):
        log.debug("  checking %s" % file_path)
        if associated_task:
            associated_task.description = _("Checking %s") % file_path
        relpath = relpath.replace("\\", "/")
        md5line = find_line_in_file(md5sums, "./%s" % relpath, endswith=True)
        if not md5line:
            raise Exception("Cannot find md5 in %s for %s" % (md5sums, relpath))
        reference_hash = md5line.split()[0]
        hash_len = len(reference_hash)*4
        if hash_len == 160:
            hash_name = 'sha1'
        elif hash_len in [224, 256, 384, 512]:
            hash_name = 'sha' + str(hash_len)
        else:
            hash_name = 'md5'
        hash_file = get_file_hash(file_path, hash_name, associated_task)
        log.debug("  %s %s = %s %s %s" % (file_path, hash_name, hash_file, hash_file == reference_hash and "==" or "!=", reference_hash))
        return hash_file == reference_hash

    def create_preseed_diskimage(self):
        source = join_path(self.info.data_dir, 'preseed.disk')
        template = read_file(source)
        password = md5_password(self.info.password)
        dic = dict(
            timezone = self.info.timezone,
            password = password,
            keyboard_variant = self.info.keyboard_variant,
            keyboard_layout = self.info.keyboard_layout,
            locale = self.info.locale,
            user_full_name = self.info.user_full_name,
            username = self.info.username)
        for k,v in dic.items():
            k = "$(%s)" % k
            template = template.replace(k, v)
        preseed_file = join_path(self.info.install_dir, "preseed.cfg")
        write_file(preseed_file, template)

        source = join_path(self.info.data_dir, "wubildr-disk.cfg")
        target = join_path(self.info.install_dir, "wubildr-disk.cfg")
        copy_file(source, target)

    def create_preseed_cdboot(self):
        source = join_path(self.info.data_dir, 'preseed.cdboot')
        target = join_path(self.info.custominstall, "preseed.cfg")
        copy_file(source, target)

    def create_preseed(self):
        template_file = join_path(self.info.data_dir, 'preseed.' + self.info.distro.name)
        if not os.path.exists(template_file):
            template_file = join_path(self.info.data_dir, 'preseed.lupin')
        template = read_file(template_file)
        if self.info.distro.packages:
            distro_packages_skip = ''
        else:
            distro_packages_skip = '#'
        partitioning = ""
        partitioning += "d-i partman-auto/disk string LIDISK\n"
        partitioning += "d-i partman-auto/method string loop\n"
        partitioning += "d-i partman-auto-loop/partition string LIPARTITION\n"
        partitioning += "d-i partman-auto-loop/recipe string \\\n"
        disks_dir = unix_path(self.info.disks_dir) + '/'
        if self.info.root_size_mb:
            partitioning += '  %s 3000 %s %s $default_filesystem method{ format } format{ } use_filesystem{ } $default_filesystem{ } mountpoint{ / } . \\\n' \
            %(disks_dir + 'root.disk', self.info.root_size_mb, self.info.root_size_mb)
        if self.info.swap_size_mb:
            partitioning += '  %s 100 %s %s linux-swap method{ swap } format{ } . \\\n' \
            %(disks_dir + 'swap.disk', self.info.swap_size_mb, self.info.swap_size_mb)
        if self.info.home_size_mb:
            partitioning += '  %s 100 %s %s $default_filesystem method{ format } format{ } use_filesystem{ } $default_filesystem{ } mountpoint{ /home } . \\\n' \
            %(disks_dir + 'home.disk', self.info.home_size_mb, self.info.home_size_mb)
        if self.info.usr_size_mb:
            partitioning += '  %s 100 %s %s $default_filesystem method{ format } format{ } use_filesystem{ } $default_filesystem{ } mountpoint{ /usr } . \\\n' \
            %(disks_dir + 'usr.disk', self.info.usr_size_mb, self.info.usr_size_mb)
        partitioning += "\n"
        safe_host_username = self.info.host_username.replace(" ", "+")
        user_directory = self.info.user_directory.replace("\\", "/")[2:]
        host_os_name = "Windows XP Professional" #TBD
        password = md5_password(self.info.password)
        dic = dict(
            timezone = self.info.timezone,
            password = password,
            user_full_name = self.info.user_full_name,
            distro_packages_skip  = distro_packages_skip,
            distro_packages = self.info.distro.packages,
            host_username = self.info.host_username,
            username = self.info.username,
            partitioning = partitioning,
            user_directory = user_directory,
            safe_host_username = safe_host_username,
            host_os_name = host_os_name,
            custom_installation_dir = unix_path(self.info.custominstall),)
        content = template
        for k,v in dic.items():
            k = "$(%s)" % k
            content = content.replace(k, v)
        preseed_file = join_path(self.info.custominstall, "preseed.cfg")
        write_file(preseed_file, content)

    @staticmethod
    def _newc_cpio(entries):
        '''
        Build a (newc / "070701") cpio archive in pure Python. `entries` is a
        list of (path, mode, data) tuples; data is None for directories. This
        lets wubi assemble the casper-hook overlay on Windows without any
        external cpio/gzip tooling.
        '''
        def field(n):
            return b"%08x" % (n & 0xffffffff)
        out = []
        ino = 1
        for path, mode, data in entries:
            is_dir = data is None
            payload = b"" if is_dir else data
            name = path.encode("ascii") + b"\0"
            header = (b"070701"
                + field(ino)              # ino
                + field(mode)             # mode
                + field(0) + field(0)     # uid, gid
                + field(2 if is_dir else 1)  # nlink
                + field(0)                # mtime
                + field(len(payload))     # filesize
                + field(0) + field(0)     # devmajor, devminor
                + field(0) + field(0)     # rdevmajor, rdevminor
                + field(len(name))        # namesize
                + field(0))               # check
            buf = header + name
            buf += b"\0" * ((4 - len(buf) % 4) % 4)   # pad after name
            buf += payload
            buf += b"\0" * ((4 - len(buf) % 4) % 4)   # pad after data
            out.append(buf)
            ino += 1
        trailer_name = b"TRAILER!!!\0"
        trailer = (b"070701"
                   + field(0)            # ino
                   + field(0)            # mode
                   + field(0) + field(0)  # uid, gid
                   + field(1)            # nlink
                   + field(0)            # mtime
                   + field(0)            # filesize
                   + field(0) + field(0)  # devmajor, devminor
                   + field(0) + field(0)  # rdevmajor, rdevminor
                   + field(len(trailer_name))
                   + field(0)) + trailer_name
        trailer += b"\0" * ((4 - len(trailer) % 4) % 4)
        out.append(trailer)
        return b"".join(out)

    def build_modern_initrd(self):
        '''
        Modern Ubuntu (24.04+) casper trigger. Instead of preseed/early_command
        (which silently fails on desktop ISOs - no debian-installer/preseed
        debconf templates) we OVERRIDE casper-bottom/24preseed: a tiny cpio
        archive carrying our hook is appended to the casper initrd. The kernel
        processes stacked initrd archives in order, so our 24preseed replaces
        casper's and stages the installer (see casper-hook/24preseed). The hook
        is launched by the existing casper-bottom/ORDER entry, so no ORDER edit
        is needed. install.sh then does the real work, configured by
        create_install_script_config.
        '''
        hook_src = join_path(self.info.data_dir, 'custom-installation-modern',
                             'casper-hook', '24preseed')
        hook = read_file(hook_src)
        if isinstance(hook, str):
            hook = hook.encode('utf-8')
        entries = [
            ("scripts", 0o040755, None),
            ("scripts/casper-bottom", 0o040755, None),
            ("scripts/casper-bottom/24preseed", 0o100755, hook),
        ]
        overlay = self._newc_cpio(entries)
        # Append the overlay to the casper initrd extracted by extract_kernel,
        # keeping the (zstd-compressed) stock initrd intact and 4-byte aligned.
        with open(self.info.initrd, 'rb') as f:
            stock = f.read()
        pad = (4 - len(stock) % 4) % 4
        with open(self.info.initrd, 'wb') as f:
            f.write(stock)
            f.write(b"\0" * pad)
            f.write(overlay)

    def create_install_script_config(self):
        '''
        Write the key=value config consumed by the modern install.sh. This is
        how the Windows-side choices (sizes, user, locale, ...) reach the
        self-contained installer running in the live session.
        '''
        target_dir = '/' + unix_path(self.info.distro.installation_dir).strip('/')
        install_dir = unix_path(self.info.install_dir)
        # install_dir is an absolute host path (e.g. C:/ubuntu/install); keep the
        # part below the drive so it is meaningful inside the live session.
        install_dir = '/' + install_dir.split(':', 1)[-1].strip('/')
        lines = [
            "# Generated by Wubi - consumed by install.sh",
            "TARGET_DIR=%s" % target_dir,
            "INSTALL_DIR=%s" % install_dir,
            "ROOT_SIZE_MB=%s" % int(self.info.root_size_mb or 0),
            "SWAP_SIZE_MB=%s" % int(self.info.swap_size_mb or 0),
            "USERNAME=%s" % self.info.username,
            "USER_FULL_NAME=%s" % (self.info.user_full_name or self.info.username),
            "PASSWORD=%s" % self.info.password,
            "HOST_NAME=%s" % self.info.host_username.replace(' ', '-'),
            "LOCALE=%s" % self.info.locale,
            "KEYBOARD_LAYOUT=%s" % self.info.keyboard_layout,
            "KEYBOARD_VARIANT=%s" % self.info.keyboard_variant,
            "TIMEZONE=%s" % self.info.timezone,
            "AUTO_REBOOT=true",
            'SQUASH_LAYERS="%s"' % ' '.join(
                os.path.basename(p)
                for p in getattr(self.info.distro.provider, 'target_squashfs_layers', ())),
            ]
        config_file = join_path(self.info.custominstall, "config")
        write_file(config_file, '\n'.join(lines) + '\n')

    def modify_bootloader(self):
        #platform specific
        pass

    def modify_grub_configuration(self):
        install_mode = getattr(self.info, 'install_mode', None) or (
            'guided' if getattr(self.info, 'dualboot', False) else 'wubi')
        if install_mode == 'autoinstall':
            template_file = join_path(self.info.data_dir, 'grub.autoinstall.cfg')
        elif install_mode == 'guided':
            # Boot the stock live session; preseed/automatic-ubiquity stripped below.
            template_file = join_path(self.info.data_dir, 'grub.install.cfg')
        elif getattr(self.info.distro.provider, 'install_method', None) == 'diskimage-script':
            template_file = join_path(self.info.data_dir, 'grub.install.modern.cfg')
        else:
            template_file = join_path(self.info.data_dir, 'grub.install.cfg')
        template = read_file(template_file)
        if self.info.run_task == "cd_boot":
            isopath = ""
        ## TBD at the moment we are extracting the ISO, not the CD content
        #~ elif self.info.cd_path:
            #~ isopath = unix_path(self.info.cd_path)
        elif self.info.iso_path:
            isopath = unix_path(self.info.iso_path)
        rootflags = "rootflags=sync"
        autoinstall_dir_unix = unix_path(
            getattr(self.info, 'autoinstall_dir', '')) if install_mode == 'autoinstall' else ''
        dic = dict(
            custom_installation_dir = unix_path(self.info.custominstall),
            autoinstall_dir = autoinstall_dir_unix,
            iso_path = isopath,
            keyboard_variant = self.info.keyboard_variant,
            keyboard_layout = self.info.keyboard_layout,
            locale = self.info.locale,
            accessibility = self.info.accessibility,
            kernel = unix_path(self.info.kernel),
            initrd = unix_path(self.info.initrd),
            rootflags = rootflags,
            title1 = "Completing the Ubuntu installation.",
            title2 = "For more installation boot options, press `ESC' now...",
            normal_mode_title = "Normal mode",
            pae_mode_title = "PAE mode",
            safe_graphic_mode_title = "Safe graphic mode",
            intel_graphics_workarounds_title = "Intel graphics workarounds",
            nvidia_graphics_workarounds_title = "Nvidia graphics workarounds",
            acpi_workarounds_title = "ACPI workarounds",
            verbose_mode_title = "Verbose mode",
            demo_mode_title =  "Demo mode",
            )
        content = template
        for k,v in dic.items():
            k = "$(%s)" % k
            content = content.replace(k, v)
        if self.info.run_task == "cd_boot":
            content = content.replace(" automatic-ubiquity", "")
            content = content.replace(" iso-scan/filename=", "")
        elif install_mode == 'guided':
            # Boot the live session for a guided "Install alongside Windows":
            # drop the automatic preseeded install and the preseed file, but
            # keep iso-scan so the live ISO is found and booted from the loopback.
            content = content.replace(" automatic-ubiquity", "")
            content = content.replace(" noprompt", "")
            content = re.sub(r"file=\S*preseed\.cfg\s*", "", content)
        grub_config_file = join_path(self.info.install_boot_dir, "grub", "grub.cfg")
        write_file(grub_config_file, content)

    def remove_target_dir(self, associated_task=None):
        if not os.path.isdir(self.info.previous_target_dir):
            log.debug("Cannot find %s" % self.info.previous_target_dir)
            return
        log.debug("Deleting %s" % self.info.previous_target_dir)
        try:
            rm_tree(self.info.previous_target_dir)
        except OSError as e:
            if e.errno == 22:
                log.exception('Unable to remove the target directory.')
                # Invalid argument - likely a corrupt file.
                cmd = spawn_command(['chkdsk', '/F'])
                cmd.communicate(input='Y%s' % os.linesep)
                raise errors.WubiCorruptionError

    def find_iso(self, associated_task=None):
        log.debug("Searching for local ISO")
        for path in self.get_iso_search_paths():
            path = join_path(path, '*.iso')
            isos = glob.glob(path)
            for iso in isos:
                if self.info.distro.is_valid_iso(iso, self.info.check_arch):
                    return iso

    def find_any_iso(self):
        '''
        look for local ISOs or pre specified ISO
        '''
        #Use pre-specified ISO
        if self.info.iso_path \
        and os.path.exists(self.info.iso_path):
            log.debug("Checking pre-specified ISO %s" % self.info.iso_path)
            for distro in self.info.distros:
                if distro.is_valid_iso(self.info.iso_path, self.info.check_arch):
                    self.info.cd_path = None
                    return self.info.iso_path, distro
        #Search local ISOs
        log.debug("Searching for local ISOs")
        for path in self.get_iso_search_paths():
            path = join_path(path, '*.iso')
            isos = glob.glob(path)
            for iso in isos:
                for distro in self.info.distros:
                    if distro.is_valid_iso(iso, self.info.check_arch):
                        return iso, distro
        return None, None

    def select_iso(self, iso_path):
        '''
        Validate a user-chosen ISO and, if it matches a supported distro,
        record it as the ISO to install from. Returns the matching distro,
        or None if the file is not a usable installation image.
        '''
        if not iso_path or not os.path.isfile(iso_path):
            return None
        log.debug("User selected ISO %s" % iso_path)
        for distro in self.info.distros:
            if distro.is_valid_iso(iso_path, self.info.check_arch):
                self.info.iso_path = iso_path
                self.info.iso_distro = distro
                self.info.cd_path = None
                self.info.cd_distro = None
                log.info("Selected ISO %s matches %s" % (iso_path, distro.name))
                return distro
        log.info("Selected ISO %s is not a supported image" % iso_path)
        return None

    def find_any_cd(self):
        log.debug("Searching for local CDs")
        for path in self.get_cd_search_paths():
            path = abspath(path)
            for distro in self.info.distros:
                if distro.is_valid_cd(path, self.info.check_arch):
                    if self.info.original_exe[:2] != path[:2]:
                        # We don't want to use the CD if it's inserted when the
                        # user is running Wubi from disk.
                        return None, None
                    else:
                        return path, distro
        return None, None

    def find_cd(self):
        log.debug("Searching for local CD")
        for path in self.get_cd_search_paths():
            path = abspath(path)
            if self.info.distro.is_valid_cd(path, self.info.check_arch):
                return path

    def parse_isolist(self, isolist_path):
        log.debug('Parsing isolist=%s' % isolist_path)
        isolist = ConfigParser.ConfigParser()
        isolist.read(isolist_path)
        distros = []
        for distro in isolist.sections():
            log.debug('  Adding distro %s' % distro)
            kargs = dict(isolist.items(distro))
            kargs['backend'] = self
            distros.append(Distro(**kargs))
            #order is lost in configparser, use the ordering attribute
        distros.sort(key=lambda d: d.ordering)
        return distros

    def run_previous_uninstaller(self):
        if not self.info.previous_uninstaller_path \
        or not os.path.isfile(self.info.previous_uninstaller_path):
            return
        previous_uninstaller = self.info.previous_uninstaller_path.lower()
        uninstaller = self.info.previous_uninstaller_path
        command = [uninstaller, "--uninstall"]
        # Propagate noninteractive mode to the uninstaller
        if self.info.non_interactive:
            command.append("--noninteractive")
        if 0 and previous_uninstaller.lower() == self.info.original_exe.lower():
            # This block is disabled as the functionality is achived via pylauncher
            if self.info.original_exe.lower().startswith(self.info.previous_target_dir.lower()):
                log.debug("Copying uninstaller to a temp directory, so that we can delete the containing directory")
                uninstaller = tempfile.NamedTemporaryFile()
                uninstaller.close()
                uninstaller = uninstaller.name
                copy_file(self.info.previous_uninstaller_path, uninstaller)
            log.info("Launching asynchronously previous uninstaller %s" % uninstaller)
            run_nonblocking_command(command, show_window=True)
            return True
        elif get_file_hash(self.info.original_exe) == get_file_hash(self.info.previous_uninstaller_path):
            log.info("This is the uninstaller running")
        else:
            log.info("Launching previous uninestaller %s" % uninstaller)
            subprocess.call(command)
            # Note: the uninstaller is now non-blocking so we can just as well quit this running version
            # TBD: make this call synchronous by waiting for the children process of the uninstaller
            self.application.quit()
            return True

