"""Verified file & folder operations within permitted security boundaries."""

from __future__ import annotations

import fnmatch
import importlib.util
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from jarvis.security.emergency import CancellationToken


SKIP_SEARCH_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".cache",
    "$recycle.bin",
    "system volume information",
    "windows",
    "winsxs",
    "appdata",
}


def format_size(num_bytes: int) -> str:
    val = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0 or unit == "TB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{num_bytes} B"


class FileSystemManager:
    """Executes safe, verified file management tasks (never overwrites silently)."""

    def __init__(self, permitted_roots_provider, max_results: int = 100) -> None:
        self._get_roots = permitted_roots_provider
        self.max_results = max_results

    def list_directory(self, path: str, limit: int = 100) -> Dict[str, Any]:
        target = Path(os.path.expandvars(path)).expanduser().resolve()
        if not target.exists():
            return {"success": False, "verified": True, "error": f"Directory does not exist: {target}"}
        if not target.is_dir():
            return {"success": False, "verified": True, "error": f"Path is not a directory: {target}"}

        entries: List[Dict[str, Any]] = []
        total_count = 0
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            total_count += 1
            if len(entries) >= limit:
                continue
            try:
                st = child.stat()
                entries.append(
                    {
                        "name": child.name,
                        "path": str(child),
                        "type": "folder" if child.is_dir() else "file",
                        "size_bytes": 0 if child.is_dir() else st.st_size,
                        "size_human": "-" if child.is_dir() else format_size(st.st_size),
                        "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    }
                )
            except OSError:
                continue

        return {
            "success": True,
            "verified": True,
            "directory": str(target),
            "total_items": total_count,
            "shown_items": len(entries),
            "entries": entries,
        }

    def search_files(
        self,
        query: str = "",
        *,
        extension: str = "",
        directory: Optional[str] = None,
        item_type: str = "all",  # all | file | folder
        max_results: Optional[int] = None,
        token: Optional[CancellationToken] = None,
    ) -> Dict[str, Any]:
        """Search files and folders by substring/glob and extension across permitted paths."""
        cap = min(max_results or self.max_results, 500)
        roots: List[Path] = (
            [Path(os.path.expandvars(directory)).expanduser().resolve()]
            if directory
            else list(self._get_roots())
        )

        q = (query or "").strip().lower()
        # Tokenize natural multi-word queries like "blender educational" so "Edu_Blender_Project.blend" also matches!
        q_tokens = [t for t in q.replace("_", " ").replace("-", " ").split() if t]
        is_glob = any(ch in q for ch in "*?[]")

        ext = (extension or "").strip().lower()
        if ext and not ext.startswith("."):
            ext = "." + ext

        matches: List[Dict[str, Any]] = []
        seen_paths: set[str] = set()
        scanned_dirs = 0

        def _name_matches(name: str) -> bool:
            low = name.lower()
            if ext and not low.endswith(ext):
                return False
            if not q:
                return True
            if is_glob:
                return fnmatch.fnmatch(low, q)
            if q in low:
                return True
            normalized = low.replace("_", " ").replace("-", " ")
            return bool(q_tokens) and all(tok in normalized for tok in q_tokens)

        for root in roots:
            if not root.exists():
                continue
            for cur_dir, dirnames, filenames in os.walk(root, topdown=True):
                if token and token.is_cancelled:
                    token.raise_if_cancelled()
                scanned_dirs += 1
                # Prune noisy/protected subdirectories in-place for blazing NVMe speed
                dirnames[:] = [
                    d for d in dirnames if d.lower() not in SKIP_SEARCH_DIRS and not d.startswith("~$")
                ]

                if item_type in {"all", "folder"} and not ext:
                    for d in dirnames:
                        if _name_matches(d):
                            full = str((Path(cur_dir) / d).resolve())
                            if full not in seen_paths:
                                seen_paths.add(full)
                                matches.append({"name": d, "path": full, "type": "folder"})
                                if len(matches) >= cap:
                                    break
                if len(matches) >= cap:
                    break

                if item_type in {"all", "file"}:
                    for fname in filenames:
                        if _name_matches(fname):
                            fpath = (Path(cur_dir) / fname).resolve()
                            full = str(fpath)
                            if full in seen_paths:
                                continue
                            seen_paths.add(full)
                            try:
                                st = fpath.stat()
                                size_b = st.st_size
                                mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
                            except OSError:
                                size_b = 0
                                mtime = ""
                            matches.append(
                                {
                                    "name": fname,
                                    "path": full,
                                    "type": "file",
                                    "size_bytes": size_b,
                                    "size_human": format_size(size_b),
                                    "modified": mtime,
                                }
                            )
                            if len(matches) >= cap:
                                break
                if len(matches) >= cap:
                    break
            if len(matches) >= cap:
                break

        return {
            "success": True,
            "verified": True,
            "query": query,
            "extension": ext,
            "searched_roots": [str(r) for r in roots],
            "scanned_directories": scanned_dirs,
            "count": len(matches),
            "matches": matches,
        }

    def create_folder(self, path: str) -> Dict[str, Any]:
        target = Path(os.path.expandvars(path)).expanduser().resolve()
        existed = target.exists()
        target.mkdir(parents=True, exist_ok=True)
        verified = target.is_dir()
        return {
            "success": verified,
            "verified": verified,
            "path": str(target),
            "already_existed": existed,
        }

    def create_file(self, path: str, content: str = "", overwrite: bool = False) -> Dict[str, Any]:
        target = Path(os.path.expandvars(path)).expanduser().resolve()
        if target.exists() and not overwrite:
            return {
                "success": False,
                "verified": True,
                "error": f"Refused to overwrite existing file '{target}'. Set overwrite=True with confirmation if intended.",
            }
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode("utf-8")
        tmp = target.with_suffix(target.suffix + ".jarvis_tmp")
        tmp.write_bytes(encoded)
        tmp.replace(target)

        # Verify file exists and exact byte length matches
        verified = target.is_file() and target.stat().st_size == len(encoded)
        return {
            "success": verified,
            "verified": verified,
            "path": str(target),
            "size_bytes": target.stat().st_size if target.exists() else 0,
        }

    def rename_path(self, source: str, new_name: str) -> Dict[str, Any]:
        src = Path(os.path.expandvars(source)).expanduser().resolve()
        if not src.exists():
            return {"success": False, "verified": True, "error": f"Source not found: {src}"}
        dst = (
            Path(os.path.expandvars(new_name)).expanduser().resolve()
            if Path(new_name).is_absolute()
            else (src.parent / new_name).resolve()
        )
        if dst.exists():
            return {
                "success": False,
                "verified": True,
                "error": f"Destination already exists (will not overwrite): {dst}",
            }
        src.rename(dst)
        verified = dst.exists() and not src.exists()
        return {
            "success": verified,
            "verified": verified,
            "source": str(src),
            "destination": str(dst),
        }

    def move_path(self, source: str, destination: str) -> Dict[str, Any]:
        src = Path(os.path.expandvars(source)).expanduser().resolve()
        if not src.exists():
            return {"success": False, "verified": True, "error": f"Source not found: {src}"}
        dst = Path(os.path.expandvars(destination)).expanduser().resolve()
        if dst.is_dir():
            dst = (dst / src.name).resolve()
        if dst.exists():
            return {
                "success": False,
                "verified": True,
                "error": f"Destination already exists (will not overwrite): {dst}",
            }
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        verified = dst.exists() and not src.exists()
        return {
            "success": verified,
            "verified": verified,
            "source": str(src),
            "destination": str(dst),
        }

    def copy_path(self, source: str, destination: str) -> Dict[str, Any]:
        src = Path(os.path.expandvars(source)).expanduser().resolve()
        if not src.exists():
            return {"success": False, "verified": True, "error": f"Source not found: {src}"}
        dst = Path(os.path.expandvars(destination)).expanduser().resolve()
        if dst.is_dir():
            dst = (dst / src.name).resolve()
        if dst.exists():
            return {
                "success": False,
                "verified": True,
                "error": f"Destination already exists (will not overwrite): {dst}",
            }
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(str(src), str(dst))
            verified = dst.is_dir() and src.is_dir()
        else:
            src_size = src.stat().st_size
            shutil.copy2(str(src), str(dst))
            verified = dst.is_file() and dst.stat().st_size == src_size
        return {
            "success": verified,
            "verified": verified,
            "source": str(src),
            "destination": str(dst),
        }

    def delete_path(self, path: str, use_recycle_bin: bool = True) -> Dict[str, Any]:
        """Delete a file/folder ONLY after SecurityGate user confirmation (prefers Recycle Bin)."""
        target = Path(os.path.expandvars(path)).expanduser().resolve()
        if not target.exists():
            return {"success": False, "verified": True, "error": f"Path not found: {target}"}

        method = "permanent"
        if use_recycle_bin and importlib.util.find_spec("send2trash") is not None:
            from send2trash import send2trash  # type: ignore

            send2trash(str(target))
            method = "recycle_bin"
        else:
            if target.is_dir():
                shutil.rmtree(str(target))
            else:
                target.unlink()

        verified = not target.exists()
        return {
            "success": verified,
            "verified": verified,
            "deleted_path": str(target),
            "method": method,
        }
