#!/usr/bin/env python3
"""Minimal FDT (flat device tree) parser for host-side generation work.

parse_fdt() returns {path: {property: bytes}} with paths like '/soc/memorymap'.
build_fdt() writes such a dictionary back as a new FDT. No dependencies.
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


def build_fdt(nodes):
    """Return an FDT (version 17) holding exactly the given nodes and properties.

    Missing parent nodes are created empty; nodes are written in path order.
    """
    tree = {'/': {}}
    for path in nodes:
        parts = [part for part in path.split('/') if part]
        for depth in range(1, len(parts) + 1):
            tree.setdefault('/' + '/'.join(parts[:depth]), {})
    for path, props in nodes.items():
        tree[path] = props
    strings, offsets = bytearray(), {}
    struct_block = bytearray()

    def emit(path):
        name = '' if path == '/' else path.rsplit('/', 1)[1]
        raw = name.encode() + b'\0'
        struct_block.extend(struct.pack('>I', FDT_BEGIN_NODE) + raw + b'\0' * (_pad4(len(raw)) - len(raw)))
        for prop, value in tree[path].items():
            if prop not in offsets:
                offsets[prop] = len(strings)
                strings.extend(prop.encode() + b'\0')
            struct_block.extend(struct.pack('>III', FDT_PROP, len(value), offsets[prop]) + value +
                                b'\0' * (_pad4(len(value)) - len(value)))
        prefix = '/' if path == '/' else path + '/'
        for child in sorted(p for p in tree if p != path and p.startswith(prefix) and '/' not in p[len(prefix):]):
            emit(child)
        struct_block.extend(struct.pack('>I', FDT_END_NODE))

    emit('/')
    struct_block.extend(struct.pack('>I', FDT_END))
    header_size, reserve_size = 40, 16
    off_reserve = header_size
    off_struct = off_reserve + reserve_size
    off_strings = off_struct + len(struct_block)
    total = off_strings + len(strings)
    header = struct.pack('>10I', 0xd00dfeed, total, off_struct, off_strings, off_reserve,
                         17, 16, 0, len(strings), len(struct_block))
    return header + b'\0' * reserve_size + bytes(struct_block) + bytes(strings)


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
