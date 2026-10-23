"""Free-provider client: request shape, JSON extraction, provider selection (no network)."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_MODEL", "PRG_AI_PROVIDER"):
    os.environ.pop(k, None)
from core import llm

assert not llm.make_client().ready                                   # nothing configured -> AI off
os.environ["GROQ_API_KEY"] = "g"; assert llm.make_client().provider == "groq"
os.environ["GEMINI_API_KEY"] = "k"; c = llm.make_client(); assert c.provider == "gemini" and c.ready
os.environ["ANTHROPIC_API_KEY"] = "a"; assert llm.make_client().provider == "anthropic"
os.environ["PRG_AI_PROVIDER"] = "groq"; assert llm.make_client().provider == "groq"
os.environ.pop("PRG_AI_PROVIDER"); os.environ.pop("ANTHROPIC_API_KEY")
os.environ.pop("GEMINI_API_KEY"); os.environ.pop("GROQ_API_KEY")
os.environ["OLLAMA_MODEL"] = "qwen2.5:7b"; o = llm.make_client(); assert o.provider == "ollama" and o.ready

seen = []
def fake(self, payload, retries=5):
    seen.append(payload)
    return {"choices": [{"message": {"content": 'Sure!\n```json\n{"rows": [{"i": 1}]}\n```'}}]}
llm.OpenAICompat._post = fake
c = llm.OpenAICompat("gemini", api_key="k")
assert c.json_call("sys", "hi") == {"rows": [{"i": 1}]}
assert seen[-1]["model"] == c.fast and isinstance(seen[-1]["messages"][1]["content"], list)
c.json_call("sys", "look", image_png=b"\x89PNG")
img = seen[-1]["messages"][1]["content"][0]
assert seen[-1]["model"] == c.strong and img["type"] == "image_url" and img["image_url"]["url"].startswith("data:image/png;base64,")
try:
    llm._extract_json("no json here"); raise SystemExit("should fail")
except llm.LLMError:
    pass
print("PROVIDERS OK")
