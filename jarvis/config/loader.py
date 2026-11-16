"""Configuration loader and atomic saver.

Never overwrites corrupted user configs without creating a timestamped backup.
Automatically seeds safe default permitted directories under the current user's profile.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from jarvis.config.schema import JarvisConfig

CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = CONFIG_DIR / "default_config.yaml"
MODELS_CATALOG_PATH = CONFIG_DIR / "models_catalog.yaml"


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def default_permitted_directories(data_dir: Path) -> list[str]:
    """Build safe default permitted directories inside the user's home folder."""
    home = Path.home()
    candidates = [
        home / "Desktop",
        home / "Documents",
        home / "Downloads",
        home / "Projects",
        home / "Pictures",
        home / "Videos",
        home / "Music",
        data_dir,
        data_dir / "workspace",
        data_dir / "screenshots",
    ]
    # Also support Windows OneDrive redirected Desktop/Documents when present
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        od = Path(onedrive)
        for sub in ("Desktop", "Documents", "Pictures"):
            if (od / sub).exists():
                candidates.append(od / sub)

    result: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        # Include standard user subdirs if they exist OR are inside data_dir
        if path.exists() or str(path).startswith(str(data_dir)):
            norm = str(path.resolve())
            if norm not in seen:
                seen.add(norm)
                result.append(norm)

    if not result:
        result.append(str(data_dir.resolve()))
    return result


def ensure_runtime_directories(config: JarvisConfig) -> Path:
    """Create Jarvis's own internal runtime folders without touching user files."""
    data_dir = config.resolved_data_dir()
    for sub in ("logs", "memory", "voices", "workspace", "screenshots", "backups"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    if not config.tts.voices_dir:
        config.tts.voices_dir = str((data_dir / "voices").resolve())
    return data_dir


def load_config(
    user_config_path: Optional[Path | str] = None,
    data_dir_override: Optional[Path | str] = None,
) -> JarvisConfig:
    """Load merged default + user YAML configuration and validate strictly."""
    base_data: Dict[str, Any] = {}
    if DEFAULT_CONFIG_PATH.exists():
        raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            base_data = raw

    if data_dir_override is not None:
        base_data["data_dir"] = str(Path(data_dir_override).expanduser().resolve())
    elif os.environ.get("JARVIS_DATA_DIR"):
        base_data["data_dir"] = str(Path(os.environ["JARVIS_DATA_DIR"]).expanduser().resolve())

    probe_cfg = JarvisConfig.model_validate(base_data)
    data_dir = probe_cfg.resolved_data_dir()

    cfg_path = (
        Path(user_config_path).expanduser().resolve()
        if user_config_path
        else data_dir / "config.yaml"
    )

    merged = base_data
    if cfg_path.exists():
        try:
            user_raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            if isinstance(user_raw, dict):
                merged = _deep_merge(base_data, user_raw)
        except yaml.YAMLError:
            backup = cfg_path.with_suffix(f".corrupt.{int(time.time())}.yaml")
            shutil.copy2(cfg_path, backup)

    if os.environ.get("JARVIS_OLLAMA_URL"):
        merged.setdefault("ollama", {})["base_url"] = os.environ["JARVIS_OLLAMA_URL"]
    if os.environ.get("JARVIS_MODEL"):
        merged.setdefault("ollama", {})["preferred_model"] = os.environ["JARVIS_MODEL"]

    config = JarvisConfig.model_validate(merged)
    data_dir = ensure_runtime_directories(config)

    if not config.security.permitted_directories:
        config.security.permitted_directories = default_permitted_directories(data_dir)
    else:
        # Ensure Jarvis's own workspace and screenshots dirs are always accessible
        existing = {str(Path(p).expanduser().resolve()) for p in config.security.permitted_directories}
        for required in (data_dir, data_dir / "workspace", data_dir / "screenshots"):
            rp = str(required.resolve())
            if rp not in existing:
                config.security.permitted_directories.append(rp)

    return config


def save_config(config: JarvisConfig, target_path: Optional[Path | str] = None) -> Path:
    """Atomically save Jarvis's own config.yaml (writes temp file then replaces)."""
    data_dir = ensure_runtime_directories(config)
    path = (
        Path(target_path).expanduser().resolve()
        if target_path
        else data_dir / "config.yaml"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".yaml.tmp")
    payload = config.model_dump(mode="json")
    tmp_path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return path


def load_models_catalog() -> Dict[str, Any]:
    """Load separated model catalog metadata."""
    if not MODELS_CATALOG_PATH.exists():
        return {"embedding_only_patterns": [], "recommended_profiles": {}}
    data = yaml.safe_load(MODELS_CATALOG_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {"embedding_only_patterns": [], "recommended_profiles": {}}
