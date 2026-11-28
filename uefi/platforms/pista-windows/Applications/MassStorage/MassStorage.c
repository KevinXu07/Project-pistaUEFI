// SPDX-License-Identifier: BSD-2-Clause-Patent
// Lends a RAM disk to the computer over USB, then boots Windows from it.
//
// Replaces hummer's application, which lent the whole internal UFS LUN 0 to
// the computer for writing. Here nothing on the phone's storage is exposed:
// the computer sees an empty disk held in RAM, copies the Windows boot files
// onto it, and after Volume Up the Windows loader boots \EFI\BOOT\BOOTAA64.EFI
// from it. The disk lives only until the phone restarts.
//
// The block I/O code belongs to this image, so the disk is used only while
// this application runs: it starts the Windows loader itself and never
// returns to the menu with the disk still installed.
#include <Uefi.h>
#include <Library/BaseLib.h>
#include <Library/BaseMemoryLib.h>
#include <Library/DevicePathLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiLib.h>
#include <Protocol/BlockIo.h>
#include <Protocol/DevicePath.h>
#include <Protocol/EFIUsbMsd.h>
#include <Protocol/GraphicsOutput.h>
#include <Protocol/LoadedImage.h>
#include <BootOptions.h>
#include <PistaRamDisk.h>

// The Windows 11 ARM64 setup files without install.wim take about 1.1 GB.
#define RAM_DISK_BYTES  (SIZE_1GB + SIZE_512MB)
#define RAM_DISK_BLOCK  512

typedef struct {
  VENDOR_DEVICE_PATH       Vendor;
  EFI_DEVICE_PATH_PROTOCOL End;
} RAM_DISK_DEVICE_PATH;

STATIC UINT8                 *mDisk;
STATIC EFI_BLOCK_IO_MEDIA    mMedia;
STATIC EFI_BLOCK_IO_PROTOCOL mBlockIo;
STATIC RAM_DISK_DEVICE_PATH  mPath = {
  { { HARDWARE_DEVICE_PATH, HW_VENDOR_DP, { sizeof (VENDOR_DEVICE_PATH), 0 } }, PISTA_RAM_DISK_GUID },
  { END_DEVICE_PATH_TYPE, END_ENTIRE_DEVICE_PATH_SUBTYPE, { sizeof (EFI_DEVICE_PATH_PROTOCOL), 0 } }
};
STATIC EFI_GUID              mWindowsBootGuid = HUMMER_GENERAL_BOOT_FILE_GUID;

STATIC EFI_STATUS
CheckRange (UINT32 MediaId, EFI_LBA Lba, UINTN Size, VOID *Buffer)
{
  if (MediaId != mMedia.MediaId) {
    return EFI_MEDIA_CHANGED;
  }
  if (Buffer == NULL || (Size % RAM_DISK_BLOCK) != 0) {
    return EFI_BAD_BUFFER_SIZE;
  }
  if (Lba > mMedia.LastBlock || Size / RAM_DISK_BLOCK > mMedia.LastBlock + 1 - Lba) {
    return EFI_INVALID_PARAMETER;
  }
  return EFI_SUCCESS;
}

STATIC EFI_STATUS EFIAPI
RamReset (EFI_BLOCK_IO_PROTOCOL *This, BOOLEAN ExtendedVerification)
{
  return EFI_SUCCESS;
}

STATIC EFI_STATUS EFIAPI
RamRead (EFI_BLOCK_IO_PROTOCOL *This, UINT32 MediaId, EFI_LBA Lba, UINTN Size, VOID *Buffer)
{
  EFI_STATUS Status = CheckRange (MediaId, Lba, Size, Buffer);

  if (!EFI_ERROR (Status)) {
    CopyMem (Buffer, mDisk + MultU64x32 (Lba, RAM_DISK_BLOCK), Size);
  }
  return Status;
}

STATIC EFI_STATUS EFIAPI
RamWrite (EFI_BLOCK_IO_PROTOCOL *This, UINT32 MediaId, EFI_LBA Lba, UINTN Size, VOID *Buffer)
{
  EFI_STATUS Status = CheckRange (MediaId, Lba, Size, Buffer);

  if (!EFI_ERROR (Status)) {
    CopyMem (mDisk + MultU64x32 (Lba, RAM_DISK_BLOCK), Buffer, Size);
  }
  return Status;
}

STATIC EFI_STATUS EFIAPI
RamFlush (EFI_BLOCK_IO_PROTOCOL *This)
{
  return EFI_SUCCESS;
}

STATIC VOID
ShowStatus (CONST CHAR16 *First, CONST CHAR16 *Second)
{
  EFI_GRAPHICS_OUTPUT_PROTOCOL  *Graphics;
  EFI_GRAPHICS_OUTPUT_BLT_PIXEL Black;
  UINTN                         Columns = 80;
  UINTN                         Rows    = 25;

  ZeroMem (&Black, sizeof (Black));
  if (!EFI_ERROR (gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&Graphics))) {
    Graphics->Blt (Graphics, &Black, EfiBltVideoFill, 0, 0, 0, 0,
                   Graphics->Mode->Info->HorizontalResolution, Graphics->Mode->Info->VerticalResolution, 0);
  }
  gST->ConOut->ClearScreen (gST->ConOut);
  gST->ConOut->QueryMode (gST->ConOut, gST->ConOut->Mode->Mode, &Columns, &Rows);
  gST->ConOut->SetAttribute (gST->ConOut, EFI_TEXT_ATTR (EFI_LIGHTGRAY, EFI_BLACK));
  gST->ConOut->SetCursorPosition (gST->ConOut, (Columns - StrLen (First)) / 2, Rows / 2 - 1);
  gST->ConOut->OutputString (gST->ConOut, (CHAR16 *)First);
  gST->ConOut->SetCursorPosition (gST->ConOut, (Columns - StrLen (Second)) / 2, Rows / 2 + 1);
  gST->ConOut->OutputString (gST->ConOut, (CHAR16 *)Second);
}

STATIC BOOLEAN
VolumeUpPressed (VOID)
{
  EFI_INPUT_KEY Key;

  ZeroMem (&Key, sizeof (Key));
  return gST->ConIn != NULL && !EFI_ERROR (gST->ConIn->ReadKeyStroke (gST->ConIn, &Key)) &&
         Key.ScanCode == SCAN_UP;
}

STATIC EFI_STATUS
InstallRamDisk (OUT EFI_HANDLE *Handle)
{
  mDisk = AllocatePages (EFI_SIZE_TO_PAGES (RAM_DISK_BYTES));
  if (mDisk == NULL) {
    return EFI_OUT_OF_RESOURCES;
  }
  // An empty first MiB shows the computer a disk without a partition table.
  ZeroMem (mDisk, SIZE_1MB);
  mMedia.MediaId       = 1;
  mMedia.RemovableMedia = TRUE;
  mMedia.MediaPresent  = TRUE;
  mMedia.BlockSize     = RAM_DISK_BLOCK;
  mMedia.LastBlock     = RAM_DISK_BYTES / RAM_DISK_BLOCK - 1;
  mBlockIo.Revision    = EFI_BLOCK_IO_PROTOCOL_REVISION;
  mBlockIo.Media       = &mMedia;
  mBlockIo.Reset       = RamReset;
  mBlockIo.ReadBlocks  = RamRead;
  mBlockIo.WriteBlocks = RamWrite;
  mBlockIo.FlushBlocks = RamFlush;
  *Handle = NULL;
  return gBS->InstallMultipleProtocolInterfaces (Handle, &gEfiBlockIoProtocolGuid, &mBlockIo,
                                                 &gEfiDevicePathProtocolGuid, &mPath, NULL);
}

// Lends the disk until Volume Up. The file system drivers are connected only
// afterwards, so the firmware never caches data the computer is changing.
STATIC EFI_STATUS
LendToComputer (VOID)
{
  EFI_USB_MSD_PROTOCOL *UsbMsd;
  EFI_STATUS           Status;
  EFI_STATUS           EventStatus;

  Status = gBS->LocateProtocol (&gEfiUsbMsdProtocolGuid, NULL, (VOID **)&UsbMsd);
  if (EFI_ERROR (Status)) {
    return Status;
  }
  Status = UsbMsd->AssignBlkIoHandle (UsbMsd, &mBlockIo, 0);
  if (EFI_ERROR (Status)) {
    return Status;
  }
  Status = UsbMsd->StartDevice (UsbMsd);
  if (!EFI_ERROR (Status)) {
    ShowStatus (L"RAM disk connected to the computer (1.5 GiB)",
                L"Copy the Windows files, eject it, then press Volume Up");
    while (!VolumeUpPressed ()) {
      EventStatus = UsbMsd->EventHandler (UsbMsd);
      if (EFI_ERROR (EventStatus) && EventStatus != EFI_NOT_READY) {
        Status = EventStatus;
        break;
      }
      gBS->Stall (100);
    }
    UsbMsd->StopDevice (UsbMsd);
  }
  UsbMsd->AssignBlkIoHandle (UsbMsd, NULL, 0);
  return Status;
}

STATIC EFI_STATUS
StartWindowsLoader (IN EFI_HANDLE ImageHandle)
{
  EFI_LOADED_IMAGE_PROTOCOL         *Self;
  EFI_LOADED_IMAGE_PROTOCOL         *Loader;
  MEDIA_FW_VOL_FILEPATH_DEVICE_PATH FileNode;
  EFI_DEVICE_PATH_PROTOCOL          *Path;
  EFI_HANDLE                        Child;
  EFI_STATUS                        Status;
  STATIC CHAR8                      Option[] = "RAM";

  Status = gBS->HandleProtocol (ImageHandle, &gEfiLoadedImageProtocolGuid, (VOID **)&Self);
  if (EFI_ERROR (Status)) {
    return Status;
  }
  EfiInitializeFwVolDevicepathNode (&FileNode, &mWindowsBootGuid);
  Path = AppendDevicePathNode (DevicePathFromHandle (Self->DeviceHandle), (EFI_DEVICE_PATH_PROTOCOL *)&FileNode);
  if (Path == NULL) {
    return EFI_OUT_OF_RESOURCES;
  }
  Status = gBS->LoadImage (FALSE, ImageHandle, Path, NULL, 0, &Child);
  FreePool (Path);
  if (EFI_ERROR (Status)) {
    return Status;
  }
  gBS->HandleProtocol (Child, &gEfiLoadedImageProtocolGuid, (VOID **)&Loader);
  Loader->LoadOptions     = Option;
  Loader->LoadOptionsSize = sizeof (Option);
  return gBS->StartImage (Child, NULL, NULL);
}

EFI_STATUS EFIAPI
MassStorageEntry (IN EFI_HANDLE ImageHandle, IN EFI_SYSTEM_TABLE *SystemTable)
{
  EFI_HANDLE    Disk;
  EFI_STATUS    Status;
  EFI_INPUT_KEY Key;

  gBS->SetWatchdogTimer (0, 0, 0, NULL);
  gST->ConOut->EnableCursor (gST->ConOut, FALSE);
  while (gST->ConIn != NULL && !EFI_ERROR (gST->ConIn->ReadKeyStroke (gST->ConIn, &Key))) {
  }
  Status = InstallRamDisk (&Disk);
  if (!EFI_ERROR (Status)) {
    Status = LendToComputer ();
  }
  if (!EFI_ERROR (Status)) {
    ShowStatus (L"Starting Windows from the RAM disk", L"");
    gBS->ConnectController (Disk, NULL, NULL, TRUE);
    Status = StartWindowsLoader (ImageHandle);
  }
  // Only reached when Windows did not start. Long-press Power to restart.
  Print (L"\r\nWindows did not start from the RAM disk: %r\r\n", Status);
  Print (L"Check that \\EFI\\BOOT\\BOOTAA64.EFI is on the disk. Long-press Power to restart.\r\n");
  CpuDeadLoop ();
  return Status;
}
