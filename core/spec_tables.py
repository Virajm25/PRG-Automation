"""Everything printed as a table / list on general-arrangement, specification and legend sheets -> panel_specs rows.

* ruled tables (PANEL DETAILS, legend / cut-out schedules)   -> label | value rows, front-panel item schedule
* GENERAL SPECIFICATIONS ("NN LABEL : value", two columns)    -> section / item / value rows incl. wiring & terminal colour-coding tables
* INTERNAL GA mounting view                                    -> rows of mounted equipment, top to bottom, left to right
* EXTERNAL GA                                                  -> dimension call-outs
Rows use the panel_specs schema: sheet, category, property_name, property_value, unit, raw_text."""
import re
from collections import defaultdict

SKIP_TABLE = re.compile(r"REVISION|DRAWN BY|CHECKED|APPROVED|^0$", re.I)


def _row(sheet, cat, name, value, unit=""):
    name, value = re.sub(r"\s+", " ", str(name)).strip(" :"), re.sub(r"\s+", " ", str(value)).strip(" :")
    if not name or not value:
        return None
    return dict(sheet=sheet, category=cat, property_name=name, property_value=value, unit=unit, raw_text=f"{name}: {value}")


def _clean(c):
    return re.sub(r"\s+", " ", (c or "").replace("\n", " ")).strip()


def ruled_tables(page, sheet):
    out = []
    try:
        tables = page.find_tables()
    except Exception:
        return out
    for t in tables:
        rows = [[_clean(c) for c in r] for r in t.extract()]
        flat = " ".join(" ".join(r) for r in rows)
        if not rows or SKIP_TABLE.search(flat) or "TITLE:-" in flat or len(rows) < 2:
            continue
        head = " ".join(rows[0]).upper()
        if "SR. NO" in head or "SR.NO" in head or "CUTOUT" in head:
            out += _legend(rows, sheet)
        elif max(len(r) for r in rows) == 2:
            for a, b in rows:
                out.append(_row(sheet, "Panel Details", a, b))
        else:
            cols = [c for c in rows[0]]
            for r in rows[1:]:
                if any(r):
                    out.append(_row(sheet, "Table", r[0] or r[1], "; ".join(f"{h}: {v}" for h, v in zip(cols[1:], r[1:]) if v and h)))
    return [r for r in out if r]


def _legend(rows, sheet):
    """FEEDER NO | FEEDER NAME | RATING | SR. NO | CUTOUT DETAILS (22.5)  -> one row per front-panel item."""
    out = []
    m = re.search(r"CUTOUT DETAILS\s*\(\s*([\d.]+)\s*\)", " ".join(rows[0]), re.I)
    cut = m.group(1) if m else ""
    for k, r in enumerate(rows):
        r = r + [""] * (5 - len(r))
        sr, item = r[3], r[4]
        if k == 0:                           # header row also carries item 1 as "SR. NO. 1" / "R PHASE (RED)"
            hm = re.match(r"SR\.? ?NO\.?\s*(\d+)$", sr, re.I)
            sr = hm.group(1) if hm else ""
            item = re.sub(r"^CUTOUT DETAILS\s*\(\s*[\d.]+\s*\)\s*", "", item, flags=re.I)
        if not sr.isdigit() or not item:
            continue
        feeder = "" if k == 0 else " ".join(x for x in (r[0], r[1], r[2]) if x and x != "-")
        out.append(_row(sheet, "Front Panel Layout", f"Item {sr}", item + (f" - feeder {feeder}" if feeder else ""), f"cut-out {cut} mm" if cut else ""))
    return out


# ------------------------------------------------------------------------------------------------ general specifications
def _words(page):
    ws = page.extract_words(keep_blank_chars=False, use_text_flow=False, x_tolerance=1.5)
    return [w for w in ws if 22 < w["top"] < float(page.height) * 0.86]


def _gutter(ws, width):
    cover = [0] * (int(width) + 2)
    for w in ws:
        for x in range(int(w["x0"]), int(w["x1"]) + 1):
            cover[x] += 1
    best, run, start = (0, 0), 0, 0
    for x in range(int(width * 0.3), int(width * 0.7)):
        if cover[x] == 0:
            if run == 0:
                start = x
            run += 1
            if run > best[0]:
                best = (run, start + run // 2)
        else:
            run = 0
    return best[1] if best[0] >= 12 else None


def _lines(ws):
    rows = defaultdict(list)
    for w in sorted(ws, key=lambda w: (w["top"] + w["bottom"]) / 2):
        c = (w["top"] + w["bottom"]) / 2
        key = next((k for k in rows if abs(k - c) <= 4.5), c)
        rows[key].append(w)
    return [" ".join(x["text"] for x in sorted(v, key=lambda x: x["x0"])) for _, v in sorted(rows.items())]


def general_specs(page, sheet):
    ws = _words(page)
    if not ws:
        return []
    g = _gutter(ws, float(page.width))
    cols = [ws] if g is None else [[w for w in ws if w["x1"] <= g + 3], [w for w in ws if w["x0"] > g + 3]]
    out, section = [], "General"
    for ci, col in enumerate(cols):
        last_name = ""
        if ci == 1:
            section = "General"
        for line in _lines(col):
            line = line.replace("�", "").strip()
            m = re.match(r"^([A-E])\.\s+(.+)$", line)
            if m:
                section = m.group(2).title(); last_name = ""
                continue
            parts = [p.strip() for p in re.split(r"\s+:+\s*|\s*:+\s+", line) if p.strip()]
            m = re.match(r"^(\d{2})\s+(.*)$", line)
            if m and len(parts) >= 2:
                name = re.sub(r"^\d{2}\s+", "", parts[0])
                last_name = name
                out.append(_row(sheet, section, name, " : ".join(parts[1:])))
            elif m and not len(parts) >= 2:                       # heading with value on following lines ("03 WIRING COLOUR CODING")
                last_name = m.group(2).title()
                section = last_name
            elif len(parts) >= 2:
                out.append(_row(sheet, section, f"{last_name} - {parts[0]}" if last_name else parts[0], " : ".join(parts[1:])))
            elif last_name and out and line and not re.match(r"^(TITLE|SYSTEM|PROJECT)", line, re.I) and out[-1]["category"] == section:
                out[-1]["property_value"] += " " + line
                out[-1]["raw_text"] = f"{out[-1]['property_name']}: {out[-1]['property_value']}"
    return [r for r in out if r]


# ------------------------------------------------------------------------------------------------ internal / external GA
def _upright_words(page):
    up = page.filter(lambda o: o.get("object_type") != "char" or o.get("upright", True))
    return [w for w in up.extract_words(x_tolerance=1.5) if 22 < w["top"] < float(page.height) * 0.86]


def _rotated_words(page):
    from .geometry import _words as gw
    H = float(page.height)
    return [w for w in gw(page) if w["rot"] and 22 < w["top"] < H * 0.86]


def internal_layout(page, sheet):
    rows = defaultdict(list)
    for w in _upright_words(page):
        key = next((k for k in rows if abs(k - w["top"]) <= 4), w["top"])
        rows[key].append(w)
    out, n = [], 0
    for _, v in sorted(rows.items()):
        v.sort(key=lambda w: w["x0"])
        items, cur = [], [v[0]]
        for w in v[1:]:
            if w["x0"] - cur[-1]["x1"] > 9:
                items.append(" ".join(c["text"] for c in cur)); cur = []
            cur.append(w)
        items.append(" ".join(c["text"] for c in cur))
        items = [i for i in items if len(i) > 1 and not re.fullmatch(r"[\d ]+", i)]
        if items and not re.match(r"^(TITLE|SYSTEM|PROJE|OFFICE|VINAYAK|OPP|WWW|AQUACARESEE|INTERNAL GENERAL|MOUNTING VIEW|00|01|REVISION)", items[0]):
            n += 1
            out.append(_row(sheet, "Internal Layout", f"Row {n} (top to bottom, left to right)", " | ".join(items)))
    rot = sorted(_rotated_words(page), key=lambda w: w["xc"])
    names = [w["text"] for w in rot if len(w["text"]) > 1]
    if names:
        out.append(_row(sheet, "Internal Layout", "Vertical labels (ducts, wire/terminal marks), left to right", " | ".join(names)))
    return [r for r in out if r]


def dimension_callouts(page, sheet, table_text=""):
    known = set(re.findall(r"\d+", table_text))
    nums = {w["text"] for w in _upright_words(page) + _rotated_words(page) if re.fullmatch(r"[1-9]\d{1,3}", w["text"]) and int(w["text"]) >= 50 and w["text"] not in known}
    nums = sorted(nums, key=int, reverse=True)
    return [r for r in [_row(sheet, "Dimension Call-outs", "Dimensions shown on the views (mm)", ", ".join(nums), "mm")] if nums]


TOC_LINE = re.compile(r"^/?(\d{1,3}(?:\.[a-z])?)\s+(.+?)(?:\s+(\d{2}-\d{2}-\d{4}))?(?:\s+([A-Z][A-Z.]+))?\s*X?$")


def toc_rows(text, sheet):
    """EPLAN 'Table of contents' lines: /N description date editor -> Drawing Index rows."""
    out = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        m = TOC_LINE.match(line) if line.startswith("/") else None
        if m and len(m.group(2)) > 3:
            out.append(_row(sheet, "Drawing Index", f"Page /{m.group(1)}", m.group(2), " ".join(x for x in (m.group(3), m.group(4)) if x)))
    return [r for r in out if r]


def extract_all(page, sheet, draw_type, text=""):
    rows = []
    if draw_type in ("external_ga", "internal_ga", "general_specs", "feeder_legend") or "PANEL DETAILS" in text.upper():
        rows += ruled_tables(page, sheet)
    tt = " ".join(r["property_name"] + " " + r["property_value"] for r in rows)
    if draw_type == "table_of_contents":
        rows += toc_rows(text, sheet)
    if draw_type == "general_specs":
        rows += general_specs(page, sheet)
    if draw_type == "internal_ga":
        rows += internal_layout(page, sheet)
    if draw_type == "external_ga":
        rows += dimension_callouts(page, sheet, tt)
    return rows
