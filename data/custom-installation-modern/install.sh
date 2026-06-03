#!/bin/sh
#
# Wubi self-contained "diskimage" installer for modern Ubuntu (24.04 "Noble"
# and newer), which no longer ships ubiquity or the lupin loopback patches.
#
# This runs as a systemd oneshot service inside the booted live session (see
# wubi-install.service; staged by the casper-hook/24preseed override). Rather
# than driving a distribution installer, it performs the install itself:
#
#   1. mount the host (Windows) partition that holds the install
#   2. create & format a loopback root.disk on it
#   3. unpack the target squashfs layers (minimal + minimal.standard, i.e. the
#      installed system without the *.live overlay) into root.disk
#   4. install a kernel (offline, from the ISO pool) and rebuild the initramfs
#      with loop-root support so the system can boot from the loopback file
#   5. write fstab, locale, keyboard, timezone, hostname and create the user
#   6. write root.disk:/boot/grub/grub.cfg for loop boot; the existing wubildr /
#      wubildr.cfg chain (installed on the host by wubi.exe) loads it.
#
# Everything is logged to the host partition so the result can be inspected
# even if the GUI is unavailable.

set -x

# ---------------------------------------------------------------------------
# Configuration (written by the Windows-side wubi.exe; see backend.py)
# ---------------------------------------------------------------------------
CONF_DIR=/custom-installation
[ -f "$CONF_DIR/config" ] && . "$CONF_DIR/config"
[ -f "$CONF_DIR/runtime.conf" ] && . "$CONF_DIR/runtime.conf"

# Defaults / fallbacks (also make the script runnable for manual testing).
TARGET_DIR=${TARGET_DIR:-/ubuntu}
INSTALL_DIR=${INSTALL_DIR:-$TARGET_DIR/install}
ROOT_SIZE_MB=${ROOT_SIZE_MB:-18000}
SWAP_SIZE_MB=${SWAP_SIZE_MB:-0}
USERNAME=${USERNAME:-ubuntu}
USER_FULL_NAME=${USER_FULL_NAME:-Ubuntu}
PASSWORD=${PASSWORD:-ubuntu}
HOST_NAME=${HOST_NAME:-ubuntu}
LOCALE=${LOCALE:-en_US.UTF-8}
KEYBOARD_LAYOUT=${KEYBOARD_LAYOUT:-us}
KEYBOARD_VARIANT=${KEYBOARD_VARIANT:-}
TIMEZONE=${TIMEZONE:-Etc/UTC}
KERNEL_FLAVOUR=${KERNEL_FLAVOUR:-generic}
AUTO_REBOOT=${AUTO_REBOOT:-false}
# Space separated list of squashfs layers (overlay order). The *.live layer is
# intentionally excluded: it carries the live session / installer only.
SQUASH_LAYERS=${SQUASH_LAYERS:-"minimal.squashfs minimal.standard.squashfs"}

HOST=/host
TARGET=/target
LOG=/tmp/wubi-install.log

log() { echo "wubi: $*"; echo "wubi: $*" > /dev/console 2>/dev/null || true; }
fail() {
    log "FAILED: $*"
    mark_status failed "$1"
    # Surface the failure to the user (zenity if a desktop is up, else stderr).
    # The message text is set on the host side (copy_installation_files).
    [ -f "$CONF_DIR/hooks/failure-command.sh" ] && \
        sh "$CONF_DIR/hooks/failure-command.sh" 2>/dev/null || true
    finish 1
}

# Status markers + log copied to the host partition for offline inspection.
mark_status() {
    _st="$1"; _msg="$2"
    [ -d "$HOST$INSTALL_DIR" ] || return 0
    : > "$HOST$INSTALL_DIR/wubi-install.$_st" 2>/dev/null || true
    [ -n "$_msg" ] && echo "$_msg" > "$HOST$INSTALL_DIR/wubi-install.$_st" 2>/dev/null || true
}
save_log() {
    [ -d "$HOST$INSTALL_DIR" ] && cp -f "$LOG" "$HOST$INSTALL_DIR/wubi-install.log" 2>/dev/null || true
}

cleanup() {
    save_log
    for m in "$TARGET/cdrom" "$TARGET/dev/pts" "$TARGET/dev" "$TARGET/proc" "$TARGET/sys" "$TARGET/run"; do
        mountpoint -q "$m" 2>/dev/null && umount -lf "$m" 2>/dev/null || true
    done
    mountpoint -q "$TARGET" 2>/dev/null && umount -lf "$TARGET" 2>/dev/null || true
    [ -n "$LOOP" ] && losetup -d "$LOOP" 2>/dev/null || true
}

finish() {
    cleanup
    if [ "$1" = "0" ] && [ "$AUTO_REBOOT" = "true" ]; then
        log "install complete, rebooting"
        sync; reboot -f
    fi
    exit "${1:-0}"
}

# Send everything to the log as well as the console/journal.
exec > "$LOG" 2>&1

log "starting Wubi modern install $(date -u)"
log "config: TARGET_DIR=$TARGET_DIR ROOT_SIZE_MB=$ROOT_SIZE_MB SWAP_SIZE_MB=$SWAP_SIZE_MB USER=$USERNAME HOST_NAME=$HOST_NAME LOCALE=$LOCALE KB=$KEYBOARD_LAYOUT/$KEYBOARD_VARIANT TZ=$TIMEZONE"

# ---------------------------------------------------------------------------
# 1. Mount the host partition (where root.disk will live)
# ---------------------------------------------------------------------------
mkdir -p "$HOST"
if mountpoint -q /isodevice; then
    # the casper hook already moved the host partition to /isodevice; reuse it.
    mount -o bind /isodevice "$HOST"
elif [ -n "$HOST_DEV" ] && [ -b "$HOST_DEV" ]; then
    mount -t ntfs3 -o rw "$HOST_DEV" "$HOST" 2>/dev/null \
        || mount -t ntfs-3g -o rw "$HOST_DEV" "$HOST" 2>/dev/null \
        || mount "$HOST_DEV" "$HOST" \
        || fail "could not mount host device $HOST_DEV"
else
    fail "host partition not found (no /isodevice, no HOST_DEV)"
fi
log "host partition mounted at $HOST"
mark_status started

# Make sure the host is writable (iso-scan mounts rw, but be defensive).
if ! touch "$HOST/.wubi-write-test" 2>/dev/null; then
    mount -o remount,rw "$HOST" 2>/dev/null || true
    touch "$HOST/.wubi-write-test" 2>/dev/null || fail "host partition is read-only"
fi
rm -f "$HOST/.wubi-write-test"

# ---------------------------------------------------------------------------
# 2. Locate the ISO content (squashfs layers + package pool)
# ---------------------------------------------------------------------------
CDROM=
for c in /cdrom /run/live/medium /isodevice; do
    if [ -e "$c/casper/$(echo "$SQUASH_LAYERS" | cut -d' ' -f1)" ]; then
        CDROM="$c"; break
    fi
done
if [ -z "$CDROM" ]; then
    # Fall back to loop-mounting the ISO referenced by iso-scan.
    iso=$(awk '{for(i=1;i<=NF;i++) if($i ~ /^iso-scan\/filename=/){sub(/^iso-scan\/filename=/,"",$i);print $i}}' /proc/cmdline)
    if [ -n "$iso" ] && [ -f "$HOST$iso" ]; then
        mkdir -p /cdrom
        mount -o loop,ro "$HOST$iso" /cdrom && CDROM=/cdrom
    fi
fi
[ -n "$CDROM" ] || fail "could not locate ISO content (casper/*.squashfs)"
log "using ISO content at $CDROM"

# ---------------------------------------------------------------------------
# 3. Create & format the loopback root.disk
# ---------------------------------------------------------------------------
DISKS="$HOST$TARGET_DIR/disks"
mkdir -p "$DISKS" || fail "could not create $DISKS"
ROOTDISK="$DISKS/root.disk"

log "creating $ROOTDISK (${ROOT_SIZE_MB} MiB)"
rm -f "$ROOTDISK"
# truncate makes a sparse file; works on ntfs3/ntfs-3g and keeps the host
# footprint small until the data is actually written.
truncate -s "${ROOT_SIZE_MB}M" "$ROOTDISK" || fail "could not create root.disk"

LOOP=$(losetup -f --show "$ROOTDISK") || fail "losetup failed for root.disk"
log "root.disk attached to $LOOP"
mkfs.ext4 -F -q -L wubi-root "$LOOP" || fail "mkfs.ext4 failed"
ROOT_UUID=$(blkid -s UUID -o value "$LOOP")
log "root.disk ext4 UUID=$ROOT_UUID"

mkdir -p "$TARGET"
mount "$LOOP" "$TARGET" || fail "could not mount root.disk"

# Optional swap file living next to root.disk on the host partition.
if [ "${SWAP_SIZE_MB:-0}" -gt 0 ]; then
    log "creating swap.disk (${SWAP_SIZE_MB} MiB)"
    truncate -s "${SWAP_SIZE_MB}M" "$DISKS/swap.disk" && mkswap "$DISKS/swap.disk" || log "warning: swap creation failed"
fi

# ---------------------------------------------------------------------------
# 4. Unpack the target squashfs layers (overlay order) into root.disk
# ---------------------------------------------------------------------------
for layer in $SQUASH_LAYERS; do
    sq="$CDROM/casper/$layer"
    [ -f "$sq" ] || fail "missing squashfs layer $sq"
    log "unpacking $layer -> $TARGET"
    unsquashfs -f -d "$TARGET" "$sq" || fail "unsquashfs $layer failed"
done

# ---------------------------------------------------------------------------
# 5. Prepare chroot
# ---------------------------------------------------------------------------
cp -L /etc/resolv.conf "$TARGET/etc/resolv.conf" 2>/dev/null || true
mkdir -p "$TARGET/cdrom"
mount --bind /proc "$TARGET/proc"
mount --bind /sys "$TARGET/sys"
mount --bind /dev "$TARGET/dev"
mount --bind /dev/pts "$TARGET/dev/pts"
mount --bind /run "$TARGET/run"
mount --bind "$CDROM" "$TARGET/cdrom"

# Loop-boot support in the target initramfs. Two parts, both applied BEFORE the
# kernel postinst builds the initrd:
#
#   (a) an /etc/initramfs-tools build hook (wubi-loop) that re-applies the fix on
#       EVERY update-initramfs. /etc/initramfs-tools is config and is never
#       overwritten by package upgrades, so the loop fix survives kernel and
#       initramfs-tools updates - unlike a one-off edit of the stock script.
#   (b) a one-off edit of the stock /usr/share script, so the very first initrd
#       (built now by the kernel postinst) already has the fix even before the
#       hook is consulted.
#
# The fix itself: replace busybox's unreliable `mount -o loop` of root.disk with
# an explicit losetup + mount, and make sure the host (Windows) filesystem
# drivers (ntfs3, vfat) are present so the partition holding root.disk can be
# mounted at boot.
oldv='mount ${roflag} -o loop -t ${FSTYPE} ${LOOPFLAGS} "/host/${LOOP#/}" '
newv='loopdev=`losetup -f`; losetup ${loopdev} "/host/${LOOP#/}"; mount ${roflag} -t ${FSTYPE} ${LOOPFLAGS} ${loopdev} '

# (a) durable build hook
mkdir -p "$TARGET/etc/initramfs-tools/hooks"
cat > "$TARGET/etc/initramfs-tools/hooks/wubi-loop" <<'HOOK'
#!/bin/sh
# Wubi loopback-root support. Re-applied on every update-initramfs so it
# survives kernel / initramfs-tools package updates (see install.sh).
PREREQ=""
prereqs() { echo "$PREREQ"; }
case "$1" in
    prereqs) prereqs; exit 0 ;;
esac
. /usr/share/initramfs-tools/hook-functions

# Host (Windows) filesystem drivers, so the partition holding root.disk mounts.
manual_add_modules ntfs3
manual_add_modules vfat

# Reliable loop-mount of root.disk: losetup + mount instead of `mount -o loop`.
LOCAL="${DESTDIR}/scripts/local"
oldv='mount ${roflag} -o loop -t ${FSTYPE} ${LOOPFLAGS} "/host/${LOOP#/}" '
newv='loopdev=`losetup -f`; losetup ${loopdev} "/host/${LOOP#/}"; mount ${roflag} -t ${FSTYPE} ${LOOPFLAGS} ${loopdev} '
if [ -f "$LOCAL" ] && grep -qF "$oldv" "$LOCAL"; then
    sed -i "s%$oldv%$newv%g" "$LOCAL"
fi
exit 0
HOOK
chmod 0755 "$TARGET/etc/initramfs-tools/hooks/wubi-loop"
log "installed durable loop-boot initramfs hook"

# Keep ntfs3/vfat in the always-load module list too (also under /etc, durable).
for mod in ntfs3 vfat; do
    grep -qx "$mod" "$TARGET/etc/initramfs-tools/modules" 2>/dev/null \
        || echo "$mod" >> "$TARGET/etc/initramfs-tools/modules"
done

# (b) one-off edit of the stock script for the first build
LOCAL="$TARGET/usr/share/initramfs-tools/scripts/local"
if [ -f "$LOCAL" ] && grep -qF "$oldv" "$LOCAL"; then
    sed -i "s%$oldv%$newv%g" "$LOCAL"
    log "applied loop-remount patch to target initramfs"
fi

# ---------------------------------------------------------------------------
# 6. Install a kernel (offline, from the ISO pool) + rebuild initramfs
# ---------------------------------------------------------------------------
# The install squashfs layers are kernel-less; install a signed GA kernel from
# the ISO pool. dpkg -i is sufficient (kmod / linux-base / initramfs-tools are
# already in the squashfs). The linux-image postinst rebuilds the initramfs
# (now with loop-root support). Disable the update-grub kernel hook for the
# duration: there is no real boot device in the chroot and we write grub.cfg
# ourselves below.
# Pick a signed generic kernel image. The name must start with a digit right
# after "linux-image-" so meta/unsigned/nvidia packages are excluded. Either the
# GA or HWE image works; the modules package below is matched to whichever wins.
KIMG=$(find "$CDROM/pool" -name "linux-image-[0-9]*-${KERNEL_FLAVOUR}_*_amd64.deb" 2>/dev/null | sort -V | head -1)
[ -n "$KIMG" ] || fail "could not find a kernel image deb in $CDROM/pool"
KVER=$(echo "$KIMG" | sed -E 's@.*/linux-image-([0-9][^_]*)_.*@\1@')
# Match the modules package to that exact kernel version (avoids grabbing e.g.
# linux-modules-nvidia-*-generic, which would fail dpkg -i with unmet deps).
KMOD=$(find "$CDROM/pool" -name "linux-modules-${KVER}_*_amd64.deb" 2>/dev/null | sort -V | head -1)
[ -n "$KMOD" ] || fail "could not find linux-modules for $KVER in $CDROM/pool"
log "installing kernel $KVER ($KIMG, $KMOD)"

GRUBHOOK="$TARGET/etc/kernel/postinst.d/zz-update-grub"
[ -f "$GRUBHOOK" ] && mv "$GRUBHOOK" "$GRUBHOOK.wubi-disabled"

# reference the debs via the bind-mounted /cdrom inside the chroot
rel_kimg="/cdrom/${KIMG#$CDROM/}"
rel_kmod="/cdrom/${KMOD#$CDROM/}"
chroot "$TARGET" dpkg -i "$rel_kmod" "$rel_kimg" || fail "kernel dpkg -i failed"

[ -f "$GRUBHOOK.wubi-disabled" ] && mv "$GRUBHOOK.wubi-disabled" "$GRUBHOOK"

# Ensure the initramfs exists and carries loop-root support.
chroot "$TARGET" update-initramfs -u -k "$KVER" 2>/dev/null \
    || chroot "$TARGET" update-initramfs -c -k "$KVER" || fail "update-initramfs failed"

# Convenience symlinks (/vmlinuz, /initrd.img) like a normal Ubuntu install.
ln -sf "boot/vmlinuz-$KVER" "$TARGET/vmlinuz" 2>/dev/null || true
ln -sf "boot/initrd.img-$KVER" "$TARGET/initrd.img" 2>/dev/null || true

# ---------------------------------------------------------------------------
# 7. System configuration: fstab, locale, keyboard, timezone, hostname, user
# ---------------------------------------------------------------------------
HOST_UUID=$(blkid -s UUID -o value "$HOST_DEV" 2>/dev/null)
[ -z "$HOST_UUID" ] && HOST_UUID=$(findmnt -no UUID --target "$HOST" 2>/dev/null)

log "writing fstab (host UUID=$HOST_UUID)"
{
    echo "# /etc/fstab: generated by Wubi (loopback install)"
    echo "$TARGET_DIR/disks/root.disk  /      ext4  loop,errors=remount-ro  0  1"
    if [ -n "$HOST_UUID" ]; then
        echo "UUID=$HOST_UUID              /host  ntfs3 defaults,nofail,uid=0,gid=0  0  0"
    fi
    [ "${SWAP_SIZE_MB:-0}" -gt 0 ] && echo "$TARGET_DIR/disks/swap.disk  none   swap  loop,sw                 0  0"
    echo "proc                        /proc  proc  nodev,noexec,nosuid     0  0"
} > "$TARGET/etc/fstab"
mkdir -p "$TARGET/host"

log "configuring locale/keyboard/timezone/hostname"
echo "LANG=$LOCALE" > "$TARGET/etc/default/locale"
echo "$LOCALE UTF-8" >> "$TARGET/etc/locale.gen"
chroot "$TARGET" locale-gen "$LOCALE" 2>/dev/null || true

cat > "$TARGET/etc/default/keyboard" <<EOF
XKBMODEL="pc105"
XKBLAYOUT="$KEYBOARD_LAYOUT"
XKBVARIANT="$KEYBOARD_VARIANT"
XKBOPTIONS=""
BACKSPACE="guess"
EOF

ln -sf "/usr/share/zoneinfo/$TIMEZONE" "$TARGET/etc/localtime" 2>/dev/null || true
echo "$TIMEZONE" > "$TARGET/etc/timezone"

echo "$HOST_NAME" > "$TARGET/etc/hostname"
cat > "$TARGET/etc/hosts" <<EOF
127.0.0.1   localhost
127.0.1.1   $HOST_NAME
::1         localhost ip6-localhost ip6-loopback
ff02::1     ip6-allnodes
ff02::2     ip6-allrouters
EOF

log "creating user $USERNAME"
chroot "$TARGET" useradd -m -s /bin/bash -c "$USER_FULL_NAME" -G adm,sudo,cdrom,dip,plugdev,lpadmin "$USERNAME" 2>/dev/null \
    || log "warning: useradd returned non-zero (user may already exist)"
echo "$USERNAME:$PASSWORD" | chroot "$TARGET" chpasswd || fail "could not set password"

# ---------------------------------------------------------------------------
# 8. Bootloader: grub.cfg inside root.disk for loop boot
# ---------------------------------------------------------------------------
# The host-side wubildr / wubildr.cfg chain loopback-mounts root.disk and runs
# /boot/grub/grub.cfg from inside it (see data/wubildr.cfg). We provide that
# config; it boots the kernel with loop=<root.disk> and root=<host UUID> so the
# initramfs (patched above) mounts the host then loop-mounts the real root.
log "writing root.disk:/boot/grub/grub.cfg"
mkdir -p "$TARGET/boot/grub"
{
    echo "# Generated by Wubi (loopback boot)"
    echo "set timeout=5"
    echo "set default=0"
    echo "insmod part_gpt"
    echo "insmod part_msdos"
    echo "insmod ntfs"
    echo "insmod ext2"
    echo "insmod loopback"
    echo "search --no-floppy --set=diskroot -f /$TARGET_DIR/disks/root.disk"
    echo "probe --set=diskuuid -u \$diskroot"
    echo
    echo "menuentry \"Ubuntu (loopback)\" {"
    echo "    linux /boot/vmlinuz-$KVER root=UUID=\$diskuuid loop=$TARGET_DIR/disks/root.disk ro quiet splash"
    echo "    initrd /boot/initrd.img-$KVER"
    echo "}"
    echo "menuentry \"Ubuntu (loopback, recovery)\" {"
    echo "    linux /boot/vmlinuz-$KVER root=UUID=\$diskuuid loop=$TARGET_DIR/disks/root.disk ro recovery nomodeset"
    echo "    initrd /boot/initrd.img-$KVER"
    echo "}"
} > "$TARGET/boot/grub/grub.cfg"

log "install finished successfully"
mark_status done
finish 0
