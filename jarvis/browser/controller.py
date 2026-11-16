"""Browser & Lazy-Loaded Playwright Automation Engine (Section 5D).

Uses 0 MB idle RAM until Playwright interaction is explicitly invoked.
Supports opening websites, explicit web search, multi-tab management, element
clicking/filling, page reading, and repeatable multi-step browser workflows.
"""

from __future__ import annotations

import importlib.util
import threading
import urllib.parse
import webbrowser
from typing import Any, Dict, List, Optional

from jarvis.config.schema import BrowserConfig
from jarvis.logs.logger import get_logger
from jarvis.security.emergency import CancellationToken

log = get_logger("browser.controller")


SEARCH_ENGINE_TEMPLATES: Dict[str, str] = {
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "github": "https://github.com/search?q={q}",
}


def normalize_http_url(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        raise ValueError("URL cannot be empty.")
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    if ":" in cleaned.split("/")[0] and not cleaned.split("/")[0].split(":")[1].isdigit():
        raise ValueError(f"Disallowed URL scheme in '{cleaned}'. Only http:// and https:// are permitted.")
    return "https://" + cleaned


class BrowserController:
    """Manages native browser launch and full Playwright tab/DOM automation."""

    def __init__(self, cfg: BrowserConfig) -> None:
        self.cfg = cfg
        self._lock = threading.RLock()
        self._pw = None
        self._browser = None
        self._context = None
        self._active_tab_idx: int = 0

    @staticmethod
    def is_playwright_available() -> bool:
        return importlib.util.find_spec("playwright") is not None

    # ---------------- Lightweight System Browser ----------------

    def open_website(self, url: str) -> Dict[str, Any]:
        """Open an HTTP(S) URL in the user's default Windows 11 browser."""
        final_url = normalize_http_url(url)
        opened = bool(webbrowser.open(final_url, new=2))
        return {
            "success": opened,
            "verified": opened,
            "url": final_url,
            "error": None if opened else "OS reported no default graphical browser available.",
        }

    def build_search_url(self, query: str, engine: Optional[str] = None) -> str:
        q = (query or "").strip()
        if not q:
            raise ValueError("Search query cannot be empty.")
        eng = (engine or self.cfg.default_search_engine).lower()
        template = SEARCH_ENGINE_TEMPLATES.get(eng, SEARCH_ENGINE_TEMPLATES["duckduckgo"])
        return template.format(q=urllib.parse.quote_plus(q))

    def web_search(self, query: str, engine: Optional[str] = None) -> Dict[str, Any]:
        """Open an explicit user-requested web search in the default browser."""
        url = self.build_search_url(query, engine)
        opened = bool(webbrowser.open(url, new=2))
        return {
            "success": opened,
            "verified": opened,
            "query": query,
            "engine": (engine or self.cfg.default_search_engine).lower(),
            "url": url,
            "error": None if opened else "No graphical browser available to open search URL.",
        }

    # ---------------- Managed Playwright Session ----------------

    def _ensure_page(self, new_tab: bool = False):
        if not self.is_playwright_available():
            raise RuntimeError(
                "Playwright is not installed. Run: pip install playwright && python -m playwright install chromium"
            )
        from playwright.sync_api import sync_playwright  # type: ignore

        with self._lock:
            if self._pw is None:
                self._pw = sync_playwright().start()
            if self._browser is None or not self._browser.is_connected():
                launcher = getattr(self._pw, self.cfg.playwright_browser)
                self._browser = launcher.launch(headless=self.cfg.playwright_headless)
                self._context = self._browser.new_context(viewport={"width": 1280, "height": 800})
                self._context.set_default_timeout(self.cfg.navigation_timeout_ms)

            pages = self._context.pages
            if new_tab or not pages:
                page = self._context.new_page()
                self._active_tab_idx = len(self._context.pages) - 1
                return page

            self._active_tab_idx = max(0, min(self._active_tab_idx, len(pages) - 1))
            return pages[self._active_tab_idx]

    def navigate(self, url: str, new_tab: bool = False) -> Dict[str, Any]:
        final_url = normalize_http_url(url)
        try:
            page = self._ensure_page(new_tab=new_tab)
            resp = page.goto(final_url, wait_until="domcontentloaded")
            title = page.title()
            status = resp.status if resp else None
            return {
                "success": True,
                "verified": True,
                "url": page.url,
                "title": title,
                "http_status": status,
                "tab_index": self._active_tab_idx,
            }
        except Exception as exc:
            return {"success": False, "verified": False, "url": final_url, "error": str(exc)}

    def list_tabs(self) -> Dict[str, Any]:
        with self._lock:
            if self._context is None:
                return {"success": True, "verified": True, "tabs": [], "active_index": -1}
            tabs = [
                {
                    "index": idx,
                    "title": p.title(),
                    "url": p.url,
                    "active": idx == self._active_tab_idx,
                }
                for idx, p in enumerate(self._context.pages)
            ]
            return {"success": True, "verified": True, "tabs": tabs, "active_index": self._active_tab_idx}

    def switch_tab(self, tab_index: int) -> Dict[str, Any]:
        with self._lock:
            if self._context is None or not self._context.pages:
                return {"success": False, "verified": True, "error": "No active Playwright browser tabs."}
            if tab_index < 0 or tab_index >= len(self._context.pages):
                return {
                    "success": False,
                    "verified": True,
                    "error": f"Tab index {tab_index} out of range (0..{len(self._context.pages) - 1}).",
                }
            self._active_tab_idx = tab_index
            page = self._context.pages[tab_index]
            page.bring_to_front()
            return {"success": True, "verified": True, "tab_index": tab_index, "title": page.title(), "url": page.url}

    def close_tab(self, tab_index: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            if self._context is None or not self._context.pages:
                return {"success": False, "verified": True, "error": "No open Playwright tabs to close."}
            idx = self._active_tab_idx if tab_index is None else int(tab_index)
            if idx < 0 or idx >= len(self._context.pages):
                return {"success": False, "verified": True, "error": f"Invalid tab index {idx}."}
            before = len(self._context.pages)
            self._context.pages[idx].close()
            after = len(self._context.pages)
            self._active_tab_idx = max(0, min(self._active_tab_idx, after - 1))
            return {"success": after == before - 1, "verified": after == before - 1, "remaining_tabs": after}

    def click(self, selector: str) -> Dict[str, Any]:
        try:
            page = self._ensure_page(new_tab=False)
            page.click(selector)
            return {"success": True, "verified": True, "selector": selector, "current_url": page.url}
        except Exception as exc:
            return {"success": False, "verified": False, "selector": selector, "error": str(exc)}

    def fill(self, selector: str, value: str) -> Dict[str, Any]:
        try:
            page = self._ensure_page(new_tab=False)
            page.fill(selector, value)
            actual = page.input_value(selector)
            verified = actual == value
            return {"success": verified, "verified": verified, "selector": selector}
        except Exception as exc:
            return {"success": False, "verified": False, "selector": selector, "error": str(exc)}

    def read_page(self, max_chars: int = 5000) -> Dict[str, Any]:
        try:
            page = self._ensure_page(new_tab=False)
            text = page.inner_text("body") or ""
            return {
                "success": True,
                "verified": True,
                "title": page.title(),
                "url": page.url,
                "content": text[:max_chars],
                "truncated": len(text) > max_chars,
            }
        except Exception as exc:
            return {"success": False, "verified": False, "error": str(exc)}

    def run_workflow(
        self,
        steps: List[Dict[str, Any]],
        token: Optional[CancellationToken] = None,
    ) -> Dict[str, Any]:
        """Execute a sequence of browser actions (goto, click, fill, wait, read) with verification."""
        if not steps:
            return {"success": False, "verified": True, "error": "Workflow steps list is empty."}
        completed: List[Dict[str, Any]] = []
        for idx, step in enumerate(steps[:15]):
            if token and token.is_cancelled:
                token.raise_if_cancelled()
            action = str(step.get("action") or "").lower()
            if action == "goto":
                res = self.navigate(str(step.get("url", "")), new_tab=bool(step.get("new_tab", False)))
            elif action == "click":
                res = self.click(str(step.get("selector", "")))
            elif action == "fill":
                res = self.fill(str(step.get("selector", "")), str(step.get("value", "")))
            elif action == "read":
                res = self.read_page(max_chars=int(step.get("max_chars", 3000)))
            elif action == "switch_tab":
                res = self.switch_tab(int(step.get("index", 0)))
            else:
                res = {"success": False, "verified": True, "error": f"Unknown browser action '{action}'."}
            completed.append({"step": idx + 1, "action": action, **res})
            if not res.get("success"):
                return {
                    "success": False,
                    "verified": bool(res.get("verified")),
                    "completed_steps": completed,
                    "error": f"Workflow stopped at step {idx + 1} ({action}): {res.get('error')}",
                }
        return {"success": True, "verified": True, "completed_steps": completed}

    def close(self) -> None:
        """Cleanly close Playwright browser and reclaim all RAM."""
        with self._lock:
            try:
                if self._context:
                    self._context.close()
                if self._browser:
                    self._browser.close()
                if self._pw:
                    self._pw.stop()
            except Exception:
                pass
            finally:
                self._context = None
                self._browser = None
                self._pw = None
