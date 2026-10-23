"""Groq-powered helpers. Guardrails: the LLM never overwrites extracted PLC addresses or tags;
it fills blanks, proposes tags (flagged in remarks), classifies unknown devices, and can re-read a
low-confidence sheet from its image."""
import json
from . import learning, db
from .llm import Groq, LLMError

SYS_IO = ("You are a senior PLC/electrical engineer preparing an I/O list from drawing text. "
          "Reply with JSON only. Never invent facts; if unsure return an empty string.")


def get_client():
    return Groq(os.getenv("GROQ_API_KEY") or db.get_setting("groq_api_key"), os.getenv("GROQ_TEXT_MODEL") or db.get_setting("groq_text_model"), os.getenv("GROQ_VISION_MODEL") or db.get_setting("groq_vision_model"))


def enrich_io(rows, client=None, batch=30):
    """rows: list of dict(id, module_type, tag, description, field_device). Returns {id: {field_device, signal_kind, suggested_tag}}."""
    client = client or get_client()
    if not client.ready:
        raise LLMError("Groq API key not set")
    shots = learning.few_shot("io", limit=15)
    out = {}
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        payload = [dict(i=r["id"], type=r["module_type"], tag=r["tag"], desc=r["description"]) for r in chunk if not r.get("spare")]
        if not payload:
            continue
        user = (
            "For each I/O channel return: field_device (the plant equipment the signal belongs to, e.g. 'AIR BLOWER-B2'), "
            "signal_kind (short engineering classification such as 'Run feedback', 'Trip feedback', 'Start command', "
            "'Level switch', 'Temperature transmitter'), and suggested_tag ONLY if tag is empty (ISA-style, e.g. XA-BL-01 for a run feedback; "
            "otherwise empty string).\n"
            + ("Company conventions learned from past corrections (follow them):\n" + "\n".join(shots) + "\n" if shots else "")
            + 'Return {"rows":[{"i":<id>,"field_device":"","signal_kind":"","suggested_tag":""}]}\nChannels:\n' + json.dumps(payload))
        res = client.chat_json(SYS_IO, user)
        for r in res.get("rows", []):
            try:
                out[int(r["i"])] = r
            except (KeyError, ValueError, TypeError):
                pass
    return out


def classify_devices(devs, client=None, batch=40):
    client = client or get_client()
    if not client.ready:
        raise LLMError("Groq API key not set")
    classes = ["Push Button", "Selector Switch", "Lamp", "Relay", "Contactor", "MCB", "MCCB", "PLC Module", "Power Supply", "HMI",
               "VFD", "Sensor", "Transmitter", "Terminal Block", "Motor", "Overload Relay / Motor Protector", "Other"]
    shots = learning.few_shot("devices", "dev_class", 12)
    out = {}
    for i in range(0, len(devs), batch):
        chunk = devs[i:i + batch]
        user = (f"Classify each electrical item into exactly one of: {classes}. Use part number, tag and description.\n"
                + ("Past corrections:\n" + "\n".join(shots) + "\n" if shots else "")
                + 'Return {"items":[{"i":<id>,"class":""}]}\nItems:\n'
                + json.dumps([dict(i=d["id"], tag=d["tag"], part=d["part_no"], make=d["make"], desc=d.get("descr", "")) for d in chunk]))
        res = client.chat_json("You classify electrical components. JSON only.", user)
        for r in res.get("items", []):
            if r.get("class") in classes:
                out[int(r["i"])] = r["class"]
    return out


def reread_sheet(png_bytes, module_type, client=None):
    """Vision fallback for low-confidence / scanned sheets. Returns list of channel dicts."""
    client = client or get_client()
    if not client.ready:
        raise LLMError("Groq API key not set")
    user = (f"This is a PLC {module_type} module wiring sheet. Read every channel from left to right. For each channel return the PLC address "
            "(e.g. I0.0, Q1.3, PIW300), the TAG (instrument/signal tag), the DESCRIPTION text, the terminal numbers if shown. "
            "Use an empty string for anything not visible - do not guess. Mark spare channels with description 'SPARE'. "
            'Return {"channels":[{"plc_address":"","tag":"","description":"","terminal":""}]}')
    res = client.chat_json("You read electrical drawings precisely. JSON only.", user, image_png=png_bytes)
    return res.get("channels", [])

