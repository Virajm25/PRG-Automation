"""Device / BOM extraction from schematic sheets.

EPLAN-style symbols stack their data vertically:  TAG  /  RATING  /  PART-NO  /  MAKE.
We anchor on the MAKE word, then walk upward in the same x-column.
"""
import re
from .pdfio import upright_chars, cluster_lines, join_chars_line

MAKES = ["SIEMENS", "SCHNEIDER", "TEKNIC", "OMRON", "DELTA", "SELEC", "ELMEX", "LEGRAND", "ABB", "PHOENIX",
         "HAVELLS", "L&T", "MEANWELL", "SALZER", "SAHARA", "KAYCEE", "REXNORD", "ANCHOR", "POLYCAB", "LAPP",
         "ENDRESS", "E+H", "YOKOGAWA", "AUTONICS", "IFM", "RITTAL", "PEPPERL", "SICK", "MITSUBISHI", "ALLEN",
         "ROCKWELL", "WAGO", "WEIDMULLER", "FINDER", "ELETTRA", "ELLETTRO", "OREVA", "BONFIGLIOLI", "DANFOSS"]
MAKE_RE = re.compile(r"^(" + "|".join(re.escape(m) for m in MAKES) + r")$", re.I)

RATING_RE = re.compile(r"^(\d+(\.\d+)?\s?(A|AMP|KW|HP|V|VA|W|MA|KA|MM2|SQ\.?MM)|\d[PN],?\s?\d+(\.\d+)?A|\d+(\.\d+)?-\d+(\.\d+)?A|\d+P,\d+A|\d+(\.\d+)?(KW|kW)|\d+/\d+A?)\b", re.I)
PART_RE = re.compile(r"^(?=.*\d)[A-Z0-9][A-Z0-9\-_./]{4,}$", re.I)
TAG_RE = re.compile(r"^\d*[A-Z]{1,5}\d*[-/][A-Z0-9/\-.]+$|^[A-Z]{1,4}\d{1,3}$")

# (regex on part number or tag-suffix, device class)  -- extendable & user-learnable
PART_CLASSES = [
    (r"^5SL|^5SY|^A9[NF]|^MCB|^iC60|^C60", "MCB"),
    (r"^3RT|^LC1|^LP1|^CJX|^3TF|^LC2", "Contactor"),
    (r"^3RU|^3RB|^LRD|^LR2|^GV2|^3RV", "Overload Relay / Motor Protector"),
    (r"^3RH|^RXM|^RXZ|^RPM|^RUM|^MY[24]|^LY[24]|^G2R|^RT3|^PTF|^PYF|^3TX|^RSB", "Relay"),
    (r"^LV\d|^NSX|^NS\d|^CVS|^3VA|^S\d{2}H", "MCCB"),
    (r"^VFD|^ATV|^6SL|^G120|^MM4|^ACS|^E[CM]\d", "VFD"),
    (r"^6ES7|^BMX|^TM2|^TM3|^DVP|^MLFB-DVP|^AS\d|^SM1|^IC", "PLC Module"),
    (r"^6AV|^HMI|^DOP|^MTP|^XBT|^HMIG|^TP\d", "HMI"),
    (r"^6EP|^ABL|^QUINT|^NDR|^SDR|^HDR|^MDR|^RS-", "Power Supply"),
    (r"^3PL|^XB[47]|^XA2|^22[0-9]{3}", "Pilot / Push Button / Lamp"),
    (r"^MFM|^EM\d|^PM\d{3}|^DPM", "Multifunction Meter"),
]
TAG_SUFFIX_CLASSES = [
    (r"-?MCB\b|-F\d*$", "MCB"), (r"-?MCCB\b", "MCCB"), (r"-C\d*\b|-CON\b|-K\d+M?$", "Contactor"),
    (r"-OLR\b|-OL\b", "Overload Relay / Motor Protector"), (r"-R\d+\b|-RL\d*\b|-CR\d*\b", "Relay"),
    (r"-PB\d*\b|-ILPB|-ILSW|-IL\d?\b", "Push Button"), (r"-SS\d*\b|-SEL", "Selector Switch"),
    (r"-L\d*$|-LAMP|-IND", "Lamp"), (r"-VFD\b|VFD", "VFD"), (r"-SMPS|-PS\d*\b|-PSU", "Power Supply"),
    (r"-HMI|HMI", "HMI"), (r"^(TT|PT|DPT|LT|FT|TE|PDT|AT|PH|FIT|LIT|PIT|TIT)-", "Transmitter / Sensor"),
    (r"^(LS|LSL|LSH|LSHH|LSLL|PS|FS|ZS|ZSO|ZSC|XS|PX|IS|TS)[-A-Z]*-", "Switch / Limit / Proximity"),
    (r"^(TB|X|XT)-?\w*\d$", "Terminal Block"), (r"^(M|MTR)\d*$|MOTOR", "Motor"), (r"^HT-|HOOTER|HORN", "Hooter / Annunciator"),
]


def classify(tag="", part="", desc=""):
    p = (part or "").upper()
    for rx, cls in PART_CLASSES:
        if p and re.search(rx, p, re.I):
            return cls
    t = (tag or "").upper()
    for rx, cls in TAG_SUFFIX_CLASSES:
        if re.search(rx, t):
            return cls
    d = (desc or "").upper()
    for kw, cls in [("PUSH BUTTON", "Push Button"), ("PUSHBUTTON", "Push Button"), ("EMERGENCY", "Push Button"), ("SELECTOR", "Selector Switch"),
                    ("INDICATION", "Lamp"), ("TUBELIGHT", "Lamp"), ("LAMP", "Lamp"), ("MCCB", "MCCB"), ("MCB", "MCB"), ("MAIN SWITCH", "MCB"),
                    ("SMPS", "Power Supply"), ("REDUNDANCY", "Power Supply"), ("POWER SUPPLY", "Power Supply"), ("HMI", "HMI"), ("VFD", "VFD"),
                    ("DRIVE", "VFD"), ("TERMINAL", "Terminal Block"), ("PLC", "PLC Module"), ("CPU", "PLC Module"), ("MODULE", "PLC Module"),
                    ("BASEUNIT", "PLC Module"), ("SWITCH, ", "Switch / Limit / Proximity"), ("HOOTER", "Hooter / Annunciator"), ("FUSE", "Fuse"),
                    ("RELAY", "Relay"), ("CONTACTOR", "Contactor"), ("TRANSMITTER", "Transmitter / Sensor"),
                    ("SENSOR", "Transmitter / Sensor"), ("SWITCH", "Switch / Limit / Proximity"),
                    ("MOTOR", "Motor"), ("VALVE", "Field Device (valve)"), ("PUMP", "Field Device (pump)")]:
        if kw in d:
            return cls
    return "Other"


def _words(chars, gap=2.2):
    out = []
    for L in cluster_lines(chars, 3.0):
        cs = sorted(L["chars"], key=lambda c: c["x0"])
        cur = []
        for c in cs:
            if cur and c["x0"] - cur[-1]["x1"] > gap:
                out.append(cur); cur = []
            cur.append(c)
        if cur:
            out.append(cur)
    res = []
    for w in out:
        res.append(dict(text="".join(c["text"] for c in w), x0=w[0]["x0"], x1=w[-1]["x1"],
                        xc=(w[0]["x0"] + w[-1]["x1"]) / 2, top=min(c["top"] for c in w),
                        bottom=max(c["bottom"] for c in w)))
    return res


def extract_devices(page, sheet_no, chars=None):
    """Return list of device dicts found on the sheet."""
    chars = chars if chars is not None else upright_chars(page)
    H = float(getattr(page, "height", 842))
    ws = [w for w in _words(chars) if w["top"] < H * 0.88]
    makes = [w for w in ws if MAKE_RE.match(w["text"])]
    devs = []
    for m in makes:
        col = [w for w in ws if abs(w["xc"] - m["xc"]) < 30 and w["bottom"] <= m["top"] + 1 and m["top"] - w["bottom"] < 70]
        col.sort(key=lambda w: -w["bottom"])
        part = rating = tag = ""
        last = m["top"]
        for w in col:
            if last - w["bottom"] > 14:
                break
            t = w["text"]
            if not part and PART_RE.match(t) and not RATING_RE.match(t) and not MAKE_RE.match(t) and len(t) >= 5:
                part = t
            elif part and not rating and RATING_RE.match(t):
                rating = t
            elif part and not tag and TAG_RE.match(t):
                tag = t
                break
            elif not part and not rating and RATING_RE.match(t):
                rating = t
            else:
                if part:
                    continue
            last = w["bottom"]
        if part:
            devs.append(dict(sheet=sheet_no, tag=tag, make=m["text"].title(), part_no=part, rating=rating,
                             desc="", dev_class=classify(tag, part), _x=m["xc"], _y=m["top"]))
    return devs


def extract_tag_devices(page, sheet_no, chars=None):
    """Field/instrument tags on sheets (valves, instruments, motors) - tag-only devices."""
    return []


# ---------------------------------------------------------------- catalog scan (no 'make' word needed)
PN_PATTERNS = [
    (re.compile(r"^(5SL\w+|5SY\w+|3R[THUV]\d[\w-]+|6ES7[\w-]+|3SU\w+|3VA\w+|6AV\w+|6EP\w+|3TX\w+)$", re.I), "Siemens"),
    (re.compile(r"^(A9[NFK]\w+|LV\d+\w*|BMX\w+|LC1\w+|LRD\w+|XB\w+|ABL\w+|RXM\w+|LAD\w+|TM\d\w+|GV2\w+|NSX\w+)$", re.I), "Schneider"),
    (re.compile(r"^(DVP[\w-]+|DOP[-\w]*\d\w*|VFD\d\w+|ASD\w+)$", re.I), "Delta"),
    (re.compile(r"^(3PL[A-Z]{1,3}\d\w*)$", re.I), "Teknic"),
    (re.compile(r"^(MFM\d+[\w-]*)$", re.I), "Selec"),
]
NOT_PART = re.compile(r"^\d+[A-Z]{1,2}-[A-Z]\d?$|^\d+F[-/]", re.I)  # '9F-T1' etc. are tags, not part numbers


def scan_catalog_parts(page, sheet_no, chars=None, skip_parts=()):
    chars = chars if chars is not None else upright_chars(page)
    H = float(getattr(page, "height", 842))
    ws = [w for w in _words(chars) if w["top"] < H * 0.88]
    found = []
    for w in ws:
        t = w["text"].strip(",;:()")
        t = re.sub(r"^MLFB-", "", t, flags=re.I)
        if NOT_PART.match(t) or len(t) < 5:
            continue
        for rx, make in PN_PATTERNS:
            if rx.match(t):
                found.append(dict(sheet=sheet_no, tag="", make=make, part_no=t, rating="", desc="",
                                  dev_class=classify("", t), _x=w["xc"], _y=w["top"]))
                break
    return [f for f in found if f["part_no"].upper() not in skip_parts]


def merge_devices(anchored, scanned):
    """Anchored (make-stack) devices win; scanned parts only added if no anchored one within 45pt."""
    out = list(anchored)
    for s in scanned:
        dup = False
        for a in anchored:
            if a["sheet"] == s["sheet"] and a["part_no"].upper() == s["part_no"].upper():
                if "_x" not in a or (abs(a.get("_x", 0) - s["_x"]) < 45 and abs(a.get("_y", 0) - s["_y"]) < 45):
                    dup = True
                    break
        if not dup:
            out.append(s)
    return out
