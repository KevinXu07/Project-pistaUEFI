#!/usr/bin/env python3
"""Build tree for the pista Windows-capable UEFI, ported from the OnePlus Ace 5 Pro.

AnyLaySys/uefi_oneplus_ace5_pro (hummer, BSD-2-Clause) is a Mu-Silicium
based UEFI that runs the full stock driver stack and boots Windows. This
script checks out the commit pinned in sources.lock.json and turns it into a
pista tree: the stock drivers and raw files come from the pista OTA uefi image
(tools/dev/extract_uefi_fv.py), hummer's driver patches are applied to them,
and hummerPkg becomes Platforms/Realme/pistaPkg. Run it on a Linux file
system (for example ~/hummer-uefi in WSL); tools/dev/build_pista_hummer.sh
builds the result.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
EXTRACTED = ROOT / 'private' / 'pista-uefi-extracted'
BOARD_DTB = ROOT / 'vendor' / 'pista' / 'dtb' / 'pista.dtb'
OVERLAY = ROOT / 'uefi' / 'platforms' / 'pista-windows'
PIN = json.loads((ROOT / 'sources.lock.json').read_text())['uefi_oneplus_ace5_pro']
# Most pista drivers carry the same code as hummer's, so hummer's patch
# offsets apply. PmicDxe is the pagani build instead (Mu-Silicium
# Binaries/pagani); hummer's patches.json names the pagani offset of each edit.
PMIC_AT_PAGANI = {0x41B4: 0x41B4, 0x41BC: 0x41BC, 0x91F4: 0x94BC, 0x1A89C: 0x1ACFC, 0x25FB0: 0x26410}
# The pista charger driver matches neither hummer's nor pagani's build, so
# the charger drivers stay out until their patches are relocated.
DROPPED = ('QcomChargerDxe/QcomChargerDxeLA.inf', 'ChargerExDxe/ChargerExDxe.inf')
# pista's stock xbl_config: hummer (16 GB) places DXE_Heap at 0xBD930000, but
# on pista ABL does not list 0xBD930000..0xBE360000 as DDR.
DXE_HEAP = '{"DXE_Heap",           0xBE360000, 0x19CA0000'
# Shared modules that include the device package for its headers.
SHARED_REFERENCES = ('Silicon/Silicium/SiliciumPkg/Library/MsBootOptionsLib/MsBootOptionsLib.inf',
                     'Silicon/Silicium/SiliciumPkg/Sec/Sec.inf',
                     'Mu_Basecore/MdeModulePkg/Application/BootMenu/BootMenu.inf')
SMBIOS = {'PcdSmbiosSystemManufacturer': 'Realme', 'PcdSmbiosSystemModel': 'GT7 Pro',
          'PcdSmbiosSystemRetailModel': 'pista', 'PcdSmbiosSystemRetailSku': 'RMX5010',
          'PcdSmbiosSystemBoardModel': 'pista'}
TEXT_SUFFIXES = ('.dsc', '.fdf', '.dec', '.inf', '.inc', '.c', '.h', '.py', '.S', '.asl', '.aslc')


def checkout(tree):
    if not tree.exists():
        tree.mkdir(parents=True)
        subprocess.run(['git', 'init', '-q'], cwd=tree, check=True)
        subprocess.run(['git', 'remote', 'add', 'origin', PIN['url']], cwd=tree, check=True)
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=tree, capture_output=True, text=True).stdout.strip()
    if head != PIN['commit']:
        subprocess.run(['git', '-c', 'core.autocrlf=false', 'fetch', '-q', '--depth', '1', 'origin', PIN['commit']],
                       cwd=tree, check=True)
        subprocess.run(['git', '-c', 'core.autocrlf=false', 'checkout', '-q', '--detach', PIN['commit']],
                       cwd=tree, check=True)
    # Undo an earlier port so every run starts from the pinned tree.
    subprocess.run(['git', 'checkout', '-q', '--', '.'], cwd=tree, check=True)
    for path in ('Platforms/Realme', 'Binaries/pista', 'Resources/Configs/pista.toml', 'Resources/DTBs/pista.dtb'):
        target = tree / path
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def stock_modules():
    by_guid, by_name = {}, {}
    for record in json.loads((EXTRACTED / 'inventory.json').read_text()):
        module = EXTRACTED / f'file-{record["guid"]}'
        by_guid[record['guid'].lower()] = module
        ui = module / 'ui.ui'
        if ui.exists():
            by_name[ui.read_bytes().decode('utf-16-le', 'replace').strip('\0')] = module
    return by_guid, by_name


def copy_binaries(tree):
    """Copy hummer's binary package layout, filled with the pista stock files."""
    hummer, pista = tree / 'Binaries' / 'hummer', tree / 'Binaries' / 'pista'
    by_guid, by_name = stock_modules()
    shutil.copytree(hummer, pista)
    for path in list(pista.rglob('*.patched.efi')) + [pista / 'patches.json', pista / 'apply_patches.py']:
        path.unlink()
    kept = []
    for inf in sorted(pista.rglob('*.inf')):
        text = inf.read_text(errors='replace')
        guid = re.search(r'FILE_GUID\s*=\s*([0-9A-Fa-f-]+)', text)
        pe = re.search(r'PE32\|([^|\s]+)', text)
        if not guid or not pe:
            continue
        module = by_guid.get(guid.group(1).lower())
        if module is None or not (module / 'pe32.pe').exists():
            kept.append(str(inf.relative_to(pista)))
            continue
        name = pe.group(1).replace('.patched.efi', '.efi')
        inf.write_text(text.replace(pe.group(1), name))
        (inf.parent / name).write_bytes((module / 'pe32.pe').read_bytes())
        depex = re.search(r'DXE_DEPEX\|([^|\s]+)', text)
        if depex and (module / 'depex.dxe.depex').exists():
            (inf.parent / depex.group(1)).write_bytes((module / 'depex.dxe.depex').read_bytes())
    for raw in (pista / 'RawFiles').iterdir():
        module = by_name.get(raw.name)
        if module is not None and (module / 'raw.raw').exists():
            raw.write_bytes((module / 'raw.raw').read_bytes())
    print(f'Stock pista binaries in Binaries/pista; kept from hummer: {", ".join(kept)}')


def patch_binaries(tree):
    hummer, pista = tree / 'Binaries' / 'hummer', tree / 'Binaries' / 'pista'
    manifest = json.loads((hummer / 'patches.json').read_text())
    for entry in manifest['patches']:
        relative = entry['file']
        if any(Path(relative).stem == Path(name).stem for name in DROPPED):
            continue
        path = pista / relative
        original = path.read_bytes()
        data = bytearray(original)
        is_pmic = 'PmicDxe' in relative
        for edit in entry['edits']:
            offset = int(edit['offset'], 16)
            before, after = bytes.fromhex(edit['before']), bytes.fromhex(edit['after'])
            if is_pmic:
                offset = PMIC_AT_PAGANI[offset]
            current = bytes(data[offset:offset + len(before)])
            # Branch offsets differ between builds; a BL must still be a BL.
            same_call = len(before) == 4 and before[3] == 0x94 and current[3:] == b'\x94'
            if current != before and not (is_pmic and same_call):
                sys.exit(f'{relative}: unexpected bytes {current.hex()} at 0x{offset:X}')
            data[offset:offset + len(before)] = after
        if 'append' in entry:
            # The appended code branches back into the driver, so the pista
            # code section must equal hummer's.
            source = (hummer / relative).read_bytes()
            code_end = len(source) * 3 // 4
            if len(original) != len(source) or original[0x1000:code_end] != source[0x1000:code_end]:
                sys.exit(f'{relative}: code differs from hummer; its appended code does not apply')
            payload = bytes.fromhex(entry['append']['data'])
            data.extend(payload + bytes(entry['append']['size'] - len(payload)))
        patched = path.with_name(f'{path.stem}.patched.efi')
        patched.write_bytes(data)
        inf = next(path.parent.glob('*.inf'))
        inf.write_text(inf.read_text().replace(f'|{path.name}', f'|{patched.name}'))
        print(f'Patched {relative} ({hashlib.sha256(data).hexdigest()[:12]})')


def port_platform(tree):
    source, package = tree / 'Platforms/OnePlus/hummerPkg', tree / 'Platforms/Realme/pistaPkg'
    shutil.copytree(source, package)
    for suffix in ('dsc', 'fdf', 'dec'):
        (package / f'hummer.{suffix}').rename(package / f'pista.{suffix}')
    for path in package.rglob('*'):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            text = path.read_text(errors='replace')
            ported = (text.replace('Binaries/hummer', 'Binaries/pista').replace('hummerPkg', 'pistaPkg')
                      .replace('hummer.dsc', 'pista.dsc').replace('hummer.fdf', 'pista.fdf')
                      .replace('hummer.dec', 'pista.dec').replace('HUMMER_UEFI', 'PISTA_UEFI')
                      .replace('"hummer"', '"pista"').replace('= hummer\n', '= pista\n')
                      .replace('= hummer\r\n', '= pista\r\n'))
            if ported != text:
                path.write_text(ported)
    dsc = package / 'pista.dsc'
    text = dsc.read_text()
    for pcd, value in SMBIOS.items():
        text = re.sub(rf'({pcd}\|)"[^"]*"', rf'\1"{value}"', text)
    dsc.write_text(text)
    for include in ('Include/DXE.inc', 'Include/APRIORI.inc'):
        path = package / include
        path.write_text(''.join(line for line in path.read_text().splitlines(True)
                                if not any(name in line for name in DROPPED)))
    memory_map = package / 'Library/MemoryMapLib/MemoryMapLib.c'
    text, count = re.subn(r'\{"DXE_Heap",\s*0x[0-9A-F]+,\s*0x[0-9A-F]+', DXE_HEAP, memory_map.read_text())
    if count != 1:
        sys.exit('DXE_Heap not found in the hummer memory map')
    memory_map.write_text(text)
    for relative in SHARED_REFERENCES:
        path = tree / relative
        path.write_text(path.read_text().replace('hummerPkg/hummer.dec', 'pistaPkg/pista.dec'))
    font = tree / 'Resources/Scripts/build_font.py'
    font.write_text(font.read_text().replace('Platforms/OnePlus/hummerPkg', 'Platforms/Realme/pistaPkg'))
    shutil.copyfile(tree / 'Resources/Configs/hummer.toml', tree / 'Resources/Configs/pista.toml')
    shutil.copyfile(BOARD_DTB, tree / 'Resources/DTBs/pista.dtb')


def replace_once(path, old, new):
    text = path.read_text().replace('\r\n', '\n')
    if text.count(old) != 1:
        sys.exit(f'{path.name}: expected one match for {old.splitlines()[0]!r}')
    path.write_text(text.replace(old, new))


def port_windows_boot(tree):
    """Make the Windows loader safe on pista and let it boot from USB or RAM."""
    package = tree / 'Platforms/Realme/pistaPkg'
    # Our own files, laid out like the package: the RAM disk Mass Storage
    # application replaces hummer's, which lent UFS LUN 0 to the computer.
    for path in OVERLAY.rglob('*'):
        if path.is_file():
            target = package / path.relative_to(OVERLAY)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    boot = package / 'Applications/WindowsBoot/WindowsBoot.c'
    # pista's board device tree names its memory node "/memory".
    replace_once(boot, 'FdtPathOffset (Dtb, "/memory@81960000")', 'FdtPathOffset (Dtb, "/memory")')
    # hummer's board bring-up does not match the pista board: its PCIe0 rails
    # power hummer's Wi-Fi front end, and GPIO53 and ldob9 (set to 3.056 V)
    # drive pista's IR emitter (L9B, 2.504 V). Camera DMA, ADSP and GPU set-up
    # follow hummer's Windows drivers. None of it runs on pista.
    replace_once(boot, '''  Pcie0Initialize ();
  MmioWrite32 (0x0F135004, 0);
  MmioWrite32 (0x0F135000, 0x200);
  {
    CONST CHAR8 *Names[] = { "ldob1", "ldob9" };
    CONST UINT32 Voltages[] = { 1800, 3056 };
    CONST UINT32 Offsets[] = { 0, 8, 4 };
    for (UINTN Rail = 0; Rail < ARRAY_SIZE (Names); Rail++) {
      UINT32 Address = CmdDbAddress (Names[Rail]);
      UINT32 Values[] = { Voltages[Rail], 7, 1 };
      if (Address != 0) { RpmhWrite (Address, ARRAY_SIZE (Values), Offsets, Values); }
    }
  }
  AdspInitialize (Handles, Count);
  GpuInitialize ();
''', '')
    replace_once(boot, '''      EFI_STATUS CameraStatus = HummerCameraDmaInitialize ();
      DEBUG ((DEBUG_INFO, "Camera DMA initialization: %r\\n", CameraStatus));
''', '')
    # The load option picks where \EFI\BOOT\BOOTAA64.EFI comes from: "USB"
    # (from the boot menu) for a USB drive, "RAM" (from the Mass Storage
    # application) for its RAM disk, none for internal storage. Device mode
    # is started only for internal storage.
    replace_once(boot, '''  EFI_DEVICE_PATH_PROTOCOL *DevicePath;

  Status = ReservePlatformMemory ();''', '''  EFI_DEVICE_PATH_PROTOCOL *DevicePath;
  EFI_LOADED_IMAGE_PROTOCOL *Self;
  BOOT_SOURCE Source;

  Source = BootSourceInternal;
  if (!EFI_ERROR (gBS->HandleProtocol (ImageHandle, &gEfiLoadedImageProtocolGuid, (VOID **)&Self))) {
    if (Self->LoadOptionsSize == sizeof ("USB") && CompareMem (Self->LoadOptions, "USB", sizeof ("USB")) == 0) {
      Source = BootSourceUsb;
    } else if (Self->LoadOptionsSize == sizeof ("RAM") && CompareMem (Self->LoadOptions, "RAM", sizeof ("RAM")) == 0) {
      Source = BootSourceRamDisk;
    }
  }
  Status = ReservePlatformMemory ();''')
    replace_once(boot, '''  Status = gBS->LocateProtocol (&gEfiUsbfnIoProtocolGuid, NULL, (VOID **)&Usb);
  if (!EFI_ERROR (Status) && Usb != NULL) {''', '''  Status = gBS->LocateProtocol (&gEfiUsbfnIoProtocolGuid, NULL, (VOID **)&Usb);
  if (Source == BootSourceInternal && !EFI_ERROR (Status) && Usb != NULL) {''')
    replace_once(boot, 'if (DevicePath == NULL || MsBootPolicyLibIsDevicePathUsb (DevicePath)) {',
                 'if (DevicePath == NULL || !IsBootSource (DevicePath, Source)) {')
    replace_once(boot, '#include <Protocol/BlockIo.h>', '''#include <Protocol/BlockIo.h>
#include <Protocol/LoadedImage.h>
#include <PistaRamDisk.h>

typedef enum { BootSourceInternal, BootSourceUsb, BootSourceRamDisk } BOOT_SOURCE;

STATIC EFI_GUID mRamDiskGuid = PISTA_RAM_DISK_GUID;

STATIC BOOLEAN
IsBootSource (IN EFI_DEVICE_PATH_PROTOCOL *Path, IN BOOT_SOURCE Source)
{
  BOOLEAN Usb = MsBootPolicyLibIsDevicePathUsb (Path);
  BOOLEAN RamDisk = FALSE;

  for (EFI_DEVICE_PATH_PROTOCOL *Node = Path; !IsDevicePathEnd (Node); Node = NextDevicePathNode (Node)) {
    if (DevicePathType (Node) == HARDWARE_DEVICE_PATH && DevicePathSubType (Node) == HW_VENDOR_DP &&
        DevicePathNodeLength (Node) >= sizeof (VENDOR_DEVICE_PATH) &&
        CompareGuid (&((VENDOR_DEVICE_PATH *)Node)->Guid, &mRamDiskGuid)) {
      RamDisk = TRUE;
    }
  }
  switch (Source) {
    case BootSourceUsb:     return Usb;
    case BootSourceRamDisk: return RamDisk;
    default:                return !Usb && !RamDisk;
  }
}''')
    # Keep the loader's own status on screen instead of repainting the screen
    # saved when the display driver started, which in DEBUG builds is a log.
    replace_once(boot, '  EFI_PRESERVED_BOOT_LOGO_PROTOCOL *Logo;\n', '')
    replace_once(boot, '''      if (!EFI_ERROR (gBS->LocateProtocol (&gPreservedBootLogoProtocolGuid, NULL, (VOID **)&Logo))) {
        Logo->Restore ();
      }
''', '''      Print (L"Starting %s\\r\\n", FileName);
''')
    inf = package / 'Applications/WindowsBoot/WindowsBoot.inf'
    replace_once(inf, '[Protocols]', '[Protocols]\n  gEfiLoadedImageProtocolGuid')
    # Without a serial port, colour bars at the top of the screen show how
    # far winload got: green when it exits boot services, then blue once the
    # kernel patch is installed, or red when winload's code was not found.
    patch = package / 'Library/WindowsKernelPatchLib/WindowsKernelPatch.c'
    replace_once(patch, '#include <Library/WindowsKernelPatchLib.h>\n',
                 '#include <Library/WindowsKernelPatchLib.h>\n#include <Protocol/GraphicsOutput.h>\n')
    replace_once(patch, 'STATIC UINT64 mMemoryMap[8192];\n', '''STATIC UINT64 mMemoryMap[8192];
STATIC UINT32 *mFrameBuffer;
STATIC UINT32 mWidth;
STATIC UINT32 mStride;

STATIC VOID
ShowProgress (UINTN Row, UINT32 Colour)
{
  if (mFrameBuffer == NULL) {
    return;
  }
  for (UINTN Y = Row * 80; Y < Row * 80 + 60; Y++) {
    for (UINTN X = 0; X < mWidth; X++) {
      mFrameBuffer[Y * mStride + X] = Colour;
    }
  }
}
''')
    replace_once(patch, '  Status = gBS->GetMemoryMap (&Size, (EFI_MEMORY_DESCRIPTOR *)mMemoryMap,',
                 '  ShowProgress (0, 0x0000C000);\n  Status = gBS->GetMemoryMap (&Size, (EFI_MEMORY_DESCRIPTOR *)mMemoryMap,')
    replace_once(patch, '    UninstallWindowsKernelPatch ();\n    return gBS->ExitBootServices (ImageHandle, MapKey);',
                 '    ShowProgress (1, 0x000000FF);\n    UninstallWindowsKernelPatch ();\n'
                 '    return gBS->ExitBootServices (ImageHandle, MapKey);')
    replace_once(patch, '  }\n  return EFI_NOT_FOUND;\n}', '  }\n  ShowProgress (1, 0x00FF0000);\n  return EFI_NOT_FOUND;\n}')
    replace_once(patch, '''  LocateMemoryAttributeProtocol ();
  mExitBootServices = gBS->ExitBootServices;''', '''  EFI_GRAPHICS_OUTPUT_PROTOCOL *Graphics;

  if (!EFI_ERROR (gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&Graphics))) {
    mFrameBuffer = (UINT32 *)(UINTN)Graphics->Mode->FrameBufferBase;
    mWidth = Graphics->Mode->Info->HorizontalResolution;
    mStride = Graphics->Mode->Info->PixelsPerScanLine;
  }
  LocateMemoryAttributeProtocol ();
  mExitBootServices = gBS->ExitBootServices;''')
    patch_inf = package / 'Library/WindowsKernelPatchLib/WindowsKernelPatchLib.inf'
    replace_once(patch_inf, '[Protocols]\n', '[Protocols]\n  gEfiGraphicsOutputProtocolGuid\n')
    # The menu's USB entry starts this loader instead of the generic MS boot
    # policy, which applies no Windows kernel patch on this SoC.
    menu = tree / 'Mu_Basecore/MdeModulePkg/Application/BootMenu/BootMenu.c'
    replace_once(menu, '      &gMsBootPolicyFileGuid, FixedPcdGetPtr (PcdUfpLoaderFile)',
                 '      mInternalStorageMenu ? &gMsBootPolicyFileGuid : &mGeneralBootGuid, FixedPcdGetPtr (PcdUfpLoaderFile)')
    # Windows would drive the internal UFS with its inbox storufs driver.
    # Until installing to internal storage is planned, Windows does not see it.
    dsdt = package / 'AcpiTables/Dsdt.asl'
    replace_once(dsdt, '''            Name (_HID, "QCOM24A5")
            Name (_UID, 0)''', '''            Name (_HID, "QCOM24A5")
            Name (_UID, 0)
            Method (_STA, 0, NotSerialized) { Return (Zero) }''')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--tree', type=Path, default=Path.home() / 'hummer-uefi',
                        help='checkout of uefi_oneplus_ace5_pro; fetched at the pinned commit when missing')
    args = parser.parse_args()
    checkout(args.tree)
    copy_binaries(args.tree)
    patch_binaries(args.tree)
    port_platform(args.tree)
    port_windows_boot(args.tree)
    print(f'pista tree ready in {args.tree}')


if __name__ == '__main__':
    main()
