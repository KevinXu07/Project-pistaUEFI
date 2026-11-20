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
#include <Library/TimerLib.h>
#include <Library/BaseMemoryLib.h>
#include "NativeModules.h"
#include "PistaDeviceTree.h"
#include "PianoUsbService.h"
#include "PianoUsbStorageExperiment.h"
#include "PianoFastbootBlockRead.h"
#include "PianoUfsShutdown.h"
#include "PianoOwnedSmmu.h"
#include "../Protocol/PianoProductRuntime.h"

VOID PianoProbeUfs (CONST VOID *Fdt);
VOID PianoUfsSetProbeAction (EFI_STATUS (*Action)(CONST VOID *Fdt));
EFI_STATUS PianoUfsReadOnlyDmaExperiment (CONST VOID *Fdt);
CONST CHAR8 *PianoUfsStartupStage (VOID);
EFI_STATUS PianoStartFaultRecovery (VOID);

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

STATIC UINT64 mCounterStart;
STATIC UINT64 mCounterEnd;
STATIC UINT64 mCounterFrequency;

STATIC UINT64 FastbootNowUs (VOID *Context)
{
  UINT64 Counter = GetPerformanceCounter ();
  UINT64 Ticks = mCounterEnd >= mCounterStart ? Counter - mCounterStart : mCounterStart - Counter;
  UINT64 Remainder;
  UINT64 Seconds = DivU64x64Remainder (Ticks, mCounterFrequency, &Remainder);

  (VOID)Context;
  return Seconds * 1000000 + DivU64x64Remainder (Remainder * 1000000, mCounterFrequency, NULL);
}

// The piano PMIC read-only power probe is not part of the pista diagnostic.
VOID PianoProbeUsbPower (CONST VOID *Fdt)
{
  (VOID)Fdt;
}

STATIC BOOLEAN    mUfsStarted;
STATIC EFI_STATUS mUfsStatus = EFI_NOT_STARTED;

// Runs after PianoProbeUfs has enabled the UFS clocks and power domain.
STATIC EFI_STATUS StartReadOnlyUfs (CONST VOID *Fdt)
{
  mUfsStatus  = PianoUfsReadOnlyDmaExperiment (Fdt);
  mUfsStarted = mUfsStatus == EFI_SUCCESS;
  return mUfsStatus;
}

// Takes over the UFS link that ABL left running and publishes each logical
// unit as a read-only Block I/O device; PartitionDxe adds the GPT partitions.
// Returns NULL when storage is unavailable; Fastboot then runs without it.
STATIC CONST PIANO_FB_STORAGE *StartStorage (VOID)
{
  CONST PIANO_FB_STORAGE *Storage;
  EFI_STATUS             Status;

  // A CPU fault during UFS DMA prints PC/ESR/FAR and stops instead of
  // resetting with DMA still running.
  Status = PianoStartFaultRecovery ();
  if (EFI_ERROR (Status)) {
    DEBUG ((DEBUG_WARN, "PISTA_FAULT_RECOVERY %r\n", Status));
  }
  PianoUfsSetProbeAction (StartReadOnlyUfs);
  PianoProbeUfs (mPistaDeviceTree);
  PianoUfsSetProbeAction (NULL);
  if (!mUfsStarted) {
    Print (L"Internal storage unavailable (step %a, %r). \"fastboot fetch\" will not work.\r\n",
      mUfsStatus == EFI_NOT_STARTED ? "UFS CLOCK" : PianoUfsStartupStage (), mUfsStatus);
    return NULL;
  }
  Status  = PianoFastbootBlockReadInit ();
  Storage = Status == EFI_SUCCESS ? PianoFastbootBlockReadStorage () : NULL;
  if ((Storage == NULL) || (Storage->Ready (Storage->Context) != EFI_SUCCESS)) {
    Print (L"Internal storage unavailable (partition list, %r). \"fastboot fetch\" will not work.\r\n", Status);
    PianoFastbootBlockReadStop ();
    return NULL;
  }
  Print (L"Internal storage: read-only\r\n");
  return Storage;
}

// Stops UFS DMA, frees its SMMU context and releases its clocks. When the USB
// service ran, its retirement proof lets the UFS teardown accept the SMMU
// changes USB left behind. Returns FALSE when UFS DMA may still be running.
STATIC BOOLEAN StopStorage (BOOLEAN UsbRan)
{
  PIANO_SMMU_RETIRED_USB_PROOF Proof;
  CONST PIANO_UFS_RESET_REPORT *Ufs;
  EFI_STATUS                   Status = EFI_SUCCESS;
  EFI_TPL                      OldTpl;

  if (!mUfsStarted) {
    return TRUE;
  }
  PianoFastbootBlockReadStop ();
  if (UsbRan) {
    ZeroMem (&Proof, sizeof (Proof));
    Status = PianoUsbControllerMakeRetiredUsbProof (mPistaDeviceTree, &Proof);
    if (Status == EFI_SUCCESS) {
      Status = PianoUfsAcceptRetiredUsb (&Proof);
    }
  }
  if (Status == EFI_SUCCESS) {
    OldTpl = gBS->RaiseTPL (TPL_CALLBACK);
    Status = PianoUfsBlockIoPrepareForReset ();
    if (Status == EFI_SUCCESS) {
      Status = PianoUfsBlockIoShutdownForReset ();
    }
    Ufs = PianoUfsResetShutdownReport ();
    if ((Status == EFI_SUCCESS) && (Ufs != NULL) && Ufs->Clean && !Ufs->Failed) {
      gBS->RestoreTPL (OldTpl);
      mUfsStarted = FALSE;
      return TRUE;
    }
  }
  DEBUG ((DEBUG_WARN, "PISTA_UFS_STOP status=%r retained=1\n", Status));
  return FALSE;
}

// Runs the resident USB Fastboot service. Returns when the service cannot
// start or the host sends "continue"; "reboot" stops the service and UFS,
// releases their DMA and SMMU resources, then cold resets.
STATIC VOID RunFastboot (VOID)
{
  PIANO_DWC3_SERVICE_CONFIG       Config;
  PIANO_DWC3_SERVICE_STATUS       State;
  PIANO_USB_SERVICE_RETIRE_REPORT Report;
  EFI_STATUS                      Status;

  mCounterFrequency = GetPerformanceCounterProperties (&mCounterStart, &mCounterEnd);
  if ((mCounterFrequency == 0) || (mCounterStart == mCounterEnd)) {
    Print (L"USB Fastboot not started: no performance counter\r\n");
    return;
  }
  ZeroMem (&Config, sizeof (Config));
  Config.NowUs   = FastbootNowUs;
  Config.Storage = StartStorage ();
  Status = PianoUsbControllerServiceStart (mPistaDeviceTree, &Config);
  if (EFI_ERROR (Status)) {
    Print (L"USB Fastboot not started: %r\r\n", Status);
    if (!StopStorage (FALSE)) {
      Print (L"UFS could not be stopped safely. Hold the power key to restart.\r\n");
      CpuDeadLoop ();
    }
    return;
  }
  Print (L"USB Fastboot ready: run \"fastboot devices\" on the computer (SunUEFI-pista).\r\n");
  ZeroMem (&State, sizeof (State));
  for ( ; ; ) {
    Status = PianoUsbControllerServicePumpApp (PIANO_PRODUCT_PUMP_APP, 10000);
    if ((Status != EFI_SUCCESS) && (Status != EFI_NOT_READY)) {
      Print (L"USB Fastboot stopped: service error %r\r\n", Status);
      break;
    }
    ZeroMem (&State, sizeof (State));
    if ((PianoUsbControllerServiceGetStatus (&State) == EFI_SUCCESS) &&
        (State.Phase == PianoUsbServiceStopRequested)) {
      break;
    }
    gBS->Stall (1000);
  }
  ZeroMem (&Report, sizeof (Report));
  Status = PianoUsbControllerServiceStop (EFI_SUCCESS, &Report);
  Print (L"USB Fastboot closed: %r, resources released %u\r\n", Status, (UINT32)Report.Clean);
  if (!Report.Clean) {
    // DMA may still target its buffers; never hand over or reset.
    Print (L"USB resources could not be released. Hold the power key to restart.\r\n");
    CpuDeadLoop ();
  }
  if (!StopStorage (TRUE)) {
    Print (L"UFS could not be stopped safely. Hold the power key to restart.\r\n");
    CpuDeadLoop ();
  }
  if ((State.Action == PianoUsbServiceActionReboot) && !EFI_ERROR (Status)) {
    gRT->ResetSystem (EfiResetCold, EFI_SUCCESS, 0, NULL);
  }
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
  Print (L"Linux and Windows PE boot are not implemented in this image.\r\n");
  if (!EFI_ERROR (Status) && Gop != NULL && Gop->Mode != NULL && Gop->Mode->Info != NULL) {
    Print (L"GOP %ux%u, stride %u, framebuffer 0x%lx\r\n",
      Gop->Mode->Info->HorizontalResolution, Gop->Mode->Info->VerticalResolution,
      Gop->Mode->Info->PixelsPerScanLine, Gop->Mode->FrameBufferBase);
  }
  PrintNativeDriverSummary ();
  DEBUG ((DEBUG_WARN, "PISTA_STAGE0_CONSOLE_READY EL%u\n", (UINT32)(CurrentEl >> 2)));
  RunFastboot ();
  // Reached only when Fastboot could not start or the host sent "continue".
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
  return NULL;
}

EFI_DEVICE_PATH_PROTOCOL **EFIAPI DeviceBootManagerOnDemandConInConnect (VOID) { return NULL; }
VOID EFIAPI DeviceBootManagerProcessBootCompletion (OUT EFI_BOOT_MANAGER_LOAD_OPTION *BootOption) { }
EFI_STATUS EFIAPI DeviceBootManagerPriorityBoot (IN OUT EFI_BOOT_MANAGER_LOAD_OPTION *BootOption) { return EFI_NOT_FOUND; }
VOID EFIAPI DeviceBootManagerUnableToBoot (VOID) { }
VOID EFIAPI DeviceBootManagerBdsEntry (VOID) { }
