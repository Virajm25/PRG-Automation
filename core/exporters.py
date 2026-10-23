"""Datasets + exporters: XLSX (formatted), CSV, PDF, XML."""
import io, csv, zipfile, re
from collections import OrderedDict, defaultdict
from xml.sax.saxutils import escape
from . import db

IO_COLS = ["Rack", "Slot", "Module Type", "DI", "DO", "AI", "AO", "RTD", "Thermocouple", "Spare I/O", "Signal Description", "Tag Name",
           "Signal Type", "Field Device Name", "Cable Number", "Terminal Number", "Remarks", "Sheet"]


def datasets(pid):
    D = OrderedDict()
    io = db.q("SELECT * FROM io_rows WHERE project_id=? ORDER BY doc_id, CAST(rack AS INT), CAST(slot AS INT), CAST(channel AS INT)", (pid,))
    rows = []
    for r in io:
        t = r["module_type"]
        mark = lambda k: r["plc_address"] if t == k and not r["spare"] else ""
        rows.append([r["rack"], r["slot"], f"{r['module_name']} ({r['module_model']})" if r["module_model"] else r["module_name"],
                     mark("DI"), mark("DO"), mark("AI"), mark("AO"), mark("RTD"), mark("TC"),
                     (r["plc_address"] or "SPARE") if r["spare"] else "", r["description"], r["tag"], r["signal_type"],
                     r["field_device"], r["cable_no"], r["terminal"], r["remarks"], r["sheet"]])
    D["I-O List"] = (IO_COLS, rows)

    mods = db.q("SELECT * FROM modules WHERE project_id=? ORDER BY doc_id, CAST(rack AS INT), CAST(slot AS INT)", (pid,))
    summ = []
    for m in mods:
        rr = [r for r in io if r["doc_id"] == m["doc_id"] and r["rack"] == m["rack"] and r["slot"] == m["slot"] and r["module_type"] == m["module_type"]]
        sp = sum(1 for r in rr if r["spare"])
        summ.append([m["rack"], m["slot"], m["label"], m["model"], m["module_type"], len(rr), len(rr) - sp, sp,
                     f"{(100*sp/len(rr)):.0f}%" if rr else "", "derived from drawing order" if m["slot_derived"] == 1 else ""])
    D["I-O Summary"] = (["Rack", "Slot", "Module", "Model", "Type", "Channels", "Used", "Spare", "Spare %", "Remarks"], summ)

    w = db.q("SELECT * FROM wires WHERE project_id=? ORDER BY CAST(sheet AS INT), wire_no", (pid,))
    D["Ferrule List"] = (["Ferrule No", "Wire No", "From Terminal", "To Terminal", "Device Reference", "Cable Reference", "Core No", "Sheet"],
                         [[x["ferrule_no"], x["wire_no"], x["from_terminal"], x["to_terminal"], x["device_ref"], x["cable_ref"], x["core_no"], x["sheet"]] for x in w])

    dv = db.q("SELECT * FROM devices WHERE project_id=? AND part_no<>'' ORDER BY make, part_no", (pid,))
    agg = OrderedDict()
    for d in dv:
        k = (d["make"].upper(), d["part_no"].upper(), d["rating"].upper())
        a = agg.setdefault(k, dict(d=d, qty=0, sheets=set(), tags=[]))
        a["qty"] += 1
        a["sheets"].add(int(d["sheet"] or 0))
        if d["tag"]:
            a["tags"].append(d["tag"])
    D["BOM"] = (["Part Description", "Manufacturer", "Part Number", "Quantity", "Rating", "Catalogue Number", "Panel Reference", "Sheet Number", "Tags", "Duplicates merged"],
                [[(a["d"]["descr"] or a["d"]["dev_class"]), a["d"]["make"], a["d"]["part_no"], a["qty"], a["d"]["rating"], a["d"]["catalogue_no"] or a["d"]["part_no"],
                  a["d"]["panel"], ", ".join(str(s) for s in sorted(a["sheets"])), ", ".join(sorted(set(a["tags"]))[:12]), a["qty"] - 1] for a in agg.values()])

    alld = db.q("SELECT * FROM devices WHERE project_id=? ORDER BY dev_class, tag", (pid,))
    D["Device List"] = (["Device Class", "Tag", "Description", "Make", "Part No", "Rating", "Sheet", "Source"],
                        [[d["dev_class"], d["tag"], d["descr"], d["make"], d["part_no"], d["rating"], d["sheet"], d["source"]] for d in alld])

    t = db.q("SELECT * FROM terminals WHERE project_id=? ORDER BY strip, CAST(terminal_no AS INT)", (pid,))
    D["Terminal Schedule"] = (["Terminal Strip", "Terminal No", "Terminal Type", "Incoming Wire", "Outgoing Wire", "Cable No", "Core No", "Signal Tag", "PLC Address", "Spare", "Sheet"],
                              [[x["strip"], x["terminal_no"], x["terminal_type"], x["incoming_wire"], x["outgoing_wire"], x["cable_no"], x["core_no"], x["signal_tag"], x["plc_address"], x["spare"], x["sheet"]] for x in t])
    c = db.q("SELECT * FROM cables WHERE project_id=?", (pid,))
    D["Cable Schedule"] = (["Cable No", "Cable Type", "Core Details", "Source Equipment", "Destination Equipment", "Length (m)", "Remarks"],
                           [[x["cable_no"], x["cable_type"], x["cores"], x["source_equipment"], x["dest_equipment"], x["length_m"], x["remarks"]] for x in c])
    circs = db.q("SELECT * FROM circuits WHERE project_id=? ORDER BY CAST(sheet AS INT), id", (pid,))
    if circs:
        D["Feeders - Circuits"] = (
            ["Feeder No", "Description", "Rating (kW)", "Starter Type", "MCB", "Contactor", "OLR", "Cable Size", "Terminal", "Controls", "Sheet"],
            [[x["feeder_no"], x["description"], x["rating_kw"], x["starter_type"], x["mcb"], x["contactor"], x["olr"], x["cable_size"], x["terminal"], x["controls"], x["sheet"]] for x in circs]
        )

    # Drawing Register — one row per page, with full classification metadata
    sh = db.q(
        "SELECT s.sheet, s.label, s.title, s.draw_type_label, s.drawing_no, s.revision, "
        "s.kind, s.scanned, d.filename "
        "FROM sheets s JOIN documents d ON d.id = s.doc_id "
        "WHERE s.project_id=? ORDER BY s.doc_id, s.sheet", (pid,)
    )
    D["Drawing Register"] = (
        ["Page", "Sheet No", "Drawing Title", "Drawing Type", "Drawing No.", "Revision", "Kind", "Scanned / Vector", "Source File"],
        [[r["sheet"], r["label"] or "", r["title"] or "", r["draw_type_label"] or r["kind"] or "",
          r["drawing_no"] or "", r["revision"] or "",
          r["kind"] or "", "Scanned (OCR)" if r["scanned"] else "Vector", r["filename"] or ""] for r in sh]
    )

    # Panel Specifications & GA Details
    ps = db.q("SELECT sheet, category, property_name, property_value, unit, raw_text FROM panel_specs WHERE project_id=? ORDER BY CAST(sheet AS INT), id", (pid,))
    if ps:
        D["Panel Specifications"] = (
            ["Sheet", "Category", "Property", "Specification / Value", "Unit", "Drawing Reference"],
            [[r["sheet"], r["category"], r["property_name"], r["property_value"], r["unit"] or "", r["raw_text"] or ""] for r in ps]
        )

    return D


def to_xlsx(pid, project_name=""):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    wb = Workbook(); wb.remove(wb.active)
    thin = Side(style="thin", color="BBBBBB")
    for name, (cols, rows) in datasets(pid).items():
        ws = wb.create_sheet(name[:31])
        ws.append([f"{project_name} - {name}"]); ws["A1"].font = Font(bold=True, size=13)
        ws.append(cols)
        for c in ws[2]:
            c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F4E78")
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); c.border = Border(top=thin, bottom=thin, left=thin, right=thin)
        for r in rows:
            ws.append(r)
        for row in ws.iter_rows(min_row=3):
            for c in row:
                c.border = Border(top=thin, bottom=thin, left=thin, right=thin); c.alignment = Alignment(vertical="top", wrap_text=True)
        for i, col in enumerate(cols, 1):
            mx = max([len(str(col))] + [len(str(r[i - 1])) for r in rows[:300]]) if rows else len(col)
            ws.column_dimensions[get_column_letter(i)].width = min(max(10, mx + 2), 50)
        ws.freeze_panes = "A3"; ws.auto_filter.ref = f"A2:{get_column_letter(len(cols))}{max(2, len(rows)+2)}"
    b = io.BytesIO(); wb.save(b); return b.getvalue()


def to_csv(pid, name):
    cols, rows = datasets(pid)[name]
    s = io.StringIO(); w = csv.writer(s); w.writerow(cols); w.writerows(rows)
    return s.getvalue().encode("utf-8-sig")


def to_csv_zip(pid):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for name in datasets(pid):
            z.writestr(re.sub(r"[^A-Za-z0-9]+", "_", name) + ".csv", to_csv(pid, name))
    return b.getvalue()


def to_xml(pid, project_name=""):
    out = [f'<?xml version="1.0" encoding="UTF-8"?>\n<Project name="{escape(project_name)}">']
    for name, (cols, rows) in datasets(pid).items():
        tag = re.sub(r"[^A-Za-z0-9]+", "", name)
        out.append(f"  <{tag}>")
        keys = [re.sub(r"[^A-Za-z0-9]+", "", c) or "Col" for c in cols]
        for r in rows:
            out.append("    <Row>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in zip(keys, r)) + "</Row>")
        out.append(f"  </{tag}>")
    out.append("</Project>")
    return "\n".join(out).encode("utf-8")


def to_pdf(pid, project_name=""):
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    b = io.BytesIO(); doc = SimpleDocTemplate(b, pagesize=landscape(A3), leftMargin=20, rightMargin=20, topMargin=20, bottomMargin=20)
    st = getSampleStyleSheet(); small = st["BodyText"].clone("s"); small.fontSize = 6.5; small.leading = 8
    el = []
    for name, (cols, rows) in datasets(pid).items():
        el += [Paragraph(f"<b>{escape(project_name)} — {escape(name)}</b>", st["Heading2"]), Spacer(1, 6)]
        if not rows:
            el += [Paragraph("(no data)", st["BodyText"]), PageBreak()]; continue
        data = [[Paragraph(f"<b>{escape(c)}</b>", small) for c in cols]] + [[Paragraph(escape(str(v)), small) for v in r] for r in rows]
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDE8F3")), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        el += [t, PageBreak()]
    doc.build(el); return b.getvalue()
