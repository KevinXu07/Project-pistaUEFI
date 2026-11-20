#!/usr/bin/env python3
"""Extract FFS modules from a Qualcomm UEFI image into file-<GUID> directories.

Walks every firmware volume found in the image (top level plus nested
FV_IMAGE sections), follows GUIDed LZMA and uncompressed section wrappers, and
writes each FFS file's sections as ui.ui, pe32.pe, te.te, depex.dxe.depex.
The layout matches what prepare_piano.py consumes. Host only, no device access.
"""
import argparse
import json
import lzma
import struct
import uuid
from pathlib import Path

FV_SIGNATURE = b'_FVH'
LZMA_GUID = uuid.UUID('EE4E5898-3914-4259-9D6E-DC7BD79403CF')
SECTION_NAMES = {0x01: 'COMPRESSION', 0x02: 'GUID_DEFINED', 0x10: 'PE32', 0x12: 'TE',
                 0x13: 'DEPEX', 0x14: 'VERSION', 0x15: 'UI', 0x17: 'FV_IMAGE', 0x19: 'RAW'}
FFS_PAD_TYPE = 0xF0


def u24(data, offset):
    return int.from_bytes(data[offset:offset + 3], 'little')


def split_sections(data):
    sections = []
    pos = 0
    while pos + 4 <= len(data):
        size = u24(data, pos)
        section_type = data[pos + 3]
        if size < 4 or pos + size > len(data):
            break
        sections.append((section_type, data[pos:pos + size]))
        pos = (pos + size + 3) & ~3
    return sections


def decode_sections(sections, depth=0):
    """Yield (kind, payload) leaves, expanding compression and GUIDed wrappers."""
    for section_type, blob in sections:
        if section_type == 0x01:  # EFI_SECTION_COMPRESSION
            uncompressed_length = struct.unpack_from('<I', blob, 4)[0]
            compression = blob[8]
            payload = blob[9:]
            if compression == 0:
                inner = payload
            elif compression == 2:
                inner = lzma.decompress(payload, format=lzma.FORMAT_ALONE)
            else:
                raise ValueError(f'Unsupported compression type {compression}')
            if len(inner) != uncompressed_length:
                raise ValueError('Decompressed size mismatch')
            yield from decode_sections(split_sections(inner), depth + 1)
        elif section_type == 0x02:  # EFI_SECTION_GUID_DEFINED
            definition = uuid.UUID(bytes_le=blob[4:20])
            data_offset, attributes = struct.unpack_from('<HH', blob, 20)
            payload = blob[data_offset:]
            if definition == LZMA_GUID and attributes & 0x01:  # PROCESSING_REQUIRED
                inner = lzma.decompress(payload, format=lzma.FORMAT_ALONE)
                yield from decode_sections(split_sections(inner), depth + 1)
            elif definition == LZMA_GUID:
                yield ('RAW_GUID_' + definition.hex, payload)
            else:
                yield ('RAW_GUID_' + definition.hex, payload)
        else:
            yield (SECTION_NAMES.get(section_type, f'TYPE_{section_type:02X}'), blob[4:])


def walk_fv(data, base, on_file, depth=0):
    """Walk one FV; call on_file(name, ftype, sections, depth) per FFS file."""
    if base + 56 > len(data) or data[base + 40:base + 44] != FV_SIGNATURE:
        return 0
    length = struct.unpack_from('<Q', data, base + 32)[0]
    header_length = struct.unpack_from('<H', data, base + 48)[0]
    if length <= header_length or base + length > len(data):
        return 0
    pos = base + header_length
    end = base + length
    count = 0
    while pos + 24 <= end:
        name = uuid.UUID(bytes_le=data[pos:pos + 16])
        ftype = data[pos + 18]
        size = u24(data, pos + 20)
        if ftype == FFS_PAD_TYPE and size >= 24:
            pos = (pos + size + 7) & ~7
            continue
        if size < 24 or pos + size > end or name.int == 0 or ftype == 0xFF:
            break
        section_pos = pos + 24
        sections = []
        while section_pos + 4 <= pos + size:
            section_size = u24(data, section_pos)
            if section_size < 4 or section_pos + section_size > pos + size:
                break
            sections.append((data[section_pos + 3], data[section_pos:section_pos + section_size]))
            section_pos = (section_pos + section_size + 3) & ~3
        count += on_file(name, ftype, sections, depth) or 0
        pos = (pos + size + 7) & ~7
    return count


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('image', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--inventory-name', default='inventory.json')
    args = ap.parse_args()
    data = args.image.read_bytes()
    args.output.mkdir(parents=True, exist_ok=True)
    inventory = []
    saved = set()

    def on_file(name, ftype, sections, depth):
        nonlocal inventory
        try:
            leaves = list(decode_sections(sections, depth))
        except (lzma.LZMAError, ValueError) as exc:
            print(f'  file-{name} type 0x{ftype:02X}: sections not decoded ({exc})')
            return 0
        nested = [payload for kind, payload in leaves if kind == 'FV_IMAGE']
        kinds = {kind for kind, _ in leaves}
        if not kinds & {'PE32', 'TE', 'UI'}:
            extracted = 0
            for blob in nested:
                extracted += walk_fv(blob, 0, on_file, depth + 1)
            return extracted
        key = (name, ftype, tuple(sorted(k for k, _ in leaves)))
        if key in saved:
            return 0
        saved.add(key)
        module_dir = args.output / f'file-{name}'
        module_dir.mkdir(exist_ok=True)
        record = {'guid': str(name), 'type': f'0x{ftype:02X}', 'sections': {}}
        for kind, payload in leaves:
            if kind in ('PE32', 'TE', 'UI', 'DEPEX', 'VERSION', 'RAW'):
                suffix = {'PE32': 'pe', 'TE': 'te', 'UI': 'ui',
                          'DEPEX': 'dxe.depex', 'VERSION': 'version', 'RAW': 'raw'}[kind]
                target = module_dir / f'{kind.lower()}.{suffix}'
                target.write_bytes(payload)
                record['sections'][kind] = {'file': target.name, 'bytes': len(payload)}
        for blob in nested:
            walk_fv(blob, 0, on_file, depth + 1)
        inventory.append(record)
        return 1

    offset = 0
    volumes = []
    while True:
        index = data.find(FV_SIGNATURE, offset)
        if index < 0:
            break
        if index >= 40:
            volumes.append(index - 40)
        offset = index + 1
    for base in sorted(set(volumes)):
        before = len(inventory)
        found = walk_fv(data, base, on_file)
        if found:
            print(f'FV at 0x{base:X}: {found} modules')
    (args.output / args.inventory_name).write_text(json.dumps(inventory, indent=2) + '\n')
    names = {}
    for record in inventory:
        ui = args.output / f'file-{record["guid"]}' / 'ui.ui'
        if ui.exists():
            names[ui.read_bytes().decode('utf-16-le', errors='replace').strip('\0')] = record['guid']
    print(f'{len(inventory)} modules, {len(names)} with UI names')
    for name in sorted(names):
        print(f'  {name} = {names[name]}')


if __name__ == '__main__':
    main()
