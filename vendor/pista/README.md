# vendor/pista inputs

Stock Realme GT7 Pro (RMX5010) inputs from the factory realme UI 16.0.7.201 full OTA payload (`private/pista-capture/manifest.json` records the source). Generation: `python tools/dev/prepare_pista.py`.

- `dtb/xbl-config.dtb` - native UEFI configuration FDT carved from the xbl_config partition.
- `native/` - original Qualcomm foundation DXE drivers (PE + DEPEX) from the uefi partition image. Same module names and GUIDs as piano; different OEM builds.
- `early-dxe/EnvDxeEnhanced/` - early environment driver with its own provenance record.
- `dtb/pista.dtb` - live board FDT read from the stock realme recovery (`/sys/firmware/fdt`, 12 GB variant), snapshot properties in `/chosen` removed.
