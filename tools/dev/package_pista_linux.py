#!/usr/bin/env python3
"""Pack a kernel, pista's RAM initramfs and its DTB into a LinuxRamBoot payload.

package_stage0.py --profile pista --linux-payload puts it after the firmware
inside the boot image's kernel; LinuxRamBoot finds it there, checks the
SHA-256 of each part and starts the kernel's EFI stub. Host-only.
"""
import argparse
import gzip
import hashlib
import io
import lzma
from pathlib import Path
import struct
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
INIT = ROOT / 'linux' / 'initramfs' / 'pista-ram' / 'init'
# LINUX_RAM_HEADER_V2 in uefi/components/linux-loader/LinuxRamBoot.c.
HEADER = struct.Struct('<16sIIQQ32s32sQ32s')
MAX_KERNEL, MAX_INITRD, MAX_DTB = 0x4000000, 0x2000000, 0x200000


def sha256(data):
    return hashlib.sha256(data).digest()


def deb_member(deb, prefix):
    """Return the named member of a Debian package (an ar archive)."""
    if deb[:8] != b'!<arch>\n':
        raise SystemExit('Not a Debian package')
    offset = 8
    while offset + 60 <= len(deb):
        name = deb[offset:offset + 16].decode().strip().rstrip('/')
        size = int(deb[offset + 48:offset + 58].decode().strip())
        body = deb[offset + 60:offset + 60 + size]
        if name.startswith(prefix):
            return name, body
        offset += 60 + size + (size & 1)
    raise SystemExit(f'Debian package has no {prefix} member')


def busybox_from_deb(path):
    name, data = deb_member(path.read_bytes(), 'data.tar')
    if name.endswith('.xz'):
        data = lzma.decompress(data)
    elif name.endswith('.gz'):
        data = gzip.decompress(data)
    elif not name.endswith('.tar'):
        raise SystemExit(f'Unsupported package compression: {name}')
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        # Debian moved the binary from /bin to /usr/bin.
        member = next((m for m in archive.getmembers()
                       if m.isfile() and m.name.lstrip('./') in ('bin/busybox', 'usr/bin/busybox')), None)
        if member is None:
            raise SystemExit('Package has no busybox binary')
        binary = archive.extractfile(member).read()
    # Static AArch64 ELF: machine 183 and no PT_INTERP program header.
    if binary[:4] != b'\x7fELF' or binary[4] != 2 or struct.unpack_from('<H', binary, 18)[0] != 183:
        raise SystemExit('busybox is not an AArch64 ELF')
    phoff, = struct.unpack_from('<Q', binary, 32)
    phentsize, phnum = struct.unpack_from('<HH', binary, 54)
    if any(struct.unpack_from('<I', binary, phoff + i * phentsize)[0] == 3 for i in range(phnum)):
        raise SystemExit('busybox is dynamically linked; use busybox-static')
    return binary


def cpio_newc(entries):
    """Build a newc cpio archive from (name, mode, data, rdev_major, rdev_minor)."""
    out = bytearray()
    for inode, (name, mode, data, major, minor) in enumerate(entries + [('TRAILER!!!', 0, b'', 0, 0)], 1):
        encoded = name.encode() + b'\0'
        fields = (inode, mode, 0, 0, 1, 0, len(data), 0, 0, major, minor, len(encoded), 0)
        out += b'070701' + b''.join(b'%08X' % value for value in fields) + encoded
        out += b'\0' * (-len(out) % 4) + data
        out += b'\0' * (-len(out) % 4)
    return bytes(out)


def initramfs(busybox):
    init = INIT.read_bytes()
    if b'\r' in init:
        raise SystemExit(f'{INIT} has CRLF line endings')
    directory, regular, char = 0o040755, 0o100755, 0o020600
    entries = [(name, directory, b'', 0, 0) for name in ('bin', 'dev', 'proc', 'sys', 'tmp')]
    entries += [('dev/console', char, b'', 5, 1), ('init', regular, init, 0, 0),
                ('bin/busybox', regular, busybox, 0, 0)]
    return gzip.compress(cpio_newc(entries), mtime=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True, help='arch/arm64/boot/Image')
    parser.add_argument('--dtb', type=Path, required=True, help='sm8750-realme-pista.dtb')
    parser.add_argument('--busybox-deb', type=Path, required=True, help='busybox-static arm64 .deb')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts' / 'pista' / 'linux-payload.bin')
    args = parser.parse_args()

    kernel, dtb = args.kernel.read_bytes(), args.dtb.read_bytes()
    if kernel[:2] != b'MZ' or kernel[0x38:0x3C] != b'ARM\x64':
        raise SystemExit('Kernel is not an ARM64 Image with an EFI stub')
    if dtb[:4] != b'\xd0\x0d\xfe\xed' or struct.unpack_from('>I', dtb, 4)[0] != len(dtb):
        raise SystemExit('DTB is not a complete flattened device tree')
    initrd = initramfs(busybox_from_deb(args.busybox_deb))
    for label, data, limit in (('kernel', kernel, MAX_KERNEL), ('initramfs', initrd, MAX_INITRD), ('DTB', dtb, MAX_DTB)):
        if len(data) > limit:
            sys.exit(f'{label} is {len(data)} bytes; LinuxRamBoot accepts at most {limit}')
    header = HEADER.pack(b'SUNUEFI-LINUXv2\0', 2, HEADER.size, len(kernel), len(initrd),
                         sha256(kernel), sha256(initrd), len(dtb), sha256(dtb))
    payload = header + kernel + initrd + dtb
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(payload)
    print(f'{args.output}: {len(payload)} bytes (kernel {len(kernel)}, initramfs {len(initrd)}, DTB {len(dtb)})')
    print(f'sha256 {hashlib.sha256(payload).hexdigest()}')


if __name__ == '__main__':
    main()
