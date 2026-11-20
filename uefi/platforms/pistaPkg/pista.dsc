##
#  Copyright (c) 2011 - 2022, ARM Limited. All rights reserved.
#  Copyright (c) 2014, Linaro Limited. All rights reserved.
#  Copyright (c) 2015 - 2020, Intel Corporation. All rights reserved.
#  Copyright (c) 2018, Bingxing Wang. All rights reserved.
#  Copyright (c) Microsoft Corporation.
#
#  SPDX-License-Identifier: BSD-2-Clause-Patent
##

################################################################################
#
# Defines Section - statements that will be processed to create a Makefile.
#
################################################################################
[Defines]
  PLATFORM_NAME                  = pista
  PLATFORM_GUID                  = A5484610-EC25-5CE7-8EB4-542F80579617
  PLATFORM_VERSION               = 0.1
  DSC_SPECIFICATION              = 0x00010005
  OUTPUT_DIRECTORY               = Build/pistaPkg
  SUPPORTED_ARCHITECTURES        = AARCH64
  BUILD_TARGETS                  = RELEASE|DEBUG
  SKUID_IDENTIFIER               = DEFAULT
  FLASH_DEFINITION               = pistaPkg/pista.fdf
  USE_CUSTOM_DISPLAY_DRIVER      = 0

  #
  # 0 = SM8750-AB
  # 1 = SM8750-3-AB
  # 2 = SM8750-AC
  #
  SOC_TYPE                       = 0

!include PakalaPkg/PakalaPkg.dsc.inc

[PcdsFixedAtBuild]
  #
  # DDR Memory
  #
  gArmTokenSpaceGuid.PcdSystemMemoryBase|0x80000000

  #
  # UEFI Stack
  #
  gArmPlatformTokenSpaceGuid.PcdCPUCoresStackBase|0xA760D000
  gArmPlatformTokenSpaceGuid.PcdCPUCorePrimaryStackSize|0x40000

  #
  # SMBIOS
  #
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosSystemManufacturer|"Realme"
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosSystemModel|"GT7 Pro"
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosSystemRetailModel|"rmx5010"
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosSystemRetailSku|"RMX5010"
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosSystemBoardModel|"rmx5010"

  #
  # Simple Frame Buffer
  #
  gSiliciumPkgTokenSpaceGuid.PcdFrameBufferWidth|1264
  gSiliciumPkgTokenSpaceGuid.PcdFrameBufferHeight|2780
  gSiliciumPkgTokenSpaceGuid.PcdFrameBufferColorDepth|32

  #
  # Platform PEI
  #
  gQcomPkgTokenSpaceGuid.PcdPlatformType|"LA"

[LibraryClasses]
  #
  # Memory Libraries
  #
  MemoryMapLib|pistaPkg/Library/MemoryMapLib/MemoryMapLib.inf

  #
  # QCOM Libraries
  #
  ConfigurationMapLib|pistaPkg/Library/ConfigurationMapLib/ConfigurationMapLib.inf

[Components]
  #
  # ACPI Tables
  #
  #pista/AcpiTables.inf

# Stage 0: no persistent variables, storage, capsules or OS boot.
[PcdsFixedAtBuild]
  gEfiMdeModulePkgTokenSpaceGuid.PcdEmuVariableNvModeEnable|TRUE
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosProcessorPartNumber|"SM8750-AB"
  gSiliciumPkgTokenSpaceGuid.PcdSmbiosProcessorModel|"Snapdragon 8 Elite"
[LibraryClasses]
  DeviceBootManagerLib|pistaPkg/Library/Stage0BootManagerLib/Stage0BootManagerLib.inf
  # Framebuffer console plus the RAM log read by "fastboot oem ramlog"; the INF
  # is assembled in the workspace by tools/dev/prepare_pista.py.
  SerialPortLib|pistaPkg/Library/RamLogSerialPortLib/RamLogSerialPortLib.inf
[Components]
  pistaPkg/Drivers/CapsuleArchNullDxe/CapsuleArchNullDxe.inf
