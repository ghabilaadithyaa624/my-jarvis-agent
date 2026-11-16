"""Smart non-destructive Directory Organizer & Two-Stage NVMe Duplicate File Finder."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.filesystem.operations import SKIP_SEARCH_DIRS, format_size
from jarvis.security.emergency import CancellationToken


CATEGORY_MAP: Dict[str, List[str]] = {
    "Documents": [".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".md", ".ppt", ".pptx", ".epub"],
    "Spreadsheets": [".xls", ".xlsx", ".csv", ".ods", ".tsv"],
    "Images": [".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".ico", ".tiff", ".heic", ".psd"],
    "Videos": [".mp4", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".m4v"],
    "Audio": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".opus", ".wma"],
    "Archives": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"],
    "Installers": [".exe", ".msi", ".msix", ".appx", ".dmg", ".deb", ".rpm"],
    "Code": [".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml", ".sql", ".cpp", ".c", ".java", ".rs", ".go", ".ipynb", ".ps1", ".bat"],
    "3D_Blender": [".blend", ".blend1", ".fbx", ".obj", ".stl", ".gltf", ".glb", ".dae", ".usd", ".usdc"],
    "Fonts": [".ttf", ".otf", ".woff", ".woff2"],
}

_EXT_TO_CATEGORY: Dict[str, str] = {
    ext: cat for cat, exts in CATEGORY_MAP.items() for ext in exts
}


def _collision_safe_path(dest_dir: Path, filename: str) -> Path:
    """Generate a unique filename inside dest_dir so existing files are NEVER overwritten."""
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        alt = dest_dir / f"{stem} ({counter}){suffix}"
        if not alt.exists():
            return alt
        counter += 1


class DirectoryOrganizer:
    """Organizes cluttered folders (like Downloads) safely with a reversible manifest."""

    def organize(
        self,
        directory: str,
        dry_run: bool = False,
        token: Optional[CancellationToken] = None,
    ) -> Dict[str, Any]:
        root = Path(os.path.expandvars(directory)).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return {"success": False, "verified": True, "error": f"Not a valid directory: {root}"}

        planned_moves: List[Dict[str, str]] = []
        category_counts: Dict[str, int] = defaultdict(int)
        skipped: List[str] = []

        for item in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if token and token.is_cancelled:
                token.raise_if_cancelled()
            if item.is_dir():
                continue
            # Skip hidden, partial browser downloads, and Jarvis manifest files
            name_low = item.name.lower()
            if (
                item.name.startswith(".")
                or item.name.startswith("_jarvis_organize_manifest_")
                or name_low in {"desktop.ini", "thumbs.db"}
                or item.suffix.lower() in {".crdownload", ".part", ".tmp", ".download"}
            ):
                skipped.append(item.name)
                continue

            category = _EXT_TO_CATEGORY.get(item.suffix.lower(), "Other")
            dest_dir = root / category
            dest_path = _collision_safe_path(dest_dir, item.name)
            planned_moves.append(
                {
                    "file": item.name,
                    "category": category,
                    "source": str(item),
                    "destination": str(dest_path),
                }
            )
            category_counts[category] += 1

        if dry_run or not planned_moves:
            return {
                "success": True,
                "verified": True,
                "dry_run": dry_run,
                "directory": str(root),
                "moved_count": 0,
                "planned_count": len(planned_moves),
                "by_category": dict(category_counts),
                "skipped_count": len(skipped),
                "preview": planned_moves[:30],
            }

        executed: List[Dict[str, str]] = []
        errors: List[str] = []
        for mv in planned_moves:
            if token and token.is_cancelled:
                token.raise_if_cancelled()
            src = Path(mv["source"])
            dest_dir = root / mv["category"]
            dest_dir.mkdir(parents=True, exist_ok=True)
            dst = _collision_safe_path(dest_dir, src.name)
            try:
                shutil.move(str(src), str(dst))
                if dst.is_file() and not src.exists():
                    executed.append({**mv, "destination": str(dst)})
                else:
                    errors.append(f"Verification failed moving {src.name}")
            except Exception as exc:
                errors.append(f"{src.name}: {exc}")

        manifest_path: Optional[str] = None
        if executed:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            mfile = root / f"_jarvis_organize_manifest_{stamp}.json"
            mfile.write_text(
                json.dumps(
                    {
                        "created_at": datetime.now().isoformat(),
                        "directory": str(root),
                        "moves": executed,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manifest_path = str(mfile)

        verified = len(errors) == 0 and all(Path(m["destination"]).is_file() for m in executed)
        return {
            "success": len(errors) == 0,
            "verified": verified,
            "dry_run": False,
            "directory": str(root),
            "moved_count": len(executed),
            "by_category": dict(category_counts),
            "skipped_count": len(skipped),
            "manifest_path": manifest_path,
            "errors": errors,
        }


class DuplicateFinder:
    """Fast 3-pass duplicate detector: byte-size -> partial 64KB hash -> full SHA-256."""

    @staticmethod
    def _quick_hash(path: Path, size: int) -> str:
        h = hashlib.sha256()
        chunk = 65536
        with path.open("rb") as fh:
            h.update(fh.read(chunk))
            if size > chunk * 2:
                fh.seek(size - chunk)
                h.update(fh.read(chunk))
        return h.hexdigest()

    @staticmethod
    def _full_hash(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            while True:
                buf = fh.read(1024 * 1024)
                if not buf:
                    break
                h.update(buf)
        return h.hexdigest()

    def find_duplicates(
        self,
        directory: str,
        min_size_bytes: int = 1,
        max_groups: int = 50,
        token: Optional[CancellationToken] = None,
    ) -> Dict[str, Any]:
        root = Path(os.path.expandvars(directory)).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return {"success": False, "verified": True, "error": f"Not a valid directory: {root}"}

        # Pass 1: Group by exact file size
        by_size: Dict[int, List[Path]] = defaultdict(list)
        total_files = 0
        for cur_dir, dirnames, filenames in os.walk(root, topdown=True):
            if token and token.is_cancelled:
                token.raise_if_cancelled()
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_SEARCH_DIRS]
            for fn in filenames:
                fp = Path(cur_dir) / fn
                if fp.is_symlink():
                    continue
                try:
                    sz = fp.stat().st_size
                except OSError:
                    continue
                total_files += 1
                if sz >= min_size_bytes:
                    by_size[sz].append(fp)

        # Pass 2: Partial hash filter
        by_quick: Dict[tuple[int, str], List[Path]] = defaultdict(list)
        for sz, paths in by_size.items():
            if len(paths) < 2:
                continue
            for fp in paths:
                if token and token.is_cancelled:
                    token.raise_if_cancelled()
                try:
                    by_quick[(sz, self._quick_hash(fp, sz))].append(fp)
                except OSError:
                    continue

        # Pass 3: Cryptographic full SHA-256 verification
        by_full: Dict[tuple[int, str], List[Path]] = defaultdict(list)
        for (sz, _qh), paths in by_quick.items():
            if len(paths) < 2:
                continue
            for fp in paths:
                if token and token.is_cancelled:
                    token.raise_if_cancelled()
                try:
                    by_full[(sz, self._full_hash(fp))].append(fp)
                except OSError:
                    continue

        groups: List[Dict[str, Any]] = []
        wasted_bytes = 0
        for (sz, digest), paths in sorted(by_full.items(), key=lambda kv: -kv[0][0] * (len(kv[1]) - 1)):
            if len(paths) < 2:
                continue
            group_waste = sz * (len(paths) - 1)
            wasted_bytes += group_waste
            if len(groups) < max_groups:
                groups.append(
                    {
                        "sha256": digest[:16],
                        "size_bytes": sz,
                        "size_human": format_size(sz),
                        "wasted_human": format_size(group_waste),
                        "files": [str(p) for p in sorted(paths)],
                    }
                )

        return {
            "success": True,
            "verified": True,
            "directory": str(root),
            "scanned_files": total_files,
            "duplicate_groups": len(groups),
            "reclaimable_bytes": wasted_bytes,
            "reclaimable_human": format_size(wasted_bytes),
            "groups": groups,
            "note": "No files were deleted. Specify exact paths and confirm if you wish to remove duplicates.",
        }
