"""
core/llm_client.py

Databricks LLM client — works on free-tier community and enterprise workspaces.

Key design decisions (v3)
--------------------------
1. PRIMARY path: ws.api_client.do() — uses SDK's own OAuth session, no PAT needed.
   This works on the free-tier community workspace where ws.config.token is empty.

2. FALLBACK path: ws.serving_endpoints.query() with dict messages — works on
   SDK >= 0.28 where the serialisation bug is fixed.

3. ENDPOINT AUTO-DISCOVERY: if the configured model name returns 404 (not found
   on this workspace), the client automatically tries each model in
   llm.fallback_models until one succeeds. This lets the same codebase work
   on free-tier (Llama/GPT endpoints) and enterprise (Claude endpoints).

4. ERROR CLASSIFICATION: 401 = auth problem (raise immediately, no retry),
   404 = wrong endpoint name (try next fallback), 5xx = transient (retry).

5. MOCK FALLBACK: if no Databricks SDK is importable at all, returns a local
   stub so the rest of the platform can be developed without a cluster.

Usage
-----
    from core.llm_client import LLMClient, LLMResponse
    from core.config_loader import ConfigLoader

    cfg    = ConfigLoader()
    client = LLMClient(cfg)
    resp   = client.complete(prompt="...", system="...")
    print(resp.text)
    print(resp.token_usage)         # {"prompt": N, "completion": M, "total": T}
    print(resp.cost_estimate_usd)
    print(resp.model)               # actual model used (may differ from config if fallback)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response dataclass
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """Structured response from a single LLM call."""
    text: str
    token_usage: dict[str, int]       # {"prompt": N, "completion": M, "total": T}
    model: str                        # actual endpoint used
    agent_id: str
    duration_seconds: float
    raw_response: Any = field(default=None, repr=False)

    @property
    def cost_estimate_usd(self) -> float:
        """
        Approximate cost. Rates vary by model — these are conservative estimates.
        Update when your workspace pricing changes.
        Claude  Opus 4:  $15 / $75 per 1M tokens (input/output)
        Llama 3.1 405B:  ~$5 / $15 per 1M tokens (input/output)
        """
        # Use a conservative blended rate for open-source models
        is_claude = "claude" in self.model.lower()
        input_rate  = 15.0 if is_claude else 5.0
        output_rate = 75.0 if is_claude else 15.0
        input_cost  = (self.token_usage.get("prompt",     0) / 1_000_000) * input_rate
        output_cost = (self.token_usage.get("completion", 0) / 1_000_000) * output_rate
        return round(input_cost + output_cost, 6)


# ---------------------------------------------------------------------------
# Mock client — used when no Databricks SDK is available (pure local dev)
# ---------------------------------------------------------------------------

class _MockLLMClient:
    def complete(self, prompt: str, system: str = "", **kwargs) -> LLMResponse:
        logger.warning("[MockLLMClient] No Databricks SDK found — returning stub response.")
        stub = (
            "-- [MOCK RESPONSE] --\n"
            "databricks-sdk not installed or no workspace reachable.\n"
            f"System prompt : {len(system)} chars\n"
            f"User prompt   : {len(prompt)} chars\n"
            "Install databricks-sdk and run inside a Databricks workspace."
        )
        return LLMResponse(
            text=stub,
            token_usage={"prompt": 0, "completion": 0, "total": 0},
            model="mock",
            agent_id="mock",
            duration_seconds=0.0,
        )


# ---------------------------------------------------------------------------
# HTTP error helpers
# ---------------------------------------------------------------------------

class _EndpointNotFound(Exception):
    """Raised when the serving endpoint does not exist on this workspace (404)."""

class _AuthError(Exception):
    """Raised on 401/403 — should not retry, should surface to user."""


def _raise_for_api_error(response_body: dict, status_code: int | None = None) -> None:
    """
    Inspect a Databricks API error response dict and raise the appropriate
    typed exception so the caller can handle it correctly.
    """
    code = response_body.get("error_code", status_code or 0)
    msg  = response_body.get("message", str(response_body))

    if str(code) in ("401", "403") or "Credential" in msg or "Unauthorized" in msg:
        raise _AuthError(f"Auth error ({code}): {msg}")
    if str(code) in ("404", "RESOURCE_DOES_NOT_EXIST") or "not found" in msg.lower():
        raise _EndpointNotFound(f"Endpoint not found ({code}): {msg}")
    raise RuntimeError(f"API error ({code}): {msg}")


# ---------------------------------------------------------------------------
# Main LLM client
# ---------------------------------------------------------------------------

class LLMClient:
    """
    Databricks LLM client with auto-discovery, retry, and token tracking.

    Call paths tried in order
    -------------------------
    1. ws.api_client.do()              — SDK's own OAuth session, no PAT needed
                                         Works on free-tier community workspace.
    2. ws.serving_endpoints.query()    — SDK high-level method (SDK >= 0.28)
                                         Fallback if api_client path unavailable.

    Endpoint resolution
    -------------------
    Tries llm.model first, then each entry in llm.fallback_models until a
    200 response is received. Records the working endpoint for all subsequent calls.
    """

    def __init__(self, config_loader):
        cfg = config_loader.llm_config

        self._primary_model   = cfg.get("model", "databricks-meta-llama-3.1-405b-instruct")
        self._fallback_models = cfg.get("fallback_models", [
            "databricks-gpt-oss-120b",
            "databricks-meta-llama-3.1-405b-instruct",
            "databricks-qwen3-next-80b-a3b-instruct",
            "databricks-gemma-3-12b",
            "databricks-gpt-oss-20b",
        ])
        self._max_tokens  = cfg.get("max_tokens", 4096)
        self._temperature = cfg.get("temperature", 0.2)
        self._timeout     = cfg.get("timeout_seconds", 120)
        self._retries     = cfg.get("retry_attempts", 3)
        self._backoff     = cfg.get("retry_backoff_seconds", 5)

        # Resolved at init time
        self._active_model : str | None = None   # endpoint that actually works
        self._ws_client                 = None   # WorkspaceClient
        self._use_mock      : bool      = False

        self._cumulative_tokens: dict[str, int] = {
            "prompt": 0, "completion": 0, "total": 0,
        }

        self._initialise()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def model(self) -> str:
        return self._active_model or self._primary_model

    def complete(
        self,
        prompt: str,
        system: str = "",
        agent_id: str = "unknown",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Send a prompt to the LLM and return a structured LLMResponse."""
        if self._use_mock:
            return _MockLLMClient().complete(prompt, system)

        resolved_max_tokens = max_tokens or self._max_tokens
        resolved_temp       = temperature if temperature is not None else self._temperature

        logger.info(
            f"LLM call | agent={agent_id} | model={self._active_model} | "
            f"max_tokens={resolved_max_tokens}"
        )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        return self._call_with_retry(
            messages=messages,
            agent_id=agent_id,
            max_tokens=resolved_max_tokens,
            temperature=resolved_temp,
        )

    @property
    def cumulative_tokens(self) -> dict[str, int]:
        return dict(self._cumulative_tokens)

    @property
    def cumulative_cost_usd(self) -> float:
        is_claude   = "claude" in (self._active_model or "").lower()
        input_rate  = 15.0 if is_claude else 5.0
        output_rate = 75.0 if is_claude else 15.0
        i = (self._cumulative_tokens["prompt"]     / 1_000_000) * input_rate
        o = (self._cumulative_tokens["completion"] / 1_000_000) * output_rate
        return round(i + o, 6)

    def reset_cumulative_tokens(self) -> None:
        self._cumulative_tokens = {"prompt": 0, "completion": 0, "total": 0}

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _initialise(self) -> None:
        """Connect to Databricks and discover which endpoint works."""
        try:
            from databricks.sdk import WorkspaceClient  # type: ignore
            ws   = WorkspaceClient()
            me   = ws.current_user.me()
            self._ws_client = ws
            logger.info(
                f"Databricks SDK connected | user={getattr(me, 'user_name', '?')} | "
                f"host={ws.config.host}"
            )
        except ImportError:
            logger.warning("databricks-sdk not installed → using mock client.")
            self._use_mock = True
            return
        except Exception as exc:
            logger.warning(f"Databricks SDK connection failed: {exc} → using mock client.")
            self._use_mock = True
            return

        # Discover working endpoint
        candidates = [self._primary_model] + [
            m for m in self._fallback_models if m != self._primary_model
        ]
        for model in candidates:
            logger.info(f"Probing endpoint: {model}")
            try:
                self._probe(model)
                self._active_model = model
                logger.info(f"✅ Active endpoint: {model}")
                return
            except _EndpointNotFound:
                logger.info(f"   Not available on this workspace: {model}")
                continue
            except _AuthError as e:
                # Auth errors won't be fixed by trying a different model
                logger.error(
                    f"Authentication error: {e}\n"
                    "The SDK authenticated but the serving endpoint rejected the call.\n"
                    "On free-tier community workspaces this can happen when the workspace\n"
                    "session token doesn't have permission to call serving endpoints.\n"
                    "Try generating a personal access token:\n"
                    "  Settings → Developer → Access tokens → Generate new token\n"
                    "Then set: spark.conf.set('DATABRICKS_TOKEN', '<your-token>')\n"
                    "before importing LLMClient."
                )
                self._use_mock = True
                return
            except Exception as exc:
                logger.warning(f"   Probe error for {model}: {exc}")
                continue

        # Nothing worked
        available = self._list_available_endpoints()
        logger.error(
            f"No working LLM endpoint found.\n"
            f"Configured model  : {self._primary_model}\n"
            f"Fallbacks tried   : {self._fallback_models}\n"
            f"Endpoints on this workspace: {available}\n"
            f"→ Update llm.model in configs/platform.yaml to one of: {available}\n"
            f"→ Using mock client."
        )
        self._use_mock = True

    def _probe(self, model: str) -> None:
        """
        Send a minimal test call to `model`. Raises _EndpointNotFound,
        _AuthError, or RuntimeError depending on the failure mode.
        """
        try:
            resp = self._api_client_call(
                model=model,
                messages=[{"role": "user", "content": "Reply with the word OK only."}],
                max_tokens=5,
                temperature=0.0,
            )
            # Success — validate response shape
            _ = self._parse_response(resp)
        except (_EndpointNotFound, _AuthError):
            raise
        except Exception as exc:
            raise RuntimeError(f"Probe failed: {exc}") from exc

    def _list_available_endpoints(self) -> list[str]:
        """Return names of all serving endpoints visible in this workspace."""
        try:
            return [ep.name for ep in self._ws_client.serving_endpoints.list()]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Retry wrapper
    # ------------------------------------------------------------------

    def _call_with_retry(
        self,
        messages: list[dict],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        last_error: Exception | None = None

        for attempt in range(1, self._retries + 1):
            try:
                start = time.perf_counter()
                raw   = self._api_client_call(
                    model=self._active_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                duration = time.perf_counter() - start

                text, usage = self._parse_response(raw)

                self._cumulative_tokens["prompt"]     += usage.get("prompt",     0)
                self._cumulative_tokens["completion"] += usage.get("completion", 0)
                self._cumulative_tokens["total"]      += usage.get("total",      0)

                result = LLMResponse(
                    text=text,
                    token_usage=usage,
                    model=self._active_model,
                    agent_id=agent_id,
                    duration_seconds=round(duration, 3),
                    raw_response=raw,
                )
                logger.info(
                    f"LLM OK | agent={agent_id} | tokens={usage.get('total','?')} | "
                    f"cost≈${result.cost_estimate_usd} | {result.duration_seconds}s"
                )
                return result

            except _AuthError:
                # No point retrying auth failures
                raise
            except Exception as exc:
                last_error = exc
                wait = self._backoff * (2 ** (attempt - 1))   # 5 → 10 → 20s
                logger.warning(
                    f"LLM attempt {attempt}/{self._retries} failed: {exc}. "
                    + (f"Retrying in {wait}s…" if attempt < self._retries else "No more retries.")
                )
                if attempt < self._retries:
                    time.sleep(wait)

        raise RuntimeError(
            f"LLM call failed after {self._retries} attempts. Last error: {last_error}"
        ) from last_error

    # ------------------------------------------------------------------
    # Core API call — ws.api_client.do() with serving_endpoints fallback
    # ------------------------------------------------------------------

    def _api_client_call(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> dict:
        """
        Call a Databricks serving endpoint via ws.api_client.do().

        This uses the SDK's own authenticated HTTP session — no PAT token is
        needed. It works on both free-tier (OAuth browser session) and
        enterprise (PAT / service principal) workspaces.

        Falls back to ws.serving_endpoints.query() if api_client raises an
        AttributeError (very old SDK versions that predate api_client).
        """
        payload = {
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
        }

        # ── Primary: api_client.do() ──────────────────────────────────────
        try:
            raw = self._ws_client.api_client.do(
                method="POST",
                path=f"/serving-endpoints/{model}/invocations",
                body=payload,
            )
            # api_client.do() raises on HTTP errors internally on newer SDK
            # versions; on older ones it may return the error as a dict.
            if isinstance(raw, dict) and "error_code" in raw:
                _raise_for_api_error(raw)
            return raw  # type: ignore[return-value]

        except (_EndpointNotFound, _AuthError):
            raise
        except AttributeError:
            # api_client not available on this SDK version — try high-level method
            logger.debug("api_client.do not available, trying serving_endpoints.query()")
            return self._serving_endpoints_call(model, messages, max_tokens, temperature)
        except Exception as exc:
            # Check if it's a known error shape we can classify
            msg = str(exc)
            if "404" in msg or "not found" in msg.lower() or "RESOURCE_DOES_NOT_EXIST" in msg:
                raise _EndpointNotFound(msg) from exc
            if "401" in msg or "403" in msg or "Credential" in msg:
                raise _AuthError(msg) from exc
            raise

    def _serving_endpoints_call(
        self,
        model: str,
        messages: list[dict],
        max_tokens: int,
        temperature: float,
    ) -> dict:
        """
        Fallback: ws.serving_endpoints.query() — requires SDK >= 0.28.
        Older versions raise 'dict has no attribute as_dict' which we catch.
        """
        try:
            resp = self._ws_client.serving_endpoints.query(
                name=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            # Normalise SDK object → dict
            if hasattr(resp, "as_dict"):
                return resp.as_dict()
            return self._obj_to_dict(resp)
        except Exception as exc:
            msg = str(exc)
            if "as_dict" in msg or "attribute" in msg:
                raise RuntimeError(
                    "SDK version too old to support serving_endpoints.query() with dict messages. "
                    "Run: %pip install --upgrade databricks-sdk"
                ) from exc
            if "404" in msg or "not found" in msg.lower():
                raise _EndpointNotFound(msg) from exc
            if "401" in msg or "403" in msg:
                raise _AuthError(msg) from exc
            raise

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, response: Any) -> tuple[str, dict[str, int]]:
        """
        Extract (text, token_usage) from a dict response.
        Handles both OpenAI-compatible shape and Databricks-specific shapes.
        """
        # Normalise to dict
        if not isinstance(response, dict):
            try:
                response = response.as_dict()
            except AttributeError:
                response = self._obj_to_dict(response)

        # Check for error payload
        if "error_code" in response:
            _raise_for_api_error(response)

        # Extract text
        try:
            text = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError(
                f"Cannot extract text from response. "
                f"Unexpected shape: {str(response)[:300]}"
            ) from exc

        # Extract token usage
        u     = response.get("usage", {})
        prompt_t    = u.get("prompt_tokens",     0)
        completion_t= u.get("completion_tokens", 0)
        total_t     = u.get("total_tokens",      prompt_t + completion_t)
        usage = {
            "prompt":     prompt_t,
            "completion": completion_t,
            "total":      total_t,
        }
        return text, usage

    @staticmethod
    def _obj_to_dict(obj: Any) -> dict:
        """Convert an SDK response object to a plain dict by walking attributes."""
        def _cvt(o):
            if isinstance(o, dict):  return {k: _cvt(v) for k, v in o.items()}
            if isinstance(o, list):  return [_cvt(i) for i in o]
            if hasattr(o, "__dict__"):
                return {k: _cvt(v) for k, v in o.__dict__.items()
                        if not k.startswith("_")}
            return o
        return _cvt(obj)
