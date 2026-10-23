"""Generic MCC Feeder / Circuit extractor for electrical drawing packages.

Extracts power and control circuits from:
  1. Feeder Legend Details (e.g. 'MCC+PLC PANEL LEGENDS DETAILS')
  2. Single Line Diagrams (SLD) sheets (FA, 1F to 28F, etc.)
Outputs structured circuit records:
  feeder_no, description, rating_kw, starter_type, mcb, contactor, olr, cable_size, terminal, controls, sheet
"""
import re


def is_feeder_sheet(text):
    t = text.upper()
    return bool(
        "SINGLE LINE DIAGRAM" in t or "SLD" in t or
        "LEGENDS DETAILS" in t or "FEEDER DETAILS" in t or "FEEDER LIST" in t
    )


LEGEND_RE = re.compile(r'(\d+F)\s+([A-Za-z0-9\s\-_/]+?)\s+(\d+(?:\.\d+)?\s*k\s*W)(?:\s+(\d+)\s+([A-Z0-9\-_]+\s+[A-Za-z0-9\s()]+?))?(?=\s+\d+F|\s*$)', re.I)


def page_legend(txt, pno):
    """Feeder legend entries on one page: {feeder_no: dict(name, kw, controls, sheet)}."""
    legend = {}
    t_up = txt.upper()
    if "LEGENDS DETAILS" in t_up or "FEEDER DETAILS" in t_up or "FEEDER LIST" in t_up:
        for m in LEGEND_RE.finditer(txt):
            legend[m.group(1).upper()] = dict(name=re.sub(r'\s+', ' ', m.group(2)).strip(), kw=m.group(3).strip(),
                                              controls=m.group(5).strip() if m.group(5) else "", sheet=pno)
    return legend


def page_sld(page, txt, pno):
    """Feeder columns of one single-line-diagram page: {feeder_no: circuit dict}."""
    circuits = {}
    txt = (txt or "").upper()
    if not ("SINGLE LINE DIAGRAM" in txt or "SLD" in txt):
        return circuits
    words = page.extract_words()
    f_words = [w for w in words if re.match(r'^(F[A-Z0-9]+|\d+F)$', w['text']) and 180 <= w['top'] <= 340]
    if not f_words:
        f_words = [w for w in words if re.match(r'^(F[A-Z0-9]+|\d+F)$', w['text']) and 100 <= w['top'] <= 380]
    
    f_words.sort(key=lambda w: w['x0'])
    xs = [w['x0'] for w in f_words]

    for i, fw in enumerate(f_words):
        f_code = fw['text']
        fx = fw['x0']
        x_left = (xs[i-1] + fx) / 2 if i > 0 else fx - 65
        x_right = (fx + xs[i+1]) / 2 if i < len(xs) - 1 else fx + 85

        col_words = [w for w in words if x_left <= w['x0'] <= x_right and w['top'] >= fw['top']]
        col_words.sort(key=lambda w: w['top'])
        col_txt = ' '.join(w['text'] for w in col_words)

        m = re.match(r'^F(\d+)$', f_code)
        f_key = f"{m.group(1)}F" if m else f_code

        # MCB
        m_mcb = re.search(r'(\d+F-MCB|MCB-\d+|INC-MCCB)?\s*([123]P\s*,\s*\d+A)', col_txt)
        mcb = f"{m_mcb.group(1) or ''} {m_mcb.group(2)}".strip() if m_mcb else ""

        # Contactors
        m_cs = re.findall(r'(\d+F-C\d?)\s*([123]P\s*,\s*\d+A|\d+A)', col_txt)
        contactor = ", ".join(f"{c[0]} {c[1]}" for c in m_cs)

        # OLR
        m_olr = re.search(r'(\d+F-OLR)\s*(\d+(?:\.\d+)?-\d+(?:\.\d+)?A)?', col_txt)
        olr = f"{m_olr.group(1)} {m_olr.group(2) or ''}".strip() if m_olr else ""

        # Cable
        m_cable = re.search(r'(\d+(?:\.\d+)?\s*SQ\.?\s*MM)', col_txt, re.I)
        cable = m_cable.group(1).upper() if m_cable else ""
        m_cores = re.search(r'\b((?:RD|YL|YE|BL|BU|BK|GY)(?:/(?:RD|YL|YE|BL|BU|BK|GY)){1,4})\b', col_txt)
        if cable and m_cores:
            cable += f" ({m_cores.group(1)})"     # core colours printed under the cable size on the SLD

        # Motor kW
        m_kw = re.search(r'(\d+(?:\.\d+)?\s*k\s*W)', col_txt, re.I)
        kw = m_kw.group(1).replace(" ", "") if m_kw else ""

        # Starter
        upper_col = col_txt.upper()
        if re.search(r"STAR\s*[-/]?\s*DELTA|S\s*/\s*D", upper_col):
            starter = "STAR-DELTA"
        elif len(m_cs) >= 3 and re.search(r"\bC[123]\b", upper_col):
            starter = "STAR-DELTA"
        elif kw:
            starter = "DOL"
        else:
            starter = "FEEDER"

        # Motor / Terminal tag
        term_tag = ""
        for tw in col_words:
            if 350 <= tw['top'] <= 680 and re.match(r'^[A-Z]{2,4}-[A-Z0-9]+$', tw['text']):
                term_tag = tw['text']
                break

        # Description strictly above title block (top <= 680)
        desc_lines = []
        for w in col_words:
            if 540 <= w['top'] <= 680 and not re.match(r'^(M|DOL|RD/.*|TITLE.*|AQUACARE.*|\d+\s*SQ.*|\d+kW|[123]PH.*|SYSTEM.*)$', w['text']):
                desc_lines.append(w['text'])
        desc = " ".join(desc_lines)
        if "UV SYSTEM" in col_txt:
            desc = "UV SYSTEM"

        circuits[f_key] = dict(
            feeder_no=f_key,
            description=desc,
            rating_kw=kw,
            starter_type=starter,
            mcb=mcb,
            contactor=contactor,
            olr=olr,
            cable_size=cable,
            terminal=term_tag,
            controls="",
            sheet=pno
        )

    return circuits


def merge_circuits(legend, circuits):
    # merge legend details
    for f_no, leg in legend.items():
        if f_no in circuits:
            if leg["name"]:
                circuits[f_no]["description"] = leg["name"]
            if leg["kw"] and not circuits[f_no]["rating_kw"]:
                circuits[f_no]["rating_kw"] = leg["kw"]
            if leg["controls"]:
                circuits[f_no]["controls"] = leg["controls"]
        else:
            circuits[f_no] = dict(
                feeder_no=f_no,
                description=leg["name"],
                rating_kw=leg["kw"],
                starter_type="DOL",
                mcb="",
                contactor="",
                olr="",
                cable_size="",
                terminal="",
                controls=leg["controls"],
                sheet=leg["sheet"]
            )

    # Clean description noise for control feeders like FA
    if "FA" in circuits:
        circuits["FA"]["description"] = "CONTROL SUPPLY FEEDER"

    def _sort_key(k):
        m = re.search(r'\d+', k)
        return (int(m.group(0)) if m else 999, k)

    return [circuits[k] for k in sorted(circuits.keys(), key=_sort_key)]






def extract_circuits(pdf):
    """Whole-document helper (sequential): legend + SLD feeders of every page."""
    legend, circuits = {}, {}
    for pno, page in enumerate(pdf.pages, start=1):
        txt = page.extract_text() or ""
        legend.update(page_legend(txt, pno))
        circuits.update(page_sld(page, txt, pno))
    return merge_circuits(legend, circuits)
