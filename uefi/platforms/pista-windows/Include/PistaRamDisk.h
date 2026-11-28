// SPDX-License-Identifier: BSD-2-Clause-Patent
// The RAM disk that the Mass Storage application lends to the computer.
// Its device path is a single vendor node with this GUID; the Windows loader
// started with the load option "RAM" boots only from file systems below it.
#ifndef PISTA_RAM_DISK_H_
#define PISTA_RAM_DISK_H_

#define PISTA_RAM_DISK_GUID \
  { 0x6c2f4e8a, 0x1b7d, 0x4f3e, { 0x9a, 0x52, 0x0d, 0x83, 0xe1, 0x7c, 0x44, 0xb9 } }

#endif
