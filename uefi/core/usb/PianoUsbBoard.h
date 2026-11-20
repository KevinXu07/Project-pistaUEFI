// SPDX-License-Identifier: BSD-2-Clause-Patent
#pragma once
// Board USB properties shared by the USB service. The defaults describe
// piano; other boards override them with compiler definitions.
#ifndef PIANO_USB_SUPERSPEED
// 1: the board routes the QMP USB3 lanes. 0: USB 2.0 only, the QMP PHY stays
// untouched and the controller takes its PIPE clock from UTMI.
#define PIANO_USB_SUPERSPEED 1
#endif
#if PIANO_USB_SUPERSPEED != 0 && PIANO_USB_SUPERSPEED != 1
#error PIANO_USB_SUPERSPEED must be exactly 0 or 1
#endif
#ifndef SUNUEFI_DEVICE_NAME
// Device code name reported in USB strings and Fastboot variables.
#define SUNUEFI_DEVICE_NAME "piano"
#endif
