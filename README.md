# Project pistaUEFI

> [!IMPORTANT]
> 本项目基于 [Project SunUEFI](https://github.com/slwyts/Project-SunUEFI)，保留了它的完整提交历史和各文件原有的版权与许可证声明。引用或再分发 SunUEFI 部分的代码、数据和结论时，请按 SunUEFI 的要求注明来源并链接回原项目。

把 [SunUEFI](https://github.com/slwyts/Project-SunUEFI) 的 EDK2 UEFI 移植到 Realme GT7 Pro（代号 `pista`，型号 RMX5010，骁龙 8 至尊版 SM8750-AB）。SunUEFI 已经让小米平板 8 Pro 在保留 Android 的同时启动主线 Linux；这里的目标是让 GT7 Pro 也能做到同样的事，并跟进 SunUEFI 社区在 Windows on ARM 上的进展。

目前完成的是诊断固件：用 `fastboot boot` 临时启动后，UEFI 能在手机上运行到启动管理阶段，沿用 ABL 点亮的屏幕，原厂的 16 个基础驱动全部正常工作，电脑可以通过 UEFI Fastboot 截图、查询状态和重启手机。它还不能启动 Linux 或 Windows，也不会访问或写入手机上的存储。

## 现状

| 部分 | 状态 | 说明 |
| --- | --- | --- |
| 原厂输入生成 | ✅ | 内存图、配置表、板级设备树和原厂 DXE 驱动全部从 RMX5010 原厂 OTA 生成 |
| 诊断固件临时启动 | ✅ | `fastboot boot` 后依次完成 SEC、DXE 和 BDS 阶段，屏幕 1264×2780 |
| 原厂基础驱动 | ✅ | 16 个基础驱动全部运行 |
| TrustZone 相关驱动 | ❌ | 临时启动时初始化失败，诊断固件暂不携带；原厂的存储、变量保存和显示驱动需要它们，SunUEFI 自己的 USB 和存储驱动不需要 |
| UEFI Fastboot | ✅ | USB 2.0，可截图、查询状态、重启；还不能读写分区，也不能导出内存日志 |
| Linux | ❌ | 设备树和驱动还没开始适配 |
| 开机选择 Android 或 UEFI | ❌ | 需要先摸清 Realme ABL 的行为 |
| Windows on ARM | ❌ | SunUEFI 在小米平板 8 Pro 上也还没有启动 |

## 试一试

诊断固件目前需要自己构建，CI 还不产出 pista 的镜像。你需要一台已解锁 Bootloader 的 GT7 Pro，以及 RMX5010 的原厂完整 OTA 包。

1. 按 [pista 诊断固件](docs/devel/pista.md) 从 OTA 生成输入并编译，产物在 `artifacts/pista/`。
2. 手机进入 Bootloader：

   ```bash
   adb reboot bootloader
   ```

3. 临时启动诊断固件：

   ```bash
   fastboot boot artifacts/pista/pista-stage0-UNTESTED.img
   ```

屏幕会先滚动启动日志，然后显示设备信息、`Stock drivers running: 16/16` 和 `USB Fastboot ready`。这时电脑可以用 UEFI Fastboot 截图并回到 Android：

```bash
fastboot -s SunUEFI-pista oem screenshot
```

```bash
fastboot -s SunUEFI-pista get_staged pista-screenshot.bmp
```

```bash
fastboot -s SunUEFI-pista reboot
```

镜像只加载到内存，不改动手机上的任何内容。如果画面卡住，长按电源键强制重启。更多命令和限制见 [pista 诊断固件](docs/devel/pista.md#uefi-fastboot)。

## 与 SunUEFI 的关系

仓库保留了 SunUEFI 的全部代码，小米平板 8 Pro（`piano`）的部分原样不动，`docs/` 里除 [pista 诊断固件](docs/devel/pista.md) 以外的页面描述的都是 piano。pista 相关的文件在 `uefi/platforms/pistaPkg/`、`vendor/pista/` 和 `tools/dev/` 下的 pista 脚本里。

GT7 Pro 和小米平板 8 Pro 用同一颗 SoC，SoC 层面的支持可以直接复用；需要重新做的是从 GT7 Pro 原厂固件生成的板级输入，以及屏幕、触控、电池等板级硬件。移植中确认的设备差异写在 [pista 诊断固件](docs/devel/pista.md#设备差异)。

骁龙 8 至尊版的 UEFI、Linux 和 Windows on ARM 进展可以在 [SunUEFI 官方交流群](https://qm.qq.com/q/C1RkQC2yYw)（QQ 群 1135414849）里跟进。

## 致谢与许可

UEFI 部分基于 [SunUEFI](https://github.com/slwyts/Project-SunUEFI)、[EDK2](https://github.com/tianocore/edk2) 和 [Mu-Silicium](https://github.com/Project-Silicium/Mu-Silicium)。各文件按自身的许可证声明分发，范围见 [LICENSE.md](LICENSE.md)，第三方来源见 [THIRD_PARTY.md](THIRD_PARTY.md)。`vendor/pista/` 中的原厂驱动和设备树来自 Realme 官方 OTA。
