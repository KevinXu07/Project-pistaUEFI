// SPDX-License-Identifier: BSD-2-Clause-Patent
// RAM copy of the firmware log for "fastboot oem ramlog".
//
// The ring lives in UEFI_RamLog, the last 2 MiB that tools/dev/prepare_pista.py
// splits off DXE_Heap and maps uncached. Unlike piano, pista has no ramoops
// region at a fixed address, so the firmware owns this ring and starts it
// empty on every boot. The header layout matches the reader in
// PianoDwc3Device.c: signature, write offset, valid bytes, then the data.
//
// Each line in the ring starts with "[seconds.milliseconds] " read from the
// architected counter, which runs from power-on; the screen copy has none.
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

// Per module: every module links its own copy of this library.
STATIC BOOLEAN mScreenCopyOff;

// Stops drawing this module's later log lines on the screen; they still go
// to the RAM log. The diagnostic boot manager calls it after its banner so
// the USB, UFS and SMMU traces do not scroll the banner away. Drawing a line
// costs about 10 ms, which made those traces delay Fastboot by over a minute.
VOID PistaRamLogSetScreenCopy (IN BOOLEAN Enabled)
{
  mScreenCopyOff = !Enabled;
}

STATIC VOID PutByte (RAM_CONSOLE *Ring, UINT32 Capacity, UINT8 Byte)
{
  // Byte stores: with the MMU off, SEC sees this RAM as device memory.
  Ring->Data[Ring->Start] = Byte;
  Ring->Start = (Ring->Start + 1) % Capacity;
  if (Ring->Size < Capacity) {
    Ring->Size++;
  }
}

// Writes "[sssss.mmm] " with the time since power-on.
STATIC VOID PutTimestamp (RAM_CONSOLE *Ring, UINT32 Capacity)
{
  UINT64 Count;
  UINT64 Frequency;
  UINT64 Milliseconds;
  UINT8  Text[12];
  UINTN  Index;

  __asm__ volatile ("isb; mrs %0, cntvct_el0" : "=r" (Count));
  __asm__ volatile ("mrs %0, cntfrq_el0" : "=r" (Frequency));
  Milliseconds = Frequency == 0 ? 0 : (Count / Frequency) * 1000 + (Count % Frequency) * 1000 / Frequency;
  Text[0]  = '[';
  Text[10] = ']';
  Text[11] = ' ';
  Text[6]  = '.';
  for (Index = 9; Index > 6; Index--) {
    Text[Index]   = (UINT8)('0' + Milliseconds % 10);
    Milliseconds /= 10;
  }
  for (Index = 5; Index > 0; Index--) {
    Text[Index]   = (Milliseconds != 0 || Index == 5) ? (UINT8)('0' + Milliseconds % 10) : ' ';
    Milliseconds /= 10;
  }
  for (Index = 0; Index < sizeof (Text); Index++) {
    PutByte (Ring, Capacity, Text[Index]);
  }
}

// Every DXE module links its own copy of this library, so the line start is
// read back from the ring instead of being kept in a module global.
STATIC BOOLEAN AtLineStart (RAM_CONSOLE *Ring, UINT32 Capacity)
{
  return Ring->Size == 0 || Ring->Data[(Ring->Start + Capacity - 1) % Capacity] == '\n';
}

STATIC VOID Append (RAM_CONSOLE *Ring, UINT32 Capacity, CONST UINT8 *Buffer, UINTN Length)
{
  for (UINTN Index = 0; Index < Length; Index++) {
    if (AtLineStart (Ring, Capacity)) {
      PutTimestamp (Ring, Capacity);
    }
    PutByte (Ring, Capacity, Buffer[Index]);
  }
}

// Returns TRUE when the caller should also draw the bytes on the screen.
BOOLEAN RamLogWrite (IN CONST UINT8 *Buffer, IN UINTN Length)
{
  STATIC CONST UINT8 Marker[] = "SUNUEFI_RAMLOG_BEGIN\n";
  RAM_CONSOLE  *Ring     = (RAM_CONSOLE *)(UINTN)RAM_LOG_BASE;
  CONST UINT32 Capacity  = RAM_LOG_BYTES - sizeof (RAM_CONSOLE);
  BOOLEAN      Interrupts;

  if ((Buffer == NULL) || (Length == 0)) {
    return !mScreenCopyOff;
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
    // The marker stays the first bytes of the ring: the reader looks for it
    // at a line start.
    for (UINTN Index = 0; Index < sizeof (Marker) - 1; Index++) {
      PutByte (Ring, Capacity, Marker[Index]);
    }
  }
  Append (Ring, Capacity, Buffer, Length);
  if (Interrupts) {
    ArmEnableInterrupts ();
  }
  return !mScreenCopyOff;
}
