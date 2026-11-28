// SPDX-License-Identifier: BSD-2-Clause-Patent
// Gives Windows the DDR that ABL lists in /memory, minus the regions its
// device tree lists under /reserved-memory and the DDR below LOW_DDR_END.
//
// ABL's /memory also covers regions that Linux keeps for the platform through
// /reserved-memory, including the ones without no-map such as the QDSS trace
// buffer at 0x82800000. Windows does not read the device tree, so these
// regions become reserved memory here.
//
// Below LOW_DDR_END, where mpss_region begins, ABL's /memory leaves only
// small gaps between platform regions (about 6 MiB in all). On pista the
// Windows boot manager stopped without an exception after placing code there,
// and Windows Setup started only once all of it was reserved, so none of it
// is given to Windows.
#include <Uefi.h>
#include <Guid/FdtHob.h>
#include <Library/BaseLib.h>
#include <Library/DebugLib.h>
#include <Library/FdtLib.h>
#include <Library/HobLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiBootServicesTableLib.h>

UINT64 PistaAddDdrFromFdt (CONST VOID *Fdt);

#define LOW_DDR_END  0x8BA00000ULL

STATIC UINT64
ReadCells (CONST UINT32 *Cells, INT32 Count)
{
  UINT64 Value = 0;

  for (INT32 Index = 0; Index < Count; Index++) {
    Value = LShiftU64 (Value, 32) | Fdt32ToCpu (Cells[Index]);
  }
  return Value;
}

// Turns the conventional memory inside [Base, End) into reserved memory.
STATIC UINT64
ReserveRange (CONST EFI_MEMORY_DESCRIPTOR *Map, UINTN MapSize, UINTN Stride, UINT64 Base, UINT64 End)
{
  UINT64 Reserved = 0;

  for (UINTN Offset = 0; Offset < MapSize; Offset += Stride) {
    CONST EFI_MEMORY_DESCRIPTOR *Region = (CONST EFI_MEMORY_DESCRIPTOR *)((CONST UINT8 *)Map + Offset);
    EFI_PHYSICAL_ADDRESS        Start   = MAX (Base, Region->PhysicalStart);
    EFI_PHYSICAL_ADDRESS        Stop    = MIN (End, Region->PhysicalStart + EFI_PAGES_TO_SIZE (Region->NumberOfPages));
    EFI_STATUS                  Status;

    if (Region->Type != EfiConventionalMemory || Start >= Stop) {
      continue;
    }
    Status = gBS->AllocatePages (AllocateAddress, EfiReservedMemoryType, EFI_SIZE_TO_PAGES (Stop - Start), &Start);
    DEBUG ((DEBUG_WARN, "RamManager: reserve 0x%lx..0x%lx %r\n", Start, Stop, Status));
    if (!EFI_ERROR (Status)) {
      Reserved += Stop - Start;
    }
  }
  return Reserved;
}

STATIC VOID
ReserveRegionsForWindows (CONST VOID *Fdt)
{
  EFI_MEMORY_DESCRIPTOR *Map = NULL;
  UINTN                 MapSize = 0;
  UINTN                 Key;
  UINTN                 Stride;
  UINT32                Version;
  UINT64                Reserved = 0;
  INT32                 Parent;
  INT32                 AddressCells;
  INT32                 SizeCells;

  // The snapshot lists the conventional memory before any reservation. The
  // low range goes first because it takes whole snapshot entries; a region
  // reserved twice fails the second time and stays reserved.
  if (gBS->GetMemoryMap (&MapSize, NULL, &Key, &Stride, &Version) != EFI_BUFFER_TOO_SMALL) {
    return;
  }
  MapSize += 8 * Stride;
  Map = AllocatePool (MapSize);
  if (Map == NULL || EFI_ERROR (gBS->GetMemoryMap (&MapSize, Map, &Key, &Stride, &Version))) {
    DEBUG ((DEBUG_WARN, "RamManager: memory map unavailable\n"));
    if (Map != NULL) {
      FreePool (Map);
    }
    return;
  }
  Reserved += ReserveRange (Map, MapSize, Stride, 0, LOW_DDR_END);
  Parent       = FdtPathOffset (Fdt, "/reserved-memory");
  AddressCells = (Parent < 0) ? 0 : FdtAddressCells (Fdt, Parent);
  SizeCells    = (Parent < 0) ? 0 : FdtSizeCells (Fdt, Parent);
  if (AddressCells < 1 || AddressCells > 2 || SizeCells < 1 || SizeCells > 2) {
    DEBUG ((DEBUG_WARN, "RamManager: no usable /reserved-memory (cells %d/%d)\n", AddressCells, SizeCells));
    Parent = -1;
  }
  for (INT32 Node = (Parent < 0) ? -1 : FdtFirstSubnode (Fdt, Parent); Node >= 0; Node = FdtNextSubnode (Fdt, Node)) {
    CONST CHAR8  *State = FdtGetProp (Fdt, Node, "status", NULL);
    CONST UINT32 *Reg;
    INT32        Length;

    if (State != NULL && AsciiStrCmp (State, "okay") != 0 && AsciiStrCmp (State, "ok") != 0) {
      continue;
    }
    Reg = FdtGetProp (Fdt, Node, "reg", &Length);
    for (INT32 At = 0; Reg != NULL && At + (AddressCells + SizeCells) * 4 <= Length; At += (AddressCells + SizeCells) * 4) {
      UINT64 Base = ReadCells (Reg + At / 4, AddressCells);
      UINT64 Size = ReadCells (Reg + At / 4 + AddressCells, SizeCells);

      if (Size != 0 && Base <= MAX_UINT64 - Size) {
        Reserved += ReserveRange (Map, MapSize, Stride, Base & ~(UINT64)EFI_PAGE_MASK,
                                  ALIGN_VALUE (Base + Size, EFI_PAGE_SIZE));
      }
    }
  }
  FreePool (Map);
  DEBUG ((DEBUG_WARN, "RamManager: %lu KiB kept from Windows\n", Reserved >> 10));
}

EFI_STATUS EFIAPI
ManageRam (IN EFI_HANDLE ImageHandle, IN EFI_SYSTEM_TABLE *SystemTable)
{
  EFI_HOB_GUID_TYPE *Hob;
  CONST VOID        *Fdt;

  Hob = GetFirstGuidHob (&gFdtHobGuid);
  if (Hob == NULL || GET_GUID_HOB_DATA_SIZE (Hob) != sizeof (UINT64)) {
    DEBUG ((DEBUG_WARN, "RamManager: ABL did not provide a device tree\n"));
    return EFI_SUCCESS;
  }
  Fdt = (CONST VOID *)(UINTN)ReadUnaligned64 (GET_GUID_HOB_DATA (Hob));
  // PistaAddDdrFromFdt checks the device tree before reading it and adds
  // nothing when it is invalid; its reserved regions are then not read.
  if (PistaAddDdrFromFdt (Fdt) != 0) {
    ReserveRegionsForWindows (Fdt);
  }
  return EFI_SUCCESS;
}
