#!/usr/bin/env python3
"""Generate pista platform memory and configuration maps from the stock xbl_config.

Host only. Reads the staged OTA capture in private/pista-capture (see
stage_pista_capture.py), locates the native UEFI configuration DTB inside the
xbl_config ELF, and writes uefi/platforms/pistaPkg/Library sources plus the
pinned vendor/pista/dtb/xbl-config.dtb input. Conversion rules mirror
prepare_piano.py so both platforms stay byte-comparable.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core_source import core_source
from fdt_parse import build_fdt, parse_fdt, reg_ranges, scan_fdts, text

ROOT = Path(__file__).resolve().parent.parent.parent
CAPTURE = ROOT / 'private' / 'pista-capture'
EXTRACTED = ROOT / 'private' / 'pista-uefi-extracted'
VENDOR = ROOT / 'vendor' / 'pista'
# The sixteen Qualcomm foundation drivers the product FV dispatches at runtime.
NATIVE_MODULES = ('SmemDxe', 'DALSys', 'ChipInfo', 'PlatformInfoDxeDriver',
                  'HWIODxeDriver', 'ULogDxe', 'CmdDbDxe', 'PwrUtilsDxe', 'RpmhDxe',
                  'NpaDxe', 'VcsDxe', 'ClockDxe', 'HALIOMMU', 'DALTLMM', 'Qup', 'I2C')
# Dispatched early via the DXE APRIORI list; validated separately from the sixteen.
EARLY_MODULE = 'EnvDxeEnhanced'
# The diagnostic FV carries the early driver and the sixteen foundation drivers.
STAGE0_MODULES = (EARLY_MODULE,) + NATIVE_MODULES
MEMORY_PATHS = ('/soc/memorymap/memory@', '/soc/registermap/memory@')
HOB_TYPES = {0: 'AddMem', 4: 'NoHob', 5: 'AddDev'}
CACHE_ATTRS = {2: 'WRITE_BACK', 0x22: 'WRITE_BACK_XN', 0x20: 'WRITE_THROUGH_XN',
              0x25: 'UNCACHED_UNBUFFERED_XN', 9: 'NS_DEVICE'}
# Logging writes stay off in the generated platform, as on piano.
CONFIG_OVERRIDES = {'EnableLogFsSyncInRetail': 0, 'EnableDisplayImageFv': 0}
CONFIG_SKIPS = ('MaxCount', 'StrMaxCount')
# The resident USB Fastboot service from the shared UEFI core, compiled into
# the diagnostic boot manager library of the installed workspace copy.
FASTBOOT_SOURCES = ('PianoUsbController.c', 'PianoDwc3Device.c', 'PianoUsbControl.c', 'PianoUsbPhy.c',
                    'PianoFastboot.c', 'PianoFastbootScreen.c', 'PianoSmmu.c', 'PianoDma.c',
                    'PianoOwnedSmmu.c', 'PianoIoPageTable.c', 'PianoFaultRecovery.c',
                    'PianoUfsProbe.c', 'PianoUfsReadOnlyDma.c', 'PianoUfsDmaLayout.c', 'PianoGpt.c',
                    'PianoBlockIo.c', 'PianoFastbootBlockRead.c')
FASTBOOT_COMPONENT_HEADERS = {'usb-host': ('PianoUsbHost.h', 'PianoUsbTypec.h'),
                              'ext4': ('PianoStorageIo.h', 'PianoStorageIo.inc', 'PianoExt4.h')}
# The service reads these nodes to validate the SMMU stream layout and the
# USB controller; the diagnostic firmware has no board FDT of its own.
FASTBOOT_DT_NODES = ('/', '/soc', '/soc/apps-smmu@15000000', '/soc/ufshc@1d84000', '/soc/ssusb@a600000',
                     '/soc/ssusb@a600000/dwc3@a600000', '/soc/qcom,gpi-dma@a00000',
                     '/soc/qcom,qupv3_1_geni_se@ac0000')
FASTBOOT_DT_PROPERTIES = ('compatible', 'reg', 'iommus', '#address-cells', '#size-cells', 'ranges')
# "oem ramlog" ring: the last 2 MiB of DXE_Heap, reserved and uncached. The
# base is repeated in RamLogSerialPortLib/PistaRamLog.c and in the
# PIANO_USB_CONSOLE_BASE flag of Stage0BootManagerLib.inf.
RAM_LOG_BASE = 0xD7E00000
RAM_LOG_SIZE = 0x200000
# UFS host controller registers. The stock map leaves them to the stock UFS
# driver, which the diagnostic firmware replaces with SunUEFI's read-only one.
UFS_HCI = {'name': 'UFS_HCI', 'base': 0x1D84000, 'size': 0x3000, 'hob': 'AddDev', 'resource_type': 1,
           'resource_attribute': 0x400, 'memory_type': 11, 'arm_attribute': 'NS_DEVICE'}
# The upstream BootShim ships a 3 MiB FD; the stock range is split to host it.
FD_SHIM_SIZE = 0x300000
FD_RESERVED_SIZE = 0x100000


def native_config(data):
    configs = [(offset, nodes) for offset, _, nodes in scan_fdts(data)
               if '/soc/memorymap' in nodes and '/sw/uefi/int_param' in nodes]
    if len(configs) != 1:
        raise SystemExit(f'Expected one native UEFI configuration DTB, found {len(configs)}')
    return configs[0]


def memory_entries(nodes):
    observed, split = [], []
    stock_fd = None
    for path, props in nodes.items():
        if not path.startswith(MEMORY_PATHS):
            continue
        label = text(props['mem-label'])[0]
        if label == 'NOMAP':
            continue
        base, size = reg_ranges(nodes, path)[0]
        if len(label.encode()) >= 32 or base % 4096 or size % 4096:
            raise SystemExit(f'Invalid memory descriptor {label} at {path}')
        entry = {'name': label, 'base': base, 'size': size,
                 'hob': HOB_TYPES[int.from_bytes(props['build-hob'], 'big')],
                 'resource_type': int.from_bytes(props['resource-type'], 'big'),
                 'resource_attribute': int.from_bytes(props['resource-attribute'], 'big'),
                 'memory_type': int.from_bytes(props['memory-type'], 'big'),
                 'arm_attribute': CACHE_ATTRS[int.from_bytes(props['cache-attributes'], 'big')]}
        observed.append(entry)
        if label == 'UEFI_FD':
            stock_fd = entry
            continue
        split.append(entry)
    if stock_fd is None:
        raise SystemExit('No UEFI_FD region in the stock memory map')
    if stock_fd['size'] != FD_SHIM_SIZE + FD_RESERVED_SIZE:
        raise SystemExit(f'Stock UEFI_FD range is 0x{stock_fd["size"]:X} bytes, '
                         f'expected 0x{FD_SHIM_SIZE + FD_RESERVED_SIZE:X}')
    fd_base = stock_fd['base']
    split.append({'name': 'FD_Reserved', 'base': fd_base, 'size': FD_RESERVED_SIZE,
                  'hob': 'AddMem', 'resource_type': 0, 'resource_attribute': 0x703C07,
                  'memory_type': 4, 'arm_attribute': 'WRITE_BACK'})
    split.append({'name': 'UEFI_FD', 'base': fd_base + FD_RESERVED_SIZE, 'size': FD_SHIM_SIZE,
                  'hob': 'AddMem', 'resource_type': 0, 'resource_attribute': 0x703C07,
                  'memory_type': 4, 'arm_attribute': 'WRITE_BACK'})
    index = next((i for i, entry in enumerate(split) if entry['name'] == 'DXE_Heap'), None)
    if index is None or split[index]['base'] + split[index]['size'] != RAM_LOG_BASE + RAM_LOG_SIZE:
        raise SystemExit('DXE_Heap does not end where the RAM log region expects')
    # Copy: the stock entry is also recorded unchanged in native-memory-map.json.
    heap = split[index] = dict(split[index], size=split[index]['size'] - RAM_LOG_SIZE)
    split.insert(split.index(heap) + 1,
                 {'name': 'UEFI_RamLog', 'base': RAM_LOG_BASE, 'size': RAM_LOG_SIZE,
                  'hob': 'AddMem', 'resource_type': 5, 'resource_attribute': 0x703C07,
                  'memory_type': 0, 'arm_attribute': 'UNCACHED_UNBUFFERED_XN'})
    ufs_end = UFS_HCI['base'] + UFS_HCI['size']
    if any(entry['base'] < ufs_end and UFS_HCI['base'] < entry['base'] + entry['size'] for entry in split):
        raise SystemExit('The stock memory map already covers the UFS controller registers')
    crypto = next(i for i, entry in enumerate(split) if entry['name'] == 'CRYPTO0_CRYPTO')
    split.insert(crypto, dict(UFS_HCI))
    return observed, split


def render_memory_source(entries):
    rows = ',\n  '.join(
        f'{{"{e["name"]}", 0x{e["base"]:X}, 0x{e["size"]:X}, {e["hob"]}, '
        f'{e["resource_type"]}, 0x{e["resource_attribute"]:X}, {e["memory_type"]}, {e["arm_attribute"]}}}'
        for e in entries)
    return ('// Generated from pista native xbl_config, not a phone memory map.\n'
            '// SPDX-License-Identifier: BSD-2-Clause-Patent\n'
            '#include <Library/MemoryMapLib.h>\n'
            'STATIC EFI_MEMORY_REGION_DESCRIPTOR gMemoryDescriptor[] = {\n  '
            + rows + '\n};\n'
            'VOID GetMemoryMap (OUT EFI_MEMORY_REGION_DESCRIPTOR **Map, OUT UINT8 *Count) {\n'
            '  *Map = gMemoryDescriptor; *Count = ARRAY_SIZE (gMemoryDescriptor);\n}\n')


def config_entries(nodes):
    params = nodes['/sw/uefi/int_param']
    values = []
    for key, blob in params.items():
        if key in CONFIG_SKIPS:
            continue
        value = CONFIG_OVERRIDES.get(key, int.from_bytes(blob, 'big'))
        values.append((key, value))
    return values


def render_config_source(values):
    rows = ',\n'.join(f'  {{"{k}", 0x{v:X}}}' for k, v in values)
    return ('// Generated from pista native UEFI DT configuration.\n'
            '// SPDX-License-Identifier: BSD-2-Clause-Patent\n'
            '#include <Library/ConfigurationMapLib.h>\n'
            'STATIC EFI_CONFIGURATION_ENTRY_DESCRIPTOR gConfigurationDescriptor[] = {\n'
            + rows + '\n};\n'
            'VOID GetConfigurationMap (OUT EFI_CONFIGURATION_ENTRY_DESCRIPTOR **Map, OUT UINT8 *Count) {\n'
            '  *Map = gConfigurationDescriptor; *Count = ARRAY_SIZE (gConfigurationDescriptor);\n}\n')


def compare_with_piano(observed):
    piano_path = ROOT / 'uefi' / 'platforms' / 'pianoPkg' / 'native-memory-map.json'
    if not piano_path.exists():
        return []
    piano = {e['name']: e for e in json.loads(piano_path.read_text())}
    pista = {e['name']: e for e in observed}
    identical, changed = [], []
    for name, entry in pista.items():
        other = piano.get(name)
        if other is None:
            changed.append((name, 'only in pista', None))
        elif all(other.get(k) == entry[k] for k in
                 ('base', 'size', 'hob', 'resource_type', 'resource_attribute', 'memory_type', 'arm_attribute')):
            identical.append(name)
        else:
            diff = [k for k in ('base', 'size', 'hob', 'resource_type', 'memory_type', 'arm_attribute')
                    if other.get(k) != entry[k]]
            changed.append((name, diff, (entry, other)))
    for name in piano:
        if name not in pista:
            changed.append((name, 'only in piano', None))
    return identical, changed


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def extracted_index():
    """Return {module name: file-<GUID> directory} from the FV extraction."""
    index = {}
    inventory = json.loads((EXTRACTED / 'inventory.json').read_text())
    for record in inventory:
        module = EXTRACTED / f'file-{record["guid"]}'
        ui = module / 'ui.ui'
        if ui.exists():
            index[ui.read_bytes().decode('utf-16-le', errors='replace').strip('\0')] = module
    return index


def binary_inf(name, guid, depex):
    source = f'''[Defines]
  INF_VERSION = 0x0001001B
  BASE_NAME = {name}
  FILE_GUID = {guid}
  MODULE_TYPE = DXE_DRIVER
  VERSION_STRING = 1.0
  ENTRY_POINT = EfiEntry
[Binaries.AARCH64]
  PE32|{name}.efi|*
'''
    if depex:
        source += f'  DXE_DEPEX|{name}.depex|*\n'
    return source


def package_vendor(platform):
    index = extracted_index()
    uefi_sha = sha256_file(CAPTURE / 'uefi.img')
    missing = [name for name in NATIVE_MODULES + (EARLY_MODULE,) if name not in index]
    if missing:
        raise SystemExit(f'Native modules missing from the extracted image: {missing}')
    native_dir = VENDOR / 'native'
    if native_dir.exists():
        shutil.rmtree(native_dir)
    native_dir.mkdir(parents=True)
    drivers = {}
    files = {'dtb/xbl-config.dtb': {
        'bytes': (VENDOR / 'dtb' / 'xbl-config.dtb').stat().st_size,
        'sha256': sha256_file(VENDOR / 'dtb' / 'xbl-config.dtb')}}
    # The live board FDT comes from the stock recovery, sanitized in place in
    # the capture directory; the installed HyperOS port never supplies it.
    board_clean = CAPTURE / 'live-recovery.clean.dtb'
    if board_clean.exists():
        shutil.copyfile(board_clean, VENDOR / 'dtb' / 'pista.dtb')
        files['dtb/pista.dtb'] = {'bytes': board_clean.stat().st_size,
                                  'sha256': sha256_file(board_clean)}
        board_dtb = {'path': 'dtb/pista.dtb', 'hardware_scope_unchanged': True,
                     'removed_properties': {'/': [], '/chosen': ['bootargs', 'kaslr-seed',
                                                                'linux,initrd-end', 'linux,initrd-start']},
                     'source_sha256': files['dtb/pista.dtb']['sha256']}
        memory_gib = 12
        board_source = ('Live board FDT read from the stock realme recovery '
                        '(/sys/firmware/fdt, MemTotal 11384380 kB), snapshot properties removed; '
                        'hardware tree and reservations unchanged')
    else:
        board_dtb = {'path': 'dtb/pista.dtb', 'status': 'PENDING_LIVE_CAPTURE'}
        memory_gib = None
        board_source = ('Pending live capture from the stock system; the installed '
                        'HyperOS port must not serve as the source for board inputs')
    for name in NATIVE_MODULES + (EARLY_MODULE,):
        module = index[name]
        pe = module / 'pe32.pe'
        depex = module / 'depex.dxe.depex'
        target_dir = VENDOR / 'early-dxe' / EARLY_MODULE if name == EARLY_MODULE else native_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target_pe = target_dir / f'{name}.efi'
        target_depex = target_dir / f'{name}.depex'
        shutil.copyfile(pe, target_pe)
        if depex.exists():
            shutil.copyfile(depex, target_depex)
        if name == EARLY_MODULE:
            early = target_dir
            (early / f'{name}.inf').write_text(binary_inf(name, module.name.removeprefix('file-'), depex.exists()))
            (early / 'provenance.json').write_text(json.dumps({
                'schema_version': 1, 'name': name,
                'file_guid': module.name.removeprefix('file-'),
                'source': 'Original factory UEFI PE and DXE DEPEX sections from the stock '
                          '16.0.7.201 OTA uefi partition image; binary INF generated here',
                'source_image': {'name': 'uefi.img', 'sha256': uefi_sha},
                'dispatch': 'DXE_DRIVER in FV and DXE APRIORI; no ProductFoundation runtime loading',
            }, indent=2) + '\n')
            continue
        drivers[name] = {'pe': f'native/{name}.efi',
                         'depex': f'native/{name}.depex' if depex.exists() else None,
                         'file_guid': module.name.removeprefix('file-')}
        files[f'native/{name}.efi'] = {'bytes': target_pe.stat().st_size, 'sha256': sha256_file(target_pe)}
        if depex.exists():
            files[f'native/{name}.depex'] = {'bytes': target_depex.stat().st_size, 'sha256': sha256_file(target_depex)}
    manifest = {
        'schema_version': 1,
        'target': {'device': 'rmx5010', 'memory_gib': memory_gib, 'soc': 'SM8750-AB'},
        'scope': 'CAPTURED_FIRMWARE_NOT_RUNTIME_AUTHORITY',
        'native_drivers': drivers,
        'board_dtb': board_dtb,
        'xbl_config_dtb': {'path': 'dtb/xbl-config.dtb',
                           'source_sha256': files['dtb/xbl-config.dtb']['sha256']},
        'provenance': {
            'board_source': board_source,
            'distribution_license_status': 'NOT_REVIEWED',
            'native_source': 'Original EFI PE and DEPEX sections extracted from the stock '
                             '16.0.7.201 OTA uefi partition image',
            'uefi_image_sha256': uefi_sha,
            'xbl_source': 'Configuration FDT extracted from the stock 16.0.7.201 OTA '
                           'xbl_config partition image',
        },
        'files': files,
    }
    (VENDOR / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    stage0 = {}
    for name in STAGE0_MODULES:
        module = index[name]
        pe = module / 'pe32.pe'
        depex = module / 'depex.dxe.depex'
        stage0[name] = {'guid': module.name.removeprefix('file-'),
                        'pe_sha256': sha256_file(pe),
                        'depex_sha256': sha256_file(depex) if depex.exists() else None}
    (platform / 'native-modules.json').write_text(json.dumps(stage0, indent=2) + '\n')
    names = ', '.join(f'L"{name}"' for name in NATIVE_MODULES)
    (platform / 'Library' / 'Stage0BootManagerLib' / 'NativeModules.h').write_text(
        '// Generated by tools/dev/prepare_pista.py from NATIVE_MODULES.\n'
        '// SPDX-License-Identifier: BSD-2-Clause-Patent\n'
        f'STATIC CONST CHAR16 *CONST mNativeModules[] = {{ {names} }};\n')
    (VENDOR / 'README.md').write_text(
        '# vendor/pista inputs\n\n'
        'Stock Realme GT7 Pro (RMX5010) inputs from the factory realme UI 16.0.7.201 '
        'full OTA payload (`private/pista-capture/manifest.json` records the source). '
        'Generation: `python tools/dev/prepare_pista.py`.\n\n'
        '- `dtb/xbl-config.dtb` - native UEFI configuration FDT carved from the xbl_config partition.\n'
        '- `native/` - original Qualcomm foundation DXE drivers (PE + DEPEX) from the uefi '
        'partition image. Same module names and GUIDs as piano; different OEM builds.\n'
        '- `early-dxe/EnvDxeEnhanced/` - early environment driver with its own provenance record.\n'
        + ('- `dtb/pista.dtb` - live board FDT read from the stock realme recovery '
           '(`/sys/firmware/fdt`, 12 GB variant), snapshot properties in `/chosen` removed.\n'
           if board_clean.exists() else
           '- `dtb/pista.dtb` - missing: needs `/sys/firmware/fdt` from the stock system; '
           'the installed HyperOS port must not supply it.\n'))
    print(f'Packaged {len(drivers)} native drivers + EnvDxeEnhanced -> {VENDOR}')
    return stage0


def write_fastboot_device_tree(platform):
    """Write the board FDT subset the USB Fastboot service reads as a C array."""
    board = VENDOR / 'dtb' / 'pista.dtb'
    if not board.exists():
        raise SystemExit('dtb/pista.dtb is missing; the USB Fastboot service needs the board FDT')
    nodes = parse_fdt(board.read_bytes())
    missing = [path for path in FASTBOOT_DT_NODES if path not in nodes]
    if missing:
        raise SystemExit(f'Board FDT lacks nodes the USB Fastboot service reads: {missing}')
    subset = {path: {key: value for key, value in nodes[path].items() if key in FASTBOOT_DT_PROPERTIES}
              for path in FASTBOOT_DT_NODES}
    blob = build_fdt(subset)
    if parse_fdt(blob) != subset:
        raise SystemExit('FDT subset does not read back unchanged')
    rows = ',\n'.join('  ' + ', '.join(f'0x{byte:02X}' for byte in blob[index:index + 16])
                      for index in range(0, len(blob), 16))
    (platform / 'Library' / 'Stage0BootManagerLib' / 'PistaDeviceTree.h').write_text(
        '// Generated by tools/dev/prepare_pista.py: the SMMU and USB nodes of\n'
        '// vendor/pista/dtb/pista.dtb read by the USB Fastboot service.\n'
        '// SPDX-License-Identifier: BSD-2-Clause-Patent\n'
        f'STATIC CONST UINT8 mPistaDeviceTree[] __attribute__((aligned(8))) = {{\n{rows}\n}};\n')
    print(f'USB Fastboot FDT subset: {len(FASTBOOT_DT_NODES)} nodes, {len(blob)} bytes')


def install_fastboot_sources(library):
    """Copy the shared USB Fastboot service into the workspace boot manager library."""
    for name in FASTBOOT_SOURCES:
        shutil.copyfile(core_source(name, ROOT), library / name)
    core = ROOT / 'uefi' / 'core'
    for path in core.rglob('*'):
        if path.is_file() and path.suffix in ('.h', '.inc'):
            shutil.copyfile(path, library / path.name)
    # Core sources include their protocols as "../Protocol/<name>.h".
    shutil.copytree(core / 'Protocol', library.parent / 'Protocol', dirs_exist_ok=True)
    for component, names in FASTBOOT_COMPONENT_HEADERS.items():
        for name in names:
            shutil.copyfile(ROOT / 'uefi' / 'components' / component / name, library / name)


def install_ram_log_serial_port(library):
    """Build the workspace serial port library: Mu's framebuffer console plus the RAM log."""
    upstream = ROOT / 'upstream' / 'Mu-Silicium' / 'Silicon' / 'Silicium' / 'SiliciumPkg' / 'Library' / 'FrameBufferSerialPortLib'
    for name in ('FrameBufferSerialPortLib.c', 'FrameBuffer.h', 'Font.h'):
        shutil.copyfile(upstream / name, library / name)
    source = (library / 'FrameBufferSerialPortLib.c').read_text()
    include, write = '#include "FrameBuffer.h"\n', '  // Get Frame Buffer Memory\n  Status = GetFrameBufferMemory (&FbBase, &FbLength);'
    if source.count(include) != 1 or source.count(write) != 1:
        raise SystemExit('Upstream FrameBufferSerialPortLib.c changed; update the RAM log hook')
    # RamLogWrite returns FALSE once the module has turned its screen copy off.
    source = source.replace(include, include + 'BOOLEAN RamLogWrite (IN CONST UINT8 *Buffer, IN UINTN Length);\n')
    source = source.replace(write, '  if (!RamLogWrite (Buffer, NumberOfBytes)) {\n    return NumberOfBytes;\n  }\n\n' + write)
    (library / 'FrameBufferSerialPortLib.c').write_text(source)
    inf = (upstream / 'FrameBufferSerialPortLib.inf').read_text()
    if inf.count('  FrameBufferSerialPortLib.c\n') != 1:
        raise SystemExit('Upstream FrameBufferSerialPortLib.inf changed; update the RAM log hook')
    inf = inf.replace('BASE_NAME                      = FrameBufferSerialPortLib',
                      'BASE_NAME                      = PistaRamLogSerialPortLib')
    inf = inf.replace('33220897-2430-1CD5-EA89-CB01B138CF39', 'B6A1D0E4-5C2F-4E7A-9D31-7F0C2A8E4B15')
    inf = inf.replace('  FrameBufferSerialPortLib.c\n', '  FrameBufferSerialPortLib.c\n  PistaRamLog.c\n')
    (library / 'RamLogSerialPortLib.inf').write_text(inf)


def install_workspace(platform):
    """Copy pistaPkg and the Stage0 binaries into the fetched Mu-Silicium workspace."""
    workspace = ROOT / 'upstream' / 'Mu-Silicium'
    if not (workspace / 'Platforms').is_dir():
        print('Mu-Silicium workspace not fetched yet; skipping workspace install')
        return
    destination = workspace / 'Platforms' / 'Realme' / 'pistaPkg'
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(platform, destination)
    install_fastboot_sources(destination / 'Library' / 'Stage0BootManagerLib')
    install_ram_log_serial_port(destination / 'Library' / 'RamLogSerialPortLib')
    index = extracted_index()
    stage0 = json.loads((platform / 'native-modules.json').read_text())
    binaries = workspace / 'Binaries' / 'pista' / 'Stage0'
    if binaries.exists():
        shutil.rmtree(binaries)
    for name in STAGE0_MODULES:
        target = workspace / 'Binaries' / 'pista' / 'Stage0' / name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(index[name] / 'pe32.pe', target / f'{name}.efi')
        if (index[name] / 'depex.dxe.depex').exists():
            shutil.copyfile(index[name] / 'depex.dxe.depex', target / f'{name}.depex')
        (target / f'{name}.inf').write_text(
            binary_inf(name, stage0[name]['guid'], (index[name] / 'depex.dxe.depex').exists()))
    configs = workspace / 'Resources' / 'Configs'
    configs.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(platform / 'pista.toml', configs / 'pista.toml')
    if (VENDOR / 'dtb' / 'pista.dtb').exists():
        shutil.copyfile(VENDOR / 'dtb' / 'pista.dtb', workspace / 'Resources' / 'DTBs' / 'pista.dtb')
    print(f'Installed pistaPkg and Stage0 binaries into {workspace}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--capture', type=Path, default=CAPTURE)
    ap.add_argument('--platform', type=Path, default=ROOT / 'uefi' / 'platforms' / 'pistaPkg')
    args = ap.parse_args()
    image = (args.capture / 'xbl_config.img').read_bytes()
    offset, nodes = native_config(image)
    dtb = next(blob for where, blob, _ in scan_fdts(image) if where == offset)
    vendor_dtb = ROOT / 'vendor' / 'pista' / 'dtb' / 'xbl-config.dtb'
    vendor_dtb.parent.mkdir(parents=True, exist_ok=True)
    vendor_dtb.write_bytes(dtb)
    observed, split = memory_entries(nodes)
    values = config_entries(nodes)
    (args.platform / 'Library' / 'MemoryMapLib').mkdir(parents=True, exist_ok=True)
    (args.platform / 'Library' / 'ConfigurationMapLib').mkdir(parents=True, exist_ok=True)
    (args.platform / 'native-memory-map.json').write_text(json.dumps(observed, indent=2) + '\n')
    (args.platform / 'Library' / 'MemoryMapLib' / 'MemoryMapLib.c').write_text(render_memory_source(split))
    (args.platform / 'Library' / 'ConfigurationMapLib' / 'ConfigurationMapLib.c').write_text(render_config_source(values))
    print(f'xbl_config DTB at image offset 0x{offset:X} -> {vendor_dtb}')
    print(f'Memory regions: {len(observed)} stock, {len(split)} in generated table')
    print(f'Configuration values: {len(values)}')
    identical, changed = compare_with_piano(observed)
    print(f'Identical to piano: {len(identical)}/{len(observed)}')
    for name, diff, pair in changed:
        if diff == 'only in piano':
            print(f'  - {name}: only in piano')
        elif diff == 'only in pista':
            print(f'  + {name}: only in pista {pair or ""}')
        else:
            print(f'  ~ {name}: differs in {", ".join(diff)}')
            if isinstance(pair, tuple):
                pista_entry, piano_entry = pair
                for key in diff:
                    print(f'      pista 0x{pista_entry[key]:X} vs piano 0x{piano_entry.get(key, 0):X} ({key})'
                          if isinstance(pista_entry[key], int) else f'      {key}: {pista_entry[key]} vs {piano_entry.get(key)}')
    package_vendor(args.platform)
    write_fastboot_device_tree(args.platform)
    install_workspace(args.platform)
    print(f'Generated {args.platform}')


if __name__ == '__main__':
    main()
