"""Generic PLC I/O channel extractor for EPLAN-style wiring sheets.

Strategy (deterministic first, LLM only as helper):
  1. Detect I/O module sheets from the title / module header text.
  2. Find channel anchors (PLC address row) -> column x positions.
  3. Find the label band (lowest text band above the title block), cluster it into columns,
     map each column to nearest channel anchor.
  4. Split each column into TAG / DESCRIPTION / REMARK.
Layout differences between PLC families (Siemens ET200SP, Schneider M340, Delta DVP ...)
are absorbed by the generic column logic rather than per-vendor parsers.
"""
import re
from .pdfio import upright_chars, cluster_lines, join_chars_line, rotated_strings, ocr_to_chars

ADDR_RE = re.compile(r"^%?[IQ]\d+\.\d+$")
ADDR_SIMPLE_RE = re.compile(r"^%?[IQ]\d+$")
ANALOG_ADDR_RE = re.compile(r"^%?(PIW|PQW|IW|QW|AIW|AQW|PEW|PAW)\d+\+?$")
SCH_ROT_ADDR = re.compile(r"^%?[IQ][O0]?\d*:\d+/\d+$")
MODEL_RE = re.compile(r"(MLFB-[A-Z0-9-]+|6ES7[\w-]+|6AV\w+|BMX[A-Z0-9]+|DVP[A-Z0-9-]+|TM\d+[A-Z0-9]+|IC\w+)")
TAG_RE = re.compile(r"^[A-Za-z0-9/().+_\- ]{3,34}$")
STRIP_RE = re.compile(r"^(X|TB|XT)-?[A-Z]{0,3}\d*[A-Z]?\d*$")

MODULE_TYPES = [  # order matters
    (re.compile(r"\bTC\b|THERMOCOUPLE", re.I), "TC"),
    (re.compile(r"\bRTD\b", re.I), "RTD"),
    (re.compile(r"ANALOG\s+OUTPUT|\bAO\b", re.I), "AO"),
    (re.compile(r"ANALOG\s+INPUT|\bAI\b", re.I), "AI"),
    (re.compile(r"DIGITAL\s+OUTPUT|\bDO\b", re.I), "DO"),
    (re.compile(r"DIGITAL\s+INPUT|\bDI\b", re.I), "DI"),
]
MODULE_HDR = re.compile(r"((?:DIGITAL|ANALOG)[A-Z ]{0,12}(?:INPUT|OUTPUT)[A-Z ]{0,8}MODULES?\s*-?\s*\d*)", re.I)


def classify_module(text):
    head = text[:1500].upper()
    m = re.search(r"TITLE[:\-\s]*([^\n]*)", head)
    probe = (m.group(1) if m else "") + " " + " ".join(MODULE_HDR.findall(head))
    for rx, t in MODULE_TYPES:
        if rx.search(probe):
            return t
    return None


def is_io_sheet(text):
    t = text.upper()
    if "TABLE OF CONTENTS" in t or "GENERAL ARRANGEMENT" in t or "SYSTEM ARCHITECTURE" in t:
        return False
    m = re.search(r"TITLE[:\-\s]*([^\n]*)", t)
    title = m.group(1) if m else ""
    # an I/O sheet has module wording in its title or module header AND a model/rack marker
    has_mod = bool(re.search(r"(DIGITAL|ANALOG)\s+[A-Z ]*(INPUT|OUTPUT)\s+(MODULE|WIRING)|\b\d+\s?(DI|DO|AI|AO)\b.*WIRING|\b(DI|DO|AI|AO|RTD|TC)\b.*WIRING", title + " " + t[:800]))
    return has_mod or ("CPU" in title and ("INPUT" in title or "OUTPUT" in title))


def parse_header(text):
    h = {}
    m = MODULE_HDR.search(text)
    h["module_name"] = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    mm = MODEL_RE.search(text)
    h["model"] = mm.group(1) if mm else ""
    mr = re.search(r"RACK\s*:?\s*(\d+)\s*SLOT\s*:?\s*(\d+)", text, re.I)
    h["rack"], h["slot"] = (mr.group(1), mr.group(2)) if mr else ("", "")
    mv = re.search(r"(\d+)\s*(?:x|X)?\s*(24\s*V\s*DC|24VDC|230\s*V\s*AC|4-?20\s*mA|0-?10\s*V)", text)
    h["signal"] = re.sub(r"\s+", " ", mv.group(2)).replace("24VDC", "24 VDC").replace("24 V DC", "24 VDC") if mv else ""
    ma = re.search(r"(\d+)\s*(DI|DO|AI|AO)\b", text)
    h["channels"] = int(ma.group(1)) if ma else None
    return h


def _anchor_rows(chars):
    """Find the single row of channel addresses: returns list of (x_center, text) sorted by x."""
    rows = {}
    for c in chars:
        pass
    lines = cluster_lines(chars, 3.0)
    best = []
    for L in lines:
        words = _words_in_line(L["chars"])
        a = [(x, t) for x, t in words if ADDR_RE.match(t)]
        if len(a) > len(best):
            best = a
    if len(best) >= 2:
        return sorted(best)
    # Schneider style: bare I0, I1 ...
    for L in lines:
        words = _words_in_line(L["chars"])
        a = [(x, t) for x, t in words if ADDR_SIMPLE_RE.match(t)]
        if len(a) > len(best):
            best = a
    return sorted(best) if len(best) >= 2 else []


def _words_in_line(chars, gap=2.2):
    chars = sorted(chars, key=lambda c: c["x0"])
    words, cur = [], []
    for c in chars:
        if cur and c["x0"] - cur[-1]["x1"] > gap:
            words.append(cur)
            cur = []
        cur.append(c)
    if cur:
        words.append(cur)
    return [((w[0]["x0"] + w[-1]["x1"]) / 2, "".join(c["text"] for c in w)) for w in words]


REF_LINE = re.compile(r"^[A-Za-z0-9+\-_ ]{0,12}/\s?\d+\.\d+(\s|$)|^\d+\.\d+(\s+\d+\.\d+)*$|^[A-Z]\d+\s+[A-Z]?\d+\s*/")
WIRING_LINE_RE = re.compile(
    r"^(\d+[A-Z]-[A-Z0-9]+|\d+\.\d+|[A-Z0-9/.\-+_ ]*/\d+\.\d+|[A-Z]\d+ [A-Z]?\d*/"
    r"|O\d C\d P\d|R\d|\+?\d+\s*-\d+|TB-?[A-Z]+|24\s*V\s*D?C?|[LM]\+?/[0-9.]+|[0-9.]*/[LM]"
    r"|24VDC\s+CONTROL\s+SUPPLY).*$", re.I
)
TAG_PAT = re.compile(r"^(Z[SO]|X[AT]|LS[LH]|CS-|DOM-|TDS-|LT-|PT-|SPARE|[A-Z]{2,4}-[A-Z0-9]+-[A-Z0-9]+)")


def _label_band(chars, page_h, left_gutter, band_gap=20, is_analog=False):
    """Return chars of the text band containing TAG / DESC rows.
    For analog sheets, top band (top < 0.35 * page_h) is checked first."""
    y_max = page_h * 0.882
    pool = [c for c in chars if c["x0"] > left_gutter and c["top"] < y_max]

    if is_analog:
        top_pool = [c for c in pool if 50 <= c["top"] < page_h * 0.35 and c["x0"] > 350]
        if len(top_pool) >= 8:
            lines = cluster_lines(top_pool, 5.0)
            valid = [L for L in lines if not REF_LINE.match(join_chars_line(L["chars"]).strip())
                     and not WIRING_LINE_RE.match(join_chars_line(L["chars"]).strip())]
            if valid:
                return [c for L in valid for c in L["chars"]]

    low = [c for c in pool if c["top"] > page_h * 0.45]
    if low:
        sizes = sorted(c.get("size", 8) for c in low)
        ref = sizes[int(len(sizes) * 0.75)]
        pool = [c for c in pool if c.get("size", 8) >= ref * 0.80]
    lines = [L for L in cluster_lines(pool, 5.0)
             if not REF_LINE.match(join_chars_line(L["chars"]).strip())
             and not WIRING_LINE_RE.match(join_chars_line(L["chars"]).strip())
             and len(join_chars_line(L["chars"]).replace(" ", "")) > 2]
    if not lines:
        top_pool = [c for c in chars if 50 <= c["top"] < page_h * 0.35 and c["x0"] > 350]
        if len(top_pool) >= 8:
            tlines = cluster_lines(top_pool, 5.0)
            return [c for L in tlines for c in L["chars"]]
        return []
    lines.sort(key=lambda L: -L["y"])
    band = [lines[0]]
    for L in lines[1:]:
        if band[-1]["y"] - L["y"] <= band_gap:
            band.append(L)
        else:
            break
    return [c for L in band for c in L["chars"]]


def _cluster_columns(chars, min_gap=12):
    if not chars:
        return []
    iv = sorted((c["x0"], c["x1"]) for c in chars)
    groups = [[iv[0][0], iv[0][1]]]
    for a, b in iv[1:]:
        if a - groups[-1][1] > min_gap:
            groups.append([a, b])
        else:
            groups[-1][1] = max(groups[-1][1], b)
    return groups


def _is_tag(line):
    s = line.strip()
    if TAG_PAT.match(s):
        return True
    if not TAG_RE.match(s) or ("-" not in s and not re.search(r"\d", s)):
        return False
    if re.fullmatch(r"\(?\d+\s*WIRE\)?", s, re.I):
        return False
    return len(s.split()) <= 2


def _split_label(lines, tag_row_y=None, desc_row_y=None):
    """lines: list of (y, text) -> (tag, description, remark)."""
    tag, desc, rem = "", [], []
    tag_idx = None
    if tag_row_y is not None and lines:
        i = min(range(len(lines)), key=lambda k: abs(lines[k][0] - tag_row_y))
        if abs(lines[i][0] - tag_row_y) <= 14 and (desc_row_y is None or abs(lines[i][0] - tag_row_y) < abs(lines[i][0] - desc_row_y)):
            tag_idx = i
    for i, (y, t) in enumerate(lines):
        t_clean = t.strip()
        if re.fullmatch(r"\(?\s*\d\s*-?\s*WIRE\s*\)?", t_clean, re.I):
            rem.append(t_clean.strip("() "))
        elif i == tag_idx:
            tag = t_clean
        elif TAG_PAT.match(t_clean) and not tag:
            tag = t_clean
        else:
            desc.append(t_clean)
    if not tag and desc and _is_tag(desc[-1]):
        tag = desc.pop()
    return tag.strip(), " ".join(desc).strip(), "; ".join(rem)


JUNK_LABEL = re.compile(r"^(SUPPLY|\d+\s*(AI|AO|DI|DO)|ANALOG\s+(INPUT|OUTPUT)S?|DIGITAL\s+(INPUT|OUTPUT)S?|MLFB-.*|[A-Z]{1,3}\d?[+-]?)$", re.I)


def _find_delta_addrs(chars):
    DELTA_RE = re.compile(r"^[XY]\d{1,2}$")
    for L in cluster_lines(chars, 3.0):
        ws = _words_in_line(L["chars"])
        deltas = [(xx, tt) for xx, tt in ws if DELTA_RE.match(tt)]
        if len(deltas) >= 4:
            return deltas
    return []


def extract_io_sheet(page, text, sheet_no, ocr=False):
    """Returns (module_info, rows). rows: dicts of channel records."""
    mtype = classify_module(text)
    if not mtype:
        return None, []
    hdr = parse_header(text)
    if re.match(r"6ES7-?15[0-3]", hdr.get("model", "")) or re.search(r"\bIM\s?15\d", text):
        return None, []          # interface module, not an I/O module
    chars = upright_chars(page) if not ocr else ocr_to_chars(page)
    page_h = float(getattr(page, "height", 842))
    left_gutter = 170

    anchors = _anchor_rows(chars)
    delta_addrs = _find_delta_addrs(chars)
    rot = rotated_strings(page) if not ocr else []
    rot_addr = [r for r in rot if ANALOG_ADDR_RE.match(r["text"]) or SCH_ROT_ADDR.match(r["text"])]

    is_analog = mtype in ("AI", "AO", "RTD", "TC")
    # For analog modules without upright anchor row, rotated strings (PIW300+) serve as anchors
    if not anchors and rot_addr:
        anchors = [(r["x"], r["text"].rstrip("+")) for r in rot_addr]
        anchors.sort(key=lambda a: a[0])

    band = _label_band(chars, page_h, left_gutter, is_analog=is_analog)

    tag_y = desc_y = None
    for L in cluster_lines([c for c in chars if c["x0"] < left_gutter and c["x0"] > 100], 4):
        s = join_chars_line(L["chars"]).replace(" ", "").upper()
        if s.startswith("TAG"):
            tag_y = L["y"]
        elif s.startswith("DESC"):
            desc_y = L["y"]

    strip_name, strip_words = _find_strip(chars)

    # Pitch calculation
    pitch = 100
    if len(anchors) >= 2:
        diffs = [b[0] - a[0] for a, b in zip(anchors, anchors[1:])]
        pitch = sorted(diffs)[len(diffs) // 2]

    # Check for tag row to perform tag-aligned column assignment
    band_lines = cluster_lines(band, 4.0)
    band_lines = [L for L in band_lines if not WIRING_LINE_RE.match(join_chars_line(L["chars"]).strip())]
    all_tags = []
    for L in band_lines:
        ws = _words_in_line(L["chars"])
        for wx, wt in ws:
            if TAG_PAT.match(wt):
                all_tags.append((wx, L["y"], wt))

    all_tags.sort(key=lambda t: t[0])
    use_tag_centers = len(all_tags) == len(anchors) and len(anchors) >= 2
    col_centers = [t[0] for t in all_tags] if use_tag_centers else [a[0] for a in anchors]

    ch_words = {i: [] for i in range(len(anchors))}
    for L in band_lines:
        ws = _words_in_line(L["chars"])
        for wx, wt in ws:
            if not col_centers:
                continue
            best_i = min(range(len(col_centers)), key=lambda idx: abs(col_centers[idx] - wx))
            if abs(col_centers[best_i] - wx) <= pitch * 0.58:
                ch_words[best_i].append((L["y"], wt))

    channels = []
    for i in range(len(anchors)):
        words = ch_words.get(i, [])
        cur_y = None
        cur_line = []
        glines = []
        for y, wt in sorted(words, key=lambda item: (round(item[0]/4)*4, item[0])):
            yb = round(y / 4) * 4
            if cur_y is None or abs(yb - cur_y) > 2:
                if cur_line: glines.append((cur_y, ' '.join(cur_line)))
                cur_y = yb
                cur_line = [wt]
            else:
                cur_line.append(wt)
        if cur_line: glines.append((cur_y, ' '.join(cur_line)))

        tag, desc, rem = _split_label(glines, tag_y, desc_y)
        channels.append(dict(cx=col_centers[i] if col_centers else anchors[i][0], tag=tag, desc=desc, rem=rem))

    # attach addresses
    out = []
    for i, (ax, atxt) in enumerate(anchors):
        ch = channels[i] if i < len(channels) else None
        # Delta address overlay
        d_addr = ""
        if delta_addrs:
            near_d = [dt for dx, dt in delta_addrs if abs(dx - ax) <= pitch * 0.45]
            if near_d:
                d_addr = near_d[0]
        final_addr = atxt
        if d_addr and atxt and d_addr != atxt:
            final_addr = f"{atxt} ({d_addr})"
        elif d_addr and not atxt:
            final_addr = d_addr

        out.append(_mk_row(mtype, hdr, sheet_no, final_addr, ch, strip_name, strip_words, ax, pitch, rot_addr))

    _attach_links(out, anchors, chars, pitch)
    cleaned = []
    for r in out:
        lab = (r["description"] or r["tag"]).strip()
        if JUNK_LABEL.match(lab) and not r["plc_address"]:
            continue
        if JUNK_LABEL.match(r["description"].strip()) and r["description"].strip().upper() in ("SUPPLY",):
            r["description"] = ""
            r["spare"] = not r["tag"]
        if not anchors and not r["plc_address"] and not r["tag"] and not r["description"]:
            continue
        if r["spare"] and not r["tag"] and r["plc_address"]:
            r["tag"] = "SPARE_" + r["plc_address"].rstrip("+")
        cleaned.append(r)
    out = cleaned
    mod = dict(sheet=sheet_no, type=mtype, **hdr)
    return mod, out


LINK_RE = re.compile(r"^(?:[A-Za-z0-9+\-_]{1,8}/)?(\d{1,3}\.\d)$")
CIRC_WIRE_RE = re.compile(r"^\d+F-\d{2,4}$|^\d+F-[RYBN]\d?$")
CIRC_DEV_RE = re.compile(r"^\d+F-(?:C\d?|OLR|MCB|ILSW\d?|K\d+|R\d+)$")
MOD_TERM_RE = re.compile(r"^(?:X\d{1,2}|Y\d{1,2}|C\d{1,2}|O\d{1,2}|L\+\d?|S/S|COM\d?|V\d\+|I\d\+|ZP\d|UP|[AB]\d|24V[+-]|M\d?|IN\d+|OUT\d+)$")


FIELD_TAG_RE = re.compile(r"^[A-Z]{1,5}\d?-[A-Z]{2,5}-[A-Z]?\d{1,3}$")    # LSL7-TWT-10, LT-MBT-01, ZSO-POV-V1


def _attach_links(rows, anchors, chars, pitch):
    """Per channel column: module terminal label, circuit-location links (e.g. 17.7 = sheet.column where the signal is used) and wire numbers."""
    if not rows or not anchors or not pitch:
        return
    words = []
    for L in cluster_lines(chars, 3.5):
        words += _words_in_line(L["chars"])
    for r, (ax, _a) in zip(rows, anchors):
        col = [t for x, t in words if abs(x - ax) <= pitch * 0.5]
        links = sorted({LINK_RE.match(t).group(1) for t in col if LINK_RE.match(t)})
        wires = sorted({t for t in col if CIRC_WIRE_RE.match(t)})
        mods = [t for t in col if MOD_TERM_RE.match(t)]
        devs = sorted({t for t in col if CIRC_DEV_RE.match(t)})
        bits = []
        if mods:
            bits.append("module terminal " + "/".join(dict.fromkeys(mods)))
        if links:
            bits.append("used at " + ", ".join(links))
        if wires:
            bits.append("wire " + ", ".join(wires))
        if devs:
            bits.append("via " + ", ".join(devs))
        ftags = [t for t in dict.fromkeys(col) if FIELD_TAG_RE.match(t)]
        if r.get("spare") and ftags:
            # the label band says SPARE but a field device is drawn and wired to this channel - a drawing inconsistency
            bits.append(f"labelled SPARE on the drawing but field device {', '.join(ftags)} is wired to this channel - verify")
            r["field_device"] = ftags[0]
            r["_spare_conflict"] = True
        if bits:
            r["remarks"] = (r["remarks"] + "; " if r["remarks"] else "") + "; ".join(bits)


def _pitch(xs):
    xs = sorted(xs)
    if len(xs) < 2:
        return None
    d = sorted(b - a for a, b in zip(xs, xs[1:]))
    return d[len(d) // 2]


def _nearest(chs, x, tol):
    best = None
    for ch in chs:
        dx = abs(ch["cx"] - x)
        if dx <= tol and (best is None or dx < abs(best["cx"] - x)):
            best = ch
    return best


def _nearest_rot(rots, x, tol):
    best = None
    for r in rots:
        dx = abs(r["x"] - x)
        if dx <= tol + 12 and (best is None or dx < abs(best["x"] - x)):
            best = r
    return best


def _find_strip(chars):
    """Find terminal strip name (TBDI / TB-DO / TBAI / X-DI1 / TB-DI1) and terminal digits."""
    lines = cluster_lines(chars, 3.5)
    lines.sort(key=lambda L: L["y"])
    for i, L in enumerate(lines):
        ws = _words_in_line(L["chars"])
        for x, t in ws:
            if re.match(r"^(TB-?[A-Z0-9]+|X-?[A-Z0-9]+|TBAI|TBDI|TBDO)$", t, re.I):
                digits = [(xx, tt) for xx, tt in ws if tt.isdigit() or re.match(r"^\d+-\d+$", tt)]
                if len(digits) >= 2:
                    return t, digits
                for sub_L in lines[i+1:i+3]:
                    sub_ws = _words_in_line(sub_L["chars"])
                    sub_digits = [(xx, tt) for xx, tt in sub_ws if tt.isdigit()]
                    if len(sub_digits) >= 2:
                        return t, sub_digits
    return "", []


def _mk_row(mtype, hdr, sheet, addr, ch, strip_name, strip_words, ax, pitch, rot_addr):
    tag = ch["tag"] if ch else ""
    desc = ch["desc"] if ch else ""
    rem = ch["rem"] if ch else ""
    # terminals under this column
    term = ""
    if strip_name and pitch:
        near = [t for x, t in strip_words if abs(x - ax) <= pitch * 0.5]
        if near:
            term = f"{strip_name}:" + ",".join(near)
    if not addr and rot_addr:
        r = _nearest_rot(rot_addr, ax, (pitch or 100) * 0.6)
        addr = r["text"] if r else ""
    spare = (not tag and not desc) or bool(re.fullmatch(r"(SPARE|RESERVED?)", (desc or tag).strip(), re.I))
    return dict(sheet=sheet, module_type=mtype, plc_address=addr, tag=tag, description=desc,
                terminal=term, remarks=rem, spare=spare, module_model=hdr.get("model", ""),
                signal_type=hdr.get("signal", ""), module_name=hdr.get("module_name", ""),
                rack=hdr.get("rack", ""), slot=hdr.get("slot", ""))
