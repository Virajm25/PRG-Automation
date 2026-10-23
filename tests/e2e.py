import sys, os, time, io, zipfile, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["PRG_DB"] = "/tmp/e2e.db"
if os.path.exists("/tmp/e2e.db"): os.remove("/tmp/e2e.db")
import app as A
c = A.app.test_client()
B = "/home/claude/in/prg automation/"
pid = c.post("/api/projects", json={"name": "PRG-2526-616 Shah Eye Care"}).json["id"]
files = [("PRG-2526-616-AQUA CARE-SHAH EYE CARE-MCC+PLC PANEL.pdf"), ("PRG-2425-465-MOJJ ENGG MSD-697 PLC PANEL.pdf")]
data = {"files": [(open(B + f, "rb"), f) for f in files]}
print("upload", c.post(f"/api/projects/{pid}/upload", data=data, content_type="multipart/form-data").json)
t0 = time.time()
while time.time() - t0 < 240:
    d = c.get(f"/api/projects/{pid}/docs").json
    if all(x["status"] in ("done", "error") for x in d): break
    time.sleep(2)
for x in d: print(x["filename"][:40], x["status"], x["message"])
print("stats", c.get(f"/api/projects/{pid}/stats").json)
for k in ["io", "summary", "bom", "devices", "terminals", "wires", "xref"]:
    j = c.get(f"/api/projects/{pid}/table/{k}").json
    print(f"{k:9s} rows={j['total']:4d}", (j["rows"][0] if j["rows"] and not isinstance(j['rows'][0], dict) else {kk: v for kk, v in list(j['rows'][0].items())[:9]} if j["rows"] else ""))
# edit -> learning
row = c.get(f"/api/projects/{pid}/table/io?q=AEARTION").json["rows"][0]
print("edit", c.patch(f"/api/row/io/{row['id']}", json={"field": "description", "value": row["description"].replace("AEARTION", "AERATION")}).json)
print("rules", c.get("/api/learning").json["rules"][:2])
for f in ["xlsx", "pdf", "csv", "xml"]:
    r = c.get(f"/api/export/{pid}/{f}"); print(f, r.status_code, len(r.data))
open("/tmp/out.xlsx", "wb").write(c.get(f"/api/export/{pid}/xlsx").data)
r = c.get(f"/api/preview/{d[0]['id']}/21.png"); print("preview", r.status_code, len(r.data))
