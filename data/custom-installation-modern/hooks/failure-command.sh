#!/bin/sh
# Shown by install.sh when the modern diskimage install fails. The msg= line is
# rewritten at build time by the Windows-side backend (copy_installation_files)
# to point at the saved logs, mirroring the legacy custom-installation hook.
msg="The installation failed. Logs have been saved in the install directory."

if [ -n "$DISPLAY" ] && command -v zenity >/dev/null 2>&1; then
    zenity --error --no-wrap --text "$msg" 2>/dev/null || true
else
    echo "$msg" >&2
fi
