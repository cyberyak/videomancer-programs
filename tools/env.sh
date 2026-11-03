# Source me: puts the SDK's OSS CAD Suite (ghdl, yosys, nextpnr, python3.11) on PATH.
VM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export VM_ROOT
export VM_SDK="$VM_ROOT/videomancer-sdk"
source "$VM_SDK/build/oss-cad-suite/environment" >/dev/null 2>&1
# The bundled Python loads old .egg packages and prints a DEPRECATION line per egg.
export PYTHONWARNINGS=ignore
