// SPDX-License-Identifier: BSD-2-Clause-Patent
// RAM copy of the firmware log for "fastboot oem ramlog".
//
// The ring lives in UEFI_RamLog, the last 2 MiB that tools/dev/prepare_pista.py
// splits off DXE_Heap and maps uncached. Unlike piano, pista has no ramoops
// region at a fixed address, so the firmware owns this ring and starts it
// empty on every boot. The header layout matches the reader in
// PianoDwc3Device.c: signature, write offset, valid bytes, then the data.
#include <Uefi.h>
#include <Library/ArmLib.h>

#define RAM_LOG_BASE      0xD7E00000U
#define RAM_LOG_BYTES     0x200000U
#define RAM_LOG_SIGNATURE 0x43474244U

typedef struct {
  volatile UINT32 Signature;
  volatile UINT32 Start;
  volatile UINT32 Size;
  volatile UINT8  Data[];
} RAM_CONSOLE;

// SEC runs from the FD image that BootShim copies on every boot, so this
// flag starts FALSE once per boot there. DXE modules run with the MMU on and
// never take the reset branch.
STATIC BOOLEAN mSecRingStarted;

STATIC VOID Append (RAM_CONSOLE *Ring, UINT32 Capacity, CONST UINT8 *Buffer, UINTN Length)
{
  UINT32 Start = Ring->Start;
  UINT32 Size  = Ring->Size;

  if (Length > Capacity) {
    Buffer += Length - Capacity;
    Length  = Capacity;
  }
  // Byte stores: with the MMU off, SEC sees this RAM as device memory.
  for (UINTN Index = 0; Index < Length; Index++) {
    Ring->Data[(Start + Index) % Capacity] = Buffer[Index];
  }
  Ring->Start = (UINT32)((Start + Length) % Capacity);
  Ring->Size  = (UINT32)MIN ((UINTN)Capacity, (UINTN)Size + Length);
}

VOID RamLogWrite (IN CONST UINT8 *Buffer, IN UINTN Length)
{
  STATIC CONST UINT8 Marker[] = "SUNUEFI_RAMLOG_BEGIN\n";
  RAM_CONSOLE  *Ring     = (RAM_CONSOLE *)(UINTN)RAM_LOG_BASE;
  CONST UINT32 Capacity  = RAM_LOG_BYTES - sizeof (RAM_CONSOLE);
  BOOLEAN      Interrupts;

  if ((Buffer == NULL) || (Length == 0)) {
    return;
  }
  Interrupts = ArmGetInterruptState ();
  if (Interrupts) {
    ArmDisableInterrupts ();
  }
  if ((!ArmMmuEnabled () && !mSecRingStarted) ||
      (Ring->Signature != RAM_LOG_SIGNATURE) || (Ring->Start >= Capacity) || (Ring->Size > Capacity)) {
    Ring->Signature = RAM_LOG_SIGNATURE;
    Ring->Start     = 0;
    Ring->Size      = 0;
    mSecRingStarted = TRUE;
    Append (Ring, Capacity, Marker, sizeof (Marker) - 1);
  }
  Append (Ring, Capacity, Buffer, Length);
  if (Interrupts) {
    ArmEnableInterrupts ();
  }
}
