"""Automatic AI pass, run after every extraction when ANTHROPIC_API_KEY is configured.

Cost control: each step only sends what the deterministic parsers could not settle, and uses the cheapest model that is good enough
(Haiku for text/classification, Sonnet only for images).  Every failure is non-fatal - the deterministic result stays."""
import traceback
from . import db, ai_assist
from .llm import LLMError
from .pdfio import open_pdf, page_png, page_text, ocr_available

VISION_TYPES = {"plc_di": "DI", "plc_do": "DO", "plc_ai": "AI", "plc_ao": "AO"}


def replace_sheet_rows(doc, sheet, mtype, channels, note):
    """Replace one sheet's I/O rows with rows read by vision (flagged source=ai-vision, low confidence -> shows in review)."""
    old = db.q("SELECT * FROM io_rows WHERE doc_id=? AND sheet=?", (doc["id"], str(sheet)))
    if old:
        tpl = old[0]
    else:
        slots = [int(r["slot"]) for r in db.q("SELECT slot FROM io_rows WHERE doc_id=?", (doc["id"],)) if str(r["slot"]).isdigit()]
        tpl = dict(rack="1", slot=str(max(slots, default=0) + 1), module_name=f"{mtype} Module (AI read)", module_model="", signal_type="")
    db.x("DELETE FROM io_rows WHERE doc_id=? AND sheet=?", (doc["id"], str(sheet)))
    rows = [dict(rack=tpl["rack"], slot=tpl["slot"], module_name=tpl["module_name"], module_type=mtype, module_model=tpl["module_model"],
                 plc_address=c.get("plc_address", ""), channel=i, tag=c.get("tag", ""), description=c.get("description", ""),
                 signal_type=tpl["signal_type"], field_device="", cable_no="", terminal=c.get("terminal", ""), remarks=note,
                 spare=str(c.get("description", "")).upper() == "SPARE", sheet=sheet, conf=0.6, source="ai-vision")
            for i, c in enumerate(channels)]
    db.insert_many("io", doc["project_id"], doc["id"], rows)
    return len(rows)


def store_schematic(doc, sheet, info):
    """Symbols / wires / contacts that vision recognised on a raster schematic -> devices, ferrules, coil-contact rows (all flagged ai-vision)."""
    from .devices import classify
    pid, did = doc["project_id"], doc["id"]
    devs = [dict(sheet=sheet, tag=d.get("tag", ""), make="", part_no="", rating=d.get("rating", ""), descr=d.get("type", ""),
                 dev_class=classify(d.get("tag", ""), "", d.get("type", "")), catalogue_no="", panel="", source="ai-vision")
            for d in info.get("devices", []) if d.get("tag")]
    db.insert_many("devices", pid, did, devs)
    wires = [dict(sheet=sheet, wire_no=w.get("wire_no", ""), ferrule_no=w.get("wire_no", ""), from_terminal=w.get("from", ""), to_terminal=w.get("to", ""),
                  device_ref=str(w.get("from", "")).split(":")[0], cable_ref="", core_no="", to_ref="", source="ai-vision")
             for w in info.get("wires", []) if w.get("wire_no") or (w.get("from") and w.get("to"))]
    db.insert_many("wires", pid, did, wires)
    xr = [dict(sheet=sheet, direction="coil" if str(c.get("role", "")).lower() == "coil" else "contact", target_sheet="", target_col="", label=c.get("tag", ""),
               text=f"{str(c.get('role', 'contact')).lower()}; {c.get('contact_type', '') + '; ' if c.get('contact_type') else ''}pins={c.get('pins') or '-'}; refs=-")
          for c in info.get("contacts", []) if c.get("tag")]
    db.insert_many("xrefs", pid, did, xr)


def enrich_doc(doc_id, client):
    n = 0
    rows = db.q("SELECT id,module_type,tag,description,field_device,spare,remarks FROM io_rows WHERE doc_id=? AND source NOT IN ('edited','manual') "
                "AND spare<>'1' AND (field_device='' OR tag='') AND description<>''", (doc_id,))
    res = ai_assist.enrich_io(rows, client) if rows else {}
    for r in rows:
        a = res.get(r["id"])
        if not a:
            continue
        rem = r["remarks"]
        if not r["field_device"] and a.get("field_device"):
            db.x("UPDATE io_rows SET field_device=? WHERE id=?", (a["field_device"], r["id"])); n += 1
        if not r["tag"] and a.get("suggested_tag"):
            rem = (rem + "; " if rem else "") + "tag suggested by AI - verify"
            db.x("UPDATE io_rows SET tag=?, remarks=? WHERE id=?", (a["suggested_tag"], rem, r["id"])); n += 1
        if a.get("signal_kind") and a["signal_kind"] not in rem:
            db.x("UPDATE io_rows SET remarks=? WHERE id=?", ((rem + "; " if rem else "") + a["signal_kind"], r["id"]))
    devs = db.q("SELECT id,tag,part_no,make,descr FROM devices WHERE doc_id=? AND dev_class='Other' AND source<>'edited'", (doc_id,))
    m = 0
    if devs:
        for did, cls in ai_assist.classify_devices(devs, client).items():
            db.x("UPDATE devices SET dev_class=? WHERE id=?", (cls, did)); m += 1
    return n, m


def run(doc_id, step=None):
    """Returns a short status string, '' if AI is off or nothing was needed. `step(text)` reports progress."""
    client = ai_assist.get_client()
    if not client.ready:
        return ""
    step = step or (lambda text: None)
    doc = db.q("SELECT * FROM documents WHERE id=?", (doc_id,), one=True)
    notes = []
    try:
        sheets = db.q("SELECT sheet,draw_type,scanned,kind FROM sheets WHERE doc_id=?", (doc_id,))
        with open_pdf(doc["path"]) as pdf:
            # 1) vision (STRONG): PLC sheets with no channels, and scanned pages when OCR is not installed
            read = 0
            have_io = {r["sheet"] for r in db.q("SELECT DISTINCT sheet FROM io_rows WHERE doc_id=?", (doc_id,))}
            for s in sheets:
                n = s["sheet"]
                if n < 1 or n > len(pdf.pages):
                    continue
                try:
                    if s["draw_type"] in VISION_TYPES and str(n) not in have_io:
                        step(f"reading PLC sheet {n} from the image")
                        png = page_png(pdf.pages[n - 1], 130)
                        replace_sheet_rows(doc, n, VISION_TYPES[s["draw_type"]], ai_assist.reread_sheet(png, VISION_TYPES[s["draw_type"]], client),
                                           "read by AI vision - verify")
                        read += 1
                    elif s["scanned"] and not ocr_available() and s["kind"] != "index":
                        step(f"reading scanned sheet {n}")
                        info = ai_assist.read_scanned_sheet(page_png(pdf.pages[n - 1], 130), client)
                        t = str(info.get("sheet_type", "")).upper()
                        if t in ("DI", "DO", "AI", "AO", "RTD", "TC") and info.get("channels"):
                            replace_sheet_rows(doc, n, t, info["channels"], "scanned sheet read by AI vision - verify")
                            read += 1
                        elif t == "SCHEMATIC":
                            store_schematic(doc, n, info)
                            read += 1
                        if info.get("title"):
                            db.x("UPDATE sheets SET title=? WHERE doc_id=? AND sheet=?", (info["title"], doc_id, n))
                except LLMError as e:
                    notes.append(f"sheet {n}: {e}")
            if read:
                notes.insert(0, f"{read} sheet(s) read by vision")
            # 2) specs (FAST): GA / spec sheets the rule-based parser produced nothing for
            got = {r["sheet"] for r in db.q("SELECT DISTINCT sheet FROM panel_specs WHERE doc_id=?", (doc_id,))}
            specs = []
            for s in sheets:
                if s["draw_type"] in ("external_ga", "internal_ga", "general_specs") and str(s["sheet"]) not in got and 1 <= s["sheet"] <= len(pdf.pages):
                    step(f"reading specifications on sheet {s['sheet']}")
                    specs += ai_assist.extract_specs(page_text(pdf.pages[s["sheet"] - 1]), s["sheet"], s["draw_type"], client)
            # 2b) GA layout (STRONG, vision): door items, sizes and view dimensions are drawn, not written as text
            for s in sheets:
                if s["draw_type"] == "external_ga" and 1 <= s["sheet"] <= len(pdf.pages):
                    step(f"reading the door layout on GA sheet {s['sheet']}")
                    try:
                        lay = ai_assist.read_ga_layout(page_png(pdf.pages[s["sheet"] - 1], 150), client)
                    except LLMError as e:
                        notes.append(f"GA sheet {s['sheet']}: {e}")
                        continue
                    for it in lay.get("items", []):
                        val = " ".join(x for x in (it.get("device", ""), f"({it['colour']})" if it.get("colour") else "",
                                                   f"- {it['position']}" if it.get("position") else "") if x).strip()
                        if val:
                            specs.append(dict(sheet=s["sheet"], category="Front Panel Layout (AI vision - verify)", property_name=f"Item {it.get('no') or '?'}",
                                              property_value=val, unit=f"{it['size_mm']} mm" if it.get("size_mm") else "", raw_text=val))
                    for v in lay.get("views", []):
                        if v.get("view") and v.get("dimensions"):
                            specs.append(dict(sheet=s["sheet"], category="GA Views (AI vision - verify)", property_name=v["view"],
                                              property_value=str(v["dimensions"]), unit="mm", raw_text=str(v["dimensions"])))
                    notes.append(f"GA sheet {s['sheet']} layout read by vision")
            db.x("DELETE FROM panel_specs WHERE doc_id=? AND category LIKE '%(AI vision%'", (doc_id,))     # re-runs replace, not duplicate
            if specs:
                db.insert_many("panel_specs", doc["project_id"], doc_id, specs)
        # 3) enrichment + classification (FAST)
        step("filling field devices and classifying devices")
        n, m = enrich_doc(doc_id, client)
        if n or m:
            notes.append(f"AI filled {n} I/O fields, classified {m} devices")
    except LLMError as e:
        notes.append(f"AI skipped: {e}")
    except Exception as e:  # noqa
        traceback.print_exc()
        notes.append(f"AI skipped: {str(e)[:80]}")
    return "; ".join(notes)


# ------------------------------------------------------------------------------------------------ background jobs
# One AI job per project, run in a background thread so extraction results are usable immediately and the UI can show progress.
import threading, time as _time
JOBS = {}
_jobs_lock = threading.Lock()


def job_status(pid):
    j = JOBS.get(pid)
    return dict(j) if j else dict(state="idle")


def start_job(pid, doc_ids):
    """Start (or keep) the AI pass for these documents. Returns the job status."""
    with _jobs_lock:
        j = JOBS.get(pid)
        if j and j["state"] == "running":              # batch upload: queue behind the running pass
            new = [d for d in doc_ids if d not in j["queue"]]
            j["queue"] += new
            j["total"] += len(new)
            return dict(j)
        j = JOBS[pid] = dict(state="running", step="starting", done=0, total=len(doc_ids), notes=[], started=_time.time(), finished=None,
                             queue=list(doc_ids))

    def work():
        try:
            while True:
                with _jobs_lock:
                    if not j["queue"]:
                        j["state"] = "finished"          # under the lock, so a document queued now starts a new job
                        break
                    did = j["queue"].pop(0)
                doc = db.q("SELECT filename FROM documents WHERE id=?", (did,), one=True) or {"filename": ""}
                j["step"] = f"{doc['filename'][:40]}: starting"
                note = run(did, step=lambda text, fn=doc["filename"][:40]: j.__setitem__("step", f"{fn}: {text}"))
                if note:
                    j["notes"].append(note)
                row = db.q("SELECT message FROM documents WHERE id=?", (did,), one=True)
                base = (row["message"] or "").split(" | AI pass")[0].split(" | AI ")[0] if row else ""
                db.x("UPDATE documents SET message=? WHERE id=?", (base + " | " + (note or "AI pass finished - nothing needed filling"), did))
                j["done"] += 1
        except Exception as e:  # noqa
            traceback.print_exc()
            j["state"], j["notes"] = "failed", j["notes"] + [str(e)[:200]]
        finally:
            j["step"], j["finished"] = "", _time.time()
    threading.Thread(target=work, daemon=True).start()
    return dict(j)
