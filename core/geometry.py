"""Symbol recognition and wire tracing on vector schematic sheets.

* Wires: the solid, axis-aligned line set of the dominant wire colour (EPLAN draws wires in their own colour); monochrome sheets fall back to
  every long solid axis-aligned line.  Segments whose end touches another segment form one net (T-junctions connect, plain crossings do not).
* Pins: small-font tokens (A1, A2, 13, 14, 1/L1 ...) sitting next to a free net end; the device is the nearest device tag.
* Symbols: every device tag with its pins, rating and printed cross-reference locations (`/15.5`), classified as coil / contact / main poles /
  protective device / lamp / switch from the pin labels and the tag class.
* Wire numbers: wire-number tokens (upright or rotated) lying on a net.
Output rows feed the ferrule list, device list and the coil/contact cross reference.
"""
import re
from collections import defaultdict, Counter
from .devices import classify

TAG = re.compile(r"^(?:\d+[A-Z]?(?:/\d+[A-Z]?)?-[A-Z]{1,5}\d{0,2}|[A-Z]{1,4}\d{1,3}|[A-Z]{1,3}-\d{1,3}|\d+[A-Z]-[A-Z]{1,3}\d?)$")
WIRE_NO = re.compile(r"^(?:\d+[A-Z]?-[RYBN]?\d{1,4}[A-Z]?|\d+[A-Z]-[RYBN]\d?|[RYBN]\d?-\d+[A-Z]?|[A-Z]\d?-\d+F|\d{3,4})$")
PIN = re.compile(r"^(?:A[12]|X[12]|[1-9]\d?|[1-9]\d?/[LT]\d|[LT]\d|PE|[UVW]1|N|P1|O1|P2|O2|L\+|L-|[+-]\d|\d{1,2}[AB])$")
XREF = re.compile(r"^/(\d{1,3})\.(\d)$")
WIRE_COL_SIZE = re.compile(r"^(RD|YE|YL|BL|BU|BK|GY|GN|WH|OG|BN|VT|PE)/(\d+(?:\.\d+)?)$")
NO_PINS = re.compile(r"(^|/)(\d?[34]|[1-9]3|[1-9]4|97|98)$")
NC_PINS = re.compile(r"(^|/)(\d?[12]|[1-9]1|[1-9]2|95|96)$")


def _words(page):
    """Words with orientation: upright (horizontal) and rotated (vertical) text, each with centre, size and colour."""
    chars = [c for c in page.chars if c["text"].strip()]
    out = []
    up = [c for c in chars if c.get("upright", True)]
    rot = [c for c in chars if not c.get("upright", True)]
    lines = defaultdict(list)
    for c in up:
        lines[round(c["bottom"] / 2.5)].append(c)
    for L in lines.values():
        L.sort(key=lambda c: c["x0"])
        cur = [L[0]]
        for c in L[1:]:
            if c["x0"] - cur[-1]["x1"] > 1.6:
                out.append(_mk(cur, False)); cur = []
            cur.append(c)
        out.append(_mk(cur, False))
    cols = defaultdict(list)
    for c in rot:
        cols[round(c["x0"] / 2.5)].append(c)
    for L in cols.values():
        L.sort(key=lambda c: -c["top"])      # reading bottom -> top
        cur = [L[0]]
        for c in L[1:]:
            if cur[-1]["top"] - c["bottom"] > 1.6:
                out.append(_mk(cur, True)); cur = []
            cur.append(c)
        out.append(_mk(cur, True))
    return out


def _mk(cs, rotated):
    x0, x1 = min(c["x0"] for c in cs), max(c["x1"] for c in cs)
    t, b = min(c["top"] for c in cs), max(c["bottom"] for c in cs)
    col = cs[0].get("non_stroking_color")
    return dict(text="".join(c["text"] for c in cs), x0=x0, x1=x1, top=t, bottom=b, xc=(x0 + x1) / 2, yc=(t + b) / 2,
                size=cs[0].get("size", 0), rot=rotated, col=tuple(col) if isinstance(col, (list, tuple)) else col)


def _wire_segments(page):
    segs = []
    for l in page.lines:
        if l.get("dash"):
            continue
        (xa, ya), (xb, yb) = l["pts"][0], l["pts"][-1]
        if abs(xa - xb) < 0.6:
            segs.append((l.get("stroking_color"), xa, min(ya, yb), xa, max(ya, yb)))
        elif abs(ya - yb) < 0.6:
            segs.append((l.get("stroking_color"), min(xa, xb), ya, max(xa, xb), ya))
    by = defaultdict(float)
    for c, x0, y0, x1, y1 in segs:
        by[str(c)] += (x1 - x0) + (y1 - y0) if max(x1 - x0, y1 - y0) >= 15 else 0
    if not by:
        return [], False
    coloured = {c: v for c, v in by.items() if c not in ("(0.0, 0.0, 0.0)", "None", "(0, 0, 0)")}
    if coloured and max(coloured.values()) >= 300:
        best = max(coloured, key=coloured.get)
        return [(x0, y0, x1, y1) for c, x0, y0, x1, y1 in segs if str(c) == best], True
    return [(x0, y0, x1, y1) for c, x0, y0, x1, y1 in segs if max(x1 - x0, y1 - y0) >= 8], False


def _on(seg, px, py, tol=1.2):
    x0, y0, x1, y1 = seg
    return x0 - tol <= px <= x1 + tol and y0 - tol <= py <= y1 + tol


def build_nets(segs):
    n = len(segs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    ends = [((s[0], s[1]), (s[2], s[3])) for s in segs]
    for i in range(n):
        for p in ends[i]:
            for j in range(n):
                if i != j and find(i) != find(j) and _on(segs[j], p[0], p[1]):
                    parent[find(i)] = find(j)
    nets = defaultdict(list)
    for i in range(n):
        nets[find(i)].append(i)
    out = []
    for idxs in nets.values():
        segl = [segs[i] for i in idxs]
        free = []
        for i in idxs:
            for p in ends[i]:
                touching = sum(1 for j in idxs if j != i and _on(segs[j], p[0], p[1]))
                if touching == 0:
                    free.append(p)
        out.append(dict(segs=segl, free=free, length=sum((s[2] - s[0]) + (s[3] - s[1]) for s in segl)))
    return out


def _dist_seg(w, seg):
    x0, y0, x1, y1 = seg
    dx = max(x0 - w["xc"], 0, w["xc"] - x1)
    dy = max(y0 - w["yc"], 0, w["yc"] - y1)
    return (dx * dx + dy * dy) ** 0.5


def _contact_kind(pins):
    """NO / NC from pin numbers: 95/96 and x1/x2 are normally-closed, 97/98 and x3/x4 normally-open (single 1,2 / 3,4 pairs on aux contacts)."""
    ps = {p for p in pins if re.match(r"^\d{1,2}$", p)}
    if ps & {"95", "96"} or any(re.match(r"^\d1$|^\d2$", p) for p in ps if len(p) == 2) or ps == {"1", "2"}:
        return "NC"
    if ps & {"97", "98"} or any(re.match(r"^\d3$|^\d4$", p) for p in ps if len(p) == 2) or ps == {"3", "4"}:
        return "NO"
    return ""


RATING_RE = re.compile(r"\d\s?(?:A|AMP|KW|HP|V|VAC|VDC|VA|HZ|MM|SQ\.?MM|KA|MA)\b|\d[PN],", re.I)


def _annotations(body, used, owner):
    """Every printed text that is not a device tag, pin, cross-reference or wire number (descriptions, ratings, supplies, notes), with the nearest device tag."""
    rest = [w for w in body if id(w) not in used and len(w["text"].strip()) > 0]
    vert = [w for w in rest if w["rot"]]
    rest = [w for w in rest if not w["rot"]]
    rest.sort(key=lambda w: (round(w["yc"] / 3), w["x0"]))
    phrases, cur = [], []
    for w in rest:
        if cur and (abs(cur[-1]["yc"] - w["yc"]) > 3 or w["x0"] - cur[-1]["x1"] > 5):
            phrases.append(cur); cur = []
        cur.append(w)
    if cur:
        phrases.append(cur)
    out = []
    for ph in phrases:
        text = " ".join(w["text"] for w in ph).strip()
        if len(text) < 2 or re.fullmatch(r"[\d\s./,-]+", text):
            continue
        near = owner(dict(xc=sum(w["xc"] for w in ph) / len(ph), yc=ph[0]["yc"]), 80)
        kind = "rating" if RATING_RE.search(text) else ("description" if re.search(r"[A-Za-z]{3,}", text) else "other")
        out.append(dict(text=text[:120], near_tag=near["tag"] if near else "", kind=kind))
    for w in vert:                                      # vertical labels (rotated wire / terminal / duct marks)
        if len(w["text"]) >= 2:
            near = owner(w, 80)
            out.append(dict(text=w["text"][:120], near_tag=near["tag"] if near else "", kind="vertical label"))
    return out


def recognize(page, sheet_no):
    """Returns (symbols, nets, xrefs). symbols: tag/role/pins/location; nets: wire no + endpoints; xrefs: coil / contact rows."""
    words = _words(page)
    H, W = float(page.height), float(page.width)
    body = [w for w in words if w["top"] < H * 0.88 and w["top"] > 20]
    tags = [w for w in body if not w["rot"] and w["size"] >= 7 and TAG.match(w["text"]) and not WIRE_NO.match(w["text"]) or
            (not w["rot"] and w["size"] >= 7 and re.match(r"^\d+[A-Z]?-(MCB|MCCB|OLR|C\d?|K\d+|R\d+|ILSW|ILPB|PB\d?|SS\d?|L\d?|F\d?|TL|SMPS|PS\d?)$", w["text"]))]
    pins = [w for w in body if not w["rot"] and w["size"] <= 6.8 and PIN.match(w["text"])]
    refs = [w for w in body if XREF.match(w["text"])]
    segs, coloured = _wire_segments(page)
    nets = build_nets(segs) if segs and len(segs) < 1800 else []
    # ---- symbols: pins + printed refs belong to the nearest tag
    sym = {}
    for t in tags:
        key = (t["text"], round(t["xc"] / 20), round(t["yc"] / 20))
        sym[key] = dict(sheet=sheet_no, tag=t["text"], x=t["xc"], y=t["yc"], pins=[], refs=[], rating="")
    skeys = list(sym.values())

    used = {id(t) for t in tags}

    def owner(w, maxd, bias=1.0):
        best, bd = None, maxd
        for s in skeys:
            dx, dy = w["xc"] - s["x"], w["yc"] - s["y"]
            d = (dx * dx * bias + dy * dy) ** 0.5
            if d < bd:
                best, bd = s, d
        return best
    for p in pins:
        s = owner(p, 42)
        if s is not None:
            s["pins"].append(p["text"]); used.add(id(p))
    for r in refs:
        s = owner(r, 60, 3.0)
        if s is not None and r["yc"] >= s["y"] - 4:
            s["refs"].append(r["text"][1:]); used.add(id(r))
    for w in body:
        if not w["rot"] and w["size"] <= 6.8 and re.match(r"^\d[PN],\s?\d+(?:\.\d+)?A$|^\d+(?:\.\d+)?-\d+(?:\.\d+)?A$|^\d+(?:\.\d+)?kW$", w["text"], re.I):
            s = owner(w, 40, 4.0)
            if s is not None and not s["rating"]:
                s["rating"] = w["text"]; used.add(id(w))
    symbols, xrefs = [], []
    for s in skeys:
        ps = set(s["pins"])
        cls = classify(s["tag"])
        if cls == "Other" and re.match(r"^[RK]\d+$", s["tag"]):
            cls = "Relay"
        if {"A1", "A2"} <= ps or ("A1" in ps and cls in ("Relay", "Contactor")):
            role = "coil"
        elif {"95", "96"} & ps or cls == "Overload Relay / Motor Protector":
            role = "overload contact" if {"95", "96"} & ps else "overload"
        elif ps & {"X1", "X2"} or cls == "Lamp":
            role = "lamp"
        elif cls in ("MCB", "MCCB"):
            role = "protective device"
        elif (any(re.match(r"^\d/L\d$|^\d/T\d$|^[LT]\d$", p) for p in ps) and cls == "Contactor") or                 (cls == "Contactor" and {"1", "2", "3", "4"} <= ps):
            role = "main contacts"
        elif cls in ("Relay", "Contactor"):
            role = "contact"
        else:
            role = cls.lower()
        kind = _contact_kind(s["pins"]) if role in ("contact", "overload contact") else ""
        if kind and cls == "Contactor" and ps in ({"1", "2"}, {"3", "4"}):
            kind = ""           # single 1/2 or 3/4 pair on a contactor may be a main pole - not claimed as aux NO/NC
        symbols.append(dict(sheet=sheet_no, tag=s["tag"], role=role, dev_class=cls, pins=",".join(sorted(ps, key=lambda p: (len(p), p))),
                            rating=s["rating"], col=min(int(s["x"] / (W / 10)), 9), x=s["x"], y=s["y"]))
        if role in ("coil", "contact", "main contacts", "overload contact"):
            xrefs.append(dict(sheet=sheet_no, direction="coil" if role == "coil" else "contact", target_sheet=",".join(r.split(".")[0] for r in s["refs"]),
                              target_col=str(min(int(s["x"] / (W / 10)), 9)), label=s["tag"],
                              text=f"{role}; {kind + '; ' if kind else ''}pins={','.join(sorted(ps)) or '-'}; refs={','.join(s['refs']) or '-'}"))
    # ---- nets: wire numbers + endpoint devices
    wire_words = [w for w in body if WIRE_NO.match(w["text"]) and w["size"] < 8.5]
    cs_words = [w for w in body if WIRE_COL_SIZE.match(w["text"])]
    out_nets = []
    for nid, net in enumerate(nets):
        if net["length"] < 15 or len(net["free"]) < 2 and not net["free"]:
            continue
        nums, extra = [], []
        for w in wire_words:
            if any(_dist_seg(w, s) <= 7 for s in net["segs"]):
                nums.append(w["text"]); used.add(id(w))
        for w in cs_words:
            if any(_dist_seg(w, s) <= 8 for s in net["segs"]):
                extra.append(w["text"]); used.add(id(w))
        ends = []
        for (fx, fy) in net["free"]:
            pw = min((p for p in pins if abs(p["xc"] - fx) <= 14 and abs(p["yc"] - fy) <= 14), key=lambda p: abs(p["xc"] - fx) + abs(p["yc"] - fy), default=None)
            if pw is not None:
                s = owner(pw, 45)
                ends.append(f"{s['tag']}:{pw['text']}" if s and s["tag"] != pw["text"] else (s["tag"] if s else f"?:{pw['text']}"))
                continue
            near = min(((abs(t["xc"] - fx) + abs(t["yc"] - fy), t) for t in tags if abs(t["xc"] - fx) < 70 and abs(t["yc"] - fy) < 40), key=lambda z: z[0], default=None)
            if near:
                ends.append(near[1]["text"])
        ends = list(dict.fromkeys(ends))
        if nums or len(ends) >= 2:
            out_nets.append(dict(sheet=sheet_no, wire_no=" / ".join(dict.fromkeys(nums)), ends=ends, marks=",".join(dict.fromkeys(extra)), length=round(net["length"])))
    ann = _annotations(body, used, owner)
    return symbols, out_nets, xrefs, coloured, ann


def page_annotations(page, sheet_no):
    """Fallback for pages without symbols (title page, cover, notes): every text phrase on the page body."""
    H = float(page.height)
    body = [w for w in _words(page) if 20 < w["top"] < H * 0.88]
    return [dict(a, sheet=sheet_no) for a in _annotations(body, set(), lambda w, d, b=1.0: None)]
