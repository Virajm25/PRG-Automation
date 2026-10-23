"""PDF -> structured engineering data. Deterministic extraction; LLM enrichment is applied separately."""
import re, os
from collections import OrderedDict, defaultdict
from .pdfio import open_pdf, is_scanned, ocr_page_words, ocr_to_chars, upright_chars, page_text
from .io_extract import is_io_sheet, extract_io_sheet, classify_module
from .devices import extract_devices, scan_catalog_parts, merge_devices, classify
from .wires import extract_wires, dedupe_wires
from .pagemeta import title_of, sheet_label, project_meta
from .circuits import extract_circuits, is_feeder_sheet
from .sheet_classify import classify_sheet, extract_title_block, DRAW_TYPE_LABEL
from .ga_extract import extract_panel_specs_from_text

SIG_TAIL = re.compile(r"\b(RUN|ON|OFF|TRIP|FAULT|START|STOP|CMD|COMMAND|F/B|FEEDBACK|STATUS|OPEN|CLOSE|CLOSED|AUTO|MANUAL|"
                      r"LOCAL|REMOTE|READY|HEALTHY|SS|AM|FWD|FORWARD|REVERSE|RESET|ACCEPT|ENABLE)\b", re.I)


def derive_field_device(desc):
    d = re.sub(r"\s+", " ", desc or "").strip()
    if not d:
        return ""
    cut = SIG_TAIL.search(d)
    out = d[:cut.start()].strip(" -/") if cut and cut.start() > 2 else d
    return out or d


def derive_signal_kind(row):
    t = row["module_type"]
    s = (row.get("signal_type") or "").strip()
    return {"DI": s or "24 VDC", "DO": s or "24 VDC", "AI": s or "4-20 mA", "AO": s or "4-20 mA",
            "RTD": s or "RTD (PT100)", "TC": s or "Thermocouple"}.get(t, s)


def _module_key(mod, title):
    t = mod["type"]
    if "CPU" in (title or "").upper():
        return (t, "CPU")
    if mod.get("rack") and mod.get("slot"):
        return (t, f"R{mod['rack']}S{mod['slot']}")
    m = re.search(r"MODULE\s*-?\s*(\d+)", mod.get("module_name", ""), re.I) or re.search(r"MODULE\s*-?\s*(\d+)", title or "", re.I) \
        or re.search(r"WIRING\s*-?\s*(\d+)\s*$", title or "", re.I)
    return (t, m.group(1) if m else (mod.get("module_name") or "?"))


def process_pdf(path, progress=None, use_ocr=True):
    res = dict(sheets=[], io=[], circuits=[], devices=[], wires=[], xrefs=[], tags=[], modules=[], meta={}, panel_specs=[], warnings=[])
    anchored_all, scanned_all = [], []
    with open_pdf(path) as pdf:
        n = len(pdf.pages)
        try:
            res["circuits"] = extract_circuits(pdf)
        except Exception:
            res["circuits"] = []
        raw_mods = []
        for i, page in enumerate(pdf.pages, start=1):
            if progress:
                progress(i, n)
            scanned = is_scanned(page)
            chars = None
            text = page_text(page)
            if scanned:
                if use_ocr:
                    w = ocr_page_words(page)
                    chars = ocr_to_chars(w)
                    text = " ".join(x["text"] for x in w)
                    if not w:
                        res["warnings"].append(f"sheet {i}: scanned page but OCR (tesseract) unavailable")
                else:
                    res["warnings"].append(f"sheet {i}: scanned page skipped (OCR disabled)")
            title = title_of(text)
            draw_type = classify_sheet(text)
            tb = extract_title_block(text)
            # Extract structured specifications from GA & Specs sheets
            if draw_type in ("external_ga", "internal_ga") or "PANEL DETAILS" in text.upper():
                try:
                    specs = extract_panel_specs_from_text(text, sheet_num=i, draw_type=draw_type)
                    if specs:
                        res["panel_specs"].extend(specs)
                except Exception:
                    pass
            # Derive legacy 'kind' from draw_type for backward compatibility
            if draw_type in ("plc_di", "plc_do", "plc_ai", "plc_ao", "plc_cpu"):
                kind = "io"
            elif draw_type in ("table_of_contents", "title_page"):
                kind = "index"
            elif is_io_sheet(text) and (chars is None or chars):
                kind = "io"
            else:
                kind = "schematic"
            if title.upper().startswith(("TABLE OF CONTENTS", "TITLE PAGE")) or "TABLE OF CONTENTS" in text[:200].upper():
                kind = "index"
            display_title = (tb.get("drawing_title") or title or text.split("\n")[0][:60]).strip()
            res["sheets"].append(dict(
                sheet=i,
                title=display_title,
                label=tb.get("sheet_no") or sheet_label(text),
                kind=kind,
                draw_type=draw_type,
                draw_type_label=DRAW_TYPE_LABEL.get(draw_type, "Unknown"),
                drawing_no=tb.get("drawing_no", ""),
                revision=tb.get("revision", ""),
                section_view=tb.get("section_view", ""),
                scanned=scanned,
                chars=len(page.chars),
            ))
            if i == 1 or not res["meta"]:
                res["meta"].update(project_meta(text))
            if kind == "index":
                continue
            ch = chars if chars is not None else upright_chars(page)
            if kind == "io":
                mod, rows = extract_io_sheet(page if chars is None else ch, text, i, ocr=chars is not None) if chars is None else (None, [])
                if mod:
                    mod["title"] = title
                    raw_mods.append((mod, rows))
                    continue
            # schematic sheet
            a = extract_devices(page, i, ch)
            s = scan_catalog_parts(page, i, ch)
            anchored_all += a
            scanned_all += s
            w, x, t = extract_wires(page, i, ch)
            res["wires"] += w
            res["xrefs"] += x
            res["tags"] += t
        _assemble_io(res, raw_mods)
    res["devices"] = merge_devices(anchored_all, scanned_all)
    res["wires"] = dedupe_wires(res["wires"])
    for d in res["devices"]:
        d.pop("_x", None); d.pop("_y", None)
    for w in res["wires"]:
        w.pop("_x", None); w.pop("_y", None)
    _add_plc_modules_to_devices(res)
    res["terminals"] = build_terminals(res["io"])
    return res


def _assemble_io(res, raw_mods):
    """Group sheets of the same physical module, assign rack / slot / channel numbers."""
    groups = OrderedDict()
    for mod, rows in raw_mods:
        key = _module_key(mod, mod.get("title"))
        g = groups.setdefault(key, dict(mod=mod, rows=[], sheets=[]))
        rows = [r for r in rows if r["plc_address"] or r["tag"] or r["description"]]
        if not rows:
            res["warnings"].append(f"sheet {mod['sheet']}: module sheet recognised but no channels found")
            continue
        g["rows"] += rows
        g["sheets"].append(mod["sheet"])
    slot_counter = defaultdict(int)
    for (mtype, mid), g in groups.items():
        if not g["rows"]:
            continue
        m = g["mod"]
        rack = m.get("rack") or "1"
        slot_given = bool(m.get("slot"))
        if slot_given:
            slot = m["slot"]
        else:
            slot_counter[rack] += 1
            slot = str(slot_counter[rack])
        label = "CPU (on-board)" if mid == "CPU" else f"{mtype} Module-{mid}"
        res["modules"].append(dict(rack=rack, slot=slot, slot_derived=not slot_given, module_type=mtype, module_id=mid,
                                   label=label, model=m.get("model", ""), channels=len(g["rows"]),
                                   sheets=",".join(str(s) for s in g["sheets"])))
        for ch, r in enumerate(g["rows"]):
            r["rack"] = rack
            r["slot"] = slot
            r["channel"] = ch
            r["module_name"] = label
            r["signal_type"] = derive_signal_kind(r)
            r["field_device"] = derive_field_device(r["description"]) if not r["spare"] else ""
            r["cable_no"] = ""
            r["conf"] = _row_conf(r)
            if r["spare"] and not r["description"]:
                r["description"] = "SPARE"
            if not slot_given:
                r["remarks"] = (r["remarks"] + "; " if r["remarks"] else "") + "slot derived from drawing order"
            res["io"].append(r)


def _row_conf(r):
    c = 1.0
    if not r["plc_address"]:
        c -= 0.4
    if not r["spare"] and not r["tag"]:
        c -= 0.2
    if r["module_type"] in ("TC", "RTD"):
        c -= 0.3
    if len(r["description"]) > 70:
        c -= 0.3
    return round(max(c, 0.05), 2)


def _add_plc_modules_to_devices(res):
    for m in res["modules"]:
        if not m["model"]:
            continue
        part = re.sub(r"^MLFB-", "", m["model"], flags=re.I)
        make = {"6ES7": "Siemens", "BMX": "Schneider", "DVP": "Delta"}
        mk = next((v for k, v in make.items() if part.upper().startswith(k)), "")
        res["devices"].append(dict(sheet=int(str(m["sheets"]).split(",")[0]), tag=f"R{m['rack']}/S{m['slot']}", make=mk,
                                   part_no=part, rating=m["module_type"], desc=f"PLC {m['label']}",
                                   dev_class="PLC Module"))


def build_terminals(io_rows):
    strips = OrderedDict()
    for r in io_rows:
        if ":" not in r.get("terminal", ""):
            continue
        strip, nums = r["terminal"].split(":", 1)
        for n in nums.split(","):
            strips.setdefault(strip, []).append(dict(strip=strip, terminal_no=n, tag=r["tag"], plc_address=r["plc_address"],
                                                      desc=r["description"], sheet=r["sheet"], module_type=r["module_type"]))
    out = []
    for strip, items in strips.items():
        used = {int(i["terminal_no"]) for i in items if i["terminal_no"].isdigit()}
        top = max(used) if used else 0
        for i in items:
            ttype = "Fused (500mA)" if i["module_type"] in ("DI", "AI") and len(items) > 0 else "Normal"
            out.append(dict(strip=strip, terminal_no=i["terminal_no"], terminal_type=ttype, incoming_wire="", outgoing_wire="",
                            cable_no="", core_no="", signal_tag=i["tag"], plc_address=i["plc_address"],
                            spare="SPARE" if (not i["tag"] or i["desc"].upper() == "SPARE") else "", sheet=i["sheet"]))
        for n in range(1, top + 1):
            if n not in used:
                out.append(dict(strip=strip, terminal_no=str(n), terminal_type="Normal", incoming_wire="", outgoing_wire="",
                                cable_no="", core_no="", signal_tag="", plc_address="", spare="SPARE", sheet=items[0]["sheet"]))
    out.sort(key=lambda t: (t["strip"], int(t["terminal_no"]) if t["terminal_no"].isdigit() else 999))
    return out

