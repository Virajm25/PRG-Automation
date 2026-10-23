"""Claude-powered helpers, run automatically after extraction when ANTHROPIC_API_KEY is set.

Guardrails: the model never overwrites extracted PLC addresses or tags; it fills blanks, proposes tags (flagged in remarks),
classifies unknown devices, and reads sheets the parsers could not.  Model choice per task (cost-aware):
  enrich_io / classify_devices / spec extraction -> FAST tier (Haiku);   sheet images -> STRONG tier (Sonnet)."""
import json
from . import learning
from .llm import LLMError, make_client

SYS_IO = ("You are a senior PLC/electrical engineer preparing an I/O list from drawing text. "
          "Never invent facts; if unsure return an empty string.")
CLASSES = ["Push Button", "Selector Switch", "Lamp", "Relay", "Contactor", "MCB", "MCCB", "PLC Module", "Power Supply", "HMI",
           "VFD", "Sensor", "Transmitter", "Terminal Block", "Motor", "Overload Relay / Motor Protector", "Other"]
_client = None


def get_client():
    global _client
    if _client is None:
        _client = make_client()     # Claude, or a free provider (Gemini / Groq / OpenRouter / Ollama) - see llm.make_client
    return _client


def ai_enabled():
    return get_client().ready


def _need(client):
    if not client.ready:
        raise LLMError("no AI provider configured (set ANTHROPIC_API_KEY, GEMINI_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY or OLLAMA_MODEL)")


def enrich_io(rows, client=None, batch=40):
    """rows: dicts(id, module_type, tag, description, spare). Returns {id: {field_device, signal_kind, suggested_tag}}."""
    client = client or get_client()
    _need(client)
    shots = learning.few_shot("io", limit=15)
    out = {}
    for i in range(0, len(rows), batch):
        payload = [dict(i=r["id"], type=r["module_type"], tag=r["tag"], desc=r["description"]) for r in rows[i:i + batch] if not r.get("spare")]
        if not payload:
            continue
        user = (
            "For each I/O channel return: field_device (the plant equipment the signal belongs to, e.g. 'AIR BLOWER-B2'), "
            "signal_kind (short classification such as 'Run feedback', 'Trip feedback', 'Start command', 'Level switch', "
            "'Temperature transmitter'), and suggested_tag ONLY if tag is empty (ISA-style, e.g. XA-BL-01; otherwise empty string).\n"
            + ("Company conventions learned from past corrections (follow them):\n" + "\n".join(shots) + "\n" if shots else "")
            + 'Return {"rows":[{"i":<id>,"field_device":"","signal_kind":"","suggested_tag":""}]}\nChannels:\n' + json.dumps(payload))
        for r in client.json_call(SYS_IO, user, model=client.fast).get("rows", []):
            try:
                out[int(r["i"])] = r
            except (KeyError, ValueError, TypeError):
                pass
    return out


def classify_devices(devs, client=None, batch=60):
    client = client or get_client()
    _need(client)
    shots = learning.few_shot("devices", "dev_class", 12)
    out = {}
    for i in range(0, len(devs), batch):
        chunk = devs[i:i + batch]
        user = (f"Classify each electrical item into exactly one of: {CLASSES}. Use part number, tag and description.\n"
                + ("Past corrections:\n" + "\n".join(shots) + "\n" if shots else "")
                + 'Return {"items":[{"i":<id>,"class":""}]}\nItems:\n'
                + json.dumps([dict(i=d["id"], tag=d["tag"], part=d["part_no"], make=d["make"], desc=d.get("descr", "")) for d in chunk]))
        for r in client.json_call("You classify electrical components.", user, model=client.fast).get("items", []):
            if r.get("class") in CLASSES:
                out[int(r["i"])] = r["class"]
    return out


def reread_sheet(png_bytes, module_type, client=None):
    """Vision read of one PLC module sheet (STRONG tier). Returns list of channel dicts."""
    client = client or get_client()
    _need(client)
    user = (f"This is a PLC {module_type} module wiring sheet. Read every channel from left to right. For each channel return the PLC address "
            "(e.g. I0.0, Q1.3, PIW300), the TAG (instrument/signal tag), the DESCRIPTION text, the terminal numbers if shown. "
            "Use an empty string for anything not visible - do not guess. Mark spare channels with description 'SPARE'. "
            'Return {"channels":[{"plc_address":"","tag":"","description":"","terminal":""}]}')
    return client.json_call("You read electrical drawings precisely.", user, image_png=png_bytes).get("channels", [])


def read_scanned_sheet(png_bytes, client=None):
    """Scanned (raster) page when no OCR engine is available: identify the sheet, read PLC channels and recognise schematic symbols in one
    vision call (STRONG tier).  Returns dict(sheet_type, title, channels, devices, wires, contacts)."""
    client = client or get_client()
    _need(client)
    user = ("Identify this electrical drawing sheet. sheet_type is one of DI, DO, AI, AO, RTD, TC (PLC module wiring), BOM, SCHEMATIC, OTHER. "
            "title is the drawing title. For PLC module sheets list every channel (plc_address, tag, description, terminal). "
            "For schematics recognise the electrical symbols: devices = every symbol with its tag, type (push button, selector switch, lamp, relay, "
            "contactor, MCB, MCCB, overload, motor, terminal, PLC module, transformer, other), pins and rating; wires = wire numbers with the two "
            "connected tag:pin ends; contacts = every relay/contactor coil or contact with tag, role (coil/contact), contact_type (NO/NC) and pins. "
            "Use empty strings for anything not visible - never guess. "
            'Return {"sheet_type":"","title":"","channels":[{"plc_address":"","tag":"","description":"","terminal":""}],'
            '"devices":[{"tag":"","type":"","pins":"","rating":""}],"wires":[{"wire_no":"","from":"","to":""}],'
            '"contacts":[{"tag":"","role":"","contact_type":"","pins":""}]}')
    return client.json_call("You read electrical drawings precisely.", user, image_png=png_bytes, max_tokens=8000)


def read_ga_layout(png_bytes, client=None):
    """General-arrangement drawing image -> what the text layer cannot give: door/front layout items (number, device, size, row)
    and the views with their overall dimensions. STRONG (vision) tier."""
    client = client or get_client()
    _need(client)
    user = ("This is a control-panel general arrangement drawing. List every numbered or labelled item drawn on the doors / front view "
            "(push buttons, lamps, selector switches, meters, HMI, fans, filters, hooter, locks, drawing pocket ...) with its item number as printed, "
            "device type, colour if shown, cut-out or size in mm if shown, and its position (door left/right, row from top). "
            "Also list each view (front, side, top, internal) with the overall dimensions written on it. Never guess values that are not printed. "
            'Return {"items":[{"no":"","device":"","colour":"","size_mm":"","position":""}],"views":[{"view":"","dimensions":""}]}')
    return client.json_call("You read electrical panel drawings precisely.", user, image_png=png_bytes, max_tokens=6000)


def extract_specs(text, sheet_num, draw_type, client=None):
    client = client or get_client()
    if not client.ready:
        return []
    user = (f"Sheet: {sheet_num}\nType: {draw_type}\nExtract all physical specs, dimensions, materials and electrical ratings. "
            'Return {"specs":[{"category":"","property_name":"","property_value":"","unit":""}]}\nText:\n' + text[:6000])
    try:
        data = client.json_call("You are an expert electrical CAD drawing parser.", user, model=client.fast)
    except LLMError:
        return []
    return [dict(sheet=sheet_num, category=s.get("category", "General"), property_name=s["property_name"], property_value=str(s["property_value"]),
                 unit=s.get("unit", ""), raw_text=f"{s['property_name']}: {s['property_value']}")
            for s in data.get("specs", []) if s.get("property_name") and s.get("property_value")]
