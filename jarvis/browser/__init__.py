"""Browser and Playwright automation package."""

from jarvis.browser.controller import (
    SEARCH_ENGINE_TEMPLATES,
    BrowserController,
    normalize_http_url,
)

__all__ = ["BrowserController", "SEARCH_ENGINE_TEMPLATES", "normalize_http_url"]
