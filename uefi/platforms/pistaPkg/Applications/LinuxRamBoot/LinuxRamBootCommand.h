// SPDX-License-Identifier: BSD-2-Clause-Patent
// Kernel command line for the pista RAM boot, read by LinuxRamBoot.c.
//
// console=tty0 is listed last so /dev/console is the framebuffer console;
// ttyGS0 is the USB serial gadget and only appears once USB is up.
// clk_ignore_unused and pd_ignore_unused keep the display clocks and power
// domains that ABL left on, since no display driver claims them yet.
// iommu.passthrough=1 matches the piano product kernel. panic=30 restarts the
// phone, which then boots Android, 30 seconds after a kernel panic.
// g_serial reports the Linux Foundation VID instead of its default 0525:a4a7,
// which vendor tool drivers on Windows claim by ID; with 1d6b:0104 Windows
// binds its own CDC ACM serial driver.
#pragma once
#define LINUX_RAM_BOOT_COMMAND \
  "rdinit=/init nokaslr efi=novamap console=ttyGS0,115200 console=tty0 " \
  "fbcon=font:TER16x32 consoleblank=0 loglevel=7 " \
  "clk_ignore_unused pd_ignore_unused iommu.passthrough=1 panic=30 " \
  "g_serial.idVendor=0x1d6b g_serial.idProduct=0x0104"
