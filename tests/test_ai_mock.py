"""Exercise the Groq code paths with a mocked HTTP layer (no network needed)."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PRG_DB"] = "/tmp/e2e.db"
import app as A
from core import llm, db, service
c = A.app.test_client()
calls = []
def fake_post(self, payload, retries=3):
    calls.append(payload)
    user = payload["messages"][1]["content"]
    if isinstance(user, list):   # vision
        body = {"channels": [{"plc_address": "I0.0", "tag": "XA-TEST", "description": "TEST PUMP RUN F/B", "terminal": "1,2"}]}
    elif "Classify" in user:
        body = {"items": [{"i": json.loads(user.split("Items:\n")[1])[0]["i"], "class": "Relay"}]}
    else:
        rows = json.loads(user.split("Channels:\n")[1])
        body = {"rows": [{"i": r["i"], "field_device": "FD " + r["desc"][:10], "signal_kind": "Run feedback", "suggested_tag": "XA-NEW" if not r["tag"] else ""} for r in rows]}
    return {"choices": [{"message": {"content": json.dumps(body)}}]}
llm.Groq._post = fake_post
c.post("/api/settings", json={"groq_api_key": "gsk_test1234"})
print("settings", c.get("/api/settings").json)
pid = db.q("SELECT id FROM projects", one=True)["id"]
print("enrich", c.post(f"/api/projects/{pid}/ai/enrich").json, "| groq calls:", len(calls))
print("sample prompt guardrail present:", "Never invent" in calls[0]["messages"][0]["content"] or "empty string" in calls[0]["messages"][0]["content"])
doc = db.q("SELECT id FROM documents WHERE filename LIKE '%SHAH%'", one=True)["id"]
before = db.q("SELECT COUNT(*) n FROM io_rows WHERE doc_id=? AND sheet='21'", (doc,), one=True)["n"]
print("reread", c.post(f"/api/docs/{doc}/sheet/21/reread", json={"module_type": "DI"}).json, "(rows before:", before, ")")
print("image sent to vision model:", isinstance(calls[-1]["messages"][1]["content"], list), calls[-1]["model"])
# learned rule is applied on re-ingest
service.ingest(doc)
r = db.q("SELECT description FROM io_rows WHERE doc_id=? AND description LIKE '%AERATION AIR VALVE%'", (doc,))
print("learned rule applied on re-ingest:", len(r) > 0, r[:1])
