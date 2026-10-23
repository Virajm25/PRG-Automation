"""Bill-of-material extraction from printed BOM sheets ("SR.NO. ITEM DESCRIPTION MAKE PART NO QTY").

Rows look like  `3 6A, 2P, 10kA, C-Curve MCB SIEMENS 5SL42067RC 5`  or  `15 4Sq.mm Control Terminals (Blue) Teknic - 10`.
The right-hand side is parsed backwards: QTY, PART NO ('-' = none), MAKE, the remainder is the description.
"""
import re
from .devices import MAKES

ROW = re.compile(r"^(\d{1,3})\s+(.+?)\s+(\d{1,5})$")
KNOWN_MAKES = {m.upper() for m in MAKES} | {"STD.", "STD", "ELTTRO", "SHAVISON", "TEKNIC", "ELMEX", "OREVA", "KAYCEE", "REXNORD", "SALZER",
                                          "WEIDMULLER", "WEIDMULLER.", "PHOENIX", "SELEC", "OMRON", "SIEMENS", "DELTA", "SCHNEIDER", "ABB", "L&T"}
SECTION = re.compile(r"^([A-Z0-9 &+/\-]+?)\s+(SECTION|PANEL)$", re.I)
RATING = re.compile(r"(\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?\s?(?:A|AMP|AMPS|KW|HP|VA|V|VAC|VDC|MA|KA)\b(?:[, ]*\d[PN])?|\d[PN],\s?\d+\s?(?:A|AMP))", re.I)
FOOTER = re.compile(r"AS BUILT|AS PER COMMENTS|FOR APPROVAL|TITLE:|PROJECT|DRAWN BY|CHECKED|REVISION|DRG\.? ?NO|FILE NO", re.I)
UOM = re.compile(r"\b(Nos\.?|Set\.?|Mtr\.?|Mtrs\.?|Meter|Pair|Lot)\s*$", re.I)


def is_bom_text(text):
    t = text.upper()
    return "BILL OF MATERIAL" in t and ("PART NO" in t or "QTY" in t)


def parse_bom_text(text, sheet_no, panel_default=""):
    panel, items, started = panel_default, [], False
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        m_sec = SECTION.match(line)
        if m_sec and "BILL" not in line.upper() and not started:
            panel = m_sec.group(1).strip().upper()
            continue
        if line.upper().startswith("SR.NO") or line.upper().startswith("SR NO"):
            started = True
            continue
        if not started or FOOTER.search(line):
            continue
        m = ROW.match(line)
        if not m:
            continue
        item_no, body, qty = m.group(1), m.group(2), m.group(3)
        uom = ""
        mu = UOM.search(body)
        if mu:
            uom = mu.group(1); body = body[:mu.start()].strip()
        toks = body.split(" ")
        part, make = "", ""
        if len(toks) >= 2:
            last = toks[-1]
            if last == "-":
                toks = toks[:-1]
            elif re.search(r"\d", last) and last.upper() not in KNOWN_MAKES and len(toks) >= 3 and toks[-2].upper().strip(".") in {k.strip(".") for k in KNOWN_MAKES}:
                part = last; toks = toks[:-1]
            elif re.search(r"\d", last) and len(toks) >= 3 and re.match(r"^[A-Z0-9][A-Z0-9\-_./]{4,}$", last, re.I) and not re.match(r"^\d+(\.\d+)?(A|KW|V|MM)", last, re.I):
                part = last; toks = toks[:-1]
        if toks and toks[-1].upper() in KNOWN_MAKES:
            make = toks[-1]; toks = toks[:-1]
        descr = " ".join(toks).strip()
        r = RATING.search(descr)
        items.append(dict(sheet=sheet_no, item_no=item_no, descr=descr, make=make.title() if make.upper() not in ("STD.", "STD") else "Std.",
                          part_no=part, qty=qty, uom=uom or "Nos.", rating=r.group(1).strip() if r else "", catalogue_no=part,
                          panel=panel, source="bom-sheet"))
    return items
