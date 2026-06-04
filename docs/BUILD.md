# Building WubiUEFI

This document explains how to build `wubi.exe` from source.

## How the build works

`wubi.exe` is a Python 3 application frozen with **PyInstaller** into a **one-dir
bundle** (`wubi.exe` next to an `_internal/` folder of DLLs and resources). A
one-dir bundle is used rather than a single self-extracting exe because the
latter is a frequent antivirus false-positive trigger. Because the target is
Windows but the supporting boot tooling
(GRUB, shim, `sbsign`, `mingw`) is Linux-native, the build is designed to run on
**Linux** (or **WSL**) and uses **Wine** to host a Windows Python 3.12:

1. `make check_wine` creates a 32-bit Wine prefix under `./wine/` and installs a
   Windows Python 3.12 (from the `pythonx86` NuGet package) plus `pip`,
   `pyinstaller` and `pycryptodome`. This runs automatically and needs network
   access; nothing is installed system-wide.
2. `make check_winboot` locates the GRUB/shim/`sbsign` tooling and generates a
   throwaway Secure Boot key pair under `./.key/` (replace it with your own for
   production).
3. `make winboot2` builds the UEFI boot loader files, `make cpuid` builds
   `cpuid.dll` with mingw, and `make translations` compiles the `.mo` files.
4. `wubi-pre-build` stages everything into `build/wubi/` (`lib/` = app code +
   `version.py`; `data/ bin/ winboot/ translations/` = resources).
5. PyInstaller freezes `build/wubi/lib/main.py` per `wubi.spec` into the one-dir
   bundle `build/dist/wubi/` (launcher: `build/dist/wubi/wubi.exe`).

## Prerequisites (Ubuntu/Debian, including WSL)

```sh
sudo dpkg --add-architecture i386
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    make wine wine32 wine64 xvfb \
    grub-pc-bin grub-efi-amd64-bin grub-efi-ia32-bin \
    shim-signed sbsigntool openssl \
    gcc-mingw-w64 binutils-mingw-w64 \
    gettext zip wget xz-utils
```

Notes:
* **32-bit matters.** The Wine Python is 32-bit, so the Wine prefix must be
  `win32`. `wine32:i386` (pulled in by `wine32`) is required.
* The first build downloads the Wine Python + PyInstaller, so it is slower and
  needs internet access.

## Building

```sh
WINEARCH=win32 WINEDLLOVERRIDES="mscoree=d;mshtml=d" make build
```

* `WINEARCH=win32` — create a 32-bit prefix to match the 32-bit Wine Python.
* `WINEDLLOVERRIDES="mscoree=d;mshtml=d"` — disable Wine's interactive
  Mono/Gecko install prompts (they otherwise block a headless build).

Output: the one-dir bundle **`build/dist/wubi/`** (run **`build/dist/wubi/wubi.exe`**).
Distribute the whole `wubi/` folder, not just the exe.

Useful follow-ups:
* `make runbin` — build and launch the frozen exe under Wine.
* `make runpy` — run from source under Wine (faster iteration, no freezing).
* `make unittest` — run the unit tests under Wine.
* `make clean` / `make distclean` — remove build output / output + Wine prefix.

If a build wedges, run it under a virtual X server so Wine's first-run setup can
proceed headlessly:

```sh
xvfb-run -a env WINEARCH=win32 WINEDLLOVERRIDES="mscoree=d;mshtml=d" make build
```

## Continuous integration / prebuilt binary

`.github/workflows/build-windows.yml` runs exactly this build on a Linux runner
for every push to `modernize` and on manual dispatch (Actions → "Build wubi.exe"
→ "Run workflow"). Download the resulting **`wubi-exe`** artifact from the run
page — this is the easiest way to get a binary without a local toolchain.

## Building on native Windows (advanced / partial)

The PyInstaller *freeze* can run on Windows, but the boot loaders (`winboot/`),
`cpuid.dll` and compiled translations are produced by Linux tools and are **not**
in the repo, so a fully functional installer is hard to produce on Windows alone.

If you still want the GUI executable on Windows:

1. Install **32-bit Python 3.12**.
2. `pip install pyinstaller pycryptodome`
3. Stage the tree `wubi.spec` expects:
   ```
   build\wubi\lib\main.py                                   (from src\main.py)
   build\wubi\lib\version.py                                (version="…"; revision=0; application_name="wubi")
   build\wubi\lib\{wubi,winui,openpgp,bittorrent,urlgrabber}  (from src\)
   build\wubi\data                                          (from data\)
   build\wubi\bin                                           (from blobs\  + cpuid.dll)
   build\wubi\winboot                                       (Linux-built boot loaders)
   build\wubi\translations                                  (compiled .mo files)
   ```
   Obtain `winboot/`, `bin/cpuid.dll` and `translations/` from a Linux/WSL build
   (or a CI artifact) and copy them in.
4. `pyinstaller --noconfirm --clean --distpath build\dist wubi.spec` →
   `build\dist\wubi\wubi.exe`.

This yields a runnable GUI (enough to exercise the UI), but a real install needs
the Linux-built boot artifacts above.

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `WINEARCH set to win32 but '…/wine' is a 64-bit installation` | The prefix was created 64-bit. `make distclean` (or `rm -rf wine`) and rebuild with `WINEARCH=win32`. |
| Build hangs early / forever | Wine's Mono/Gecko prompt. Set `WINEDLLOVERRIDES="mscoree=d;mshtml=d"` and run under `xvfb-run -a`. |
| `wine32` not installable | Run `sudo dpkg --add-architecture i386 && sudo apt-get update` first. |
| `i686-w64-mingw32-gcc: not found` (cpuid) | Install `gcc-mingw-w64`. |
| `grub-mkimage: … i386-efi/… No such file` | Install `grub-efi-ia32-bin` (and `grub-efi-amd64-bin`, `grub-pc-bin`). |
| Secure Boot signing fails | Install `sbsigntool`; `make check_winboot` will generate dummy keys under `.key/`. |
