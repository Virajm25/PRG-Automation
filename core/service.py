"""Ingestion service: run pipeline on an uploaded PDF, apply learned rules, persist."""
import os, threading, traceback, re
from . import db, learning, autoai
from .pipeline import process_pdf
from .devices import classify
from .cables import assemble, symbol_devices


def ingest_async(doc_id):
    t = threading.Thread(target=ingest, args=(doc_id,), daemon=True)
    t.start()
    return t


def ingest(doc_id):
    doc = db.q("SELECT * FROM documents WHERE id=?", (doc_id,), one=True)
    pid = doc["project_id"]
    try:
        db.x("UPDATE documents SET status='processing', message='' WHERE id=?", (doc_id,))
        def prog(i, n):
            db.x("UPDATE documents SET progress=?, pages=? WHERE id=?", (int(100 * i / n), n, doc_id))
        res = process_pdf(doc["path"], progress=prog)
        rules = learning.load_rules()
        auto = learning.apply_rules("io", res["io"], rules)
        # devices: schematic parts + PLC modules + instruments / field devices derived from I/O
        devs = list(res["devices"])
        seen = set()
        for r in res["io"]:
            if r["spare"]:
                continue
            if r["module_type"] in ("AI", "AO", "RTD", "TC") and r["tag"]:
                devs.append(dict(sheet=r["sheet"], tag=r["tag"], make="", part_no="", rating=r["signal_type"], desc=r["description"],
                                 dev_class=classify(r["tag"], "", r["description"]), source="io"))
            elif r["field_device"] and r["field_device"].upper() not in seen:
                seen.add(r["field_device"].upper())
                devs.append(dict(sheet=r["sheet"], tag="", make="", part_no="", rating="", desc=r["field_device"],
                                 dev_class=classify("", "", r["field_device"]), source="io"))
        for b in res.get("bom", []):   # printed BOM lines also appear in the device list (grouped by class)
            devs.append(dict(sheet=b["sheet"], tag="", make=b["make"], part_no=b["part_no"], rating=b["rating"], desc=f"{b['descr']} (qty {b['qty']})",
                             dev_class=classify("", b["part_no"], b["descr"]), source="bom", catalogue_no=b["part_no"]))
        devs += symbol_devices(res, devs)
        for d in devs:
            d.setdefault("source", "drawing")
            d["descr"] = d.pop("desc", d.get("descr", ""))
            d["catalogue_no"] = d.get("catalogue_no", d.get("part_no", ""))
            d["panel"] = res["meta"].get("project", "")
        auto += learning.apply_rules("devices", devs, rules)
        auto += learning.apply_rules("bom_lines", res.get("bom", []), rules)
        for r in res["io"]:
            r["source"] = "vector" if not any(s["scanned"] for s in res["sheets"] if s["sheet"] == r["sheet"]) else "ocr"
        panel = res["meta"].get("project", "")
        cables, wires, terminals = assemble(res, panel)
        auto += learning.apply_rules("cables", cables, rules)
        auto += learning.apply_rules("circuits", res.get("circuits", []), rules)
        auto += learning.apply_rules("wires", wires, rules) + learning.apply_rules("terminals", terminals, rules)
        for t in db.DOC_TABLES:
            db.x(f"DELETE FROM {t} WHERE doc_id=?", (doc_id,))
        db.insert_many("io", pid, doc_id, res["io"])
        if res.get("circuits"):
            db.insert_many("circuits", pid, doc_id, res["circuits"])
        if res.get("panel_specs"):
            db.insert_many("panel_specs", pid, doc_id, res["panel_specs"])
        db.insert_many("devices", pid, doc_id, devs)
        db.insert_many("cables", pid, doc_id, cables)
        db.insert_many("symbols", pid, doc_id, res.get("symbols", []))
        db.insert_many("annotations", pid, doc_id, res.get("annotations", []))
        db.insert_many("bom", pid, doc_id, res.get("bom", []))
        db.insert_many("wires", pid, doc_id, wires)
        db.insert_many("terminals", pid, doc_id, terminals)
        # device / coil-contact cross reference: tag -> sheets where it occurs (sheet-level mapping)
        from collections import defaultdict, Counter
        occ = defaultdict(Counter)
        for t in res["tags"]:
            occ[t["tag"]][t["sheet"]] += 1
        xr = list(res["xrefs"])
        for tag, c in sorted(occ.items()):
            cls = classify(tag)
            xr.append(dict(sheet=min(c), direction="device", target_sheet=",".join(str(k) for k in sorted(c)), target_col=str(sum(c.values())),
                           label=tag, text=f"{cls}; " + ", ".join(f"{k}({v})" for k, v in sorted(c.items()))))
        db.insert_many("xrefs", pid, doc_id, xr)
        for s in res["sheets"]:
            db.x(
                "INSERT INTO sheets(project_id,doc_id,sheet,title,label,kind,"
                "draw_type,draw_type_label,drawing_no,revision,section_view,scanned) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, doc_id, s["sheet"], s["title"], s["label"], s["kind"],
                 s.get("draw_type", ""), s.get("draw_type_label", ""),
                 s.get("drawing_no", ""), s.get("revision", ""),
                 s.get("section_view", ""), int(s["scanned"])),
            )
        for m in res["modules"]:
            db.x("INSERT INTO modules(project_id,doc_id,rack,slot,module_type,label,model,channels,sheets,slot_derived) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (pid, doc_id, m["rack"], m["slot"], m["module_type"], m["label"], m["model"], m["channels"], m["sheets"], int(m["slot_derived"])))
        n_circ = len(res.get("circuits", []))
        circ_msg = f", {n_circ} circuits" if n_circ else ""
        msg = f"{len(res['io'])} I/O{circ_msg}, {len(devs)} devices, {len(wires)} ferrules, {len(cables)} cables, {len(res.get('bom', []))} BOM lines" + (f", {auto} learned corrections applied" if auto else "")
        if res["warnings"]:
            msg += " | " + "; ".join(res["warnings"][:3])
        if os.environ.get("VERCEL"):
            # serverless: extraction runs inside the upload request; a full AI pass would exceed the function time limit
            ai_note = "AI pass not run automatically on the web version - use Re-run AI" if autoai.ai_assist.ai_enabled() else ""
        else:
            db.x("UPDATE documents SET status='ai', progress=100, panel=?, message=? WHERE id=?",
                 (res["meta"].get("project", ""), msg + " | AI pass running", doc_id))
            ai_note = autoai.run(doc_id)       # no-op when no AI provider is configured
        if ai_note:
            msg += " | " + ai_note
        db.x("UPDATE documents SET status='done', progress=100, panel=?, message=? WHERE id=?",
             (res["meta"].get("project", ""), msg, doc_id))
    except Exception as e:  # noqa
        traceback.print_exc()
        db.x("UPDATE documents SET status='error', message=? WHERE id=?", (str(e)[:300], doc_id))
