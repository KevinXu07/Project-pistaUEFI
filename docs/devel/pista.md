# Realme GT7 Pro（pista）诊断固件

`pista` 是 Realme GT7 Pro（RMX5010，SM8750-AB）的代号。这一页说明怎样从原厂固件生成 pista 的 UEFI 输入、编译诊断固件、用 `fastboot boot` 临时启动它，以及移植时确认过的设备差异。

诊断固件只验证 UEFI 能在这台手机上启动：它运行到启动管理阶段，在屏幕上显示设备信息和原厂驱动的运行情况，然后关机。它不带存储、USB 和系统引导，不写任何分区。产品核心还没有移植。

## 现状

用 `fastboot boot` 临时启动时，固件会依次完成 BootShim、SEC、DXE 和 BDS 阶段，并接着使用 ABL 已经点亮的屏幕（1264×2780）。piano 产品核心依赖的 16 个原厂基础模块全部能运行，横幅显示 `Stock drivers running: 16/16`。BDS 找不到启动项时显示 "No Operating System Found!"，10 秒后关机；如果连着 USB，手机随后会自动开机回到 Android。

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

`prepare_pista.py` 从 `xbl_config` 里的 UEFI 配置设备树生成内存图和配置表，写入 `uefi/platforms/pistaPkg/`；把原厂模块和设备树复制到 `vendor/pista/` 并记录哈希；最后把平台和模块装进 `upstream/Mu-Silicium`。它还会打印内存图与 piano 的差异：40 个区域里只有 `DXE_Heap` 不同（起点和大小不同，终点同为 `0xD8000000`），`UEFI_FD` 同样在 `0xA7000000`，大小 4 MB。

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

镜像只加载到内存，不写入分区。固件卡住时，长按电源键可以强制重启；ABL 启动的 phoenix 看门狗也会在约 90 秒后让整机复位。

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
| 看门狗 | ABL 会启动 phoenix 看门狗，固件不喂狗时约 90 秒后整机复位 | 固件挂死时手机会自己重启；产品核心需要处理它 |
| 运行级别 | 固件运行在 EL1 | 与高通启动链一致，EL2 由虚拟机管理器占用 |

## 文件

| 路径 | 内容 |
| --- | --- |
| `uefi/platforms/pistaPkg/` | 诊断平台。`MemoryMapLib.c`、`ConfigurationMapLib.c`、`native-memory-map.json`、`native-modules.json` 和 `Stage0BootManagerLib/NativeModules.h` 由 `prepare_pista.py` 生成 |
| `vendor/pista/` | 原厂设备树、16 个基础模块和 EnvDxeEnhanced，`manifest.json` 记录来源和哈希 |
| `tools/dev/stage_pista_capture.py` | 把 OTA 分区镜像放进 `private/pista-capture/` |
| `tools/dev/extract_uefi_fv.py` | 从 `uefi` 分区提取 FV 里的 DXE 模块 |
| `tools/dev/fdt_parse.py` | 读取设备树的辅助模块 |
| `tools/dev/prepare_pista.py` | 生成平台文件和输入包 |
