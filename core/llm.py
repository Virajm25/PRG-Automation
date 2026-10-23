"""Groq client (OpenAI-compatible REST). No SDK dependency; works with requests only.

Model names are settings, not constants - Groq rotates its model catalogue. Use list_models() to discover
what your key can use and pick the text + vision models in Settings."""
import json, re, time, base64
import requests

BASE = "https://api.groq.com/openai/v1"
DEFAULT_TEXT = "openai/gpt-oss-120b"
DEFAULT_VISION = "qwen/qwen3.8-27b"


class LLMError(Exception):
    pass


class Groq:
    def __init__(self, api_key, text_model=DEFAULT_TEXT, vision_model=DEFAULT_VISION, timeout=90):
        self.key, self.text_model, self.vision_model, self.timeout = api_key, text_model or DEFAULT_TEXT, vision_model or DEFAULT_VISION, timeout

    @property
    def ready(self):
        return bool(self.key)

    def _pick_active_model(self):
        try:
            available = self.list_models()
            for m in available:
                if any(x in m.lower() for x in ["gpt-oss", "qwen", "llama"]) and not any(x in m.lower() for x in ["whisper", "guard"]):
                    return m
            return available[0] if available else DEFAULT_TEXT
        except Exception:
            return DEFAULT_TEXT

    def _post(self, payload, retries=3):
        h = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        for a in range(retries):
            try:
                r = requests.post(f"{BASE}/chat/completions", headers=h, json=payload, timeout=self.timeout)
            except requests.RequestException as e:
                if a == retries - 1:
                    raise LLMError(f"network error: {e}")
                time.sleep(2 ** a); continue
            if r.status_code == 404 or "model_not_found" in r.text:
                new_model = self._pick_active_model()
                if new_model != payload.get("model"):
                    payload["model"] = new_model
                    self.text_model = new_model
                    continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(2 ** (a + 1), 10)); continue
            if r.status_code != 200:
                raise LLMError(f"Groq HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        raise LLMError("Groq rate-limited / unavailable after retries")

    def chat_json(self, system, user, image_png=None, model=None, max_tokens=3000):
        if image_png:
            b64 = base64.b64encode(image_png).decode()
            content = [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]
            model = model or self.vision_model
        else:
            content = user
            model = model or self.text_model
        payload = dict(model=model, temperature=0, max_tokens=max_tokens, response_format={"type": "json_object"},
                       messages=[{"role": "system", "content": system}, {"role": "user", "content": content}])
        out = self._post(payload)
        txt = out["choices"][0]["message"]["content"]
        try:
            return json.loads(txt)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", txt, re.S)
            if m:
                return json.loads(m.group(0))
            raise LLMError("model did not return valid JSON")

    def list_models(self):
        r = requests.get(f"{BASE}/models", headers={"Authorization": f"Bearer {self.key}"}, timeout=30)
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        return sorted(m["id"] for m in r.json().get("data", []))

