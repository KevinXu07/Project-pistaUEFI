// SPDX-License-Identifier: BSD-2-Clause-Patent
#include <Uefi.h>
#include <Library/DeviceBootManagerLib.h>
#include <Library/UefiLib.h>
#include <Library/UefiBootServicesTableLib.h>
#include <Library/UefiRuntimeServicesTableLib.h>
#include <Library/DebugLib.h>
#include <Library/BaseLib.h>
#include <Library/DevicePathLib.h>
#include <Library/MemoryAllocationLib.h>
#include <Protocol/GraphicsOutput.h>
#include <Protocol/LoadedImage.h>
#include <Protocol/FirmwareVolume2.h>
#include "NativeModules.h"

STATIC EFI_EVENT mReturnToAndroidEvent;

// Returns the UI name of a driver loaded from a firmware volume, or NULL.
// The caller frees the returned string.
STATIC CHAR16 *LoadedImageName (IN EFI_HANDLE Handle)
{
  EFI_LOADED_IMAGE_PROTOCOL     *Image;
  EFI_FIRMWARE_VOLUME2_PROTOCOL *Fv;
  CONST EFI_GUID                *File;
  CHAR16                        *Name = NULL;
  UINTN                         Size = 0;
  UINT32                        Authentication;

  if (EFI_ERROR (gBS->HandleProtocol (Handle, &gEfiLoadedImageProtocolGuid, (VOID **)&Image)) ||
      (Image->FilePath == NULL)) {
    return NULL;
  }
  File = EfiGetNameGuidFromFwVolDevicePathNode ((CONST MEDIA_FW_VOL_FILEPATH_DEVICE_PATH *)Image->FilePath);
  if ((File == NULL) ||
      EFI_ERROR (gBS->HandleProtocol (Image->DeviceHandle, &gEfiFirmwareVolume2ProtocolGuid, (VOID **)&Fv)) ||
      EFI_ERROR (Fv->ReadSection (Fv, File, EFI_SECTION_USER_INTERFACE, 0, (VOID **)&Name, &Size, &Authentication))) {
    return NULL;
  }
  return Name;
}

// A stock driver counts as running when its image is still loaded: DXE core
// unloads drivers whose entry point fails, and never loads drivers whose
// DEPEX stays unsatisfied.
STATIC VOID PrintNativeDriverSummary (VOID)
{
  EFI_HANDLE *Handles;
  UINTN      Count;
  UINTN      Index;
  UINTN      Module;
  UINTN      Running = 0;
  BOOLEAN    Seen[ARRAY_SIZE (mNativeModules)];
  CHAR16     *Name;

  for (Module = 0; Module < ARRAY_SIZE (mNativeModules); Module++) {
    Seen[Module] = FALSE;
  }
  if (EFI_ERROR (gBS->LocateHandleBuffer (ByProtocol, &gEfiLoadedImageProtocolGuid, NULL, &Count, &Handles))) {
    Print (L"Stock drivers: loaded image list unavailable\r\n");
    return;
  }
  for (Index = 0; Index < Count; Index++) {
    Name = LoadedImageName (Handles[Index]);
    if (Name == NULL) {
      continue;
    }
    for (Module = 0; Module < ARRAY_SIZE (mNativeModules); Module++) {
      if (StrCmp (Name, mNativeModules[Module]) == 0) {
        Seen[Module] = TRUE;
      }
    }
    FreePool (Name);
  }
  FreePool (Handles);
  for (Module = 0; Module < ARRAY_SIZE (mNativeModules); Module++) {
    if (Seen[Module]) {
      Running++;
    }
  }
  Print (L"Stock drivers running: %u/%u\r\n", (UINT32)Running, (UINT32)ARRAY_SIZE (mNativeModules));
  for (Module = 0; Module < ARRAY_SIZE (mNativeModules); Module++) {
    if (!Seen[Module]) {
      Print (L"  not running: %s\r\n", mNativeModules[Module]);
    }
  }
}

STATIC VOID EFIAPI ReturnToAndroid (IN EFI_EVENT Event, IN VOID *Context)
{
  DEBUG ((DEBUG_WARN, "PISTA_STAGE0_RETURN_TO_ANDROID\n"));
  gRT->ResetSystem (EfiResetCold, EFI_SUCCESS, 0, NULL);
}

EFI_HANDLE EFIAPI DeviceBootManagerBeforeConsole (
  OUT EFI_DEVICE_PATH_PROTOCOL **DevicePath,
  OUT BDS_CONSOLE_CONNECT_ENTRY **PlatformConsoles)
{
  if (DevicePath != NULL) { *DevicePath = NULL; }
  if (PlatformConsoles != NULL) { *PlatformConsoles = NULL; }
  return NULL;
}

EFI_DEVICE_PATH_PROTOCOL **EFIAPI DeviceBootManagerAfterConsole (VOID)
{
  EFI_GRAPHICS_OUTPUT_PROTOCOL *Gop = NULL;
  EFI_STATUS Status;
  UINT64 CurrentEl;

  __asm__ volatile ("mrs %0, CurrentEL" : "=r" (CurrentEl));
  Status = gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&Gop);
  Print (L"\r\nSunUEFI: Realme GT7 Pro / rmx5010 / SM8750-AB\r\n");
  Print (L"Stage 0 diagnostic: DXE and console reached, EL%u\r\n", (UINT32)(CurrentEl >> 2));
  Print (L"Internal storage and USB mass-storage drivers are excluded.\r\n");
  Print (L"Linux and Windows PE boot are not implemented in this image.\r\n");
  if (!EFI_ERROR (Status) && Gop != NULL && Gop->Mode != NULL && Gop->Mode->Info != NULL) {
    Print (L"GOP %ux%u, stride %u, framebuffer 0x%lx\r\n",
      Gop->Mode->Info->HorizontalResolution, Gop->Mode->Info->VerticalResolution,
      Gop->Mode->Info->PixelsPerScanLine, Gop->Mode->FrameBufferBase);
  }
  PrintNativeDriverSummary ();
  // This cannot recover a hang that happens before the console callback.
  Status = gBS->CreateEvent (EVT_TIMER | EVT_NOTIFY_SIGNAL, TPL_CALLBACK,
                           ReturnToAndroid, NULL, &mReturnToAndroidEvent);
  if (!EFI_ERROR (Status)) {
    Status = gBS->SetTimer (mReturnToAndroidEvent, TimerRelative, 45ULL * 10000000ULL);
    if (!EFI_ERROR (Status)) {
      Print (L"Automatic cold reboot requested in 45 seconds.\r\n");
    } else {
      gBS->CloseEvent (mReturnToAndroidEvent);
      mReturnToAndroidEvent = NULL;
    }
  }
  DEBUG ((DEBUG_WARN, "PISTA_STAGE0_CONSOLE_READY EL%u\n", (UINT32)(CurrentEl >> 2)));
  return NULL;
}

EFI_DEVICE_PATH_PROTOCOL **EFIAPI DeviceBootManagerOnDemandConInConnect (VOID) { return NULL; }
VOID EFIAPI DeviceBootManagerProcessBootCompletion (OUT EFI_BOOT_MANAGER_LOAD_OPTION *BootOption) { }
EFI_STATUS EFIAPI DeviceBootManagerPriorityBoot (IN OUT EFI_BOOT_MANAGER_LOAD_OPTION *BootOption) { return EFI_NOT_FOUND; }
VOID EFIAPI DeviceBootManagerUnableToBoot (VOID) { }
VOID EFIAPI DeviceBootManagerBdsEntry (VOID) { }
