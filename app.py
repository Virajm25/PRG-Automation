"""MS-PRG Drawing Data Extractor - MVP.  Local web app (runs fully on the user's PC; PDFs never leave it,
except the Claude API calls (enabled when ANTHROPIC_API_KEY is set) which send only extracted text or the sheet image being read)."""
import os, re, io, json, time, shutil
from flask import Flask, request, jsonify, send_file, render_template, abort
from werkzeug.utils import secure_filename
from core import db, learning, exporters, service
from core.pdfio import open_pdf
from core.llm import LLMError

ROOT = os.path.dirname(os.path.abspath(__file__))
from core.paths import UPLOADS as UP, CACHE, CODE_ROOT
app = Flask(__name__, template_folder=os.path.join(CODE_ROOT, "templates"))
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

@app.after_request
def no_cache(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response
app.config["MAX_CONTENT_LENGTH"] = 600 * 1024 * 1024


class _RestoreRewrittenPath:
    """vercel.json rewrites every URL to /api/index?__path=<original path>; Vercel's Python runtime hands the app the rewritten
    path, so put the original one back before routing. Harmless elsewhere (the parameter is never present locally)."""
    def __init__(self, wsgi):
        self.wsgi = wsgi

    def __call__(self, environ, start_response):
        from urllib.parse import parse_qsl, urlencode
        qs = environ.get("QUERY_STRING", "")
        if "__path=" in qs:
            query = parse_qsl(qs, keep_blank_values=True)
            orig = next((v for k, v in query if k == "__path"), "")
            environ["PATH_INFO"] = "/" + orig.lstrip("/")
            environ["QUERY_STRING"] = urlencode([(k, v) for k, v in query if k != "__path"])
        return self.wsgi(environ, start_response)


app.wsgi_app = _RestoreRewrittenPath(app.wsgi_app)
db.init()
EDITABLE = {"io": "io_rows", "circuits": "circuits", "devices": "devices", "wires": "wires", "terminals": "terminals", "cables": "cables", "panel_specs": "panel_specs", "bom_lines": "bom_items"}


DATASET_KEYS = {"bom": "BOM", "summary": "I-O Summary", "xref": "Device Cross Reference", "contacts": "Contact Cross Reference",
                "coils": "Coil-Contact Mapping", "signals": "Signal Links", "symbols": "Symbols & Pins", "annotations": "Sheet Annotations", "symbols": "Symbols & Pins", "annotations": "Sheet Annotations", "sheetrefs": "Sheet References", "devsummary": "Device Summary", "blocks": "Terminal Blocks"}


SERVERLESS = bool(os.environ.get("VERCEL"))


def run_ingest(doc_id):
    """Desktop: extract in a background thread (the UI polls progress). Serverless (Vercel): the instance is frozen once the response
    is sent, so a background thread would never finish - extract inside the request instead (bounded by the function's maxDuration)."""
    if SERVERLESS:
        service.ingest(doc_id)
    else:
        service.ingest_async(doc_id)


def J(x, code=200):
    r = jsonify(x); r.status_code = code; return r


@app.get("/favicon.ico")
def favicon():
    return ("", 204)

@app.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------------------ projects & documents
@app.get("/api/health")
def health():
    from core.mongo_db import ping
    from core.pdfio import ocr_available
    from core.paths import HOME
    return J({"sqlite": True, "mongodb": ping(), "ocr": ocr_available(), "vercel": bool(os.environ.get("VERCEL")), "data_folder": HOME})

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
    for t in (*db.DOC_TABLES, "documents"):
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
        run_ingest(did)
    return J({"docs": ids})


@app.post("/api/projects/<int:pid>/reprocess")
def reprocess(pid):
    """Re-run extraction on every stored drawing (picks up learned rules / newer extractors). Manual edits on those rows are replaced."""
    ids = [d["id"] for d in db.q("SELECT id FROM documents WHERE project_id=?", (pid,))]
    for i in ids:
        run_ingest(i)
    return J({"docs": ids})


@app.get("/api/projects/<int:pid>/docs")
def docs(pid):
    return J(db.q("SELECT id,filename,pages,status,progress,message,panel FROM documents WHERE project_id=? ORDER BY id DESC", (pid,)))


@app.delete("/api/docs/<int:did>")
def del_doc(did):
    for t in db.DOC_TABLES:
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
        cols = ["id"] + db.TABLES[key if key != "bom_lines" else "bom"][1]
        order = {"io": "doc_id, CAST(rack AS INT), CAST(slot AS INT), CAST(channel AS INT)",
                 "circuits": "CAST(sheet AS INT), id",
                 "devices": "dev_class, tag",
                 "wires": "CAST(sheet AS INT), wire_no", "terminals": "strip, CAST(terminal_no AS INT)", "cables": "id",
                 "panel_specs": "CAST(sheet AS INT), id", "bom_lines": "CAST(sheet AS INT), CAST(item_no AS INT)"}[key]
        rows = db.q(f"SELECT {','.join(cols)}, doc_id FROM {tbl} WHERE project_id=? ORDER BY {order}", (pid,))
        fkey = {"io": "module_type", "circuits": "starter_type", "devices": "dev_class", "terminals": "spare", "wires": "source", "panel_specs": "category", "cables": "source", "bom_lines": "panel"}.get(key)
        opts = sorted({r[fkey] for r in rows if fkey and r[fkey]}) if fkey else []
        if flt and fkey:
            rows = [r for r in rows if r[fkey] == flt]
        if q:
            rows = [r for r in rows if q in " ".join(str(v) for v in r.values()).lower()]
        for r in rows:
            r["_review"] = needs_review(r)
        if request.args.get("review") == "1":
            rows = [r for r in rows if r["_review"]]
        return J(dict(cols=db.TABLES[key if key != "bom_lines" else "bom"][1], rows=rows[:5000], total=len(rows), editable=True, filter_options=opts, filter_label=fkey))
    ds = exporters.datasets(pid)
    name = DATASET_KEYS.get(key)
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
                  cables=g("SELECT COUNT(*) n FROM cables WHERE project_id=?"),
                  bom=g("SELECT COUNT(*) n FROM bom_items WHERE project_id=?"),
                  corrections=db.q("SELECT COUNT(*) n FROM corrections", one=True)["n"]))


@app.patch("/api/row/<key>/<int:rid>")
def edit_row(key, rid):
    if key not in EDITABLE:
        abort(404)
    d = request.json; field, value = d["field"], d["value"]
    if field not in db.TABLES[key if key != "bom_lines" else "bom"][1]:
        return J({"error": "bad field"}, 400)
    tbl = EDITABLE[key]
    old = db.q(f"SELECT * FROM {tbl} WHERE id=?", (rid,), one=True)
    if not old:
        abort(404)
    db.x(f"UPDATE {tbl} SET {field}=? WHERE id=?", (value, rid))
    if key == "io":
        db.x("UPDATE io_rows SET source='edited', conf='1.0' WHERE id=?", (rid,))
    elif key in ("cables", "wires", "bom_lines", "devices"):
        db.x(f"UPDATE {tbl} SET source='edited' WHERE id=?", (rid,))
    ctx = {"tag": old.get("tag", ""), "part_no": old.get("part_no", ""), "module_type": old.get("module_type", "")}
    learning.record(key, field, old.get(field, ""), value, ctx)
    return J({"ok": True})


@app.post("/api/row/<key>")
def add_row(key):
    if key not in EDITABLE:
        abort(404)
    d = request.json; pid = d["project_id"]
    doc = db.q("SELECT id FROM documents WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,), one=True)
    cols = db.TABLES[key if key != "bom_lines" else "bom"][1]
    vals = {c: str(d.get("values", {}).get(c, "")) for c in cols}
    if key == "io":
        vals.update(source="manual", conf="1.0")
    elif "source" in vals:
        vals["source"] = "manual"
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
    """?tables=Name|Name (dataset names, '|'-separated; ?table= kept for one table). No selection = every document."""
    p = db.q("SELECT name FROM projects WHERE id=?", (pid,), one=True) or {"name": "project"}
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", p["name"])
    only = [t for t in (request.args.get("tables") or request.args.get("table") or "").split("|") if t]
    suffix = re.sub(r"[^A-Za-z0-9]+", "_", only[0]) if len(only) == 1 else "engineering_data"
    if fmt == "xlsx":
        return send_file(io.BytesIO(exporters.to_xlsx(pid, p["name"], only)), as_attachment=True, download_name=f"{base}_{suffix}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if fmt == "csv":
        if len(only) == 1:
            return send_file(io.BytesIO(exporters.to_csv(pid, only[0])), as_attachment=True, download_name=f"{base}_{suffix}.csv", mimetype="text/csv")
        return send_file(io.BytesIO(exporters.to_csv_zip(pid, only)), as_attachment=True, download_name=f"{base}_{suffix}_csv.zip", mimetype="application/zip")
    if fmt == "pdf":
        return send_file(io.BytesIO(exporters.to_pdf(pid, p["name"], only)), as_attachment=True, download_name=f"{base}_{suffix}.pdf", mimetype="application/pdf")
    if fmt == "xml":
        return send_file(io.BytesIO(exporters.to_xml(pid, p["name"], only)), as_attachment=True, download_name=f"{base}_{suffix}.xml", mimetype="application/xml")
    abort(404)


@app.get("/api/env")
def env_info():
    """Fast status for the UI status bar (no network calls)."""
    from core.pdfio import ocr_available
    from core.paths import HOME
    return J(dict(vercel=bool(os.environ.get("VERCEL")), ocr=ocr_available(), data_folder=HOME))


@app.get("/api/projects/<int:pid>/datasets")
def dataset_list(pid):
    return J([dict(name=k, rows=len(v[1])) for k, v in exporters.datasets(pid).items()])


REVIEW_SOURCES = {"derived", "derived-io", "ai-vision", "terminal-sheet"}


def needs_review(r):
    """Rows a person should check before export: low confidence, AI-read / derived values, or remarks asking to verify."""
    try:
        if r.get("conf") not in (None, "") and float(r["conf"]) < 0.7:
            return True
    except ValueError:
        pass
    return r.get("source") in REVIEW_SOURCES or "verify" in str(r.get("remarks", "")).lower() or r.get("wire_no") == "(unnumbered)"


@app.get("/api/projects/<int:pid>/overview")
def overview(pid):
    g = lambda sql: db.q(sql, (pid,), one=True)["n"]
    rev = {}
    for key in ("io", "wires", "cables", "devices", "bom_lines", "terminals"):
        rows = db.q(f"SELECT * FROM {EDITABLE[key]} WHERE project_id=?", (pid,))
        rev[key] = [len(rows), sum(1 for r in rows if needs_review(r))]
    docs_ = db.q("SELECT id,filename,pages,status,progress,message FROM documents WHERE project_id=? ORDER BY id", (pid,))
    types = db.q("SELECT draw_type_label t, COUNT(*) n FROM sheets WHERE project_id=? GROUP BY draw_type_label ORDER BY n DESC", (pid,))
    rev["bom"] = [len(exporters.bom_dataset(pid)[1]), rev["bom_lines"][1]]
    return J(dict(tables=rev, docs=docs_, sheet_types=types,
                  sheets=g("SELECT COUNT(*) n FROM sheets WHERE project_id=?"), scanned=g("SELECT COUNT(*) n FROM sheets WHERE project_id=? AND scanned=1"),
                  specs=g("SELECT COUNT(*) n FROM panel_specs WHERE project_id=?"), circuits=g("SELECT COUNT(*) n FROM circuits WHERE project_id=?"),
                  xrefs=g("SELECT COUNT(*) n FROM xrefs WHERE project_id=? AND direction IN ('coil','contact')"),
                  spare=g("SELECT COUNT(*) n FROM io_rows WHERE project_id=? AND spare='1'"),
                  corrections=db.q("SELECT COUNT(*) n FROM corrections", one=True)["n"]))


# ------------------------------------------------------------------ AI (Claude; key from environment / .env only)
@app.get("/api/ai/status")
def ai_status():
    from core.ai_assist import get_client
    c = get_client()
    return J(dict(enabled=c.ready, provider=c.provider, fast_model=c.fast, vision_model=c.strong,
                  hint="" if c.ready else "Set ANTHROPIC_API_KEY (or a free GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY, or OLLAMA_MODEL) in the .env file and restart to enable AI."))


@app.post("/api/projects/<int:pid>/ai/run")
def ai_run(pid):
    """Start the AI pass on every document of the project. Desktop: background job, poll /ai/job for progress.
    Serverless: runs inside the request (no background threads there)."""
    from core import autoai
    if not autoai.ai_assist.ai_enabled():
        return J({"error": "AI is off: set an AI provider key in .env"}, 400)
    ids = [d["id"] for d in db.q("SELECT id FROM documents WHERE project_id=? AND status='done'", (pid,))]
    if not ids:
        return J({"error": "no processed drawings in this project yet"}, 400)
    if SERVERLESS:
        notes = [autoai.run(i) for i in ids]
        return J(dict(state="finished", done=len(ids), total=len(ids), step="", notes=[n for n in notes if n]))
    return J(autoai.start_job(pid, ids))


@app.get("/api/projects/<int:pid>/ai/job")
def ai_job(pid):
    from core import autoai
    j = autoai.job_status(pid)
    j.pop("queue", None)
    if j.get("started"):
        j["elapsed"] = round((j.get("finished") or time.time()) - j["started"])
    return J(j)


@app.post("/api/docs/<int:did>/sheet/<int:sheet>/reread")
def ai_reread(did, sheet):
    """Vision re-read of one sheet (Sonnet); replaces that sheet's I/O rows (flagged source=ai-vision, needs review)."""
    from core import ai_assist, autoai
    from core.pdfio import page_png
    doc = db.q("SELECT * FROM documents WHERE id=?", (did,), one=True)
    if not doc:
        abort(404)
    mtype = (request.json or {}).get("module_type") or "DI"
    try:
        with open_pdf(doc["path"]) as pdf:
            png = page_png(pdf.pages[sheet - 1], 130)
        chans = ai_assist.reread_sheet(png, mtype)
        return J({"ok": True, "channels": autoai.replace_sheet_rows(doc, sheet, mtype, chans, "re-read by AI vision - verify")})
    except LLMError as e:
        return J({"error": str(e)}, 400)


@app.post("/api/projects/<int:pid>/extract-ga")
def extract_project_ga(pid):
    """Extract GA panel details and specs for all GA sheets in a project (rules first, Claude Haiku as fallback when AI is on)."""
    from core.ga_extract import extract_sheet_specs, extract_with_llm
    from core.pdfio import page_text
    from core.ai_assist import get_client
    client = get_client()
    docs_ = db.q("SELECT id, path FROM documents WHERE project_id=?", (pid,))
    total_extracted = 0
    for d in docs_:
        if not os.path.exists(d["path"]):
            continue
        sheets_ = db.q("SELECT sheet, draw_type FROM sheets WHERE doc_id=? ORDER BY sheet", (d["id"],))
        db.x("DELETE FROM panel_specs WHERE doc_id=?", (d["id"],))
        specs_to_insert = []
        with open_pdf(d["path"]) as pdf:
            for s in sheets_:
                s_num = s["sheet"]
                if s_num < 1 or s_num > len(pdf.pages):
                    continue
                dtype = s["draw_type"]
                txt = page_text(pdf.pages[s_num - 1])
                if dtype in ("external_ga", "internal_ga", "general_specs", "feeder_legend") or "PANEL DETAILS" in txt.upper() or "DIMENSIONS IN MM" in txt.upper():
                    sheet_specs = extract_sheet_specs(pdf.pages[s_num - 1], s_num, dtype, txt)
                    if not sheet_specs and client.ready:
                        sheet_specs = extract_with_llm(txt, sheet_num=s_num, draw_type=dtype, client=client)
                    specs_to_insert.extend(sheet_specs or [])
        if specs_to_insert:
            db.insert_many("panel_specs", pid, d["id"], specs_to_insert)
            total_extracted += len(specs_to_insert)
    return J({"ok": True, "extracted_specs": total_extracted})


@app.get("/api/learning")
def learning_info():
    return J(dict(rules=db.q("SELECT id,entity,field,original,corrected,hits FROM rules ORDER BY hits DESC, id DESC LIMIT 500"),
                  corrections=db.q("SELECT COUNT(*) n FROM corrections", one=True)["n"],
                  applied=db.q("SELECT COALESCE(SUM(hits),0) n FROM rules", one=True)["n"]))


@app.delete("/api/learning/<int:rid>")
def learning_delete(rid):
    learning.delete_rule(rid)
    return J({"ok": True})


@app.get("/api/learning/export")
def learning_export():
    return send_file(io.BytesIO(json.dumps(learning.export_rules(), indent=1).encode()), as_attachment=True, download_name="prg_learned_rules.json", mimetype="application/json")


@app.post("/api/learning/import")
def learning_import():
    f = request.files.get("file")
    try:
        rows = json.load(f.stream) if f else (request.json or [])
    except Exception:
        return J({"error": "not a valid rules file"}, 400)
    return J({"imported": learning.import_rules(rows)})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5057)), debug=False, threaded=True)






