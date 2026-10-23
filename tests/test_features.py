"""Feature tests on the sample drawings: terminal sheets, symbol recognition / wire tracing, coil-contact mapping, learning, raster vision path."""
import sys, os, json, time, tempfile
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp()
os.environ["PRG_DB"] = os.path.join(tmp, "f.db")
os.environ["PRG_HOME"] = tmp
os.environ["ANTHROPIC_API_KEY"] = ""; os.environ["PRG_AI_PROVIDER"] = "off"
from core import db, service, exporters, learning, llm, ai_assist, autoai
from core.pdfio import open_pdf, page_text
from core.terminal_detail import extract_terminal_detail, is_terminal_detail
from core.geometry import recognize

ASEE = os.path.join(ROOT, "Docs", "PRG-2526-637-ASEE-MARVEL-375KLD PLC+MCC.pdf")
db.init()

# 1. terminal-detail sheets -> terminals / cables / ferrules
with open_pdf(ASEE) as pdf:
    T, C, F = [], [], []
    for i, p in enumerate(pdf.pages, 1):
        if is_terminal_detail(page_text(p)):
            t, c, f = extract_terminal_detail(p, i); T += t; C += c; F += f
assert len(T) >= 90 and len(C) >= 30, (len(T), len(C))
c1 = next(c for c in C if c["cable_no"] == "W-1F-X0")
assert "RD/YE/BU" in c1["cores"] and "2.5" in c1["cores"] and "OIL SKIMMER" in c1["dest_equipment"], c1
print("terminal sheets OK:", len(T), "terminals", len(C), "cables", len(F), "ferrules")

# 2. symbols + traced wires on a vector schematic
with open_pdf(ASEE) as pdf:
    sy, ne, xr, coloured, ann = recognize(pdf.pages[15], 16)
roles = {(s["tag"], s["role"]) for s in sy}
assert ("1F-C1", "coil") in roles and ("1F-OLR", "overload contact") in roles and ("1F-ILSW", "lamp") in roles, roles
assert any(n["wire_no"] == "1F-R0" and any(e.startswith("1F-X0") for e in n["ends"]) for n in ne), ne[:5]
print("symbols OK:", len(sy), "symbols", len(ne), "nets")

# 3. full ingest -> coil/contact mapping with resolved printed references
pid = db.x("INSERT INTO projects(name,client,created) VALUES('t','',0)")
did = db.x("INSERT INTO documents(project_id,filename,path,status,created) VALUES(?,?,?,?,0)", (pid, "asee", ASEE, "queued"))
service.ingest(did)
D = exporters.datasets(pid)
row = next(r for r in D["Coil-Contact Mapping"][1] if r[0] == "1F-C1")
assert row[2] == "p16.5" and "p16.0" in row[3], row
assert D["Ferrule List"][1] and D["Terminal Schedule"][1] and D["Cable Schedule"][1]
assert any(r[8] == "terminal-sheet" for r in D["Ferrule List"][1]) and any(r[8] == "traced" for r in D["Ferrule List"][1])
print("coil/contact OK:", row)

# 4. learning: a spelling fix generalises to other cells and tag families
db.x("INSERT INTO io_rows(project_id,doc_id,description,tag,field_device,source) VALUES(?,?,?,?,?,?)", (pid, did, "AEARTION AIR VALVE", "X1", "", "vector"))
learning.record("io", "description", "AEARTION AIR VALVE", "AERATION AIR VALVE")
rows = [dict(description="AEARTION BLOWER B1 RUN", tag="X2", field_device="", signal_type="")]
assert learning.apply_rules("io", rows) >= 1 and rows[0]["description"] == "AERATION BLOWER B1 RUN", rows
learning.record("devices", "dev_class", "Other", "Selector Switch", {"tag": "7F-ILSW", "part_no": ""})
devs = [dict(tag="9F-ILSW", part_no="", make="", dev_class="Other", descr="")]
learning.apply_rules("devices", devs)
assert devs[0]["dev_class"] == "Selector Switch", devs
assert db.q("SELECT hits FROM rules WHERE field='token'", one=True)["hits"] >= 1
print("learning OK")

# 5. raster schematic through the Claude vision path (mocked)
import io as _io
with open_pdf(ASEE) as pdf:
    img = pdf.pages[15].to_image(resolution=100).original.convert("RGB")
scan = os.path.join(tmp, "scan.pdf")
img.save(scan, "PDF", resolution=100)


def fake_post(self, payload, retries=3):
    body = {"sheet_type": "SCHEMATIC", "title": "SCANNED POWER", "channels": [], "devices": [{"tag": "1F-C1", "type": "contactor", "pins": "A1,A2", "rating": ""}],
            "wires": [{"wire_no": "1F-R0", "from": "1F-OLR:2", "to": "1F-X0:1"}], "contacts": [{"tag": "1F-C1", "role": "coil", "contact_type": "", "pins": "A1,A2"}]}
    return {"content": [{"type": "tool_use", "name": "return_result", "input": body}]}


llm.Claude._post = fake_post
ai_assist._client = llm.Claude(api_key="sk-test")
pid2 = db.x("INSERT INTO projects(name,client,created) VALUES('scan','',0)")
did2 = db.x("INSERT INTO documents(project_id,filename,path,status,created) VALUES(?,?,?,?,0)", (pid2, "scan", scan, "queued"))
service.ingest(did2)
import core.pdfio as pio
if not pio.ocr_available():
    assert db.q("SELECT COUNT(*) n FROM devices WHERE doc_id=? AND source='ai-vision'", (did2,), one=True)["n"] == 1
    assert db.q("SELECT COUNT(*) n FROM wires WHERE doc_id=? AND source='ai-vision'", (did2,), one=True)["n"] == 1
    assert db.q("SELECT COUNT(*) n FROM xrefs WHERE doc_id=? AND direction='coil'", (did2,), one=True)["n"] == 1
    print("raster vision OK")
else:
    print("raster vision skipped (Tesseract present: OCR path used instead)")
print("ALL FEATURE TESTS OK")
