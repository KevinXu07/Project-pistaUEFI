// SPDX-License-Identifier: BSD-2-Clause-Patent
// Adds the DDR that ABL lists in the /memory node of its device tree to the
// UEFI memory map, so an operating system started from this firmware gets
// all of the phone's RAM and not only the regions UEFI itself runs in.
//
// The generated memory map describes only what UEFI uses; ABL fills /memory
// from the DDR partition table at every boot, so it also follows the RAM size
// of the phone. Ranges named in the generated map keep their type there, and
// only parts that UEFI does not describe at all become conventional memory.
#include <Uefi.h>
#include <Library/BaseLib.h>
#include <Library/DebugLib.h>
#include <Library/DxeServicesTableLib.h>
#include <Library/FdtLib.h>
#include <Library/MemoryMapLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Protocol/Cpu.h>

// Written by the SunUEFI BootShim: [0] magic, [1] the FDT address from ABL.
#define HANDOFF_RECORD_ADDRESS 0xA7FFF000U
#define HANDOFF_RECORD_MAGIC   0x534E554546494448ULL
#define ABL_FDT_MAX_BYTES      0x200000U
// The capabilities and the mapping of the stock system memory regions.
#define DDR_CAPABILITIES (EFI_MEMORY_UC | EFI_MEMORY_WC | EFI_MEMORY_WT | EFI_MEMORY_WB | \
                          EFI_MEMORY_RP | EFI_MEMORY_WP | EFI_MEMORY_XP)
#define DDR_ATTRIBUTES   (EFI_MEMORY_WB | EFI_MEMORY_XP)

STATIC EFI_CPU_ARCH_PROTOCOL *mCpu;
STATIC UINT64                mAddedBytes;

// TRUE when [Base, Base + Length) lies in one system memory range that UEFI
// already maps, so reading it cannot fault.
STATIC BOOLEAN InSystemMemory (UINT64 Base, UINT64 Length)
{
  EFI_GCD_MEMORY_SPACE_DESCRIPTOR Descriptor;

  if ((Length == 0) || (Base > MAX_UINT64 - Length) ||
      EFI_ERROR (gDS->GetMemorySpaceDescriptor (Base, &Descriptor))) {
    return FALSE;
  }
  return (Descriptor.GcdMemoryType == EfiGcdMemoryTypeSystemMemory) &&
         (Base + Length <= Descriptor.BaseAddress + Descriptor.Length);
}

// Maps [Base, End) before adding it: the memory services may hand out the
// new pages as soon as AddMemorySpace returns.
STATIC VOID AddRange (UINT64 Base, UINT64 End)
{
  EFI_STATUS Status;

  Status = mCpu->SetMemoryAttributes (mCpu, Base, End - Base, DDR_ATTRIBUTES);
  if (!EFI_ERROR (Status)) {
    Status = gDS->AddMemorySpace (EfiGcdMemoryTypeSystemMemory, Base, End - Base, DDR_CAPABILITIES);
  }
  if (!EFI_ERROR (Status)) {
    Status = gDS->SetMemorySpaceAttributes (Base, End - Base, DDR_ATTRIBUTES);
  }
  DEBUG ((DEBUG_WARN, "PISTA_DDR_ADD 0x%lx..0x%lx %r\n", Base, End, Status));
  if (!EFI_ERROR (Status)) {
    mAddedBytes += End - Base;
  }
}

// Adds the parts of [Base, End) that the GCD map still lists as nonexistent.
STATIC VOID AddNonexistent (UINT64 Base, UINT64 End)
{
  EFI_GCD_MEMORY_SPACE_DESCRIPTOR Descriptor;
  UINT64                          Top;

  while (Base < End) {
    if (EFI_ERROR (gDS->GetMemorySpaceDescriptor (Base, &Descriptor))) {
      return;
    }
    Top = MIN (End, Descriptor.BaseAddress + Descriptor.Length);
    if (Descriptor.GcdMemoryType == EfiGcdMemoryTypeNonExistent) {
      AddRange (Base, Top);
    }
    Base = Top;
  }
}

// Adds [Base, End) without any part that the generated memory map names,
// including the regions it lists without a HOB.
STATIC VOID AddOutsideMemoryMap (UINT64 Base, UINT64 End)
{
  EFI_MEMORY_REGION_DESCRIPTOR *Map;
  UINT8                        Count;
  UINT64                       Next;
  BOOLEAN                      Covered;

  GetMemoryMap (&Map, &Count);
  while (Base < End) {
    Next    = End;
    Covered = FALSE;
    for (UINT8 Index = 0; Index < Count; Index++) {
      UINT64 Start = Map[Index].Address;
      UINT64 Stop  = Map[Index].Address + Map[Index].Length;
      if ((Start <= Base) && (Base < Stop)) {
        Base    = Stop;
        Covered = TRUE;
        break;
      }
      if ((Base < Start) && (Start < Next)) {
        Next = Start;
      }
    }
    if (!Covered) {
      AddNonexistent (Base, Next);
      Base = Next;
    }
  }
}

STATIC UINT64 ReadCells (CONST UINT32 *Cells, UINT32 Count)
{
  UINT64 Value = 0;

  for (UINT32 Index = 0; Index < Count; Index++) {
    Value = LShiftU64 (Value, 32) | Fdt32ToCpu (Cells[Index]);
  }
  return Value;
}

UINT64 PistaAddDdrFromFdt (CONST VOID *Fdt)
{
  EFI_PHYSICAL_ADDRESS  Address = (UINTN)Fdt;
  CONST UINT32          *Reg;
  INT32                 Node;
  INT32                 Length;
  INT32                 AddressCells;
  INT32                 SizeCells;
  EFI_STATUS            Status;

  mAddedBytes = 0;
  Status = gBS->LocateProtocol (&gEfiCpuArchProtocolGuid, NULL, (VOID **)&mCpu);
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_WARN, "PISTA_DDR no CPU protocol %r\n", Status));
    return 0;
  }
  if (!InSystemMemory (Address, 40)) {
    DEBUG ((DEBUG_WARN, "PISTA_DDR no ABL device tree\n"));
    return 0;
  }
  if ((FdtCheckHeader (Fdt) != 0) || (FdtTotalSize (Fdt) > ABL_FDT_MAX_BYTES) ||
      !InSystemMemory (Address, FdtTotalSize (Fdt))) {
    DEBUG ((DEBUG_WARN, "PISTA_DDR invalid ABL device tree at 0x%lx\n", Address));
    return 0;
  }
  AddressCells = FdtAddressCells (Fdt, 0);
  SizeCells    = FdtSizeCells (Fdt, 0);
  if ((AddressCells < 1) || (AddressCells > 2) || (SizeCells < 1) || (SizeCells > 2)) {
    DEBUG ((DEBUG_WARN, "PISTA_DDR unsupported cells %d/%d\n", AddressCells, SizeCells));
    return 0;
  }
  for (Node = FdtFirstSubnode (Fdt, 0); Node >= 0; Node = FdtNextSubnode (Fdt, Node)) {
    CONST CHAR8 *Type = FdtGetProp (Fdt, Node, "device_type", &Length);
    if ((Type == NULL) || (Length != sizeof ("memory")) || (AsciiStrCmp (Type, "memory") != 0)) {
      continue;
    }
    Reg = FdtGetProp (Fdt, Node, "reg", &Length);
    if ((Reg == NULL) || (Length <= 0)) {
      continue;
    }
    for (INT32 At = 0; At + (AddressCells + SizeCells) * 4 <= Length; At += (AddressCells + SizeCells) * 4) {
      CONST UINT32 *Entry = Reg + At / 4;
      UINT64       Base   = ReadCells (Entry, (UINT32)AddressCells);
      UINT64       Size   = ReadCells (Entry + AddressCells, (UINT32)SizeCells);
      UINT64       Start  = ALIGN_VALUE (Base, EFI_PAGE_SIZE);
      UINT64       End;
      if ((Size == 0) || (Base > MAX_UINT64 - Size)) {
        continue;
      }
      End = (Base + Size) & ~(UINT64)EFI_PAGE_MASK;
      DEBUG ((DEBUG_WARN, "PISTA_DDR_ABL 0x%lx..0x%lx\n", Base, Base + Size));
      if (Start < End) {
        AddOutsideMemoryMap (Start, End);
      }
    }
  }
  DEBUG ((DEBUG_WARN, "PISTA_DDR_ADDED %lu MiB\n", mAddedBytes >> 20));
  return mAddedBytes;
}

UINT64 PistaAddDdrFromAbl (VOID)
{
  CONST volatile UINT64 *Record = (CONST volatile UINT64 *)(UINTN)HANDOFF_RECORD_ADDRESS;

  if (Record[0] != HANDOFF_RECORD_MAGIC) {
    DEBUG ((DEBUG_WARN, "PISTA_DDR no ABL device tree\n"));
    return 0;
  }
  return PistaAddDdrFromFdt ((CONST VOID *)(UINTN)Record[1]);
}
