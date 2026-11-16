"""Exclusive local Ollama REST API client with streaming, retries & OOM recovery.

Hard guarantee: connects ONLY to loopback addresses (127.0.0.1 / localhost / ::1).
Never silently redirects or falls back to any cloud inference provider.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, List, Optional

import httpx

from jarvis.config.schema import OllamaConfig, is_loopback_url
from jarvis.logs.logger import get_logger
from jarvis.security.emergency import CancellationToken

log = get_logger("models.ollama")


class OllamaError(RuntimeError):
    """Base error for local Ollama communication failures."""


class OllamaUnavailableError(OllamaError):
    """Ollama daemon is not running or unreachable on loopback."""


class OllamaModelNotFoundError(OllamaError):
    """Requested model is not installed locally in Ollama."""


class OllamaTimeoutError(OllamaError):
    """Local Ollama request exceeded configured timeout."""


@dataclass
class LocalModelInfo:
    name: str
    size_bytes: int = 0
    parameter_size: str = ""
    quantization_level: str = ""
    family: str = ""
    modified_at: str = ""
    digest: str = ""
    is_embedding: bool = False
    vram_fit: str = "unknown"  # coexist_gpu | optimal_6gb | hybrid_offload | heavy_offload
    notes: str = ""

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024 ** 3), 2) if self.size_bytes else 0.0

    @property
    def is_chat_compatible(self) -> bool:
        return not self.is_embedding

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "size_bytes": self.size_bytes,
            "size_gb": self.size_gb,
            "parameter_size": self.parameter_size,
            "quantization_level": self.quantization_level,
            "family": self.family,
            "modified_at": self.modified_at,
            "is_embedding": self.is_embedding,
            "is_chat_compatible": self.is_chat_compatible,
            "vram_fit": self.vram_fit,
            "notes": self.notes,
        }


@dataclass
class RunningModelStatus:
    name: str
    size_bytes: int = 0
    size_vram_bytes: int = 0
    expires_at: str = ""

    @property
    def vram_mb(self) -> int:
        return int(self.size_vram_bytes / (1024 * 1024))


@dataclass
class ChatResponse:
    content: str
    model: str
    done: bool = True
    total_duration_ms: float = 0.0
    prompt_eval_count: int = 0
    eval_count: int = 0
    used_num_ctx: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)


class OllamaClient:
    """Resilient local-only HTTP client for Ollama."""

    def __init__(self, config: OllamaConfig) -> None:
        self._assert_local_only(config.base_url, config.enforce_local_only)
        self.config = config
        self.base_url = config.base_url.rstrip("/")

    @staticmethod
    def _assert_local_only(url: str, enforce: bool = True) -> None:
        if enforce and not is_loopback_url(url):
            raise ValueError(
                f"Security violation: non-loopback Ollama endpoint '{url}' rejected. "
                "JARVIS operates strictly on local Ollama."
            )

    def _timeout(self, read_override: Optional[float] = None) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.config.connect_timeout_sec,
            read=read_override or self.config.read_timeout_sec,
            write=15.0,
            pool=5.0,
        )

    def _client(self, read_override: Optional[float] = None) -> httpx.Client:
        self._assert_local_only(self.base_url, self.config.enforce_local_only)
        # trust_env=False ensures HTTP_PROXY never tunnels local Ollama traffic externally!
        return httpx.Client(
            base_url=self.base_url,
            timeout=self._timeout(read_override),
            trust_env=False,
        )

    def check_health(self) -> Dict[str, Any]:
        """Check if Ollama daemon is reachable and return version info."""
        try:
            with self._client(read_override=5.0) as client:
                resp = client.get("/api/version")
                resp.raise_for_status()
                data = resp.json()
                return {
                    "online": True,
                    "base_url": self.base_url,
                    "version": data.get("version", "unknown"),
                    "error": None,
                }
        except Exception as exc:
            return {
                "online": False,
                "base_url": self.base_url,
                "version": None,
                "error": str(exc),
            }

    def list_models(self, embedding_patterns: Optional[List[str]] = None) -> List[LocalModelInfo]:
        """Fetch all locally installed Ollama models via /api/tags."""
        patterns = [p.lower() for p in (embedding_patterns or ["embed", "minilm", "bge-"])]
        try:
            with self._client(read_override=10.0) as client:
                resp = client.get("/api/tags")
                resp.raise_for_status()
                payload = resp.json()
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(f"Timed out listing Ollama models at {self.base_url}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(
                f"Cannot reach local Ollama at {self.base_url}. Ensure Ollama is running ('ollama serve'). Detail: {exc}"
            ) from exc

        models: List[LocalModelInfo] = []
        for item in payload.get("models", []) or []:
            name = str(item.get("name") or item.get("model") or "").strip()
            if not name:
                continue
            details = item.get("details") or {}
            family = str(details.get("family") or "")
            families = [str(f).lower() for f in (details.get("families") or [])]
            name_l = name.lower()
            is_emb = any(pat in name_l for pat in patterns) or any(
                "bert" in f or "nomic-bert" in f for f in families
            )
            size_bytes = int(item.get("size") or 0)
            models.append(
                LocalModelInfo(
                    name=name,
                    size_bytes=size_bytes,
                    parameter_size=str(details.get("parameter_size") or ""),
                    quantization_level=str(details.get("quantization_level") or ""),
                    family=family,
                    modified_at=str(item.get("modified_at") or ""),
                    digest=str(item.get("digest") or ""),
                    is_embedding=is_emb,
                )
            )
        return models

    def list_running(self) -> List[RunningModelStatus]:
        """Inspect models currently resident in RAM/VRAM via /api/ps."""
        try:
            with self._client(read_override=5.0) as client:
                resp = client.get("/api/ps")
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        running: List[RunningModelStatus] = []
        for item in data.get("models", []) or []:
            running.append(
                RunningModelStatus(
                    name=str(item.get("name") or item.get("model") or ""),
                    size_bytes=int(item.get("size") or 0),
                    size_vram_bytes=int(item.get("size_vram") or 0),
                    expires_at=str(item.get("expires_at") or ""),
                )
            )
        return running

    def show_model(self, model_name: str) -> Dict[str, Any]:
        """Fetch detailed model metadata from /api/show."""
        try:
            with self._client(read_override=10.0) as client:
                resp = client.post("/api/show", json={"name": model_name})
                if resp.status_code == 404:
                    raise OllamaModelNotFoundError(
                        f"Model '{model_name}' is not installed locally. Run: ollama pull {model_name}"
                    )
                resp.raise_for_status()
                return resp.json()
        except OllamaModelNotFoundError:
            raise
        except httpx.TimeoutException as exc:
            raise OllamaTimeoutError(f"Timed out inspecting model '{model_name}': {exc}") from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailableError(f"Failed inspecting model '{model_name}': {exc}") from exc

    def unload_model(self, model_name: str) -> bool:
        """Immediately evict a model from RTX 3050 VRAM (keep_alive=0)."""
        if not model_name:
            return False
        try:
            with self._client(read_override=10.0) as client:
                resp = client.post(
                    "/api/chat",
                    json={"model": model_name, "messages": [], "keep_alive": 0},
                )
                return resp.status_code < 400
        except Exception as exc:
            log.debug("Non-fatal unload error for %s: %s", model_name, exc)
            return False

    @staticmethod
    def _looks_like_oom(message: str) -> bool:
        low = message.lower()
        return any(
            k in low
            for k in (
                "out of memory",
                "cuda error",
                "cudamalloc",
                "unable to allocate",
                "requires more system memory",
                "vram",
            )
        )

    def chat_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        num_ctx: Optional[int] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        token: Optional[CancellationToken] = None,
    ) -> Generator[str, None, ChatResponse]:
        """Stream response chunks from local Ollama /api/chat with retry & OOM fallback."""
        if not model:
            raise OllamaModelNotFoundError("No local Ollama model selected.")

        target_ctx = num_ctx or self.config.num_ctx
        attempts = 0
        max_attempts = self.config.max_retries + 1
        last_error: Optional[Exception] = None

        while attempts < max_attempts:
            attempts += 1
            if token:
                token.raise_if_cancelled()

            payload: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": True,
                "keep_alive": self.config.keep_alive,
                "options": {
                    "temperature": self.config.temperature if temperature is None else temperature,
                    "top_p": self.config.top_p,
                    "num_ctx": target_ctx,
                    "num_predict": max_tokens or self.config.max_output_tokens,
                },
            }
            if json_mode:
                payload["format"] = "json"

            chunks: List[str] = []
            final_meta: Dict[str, Any] = {}
            started = time.perf_counter()

            try:
                with self._client() as client:
                    with client.stream("POST", "/api/chat", json=payload) as resp:
                        if resp.status_code == 404:
                            raise OllamaModelNotFoundError(
                                f"Model '{model}' was not found in local Ollama."
                            )
                        if resp.status_code >= 400:
                            err_body = resp.read().decode("utf-8", errors="replace")
                            if self._looks_like_oom(err_body) and target_ctx > self.config.low_vram_num_ctx:
                                log.warning(
                                    "VRAM pressure detected on %s (ctx=%d); retrying with ctx=%d",
                                    model,
                                    target_ctx,
                                    self.config.low_vram_num_ctx,
                                )
                                target_ctx = self.config.low_vram_num_ctx
                                continue
                            raise OllamaError(f"Ollama HTTP {resp.status_code}: {err_body[:300]}")

                        for line in resp.iter_lines():
                            if token and token.is_cancelled:
                                resp.close()
                                token.raise_if_cancelled()
                            if not line:
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if "error" in event and event["error"]:
                                err_msg = str(event["error"])
                                if (
                                    not chunks
                                    and self._looks_like_oom(err_msg)
                                    and target_ctx > self.config.low_vram_num_ctx
                                ):
                                    target_ctx = self.config.low_vram_num_ctx
                                    raise _RetryWithSmallerContext(err_msg)
                                raise OllamaError(f"Ollama stream error: {err_msg}")

                            msg_obj = event.get("message") or {}
                            piece = msg_obj.get("content") or ""
                            if piece:
                                chunks.append(piece)
                                yield piece
                            if event.get("done"):
                                final_meta = event
                                break

                elapsed_ms = (time.perf_counter() - started) * 1000.0
                return ChatResponse(
                    content="".join(chunks),
                    model=model,
                    done=True,
                    total_duration_ms=round(elapsed_ms, 1),
                    prompt_eval_count=int(final_meta.get("prompt_eval_count") or 0),
                    eval_count=int(final_meta.get("eval_count") or 0),
                    used_num_ctx=target_ctx,
                    raw=final_meta,
                )

            except _RetryWithSmallerContext as exc:
                last_error = exc
                continue
            except OllamaModelNotFoundError:
                raise
            except httpx.TimeoutException as exc:
                last_error = OllamaTimeoutError(
                    f"Ollama timed out after {self.config.read_timeout_sec}s on model '{model}'."
                )
                if chunks or attempts >= max_attempts:
                    raise last_error from exc
                log.warning("Transient timeout on attempt %d/%d; retrying...", attempts, max_attempts)
                time.sleep(min(0.5 * attempts, 2.0))
            except httpx.HTTPError as exc:
                last_error = OllamaUnavailableError(
                    f"Lost connection to local Ollama ({self.base_url}): {exc}"
                )
                if chunks or attempts >= max_attempts:
                    raise last_error from exc
                log.warning("Connection error on attempt %d/%d; retrying...", attempts, max_attempts)
                time.sleep(min(0.5 * attempts, 2.0))

        raise OllamaError(f"Ollama chat failed after {max_attempts} attempts: {last_error}")

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        num_ctx: Optional[int] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        token: Optional[CancellationToken] = None,
    ) -> ChatResponse:
        """Execute a complete chat turn while optionally forwarding streamed tokens."""
        gen = self.chat_stream(
            model=model,
            messages=messages,
            temperature=temperature,
            num_ctx=num_ctx,
            max_tokens=max_tokens,
            json_mode=json_mode,
            token=token,
        )
        pieces: List[str] = []
        try:
            while True:
                piece = next(gen)
                pieces.append(piece)
                if on_token:
                    on_token(piece)
        except StopIteration as stop:
            if isinstance(stop.value, ChatResponse):
                return stop.value
            return ChatResponse(content="".join(pieces), model=model, done=True)


class _RetryWithSmallerContext(Exception):
    pass
