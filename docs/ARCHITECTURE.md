# Architecture

A short tour of how WubiUEFI is structured, aimed at contributors.

## Backend / frontend split

The application (`src/wubi`) is split into a **backend** (does the work) and a
**frontend** (the GUI), running in separate threads and communicating through a
**tasklist** (`backends/common/tasklist.py`): the frontend runs a tasklist, which
is an ordered set of backend tasks with progress/error propagation. Both layers
are platform-specific; only Windows is implemented:

* `backends/common` — shared, OS-independent logic.
* `backends/win32` — Windows specifics (registry, drives, EFI/BCD, memory,
  BitLocker detection, virtual-disk allocation).
* `frontends/win32` — the GUI pages, built on the `src/winui` ctypes wrapper.

`src/main.py` adds the bundled `lib/` to `sys.path` and starts
`wubi.application.Wubi`. When frozen, PyInstaller unpacks resources under
`sys._MEIPASS`.

## What an install does

The Windows side never installs Linux directly. It:

1. fetches host info and checks requirements;
2. picks/finds/downloads an ISO (HTTP via `urlgrabber`; checksums + GPG via
   `openpgp`);
3. allocates the loopback `root.disk` and writes a boot entry (BCD/EFI, with a
   signed shim+GRUB for Secure Boot);
4. reboots into the live ISO, where the install is completed and `root.disk` is
   populated.

At normal boot afterwards, `data/wubildr*.cfg` loop-mounts `root.disk` and boots
the installed system (`loop=/…/root.disk root=UUID=<host>`).

## Distro providers

`backends/common/providers.py` is the extension point that decouples
distribution-specific knowledge from the shared backend. A `DistroProvider` is
bound to a `Distro` and is selected per `data/isolist.ini` entry via the
`provider=` key (default `ubuntu`). It declares the image layout (info file,
kernel/initrd, the files that mark a valid image), the install template, and the
`install_method` the backend branches on.

| Provider | `install_method` | Releases | How the install is performed |
|----------|------------------|----------|------------------------------|
| `UbuntuProvider` (`ubuntu`) | `ubiquity-lupin` | ≤ 22.04 | The distribution's `ubiquity` installer, driven by a debconf preseed (`data/preseed.lupin`) plus the `lupin` loopback patches (`data/custom-installation/`). |
| `UbuntuModernProvider` (`ubuntu-modern`) | `diskimage-script` | 24.04+ | A self-contained Wubi installer (see below). |

Unknown/absent providers fall back to `UbuntuProvider`, so existing
configuration keeps working. Metadata (`.disk/info`) parsing is shared, since the
format is unchanged.

## The modern (24.04+) self-contained installer

Ubuntu 24.04 replaced `ubiquity`/`lupin` with the subiquity /
ubuntu-desktop-provision installer, which **cannot install into a loopback
file**. The boot-time loop mechanism is installer-independent, so only the
*install stage* needed replacing — Wubi performs it itself instead of driving a
distribution installer.

Pieces (under `data/custom-installation-modern/` and `backends/common/backend.py`):

* **Casper trigger.** On 24.04 desktop `preseed/early_command` is silently
  ignored (no debconf templates), so the backend appends a small CPIO to the
  casper `initrd` (`build_modern_initrd`) that overrides
  `scripts/casper-bottom/24preseed` to launch the installer as a systemd oneshot
  (`wubi-install.service`). The boot config is `data/grub.install.modern.cfg`
  (boots `casper` **without** `automatic-ubiquity`).
* **`install.sh`.** Runs in the live session: creates and formats the loopback
  `root.disk`, unpacks the *installed* squashfs layers (`minimal.squashfs` +
  `minimal.standard.squashfs`, **excluding** the `*.live` layer) into it,
  installs a kernel + modules offline from the ISO pool, sets up a durable
  loop-boot initramfs (re-applied via an initramfs-tools hook so kernel updates
  don't break booting), writes `fstab`/locale/keyboard/timezone/hostname, creates
  the user, and writes `root.disk:/boot/grub/grub.cfg` for the `wubildr` chain.

The legacy path is untouched: the backend selects the modern tasklist, grub
template and install assets only when `install_method == 'diskimage-script'`.

## Vendored libraries

`src/openpgp` (GPG signature verification), `src/urlgrabber` (HTTP download) and
`src/bittorrent` (optional, opt-in via `--bittorrent`) are vendored. They were
ported to Python 3; the HTTP download path is the verified primary, with
BitTorrent off by default.

## Build

The executable is produced with PyInstaller (`wubi.spec`) under Wine. See
[BUILD.md](BUILD.md).
