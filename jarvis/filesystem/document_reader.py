"""Multi-format local document reader and Blender (.blend) binary integrity inspector."""

from __future__ import annotations

import csv
import gzip
import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List


TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".py", ".js", ".ts", ".jsx", ".tsx",
    ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".log", ".bat", ".ps1", ".cmd", ".sh", ".sql", ".xml", ".c", ".cpp", ".h",
    ".java", ".rs", ".go", ".cs",
}


class DocumentReader:
    """Safely reads supported local documents without modifying them."""

    def __init__(self, max_bytes: int = 2_000_000, max_output_chars: int = 12_000) -> None:
        self.max_bytes = max_bytes
        self.max_output_chars = max_output_chars

    def read(self, path: str) -> Dict[str, Any]:
        target = Path(os.path.expandvars(path)).expanduser().resolve()
        if not target.exists():
            return {"success": False, "verified": True, "error": f"File not found: {target}"}
        if not target.is_file():
            return {"success": False, "verified": True, "error": f"Not a regular file: {target}"}

        ext = target.suffix.lower()
        size = target.stat().st_size

        if ext == ".blend":
            return self.inspect_blend_file(str(target))
        if ext == ".docx":
            return self._read_docx(target)
        if ext == ".pdf":
            return self._read_pdf(target)
        if ext == ".xlsx":
            return self._read_xlsx(target)
        if ext == ".csv":
            return self._read_csv(target)

        if ext in TEXT_EXTENSIONS or size <= self.max_bytes:
            return self._read_text(target, size)

        return {
            "success": False,
            "verified": True,
            "error": f"Unsupported binary format '{ext}' ({size} bytes).",
        }

    def _truncate(self, text: str) -> tuple[str, bool]:
        if len(text) > self.max_output_chars:
            return text[: self.max_output_chars] + "\n...[truncated]", True
        return text, False

    def _read_text(self, target: Path, size: int) -> Dict[str, Any]:
        with target.open("rb") as fh:
            raw = fh.read(min(size, self.max_bytes))
        if b"\x00" in raw[:2048]:
            return {
                "success": False,
                "verified": True,
                "error": f"'{target.name}' appears to be a binary file and cannot be read as plain text.",
            }
        text = raw.decode("utf-8", errors="replace")
        content, truncated = self._truncate(text)
        return {
            "success": True,
            "verified": True,
            "path": str(target),
            "format": target.suffix.lower() or "text",
            "size_bytes": size,
            "line_count": text.count("\n") + (1 if text else 0),
            "truncated": truncated or (size > self.max_bytes),
            "content": content,
        }

    def _read_csv(self, target: Path) -> Dict[str, Any]:
        rows_out: List[str] = []
        with target.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            for idx, row in enumerate(reader):
                if idx >= 100:
                    break
                rows_out.append(" | ".join(row))
        content, truncated = self._truncate("\n".join(rows_out))
        return {
            "success": True,
            "verified": True,
            "path": str(target),
            "format": ".csv",
            "rows_previewed": len(rows_out),
            "truncated": truncated,
            "content": content,
        }

    def _read_docx(self, target: Path) -> Dict[str, Any]:
        if importlib.util.find_spec("docx") is None:
            return {
                "success": False,
                "verified": False,
                "error": "python-docx is not installed. Run: pip install python-docx",
            }
        import docx  # type: ignore

        doc = docx.Document(str(target))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        content, truncated = self._truncate("\n".join(paragraphs))
        return {
            "success": True,
            "verified": True,
            "path": str(target),
            "format": ".docx",
            "paragraphs": len(paragraphs),
            "truncated": truncated,
            "content": content,
        }

    def _read_pdf(self, target: Path) -> Dict[str, Any]:
        if importlib.util.find_spec("pypdf") is None:
            return {
                "success": False,
                "verified": False,
                "error": "pypdf is not installed. Run: pip install pypdf",
            }
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(target))
        pages_text: List[str] = []
        for idx, page in enumerate(reader.pages[:30]):
            pages_text.append(f"--- Page {idx + 1} ---\n{page.extract_text() or ''}")
        content, truncated = self._truncate("\n\n".join(pages_text))
        return {
            "success": True,
            "verified": True,
            "path": str(target),
            "format": ".pdf",
            "total_pages": len(reader.pages),
            "truncated": truncated or len(reader.pages) > 30,
            "content": content,
        }

    def _read_xlsx(self, target: Path) -> Dict[str, Any]:
        if importlib.util.find_spec("openpyxl") is None:
            return {
                "success": False,
                "verified": False,
                "error": "openpyxl is not installed. Run: pip install openpyxl",
            }
        import openpyxl  # type: ignore

        wb = openpyxl.load_workbook(str(target), read_only=True, data_only=True)
        lines: List[str] = []
        for name in wb.sheetnames[:5]:
            lines.append(f"=== Sheet: {name} ===")
            ws = wb[name]
            for r_idx, row in enumerate(ws.iter_rows(values_only=True)):
                if r_idx >= 50:
                    break
                lines.append(" | ".join("" if c is None else str(c) for c in row))
        wb.close()
        content, truncated = self._truncate("\n".join(lines))
        return {
            "success": True,
            "verified": True,
            "path": str(target),
            "format": ".xlsx",
            "sheets": wb.sheetnames,
            "truncated": truncated,
            "content": content,
        }

    def inspect_blend_file(self, blend_path: str) -> Dict[str, Any]:
        """Perform non-destructive binary header & project-health inspection on a .blend file."""
        target = Path(os.path.expandvars(blend_path)).expanduser().resolve()
        if not target.is_file():
            return {"success": False, "verified": True, "error": f"Blender file not found: {target}"}

        size = target.stat().st_size
        issues: List[str] = []
        compression = "none"
        blender_version = "unknown"
        pointer_bits = 0
        endianness = "unknown"
        header_valid = False

        if size < 12:
            issues.append("File is critically truncated (< 12 bytes) — likely corrupt.")
        else:
            with target.open("rb") as fh:
                head = fh.read(16)

            if head.startswith(b"BLENDER"):
                header_valid, blender_version, pointer_bits, endianness = self._parse_blender_magic(head)
                if not header_valid:
                    issues.append("Malformed uncompressed BLENDER header.")
            elif head[:2] == b"\x1f\x8b":
                compression = "gzip"
                try:
                    with gzip.open(target, "rb") as gzf:
                        inner = gzf.read(16)
                    header_valid, blender_version, pointer_bits, endianness = self._parse_blender_magic(inner)
                    if not header_valid:
                        issues.append("Gzip archive does not contain a valid BLENDER header.")
                except Exception as exc:
                    issues.append(f"Gzip stream corrupt: {exc}")
            elif head[:4] == b"\x28\xb5\x2f\xfd":
                # Blender 3.0+ uses Zstandard compression when Save Compressed is enabled
                compression = "zstd"
                header_valid = True
                blender_version = "3.0+ (Zstandard compressed)"
            else:
                issues.append("Unrecognized file signature — does not match BLENDER, gzip, or zstd .blend format.")

        # Check sibling project assets, backup files (.blend1/.blend2), and crash logs
        parent = target.parent
        backups = [p.name for p in parent.glob(f"{target.stem}.blend[0-9]*")]
        texture_dirs = [
            d.name for d in parent.iterdir()
            if d.is_dir() and d.name.lower() in {"textures", "tex", "assets", "renders", "cache", "audio", "scripts"}
        ]
        empty_texture_dirs = [
            d for d in texture_dirs if not any((parent / d).iterdir())
        ]
        for ed in empty_texture_dirs:
            issues.append(f"Companion folder '{ed}/' exists but is empty (verify linked assets).")

        # Check Windows/OS temp directory for recent Blender crash report matching this project
        crash_file = Path(tempfile.gettempdir()) / f"{target.stem}.crash.txt"
        crash_log_found = crash_file.is_file()
        if crash_log_found:
            issues.append(f"Found Blender crash log for this project: {crash_file}")

        healthy = header_valid and not any("corrupt" in i.lower() or "unrecognized" in i.lower() or "truncated" in i.lower() for i in issues)
        return {
            "success": True,
            "verified": True,
            "path": str(target),
            "project_dir": str(parent),
            "size_bytes": size,
            "size_mb": round(size / (1024 * 1024), 2),
            "header_valid": header_valid,
            "healthy": healthy,
            "compression": compression,
            "blender_version": blender_version,
            "pointer_bits": pointer_bits,
            "endianness": endianness,
            "backup_files": backups,
            "companion_folders": texture_dirs,
            "crash_log": str(crash_file) if crash_log_found else None,
            "issues": issues,
        }

    @staticmethod
    def _parse_blender_magic(header: bytes) -> tuple[bool, str, int, str]:
        """Decode both classic 12-byte ('BLENDER-v402') and Blender 5.0 17-byte headers."""
        if len(header) < 12 or not header.startswith(b"BLENDER"):
            return False, "unknown", 0, "unknown"
        ptr_ch = chr(header[7])
        end_ch = chr(header[8])
        if ptr_ch in {"_", "-"} and end_ch in {"v", "V"}:
            bits = 64 if ptr_ch == "-" else 32
            endian = "little" if end_ch == "v" else "big"
            ver_raw = header[9:12].decode("ascii", errors="ignore")
            if len(ver_raw) == 3 and ver_raw.isdigit():
                ver = f"{int(ver_raw[0])}.{int(ver_raw[1:])}"
                return True, ver, bits, endian
        # Blender 5.0+ extended header format: BLENDER17-01v0500
        if header[7:9].isdigit():
            ver_txt = header[7:16].decode("ascii", errors="ignore")
            return True, f"extended ({ver_txt})", 64, "little"
        return False, "unknown", 0, "unknown"
