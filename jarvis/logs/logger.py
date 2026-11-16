"""Redacting rotating logger and a SHA-256 hash-chained, append-only audit log.

Two independent facilities live here:

* :func:`setup_logging` / :func:`get_logger` — human-readable operational logs
  written to a ``RotatingFileHandler`` plus the console, with a
  :class:`RedactionFilter` that strips API keys, bearer tokens, and passwords
  before anything is written to disk or printed.
* :class:`AuditLogger` — an append-only JSONL security ledger in which every
  entry carries the SHA-256 hash of the previous entry, so any edit, deletion,
  or reordering of a past record is detectable with :meth:`AuditLogger.verify_chain`.

Everything is local. Nothing is uploaded anywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

REDACTION_PLACEHOLDER = "***REDACTED***"

#: Name of the root logger used by every JARVIS module.
PACKAGE_LOGGER_NAME = "jarvis"

#: SHA-256 hash used as ``prev_hash`` for the very first audit entry.
GENESIS_HASH = "0" * 64

_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

#: Keys whose values must never be written to a log or audit file.
SENSITIVE_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|apikey|access[_-]?key|secret|client[_-]?secret|token|"
    r"auth[_-]?token|password|passwd|pwd|authorization|bearer|private[_-]?key|"
    r"credential|session[_-]?id|cookie)"
)

# key = value / "key": "value" pairs embedded in free-form log text
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?key|secret|client[_-]?secret|token|"
    r"auth[_-]?token|password|passwd|pwd|authorization|private[_-]?key|credential)"
    r"\b(\s*[:=]\s*)"
    r"((?:bearer|basic|token)\s+)?"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)

#: Well-known credential shapes that should be masked even without a key name.
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
)


def redact_text(text: str) -> str:
    """Return ``text`` with API keys, tokens, and passwords replaced."""
    if not text:
        return text
    cleaned = _SECRET_ASSIGNMENT_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{REDACTION_PLACEHOLDER}", text
    )
    for pattern in SECRET_VALUE_PATTERNS:
        cleaned = pattern.sub(REDACTION_PLACEHOLDER, cleaned)
    return cleaned


def redact_value(value: Any) -> Any:
    """Recursively redact a JSON-like structure (dict keys and string values)."""
    if isinstance(value, dict):
        cleaned: Dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and SENSITIVE_KEY_RE.search(key):
                cleaned[key] = REDACTION_PLACEHOLDER
            else:
                cleaned[key] = redact_value(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


class RedactionFilter(logging.Filter):
    """Logging filter that strips secrets before a record reaches any handler."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 - stdlib API
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive: broken format args
            return True
        redacted = redact_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_CONFIGURED_TARGETS: set[str] = set()


def setup_logging(
    log_dir: Optional[Union[str, Path]] = None,
    *,
    level: int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
    console: bool = True,
    filename: str = "jarvis.log",
) -> logging.Logger:
    """Configure the ``jarvis`` logger tree with rotating file + console output.

    Safe to call more than once (for example by every test that builds a
    controller): a given ``log_dir`` is only configured a single time, so no
    duplicate records are emitted.

    Args:
        log_dir: Directory for ``jarvis.log``. ``None`` logs to the console only.
        level: Minimum level for the handlers.
        max_bytes: Rotate after this many bytes (default 5 MB).
        backup_count: Number of rotated files to keep.
        console: Also write to stderr.
        filename: Log file name inside ``log_dir``.
    """
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    target = str(Path(log_dir).expanduser().resolve()) if log_dir is not None else "<console>"
    if target in _CONFIGURED_TARGETS:
        return logger

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)
    redaction = RedactionFilter()
    added = False

    if log_dir is not None:
        try:
            directory = Path(log_dir).expanduser()
            directory.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                directory / filename,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
                delay=False,
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(redaction)
            logger.addHandler(file_handler)
            added = True
        except OSError:  # pragma: no cover - unwritable directory fallback
            logger.warning("Could not open log file in %s; logging to console only.", log_dir)

    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(redaction)
        logger.addHandler(stream_handler)
        added = True

    if added:
        _CONFIGURED_TARGETS.add(target)
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger (``get_logger("brain.executor")``)."""
    cleaned = (name or "jarvis").strip()
    if cleaned == PACKAGE_LOGGER_NAME or cleaned.startswith(PACKAGE_LOGGER_NAME + "."):
        return logging.getLogger(cleaned)
    return logging.getLogger(f"{PACKAGE_LOGGER_NAME}.{cleaned}")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _hash_entry(entry_without_hash: Dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(entry_without_hash).encode("utf-8")).hexdigest()


class AuditLogger:
    """Append-only JSONL audit ledger with a SHA-256 hash chain.

    Each line is a self-contained JSON object::

        {"seq": 1, "timestamp": "...", "event_type": "PERMISSION_CHECK",
         "action": "open_vscode", "decision": "ALLOWED", "risk_level": "MODERATE",
         "verified": false, "details": {...}, "prev_hash": "00...0",
         "entry_hash": "9f..."}

    ``entry_hash`` is SHA-256 over the canonical JSON of the entry (with
    ``entry_hash`` itself excluded) and ``prev_hash`` is the ``entry_hash`` of
    the preceding line. Editing, deleting, or reordering any historical line
    therefore breaks the chain and :meth:`verify_chain` reports the exact line.
    """

    def __init__(self, path: Union[str, Path], *, fsync: bool = False) -> None:
        self.path = Path(path).expanduser()
        self.fsync = fsync
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._seq, self._head_hash = self._scan_head()

    # -- public API -----------------------------------------------------

    def record(
        self,
        event_type: str,
        action: str,
        decision: str,
        risk_level: Any,
        details: Optional[Dict[str, Any]] = None,
        *,
        verified: bool = False,
    ) -> Dict[str, Any]:
        """Append one redacted, hash-chained entry and return it."""
        risk = getattr(risk_level, "value", risk_level)
        with self._lock:
            seq = self._seq + 1
            entry: Dict[str, Any] = {
                "seq": seq,
                "timestamp": _utc_now_iso(),
                "event_type": str(event_type),
                "action": str(action),
                "decision": str(decision),
                "risk_level": str(risk),
                "verified": bool(verified),
                "details": redact_value(details if details is not None else {}),
                "prev_hash": self._head_hash,
            }
            entry_hash = _hash_entry(entry)
            entry["entry_hash"] = entry_hash

            line = _canonical_json(entry)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                if self.fsync:
                    import os

                    os.fsync(handle.fileno())

            self._seq = seq
            self._head_hash = entry_hash
            return entry

    def verify_chain(self) -> Tuple[bool, int, str]:
        """Re-hash every entry and validate the chain.

        Returns:
            ``(is_valid, entry_count, human_readable_message)``.
        """
        with self._lock:
            if not self.path.exists():
                return True, 0, "Audit log does not exist yet — empty chain is valid."

            try:
                raw = self.path.read_text(encoding="utf-8")
            except OSError as exc:  # pragma: no cover - unreadable file
                return False, 0, f"Audit log could not be read: {exc}"

            lines = [line for line in raw.splitlines() if line.strip()]
            if not lines:
                return True, 0, "Audit log is empty — empty chain is valid."

            expected_prev = GENESIS_HASH
            for index, line in enumerate(lines, start=1):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    return False, index - 1, f"Audit chain broken at line {index}: invalid JSON ({exc.msg})."
                if not isinstance(entry, dict):
                    return False, index - 1, f"Audit chain broken at line {index}: entry is not an object."

                stored_hash = entry.get("entry_hash")
                payload = {k: v for k, v in entry.items() if k != "entry_hash"}
                computed = _hash_entry(payload)
                if stored_hash != computed:
                    return (
                        False,
                        index - 1,
                        f"Audit chain broken at line {index}: entry hash mismatch — "
                        "the record was modified after it was written.",
                    )
                if entry.get("prev_hash") != expected_prev:
                    return (
                        False,
                        index - 1,
                        f"Audit chain broken at line {index}: previous-hash mismatch — "
                        "an entry was inserted, deleted, or reordered.",
                    )
                expected_prev = str(stored_hash)

            head = expected_prev[:12]
            return True, len(lines), f"Audit chain verified: {len(lines)} entries, head hash {head}…"

    def read_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return up to ``limit`` most recent entries, newest first."""
        if not self.path.exists():
            return []
        try:
            lines = [line for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError:  # pragma: no cover - unreadable file
            return []
        recent: List[Dict[str, Any]] = []
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                recent.append(entry)
            if len(recent) >= max(1, int(limit)):
                break
        return recent

    # -- internals ------------------------------------------------------

    def _scan_head(self) -> Tuple[int, str]:
        """Recover the current sequence number and chain head on startup."""
        if not self.path.exists():
            return 0, GENESIS_HASH
        last_seq = 0
        last_hash = GENESIS_HASH
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seq = entry.get("seq")
                if isinstance(seq, int) and seq > last_seq:
                    last_seq = seq
                entry_hash = entry.get("entry_hash")
                if isinstance(entry_hash, str) and len(entry_hash) == 64:
                    last_hash = entry_hash
        except OSError:  # pragma: no cover - unreadable file
            return 0, GENESIS_HASH
        return last_seq, last_hash


# Keep the library quiet when it is imported as a dependency without configuration.
logging.getLogger(PACKAGE_LOGGER_NAME).addHandler(logging.NullHandler())
