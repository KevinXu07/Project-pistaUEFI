#!/usr/bin/env python3
"""Stage stock RMX5010 partition images as the pista capture directory.

The images come from the stock realme UI full OTA payload, so this replaces a
rooted capture for every static input; the live board FDT stays device-only.
Also carves the FDTs embedded in the xbl_config ELF for inspection.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

PARTITIONS = ('xbl_config', 'uefi', 'abl', 'boot', 'dtbo', 'pvmfw', 'splash')
OUTPUT = Path(__file__).resolve().parent.parent.parent / 'private' / 'pista-capture'
FDT_MAGIC = b'\xd0\x0d\xfe\xed'


def carve_fdts(data):
    hits = []
    offset = 0
    while True:
        offset = data.find(FDT_MAGIC, offset)
        if offset < 0:
            return hits
        if offset + 8 <= len(data):
            size = struct.unpack('>I', data[offset + 4:offset + 8])[0]
            if 0 < size <= len(data) - offset:
                hits.append((offset, size))
        offset += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--images', type=Path, required=True,
                    help='directory holding the partition images extracted from the OTA payload')
    ap.add_argument('--prefix', default='',
                    help='file name prefix of the images, e.g. "201_" for 201_uefi.img')
    ap.add_argument('--source', required=True,
                    help='OTA build recorded in the manifest, e.g. "realme UI 16.0.7.201"')
    ap.add_argument('--output', type=Path, default=OUTPUT)
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = {'device': 'rmx5010', 'soc': 'sun', 'source': f'{args.source} full OTA payload',
                'files': {}, 'xbl_config_fdts': []}
    for name in PARTITIONS:
        raw = (args.images / f'{args.prefix}{name}.img').read_bytes()
        (args.output / f'{name}.img').write_bytes(raw)
        manifest['files'][name + '.img'] = {
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        print(f'{name}: {len(raw)} bytes')
    data = (args.output / 'xbl_config.img').read_bytes()
    for index, (offset, size) in enumerate(carve_fdts(data)):
        target = args.output / f'xbl-fdt-{index}.dtb'
        target.write_bytes(data[offset:offset + size])
        manifest['xbl_config_fdts'].append({
            'offset': offset, 'bytes': size, 'file': target.name,
            'sha256': hashlib.sha256(data[offset:offset + size]).hexdigest()})
        print(f'xbl_config FDT {index}: offset 0x{offset:X}, {size} bytes')
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Staged {len(manifest["files"])} images, {len(manifest["xbl_config_fdts"])} FDTs -> {args.output}')


if __name__ == '__main__':
    main()
