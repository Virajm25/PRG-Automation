"""Drawing sheet classifier and title-block metadata extractor.

Classifies each PDF page into one of the recognised drawing categories and
extracts the structured engineering metadata from the title block.
"""
import re

# --------------------------------------------------------------------------- #
# Ordered classification rules: (label, pattern-list-ANY, anti-pattern-list)  #
# First match wins.                                                            #
# --------------------------------------------------------------------------- #
_RULES = [
    # --- TOC first to avoid false matches ---
    ("table_of_contents", [r"TABLE\s+OF\s+CONTENTS?", r"PAGE\s+PAGE\s+DESCRIPTION"], []),

    # --- title page: cover sheet with revision history table ---
    ("title_page", [
        r"REVISION\s+HISTORY",
        r"REVISION\s+NO\s+DATE\s+DESCRIPTION",
        r"(?:MS[\s\-]PRG|AQUACARESEE)\s+PVT.*REVISION",
    ], [r"TABLE\s+OF\s+CONTENTS?", r"TITLE\s*:-"]),

    # --- PLC I/O types: anchored on TITLE line for accuracy ---
    ("plc_cpu", [
        r"TITLE\s*[:\-]+[^\n]*CPU[^\n]*(?:DIGITAL|DI|DO|INPUT|OUTPUT)",
        r"TITLE\s*[:\-]+[^\n]*CPU[^\n]*WIRING",
        r"CPU\s+DVP",
    ], []),
    ("plc_di", [
        r"TITLE\s*[:\-]+[^\n]*DIGITAL\s+INPUT",
        r"TITLE\s*[:\-]+[^\n]*\bDI\b[^\n]*WIRING",
    ], []),
    ("plc_do", [
        r"TITLE\s*[:\-]+[^\n]*DIGITAL\s+OUTPUT",
        r"TITLE\s*[:\-]+[^\n]*\bDO\b[^\n]*WIRING",
    ], []),
    ("plc_ai", [
        r"TITLE\s*[:\-]+[^\n]*ANALOG\s+INPUT",
        r"TITLE\s*[:\-]+[^\n]*\bAI\b[^\n]*WIRING",
    ], []),
    ("plc_ao", [
        r"TITLE\s*[:\-]+[^\n]*ANALOG\s+OUTPUT",
        r"TITLE\s*[:\-]+[^\n]*\bAO\b[^\n]*WIRING",
    ], []),

    # --- layout / arrangement ---
    ("external_ga", [r"EXTERNAL\s+GENERAL\s+ARRANG", r"EXTERNAL\s+GA\b"], []),
    ("internal_ga", [r"INTERNAL\s+GENERAL\s+ARRANG", r"INTERNAL\s+GA\b", r"MOUNTING\s+VIEW"], []),

    # --- SLD ---
    ("sld", [r"SINGLE\s+LINE\s+DIAGRAM", r"\bSLD\b"], [r"LEGENDS?\s+DETAILS?"]),

    # --- feeder legend ---
    ("feeder_legend", [r"LEGENDS?\s+DETAILS?", r"FEEDER\s+(?:LIST|DETAILS)", r"FEEDER\s+SR\."], []),

    # --- individual power/control feeder sheets ---
    ("power_control", [
        r"\d+F[-\s]MCB",
        r"3PH,?\s*415V,?\s*50Hz",
        r"POWER\s+(?:AND|&)\s+CONTROL",
        r"230\s*VAC\s+(?:CONTROL|POWER)\s+SUPPLY",
        r"INCOMER\s+WIRING",
        r"VAC\s*(?:&|AND)\s*24\s*VDC\s+DISTRIBUTION",
    ], [r"SINGLE\s+LINE", r"LEGENDS?\s+DETAILS?"]),

    # --- generic PLC wiring overview ---
    ("plc_general", [r"PLC\s+WIRING\s+DIAGRAM", r"PLC\s+WIRING"], []),

    # --- terminal ---
    ("terminal_diagram", [r"TERMINAL\s+DETAILS?", r"TERMINAL\s+SCHEDULE", r"FERRULE\s+DIAGRAM"], []),

    # --- cable schedule ---
    ("cable_schedule", [r"CABLE\s+SCHEDULE", r"CABLE\s+LIST"], []),

    # --- BOM ---
    ("bom", [r"BILL\s+OF\s+MATERIAL", r"\bBOM\b", r"COMPONENT\s+SCHEDULE",
             r"MATERIAL\s+LIST", r"PANEL\s+BOM"], []),

    # --- specs ---
    ("general_specs", [r"GENERAL\s+SPECIFICATION", r"WIRING\s+COLOUR\s+CODING",
                       r"A\.\s+REFERENCES", r"LEGEND\s+&\s+ABBREVIATION"], []),
]

_COMPILED = [(lbl,
              [re.compile(p, re.I | re.DOTALL) for p in pos],
              [re.compile(p, re.I | re.DOTALL) for p in neg])
             for lbl, pos, neg in _RULES]

# Human-readable display labels
DRAW_TYPE_LABEL = {
    "title_page":        "Title Page",
    "table_of_contents": "Table of Contents",
    "general_specs":     "General Specifications",
    "external_ga":       "External General Arrangement",
    "internal_ga":       "Internal General Arrangement",
    "sld":               "Single Line Diagram",
    "feeder_legend":     "Feeder Legend Details",
    "power_control":     "Power & Control Circuit",
    "plc_cpu":           "PLC CPU Wiring",
    "plc_di":            "PLC DI Wiring",
    "plc_do":            "PLC DO Wiring",
    "plc_ai":            "PLC AI Wiring",
    "plc_ao":            "PLC AO Wiring",
    "plc_general":       "PLC Wiring Diagram",
    "terminal_diagram":  "Terminal Diagram",
    "cable_schedule":    "Cable Schedule",
    "bom":               "Bill of Materials",
    "unknown":           "Unknown",
}


def classify_sheet(text: str) -> str:
    """Return a DRAW_TYPE string for the given page text."""
    probe = text[:2500]
    for label, pos_pats, neg_pats in _COMPILED:
        if any(p.search(probe) for p in pos_pats):
            if not any(n.search(probe) for n in neg_pats):
                return label
    return "unknown"


# --------------------------------------------------------------------------- #
# Title-block metadata extraction                                              #
# --------------------------------------------------------------------------- #
# The title block in these drawings is at the BOTTOM of the page. pdfplumber
# extracts text top-to-bottom, so the title block content appears at the END
# of the extracted text string.
_TITLE_BLOCK_CHARS = 600  # tail characters where title-block lives


def extract_title_block(text: str) -> dict:
    """Extract structured metadata from a drawing title block.

    The title block appears at the bottom of each drawing. We look at the
    last ~600 chars for most metadata fields, and the full first 2500 chars
    for the drawing title (TITLE:- line).

    Returns a dict with keys:
      drawing_title, sheet_no, drawing_no, revision, section_view,
      project, client, date, system
    """
    head = text[:2500]   # for TITLE line
    tail = text[-_TITLE_BLOCK_CHARS:] if len(text) > _TITLE_BLOCK_CHARS else text

    def _find_in(t, patterns, fallback=""):
        for rx in patterns:
            m = re.search(rx, t, re.I)
            if m:
                v = m.group(1).strip()
                v = re.split(r"\s{3,}|\t|\||\n", v)[0].strip()
                v = re.sub(r"\s+", " ", v)
                if v:
                    return v[:120]
        return fallback

    # TITLE from TITLE:- line in the body
    title = _find_in(head, [
        r"TITLE\s*[:\-]+\s*([^\n]{4,})",
    ])
    if title:
        # strip trailing noise like SYSTEM / PROJECT labels
        title = re.split(r"\s{2,}|\bSYSTEM\s*[:\-]|\bPROJECT\s*[:\-]", title)[0].strip()

    # Sheet number: "X/Y" format in the tail (title block)
    # In these drawings the sheet number is in the form "45/47" in the tail
    sheet_no = _find_in(tail, [
        r"\b(\d{1,3}\s*/\s*\d{1,3})\b",
    ])

    drawing_no = _find_in(tail, [
        r"DRG\.?\s*NO\.?\s*[:\-]?\s*([A-Z0-9][\w\-./]{3,})",
        r"DRAWING\s*NO\.?\s*[:\-]?\s*([A-Z0-9][\w\-./]{3,})",
    ])
    # Also check head if not found
    if not drawing_no:
        drawing_no = _find_in(head, [
            r"DRG\.?\s*NO\.?\s*[:\-]?\s*([A-Z0-9][\w\-./]{3,})",
        ])

    # Revision: look for clean short revision string in tail
    # Valid: "0", "1", "R0", "00", "A"
    # Invalid: "DETAILS", "DESCRIPTION", page-count numbers (e.g. "47") etc.
    revision = _find_in(tail, [
        r"\bREV(?:ISION)?\s*(?:NO\.?)?\s*[:\-]?\s*([A-Z0-9]{1,4})\b(?!\s*[A-Z]{3,})",
        r"\b([0-9]{1,2})\s+AS\s+BUILT\b",
        r"\b([0-9]{1,2})\s+FOR\s+APPROVAL\b",
    ])
    if revision:
        if re.search(r"[A-Z]{2,}", revision):  # "DETA" etc. → discard
            revision = ""
        elif revision.isdigit() and int(revision) > 20:  # page-count artefact (e.g. "47") → discard
            revision = ""

    section_view = _find_in(head, [
        r"SECTION\s*[:\-]?\s*([^\n]{3,50})",
        r"VIEW\s*[:\-]?\s*([^\n]{3,40})",
    ])

    project = _find_in(tail, [
        r"PROJECT\s*(?:NAME)?\s*[:\-]+\s*([^\n]{4,80})",
    ])
    if not project:
        project = _find_in(head, [r"PROJECT\s*(?:NAME)?\s*[:\-]+\s*([^\n]{4,80})"])

    client = _find_in(tail, [
        r"CLIENT\s*[:\-]+\s*([^\n]{4,80})",
        r"CUSTOMER\s*(?:NAME)?\s*[:\-]+\s*([^\n]{4,80})",
    ])
    if not client:
        client = _find_in(head, [r"CLIENT\s*[:\-]+\s*([^\n]{4,80})"])

    date = _find_in(tail, [
        r"\b(\d{2}[-/]\d{2}[-/]\d{4})\b",
        r"\b(\d{2}[-/]\d{2}[-/]\d{2})\b",
    ])

    system = _find_in(head, [
        r"SYSTEM\s*[:\-]+\s*([^\n]{4,80})",
    ])

    return dict(
        drawing_title=title,
        sheet_no=sheet_no,
        drawing_no=drawing_no,
        revision=revision,
        section_view=section_view,
        project=project,
        client=client,
        date=date,
        system=system,
    )
