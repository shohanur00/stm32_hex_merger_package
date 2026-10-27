# STM32 Firmware Package Tool

A Windows-friendly Tkinter GUI to combine bootloader and application Intel HEX files, calculate CRC-16/CCITT-FALSE, generate the 32-byte firmware metadata structure, and optionally export a full-Flash BIN. Python standard library only; no `pip install` is needed.

## Run

```powershell
python stm32_hex_merger.py
```

Python 3.10+ recommended. On Windows, install Python from python.org and enable **Add Python to PATH**.

## Features

- User-configurable Flash base address and Flash size (KiB).
- User-configurable bootloader start, application start, metadata start, and metadata page size (KiB).
- Bootloader end is derived from application start; application end is derived from metadata start.
- Intel HEX checksum and record validation; rejects missing EOF and conflicting bytes.
- Configured bootloader/application memory-range checks and overlap detection.
- CRC-16/CCITT-FALSE calculated to match the supplied `BL_Flash_CalculateCRC()` implementation.
- Sparse application gaps are treated as erased Flash (`0xFF`) while calculating CRC.
- Generates the 32-byte little-endian metadata structure at the configured metadata address.
- Optional full-Flash BIN export, based at the configured Flash base, with omitted bytes filled with `0xFF`.
- Re-reads and verifies generated HEX and BIN outputs.
- Supports decimal and `0x` hexadecimal address/version values.

## Default memory layout

The GUI defaults to this STM32G030F6P6 configuration, but the address fields can be changed for other memory layouts:

| Region | Address range | Size |
|---|---|---:|
| Flash | `0x08000000`–`0x08007FFF` | 32 KiB |
| Bootloader | `0x08000000`–`0x080017FF` | 6 KiB |
| Application | `0x08001800`–`0x080077FF` | 24 KiB |
| Metadata page | `0x08007800`–`0x08007FFF` | 2 KiB |

Application linker script for the default layout:

```ld
FLASH (rx) : ORIGIN = 0x08001800, LENGTH = 24K
```

If you change **Application start** or the linker memory map, rebuild the application using the matching linker script. The bootloader's compile-time memory definitions must also match the values entered in this tool.

## Metadata

The tool serializes the C structure as `<IIIHHI3I` (32 bytes, little-endian): magic `0x424C4D44`, application start, application span, CRC, version, valid status `0xA5A5A5A5`, and three reserved values `0xFFFFFFFF`.

CRC parameters: polynomial `0x1021`, initial value `0xFFFF`, no reflection, xorout `0`.

## Important

1. **Erase target Flash before programming.** Intel HEX and BIN files do not erase Flash by themselves.
2. The bootloader's `BL_Metadata_IsValid()` must accept the configured application size/address range. If `BL_APP_END_ADDRESS` is inclusive, the size check should account for the final byte with `+1UL`.
3. The BIN size is the configured Flash size, and its base address is the configured Flash base. Confirm your programming tool uses that base address.
4. The HEX includes the 32 metadata bytes, not a full explicit metadata-page record. BIN export fills all omitted bytes with `0xFF`.
5. This tool has been syntax-checked and tested with generated sample HEX files; it has not been validated on physical MCU hardware.
