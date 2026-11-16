"""Speech output sanitizer — ensures JARVIS NEVER reads code blocks or raw dumps aloud.

Strips fenced code, JSON payloads, stack traces, markdown syntax, long file paths,
URLs, hashes, and tables while preserving crisp, natural English and Tamil prose.
"""

from __future__ import annotations

import re
from typing import List


_FENCED_CODE_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_UNCLOSED_CODE_RE = re.compile(r"```[\s\S]*$", re.MULTILINE)
_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)
_JSON_BLOCK_RE = re.compile(r"(?m)^\s*[\{\[][\s\S]{30,}?[\}\]]\s*$")
_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):[\s\S]*?(?=\n\n|\Z)")
_MD_TABLE_RE = re.compile(r"(?m)^\|.*\|\s*$")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\)]+)\)")
_RAW_URL_RE = re.compile(r"https?://\S+")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\\s:*?\"<>|\r\n]+\\){2,}([^\\\s:*?\"<>|\r\n]+)")
_UNIX_PATH_RE = re.compile(r"(?<!\w)/(?:[\w.\-]+/){2,}([\w.\-]+)")
_HEX_HASH_RE = re.compile(r"\b[a-fA-F0-9]{16,}\b")
_MD_HEADER_RE = re.compile(r"(?m)^\s*#{1,6}\s*")
_MD_BULLET_RE = re.compile(r"(?m)^\s*(?:[-*•+]|\d+\.)\s+")
_BOLD_ITALIC_RE = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।])\s+")


CODE_OMISSION_NOTES = {
    "en": "I have placed the code in the conversation panel.",
    "ta": "குறியீட்டை உரையாடல் பலகையில் கொடுத்துள்ளேன்.",
}

TRUNCATION_NOTES = {
    "en": "The full details are shown on screen.",
    "ta": "முழு விவரங்கள் திரையில் உள்ளன.",
}


class SpeechSanitizer:
    """Transforms assistant responses into concise, natural spoken text."""

    def __init__(
        self,
        max_chars: int = 280,
        announce_code_omission: bool = True,
    ) -> None:
        self.max_chars = max_chars
        self.announce_code_omission = announce_code_omission

    def sanitize(self, text: str, language: str = "en") -> str:
        if not text or not text.strip():
            return ""

        out = text
        # 1. Remove hidden reasoning blocks
        out = _THINK_BLOCK_RE.sub("", out)

        # 2. Remove all fenced code blocks (NEVER speak code!)
        had_code = bool(_FENCED_CODE_RE.search(out) or _UNCLOSED_CODE_RE.search(out))
        out = _FENCED_CODE_RE.sub(" ", out)
        out = _UNCLOSED_CODE_RE.sub(" ", out)

        # 3. Strip stack traces, JSON blobs, markdown tables & long hex hashes
        out = _TRACEBACK_RE.sub(" An error traceback is shown on screen. ", out)
        out = _JSON_BLOCK_RE.sub(" ", out)
        out = _MD_TABLE_RE.sub(" ", out)
        out = _HEX_HASH_RE.sub("", out)

        # 4. Replace markdown links with label; strip raw URLs
        out = _MD_LINK_RE.sub(r"\1", out)
        out = _RAW_URL_RE.sub("", out)

        # 5. Shorten deep Windows/Unix file paths to just the filename
        out = _WIN_PATH_RE.sub(r"\1", out)
        out = _UNIX_PATH_RE.sub(r"\1", out)

        # 6. Handle inline code: drop if it looks like syntax, keep short identifiers/filenames
        def _replace_inline(match: re.Match) -> str:
            token = match.group(1).strip()
            if len(token) > 40 or any(ch in token for ch in "{}[]();=<>|$\\"):
                return ""
            return token

        out = _INLINE_CODE_RE.sub(_replace_inline, out)

        # 7. Strip markdown formatting symbols
        out = _MD_HEADER_RE.sub("", out)
        out = _MD_BULLET_RE.sub("", out)
        out = _BOLD_ITALIC_RE.sub(r"\2", out)
        out = out.replace("✓", "").replace("✗", "").replace("✅", "").replace("❌", "").replace("⚠️", "")

        # 8. Normalize newlines into sentence pauses & collapse whitespace
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        normalized_lines: List[str] = []
        for ln in lines:
            # Skip leftover raw log/CLI lines
            if ln.startswith(("$ ", "> ", "PS ", ">>> ", "File \"")):
                continue
            if ln[-1] not in ".!?:;,":
                ln = ln + "."
            normalized_lines.append(ln)
        out = " ".join(normalized_lines)
        out = re.sub(r"\s+", " ", out).strip()

        # 9. Add natural code-omission note if code was removed
        if had_code and self.announce_code_omission:
            note = CODE_OMISSION_NOTES.get(language, CODE_OMISSION_NOTES["en"])
            if out:
                out = f"{out} {note}"
            else:
                out = note

        # 10. Bound spoken length cleanly at sentence boundary (preserving closing conversational offer)
        if len(out) > self.max_chars:
            sentences = self.split_sentences(out)
            followup_sent = ""
            if sentences and any(
                phrase in sentences[-1].lower()
                for phrase in ("another task", "no problem", "வேறு ஏதேனும்", "வேலை செய்ய வேண்டுமா")
            ):
                followup_sent = sentences[-1]
                content_sentences = sentences[:-1]
            else:
                content_sentences = sentences

            tail = TRUNCATION_NOTES.get(language, TRUNCATION_NOTES["en"])
            budget = self.max_chars - (len(followup_sent) + len(tail) + 4 if followup_sent else len(tail) + 2)

            kept: List[str] = []
            length = 0
            for sent in content_sentences:
                if length + len(sent) + 1 > budget and kept:
                    break
                kept.append(sent)
                length += len(sent) + 1

            truncated_body = (" ".join(kept).rstrip() + " " + tail).strip()
            out = f"{truncated_body} {followup_sent}".strip() if followup_sent else truncated_body

        # 11. Word count hard-cap: keep first 50 words maximum
        words = out.split()
        if len(words) > 50:
            out = " ".join(words[:50])

        return out

    @staticmethod
    def split_sentences(text: str) -> List[str]:
        parts = _SENTENCE_SPLIT_RE.split(text.strip())
        return [p.strip() for p in parts if p and p.strip()]
