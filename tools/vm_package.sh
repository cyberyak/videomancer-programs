#!/bin/bash
# Build a .vmprog package (all input configs) for one of our programs.
#
#   tools/vm_package.sh <program_dir> [--sign]
#
# Runs the SDK's build_programs.sh, patched on the fly to use the bundled
# Python 3.11 instead of the hard-coded /usr/bin/python3 (macOS 3.9, no
# Pillow). The SDK file itself is not modified.
#
# Unsigned by default: the firmware only trusts LZX's built-in keys and
# rejects invalid signatures, so a self-signed package is no better than an
# unsigned one. Unsigned packages need Developer Mode on the device.
# Output: out/<hardware>/<name>.vmprog
set -euo pipefail
source "$(dirname "$0")/env.sh"

dir="$(cd "$1" && pwd)"
name="$(basename "$dir")"
sign="${2:-}"
patched="$VM_ROOT/build/build_programs_local.sh"
mkdir -p "$VM_ROOT/build" "$VM_ROOT/out"

sed -e "s#^SCRIPT_DIR=.*#SCRIPT_DIR=\"$VM_SDK\"#" \
    -e 's#/usr/bin/python3#python3#g' \
    "$VM_SDK/build_programs.sh" > "$patched"

export VIDEOMANCER_PROGRAMS_DIR="$(dirname "$dir")/"
export VIDEOMANCER_BUILD_DIR="$VM_ROOT/build/vmprog/"
export VIDEOMANCER_OUT_DIR="$VM_ROOT/out/"
if [[ "$sign" != "--sign" ]]; then
    export VIDEOMANCER_ALLOW_UNSIGNED=1
    export VIDEOMANCER_KEYS_DIR="$VM_ROOT/build/no-keys/"
fi

log="$VM_ROOT/build/package_$name.log"
if bash "$patched" "$name" >"$log" 2>&1; then
    find "$VM_ROOT/out" -name "$name.vmprog" -exec ls -la {} \;
else
    echo "package build failed, see $log"
    sed 's/\x1b\[[0-9;]*m//g' "$log" | grep -v DEPRECATION | tail -25
    exit 1
fi
