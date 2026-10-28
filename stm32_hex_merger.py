#!/usr/bin/env python3
"""Advanced STM32G030F6P6 bootloader/application HEX merger (stdlib only)."""
from __future__ import annotations
import struct
from pathlib import Path

APP_NAME = "STM32 HEX Merger — FlashForge"
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


from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QPushButton,
    QFileDialog, QMessageBox, QCheckBox, QGridLayout, QVBoxLayout,
    QHBoxLayout, QGroupBox, QTextEdit, QSpinBox, QFormLayout
)
import sys

class App(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(940, 760)
        self.setMinimumSize(820, 680)
        self.fields = {}
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        title = QLabel("STM32 HEX Merger")
        title.setObjectName("title")
        subtitle = QLabel("Merge Bootloader + Application, generate CRC metadata, and export HEX/BIN.")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        files_box = QGroupBox("Firmware files")
        files = QGridLayout(files_box)
        self.boot = self.add_file_row(files, 0, "Bootloader HEX", "Select bootloader HEX", False)
        self.apphex = self.add_file_row(files, 1, "Application HEX", "Select application HEX", False)
        self.outhex = self.add_file_row(files, 2, "Output combined HEX", "Save combined HEX", True, "stm32_combined.hex")
        root.addWidget(files_box)

        self.bin_check = QCheckBox("Also generate full-Flash BIN image")
        self.bin_check.setChecked(True)
        self.bin_check.toggled.connect(self.toggle_bin)
        root.addWidget(self.bin_check)
        bin_row = QHBoxLayout()
        bin_row.addWidget(QLabel("Output BIN"))
        self.bin_edit = QLineEdit()
        bin_row.addWidget(self.bin_edit, 1)
        bin_btn = QPushButton("Browse…")
        bin_btn.clicked.connect(self.pick_bin)
        bin_row.addWidget(bin_btn)
        root.addLayout(bin_row)
        self.bin_row_widgets = [self.bin_edit, bin_btn]

        settings = QGroupBox("Firmware / memory layout (addresses accept 0x-prefixed HEX)")
        grid = QGridLayout(settings)
        defaults = [
            ("Firmware version (uint16)", "version", "1", 0, 0),
            ("Flash base", "flash_start", "0x08000000", 0, 2),
            ("Flash size (KiB)", "flash_size", "32", 1, 0),
            ("Bootloader start", "boot_start", "0x08000000", 1, 2),
            ("Application start", "app_start", "0x08001800", 2, 0),
            ("Metadata start", "metadata_start", "0x08007800", 2, 2),
            ("Metadata page (KiB)", "metadata_size", "2", 3, 0),
        ]
        for label, key, value, row, col in defaults:
            grid.addWidget(QLabel(label), row, col)
            edit = QLineEdit(value)
            edit.setMinimumWidth(180)
            grid.addWidget(edit, row, col + 1)
            self.fields[key] = edit
        root.addWidget(settings)

        buttons = QHBoxLayout()
        self.generate_btn = QPushButton("Generate & Verify Package")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.clicked.connect(self.generate)
        buttons.addWidget(self.generate_btn, 1)
        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(lambda: self.log.clear())
        buttons.addWidget(clear_btn)
        root.addLayout(buttons)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Validation results and output details will appear here…")
        root.addWidget(self.log, 1)
        warning = QLabel("Safety: HEX/BIN files do not erase old target Flash. Erase the required Flash region before programming.")
        warning.setWordWrap(True)
        warning.setObjectName("warning")
        root.addWidget(warning)
        self.setStyleSheet("""
            QMainWindow { background: #f4f6f8; }
            QWidget { font-family: 'Segoe UI'; font-size: 10pt; color: #17212b; }
            QLabel#title { font-size: 21pt; font-weight: 700; color: #12324a; padding-top: 4px; }
            QGroupBox { font-weight: 600; border: 1px solid #cbd5df; border-radius: 8px; margin-top: 10px; padding: 12px; background: white; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QLineEdit { background: white; border: 1px solid #bac6d1; border-radius: 5px; padding: 7px; }
            QPushButton { padding: 8px 13px; border: 1px solid #bac6d1; border-radius: 5px; background: white; }
            QPushButton:hover { background: #eaf1f7; }
            QPushButton#primary { background: #1769aa; color: white; font-weight: 600; border: none; padding: 11px; }
            QPushButton#primary:hover { background: #12568d; }
            QTextEdit { background: #101923; color: #d7e5ef; border-radius: 6px; padding: 8px; font-family: Consolas; }
            QLabel#warning { color: #8a3c12; padding: 5px 0; }
        """)
        self.toggle_bin(self.bin_check.isChecked())

    def add_file_row(self, layout, row, label, title, save=False, initial=""):
        layout.addWidget(QLabel(label), row, 0)
        edit = QLineEdit()
        layout.addWidget(edit, row, 1)
        btn = QPushButton("Browse…")
        btn.clicked.connect(lambda: self.choose_file(edit, title, save, initial))
        layout.addWidget(btn, row, 2)
        return edit

    def choose_file(self, edit, title, save=False, initial=""):
        if save:
            path, _ = QFileDialog.getSaveFileName(self, title, initial, "Intel HEX (*.hex);;All files (*)")
        else:
            path, _ = QFileDialog.getOpenFileName(self, title, "", "Intel HEX (*.hex *.ihx);;All files (*)")
        if path:
            edit.setText(path)
            self.suggest_outputs()

    def suggest_outputs(self):
        source = self.apphex.text().strip() or self.boot.text().strip()
        if not source: return
        base = Path(source)
        stem = base.with_suffix("")
        if not self.outhex.text().strip(): self.outhex.setText(str(stem.parent / (stem.name + "_combined.hex")))
        if not self.bin_edit.text().strip(): self.bin_edit.setText(str(stem.parent / (stem.name + "_combined.bin")))

    def pick_bin(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save full-Flash BIN", "stm32_combined.bin", "Binary image (*.bin);;All files (*)")
        if path: self.bin_edit.setText(path)

    def toggle_bin(self, enabled):
        for widget in getattr(self, "bin_row_widgets", []): widget.setVisible(enabled)

    def log_line(self, text): self.log.append(text)

    def generate(self):
        boot, app, out_hex = self.boot.text().strip(), self.apphex.text().strip(), self.outhex.text().strip()
        out_bin = self.bin_edit.text().strip() if self.bin_check.isChecked() else None
        if not boot or not app or not out_hex or (self.bin_check.isChecked() and not out_bin):
            QMessageBox.warning(self, "Missing input", "Select both input HEX files and all enabled output paths.")
            return
        if not Path(boot).is_file() or not Path(app).is_file():
            QMessageBox.critical(self, "File not found", "Bootloader or application HEX file does not exist.")
            return
        try:
            version = int(self.fields["version"].text().strip(), 0)
            flash_start = parse_address(self.fields["flash_start"].text(), "Flash base")
            flash_size = int(self.fields["flash_size"].text().strip(), 0)
            boot_start = parse_address(self.fields["boot_start"].text(), "Bootloader start")
            app_start = parse_address(self.fields["app_start"].text(), "Application start")
            metadata_start = parse_address(self.fields["metadata_start"].text(), "Metadata start")
            metadata_size = int(self.fields["metadata_size"].text().strip(), 0)
            result = build_package(boot, app, out_hex, out_bin, version, flash_start, flash_size,
                                   boot_start, app_start, metadata_start, metadata_size)
        except Exception as exc:
            self.log_line("ERROR: " + str(exc))
            QMessageBox.critical(self, "Package generation failed", str(exc))
            return
        self.log_line("=" * 62)
        self.log_line("PACKAGE GENERATED AND VERIFIED")
        self.log_line(f"Bootloader data:       {result['boot_bytes']:,} bytes")
        self.log_line(f"Application HEX data:  {result['app_bytes']:,} bytes")
        self.log_line(f"Application CRC span:  {result['app_span']:,} bytes")
        self.log_line(f"CRC-16/CCITT-FALSE:    0x{result['crc']:04X}")
        self.log_line(f"Firmware version:      {result['version']} (0x{result['version']:04X})")
        self.log_line(f"Flash range:           0x{result['flash_start']:08X}–0x{result['flash_end']-1:08X}")
        self.log_line(f"Bootloader range:      0x{result['boot_start']:08X}–0x{result['boot_end']-1:08X}")
        self.log_line(f"Application range:     0x{result['app_start']:08X}–0x{result['app_end']-1:08X}")
        self.log_line(f"Metadata:              {result['metadata_bytes']} bytes @ 0x{result['metadata_start']:08X}")
        self.log_line(f"Combined HEX data:     {result['combined_bytes']:,} bytes / {result['hex_records']} records")
        self.log_line(f"HEX output: {result['hex']}")
        if result['bin']:
            self.log_line(f"BIN output: {result['bin']} ({result['bin_size']:,} bytes; base 0x{result['flash_start']:08X})")
        self.log_line("Verified by re-reading generated output file(s).")
        self.log_line("IMPORTANT: erase target Flash before programming.")
        QMessageBox.information(self, "Package ready", f"Package generated and verified.\n\nApp span: {result['app_span']:,} bytes\nCRC: 0x{result['crc']:04X}\nVersion: {result['version']}\n" + (f"BIN: {result['bin_size']:,} bytes\nBase: 0x{result['flash_start']:08X}\n\n" if result['bin'] else "\n") + "Erase target Flash before programming.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = App()
    window.show()
    sys.exit(app.exec())
