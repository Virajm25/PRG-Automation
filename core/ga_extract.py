# -*- coding: utf-8 -*-
"""Data extractor for General Arrangement (External & Internal GA) and Specifications drawings."""
import re, json
from typing import List, Dict, Any, Optional

def extract_panel_specs_from_text(text: str, sheet_num: int = 0, draw_type: str = "") -> List[Dict[str, Any]]:
    specs = []
    
    def add(cat: str, prop: str, val: str, unit: str = "", raw: str = ""):
        if val and str(val).strip():
            specs.append({
                "sheet": sheet_num,
                "category": cat,
                "property_name": prop,
                "property_value": str(val).strip(),
                "unit": unit.strip(),
                "raw_text": (raw or str(val)).strip()
            })

    # 1. Dimensions
    m_dim = re.search(r"(\d{3,4})(?:\+(\d{2,3}))?\s*\([Hh]\)\s*[+xX*]*\s*(\d{3,4})\s*\([Ww]\)\s*[xX*]\s*(\d{2,4})\s*\([Dd]\)", text)
    if not m_dim:
        m_dim = re.search(r"(\d{3,4})\s*[xX*]\s*(\d{3,4})\s*[xX*]\s*(\d{2,4})", text)
        if m_dim:
            h, w, d = m_dim.group(1), m_dim.group(2), m_dim.group(3)
            plinth = ""
        else:
            h = w = d = plinth = ""
    else:
        h, plinth, w, d = m_dim.group(1), m_dim.group(2) or "", m_dim.group(3), m_dim.group(4)

    if not plinth:
        m_pl = re.search(r"PLINTH\s*(?:HEIGHT)?\s*[:\-]?\s*(\d{2,3})\s*MM", text, re.I)
        if m_pl:
            plinth = m_pl.group(1)

    if h:
        dim_str = f"{h}" + (f"+{plinth}" if plinth else "") + f"(H) x {w}(W) x {d}(D)"
        add("Dimensions", "Panel Dimensions (H x W x D)", dim_str, "mm", dim_str)
        add("Dimensions", "Height", h, "mm")
        add("Dimensions", "Width", w, "mm")
        add("Dimensions", "Depth", d, "mm")
        if plinth:
            add("Dimensions", "Plinth Height", plinth, "mm")
            try:
                tot_h = int(h) + int(plinth)
                add("Dimensions", "Total Height (inc. Plinth)", str(tot_h), "mm")
            except ValueError:
                pass

    # 2. Make of Panel
    if draw_type in ("external_ga", "general_specs") or "PANEL DETAILS" in text.upper():
        if re.search(r"M\.?S\.?\s+FABRICATED", text, re.I):
            add("Construction", "Panel Construction / Make", "M.S FABRICATED")
        elif re.search(r"CRCA\s+SHEET", text, re.I):
            add("Construction", "Panel Construction / Make", "CRCA SHEET FABRICATED")

    # 3. Sheet Metal Thicknesses (only for External GA / General Specs)
    if draw_type in ("external_ga", "general_specs") or "THICKNESS OF SHEET" in text.upper():
        thk_matches = re.findall(r"([0-9.]+\s*MM\s*\([A-Z\s]+\))", text, re.I)
        if len(thk_matches) >= 4:
            add("Sheet Metal", "Enclosure Thickness", thk_matches[0])
            add("Sheet Metal", "Door Thickness", thk_matches[1])
            add("Sheet Metal", "Mounting Plate Thickness", thk_matches[2])
            add("Sheet Metal", "Gland Plate Thickness", thk_matches[3])
        elif len(thk_matches) >= 1:
            add("Sheet Metal", "Sheet Thickness", thk_matches[0])

    # 4. Degree of Protection
    m_ip = re.search(r"IP\s*[-]?\s*([0-9]{2}(?:\s+[A-Za-z]+)?)", text, re.I)
    if m_ip:
        add("Enclosure", "Degree of Protection", f"IP-{m_ip.group(1).strip()}")
    elif "IP42" in text.upper():
        add("Enclosure", "Degree of Protection", "IP-42 INDOOR")
    elif "IP54" in text.upper():
        add("Enclosure", "Degree of Protection", "IP-54 INDOOR")
    elif "IP55" in text.upper():
        add("Enclosure", "Degree of Protection", "IP-55 INDOOR")

    # 5. Cable Entry & Gland Mounting
    if "AT TOP" in text.upper() or "FROM TOP SIDE" in text.upper():
        add("Installation", "Cable Entry", "AT TOP")
    elif "AT BOTTOM" in text.upper() or "FROM BOTTOM SIDE" in text.upper():
        add("Installation", "Cable Entry", "AT BOTTOM")

    m_gland = re.search(r"UNDRILLED\s+GLAND\s+PLATE[^\r\n]*", text, re.I)
    if m_gland:
        add("Installation", "Gland Mounting", m_gland.group(0).strip())

    # 6. Paint / Colour
    if re.search(r"RAL\s*7035|SIEMENS\s+GREY", text, re.I):
        add("Paint & Finish", "Panel External Colour", "SIEMENS GREY RAL 7035 (STRUCTURE FINISH)")
    if re.search(r"DIP\s+ORANGE|RAL\s*2000", text, re.I):
        add("Paint & Finish", "Mounting Plate Colour", "DIP ORANGE")

    # 7. Hardware & Access
    if re.search(r"LIFTING\s+EYEBOLTS?.*PROVIDED|PROVIDED\s+AT\s+TOP", text, re.I):
        add("Hardware", "Lifting Eyebolts", "PROVIDED AT TOP")

    if re.search(r"REAR\s+ACCESS\s*[:\-]?\s*NO\b", text, re.I):
        add("Access", "Rear Access", "NO (FRONT ACCESS ONLY)")
    elif re.search(r"REAR\s+ACCESS\s*[:\-]?\s*YES\b", text, re.I):
        add("Access", "Rear Access", "YES")

    if re.search(r"HINGED\s+DOORS?|FRONT\s+DOOR\s+OPENING", text, re.I):
        add("Access", "Door Type", "HINGED DOORS WITH FRONT OPENING")

    # 8. Electrical Ratings
    if "POWER CUM PLC PANEL" in text.upper():
        add("Electrical Ratings", "Type of System", "POWER CUM PLC PANEL")

    if re.search(r"415\s*VAC.*3\s*PH", text, re.I):
        add("Electrical Ratings", "Rated Incoming Voltage", "415V AC, 3 Phase, 4 Wire, 50 Hz")

    if re.search(r"230\s*VAC\s+(?:CONTROL|SUPPLY)|RATED\s+CONTROL.*230", text, re.I):
        add("Electrical Ratings", "Rated Control Voltage (AC)", "230V AC")
    if re.search(r"24\s*VDC\s+(?:CONTROL|SUPPLY)", text, re.I):
        add("Electrical Ratings", "Rated Control Voltage (DC)", "24V DC")

    m_freq = re.search(r"50\s*Hz[^\r\n]*", text, re.I)
    if m_freq:
        add("Electrical Ratings", "Rated Frequency", m_freq.group(0).strip())

    if re.search(r"40\s*Â°?\s*C|45\s*Â°?\s*C", text, re.I):
        add("Environment", "Ambient Temperature", "40 deg C")

    # 9. Internal GA Components
    if draw_type == "internal_ga" or "INTERNAL GENERAL ARRANG" in text.upper() or "MOUNTING VIEW" in text.upper():
        m_plc = re.search(r"((?:DELTA|SIEMENS|SCHNEIDER|ALLEN\s+BRADLEY|MITSUBISHI|ABB)\s+PLC[^\r\n]*)", text, re.I)
        if m_plc:
            add("Internal Layout", "PLC System", m_plc.group(1).strip())

        smps_matches = re.findall(r"SMPS[-\s]\d+[\s\r\n]+(?:\d+AMP[\s\r\n]+)?24\s*VDC", text, re.I)
        if smps_matches:
            add("Internal Layout", "Power Supplies (SMPS)", " | ".join(re.sub(r"\s+", " ", s).strip() for s in smps_matches[:5]))
        elif re.search(r"24\s*VDC.*SMPS|SMPS.*24\s*VDC", text, re.I | re.DOTALL):
            add("Internal Layout", "Power Supplies (SMPS)", "24V DC SMPS Power Supply")

        m_inc_b = re.search(r"(?:INC-MCCB|MAIN\s+INCOMER)[\s\r\n]+([0-9A-Z,\s]+MCCB)", text, re.I)
        if m_inc_b:
            add("Internal Layout", "Main Incomer Breaker", re.sub(r"\s+", " ", m_inc_b.group(0)).strip())
        else:
            m_b = re.search(r"3P,?\s*\d+\s*AMP\s*MCCB", text, re.I)
            if m_b:
                add("Internal Layout", "Main Incomer Breaker", m_b.group(0))

        ducts = set(re.findall(r"CABLE\s+DUCT\s*([0-9*xX]+MM)", text, re.I))
        if ducts:
            add("Internal Layout", "Cable Ducts", ", ".join(f"Cable Duct {d}" for d in sorted(ducts)))

        r_boards = re.findall(r"RELAY\s+BOARD[-\s]\d+[\s\r\n]+(?:\d+\s+CHANNEL[^\r\n]*)?", text, re.I)
        if r_boards:
            add("Internal Layout", "Relay Boards", " | ".join(re.sub(r"\s+", " ", r).strip() for r in r_boards[:6]))

    # 10. External GA Front Items
    if draw_type == "external_ga" or "EXTERNAL GENERAL ARRANG" in text.upper() or "FRONT VIEW" in text.upper():
        eq = []
        if re.search(r"\bHMI\b", text, re.I): eq.append("Touchscreen HMI (Door Mounted)")
        if re.search(r"\bMFM\b|ENERGY\s+METER", text, re.I): eq.append("Multifunction Meter (MFM)")
        if re.search(r"HOOTER|ALARM", text, re.I): eq.append("Electronic Hooter / Alarm")
        if re.search(r"EMERGENCY\s+STOP|PB3", text, re.I): eq.append("Emergency Stop Pushbutton")
        if re.search(r"TUBE\s*LIGHT|TL-1|TL-2", text, re.I): eq.append("Panel Illumination Lamp (Tube Light)")
        if re.search(r"DOOR\s+LIMIT\s+SWITCH|DLS-1|DLS-2", text, re.I): eq.append("Door Limit Switch (DLS)")
        if re.search(r"COOLING\s+FAN|FAN\+AIR\s+FILTER", text, re.I): eq.append("Cooling Fan with Air Filter (Louvers)")
        if re.search(r"CONTROL\s+TRANSFORMER|CONTROL-TFR", text, re.I): eq.append("Control Transformer")
        if eq:
            add("Front Equipment", "Mounted Equipment & Controls", "; ".join(eq))

    return specs


def extract_with_llm(text: str, sheet_num: int, draw_type: str, client) -> List[Dict[str, Any]]:
    if not client or not client.ready:
        return []
    
    system_prompt = (
        "You are an expert electrical CAD drawing parser. Given raw text from an engineering sheet, "
        "extract all physical specs, dimensions, materials, electrical ratings. Return JSON with "
        "specs: [{category, property_name, property_value, unit}]."
    )
    user_prompt = f"Sheet: {sheet_num}\nType: {draw_type}\nText:\n{text[:3500]}"
    try:
        data = client.chat_json(system_prompt, user_prompt, model=client.text_model)
        out = []
        for item in data.get("specs", []):
            if item.get("property_name") and item.get("property_value"):
                out.append({
                    "sheet": sheet_num,
                    "category": item.get("category", "General"),
                    "property_name": item["property_name"],
                    "property_value": str(item["property_value"]),
                    "unit": item.get("unit", ""),
                    "raw_text": f"{item['property_name']}: {item['property_value']}"
                })
        return out
    except Exception as e:
        print(f"Groq extraction error: {e}")
        return []

