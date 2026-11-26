# Realme GT7 Pro（pista）诊断固件

`pista` 是 Realme GT7 Pro（RMX5010，SM8750-AB）的代号。这一页说明怎样从原厂固件生成 pista 的 UEFI 输入、编译诊断固件、用 `fastboot boot` 临时启动它，以及移植时确认过的设备差异。

诊断固件验证 UEFI 能在这台手机上启动：它运行到启动管理阶段，在屏幕上显示设备信息和原厂驱动的运行情况，然后提供 UEFI Fastboot，供电脑截图、查询状态、只读导出分区和重启。同一份固件还能从内存启动主线 Linux 内核，见[从内存启动 Linux](#从内存启动-linux)。它不写任何分区。产品核心还没有移植。

## 现状

用 `fastboot boot` 临时启动时，固件会依次完成 BootShim、SEC、DXE 和 BDS 阶段，并接着使用 ABL 已经点亮的屏幕（1264×2780）。piano 产品核心依赖的 16 个原厂基础模块全部能运行，横幅显示 `Stock drivers running: 16/16`。随后固件以只读方式接管内部 UFS，再启动 USB Fastboot 服务，电脑上可以看到 `SunUEFI-pista`。从 `fastboot boot` 到出现这个设备约 8 秒。横幅出现前屏幕滚动启动日志；之后屏幕只显示横幅和每一步的状态（接管存储、启动 USB Fastboot），详细日志只写进内存日志。

Fastboot 服务启动失败时，横幅显示原因，固件在 45 秒后冷重启回 Android。电脑发送 `fastboot continue` 时，固件停止服务，提示 "No Operating System Found!"，10 秒后关机；连着 USB 时手机随后会自动开机回到 Android。

## 生成输入

原厂输入只取自 RMX5010 的原厂 realme UI 完整 OTA。手机上装着其他机型的移植系统时，机内的设备树和固件都不能当作输入。

1. 用 payload 解包工具从 OTA 的 `payload.bin` 提取 `xbl_config`、`uefi`、`abl`、`boot`、`dtbo`、`pvmfw`、`splash` 七个分区镜像。
2. 把它们放进 `private/pista-capture/`：

   ```bash
   python tools/dev/stage_pista_capture.py --images <镜像目录> --prefix <文件名前缀> --source "realme UI 16.0.7.201"
   ```

3. 从 `uefi` 分区提取原厂 DXE 模块：

   ```bash
   python tools/dev/extract_uefi_fv.py private/pista-capture/uefi.img --output private/pista-uefi-extracted
   ```

4. 在原厂 recovery 的 root shell 里读取 `/sys/firmware/fdt`，删掉 `/chosen` 里的 `bootargs`、`kaslr-seed`、`linux,initrd-start`、`linux,initrd-end`，保存为 `private/pista-capture/live-recovery.clean.dtb`。
5. 生成平台文件和输入包，并装进固件工作区：

   ```bash
   python tools/dev/prepare_pista.py
   ```

`prepare_pista.py` 从 `xbl_config` 里的 UEFI 配置设备树生成内存图（另外从 `DXE_Heap` 末尾划出 2 MiB 给内存日志）和配置表，写入 `uefi/platforms/pistaPkg/`；把原厂模块和设备树复制到 `vendor/pista/` 并记录哈希；最后把平台和模块装进 `upstream/Mu-Silicium`。它还会打印内存图与 piano 的差异：40 个区域里只有 `DXE_Heap` 不同（起点和大小不同，终点同为 `0xD8000000`），`UEFI_FD` 同样在 `0xA7000000`，大小 4 MB。

## 编译和临时启动

构建在 Linux 或 WSL 里进行。使用系统自带的工具链时，主机需要 clang、lld、llvm（提供 `llvm-lib`）、nasm、acpica-tools、aarch64 binutils 和 uuid-dev。

```bash
SUNUEFI_TOOLCHAIN_ROOT=/usr bash tools/build_stage0.sh pista
```

```bash
SUNUEFI_TOOLCHAIN_ROOT=/usr .venv/bin/python tools/dev/package_stage0.py --profile pista --header-version 4
```

产物在 `artifacts/pista/`。构建是否成功以 `build-ok.json` 为准；构建日志经过管道过滤后，退出码会丢失。

在 Windows 下通过 WSL 构建时，Git 必须设置 `core.autocrlf false`，否则检出的 shell 脚本、汇编和 EDK2 文件会带上 CRLF，构建失败。

临时启动：

```bash
adb reboot bootloader
```

```bash
fastboot boot artifacts/pista/pista-stage0-UNTESTED.img
```

镜像只加载到内存，不写入分区。固件卡住时，长按电源键可以强制重启；固件挂死在断言里时，看门狗也会在约 90 秒后让整机复位。

## UEFI Fastboot

横幅出现 `USB Fastboot ready` 后，电脑上用 `SunUEFI-pista` 这个序列号访问固件：

```bash
fastboot -s SunUEFI-pista getvar product
```

```bash
fastboot -s SunUEFI-pista oem screenshot
```

```bash
fastboot -s SunUEFI-pista get_staged pista-screenshot.bmp
```

```bash
fastboot -s SunUEFI-pista oem ramlog
```

```bash
fastboot -s SunUEFI-pista get_staged pista-ramlog.txt
```

```bash
fastboot -s SunUEFI-pista fetch boot_a boot_a.img
```

```bash
fastboot -s SunUEFI-pista reboot
```

`fetch` 按 GPT 分区名读取内部存储，每次请求最多 64 KiB（`getvar max-fetch-size`），实测约 2.8 MB/s，96 MB 的 `boot_a` 需要约 36 秒。读出的 `vbmeta_a`、`xbl_config_a`、`dtbo_a` 和 `boot_a` 与 Android 下 root 读取同一分区的 SHA-256 一致。`oem ramlog` 导出本次启动的完整固件日志（整个 2 MiB 环形缓冲区），从 `SUNUEFI_RAMLOG_BEGIN` 开始，包括 SEC 横幅、内存分配、DXE 驱动加载以及 UFS、USB、SMMU 的每一步。每行开头的 `[秒.毫秒]` 是开机以来的时间，从手机上电算起，包括 ABL 的时间。`oem screenshot` 导出当前屏幕的 24 位 BMP（1264×2780，约 10 MB）。屏幕和内存日志只在有新日志时更新，USB 空闲时画面不动是正常的。`oem status` 输出 USB 控制器和存储策略的状态，`reboot` 会先停止服务，再停止 UFS，释放两者的 DMA、SMMU 和时钟资源，然后冷重启，约 16 秒后回到 Android。任何一方停不干净时固件不复位，屏幕提示长按电源键。Fastboot 空闲运行几分钟不会被看门狗复位。

内部存储只读：`flash` 和 `erase` 会失败，存储策略显示 `read-only`。`oem sha256` 返回 "SHA256 unavailable"，因为关闭安全启动时 BaseCryptLib 是空实现。

UFS 使用 SunUEFI 自己的只读驱动（`uefi/core/storage/`），不用原厂 UFSDxe，因为原厂驱动需要 TrustZone。驱动不初始化 PHY 和供电，只接管 ABL 留下的链路：检查链路状态、必要时退出 Hibern8，然后换上自己经 SMMU（流 ID 0x60）映射的传输队列，读取设备描述符、各 LUN 容量和 GPT，把每个 LUN 发布成只读 Block I/O，由 PartitionDxe 生成分区。写入和同步命令在提交给控制器之前就被拒绝。pista 的 UFS 控制器、PHY、7 路供电电压和 SMMU 流 ID 与 piano 的原厂设备树完全相同，所需的 10 个时钟和电源域在 pista 原厂 ClockDxe 里都有。原厂内存图没有映射 UFS 控制器寄存器，`prepare_pista.py` 补上 `UFS_HCI`（`0x1D84000`，`0x3000`）。UFS 接管失败时屏幕写明失败的步骤，Fastboot 照常启动，只是 `fetch` 不可用。

内存日志写在 `DXE_Heap` 末尾划出的 2 MiB（`0xD7E00000`，内存图中的 `UEFI_RamLog`，保留且不缓存）。piano 把日志写在 Linux ramoops 预留的 `0xA3500000`，并且从不初始化它；pista 的设备树没有固定地址的 ramoops，而 `0xA3500000` 在 pista 上是显示的 Demura 数据，所以 pista 用自己的区域，并在每次启动时由 SEC 清空。串口库（`RamLogSerialPortLib`）由 `prepare_pista.py` 在工作区里用 Mu 的帧缓冲串口库加上 `PistaRamLog.c` 组装，日志同时写屏幕和这块内存。诊断启动管理库显示横幅后，关掉自己这个模块的屏幕输出：UFS、USB 和 SMMU 的调试行有约 4000 行，每画一行约 10 毫秒，全部上屏会让 Fastboot 晚一分多钟出现，还会把横幅滚出屏幕。Silicium 默认每画一行再等 10 毫秒（`PcdFrameBufferDelay`），pista 设为 0。

服务代码来自共用的 `uefi/core/usb/`、`uefi/core/storage/` 和 `uefi/core/memory/`，由 `prepare_pista.py` 复制进工作区的诊断启动管理库，以 USB 2.0 方式编译（`PIANO_USB_SUPERSPEED=0`、`SUNUEFI_DEVICE_NAME="pista"`）。GT7 Pro 的 USB3 PHY 在板级设备树里是禁用的，`maximum-speed` 为 `high-speed`，所以固件不初始化 QMP PHY，按 Linux `dwc3-qcom` 对纯 USB 2.0 板子的做法让控制器从 UTMI 取 PIPE 时钟，并把 DWC3 设为 High-Speed。M31 eUSB2 PHY 和中继器的供电与 piano 相同（ldod2、ldof1 为 0.88 V，ldob5 为 3.1 V，ldob15 为 1.8 V），通过原厂 RpmhDxe 投票。诊断固件没有完整的板级设备树，服务读取的 SMMU 和 USB 节点由 `prepare_pista.py` 从 `pista.dtb` 中裁出，生成 `PistaDeviceTree.h`。

## 从内存启动 Linux

诊断固件在横幅之后、启动 USB Fastboot 之前，会检查启动镜像里有没有 Linux 载荷。有就校验后启动内核，没有就照常进入 Fastboot。目前内核能启动到 initramfs，电脑通过 USB 串口得到一个 shell，屏幕显示内核日志。启动过程只占用内存，不访问存储，重启后回到 Android。

### 构建

内核是 7.3.3 stable（`d50d994b`）加上 `patches/linux/7.3/` 里的全部补丁，pista 的设备树在补丁 0141 到 0143 里。在 WSL 里取源码并打补丁（`series.json` 记录了每个补丁的 SHA-256 和打完后的源码树，最后一个补丁之后的源码树应为 `target_tree`）：

```bash
git init linux && cd linux && git fetch --depth=1 https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git d50d994bffa3c7c6db2d8bc4c5f0c55e3f46b0cd && git checkout FETCH_HEAD
```

```bash
for p in $(python3 -c "import json;print(' '.join(r['file'] for r in json.load(open('<仓库>/patches/linux/7.3/series.json'))['patches']))"); do git am -q <仓库>/patches/linux/7.3/$p || break; done
```

配置是 `defconfig` 加上 `linux/configs/piano-ram.config` 和 `linux/configs/pista-ram.config`：驱动全部编进内核，不带存储和模块。编译内核和设备树：

```bash
make O=../out ARCH=arm64 LLVM=1 defconfig && scripts/kconfig/merge_config.sh -m -O ../out ../out/.config <仓库>/linux/configs/piano-ram.config <仓库>/linux/configs/pista-ram.config && make O=../out ARCH=arm64 LLVM=1 olddefconfig
```

```bash
make O=../out ARCH=arm64 LLVM=1 -j$(nproc) Image qcom/sm8750-realme-pista.dtb
```

把内核、initramfs 和设备树打成载荷，再和固件一起打成启动镜像。initramfs 由 `linux/initramfs/pista-ram/init` 和 Debian 的 `busybox-static` arm64 包（`config/release.json` 锁定的 1.38.0-3+b2）生成：

```bash
python3 tools/dev/package_pista_linux.py --kernel ../out/arch/arm64/boot/Image --dtb ../out/arch/arm64/boot/dts/qcom/sm8750-realme-pista.dtb --busybox-deb busybox-static_1.38.0-3+b2_arm64.deb
```

```bash
SUNUEFI_TOOLCHAIN_ROOT=/usr .venv/bin/python tools/dev/package_stage0.py --profile pista --header-version 4 --linux-payload artifacts/pista/linux-payload.bin
```

产物是 `artifacts/pista/pista-linux.img`（约 17 MB），用 `fastboot boot` 启动。

### 使用

启动后约 12 秒，电脑上出现一个新的 USB 串口（Windows 显示为"USB 串行设备"），用 115200 波特率打开就是 shell。手机屏幕显示内核版本和 USB 状态。在 shell 里执行 `reboot -f`，约 13 秒后回到 Android。USB 在 10 分钟内一直没连上时，手机自动重启回 Android；内核 panic 后 30 秒也会重启。

### 实现

Realme 的 ABL 执行 `fastboot boot` 时，交给后续阶段的 ramdisk 是手机上 vendor_boot 和 init_boot 两个分区的 ramdisk（18.4 MB 加 3.2 MB），启动镜像自带的 ramdisk 会被丢弃。所以 Linux 载荷放在启动镜像的内核部分，紧跟在固件后面一起压缩，镜像头里的 image size 包含载荷。BootShim 把固件的加载地址和大小记在 `0xA7FFF000`，`LinuxRamBoot` 在 ramdisk 里找不到载荷时，就到"固件地址加固件大小"处读取。载荷格式是 `SUNUEFI-LINUXv2`：内核、initramfs 和设备树各带 SHA-256，校验不过就不启动，退回 Fastboot。

`LinuxRamBoot` 通过 EFI stub 启动内核，命令行在 `uefi/platforms/pistaPkg/Applications/LinuxRamBoot/LinuxRamBootCommand.h`。g_serial 用 Linux 基金会的 ID（`1d6b:0104`）：它的默认 ID `0525:a4a7` 会被一些厂商工具的 Windows 驱动按 ID 认领，在测试用的电脑上就被一个签名无效的 UNISOC 驱动认领，串口出不来。

pista 的 apps_smmu 有 132 组流匹配寄存器，piano 有不超过 128 组。内核接管 UEFI 留下的 SMMU 状态时，快照最多只放 128 组，pista 上 SMMU 驱动因此加载失败（`-95`），USB 控制器无法做 DMA。快照上限已改为 256，即 ID0.NUMSMRG 能表示的全部范围。

从原厂配置生成的内存图只描述 UEFI 自己用到的区域，只靠它的话 Linux 只能看到约 368 MB。所以诊断启动管理库在启动 Linux 之前，会读取 ABL 交给固件的设备树里的 `/memory` 节点，把其中内存图完全没有描述的部分作为可用内存加入 UEFI 内存图（`PistaDdrMemory.c`）。ABL 每次启动都按 DDR 分区表填写这个节点，所以内存容量跟着机器走，不写死在内存图里；目前只在 12 GB 机型上测过。内存图里已有的区域保持原来的类型，`Kernel` 区域（256 MB）继续保留给 ABL 放内核和 ramdisk；需要避开的固件区域由内核设备树里的 reserved-memory 节点保留。在 12 GB 的机器上，固件加入 10550 MiB，Linux 的 `MemTotal` 是 10.2 GiB。写满 9.3 GB 再读回校验没有出错，加入的内存都能正常读写。

## 原厂模块的加载顺序

16 个原厂基础模块必须放进 FDF 的 `APRIORI DXE` 列表，并保持原厂 DXE APRIORI 列表中的相对顺序：

SmemDxe → DALSys → HWIODxeDriver → ChipInfo → PlatformInfoDxeDriver → HALIOMMU → ULogDxe → CmdDbDxe → PwrUtilsDxe → NpaDxe → RpmhDxe → VcsDxe → ClockDxe → DALTLMM → Qup → I2C

只靠 DEPEX 排序不行。ULogDxe、CmdDbDxe 和 NpaDxe 的 DEPEX 写的是无条件加载，但运行时需要 DALSys 协议。DXE 第一遍扫描时 DALSys 还在等 SMEM 协议，这些模块会抢先运行，然后在 `DALModEnv.c +115` 断言失败。APRIORI 里的模块严格按列出的顺序加载，不检查 DEPEX，所以顺序一错同样会触发这个断言：piano 模板 `piano.fdf` 就把 ChipInfo 排在了 DALSys 和 SmemDxe 前面。

原厂 DXE APRIORI 是 `uefi` 分区 FV 里 GUID 为 `fc510ee7-ffdc-11d4-bd41-0080c73c8881` 的文件，共 101 项。

## 设备差异

| 项目 | pista 的情况 | 影响 |
| --- | --- | --- |
| TrustZone 相关模块 | `fastboot boot` 起来的固件里，`ScmDxeCompat` 调用 TrustZone 失败（`ScmCall ret=0xE`），`TzDxeLA` 随后断言挂死 | 诊断固件不带 ShmBridgeDxeLA、ScmDxeCompat、TzDxeLA。16 个基础模块都不需要它们；UFSDxe、VariableDxe、DisplayDxe 等模块会用到 |
| CPU 调频 | ClockDxe 打印 `ClockInitCPU: SCMI LocateProtocol failed` 后继续运行 | SCMI 协议由 QcomScmiDxe 提供，而它排在需要 TrustZone 的 MailboxDxe 之后。没有它时 CPU 保持启动时的频率，UEFI 阶段不受影响 |
| 内核格式 | 原厂 `boot.img` 是 v4 头，内核是未压缩的 PE | ABL 同样接受 gzip 压缩的 BootShim 加固件 |
| USB | 只有 USB 2.0，USB3 PHY 在设备树中禁用 | Fastboot 以 High-Speed 工作，不需要 Type-C 正反面切换 |
| 看门狗 | 固件挂死在断言里时约 90 秒后整机复位；Fastboot 空闲运行几分钟没有复位 | 挂死时手机会自己重启 |
| 运行级别 | 固件运行在 EL1 | 与高通启动链一致，EL2 由虚拟机管理器占用 |
| 启动镜像的 ramdisk | `fastboot boot` 时 ABL 改用 vendor_boot 和 init_boot 的 ramdisk | Linux 载荷放在启动镜像的内核部分 |
| apps_smmu | 132 组流匹配寄存器 | 内核的 SMMU 接管快照上限从 128 改为 256 |

## 文件

| 路径 | 内容 |
| --- | --- |
| `uefi/platforms/pistaPkg/` | 诊断平台。`MemoryMapLib.c`、`ConfigurationMapLib.c`、`native-memory-map.json`、`native-modules.json`、`Stage0BootManagerLib/NativeModules.h` 和 `Stage0BootManagerLib/PistaDeviceTree.h` 由 `prepare_pista.py` 生成 |
| `vendor/pista/` | 原厂设备树、16 个基础模块和 EnvDxeEnhanced，`manifest.json` 记录来源和哈希 |
| `tools/dev/stage_pista_capture.py` | 把 OTA 分区镜像放进 `private/pista-capture/` |
| `tools/dev/extract_uefi_fv.py` | 从 `uefi` 分区提取 FV 里的 DXE 模块 |
| `tools/dev/fdt_parse.py` | 读取设备树的辅助模块 |
| `tools/dev/prepare_pista.py` | 生成平台文件和输入包 |
| `tools/dev/package_pista_linux.py` | 把内核、initramfs 和设备树打成 Linux 载荷 |
| `linux/configs/pista-ram.config` | 内存启动内核的 pista 配置 |
| `linux/initramfs/pista-ram/init` | 内存启动的 initramfs：屏幕状态和 USB 串口 shell |
| `uefi/platforms/pistaPkg/Applications/LinuxRamBoot/` | pista 的内核命令行；加载器本体在 `uefi/components/linux-loader/` |
