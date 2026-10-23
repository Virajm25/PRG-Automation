"""Terminal-detail sheets ("TERMINAL DETAILS ... IN PANEL | ON FIELD") -> terminal schedule, ferrules and field cables.

Layout of these sheets: several blocks side by side, each with an `IN PANEL ON FIELD` caption; a block title with the strip name
(`POWER TERMINAL (5F-X0)`, `DIGITAL INPUT (TBDI-6)`, `4F-TB0`), the cable size (`2.5SQMM`), then one row per terminal:
    [in-panel wire no | PLC address]  [terminal no]  [core colour]  [field description]
Everything is located by word coordinates, so wrapped/interleaved text in a neighbouring block does not leak across columns.
"""
import re
from collections import defaultdict
from .devices import _words
from .pdfio import upright_chars

COLOURS = {"RD", "BK", "BU", "BL", "YE", "YL", "GY", "GN", "GR", "OG", "WH", "BN", "VT", "PE", "PK", "GNYE", "OR"}
PLC_ADDR = re.compile(r"^(?:[IQ]\d+\.\d|%?[IQ]W?\d+(?:\.\d+)?|P[IQ]W\d+[+-]?|I\d:\d+/\d+|O\d:\d+/\d+|L\+\d?|M\d+\.\d|A[IQ]\d+)$", re.I)
SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s?SQ\.?MM$", re.I)
SIZE_WORDS = re.compile(r"^(\d+(?:\.\d+)?)\s?SQ\.?$", re.I)
STRIP_PAREN = re.compile(r"\(([A-Z0-9][A-Z0-9\-/+.]*\d[A-Z0-9\-/+.]*)\)?$", re.I)
STRIP_BARE = re.compile(r"^(?:\d+F-)?(?:TB[A-Z0-9-]*|X[A-Z0-9-]*|XDI\d*|XDO\d*|X-TB)$", re.I)
CABLE_TAG = re.compile(r"^(?!TB|X)(?:[A-Z]{2,3}-\d{2}|[A-Z]{3}-[A-Z]{2}\d{1,2})$")
GOOD_WIRE = re.compile(r"^[A-Za-z0-9/*+\-.:]{1,14}$")
WIRE = re.compile(r"^[A-Z0-9][A-Z0-9/*+\-.]*$", re.I)
DIGITS = re.compile(r"^\d{1,3}$")


def is_terminal_detail(text):
    t = text.upper()
    return "TERMINAL DETAILS" in t and "IN PANEL" in t


def _near(a, b, dy=3.5):
    return abs(a["top"] - b["top"]) <= dy


def extract_terminal_detail(page, sheet_no, chars=None):
    """Returns (terminals, cables, ferrules) for one sheet."""
    chars = chars if chars is not None else upright_chars(page)
    H = float(getattr(page, "height", 842))
    ws = [w for w in _words(chars) if w["top"] < H * 0.90]
    # block columns start at each 'IN' of 'IN PANEL'
    starts = sorted({round(w["x0"]) for i, w in enumerate(ws) if w["text"] == "IN" and any(v["text"].startswith("PANEL") and _near(w, v) and 0 < v["x0"] - w["x1"] < 8 for v in ws)})
    merged = []
    for x in starts:
        if not merged or x - merged[-1] > 60:
            merged.append(x)
    if not merged:
        return [], [], []
    bands = [(x - 6, (merged[i + 1] - 6) if i + 1 < len(merged) else 1e9) for i, x in enumerate(merged)]
    terminals, cables, ferrules = [], [], []
    for lo, hi in bands:
        col = sorted([w for w in ws if lo <= w["x0"] < hi], key=lambda w: (w["top"], w["x0"]))
        _band(col, sheet_no, terminals, cables, ferrules)
    return terminals, cables, ferrules


def _headers(col):
    hs = []
    for w in col:
        t = w["text"]
        m = STRIP_PAREN.search(t)
        if m and "(" in t:
            hs.append((w, m.group(1).upper()))
        elif STRIP_BARE.match(t) and (not DIGITS.match(t)):
            hs.append((w, t.upper()))
    return hs


def _band(col, sheet_no, terminals, cables, ferrules):
    hs = _headers(col)
    if not hs:
        return
    # terminal-number tokens: digits with a wire/address token to the left, or wire token to the right (incoming terminal style)
    cand = []
    for w in col:
        if not DIGITS.match(w["text"]):
            continue
        left = [v for v in col if _near(v, w) and 0 < w["x0"] - v["x1"] < 70 and v["x1"] <= w["x0"] + 0.5]
        left = [v for v in left if GOOD_WIRE.match(v["text"]) and not re.search(r"SQ|WIRE|,$", v["text"], re.I)]
        left.sort(key=lambda v: -v["x1"])
        right = [v for v in col if _near(v, w) and 0 < v["x0"] - w["x1"] < 30 and GOOD_WIRE.match(v["text"]) and not re.search(r"SQ|WIRE|,$", v["text"], re.I)]
        if left or right:
            cand.append((w, left[0] if left else None, right))
    # strip each candidate belongs to: nearest header above
    blocks = defaultdict(list)
    for w, left, right in cand:
        above = [h for h in hs if h[0]["top"] <= w["top"] + 1]
        if not above:
            continue
        h = max(above, key=lambda h: h[0]["top"])
        if w["top"] - h[0]["top"] > 400:
            continue
        blocks[(h[1], round(h[0]["top"]))].append((w, left, right))
    for (strip, htop), rows in blocks.items():
        hdr = next(h[0] for h in hs if h[1] == strip and round(h[0]["top"]) == htop)
        nxt = [h[0]["top"] for h in hs if h[0]["top"] > hdr["top"] + 2]
        ytop, ybot = hdr["top"] - 12, (min(nxt) - 12 if nxt else 1e9)
        zone = [w for w in col if ytop <= w["top"] < ybot]
        rows.sort(key=lambda r: r[0]["top"])
        # consecutive-numbering filter: keep a plausible 1..n sequence only
        seq, last = [], 0
        for r in rows:
            n = int(r[0]["text"])
            if n == last + 1 or (not seq and n <= 3):
                seq.append(r); last = n
            elif n > last and n <= last + 2:
                seq.append(r); last = n
        rows = seq or rows
        size = next((SIZE.match(w["text"]).group(1) for w in zone if SIZE.match(w["text"])), "")
        if not size:
            sw = [w for w in zone if SIZE_WORDS.match(w["text"])]
            size = SIZE_WORDS.match(sw[0]["text"]).group(1) if sw else ""
        cable_tag = next((w["text"] for w in zone if CABLE_TAG.match(w["text"]) and not PLC_ADDR.match(w["text"])), "")
        colours = [w for w in zone if w["text"].upper() in COLOURS]
        used = set()
        tnums = {id(r[0]) for r in rows}
        io_block = any(left is not None and PLC_ADDR.match(left["text"]) for _, left, _ in rows)
        desc_pool = [w for w in zone if id(w) not in tnums and w["text"].upper() not in COLOURS and not SIZE.match(w["text"])
                     and not (w is hdr) and not WIRE_ONLY(w) and w["text"] != strip and "(" not in w["text"]
                     and w["text"] not in ("IN", "PANEL", "ON", "FIELD") and not re.match(r"^(POWER|DIGITAL|ANALOG|INPUT|OUTPUT|TERMINAL|INCOMING)$", w["text"], re.I)]
        first_cols = []
        for k, (w, left, right) in enumerate(rows):
            lt = left["text"] if left else ""
            plc = lt if PLC_ADDR.match(lt) else ""
            inwire = "" if plc else lt
            if not left and right:
                inwire = right[0]["text"]
            # core colour: nearest colour token to the right of the number, same row or slightly above
            best, bd = None, 1e9
            for c in colours:
                if id(c) in used:
                    continue
                dx, dy = c["x0"] - w["x1"], c["top"] - w["top"]
                if -14 <= dx <= 150 and -9 <= dy <= 5:
                    d = abs(dy) * 1.5 + abs(dx) * 0.2
                    if d < bd:
                        best, bd = c, d
            if best is not None:
                used.add(id(best))
            nxt_top = rows[k + 1][0]["top"] if k + 1 < len(rows) else w["top"] + 14
            if io_block:
                dsc = [d for d in desc_pool if d["x0"] > w["x1"] + 4 and w["top"] - 4 <= d["top"] < nxt_top - 4]
                dsc.sort(key=lambda d: (round(d["top"] / 3), d["x0"]))
                desc = " ".join(d["text"] for d in dsc if not CABLE_TAG.match(d["text"]))
            else:
                desc = ""
            terminals.append(dict(strip=strip, terminal_no=w["text"], terminal_type="Power" if not io_block else "Signal",
                                  incoming_wire=plc or inwire, outgoing_wire=(desc if io_block else ""), cable_no="", core_no=best["text"].upper() if best else "",
                                  signal_tag="", plc_address=plc, spare="SPARE" if re.search(r"SPARE", desc, re.I) else "", sheet=sheet_no,
                                  _in_wire=inwire, _desc=desc, _y=w["top"]))
            first_cols.append(w)
        # block-level destination for power / control strips
        dest = ""
        if not io_block:
            ds = sorted([d for d in desc_pool if d["x0"] > min(r[0]["x1"] for r in rows) + 4 and d["top"] >= rows[0][0]["top"] - 6],
                        key=lambda d: (round(d["top"] / 3), d["x0"]))
            dest = " ".join(d["text"] for d in ds)[:80]
        cno = cable_tag or f"W-{strip}"
        ccount = len(rows)
        cols = [t["core_no"] for t in terminals if t["strip"] == strip and t["sheet"] == sheet_no and t["core_no"]]
        for t in terminals:
            if t["strip"] == strip and t["sheet"] == sheet_no and t["cable_no"] == "":
                t["cable_no"] = cno
                if not io_block:
                    t["outgoing_wire"] = (dest or "") + (f" [{t['core_no']}]" if t["core_no"] else "")
        cables.append(dict(cable_no=cno, cable_type=f"Control/power cable{', ' + size + ' sq.mm' if size else ''}",
                           cores=f"{ccount}C{' x ' + size + ' sq.mm' if size else ''}" + (f" ({'/'.join(cols)})" if cols else ""),
                           source_equipment=f"Panel terminal strip {strip}", dest_equipment=dest or ("PLC / field devices (" + strip + ")" if io_block else ""),
                           length_m="", sheet=sheet_no,
                           remarks="read from terminal-detail sheet" + ("" if cable_tag else "; cable no. generated - verify"), source="drawing" if cable_tag else "terminal-sheet"))
        for t in terminals:
            if t["strip"] == strip and t["sheet"] == sheet_no and t.get("_in_wire") is not None and not t.get("_done"):
                t["_done"] = True
                if t["_in_wire"] or t["plc_address"]:
                    ferrules.append(dict(sheet=sheet_no, wire_no=t["_in_wire"] or t["plc_address"], ferrule_no=t["_in_wire"] or t["plc_address"],
                                         from_terminal=f"{strip}:{t['terminal_no']}",
                                         to_terminal=(f"PLC {t['plc_address']}" if t["plc_address"] else (t["_desc"] or dest or "field")),
                                         device_ref=strip, cable_ref=cno, core_no=t["core_no"], to_ref="", source="terminal-sheet"))
    for t in terminals:
        for k in ("_in_wire", "_desc", "_y", "_done"):
            t.pop(k, None)


def WIRE_ONLY(w):
    t = w["text"]
    return bool(re.match(r"^[A-Z0-9]{1,4}F?-[A-Z]\d*$|^[A-Z]\d?-\d+F$|^\d{3,4}$|^[IQ]\d", t, re.I)) and len(t) <= 10 and not re.search(r"[A-Z]{4,}", t)
