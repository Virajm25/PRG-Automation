"""Claude client (Anthropic Messages API over plain HTTPS - no SDK needed, so it bundles cleanly into the Windows build).

The API key comes only from the environment (ANTHROPIC_API_KEY, normally via .env). Two model tiers are used so cheap work never
runs on an expensive model:
  FAST   (claude-haiku-5-5)  - classification, field-device / tag enrichment, structured text extraction
  STRONG (claude-sonnet-5-5) - reading drawing images (vision): scanned sheets and sheets the parsers could not read
Override with PRG_MODEL_FAST / PRG_MODEL_STRONG."""
import os, time, base64, json
import requests

API = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/") + "/v1/messages"
VERSION = "2023-06-01"
DEFAULT_FAST = "claude-haiku-5-5"
DEFAULT_STRONG = "claude-sonnet-5-5"


class LLMError(Exception):
    pass


class Claude:
    def __init__(self, api_key=None, fast=None, strong=None, timeout=120):
        self.key = (api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")).strip()
        self.fast = fast or os.environ.get("PRG_MODEL_FAST") or DEFAULT_FAST
        self.strong = strong or os.environ.get("PRG_MODEL_STRONG") or DEFAULT_STRONG
        self.timeout = timeout

    @property
    def ready(self):
        return bool(self.key)

    def _post(self, payload, retries=3):
        h = {"x-api-key": self.key, "anthropic-version": VERSION, "content-type": "application/json"}
        for a in range(retries):
            try:
                r = requests.post(API, headers=h, json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                if a == retries - 1:
                    raise LLMError(f"network error: {e}")
                time.sleep(2 ** a)
                continue
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(min(2 ** (a + 1), 15))
                continue
            if r.status_code != 200:
                raise LLMError(f"Claude API HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise LLMError("Claude API rate-limited / unavailable after retries")

    def json_call(self, system, user, image_png=None, model=None, max_tokens=4000):
        """One request that must come back as a JSON object. Uses a forced tool call so the reply is always parseable."""
        content = [{"type": "text", "text": user}]
        if image_png:
            content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                           "data": base64.b64encode(image_png).decode()}})
        payload = dict(model=model or (self.strong if image_png else self.fast), max_tokens=max_tokens, system=system,
                       messages=[{"role": "user", "content": content}],
                       tools=[{"name": "return_result", "description": "Return the structured result.",
                               "input_schema": {"type": "object", "additionalProperties": True}}],
                       tool_choice={"type": "tool", "name": "return_result"})
        out = self._post(payload)
        for block in out.get("content", []):
            if block.get("type") == "tool_use":
                return block.get("input") or {}
        raise LLMError("model did not return a structured result")

    provider = "anthropic"


# ---------------------------------------------------------------------------------------------------------------------
# Free / low-cost alternatives. Gemini, Groq, OpenRouter and Ollama all expose an OpenAI-compatible chat endpoint, so one
# small client covers them. Defaults below are free-tier models; override with PRG_MODEL_FAST / PRG_MODEL_STRONG.
PRESETS = {
    "gemini":     dict(base="https://generativelanguage.googleapis.com/v1beta/openai", key_env="GEMINI_API_KEY",
                       fast="gemini-3.8-flash", strong="gemini-3.8-flash", gap=4.0),     # 2.5-flash is closed to new API users
    "groq":       dict(base="https://api.groq.com/openai/v1", key_env="GROQ_API_KEY",
                       fast="llama-3.3-70b-versatile", strong="meta-llama/llama-4-scout-17b-16e-instruct", gap=2.0),
    "openrouter": dict(base="https://openrouter.ai/api/v1", key_env="OPENROUTER_API_KEY",
                       fast="meta-llama/llama-3.3-70b-instruct:free", strong="google/gemma-3-27b-it:free", gap=3.5),
    "ollama":     dict(base=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), key_env=None,
                       fast="qwen2.5:7b", strong="llama3.2-vision", gap=0.0),
}


def _extract_json(text):
    """Pull the first JSON object out of a model reply (handles ``` fences and stray prose)."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        raise LLMError("model did not return JSON")
    try:
        return json.loads(t[a:b + 1])
    except ValueError as e:
        raise LLMError(f"model returned invalid JSON: {e}")


class OpenAICompat:
    """Same interface as Claude (ready / fast / strong / json_call) for free OpenAI-compatible providers."""

    def __init__(self, provider, api_key=None, fast=None, strong=None, timeout=180):
        cfg = PRESETS[provider]
        self.provider = provider
        self.base = cfg["base"].rstrip("/")
        env = cfg["key_env"]
        self.key = (api_key if api_key is not None else (os.environ.get(env, "") if env else "")).strip()
        self.fast = fast or os.environ.get("PRG_MODEL_FAST") or cfg["fast"]
        self.strong = strong or os.environ.get("PRG_MODEL_STRONG") or cfg["strong"]
        self.gap, self.timeout, self._last = cfg["gap"], timeout, 0.0

    @property
    def ready(self):
        return bool(self.key) if PRESETS[self.provider]["key_env"] else True

    def _post(self, payload, retries=5):
        h = {"content-type": "application/json"}
        if self.key:
            h["authorization"] = f"Bearer {self.key}"
        for a in range(retries):
            wait = self.gap - (time.time() - self._last)        # stay under the free-tier requests-per-minute cap
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = requests.post(self.base + "/chat/completions", headers=h, json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                if a == retries - 1:
                    raise LLMError(f"network error: {e}")
                time.sleep(2 ** a)
                continue
            if r.status_code in (429, 500, 502, 503):
                try:
                    delay = float(r.headers.get("retry-after", 0))
                except ValueError:
                    delay = 0
                time.sleep(min(max(delay, 2 ** (a + 2)), 60))
                continue
            if r.status_code != 200:
                raise LLMError(f"{self.provider} API HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise LLMError(f"{self.provider} API rate-limited / unavailable after retries")

    def json_call(self, system, user, image_png=None, model=None, max_tokens=4000):
        text = user + "\n\nReply with ONE JSON object only - no prose, no markdown fences."
        content = [{"type": "text", "text": text}]
        if image_png:
            uri = "data:image/png;base64," + base64.b64encode(image_png).decode()
            content.insert(0, {"type": "image_url", "image_url": {"url": uri}})
        payload = dict(model=model or (self.strong if image_png else self.fast), max_tokens=max_tokens, temperature=0,
                       messages=[{"role": "system", "content": system}, {"role": "user", "content": content}],
                       response_format={"type": "json_object"})
        out = self._post(payload)
        try:
            return _extract_json(out["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            raise LLMError("model did not return a result")


def make_client():
    """Pick the AI provider. PRG_AI_PROVIDER = auto (default) | anthropic | gemini | groq | openrouter | ollama.
    auto: Claude when ANTHROPIC_API_KEY is set, otherwise the first free provider that has a key (Gemini, Groq, OpenRouter),
    otherwise Ollama when OLLAMA_MODEL is set."""
    want = os.environ.get("PRG_AI_PROVIDER", "auto").strip().lower()
    if want in ("off", "none", "disabled"):
        return Claude(api_key="")           # not ready -> every AI step is skipped
    if want == "anthropic":
        return Claude()
    if want in PRESETS:
        return OpenAICompat(want)
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return Claude()
    for name in ("gemini", "groq", "openrouter"):
        if os.environ.get(PRESETS[name]["key_env"], "").strip():
            return OpenAICompat(name)
    if os.environ.get("OLLAMA_MODEL", "").strip():
        m = os.environ["OLLAMA_MODEL"].strip()
        return OpenAICompat("ollama", fast=m, strong=os.environ.get("OLLAMA_VISION_MODEL", m).strip())
    return Claude()
