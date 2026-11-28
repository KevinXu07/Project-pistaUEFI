// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <Uefi.h>
#include <Guid/FdtHob.h>
#include <Library/BaseLib.h>
#include <Library/DebugLib.h>
#include <Library/HobLib.h>

UINT64 PistaAddDdrFromFdt (CONST VOID *Fdt);

EFI_STATUS EFIAPI
ManageRam (IN EFI_HANDLE ImageHandle, IN EFI_SYSTEM_TABLE *SystemTable)
{
  EFI_HOB_GUID_TYPE *Hob;

  Hob = GetFirstGuidHob (&gFdtHobGuid);
  if (Hob == NULL || GET_GUID_HOB_DATA_SIZE (Hob) != sizeof (UINT64)) {
    DEBUG ((DEBUG_WARN, "RamManager: ABL did not provide a device tree\n"));
    return EFI_SUCCESS;
  }
  PistaAddDdrFromFdt ((CONST VOID *)(UINTN)ReadUnaligned64 (GET_GUID_HOB_DATA (Hob)));
  return EFI_SUCCESS;
}
