#!/bin/bash
# Launch the SDK's GHDL simulator window with logging: everything it prints,
# plus a native crash trace (faulthandler), goes to build/ghdl_gui.log.
#   tools/sim_gui.sh [programs_dir]     (default: programs/cyberyak)
source "$(dirname "$0")/env.sh"
log="$VM_ROOT/build/ghdl_gui.log"
mkdir -p "$VM_ROOT/build"
echo "---- $(date) ----" >> "$log"
export LZX_VIT_PROGRAMS_DIR="${1:-$VM_ROOT/programs/cyberyak}"
export LZX_VIT_TEST_IMAGES_DIR="$VM_ROOT/test-images"
python3 -X faulthandler "$VM_SDK/tools/vhdl-image-tester/run.py" 2>&1 | grep --line-buffered -v DEPRECATION | tee -a "$log"
echo "exit status ${PIPESTATUS[0]} (log: $log)" | tee -a "$log"
