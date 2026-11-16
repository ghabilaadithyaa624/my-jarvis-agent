"""Local logging & tamper-evident audit trail for JARVIS PERSONAL AI.

Everything here writes to the local data directory (`~/.jarvis/logs/` by default).
No log record or audit entry ever leaves the machine.
"""

from __future__ import annotations

from jarvis.logs.logger import (
    REDACTION_PLACEHOLDER,
    AuditLogger,
    RedactionFilter,
    get_logger,
    redact_text,
    setup_logging,
)

__all__ = [
    "REDACTION_PLACEHOLDER",
    "AuditLogger",
    "RedactionFilter",
    "get_logger",
    "redact_text",
    "setup_logging",
]
