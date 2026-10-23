import sys, re, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pandas as pd
from core.pipeline import process_pdf

BASE = os.path.join(os.path.dirname(__file__), "..", "Docs")
PAIRS = [("PRG-2526-616-AQUA CARE-SHAH EYE CARE-MCC+PLC PANEL.pdf", "PRG-2526-616-shaha Eye Care.xls"),
         ("PRG-2526-637-ASEE-MARVEL-375KLD PLC+MCC.pdf", "PRG-2526-637-375KLD-ASEE-R3.xls"),
         ("PRG-2425-465-MOJJ ENGG MSD-697 PLC PANEL.pdf", "PRG-2425-465-MSD697-R0.xls"),
         ("PRG-2526-562-F25-14-PRIME HEAT-PLC PANEL.pdf", "PRG-2526-562-PHF-F25-14-STEELCO_IO_LIST_R0.xls")]
norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s).upper())

def ref_cols(xls):
    x = pd.ExcelFile(os.path.join(BASE, xls))
    tags, descs = set(), set()
    for sn in x.sheet_names:
        if re.search(r"tag list|summary|sequence|architecture", sn, re.I):
            continue
        d = x.parse(sn, header=None)
        for i in range(min(15, len(d))):
            row = [str(v).strip().lower() for v in d.iloc[i].tolist()]
            if "tag" in row and any(c.startswith("desc") for c in row):
                tc = row.index("tag"); dc = next(j for j, c in enumerate(row) if c.startswith("desc"))
                for _, r in d.iloc[i + 1:].iterrows():
                    t, de = r.iloc[tc], r.iloc[dc]
                    if isinstance(t, str) and len(norm(t)) > 2 and norm(t) != "TAG": tags.add(norm(t))
                    if isinstance(de, str) and len(norm(de)) > 3 and not re.match(r"(?i)description", de): descs.add(norm(de))
                break
    return tags, descs

def ref_tags(xls):
    x = pd.ExcelFile(os.path.join(BASE, xls))
    tags = {}
    for sn in x.sheet_names:
        d = x.parse(sn, header=None)
        if not re.search(r"digital|analog|input|output|di|do|ai|ao|rtd|tc", sn, re.I) or re.search(r"tag list|summary|sequence", sn, re.I):
            continue
        hdr = None
        for i in range(min(15, len(d))):
            row = [str(v).strip().lower() for v in d.iloc[i].tolist()]
            if "tag" in row or "tag no" in row or "tag no." in row:
                hdr = i; break
        if hdr is None:
            continue
        row = [str(v).strip().lower() for v in d.iloc[hdr].tolist()]
        col = next(j for j, v in enumerate(row) if v.startswith("tag"))
        for v in d.iloc[hdr + 1:, col].dropna():
            n = norm(v)
            if len(n) >= 3 and n not in ("TAG",):
                tags.setdefault(sn, set()).add(n)
    return tags

if __name__ == "__main__":
    for pdf, xls in PAIRS:
        pdf_path = os.path.join(BASE, pdf)
        if not os.path.exists(pdf_path):
            print(f"Skipping {pdf} (not found)")
            continue
        t0 = time.time()
        r = process_pdf(pdf_path)
        got = {norm(x["tag"]) for x in r["io"] if x["tag"]}
        ref = ref_tags(xls)
        allref = set().union(*ref.values()) if ref else set()
        rt, rd = ref_cols(xls)
        gd = {norm(x["description"]) for x in r["io"] if not x["spare"]}
        dh = len(rd & gd)
        hit = len(allref & got)
        print(f"{pdf[:36]:38s} sheets={len(r['sheets']):3d} io_rows={len(r['io']):3d} spare={sum(1 for x in r['io'] if x['spare']):3d} "
              f"tags={len(got):3d} | ref_tags={len(allref):3d} matched={hit:3d} ({(100*hit/max(1,len(allref))):.0f}%) | {time.time()-t0:.1f}s")
