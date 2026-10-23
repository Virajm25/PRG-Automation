"""Wire numbers (ferrules), device tags, terminal strips and EPLAN-style cross references."""
import re
from .pdfio import upright_chars
from .devices import _words

WIRE_RE = re.compile(r"^(\d+[A-Z](?:/\d+[A-Z])?-\d{2,4}[A-Z]?)(?:/(\d{1,3})\.(\d))?$")      # 2F-101  2F-101/23.4
XREF_TO = re.compile(r"^(?:[A-Za-z0-9+\-_]{1,10})?/(\d{1,3})\.(\d)$")                            # /11.6  L+/22.0  1L1/12.0
XREF_FROM = re.compile(r"^(\d{1,3})\.(\d)(?:/([A-Za-z0-9+\-_]{1,10}))?$")                        # 10.7/P1  20.9/L+
DEV_TAG_RE = re.compile(r"^(\d+[A-Z]?(?:/\d+[A-Z]?)?)-([A-Z]{1,5}\d{0,2})$")                    # 2F-MCB 6F/7F-C4 2F-OLR
COIL_CLASSES = {"Relay", "Contactor"}


def extract_wires(page, sheet_no, chars=None):
    """Return (wires, xrefs, tags) found on the sheet."""
    chars = chars if chars is not None else upright_chars(page)
    H = float(getattr(page, "height", 842))
    ws = [w for w in _words(chars) if w["top"] < H * 0.88]
    tags = [w for w in ws if DEV_TAG_RE.match(w["text"])]
    wires, xrefs = [], []
    for w in ws:
        m = WIRE_RE.match(w["text"])
        if m and not DEV_TAG_RE.match(w["text"]):
            near = _nearest(tags, w)
            wires.append(dict(sheet=sheet_no, wire_no=m.group(1), ferrule_no=m.group(1),
                              to_ref=f"{m.group(2)}.{m.group(3)}" if m.group(2) else "",
                              device_ref=near["text"] if near else "", _x=w["xc"], _y=w["top"]))
            continue
        m = XREF_TO.match(w["text"])
        if m:
            xrefs.append(dict(sheet=sheet_no, direction="to", target_sheet=m.group(1), target_col=m.group(2),
                              label=w["text"].split("/")[0], text=w["text"]))
            continue
        m = XREF_FROM.match(w["text"])
        if m:
            xrefs.append(dict(sheet=sheet_no, direction="from", target_sheet=m.group(1), target_col=m.group(2),
                              label=m.group(3) or "", text=w["text"]))
    tagrows = [dict(sheet=sheet_no, tag=t["text"]) for t in tags]
    return wires, xrefs, tagrows


def _nearest(tags, w, maxd=90):
    best, bd = None, 1e9
    for t in tags:
        d = abs(t["xc"] - w["xc"]) + abs(t["top"] - w["top"]) * 0.7
        if d < bd:
            best, bd = t, d
    return best if best is not None and bd < maxd else None


def dedupe_wires(wires):
    seen, out = set(), []
    for w in wires:
        k = (w["sheet"], w["wire_no"], w["to_ref"])
        if k in seen:
            continue
        seen.add(k)
        out.append(w)
    return out
