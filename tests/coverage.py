"""Per-page extraction coverage audit.

For every page: which meaningful text tokens printed on the sheet appear in anything we extracted from that sheet?
Usage: python tests/coverage.py "<pdf in Docs/>" [--tokens]   (--tokens lists the unexplained tokens per page type)
Boilerplate (title block, grid numbers, revision table) is excluded so the percentage reflects engineering content."""
import sys, os, re, tempfile, collections
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp()
os.environ["PRG_DB"] = os.path.join(tmp, "c.db"); os.environ["PRG_HOME"] = tmp; os.environ["ANTHROPIC_API_KEY"] = ""; os.environ["PRG_AI_PROVIDER"] = "off"
from core import db, service
from core.pdfio import open_pdf, page_text
from core.geometry import _words as gw

TABLES = ["io_rows", "circuits", "devices", "wires", "terminals", "cables", "xrefs", "panel_specs", "bom_items", "modules", "symbols", "annotations"]
BOILER = re.compile(r"^(REVISION|DETAILS|DATE|DRAWN|CHECKED|APPROVED|BY|TITLE|SYSTEM|PROJECT|DRG|NO|SHEET|REV|AS|BUILT|FOR|APPROVAL|COMMENTS|PER|"
                    r"PVT|LTD|OFFICE|AND|OPP|AKURDI|PUNE|VINAYAK|ARCADE|FLOOR|ND|WWW|SOFTWARE|EPLAN|REPLACEMENT|REPLACED|PAGE|PROJECT|CODE|START|"
                    r"MS-PRG|AUTOMATION|ED|APPR|OF|THE|TYPE|TERMINAL|TERMINALS|USED|NORMAL|FUSED|NOTE-TYPE|DIGITAL|INPUT|INPUTS|OUTPUT|OUTPUTS|MODULE|"
                    r"DIAGRAM|WIRING|ANALOG|SUPPLY|DESCRIPTION|SIZE|COLUMN|PAGE|AUTOMATICALLY|GENERATED|EDITED|WAS|AN|X)$", re.I)


def toks(s):
    return {t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9./+\-]*", str(s).upper()) if len(t) > 1 or t.isdigit() is False}


def main(pdf_name, show):
    path = os.path.join(ROOT, "Docs", pdf_name)
    db.init()
    pid = db.x("INSERT INTO projects(name,client,created) VALUES('c','',0)")
    did = db.x("INSERT INTO documents(project_id,filename,path,status,created) VALUES(?,?,?,?,0)", (pid, pdf_name, path, "queued"))
    service.ingest(did)
    got = collections.defaultdict(set)
    for t in TABLES:
        for r in db.q(f"SELECT * FROM {t} WHERE doc_id=?", (did,)):
            sh = r.get("sheet")
            for s in (str(sh).split(",") if sh not in (None, "") else []):
                if s.strip().isdigit():
                    got[int(s)] |= toks(" ".join(str(v) for k, v in r.items() if k not in ("id", "project_id", "doc_id", "sheet")))
    types = {r["sheet"]: r["draw_type_label"] or r["kind"] for r in db.q("SELECT * FROM sheets WHERE doc_id=?", (did,))}
    by_type = collections.defaultdict(lambda: [0, 0, collections.Counter()])
    with open_pdf(path) as pdf:
        for i, p in enumerate(pdf.pages, 1):
            H = float(p.height)
            words = [w for w in gw(p) if w["top"] < H * 0.86 and w["top"] > 22]     # rotation-aware words
            ws = {t for w in words for t in toks(w["text"]) if not BOILER.match(t) and not re.fullmatch(r"\d{1,2}", t)}
            hit = ws & got[i]
            ty = types.get(i, "?")
            by_type[ty][0] += len(ws); by_type[ty][1] += len(hit)
            by_type[ty][2].update(ws - got[i])
    tot = sum(v[0] for v in by_type.values()); hit = sum(v[1] for v in by_type.values())
    print(f"\n{pdf_name}: {100 * hit / max(1, tot):.0f}% of engineering tokens captured ({hit}/{tot})")
    for ty, (n, h, miss) in sorted(by_type.items(), key=lambda kv: kv[1][1] / max(1, kv[1][0])):
        print(f"  {ty:32s} {100 * h / max(1, n):3.0f}%  ({h}/{n})" + (f"   missing e.g. {[k for k, _ in miss.most_common(45)]}" if show else ""))


if __name__ == "__main__":
    main(sys.argv[1], "--tokens" in sys.argv)
