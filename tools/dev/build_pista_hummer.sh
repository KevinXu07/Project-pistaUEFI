#!/usr/bin/env bash
# Builds the tree prepared by tools/dev/port_pista_hummer.py into a boot image
# for `fastboot boot`: artifacts/pista-hummer/pista-<TARGET>.img.
# Usage: build_pista_hummer.sh [DEBUG|RELEASE] [tree]. Host-only.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
target="${1:-DEBUG}"
ws="${2:-$HOME/hummer-uefi}"
tools="${SUNUEFI_TOOLCHAIN_ROOT:-/usr}"
export MAKEFLAGS="${MAKEFLAGS:--j8}"
export PATH="$root/.venv/bin:$tools/bin:$PATH"
export CLANG_BIN="$tools/bin/" IASL_PREFIX="$tools/bin/" NASM_PREFIX="$tools/bin/"
export PYTHON_COMMAND="$root/.venv/bin/python"
export WORKSPACE="$ws"
export EDK_TOOLS_PATH="$ws/Mu_Basecore/BaseTools"
export PACKAGES_PATH="$ws/Platforms/Realme:$ws/Common/Mu:$ws/Common/Mu_OEM_Sample:$ws/Mu_Basecore:$ws/Silicon/Qualcomm:$ws/Silicon/Silicium"
cd "$ws"
test -f Platforms/Realme/pistaPkg/pista.dsc || { echo "Run tools/dev/port_pista_hummer.py first" >&2; exit 1; }
# Resources/Scripts/build_font.py needs Pillow; any host Python with it works.
test -f Build/font-cache/Inconsolata.bin || { echo "Run Resources/Scripts/build_font.py in $ws first" >&2; exit 1; }
# The tree ships without the Brotli sources; its firmware volume uses LZMA.
make -s -C "$EDK_TOOLS_PATH/Source/C" -j8 \
  APPLICATIONS="VfrCompile EfiRom GenFfs GenFv GenFw GenSec GenCrc32 LzmaCompress TianoCompress VolInfo DevicePath GenStm" > /dev/null
# The tree ships without BaseTools/Conf; its BaseTools equal those of the Mu
# Basecore that Mu-Silicium pins, so the templates come from there.
conf="$root/upstream/Mu-Silicium/Mu_Basecore/BaseTools/Conf"
mkdir -p Conf
for c in target tools_def build_rule; do
  cmp -s "$conf/$c.template" "Conf/$c.txt" || cp "$conf/$c.template" "Conf/$c.txt"
done
export PATH="$EDK_TOOLS_PATH/Source/C/bin:$EDK_TOOLS_PATH/BinWrappers/PosixLike:$PATH"
export PYTHONPATH="$EDK_TOOLS_PATH/Source/Python${PYTHONPATH:+:$PYTHONPATH}"
"$PYTHON_COMMAND" "$EDK_TOOLS_PATH/Source/Python/build/build.py" \
  -a AARCH64 -b "$target" -t CLANGPDB -p pistaPkg/pista.dsc -n 8 \
  -D ENABLE_SECUREBOOT=0 -D FD_BASE=0xA7100000 -D FD_SIZE=0x300000 -D FD_BLOCKS=0x300 \
  -D DEVICE_MODEL=0 -D MEMORY_PROTECTION=TRUE -D SHIP_MODE=FALSE -D BUILDID_STRING=pista
make -s -C BootShim REQUIRES_KERNEL_HEADER=1 FD_BASE=0xA7100000 FD_SIZE=0x300000 \
  OBJCOPY="$tools/bin/aarch64-linux-gnu-objcopy"
# Same layout as hummer's build_uefi.py: BootShim + FD, gzip, board DTB appended.
fv="Build/pistaPkg/${target}_CLANGPDB/FV"
cat BootShim/BootShim.bin "$fv/PISTA_UEFI.fd" | gzip -9 -n > "$fv/PISTA_UEFI.fd-bootshim.gz"
cat "$fv/PISTA_UEFI.fd-bootshim.gz" Resources/DTBs/pista.dtb > Build/pistaPkg/pista-kernel
mkdir -p "$root/artifacts/pista-hummer"
"$PYTHON_COMMAND" Resources/Scripts/mkbootimg.py --kernel Build/pistaPkg/pista-kernel \
  --ramdisk Resources/ramdisk --header_version 4 -o "$root/artifacts/pista-hummer/pista-$target.img"
ls -la "$root/artifacts/pista-hummer/pista-$target.img"
