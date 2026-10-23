"""Exercise the automatic Claude AI pass with a mocked HTTP layer (no network, no real key).
Checks: AI runs by default after extraction when ANTHROPIC_API_KEY is set, cheap tier for text / strong tier for images,
and nothing breaks (or is sent) when the key is absent."""
import sys, os, json, time, tempfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp()
os.environ["PRG_DB"] = os.path.join(tmp, "ai.db")
os.environ["PRG_HOME"] = tmp
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"; os.environ["PRG_AI_PROVIDER"] = "anthropic"
import app as A
from core import llm, db

calls = []


def fake_post(self, payload, retries=3):
    calls.append(payload)
    content = payload["messages"][0]["content"]
    user = next(b["text"] for b in content if b["type"] == "text")
    if any(b["type"] == "image" for b in content):
        body = {"sheet_type": "DI", "title": "T", "channels": [{"plc_address": "I0.0", "tag": "XA-TEST", "description": "TEST PUMP RUN F/B", "terminal": "1,2"}]}
    elif "Classify each" in user:
        body = {"items": [{"i": i["i"], "class": "Relay"} for i in json.loads(user.split("Items:\n")[1])]}
    elif "field_device" in user:
        rows = json.loads(user.split("Channels:\n")[1])
        body = {"rows": [{"i": r["i"], "field_device": "FD " + r["desc"][:10], "signal_kind": "Run feedback", "suggested_tag": "XA-NEW" if not r["tag"] else ""} for r in rows]}
    else:
        body = {"specs": []}
    return {"content": [{"type": "tool_use", "name": "return_result", "input": body}]}


llm.Claude._post = fake_post
c = A.app.test_client()
st = c.get("/api/ai/status").json
print("status", st)
assert st["enabled"] and "haiku" in st["fast_model"] and "sonnet" in st["vision_model"]
pid = c.post("/api/projects", json={"name": "AI"}).json["id"]
f = "PRG-2526-616-AQUA CARE-SHAH EYE CARE-MCC+PLC PANEL.pdf"
c.post(f"/api/projects/{pid}/upload", data={"files": [(open(os.path.join(ROOT, "Docs", f), "rb"), f)]}, content_type="multipart/form-data")
t0 = time.time()
while time.time() - t0 < 200:
    d = c.get(f"/api/projects/{pid}/docs").json
    if d and d[0]["status"] in ("done", "error"):
        break
    time.sleep(1)
print(d[0]["status"], d[0]["message"])
assert d[0]["status"] == "done" and "AI filled" in d[0]["message"], "AI pass did not run by default"
models = {p["model"] for p in calls}
print("models used:", models, "| calls:", len(calls))
assert all(("sonnet" in p["model"]) == any(b["type"] == "image" for b in p["messages"][0]["content"]) for p in calls), "image calls must use the strong tier only"
assert not any("opus" in m for m in models)
doc = db.q("SELECT id FROM documents", one=True)["id"]
r = c.post(f"/api/docs/{doc}/sheet/21/reread", json={"module_type": "DI"}).json
print("reread", r)
assert r.get("channels") == 1
# without a key: no calls, extraction still completes
calls.clear()
llm.Claude.__init__.__defaults__  # noqa
from core import ai_assist
ai_assist._client = llm.Claude(api_key="")
from core import autoai
assert autoai.run(doc) == "" and not calls
print("AI MOCK OK")
