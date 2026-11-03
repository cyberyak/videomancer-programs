#!/bin/bash
# Synthesize one Videomancer program for SD and/or HD and report resources.
#
#   tools/vm_build.sh <program_dir> [sd|hd|both]
#
# <program_dir> is a folder holding <name>.vhd + <name>.toml, e.g.
#   videomancer-sdk/programs/sabattier
#   videomancer-community-programs/programs/czartroniks/reflector
#   programs/cyberyak/kaleido
# Logs and bitstreams go to build/<name>/.
set -euo pipefail
source "$(dirname "$0")/env.sh"

dir="$(cd "$1" && pwd)"
name="$(basename "$dir")"
which="${2:-both}"
out="$VM_ROOT/build/$name"
mkdir -p "$out"

build() {  # config frequency
    local log="$out/$1.log"
    make -C "$VM_SDK/fpga" VIDEOMANCER_SDK_ROOT="$VM_SDK" PROJECT_ROOT="$dir/" \
        BUILD_ROOT="$out/" PROGRAM="$name" CONFIG="$1" DEVICE=hx4k PACKAGE=tq144 \
        FREQUENCY="$2" HARDWARE=rev_b CORE=yuv444_30b PLATFORM=ice40 >"$log" 2>&1 \
        || { echo "== $1: BUILD FAILED (see $log)"; tail -20 "$log"; return 1; }
    echo "== $1"
    grep -E "ICESTORM_LC:|ICESTORM_RAM:" "$log" | sed 's/^Info: *//' | sort -u
    # Last report is the post-route figure.
    grep "Max frequency for clock" "$log" | tail -1 | sed 's/^[A-Za-z]*: *//'
}

[[ "$which" == sd || "$which" == both ]] && build sd_analog 27
[[ "$which" == hd || "$which" == both ]] && build hd_hdmi 74.25
echo "(stock passthru: ~1600 LC, 0 EBR; the ADV7611_CLK 'FAIL at 74.25 MHz' line also appears for passthru)"
