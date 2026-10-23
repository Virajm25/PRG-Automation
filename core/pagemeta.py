import re

def title_of(text):
    m = re.search(r"TITLE\s*:?-?\s*([^\n]+)", text)
    if not m:
        return ""
    t = m.group(1).strip()
    # the title can continue on the next line (e.g. 'POWER & CONTROL WIRING')
    nxt = text[m.end():].split("\n", 2)
    if len(nxt) > 1 and nxt[1] and nxt[1].isupper() and len(nxt[1]) < 45 and not re.search(r"\d{2}-\d{2}-\d{4}|AS PER|FOR APPROVAL|AQUA|PVT", nxt[1]):
        t += " " + nxt[1].strip()
    t = re.split(r"\s+(SYSTEM|PROJECT)\s*:?-", t)[0]
    return t.strip()

def sheet_label(text):
    m = re.search(r"(\d{1,3})\s*/\s*(\d{1,3})\s+(?:AS BUILT|FOR APPROVAL|APPROVED|[A-Z ]{3,15})?", text[-400:])
    return f"{m.group(1)}/{m.group(2)}" if m else ""

def project_meta(text):
    d = {}
    for key, rx in [("project", r"PROJECT\s*(?:NAME)?\s*:-?\s*([^\n]+)"), ("client", r"CLIENT\s*:?-?\s*([^\n]+)"),
                    ("drawing_no", r"DRG\.?\s*NO\.?\s*:-?\s*([^\n]+)")]:
        m = re.search(rx, text)
        if m:
            d[key] = m.group(1).strip()[:80]
    return d
