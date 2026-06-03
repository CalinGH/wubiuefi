# Copyright (c) 2008 Agostino Russo
#
# This file is part of Wubi the Win32 Ubuntu Installer.
#
# Wubi is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as
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

'''
Distribution providers.

A DistroProvider encapsulates the distribution-specific knowledge that Wubi
needs in order to identify an image and (later) drive the install and boot
configuration. It is the extension point that lets Wubi support more than the
historical Ubuntu/casper/ubiquity layout: a new release family (e.g. modern
Ubuntu installed via a self-contained installer) or a different distribution
(e.g. Fedora) is added by implementing a new provider rather than by editing
the shared backend logic.

A provider is bound to a Distro instance (so it can read configuration such as
the expected architecture) and is selected through the optional "provider="
key in isolist.ini. When the key is absent the historical Ubuntu behaviour is
used, so existing configuration keeps working unchanged.
'''

import re
import logging

log = logging.getLogger('DistroProvider')

_PROVIDERS = {}


def register_provider(cls):
    _PROVIDERS[cls.name] = cls
    return cls


def get_provider(name):
    '''
    Return the provider class registered under ``name`` (falling back to the
    default Ubuntu provider when ``name`` is empty or unknown).
    '''
    if name:
        name = name.strip().lower()
    provider = _PROVIDERS.get(name)
    if provider is None:
        if name:
            log.warning("Unknown distro provider %r, using default", name)
        provider = _PROVIDERS[DEFAULT_PROVIDER]
    return provider


class DistroProvider(object):
    '''
    Base class describing the distro-specific operations. Subclasses override
    the class attributes and methods below. Everything here is intentionally
    declarative/minimal so that future providers (modern Ubuntu, Fedora, ...)
    have a clear contract to implement.
    '''

    #: Identifier referenced from isolist.ini via "provider=".
    name = 'base'

    #: Default in-image metadata file describing name/version/arch.
    default_info_file = None
    #: Default in-image paths of the live kernel and initrd.
    default_kernel = None
    default_initrd = None
    #: Default file whose presence marks a valid image.
    default_files_to_check = None
    #: data/ template used to answer the installer (without distro suffix).
    default_preseed_template = None
    #: How the install is actually performed; consulted by the backend.
    install_method = None

    def __init__(self, distro):
        self.distro = distro

    def parse_info(self, info):
        '''
        Parse the contents of the in-image metadata file into the tuple
        (name, version, subversion, arch). Return None when ``info`` is empty
        and a tuple with empty fields when the format is not recognised.
        '''
        raise NotImplementedError


@register_provider
class UbuntuProvider(DistroProvider):
    '''
    Historical Ubuntu family layout: a casper live system whose metadata lives
    in ``.disk/info`` and which is installed by ubiquity driven through a
    debconf preseed plus the lupin loopback patches.
    '''

    name = 'ubuntu'

    default_info_file = '.disk/info'
    default_kernel = 'casper/vmlinuz'
    default_initrd = 'casper/initrd'
    default_files_to_check = 'casper/filesystem.squashfs'
    default_preseed_template = 'lupin'
    install_method = 'ubiquity-lupin'

    # e.g.
    #   Ubuntu 9.04 "Jaunty Jackalope" - Alpha i386 (20090106)
    #   Ubuntu-Studio 12.10 "Quantal Quetzal" - Release amd64 (20121017.1)
    info_re = re.compile(
        r'''(?P<name>[\w\s-]+) (?P<version>[\w.]+)(?: LTS)?(?: (?:[\"\(])?(?P<codename>[\w\s-]+)(?:[\"\)])?)? - (?P<subversion>[\D]+)? (?P<arch>i386|amd64)(?:[\D]+)?(?P<build>[\d:.-]+)?''')

    def parse_info(self, info):
        if not info:
            return None
        match = self.info_re.match(info)
        if match is None:
            log.debug("  parsed info=None")
            return ("", "", "", self.distro.arch)
        log.debug("  parsed info=%s" % match.groupdict())
        return (
            match.group('name').replace('-', ' '),
            match.group('version'),
            match.group('subversion'),
            match.group('arch'))


DEFAULT_PROVIDER = 'ubuntu'
