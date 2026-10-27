#!/usr/bin/env python3
"""Advanced STM32G030F6P6 bootloader/application HEX merger (stdlib only)."""
from __future__ import annotations
import struct
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path

APP_NAME = "STM32G030 Firmware Package Tool"
FLASH_START = 0x08000000
FLASH_SIZE = 32 * 1024
FLASH_END = FLASH_START + FLASH_SIZE
BOOT_START, BOOT_SIZE = 0x08000000, 6 * 1024
BOOT_END = BOOT_START + BOOT_SIZE
APP_START, APP_SIZE = 0x08001800, 24 * 1024
APP_END = APP_START + APP_SIZE
META_START, META_SIZE = 0x08007800, 2 * 1024
META_END = META_START + META_SIZE
MAGIC = 0x424C4D44
STATUS_VALID = 0xA5A5A5A5
CRC_POLY, CRC_INIT = 0x1021, 0xFFFF
META = struct.Struct("<IIIHHI3I")

class HexFormatError(ValueError): pass

def _checksum(raw: bytes) -> int:
    return (-sum(raw)) & 0xFF

def parse_ihex(path: str | Path) -> dict[int, int]:
    memory: dict[int, int] = {}
    upper = 0
    eof_seen = False
    try:
        lines = Path(path).read_text(encoding="ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise HexFormatError(f"{path}: file is not ASCII Intel HEX") from exc
    for line_no, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line: continue
        if eof_seen: raise HexFormatError(f"{path}:{line_no}: data appears after EOF")
        if not line.startswith(":"): raise HexFormatError(f"{path}:{line_no}: record must start with ':'")
        try: rec = bytes.fromhex(line[1:])
        except ValueError as exc: raise HexFormatError(f"{path}:{line_no}: invalid hex data") from exc
        if len(rec) < 5: raise HexFormatError(f"{path}:{line_no}: record too short")
        count, addr, typ = rec[0], int.from_bytes(rec[1:3], "big"), rec[3]
        data = rec[4:-1]
        if len(rec) != count + 5: raise HexFormatError(f"{path}:{line_no}: byte count mismatch")
        if sum(rec) & 0xFF: raise HexFormatError(f"{path}:{line_no}: checksum error")
        if typ == 0x00:
            absolute = upper + addr
            for i, value in enumerate(data):
                a = absolute + i
                if a in memory and memory[a] != value:
                    raise HexFormatError(f"{path}:{line_no}: conflicting byte at 0x{a:08X}")
                memory[a] = value
        elif typ == 0x01:
            if count != 0: raise HexFormatError(f"{path}:{line_no}: malformed EOF")
            eof_seen = True
        elif typ == 0x02:
            if count != 2: raise HexFormatError(f"{path}:{line_no}: malformed segment address")
            upper = int.from_bytes(data, "big") << 4
        elif typ == 0x04:
            if count != 2: raise HexFormatError(f"{path}:{line_no}: malformed linear address")
            upper = int.from_bytes(data, "big") << 16
        elif typ in (0x03, 0x05):
            continue
        else: raise HexFormatError(f"{path}:{line_no}: unsupported record type 0x{typ:02X}")
    if not eof_seen: raise HexFormatError(f"{path}: missing EOF record")
    if not memory: raise HexFormatError(f"{path}: no data records found")
    return memory

def make_ihex(memory: dict[int, int], record_size: int = 16) -> str:
    if not memory: raise ValueError("No data to write")
    lines, current_upper = [], None
    addresses = sorted(memory)
    i = 0
    while i < len(addresses):
        start = addresses[i]; upper = start >> 16
        if upper != current_upper:
            raw = bytes((2, 0, 0, 4)) + upper.to_bytes(2, "big")
            lines.append(":" + (raw + bytes((_checksum(raw),))).hex().upper())
            current_upper = upper
        chunk, expected = bytearray(), start
        while i < len(addresses) and len(chunk) < record_size:
            a = addresses[i]
            if a != expected or (a >> 16) != upper: break
            chunk.append(memory[a]); expected += 1; i += 1
        raw = bytes((len(chunk), (start >> 8) & 255, start & 255, 0)) + bytes(chunk)
        lines.append(":" + (raw + bytes((_checksum(raw),))).hex().upper())
    eof = bytes((0, 0, 0, 1))
    lines.append(":" + (eof + bytes((_checksum(eof),))).hex().upper())
    return "\n".join(lines) + "\n"

def crc16(data: bytes) -> int:
    crc = CRC_INIT
    for b in data:
        crc ^= b << 8
        for _ in range(8): crc = (((crc << 1) ^ CRC_POLY) if crc & 0x8000 else (crc << 1)) & 0xFFFF
    return crc

def validate_region(memory: dict[int, int], start: int, end: int, label: str) -> None:
    outside = [a for a in memory if not start <= a < end]
    if outside:
        a = min(outside)
        raise ValueError(f"{label} HEX has address 0x{a:08X} outside allowed range 0x{start:08X}–0x{end-1:08X}")

def parse_address(value: str, label: str) -> int:
    try:
        address = int(value.strip(), 0)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a decimal or 0x-prefixed hexadecimal number") from exc
    if not 0 <= address <= 0xFFFFFFFF:
        raise ValueError(f"{label} must fit in a 32-bit address")
    return address

def build_package(boot_path: str, app_path: str, hex_path: str, bin_path: str | None, version: int,
                  flash_start: int, flash_size_kb: int, boot_start: int, app_start: int,
                  metadata_start: int, metadata_size_kb: int) -> dict:
    if flash_size_kb <= 0 or metadata_size_kb <= 0:
        raise ValueError("Flash size and metadata page size must be positive")
    flash_end = flash_start + flash_size_kb * 1024
    boot_end, app_end = app_start, metadata_start
    metadata_end = metadata_start + metadata_size_kb * 1024
    if not (flash_start <= boot_start < app_start < metadata_start < flash_end):
        raise ValueError("Address order must be: Flash base <= Bootloader start < Application start < Metadata start < Flash end")
    if boot_start < flash_start or metadata_end > flash_end:
        raise ValueError("Bootloader/metadata region is outside the configured Flash range")
    if app_start >= app_end or boot_start >= boot_end:
        raise ValueError("Memory regions have zero or negative size")
    if not 0 <= version <= 0xFFFF: raise ValueError("Firmware version must be 0..65535")
    boot, app = parse_ihex(boot_path), parse_ihex(app_path)
    validate_region(boot, boot_start, boot_end, "Bootloader")
    validate_region(app, app_start, app_end, "Application")
    if app_start not in app:
        raise ValueError(f"Application has no byte at 0x{app_start:08X}; check linker FLASH ORIGIN")
    if Path(hex_path).resolve() in (Path(boot_path).resolve(), Path(app_path).resolve()):
        raise ValueError("Output HEX path must not overwrite either input file")
    if bin_path and Path(bin_path).resolve() in (Path(boot_path).resolve(), Path(app_path).resolve()):
        raise ValueError("Output BIN path must not overwrite either input file")
    app_size = app_end - app_start
    app_length = max(app) - app_start + 1
    if not 0 < app_length <= app_size: raise ValueError("Application span exceeds application region")
    # Firmware CRC reads every address in [app_start, app_start + size), so holes are 0xFF.
    app_image = bytearray(b"\xFF" * app_length)
    for a, b in app.items(): app_image[a - app_start] = b
    crc = crc16(bytes(app_image))
    metadata = META.pack(MAGIC, app_start, app_length, crc, version, STATUS_VALID, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    combined: dict[int, int] = {}
    for region in (boot, app):
        overlap = combined.keys() & region.keys()
        if overlap: raise ValueError(f"Input HEX files overlap at 0x{min(overlap):08X}")
        combined.update(region)
    for i, b in enumerate(metadata):
        a = metadata_start + i
        if a in combined: raise ValueError(f"Metadata overlaps input data at 0x{a:08X}")
        combined[a] = b
    out_hex = Path(hex_path)
    out_hex.parent.mkdir(parents=True, exist_ok=True)
    out_hex.write_text(make_ihex(combined), encoding="ascii", newline="\n")
    reread = parse_ihex(out_hex)
    if reread != combined: raise RuntimeError("HEX output verification failed: data differs after re-reading")
    if bin_path:
        image = bytearray(b"\xFF" * (flash_size_kb * 1024))
        for a, b in combined.items():
            if not flash_start <= a < flash_end: raise ValueError(f"Address 0x{a:08X} is outside configured Flash")
            image[a - flash_start] = b
        Path(bin_path).parent.mkdir(parents=True, exist_ok=True)
        Path(bin_path).write_bytes(image)
        # Verify BIN content against the combined address map.
        saved = Path(bin_path).read_bytes()
        if len(saved) != flash_size_kb * 1024 or any(saved[a-flash_start] != b for a, b in combined.items()):
            raise RuntimeError("BIN output verification failed")
    return {"hex": str(out_hex), "bin": str(Path(bin_path)) if bin_path else None,
            "boot_bytes": len(boot), "app_bytes": len(app), "app_span": app_length,
            "crc": crc, "version": version, "metadata_bytes": len(metadata),
            "combined_bytes": len(combined), "hex_records": len(out_hex.read_text(encoding="ascii").splitlines()),
            "bin_size": flash_size_kb * 1024 if bin_path else 0,
            "flash_start": flash_start, "flash_end": flash_end,
            "boot_start": boot_start, "boot_end": boot_end,
            "app_start": app_start, "app_end": app_end,
            "metadata_start": metadata_start, "metadata_end": metadata_end}

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME); self.geometry("880x650"); self.minsize(790, 590)
        self.boot_var, self.app_var = tk.StringVar(), tk.StringVar()
        self.hex_var = tk.StringVar()
        self.bin_var = tk.StringVar()
        self.version_var = tk.StringVar(value="1")
        self.flash_start_var = tk.StringVar(value="0x08000000")
        self.flash_size_var = tk.StringVar(value="32")
        self.boot_start_var = tk.StringVar(value="0x08000000")
        self.app_start_var = tk.StringVar(value="0x08001800")
        self.metadata_start_var = tk.StringVar(value="0x08007800")
        self.metadata_size_var = tk.StringVar(value="2")
        self.make_bin_var = tk.BooleanVar(value=True)
        self._ui()

    def _ui(self):
        root = ttk.Frame(self, padding=18); root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1); root.rowconfigure(15, weight=1)
        ttk.Label(root, text="STM32G030 Firmware Package Tool", font=("Segoe UI", 17, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(root, text="Validate and merge Bootloader + Application + CRC metadata; optionally export a full 32 KiB BIN.", wraplength=800).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 16))
        self.file_row(root, 2, "Bootloader HEX", self.boot_var, self.pick_boot)
        self.file_row(root, 3, "Application HEX", self.app_var, self.pick_app)
        self.file_row(root, 4, "Output combined HEX", self.hex_var, self.pick_hex)
        ttk.Checkbutton(root, text="Also generate full-flash BIN (32 KiB, base address 0x08000000)", variable=self.make_bin_var, command=self.toggle_bin).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.bin_entry_row = ttk.Frame(root); self.bin_entry_row.grid(row=6, column=0, columnspan=3, sticky="ew")
        self.bin_entry_row.columnconfigure(1, weight=1)
        ttk.Label(self.bin_entry_row, text="Output BIN").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(self.bin_entry_row, textvariable=self.bin_var).grid(row=0, column=1, sticky="ew", padx=(10, 8), pady=5)
        ttk.Button(self.bin_entry_row, text="Browse…", command=self.pick_bin).grid(row=0, column=2, pady=5)
        ttk.Label(root, text="Firmware version (uint16; decimal or 0xHEX)").grid(row=7, column=0, sticky="w", pady=(7, 4))
        ttk.Entry(root, textvariable=self.version_var, width=18).grid(row=7, column=1, sticky="w", pady=(7, 4))
        mem = ttk.LabelFrame(root, text="Memory layout — editable (addresses accept 0xHEX)", padding=10)
        mem.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 6))
        for col in (1, 3): mem.columnconfigure(col, weight=1)
        fields = [
            ("Flash base", self.flash_start_var, 0, 0), ("Flash size (KiB)", self.flash_size_var, 0, 2),
            ("Bootloader start", self.boot_start_var, 1, 0), ("Application start", self.app_start_var, 1, 2),
            ("Metadata start", self.metadata_start_var, 2, 0), ("Metadata page (KiB)", self.metadata_size_var, 2, 2),
        ]
        for label, var, row, col in fields:
            ttk.Label(mem, text=label).grid(row=row, column=col, sticky="w", padx=(0, 6), pady=4)
            ttk.Entry(mem, textvariable=var, width=18).grid(row=row, column=col+1, sticky="ew", padx=(0, 12), pady=4)
        ttk.Label(mem, text="Bootloader end = Application start − 1 | Application end = Metadata start − 1 | BIN base = Flash base", wraplength=800).grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))
        buttons = ttk.Frame(root); buttons.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(4, 10))
        ttk.Button(buttons, text="Generate & Verify Package", command=self.generate).pack(side="left", fill="x", expand=True, ipady=6)
        ttk.Button(buttons, text="Clear Log", command=self.clear_log).pack(side="left", padx=(8, 0), ipady=6)
        self.log = tk.Text(root, height=9, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.grid(row=10, column=0, columnspan=3, sticky="nsew")
        ttk.Label(root, text="Safety: erase target Flash before programming. HEX/BIN files do not erase old Flash by themselves.", foreground="#9A3412", wraplength=820).grid(row=11, column=0, columnspan=3, sticky="w", pady=(9, 0))

    def file_row(self, parent, row, label, var, callback):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew", padx=(10, 8), pady=5)
        ttk.Button(parent, text="Browse…", command=callback).grid(row=row, column=2, pady=5)
    def pick(self, title, save=False, initial=""):
        if save:
            return filedialog.asksaveasfilename(title=title, defaultextension=Path(initial).suffix or ".hex", initialfile=initial, filetypes=[("Firmware file", "*.hex *.bin"), ("All files", "*.*")])
        return filedialog.askopenfilename(title=title, filetypes=[("Intel HEX", "*.hex *.ihx"), ("All files", "*.*")])
    def pick_boot(self):
        p=self.pick("Select bootloader HEX")
        if p: self.boot_var.set(p); self.suggest_outputs()
    def pick_app(self):
        p=self.pick("Select application HEX")
        if p: self.app_var.set(p); self.suggest_outputs()
    def suggest_outputs(self):
        base = Path(self.app_var.get() or self.boot_var.get())
        if base.suffix: base = base.with_suffix("")
        if not self.hex_var.get(): self.hex_var.set(str(base.parent / (base.name + "_combined.hex")))
        if not self.bin_var.get(): self.bin_var.set(str(base.parent / (base.name + "_combined.bin")))
    def pick_hex(self):
        p=self.pick("Save combined Intel HEX", True, "stm32g030_combined.hex")
        if p: self.hex_var.set(p)
    def pick_bin(self):
        p=self.pick("Save full-flash BIN", True, "stm32g030_combined.bin")
        if p: self.bin_var.set(p)
    def toggle_bin(self):
        if self.make_bin_var.get(): self.bin_entry_row.grid()
        else: self.bin_entry_row.grid_remove()
    def write_log(self, msg):
        self.log.configure(state="normal"); self.log.insert("end", msg + "\n"); self.log.see("end"); self.log.configure(state="disabled")
    def clear_log(self):
        self.log.configure(state="normal"); self.log.delete("1.0", "end"); self.log.configure(state="disabled")
    def generate(self):
        boot, app, out_hex = self.boot_var.get().strip(), self.app_var.get().strip(), self.hex_var.get().strip()
        out_bin = self.bin_var.get().strip() if self.make_bin_var.get() else None
        if not boot or not app or not out_hex or (self.make_bin_var.get() and not out_bin):
            messagebox.showerror("Missing input", "Select both input HEX files and all enabled output paths."); return
        if not Path(boot).is_file() or not Path(app).is_file():
            messagebox.showerror("File not found", "Bootloader or application HEX file does not exist."); return
        try:
            version = int(self.version_var.get().strip(), 0)
            flash_start = parse_address(self.flash_start_var.get(), "Flash base")
            flash_size = int(self.flash_size_var.get().strip(), 0)
            boot_start = parse_address(self.boot_start_var.get(), "Bootloader start")
            app_start = parse_address(self.app_start_var.get(), "Application start")
            metadata_start = parse_address(self.metadata_start_var.get(), "Metadata start")
            metadata_size = int(self.metadata_size_var.get().strip(), 0)
            r = build_package(boot, app, out_hex, out_bin, version, flash_start, flash_size,
                              boot_start, app_start, metadata_start, metadata_size)
        except Exception as exc:
            self.write_log("ERROR: " + str(exc)); messagebox.showerror("Package generation failed", str(exc)); return
        self.write_log("=" * 64)
        self.write_log("PACKAGE GENERATED AND VERIFIED")
        self.write_log(f"Bootloader data:        {r['boot_bytes']:,} bytes")
        self.write_log(f"Application HEX data:   {r['app_bytes']:,} bytes")
        self.write_log(f"Application CRC span:   {r['app_span']:,} bytes")
        self.write_log(f"CRC-16/CCITT-FALSE:     0x{r['crc']:04X}")
        self.write_log(f"Firmware version:       {r['version']} (0x{r['version']:04X})")
        self.write_log(f"Flash range:             0x{r['flash_start']:08X}–0x{r['flash_end']-1:08X}")
        self.write_log(f"Bootloader range:        0x{r['boot_start']:08X}–0x{r['boot_end']-1:08X}")
        self.write_log(f"Application range:       0x{r['app_start']:08X}–0x{r['app_end']-1:08X}")
        self.write_log(f"Metadata:               {r['metadata_bytes']} bytes @ 0x{r['metadata_start']:08X}")
        self.write_log(f"Combined HEX data:      {r['combined_bytes']:,} bytes / {r['hex_records']} records")
        self.write_log(f"HEX output: {r['hex']}")
        if r['bin']:
            self.write_log(f"BIN output: {r['bin']} ({r['bin_size']:,} bytes; base 0x{r['flash_start']:08X})")
        self.write_log("Verified by re-reading generated output file(s).")
        self.write_log("IMPORTANT: erase target Flash before programming.")
        messagebox.showinfo("Package ready", f"Package generated and verified.\n\nApp span: {r['app_span']:,} bytes\nCRC: 0x{r['crc']:04X}\nVersion: {r['version']}\n" + (f"BIN: {r['bin_size']:,} bytes\nBase: 0x{r['flash_start']:08X}\n\n" if r['bin'] else "\n") + "Erase target Flash before programming.")

if __name__ == "__main__":
    App().mainloop()
