"""LLM client used to draft agent personas, tasks, and full crew designs.

Two backends:

- ``ClouderaInferenceClient`` — calls Cloudera AI Inference Service via an
  OpenAI-compatible endpoint. Reads ``CDP_INFERENCE_ENDPOINT`` and
  ``CDP_INFERENCE_API_KEY``.
- ``OpenAICompatibleClient`` — hits any OpenAI-compatible chat completions
  endpoint (OpenAI itself, Anthropic's Claude via its OpenAI-compat surface,
  a local vLLM/TGI, etc.). Reads ``OPENAI_API_KEY`` and optional
  ``OPENAI_BASE_URL`` / ``OPENAI_MODEL``. When ``ANTHROPIC_API_KEY`` is set
  and no OpenAI key is, defaults to Anthropic's endpoint.

Clients expose ``draft_agent``, ``draft_task``, and ``draft_design``,
returning structured drafts the UI folds into forms or a full ``Design``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict
from urllib.parse import urlparse

import httpx


BackendKind = Literal["cloudera", "openai", "anthropic"]

# Transient HTTP statuses worth retrying before surfacing to the UI.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_DEFAULT_TIMEOUT = 60.0
_DEFAULT_RETRIES = 2
_RETRY_BACKOFF_SEC = 0.5
# Full crew JSON is larger than a single agent/task draft.
_DESIGN_MAX_TOKENS = 4096


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base class for every LLM client failure the UI can surface."""


class LLMConfigError(LLMError, ValueError):
    """Raised when backend config is missing or malformed before any request."""


class LLMHTTPError(LLMError):
    """Raised when the remote endpoint returns an error or is unreachable."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMParseError(LLMError):
    """Raised when the model reply cannot be parsed into the expected JSON."""


class BackendDefaults(TypedDict):
    label: str
    base_url: str
    model: str


# ---------------------------------------------------------------------------
# Draft types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentDraft:
    role: str
    goal: str
    backstory: str


@dataclass(frozen=True)
class TaskDraft:
    description: str
    expected_output: str


@dataclass(frozen=True)
class DesignToolSpec:
    name: str
    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DesignAgentSpec:
    name: str
    role: str
    goal: str
    backstory: str
    tools: tuple[str, ...] = ()
    allow_delegation: bool = False


@dataclass(frozen=True)
class DesignTaskSpec:
    name: str
    description: str
    expected_output: str
    agent: str | None = None
    context: tuple[str, ...] = ()


@dataclass(frozen=True)
class DesignCrewSpec:
    name: str
    process: str = "sequential"
    manager_llm: str | None = None


@dataclass(frozen=True)
class DesignDraft:
    """Structured multi-agent crew draft from :meth:`LLMClient.draft_design`."""

    crew: DesignCrewSpec
    agents: tuple[DesignAgentSpec, ...]
    tasks: tuple[DesignTaskSpec, ...]
    tools: tuple[DesignToolSpec, ...] = ()


# ---------------------------------------------------------------------------
# Prompts (bilingual)
# ---------------------------------------------------------------------------

_AGENT_SYSTEM = {
    "en": (
        "You are drafting a CrewAI agent from a one-line description. "
        "Return STRICT JSON with keys: role, goal, backstory. "
        "role is a short job title. goal is a single sentence describing what "
        "the agent aims to achieve. backstory is 2–4 sentences of persona and "
        "expertise, written in third person. Do not include any other keys or "
        "prose outside the JSON object."
    ),
    "es": (
        "Estás redactando un agente CrewAI a partir de una descripción de una "
        "línea. Devuelve JSON ESTRICTO con las claves: role, goal, backstory. "
        "role es un título de puesto corto. goal es una sola oración que "
        "describe lo que busca el agente. backstory son 2–4 oraciones de "
        "personalidad y experiencia, escritas en tercera persona. No incluyas "
        "otras claves ni texto fuera del objeto JSON. Todo el contenido debe "
        "estar en español."
    ),
}

_TASK_SYSTEM = {
    "en": (
        "You are drafting a CrewAI task from a one-line description. "
        "Return STRICT JSON with keys: description, expected_output. "
        "description explains what the task does (1–3 sentences). "
        "expected_output describes what a successful completion looks like: "
        "shape, format, and any acceptance criteria (1–3 sentences). "
        "Do not include any other keys or prose outside the JSON object."
    ),
    "es": (
        "Estás redactando una tarea CrewAI a partir de una descripción de una "
        "línea. Devuelve JSON ESTRICTO con las claves: description, "
        "expected_output. description explica qué hace la tarea (1–3 "
        "oraciones). expected_output describe cómo se ve una finalización "
        "exitosa: forma, formato y criterios de aceptación (1–3 oraciones). "
        "No incluyas otras claves ni texto fuera del objeto JSON. Todo el "
        "contenido debe estar en español."
    ),
}


def _catalog_kinds_for_prompt() -> list[str]:
    """Catalog kinds the design prompt may reference (excludes CustomTool)."""
    from .tools_catalog import CATALOG, is_custom

    return [e.kind for e in CATALOG if not is_custom(e.kind)]


def design_system_prompt(lang: str) -> str:
    """Build the bilingual system prompt for :meth:`LLMClient.draft_design`.

    Injects the live catalog kind list so the model only suggests tools the
    designer can materialize without CustomTool codegen.
    """
    kinds = ", ".join(_catalog_kinds_for_prompt())
    if lang == "es":
        return (
            "Estás diseñando un crew multi-agente de CrewAI a partir de un "
            "objetivo en lenguaje natural. Devuelve JSON ESTRICTO con las "
            "claves de nivel superior: crew, agents, tasks, tools.\n"
            "Límites: 2–5 agents, 2–6 tasks.\n"
            "crew: {name (identificador Python válido, CapWords preferido), "
            "process ('sequential' o 'hierarchical'), manager_llm (string o "
            "null; requerido si process es hierarchical)}.\n"
            "agents[]: {name (identificador), role, goal, backstory, "
            "tools (lista de nombres de tools[]), allow_delegation (bool)}.\n"
            "tasks[]: {name (identificador), description, expected_output, "
            "agent (name de un agent), context (lista de names de tasks "
            "previas)}.\n"
            "tools[]: {name (identificador), kind, params (objeto)}.\n"
            f"kind DEBE ser uno de: {kinds}. "
            "No inventes otras clases de tools. "
            "Asigna cada task a un agent. Usa context para dependencias "
            "secuenciales cuando ayude. Prefiere sequential salvo que el "
            "objetivo pida claramente un manager. "
            "Todo el contenido textual en español. "
            "Sin prosa fuera del objeto JSON."
        )
    return (
        "You are designing a CrewAI multi-agent crew from a natural-language "
        "objective. Return STRICT JSON with top-level keys: crew, agents, "
        "tasks, tools.\n"
        "Limits: 2–5 agents, 2–6 tasks.\n"
        "crew: {name (valid Python identifier, CapWords preferred), "
        "process ('sequential' or 'hierarchical'), manager_llm (string or "
        "null; required when process is hierarchical)}.\n"
        "agents[]: {name (identifier), role, goal, backstory, "
        "tools (list of tools[].name refs), allow_delegation (bool)}.\n"
        "tasks[]: {name (identifier), description, expected_output, "
        "agent (an agents[].name), context (list of prior task names)}.\n"
        "tools[]: {name (identifier), kind, params (object)}.\n"
        f"kind MUST be one of: {kinds}. "
        "Do not invent other tool classes. "
        "Wire every task to an agent. Use context for sequential "
        "dependencies when useful. Prefer sequential unless the objective "
        "clearly needs a manager. "
        "No prose outside the JSON object."
    )


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    """Interface implemented by every backend."""

    label: str  # Shown in the sidebar

    def draft_agent(self, description: str, lang: str) -> AgentDraft: ...
    def draft_task(self, description: str, lang: str) -> TaskDraft: ...
    def draft_design(self, prompt: str, lang: str) -> DesignDraft: ...
    def ping(self) -> str: ...


# ---------------------------------------------------------------------------
# OpenAI-compatible implementation (shared by Cloudera, OpenAI, Anthropic)
# ---------------------------------------------------------------------------

class OpenAICompatibleClient:
    """Chat-completions client for any OpenAI-shaped endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        label: str,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.label = label
        self._timeout = timeout
        self._max_retries = max_retries

    def _post(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        """Call chat/completions and return the assistant text.

        Deliberately does NOT set ``response_format: json_object``. In
        practice some OpenAI-compatible proxies (notably LiteLLM used by
        Cloudera's AI Gateway) satisfy that flag by *forcing a tool call*
        instead of returning the JSON in ``message.content`` — resulting in
        an empty ``content`` field and the actual JSON hidden inside
        ``tool_calls[0].function.arguments``. We rely on the strict-JSON
        instruction in the system prompt and the tolerant `_parse_json`
        helper instead.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.4,
            # Generous cap: reasoning models (Gemini 3, o1) consume this on
            # internal thinking before they emit any user-visible text; smaller
            # values can produce an empty choice.
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        data = _request_json(
            f"{self._base_url}/chat/completions",
            payload=payload,
            headers=headers,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )
        choices = data.get("choices") or []
        if not choices:
            usage = data.get("usage", {})
            raise LLMHTTPError(
                "Endpoint returned no choices "
                f"(usage={usage}). Model may have exhausted max_tokens on "
                "reasoning without producing visible text — try a larger "
                "max_tokens or a non-reasoning model."
            )
        message = choices[0].get("message", {})
        content = message.get("content") or ""
        # Some proxies still smuggle the JSON into a synthetic tool call —
        # fall back to the tool_call arguments if content is empty.
        if not content.strip() and message.get("tool_calls"):
            try:
                return message["tool_calls"][0]["function"]["arguments"]
            except (KeyError, IndexError, TypeError):
                pass
        return content

    def draft_agent(self, description: str, lang: str) -> AgentDraft:
        system = _AGENT_SYSTEM.get(lang, _AGENT_SYSTEM["en"])
        raw = self._post(system, description)
        obj = _parse_json(raw, required={"role", "goal", "backstory"})
        return AgentDraft(
            role=str(obj["role"]),
            goal=str(obj["goal"]),
            backstory=str(obj["backstory"]),
        )

    def draft_task(self, description: str, lang: str) -> TaskDraft:
        system = _TASK_SYSTEM.get(lang, _TASK_SYSTEM["en"])
        raw = self._post(system, description)
        obj = _parse_json(raw, required={"description", "expected_output"})
        return TaskDraft(
            description=str(obj["description"]),
            expected_output=str(obj["expected_output"]),
        )

    def draft_design(self, prompt: str, lang: str) -> DesignDraft:
        system = design_system_prompt(lang)
        raw = self._post(system, prompt, max_tokens=_DESIGN_MAX_TOKENS)
        obj = _parse_json(raw, required={"crew", "agents", "tasks"})
        return design_draft_from_obj(obj)

    def ping(self) -> str:
        """Send a minimal request to confirm the endpoint is reachable and
        the API key is valid. Returns the assistant reply.

        On error, surfaces the gateway's own message (LiteLLM's ``error.message``
        is far more informative than the raw ``httpx`` HTTPStatusError, which
        just says ``400 Bad Request`` — the actual reason like "Invalid model
        name" is buried in the response body).
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "user", "content": "Reply with the single word: pong"},
            ],
            # Reasoning models (Gemini 3, o1, etc.) burn internal "thinking"
            # tokens against max_tokens and can return an empty choice if
            # the cap is too low. 256 is plenty for a one-word reply on any
            # model and only costs a fraction of a cent even at unused-cap
            # billing rates.
            "max_tokens": 256,
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        data = _request_json(
            f"{self._base_url}/chat/completions",
            payload=payload,
            headers=headers,
            timeout=self._timeout,
            max_retries=self._max_retries,
        )
        choices = data.get("choices") or []
        if not choices:
            # Some models (Gemini reasoning tier via LiteLLM) can return
            # 200 with an empty `choices` list if all tokens were spent on
            # internal reasoning. Surface that explicitly instead of an
            # IndexError, and hint at the fix.
            usage = data.get("usage", {})
            raise LLMHTTPError(
                "Endpoint returned no choices "
                f"(usage={usage}). Model may have exhausted max_tokens on "
                "reasoning without producing visible text — try a larger "
                "max_tokens or a non-reasoning model."
            )
        return (choices[0].get("message", {}).get("content") or "").strip()

    def describe(self) -> dict[str, str]:
        """Return a redacted summary of this client's config for display."""
        return {
            "label": self.label,
            "base_url": self._base_url,
            "model": self._model,
        }


# ---------------------------------------------------------------------------
# Cloudera Inference — thin wrapper that just re-labels the OpenAI client
# ---------------------------------------------------------------------------

class ClouderaInferenceClient(OpenAICompatibleClient):
    """Cloudera AI Inference Service exposes an OpenAI-compatible endpoint.

    Distinct class so the sidebar can label the active backend clearly and
    so we can add Cloudera-specific tweaks later without touching the OpenAI
    path.
    """


# ---------------------------------------------------------------------------
# Anthropic — native Messages API (not OpenAI-shaped)
# ---------------------------------------------------------------------------


# Header value pinned here so the app doesn't depend on the newest date each
# time we bump the SDK. Any date after 2023-06-01 works for /v1/messages.
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicClient:
    """Client for Anthropic's native Messages API.

    Anthropic does not accept OpenAI's ``/chat/completions`` shape:
    - auth is ``x-api-key: <key>`` (not ``Authorization: Bearer``),
    - endpoint is ``/v1/messages``,
    - the system prompt is a top-level ``system`` field (not a first message
      with ``role: "system"``),
    - responses come back as ``content: [{"type": "text", "text": "..."}]``.

    Using the OpenAI-compatible client against ``api.anthropic.com`` returns
    ``401 Unauthorized`` because the request is missing the ``x-api-key``
    header. This class does the right thing.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        label: str,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self.label = label
        self._timeout = timeout
        self._max_retries = max_retries

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    def _post(self, system: str, user: str, *, max_tokens: int = 1024) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": 0.4,
        }
        data = _request_json(
            f"{self._base_url}/messages",
            payload=payload,
            headers=self._headers(),
            timeout=self._timeout,
            max_retries=self._max_retries,
        )
        # Extract concatenated text from the response content blocks. Ignore
        # tool_use / thinking blocks — we only ask for text back.
        chunks: list[str] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                chunks.append(block.get("text", ""))
        return "".join(chunks)

    def draft_agent(self, description: str, lang: str) -> AgentDraft:
        system = _AGENT_SYSTEM.get(lang, _AGENT_SYSTEM["en"])
        raw = self._post(system, description)
        obj = _parse_json(raw, required={"role", "goal", "backstory"})
        return AgentDraft(
            role=str(obj["role"]),
            goal=str(obj["goal"]),
            backstory=str(obj["backstory"]),
        )

    def draft_task(self, description: str, lang: str) -> TaskDraft:
        system = _TASK_SYSTEM.get(lang, _TASK_SYSTEM["en"])
        raw = self._post(system, description)
        obj = _parse_json(raw, required={"description", "expected_output"})
        return TaskDraft(
            description=str(obj["description"]),
            expected_output=str(obj["expected_output"]),
        )

    def draft_design(self, prompt: str, lang: str) -> DesignDraft:
        system = design_system_prompt(lang)
        raw = self._post(system, prompt, max_tokens=_DESIGN_MAX_TOKENS)
        obj = _parse_json(raw, required={"crew", "agents", "tasks"})
        return design_draft_from_obj(obj)

    def ping(self) -> str:
        """Minimal reachability + auth check. Uses a tiny ``max_tokens`` so a
        misconfigured client fails fast and cheaply."""
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 16,
            "messages": [
                {"role": "user", "content": "Reply with the single word: pong"},
            ],
        }
        data = _request_json(
            f"{self._base_url}/messages",
            payload=payload,
            headers=self._headers(),
            timeout=self._timeout,
            max_retries=self._max_retries,
        )
        for block in data.get("content", []):
            if block.get("type") == "text":
                return block.get("text", "").strip()
        return ""

    def describe(self) -> dict[str, str]:
        return {
            "label": self.label,
            "base_url": self._base_url,
            "model": self._model,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# Defaults used when the sidebar form populates a fresh row for a given backend.
BACKEND_DEFAULTS: dict[BackendKind, BackendDefaults] = {
    "cloudera": {
        "label": "Cloudera AI Inference",
        "base_url": "",
        "model": "meta-llama-3-1-70b-instruct",
    },
    "openai": {
        "label": "OpenAI-compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-opus-4-8",
    },
}

# Display labels that may linger in session state from older builds or
# i18n strings — map them back to the radio option keys.
_BACKEND_LABEL_ALIASES: dict[str, BackendKind] = {
    "Cloudera AI Inference": "cloudera",
    "OpenAI-compatible": "openai",
    "Compatible con OpenAI": "openai",
    "Anthropic": "anthropic",
}


def normalize_backend(value: object) -> BackendKind:
    """Coerce a session-state / UI value into a valid ``BackendKind``.

    The sidebar radio options are ``cloudera`` / ``openai`` / ``anthropic``.
    Stale session state sometimes holds a display label (e.g.
    ``"OpenAI-compatible"``) instead — calling ``list.index`` on that
    raises ``ValueError``. Unknown values fall back to env detection, then
    ``"openai"``.
    """
    if isinstance(value, str):
        if value in BACKEND_DEFAULTS:
            return value  # type: ignore[return-value]
        aliased = _BACKEND_LABEL_ALIASES.get(value)
        if aliased is not None:
            return aliased
    return detect_backend_from_env() or "openai"


def validate_config(*, base_url: str, api_key: str, model: str) -> None:
    """Fail fast on missing or malformed LLM settings.

    Raises:
        LLMConfigError: when a required field is blank or ``base_url`` is not
            an absolute http(s) URL.
    """
    if not api_key.strip():
        raise LLMConfigError("API key is required.")
    if not base_url.strip():
        raise LLMConfigError("Base URL is required.")
    if not model.strip():
        raise LLMConfigError("Model is required.")
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise LLMConfigError(
            f"Base URL must be an absolute http(s) URL, got {base_url!r}."
        )


def build_from_config(
    backend: BackendKind, *, base_url: str, api_key: str, model: str
) -> LLMClient:
    """Construct a client from explicit values (typically the sidebar form).

    Bypasses env-var precedence — the caller is telling us exactly which
    backend they want and with what credentials. Raises ``LLMConfigError`` if
    any required field is missing or malformed.
    """
    validate_config(base_url=base_url, api_key=api_key, model=model)
    label = BACKEND_DEFAULTS[backend]["label"]
    if backend == "cloudera":
        cls = ClouderaInferenceClient
    elif backend == "anthropic":
        cls = AnthropicClient
    else:
        cls = OpenAICompatibleClient
    return cls(
        base_url=base_url.strip(),
        api_key=api_key.strip(),
        model=model.strip(),
        label=label,
    )


def detect_backend_from_env() -> BackendKind | None:
    """Return which backend `build_client()` would pick from the environment,
    or None if nothing's configured. Used by the sidebar to pre-select the
    right radio option when the app starts."""
    if os.environ.get("CDP_INFERENCE_ENDPOINT") and (
        os.environ.get("CDP_INFERENCE_API_KEY") or os.environ.get("CDP_TOKEN")
    ):
        return "cloudera"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def build_client() -> LLMClient | None:
    """Return the first configured backend, or None if nothing is set.

    Order of precedence: Cloudera Inference → OpenAI → Anthropic. This lets a
    Cloudera AI deployment "just work" without touching env vars, while
    local development falls back to whatever key you already have.
    """
    cdp_endpoint = os.environ.get("CDP_INFERENCE_ENDPOINT")
    cdp_key = os.environ.get("CDP_INFERENCE_API_KEY") or os.environ.get("CDP_TOKEN")
    if cdp_endpoint and cdp_key:
        return ClouderaInferenceClient(
            base_url=cdp_endpoint,
            api_key=cdp_key,
            model=os.environ.get("CDP_INFERENCE_MODEL", "meta-llama-3-1-70b-instruct"),
            label="Cloudera AI Inference",
        )

    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return OpenAICompatibleClient(
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=openai_key,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            label="OpenAI-compatible",
        )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        # Anthropic uses its native Messages API — not OpenAI's shape.
        return AnthropicClient(
            base_url=os.environ.get(
                "ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"
            ),
            api_key=anthropic_key,
            model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8"),
            label="Anthropic",
        )

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _request_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    max_retries: int,
) -> dict[str, Any]:
    """POST JSON with retries on transient network / gateway failures.

    Retries ``max_retries`` times after the first attempt on timeouts,
    connection errors, and retryable HTTP statuses (429/5xx). Non-retryable
    4xx responses fail immediately with :class:`LLMHTTPError`.
    """
    attempts = max(1, max_retries + 1)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                message = _extract_error(resp)
                if resp.status_code in _RETRYABLE_STATUS and attempt < attempts - 1:
                    last_error = LLMHTTPError(message, status_code=resp.status_code)
                    time.sleep(_RETRY_BACKOFF_SEC * (2**attempt))
                    continue
                raise LLMHTTPError(message, status_code=resp.status_code)
            try:
                data = resp.json()
            except json.JSONDecodeError as e:
                raise LLMHTTPError(
                    f"Endpoint returned non-JSON body (HTTP {resp.status_code}): "
                    f"{resp.text[:200]}"
                ) from e
            if not isinstance(data, dict):
                raise LLMHTTPError(
                    f"Endpoint returned JSON {type(data).__name__}, expected object."
                )
            return data
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_error = LLMHTTPError(f"Request failed: {e}")
            if attempt < attempts - 1:
                time.sleep(_RETRY_BACKOFF_SEC * (2**attempt))
                continue
            raise last_error from e
    assert last_error is not None
    raise last_error


def _extract_error(resp: httpx.Response) -> str:
    """Pull the most useful error message out of an HTTP error response.

    OpenAI and Anthropic both nest their real error text under
    ``error.message``. LiteLLM (Cloudera AI Gateway) does the same. Falling
    back to the raw body means the user always gets something useful in the
    UI — never just "400 Bad Request".
    """
    try:
        body = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}: {resp.text[:300]}"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return f"HTTP {resp.status_code}: {err['message']}"
        if isinstance(err, str):
            return f"HTTP {resp.status_code}: {err}"
        if body.get("message"):
            return f"HTTP {resp.status_code}: {body['message']}"
    return f"HTTP {resp.status_code}: {resp.text[:300]}"


def _parse_json(raw: str, *, required: set[str]) -> dict[str, Any]:
    """Extract a JSON object from the model output, tolerating light
    conversational wrapping like ``json {...}`` or fenced code blocks.

    Returns the full parsed object (nested structures preserved). Callers
    that need string fields should coerce themselves.
    """
    text = raw.strip()
    if not text:
        raise LLMParseError("LLM returned an empty response.")
    # Strip common fences the model sometimes wraps output in.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # Fall back to slicing between first `{` and last `}` if extra prose sneaks in.
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        first = text.find("{")
        last = text.rfind("}")
        if first == -1 or last == -1 or last <= first:
            raise LLMParseError(
                "LLM response was not valid JSON "
                f"(preview={text[:120]!r})."
            ) from None
        try:
            obj = json.loads(text[first : last + 1])
        except json.JSONDecodeError as e:
            raise LLMParseError(
                f"LLM response JSON could not be parsed: {e} "
                f"(preview={text[first:first + 120]!r})."
            ) from e
    if not isinstance(obj, dict):
        raise LLMParseError(
            f"LLM response JSON must be an object, got {type(obj).__name__}."
        )
    missing = required - set(obj)
    if missing:
        raise LLMParseError(f"LLM response missing keys: {sorted(missing)}")
    return obj


def design_draft_from_obj(obj: dict[str, Any]) -> DesignDraft:
    """Coerce a parsed JSON object into a :class:`DesignDraft`.

    Raises :class:`LLMParseError` when required nested shapes are wrong.
    """
    crew_raw = obj.get("crew")
    if not isinstance(crew_raw, dict):
        raise LLMParseError("design JSON 'crew' must be an object")
    name = str(crew_raw.get("name") or "GeneratedCrew").strip() or "GeneratedCrew"
    process = str(crew_raw.get("process") or "sequential").strip().lower()
    if process not in ("sequential", "hierarchical"):
        process = "sequential"
    manager_llm = crew_raw.get("manager_llm")
    if manager_llm is not None:
        manager_llm = str(manager_llm).strip() or None
    crew = DesignCrewSpec(name=name, process=process, manager_llm=manager_llm)

    agents_raw = obj.get("agents")
    if not isinstance(agents_raw, list) or not agents_raw:
        raise LLMParseError("design JSON 'agents' must be a non-empty list")
    agents: list[DesignAgentSpec] = []
    for i, entry in enumerate(agents_raw):
        if not isinstance(entry, dict):
            raise LLMParseError(f"agents[{i}] must be an object")
        tools = entry.get("tools") or []
        if not isinstance(tools, list):
            tools = []
        agents.append(
            DesignAgentSpec(
                name=str(entry.get("name") or f"agent_{i + 1}").strip(),
                role=str(entry.get("role") or "").strip() or "Agent",
                goal=str(entry.get("goal") or "").strip() or "Complete assigned tasks.",
                backstory=str(entry.get("backstory") or "").strip() or "A capable specialist.",
                tools=tuple(str(t) for t in tools if t),
                allow_delegation=bool(entry.get("allow_delegation", False)),
            )
        )

    tasks_raw = obj.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise LLMParseError("design JSON 'tasks' must be a non-empty list")
    tasks: list[DesignTaskSpec] = []
    for i, entry in enumerate(tasks_raw):
        if not isinstance(entry, dict):
            raise LLMParseError(f"tasks[{i}] must be an object")
        context = entry.get("context") or []
        if not isinstance(context, list):
            context = []
        agent_ref = entry.get("agent")
        tasks.append(
            DesignTaskSpec(
                name=str(entry.get("name") or f"task_{i + 1}").strip(),
                description=str(entry.get("description") or "").strip()
                or "Complete the assigned work.",
                expected_output=str(entry.get("expected_output") or "").strip()
                or "A clear deliverable.",
                agent=str(agent_ref).strip() if agent_ref else None,
                context=tuple(str(c) for c in context if c),
            )
        )

    tools_raw = obj.get("tools") or []
    if not isinstance(tools_raw, list):
        tools_raw = []
    tools: list[DesignToolSpec] = []
    for i, entry in enumerate(tools_raw):
        if not isinstance(entry, dict):
            continue
        params = entry.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        tools.append(
            DesignToolSpec(
                name=str(entry.get("name") or f"tool_{i + 1}").strip(),
                kind=str(entry.get("kind") or "").strip(),
                params=dict(params),
            )
        )

    return DesignDraft(
        crew=crew,
        agents=tuple(agents),
        tasks=tuple(tasks),
        tools=tuple(tools),
    )
