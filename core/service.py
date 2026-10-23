"""Ingestion service: run pipeline on an uploaded PDF, apply learned rules, persist."""
import os, threading, traceback, re
from . import db, learning
from .pipeline import process_pdf
from .devices import classify


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
        for d in devs:
            d.setdefault("source", "drawing")
            d["descr"] = d.pop("desc", d.get("descr", ""))
            d["catalogue_no"] = d.get("catalogue_no", d.get("part_no", ""))
            d["panel"] = res["meta"].get("project", "")
        auto += learning.apply_rules("devices", devs, rules)
        for r in res["io"]:
            r["source"] = "vector" if not any(s["scanned"] for s in res["sheets"] if s["sheet"] == r["sheet"]) else "ocr"
        wires = []
        for w in res["wires"]:
            wires.append(dict(w, from_terminal="", to_terminal=w["to_ref"] and f"→ sheet {w['to_ref']}", cable_ref="", core_no=""))
        db.x("DELETE FROM io_rows WHERE doc_id=?", (doc_id,))
        for t in ("io_rows", "circuits", "devices", "wires", "terminals", "xrefs", "sheets", "modules", "panel_specs"):
            db.x(f"DELETE FROM {t} WHERE doc_id=?", (doc_id,))
        db.insert_many("io", pid, doc_id, res["io"])
        if res.get("circuits"):
            db.insert_many("circuits", pid, doc_id, res["circuits"])
        if res.get("panel_specs"):
            db.insert_many("panel_specs", pid, doc_id, res["panel_specs"])
        db.insert_many("devices", pid, doc_id, devs)
        db.insert_many("wires", pid, doc_id, wires)
        db.insert_many("terminals", pid, doc_id, res["terminals"])
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
        msg = f"{len(res['io'])} I/O{circ_msg}, {len(devs)} devices, {len(wires)} wires" + (f", {auto} learned corrections applied" if auto else "")
        if res["warnings"]:
            msg += " | " + "; ".join(res["warnings"][:3])
        db.x("UPDATE documents SET status='done', progress=100, panel=?, message=? WHERE id=?",
             (res["meta"].get("project", ""), msg, doc_id))
    except Exception as e:  # noqa
        traceback.print_exc()
        db.x("UPDATE documents SET status='error', message=? WHERE id=?", (str(e)[:300], doc_id))
