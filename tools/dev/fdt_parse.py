#!/usr/bin/env python3
"""Minimal FDT (flat device tree) parser for host-side generation work.

parse_fdt() returns {path: {property: bytes}} with paths like '/soc/memorymap'.
No dependencies; the parser only reads, it never rewrites blobs.
"""
import struct

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def _pad4(offset):
    return (offset + 3) & ~3


def _string(data, offset):
    end = data.index(b'\0', offset)
    return data[offset:end].decode(), end + 1


def parse_fdt(data):
    if data[:4] != b'\xd0\x0d\xfe\xed':
        raise ValueError('Not an FDT')
    totalsize, off_struct, off_strings = struct.unpack_from('>III', data, 4)[0:3]
    if totalsize > len(data):
        raise ValueError('FDT truncated')
    nodes = {}
    stack = []
    pos = off_struct
    while True:
        token = struct.unpack_from('>I', data, pos)[0]
        pos += 4
        if token == FDT_BEGIN_NODE:
            name, pos = _string(data, pos)
            pos = _pad4(pos)
            stack.append(name)
        elif token == FDT_END_NODE:
            stack.pop()
        elif token == FDT_PROP:
            length, nameoff = struct.unpack_from('>II', data, pos)
            pos += 8
            value = data[pos:pos + length]
            pos = _pad4(pos + length)
            path = '/' + '/'.join(part for part in stack if part)
            nodes.setdefault(path, {})[_string(data, off_strings + nameoff)[0]] = value
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break
        else:
            raise ValueError(f'Bad FDT token 0x{token:X} at 0x{pos - 4:X}')
    return nodes


def text(value):
    """Return the printable strings inside a property blob."""
    return [part.decode() for part in value.split(b'\0') if part]


def reg_ranges(nodes, path):
    """Return [(base, size)] from a node's reg property, 64-bit big endian."""
    reg = nodes[path]['reg']
    if len(reg) % 16:
        raise ValueError(f'reg is not 64/64 cells: {path}')
    return [struct.unpack_from('>QQ', reg, index) for index in range(0, len(reg), 16)]


def scan_fdts(data):
    """Yield (offset, fdt_bytes, nodes) for every FDT embedded in a blob."""
    offset = 0
    while True:
        offset = data.find(b'\xd0\x0d\xfe\xed', offset)
        if offset < 0:
            return
        if offset + 8 <= len(data):
            size = struct.unpack_from('>I', data, offset + 4)[0]
            if 0 < size <= len(data) - offset:
                blob = data[offset:offset + size]
                try:
                    yield offset, blob, parse_fdt(blob)
                except (ValueError, struct.error):
                    pass
        offset += 1
