# WubiUEFI

Wubi is the **Windows Ubuntu Installer**. It installs Ubuntu inside a single
file on an existing Windows (NTFS) partition, so no CD burning or repartitioning
is required, yet the result is a real dual-boot setup. WubiUEFI is the fork that
adds UEFI/Secure Boot support and support for recent Ubuntu releases.

For background see the wiki: https://github.com/hakuna-m/wubiuefi/wiki

> **Status of this branch.** The codebase has been migrated to **Python 3**, the
> Windows executable is now produced with **PyInstaller**, and support for
> **modern Ubuntu (24.04 "Noble" and newer)** has been added via a self-contained
> installer (the old releases that shipped `ubiquity`/`lupin` were the last that
> could be installed the legacy way). See [Ubuntu version support](#ubuntu-version-support).

## Quick start (build `wubi.exe`)

The build runs on **Linux** (or **WSL** on Windows) using Wine to host a Windows
Python; it cross-produces a Windows `wubi.exe`. On a fresh Ubuntu/Debian machine:

```sh
# 1. install the build dependencies (see docs/BUILD.md for the full list)
sudo dpkg --add-architecture i386 && sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    make wine wine32 wine64 xvfb \
    grub-pc-bin grub-efi-amd64-bin grub-efi-ia32-bin \
    shim-signed sbsigntool openssl \
    gcc-mingw-w64 binutils-mingw-w64 gettext zip wget xz-utils

# 2. build (first run downloads the Wine Python 3.12 + PyInstaller automatically)
WINEARCH=win32 WINEDLLOVERRIDES="mscoree=d;mshtml=d" make build
```

The result is `build/wubi.exe`. For the complete guide (prerequisites, native
Windows notes, troubleshooting, downloading a prebuilt artifact from CI) see
**[docs/BUILD.md](docs/BUILD.md)**.

## Make targets

| Command            | Description                                                                                  |
|--------------------|----------------------------------------------------------------------------------------------|
| `make` / `make build` | Build `build/wubi.exe` (freezes the app with PyInstaller; builds the boot loaders, `cpuid.dll` and translations as needed). |
| `make runpy`       | Run Wubi from source under Wine (no freezing).                                               |
| `make runbin`      | Build and run the packaged `wubi.exe` under Wine.                                            |
| `make wubizip`     | Produce a zip with the staged tree + Wine Python for debugging.                              |
| `make unittest`    | Run the unit tests under Wine.                                                               |
| `make pot` / `make update-po` | Regenerate / merge the gettext translation templates.                              |
| `make check_wine`  | Create the Wine prefix and install the Windows Python 3.12 + PyInstaller.                    |
| `make check_winboot` | Install/locate the GRUB + shim + `sbsign` tooling and generate dummy Secure Boot keys.     |
| `make winboot2`    | Build the (UEFI) boot loader files.                                                          |
| `make clean` / `make distclean` | Remove build output / build output + the Wine prefix.                           |

CI also builds `wubi.exe` on every push to `modernize` and on manual dispatch —
download the `wubi-exe` artifact from the **Actions** tab
(`.github/workflows/build-windows.yml`).

## Code overview

* `/src/wubi` — the main application, split into a **backend** and **frontend**
  that each run in their own thread and communicate through a tasklist. Backends
  and frontends are platform-specific (only Windows is implemented).
  * `backends/common` — shared install logic, ISO/metadata handling, downloader,
    tasklist, and the **distro providers** (`providers.py`, see
    [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)).
  * `backends/win32` — Windows-specific bits (registry, drives, EFI/BCD, memory,
    BitLocker detection, virtual disk).
  * `frontends/win32` — the GUI pages.
* `/src/winui` — thin `ctypes` wrapper around the native Win32 GUI.
* `/src/openpgp`, `/src/urlgrabber`, `/src/bittorrent` — vendored libraries
  (GPG signature verification, HTTP download, optional BitTorrent).
* `/data` — branding, preseed/installer assets, boot configs, signing keys.
* `/po` — translations.
* `/blobs` — prebuilt runtime binaries (7-Zip, `resize2fs`, …) staged into `bin/`.
* `wubi.spec` — the PyInstaller spec used to freeze the app.

(The old `src/pylauncher`/`src/pypack` freezer has been removed in favour of
PyInstaller.)

## Ubuntu version support

Wubi reboots into the live ISO and installs Ubuntu into a loopback file
(`root.disk`). How that install is driven depends on the release, selected per
entry in `data/isolist.ini` via `provider=`:

* **Legacy (≤ 22.04, `provider=ubuntu`)** — driven by the distribution's
  `ubiquity` installer through a debconf preseed plus the `lupin` loopback
  patches. Unchanged.
* **Modern (24.04+, `provider=ubuntu-modern`)** — 24.04 dropped `ubiquity` and
  `lupin` for the subiquity installer, which cannot install into a loopback
  file. Wubi therefore performs the install itself: a self-contained script runs
  in the live session, unpacks the layered squashfs (`minimal` +
  `minimal.standard`, excluding the live-only layer) into `root.disk`, configures
  the system and a loop-boot initramfs, and writes the boot config that the
  existing `wubildr` chain loads. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Installation types

The installation page offers three installation types (selectable in the GUI, or
non-interactively with `--install-mode`):

* **Wubi — Ubuntu inside Windows** (`--install-mode=wubi`, the default). The
  classic Wubi experience: Ubuntu lives in a loopback file (`root.disk`) on an
  existing Windows (NTFS) partition. No repartitioning, and it can be removed
  cleanly from Windows. This is the only mode that uses the **Installation size**
  selector.
* **Install alongside Windows — automated** (`--install-mode=autoinstall`). A
  real-partition dual boot with **no manual steps in the Ubuntu installer**. Wubi
  stages the ISO and a [subiquity autoinstall](https://canonical-subiquity.readthedocs-hosted.com/en/latest/reference/autoinstall-reference.html)
  configuration (`user-data`/`meta-data`) and reboots into the live session,
  which automatically resizes the largest Windows partition (`storage.layout.name:
  alongside`) and installs Ubuntu on its own partition.
* **Launch Ubuntu installer manually — guided** (`--install-mode=guided`, alias
  `--dualboot`). Wubi prepares the boot environment and reboots into the stock
  Ubuntu live installer so you can partition the disk yourself.

> **Real partitioning can cause data loss if interrupted.** The two
> real-partition modes show a confirmation warning; back up before using them.

## What Wubi does

* Gathers host system info and checks the minimum installation requirements.
* Collects user choices through the GUI (you can **browse for a local ISO** and
  pick the **installation type**, see above).
* Detects **BitLocker** on the target/system drive and warns before changing the
  boot configuration.
* Finds a local ISO/CD or downloads one over HTTP (BitTorrent is opt-in via
  `--bittorrent`); verifies checksums and the GPG signature.
* Adds a boot entry to the Windows boot configuration (BCD/EFI), with **Secure
  Boot** support via a signed shim + GRUB.
* For a Wubi install, allocates the virtual disk and prepares the install; the
  actual installation completes after rebooting into Linux. For the
  real-partition modes, stages the ISO (plus an autoinstall config in automated
  mode) and hands off to the Ubuntu installer after reboot.

## Customization

* Edit the files in `data/` and rebuild.
* Provide an ISO whose `.disk/info` is formatted like Ubuntu's, and a webserver
  with the metalink, checksums and signatures.
* Add your signing key to `data/trustedkeys.gpg`.
* Replace the generated dummy keys in `.key/` with your own Secure Boot keys.
* The Linux side must be able to boot/reboot off a loop file and accept the
  special boot parameters (legacy path), or match a supported layered-squashfs
  layout (modern path).

## License

GPL v2. See [LICENSE](./LICENSE).
