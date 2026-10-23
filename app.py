"""MS-PRG Drawing Data Extractor - MVP.  Local web app (runs fully on the user's PC; PDFs never leave it,
except the optional Groq calls which send only extracted text / sheet images when the user triggers AI actions)."""
import os, re, io, json, time, shutil
from flask import Flask, request, jsonify, send_file, render_template, abort
from werkzeug.utils import secure_filename
from core import db, learning, exporters, service
from core.pdfio import open_pdf
from core.llm import LLMError, Groq

ROOT = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join("/tmp", "prg-uploads") if os.environ.get("VERCEL") else os.path.join(ROOT, "data", "uploads")
CACHE = os.path.join("/tmp", "prg-cache") if os.environ.get("VERCEL") else os.path.join(ROOT, "data", "cache")
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response
app.config["MAX_CONTENT_LENGTH"] = 600 * 1024 * 1024
db.init()
EDITABLE = {"io": "io_rows", "circuits": "circuits", "devices": "devices", "wires": "wires", "terminals": "terminals", "cables": "cables", "panel_specs": "panel_specs"}


def J(x, code=200):
    r = jsonify(x); r.status_code = code; return r


@app.get("/favicon.ico")
def favicon():
    return ("", 204)

@app.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------------------ projects & documents
@app.get("/api/projects")
def projects():
    ps = db.q("SELECT * FROM projects ORDER BY id DESC")
    for p in ps:
        p["docs"] = db.q("SELECT COUNT(*) n FROM documents WHERE project_id=?", (p["id"],), one=True)["n"]
        p["io"] = db.q("SELECT COUNT(*) n FROM io_rows WHERE project_id=?", (p["id"],), one=True)["n"]
    return J(ps)


@app.post("/api/projects")
def new_project():
    d = request.json or {}
    name = (d.get("name") or "").strip()
    if not name:
        return J({"error": "name required"}, 400)
    pid = db.x("INSERT INTO projects(name,client,created) VALUES(?,?,?)", (name, d.get("client", ""), time.time()))
    return J({"id": pid})


@app.delete("/api/projects/<int:pid>")
def del_project(pid):
    for t in ("io_rows", "devices", "wires", "terminals", "cables", "xrefs", "sheets", "modules", "documents"):
        db.x(f"DELETE FROM {t} WHERE project_id=?", (pid,))
    db.x("DELETE FROM projects WHERE id=?", (pid,))
    shutil.rmtree(os.path.join(UP, str(pid)), ignore_errors=True)
    return J({"ok": True})


@app.post("/api/projects/<int:pid>/upload")
def upload(pid):
    files = request.files.getlist("files")
    if not files:
        return J({"error": "no files"}, 400)
    d = os.path.join(UP, str(pid)); os.makedirs(d, exist_ok=True)
    ids = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            continue
        fn = secure_filename(f.filename) or f"drawing_{int(time.time())}.pdf"
        path = os.path.join(d, f"{int(time.time()*1000)}_{fn}")
        f.save(path)
        did = db.x("INSERT INTO documents(project_id,filename,path,status,created) VALUES(?,?,?,?,?)",
                   (pid, f.filename, path, "queued", time.time()))
        ids.append(did)
        service.ingest_async(did)
    return J({"docs": ids})


@app.get("/api/projects/<int:pid>/docs")
def docs(pid):
    return J(db.q("SELECT id,filename,pages,status,progress,message,panel FROM documents WHERE project_id=? ORDER BY id DESC", (pid,)))


@app.delete("/api/docs/<int:did>")
def del_doc(did):
    for t in ("io_rows", "devices", "wires", "terminals", "xrefs", "sheets", "modules"):
        db.x(f"DELETE FROM {t} WHERE doc_id=?", (did,))
    d = db.q("SELECT path FROM documents WHERE id=?", (did,), one=True)
    if d:
        try: os.remove(d["path"])
        except OSError: pass
    db.x("DELETE FROM documents WHERE id=?", (did,))
    return J({"ok": True})


# ------------------------------------------------------------------ tables
@app.get("/api/projects/<int:pid>/table/<key>")
def table(pid, key):
    q = (request.args.get("q") or "").strip().lower()
    flt = request.args.get("filter") or ""
    if key in EDITABLE:
        tbl = EDITABLE[key]
        cols = ["id"] + db.TABLES[key][1]
        order = {"io": "doc_id, CAST(rack AS INT), CAST(slot AS INT), CAST(channel AS INT)",
                 "circuits": "CAST(sheet AS INT), id",
                 "devices": "dev_class, tag",
                 "wires": "CAST(sheet AS INT), wire_no", "terminals": "strip, CAST(terminal_no AS INT)", "cables": "id",
                 "panel_specs": "CAST(sheet AS INT), id"}[key]
        rows = db.q(f"SELECT {','.join(cols)}, doc_id FROM {tbl} WHERE project_id=? ORDER BY {order}", (pid,))
        fkey = {"io": "module_type", "circuits": "starter_type", "devices": "dev_class", "terminals": "spare", "wires": "sheet", "panel_specs": "category"}.get(key)
        opts = sorted({r[fkey] for r in rows if fkey and r[fkey]}) if fkey else []
        if flt and fkey:
            rows = [r for r in rows if r[fkey] == flt]
        if q:
            rows = [r for r in rows if q in " ".join(str(v) for v in r.values()).lower()]
        return J(dict(cols=db.TABLES[key][1], rows=rows[:5000], total=len(rows), editable=True, filter_options=opts, filter_label=fkey))
    ds = exporters.datasets(pid)
    name = {"bom": "BOM", "summary": "I-O Summary", "xref": "Cross Reference"}.get(key)
    if not name:
        abort(404)
    cols, rows = ds[name]
    rows = [r for r in rows if not q or q in " ".join(str(v) for v in r).lower()]
    return J(dict(cols=cols, rows=rows[:5000], total=len(rows), editable=False, filter_options=[], filter_label=None))


@app.get("/api/projects/<int:pid>/sheets")
def sheets(pid):
    return J(db.q("SELECT s.*, d.filename FROM sheets s JOIN documents d ON d.id=s.doc_id WHERE s.project_id=? ORDER BY s.doc_id, s.sheet", (pid,)))


@app.get("/api/projects/<int:pid>/stats")
def stats(pid):
    g = lambda sql: db.q(sql, (pid,), one=True)["n"]
    return J(dict(io=g("SELECT COUNT(*) n FROM io_rows WHERE project_id=?"),
                  circuits=g("SELECT COUNT(*) n FROM circuits WHERE project_id=?"),
                  specs=g("SELECT COUNT(*) n FROM panel_specs WHERE project_id=?"),
                  spare=g("SELECT COUNT(*) n FROM io_rows WHERE project_id=? AND spare='1'"),
                  low=g("SELECT COUNT(*) n FROM io_rows WHERE project_id=? AND CAST(conf AS REAL)<0.7"),
                  devices=g("SELECT COUNT(*) n FROM devices WHERE project_id=?"),
                  parts=g("SELECT COUNT(*) n FROM devices WHERE project_id=? AND part_no<>''"),
                  wires=g("SELECT COUNT(*) n FROM wires WHERE project_id=?"),
                  corrections=db.q("SELECT COUNT(*) n FROM corrections", one=True)["n"]))


@app.patch("/api/row/<key>/<int:rid>")
def edit_row(key, rid):
    if key not in EDITABLE:
        abort(404)
    d = request.json; field, value = d["field"], d["value"]
    if field not in db.TABLES[key][1]:
        return J({"error": "bad field"}, 400)
    tbl = EDITABLE[key]
    old = db.q(f"SELECT * FROM {tbl} WHERE id=?", (rid,), one=True)
    if not old:
        abort(404)
    db.x(f"UPDATE {tbl} SET {field}=? WHERE id=?", (value, rid))
    if key == "io":
        db.x("UPDATE io_rows SET source='edited', conf='1.0' WHERE id=?", (rid,))
    ctx = {"tag": old.get("tag", ""), "part_no": old.get("part_no", ""), "module_type": old.get("module_type", "")}
    if key in ("io", "devices"):
        learning.record(key, field, old.get(field, ""), value, ctx)
    return J({"ok": True})


@app.post("/api/row/<key>")
def add_row(key):
    if key not in EDITABLE:
        abort(404)
    d = request.json; pid = d["project_id"]
    doc = db.q("SELECT id FROM documents WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,), one=True)
    cols = db.TABLES[key][1]
    vals = {c: str(d.get("values", {}).get(c, "")) for c in cols}
    if key == "io":
        vals.update(source="manual", conf="1.0")
    rid = db.x(f"INSERT INTO {EDITABLE[key]}(project_id,doc_id,{','.join(cols)}) VALUES(?,?,{','.join('?'*len(cols))})",
               (pid, doc["id"] if doc else 0, *[vals[c] for c in cols]))
    return J({"id": rid})


@app.delete("/api/row/<key>/<int:rid>")
def del_row(key, rid):
    if key not in EDITABLE:
        abort(404)
    db.x(f"DELETE FROM {EDITABLE[key]} WHERE id=?", (rid,))
    return J({"ok": True})


# ------------------------------------------------------------------ preview
@app.get("/api/preview/<int:did>/<int:sheet>.png")
def preview(did, sheet):
    doc = db.q("SELECT path FROM documents WHERE id=?", (did,), one=True)
    if not doc:
        abort(404)
    os.makedirs(CACHE, exist_ok=True)
    out = os.path.join(CACHE, f"{did}_{sheet}.png")
    if not os.path.exists(out):
        with open_pdf(doc["path"]) as pdf:
            if sheet < 1 or sheet > len(pdf.pages):
                abort(404)
            pdf.pages[sheet - 1].to_image(resolution=110).save(out, format="PNG")
    return send_file(out, mimetype="image/png")


# ------------------------------------------------------------------ export
@app.get("/api/export/<int:pid>/<fmt>")
def export(pid, fmt):
    p = db.q("SELECT name FROM projects WHERE id=?", (pid,), one=True) or {"name": "project"}
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", p["name"])
    tbl = request.args.get("table")
    if fmt == "xlsx":
        return send_file(io.BytesIO(exporters.to_xlsx(pid, p["name"])), as_attachment=True, download_name=f"{base}_engineering_data.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "csv":
        if tbl:
            return send_file(io.BytesIO(exporters.to_csv(pid, tbl)), as_attachment=True, download_name=f"{base}_{re.sub(r'[^A-Za-z0-9]+','_',tbl)}.csv", mimetype="text/csv")
        return send_file(io.BytesIO(exporters.to_csv_zip(pid)), as_attachment=True, download_name=f"{base}_csv.zip", mimetype="application/zip")
    if fmt == "pdf":
        return send_file(io.BytesIO(exporters.to_pdf(pid, p["name"])), as_attachment=True, download_name=f"{base}_engineering_data.pdf", mimetype="application/pdf")
    if fmt == "xml":
        return send_file(io.BytesIO(exporters.to_xml(pid, p["name"])), as_attachment=True, download_name=f"{base}_engineering_data.xml", mimetype="application/xml")
    abort(404)


# ------------------------------------------------------------------ settings & AI
@app.get("/api/settings")
def get_settings():
    k = db.get_setting("groq_api_key")
    return J(dict(groq_key_set=bool(k), groq_key_hint=("â€¦" + k[-4:]) if k else "", text_model=db.get_setting("groq_text_model", "openai/gpt-oss-120b"),
                  vision_model=db.get_setting("groq_vision_model", "qwen/qwen3.8-27b")))


@app.post("/api/settings")
def set_settings():
    d = request.json or {}
    for k_in, k_db in (("groq_api_key", "groq_api_key"), ("text_model", "groq_text_model"), ("vision_model", "groq_vision_model")):
        if d.get(k_in):
            db.set_setting(k_db, d[k_in].strip())
    return J({"ok": True})


@app.get("/api/llm/models")
def llm_models():
    from core.ai_assist import get_client
    try:
        return J(get_client().list_models())
    except (LLMError, Exception) as e:  # noqa
        return J({"error": str(e)}, 400)


@app.post("/api/projects/<int:pid>/ai/enrich")
def ai_enrich(pid):
    from core import ai_assist
    try:
        client = ai_assist.get_client()
        io_rows = db.q("SELECT id,module_type,tag,description,field_device,spare,remarks FROM io_rows WHERE project_id=? AND source!='edited'", (pid,))
        res = ai_assist.enrich_io(io_rows, client)
        n = 0
        for r in io_rows:
            a = res.get(r["id"])
            if not a:
                continue
            if not r["field_device"] and a.get("field_device"):
                db.x("UPDATE io_rows SET field_device=? WHERE id=?", (a["field_device"], r["id"])); n += 1
            if not r["tag"] and a.get("suggested_tag"):
                db.x("UPDATE io_rows SET tag=?, remarks=? WHERE id=?",
                     (a["suggested_tag"], (r["remarks"] + "; " if r["remarks"] else "") + "tag suggested by AI - verify", r["id"])); n += 1
            if a.get("signal_kind"):
                db.x("UPDATE io_rows SET remarks=? WHERE id=? AND remarks NOT LIKE ?",
                     ((r["remarks"] + "; " if r["remarks"] else "") + a["signal_kind"], r["id"], "%" + a["signal_kind"] + "%"))
        devs = db.q("SELECT id,tag,part_no,make,descr FROM devices WHERE project_id=? AND dev_class='Other'", (pid,))
        m = 0
        if devs:
            for did, cls in ai_assist.classify_devices(devs, client).items():
                db.x("UPDATE devices SET dev_class=? WHERE id=?", (cls, did)); m += 1
        return J({"ok": True, "io_updates": n, "devices_classified": m})
    except LLMError as e:
        return J({"error": str(e)}, 400)


@app.post("/api/docs/<int:did>/sheet/<int:sheet>/reread")
def ai_reread(did, sheet):
    """Vision re-read of one sheet; replaces that sheet's I/O rows (flagged source=ai-vision, needs review)."""
    from core import ai_assist
    from core.pdfio import page_png
    doc = db.q("SELECT * FROM documents WHERE id=?", (did,), one=True)
    mtype = (request.json or {}).get("module_type") or "DI"
    try:
        with open_pdf(doc["path"]) as pdf:
            png = page_png(pdf.pages[sheet - 1], 130)
        chans = ai_assist.reread_sheet(png, mtype)
        old = db.q("SELECT * FROM io_rows WHERE doc_id=? AND sheet=?", (did, str(sheet)))
        tpl = old[0] if old else dict(rack="1", slot="1", module_name="", module_model="", signal_type="")
        db.x("DELETE FROM io_rows WHERE doc_id=? AND sheet=?", (did, str(sheet)))
        rows = [dict(rack=tpl["rack"], slot=tpl["slot"], module_name=tpl["module_name"], module_type=mtype, module_model=tpl["module_model"],
                     plc_address=c.get("plc_address", ""), channel=i, tag=c.get("tag", ""), description=c.get("description", ""),
                     signal_type=tpl["signal_type"], field_device="", cable_no="", terminal=c.get("terminal", ""),
                     remarks="re-read by AI vision - verify", spare=str(c.get("description", "")).upper() == "SPARE", sheet=sheet, conf=0.6,
                     source="ai-vision") for i, c in enumerate(chans)]
        db.insert_many("io", doc["project_id"], did, rows)
        return J({"ok": True, "channels": len(rows)})
    except LLMError as e:
        return J({"error": str(e)}, 400)


@app.post("/api/projects/<int:pid>/extract-ga")
def extract_project_ga(pid):
    """Extract GA panel details and specs for all GA sheets in a project."""
    from core.ga_extract import extract_panel_specs_from_text, extract_with_llm
    from core.pdfio import open_pdf, page_text
    from core.ai_assist import get_client
    use_ai = (request.json or {}).get("use_ai", False)
    client = None
    if use_ai:
        try: client = get_client()
        except Exception: pass
    
    docs = db.q("SELECT id, path FROM documents WHERE project_id=?", (pid,))
    total_extracted = 0
    for d in docs:
        if not os.path.exists(d["path"]):
            continue
        sheets = db.q("SELECT sheet, draw_type FROM sheets WHERE doc_id=? ORDER BY sheet", (d["id"],))
        db.x("DELETE FROM panel_specs WHERE doc_id=?", (d["id"],))
        specs_to_insert = []
        with open_pdf(d["path"]) as pdf:
            for s in sheets:
                s_num = s["sheet"]
                if s_num < 1 or s_num > len(pdf.pages):
                    continue
                dtype = s["draw_type"]
                txt = page_text(pdf.pages[s_num - 1])
                if dtype in ("external_ga", "internal_ga", "general_specs") or "PANEL DETAILS" in txt.upper() or "DIMENSIONS IN MM" in txt.upper():
                    sheet_specs = extract_panel_specs_from_text(txt, sheet_num=s_num, draw_type=dtype)
                    if not sheet_specs and client and client.ready:
                        sheet_specs = extract_with_llm(txt, sheet_num=s_num, draw_type=dtype, client=client)
                    if sheet_specs:
                        specs_to_insert.extend(sheet_specs)
        if specs_to_insert:
            db.insert_many("panel_specs", pid, d["id"], specs_to_insert)
            total_extracted += len(specs_to_insert)
    return J({"ok": True, "extracted_specs": total_extracted})


@app.get("/api/learning")
def learning_info():
    return J(dict(rules=db.q("SELECT entity,field,original,corrected,hits FROM rules ORDER BY id DESC LIMIT 100"),
                  corrections=db.q("SELECT COUNT(*) n FROM corrections", one=True)["n"]))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5057)), debug=False, threaded=True)





