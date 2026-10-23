"""Learning from user corrections.

Two mechanisms (both work offline, no model retraining needed for the MVP):
 1. RULES  - every edited cell stores (entity, field, original -> corrected). On the next extraction,
             identical values are auto-corrected, and a part-number -> class/make mapping is remembered.
 2. FEW-SHOT - the most recent corrections are injected into Groq prompts as examples, so the LLM
             follows the company's naming conventions.
A later phase can export the correction log as a fine-tuning / template-training dataset.
"""
import json, time
from . import db

FIELDS_BY_ENTITY = {"io": ["description", "tag", "field_device", "signal_type"], "devices": ["dev_class", "make", "descr"]}


def record(entity, field, original, corrected, context=None):
    original, corrected = (original or "").strip(), (corrected or "").strip()
    if original == corrected:
        return
    db.x("INSERT INTO corrections(entity,field,original,corrected,context,created) VALUES(?,?,?,?,?,?)",
         (entity, field, original, corrected, json.dumps(context or {}), time.time()))
    if original:
        db.x("INSERT INTO rules(entity,field,original,corrected,hits) VALUES(?,?,?,?,0) "
             "ON CONFLICT(entity,field,original) DO UPDATE SET corrected=excluded.corrected", (entity, field, original, corrected))
    # part-number memory: when the class/make of a part is corrected, remember it for every future occurrence
    if entity == "devices" and field in ("dev_class", "make") and context and context.get("part_no"):
        db.x("INSERT INTO rules(entity,field,original,corrected,hits) VALUES(?,?,?,?,0) "
             "ON CONFLICT(entity,field,original) DO UPDATE SET corrected=excluded.corrected",
             ("devices", f"{field}@part", context["part_no"].upper(), corrected))


def load_rules():
    rows = db.q("SELECT entity,field,original,corrected FROM rules")
    d = {}
    for r in rows:
        d[(r["entity"], r["field"], r["original"])] = r["corrected"]
    return d


def apply_rules(entity, rows, rules=None):
    """Mutates rows in place. Returns number of auto-corrections made."""
    rules = rules if rules is not None else load_rules()
    n = 0
    for r in rows:
        if entity == "devices":
            pn = (r.get("part_no") or "").upper()
            for f in ("dev_class", "make"):
                v = rules.get(("devices", f"{f}@part", pn))
                if v and r.get(f) != v:
                    r[f] = v; n += 1
        for f in FIELDS_BY_ENTITY.get(entity, []):
            v = rules.get((entity, f, (r.get(f) or "").strip()))
            if v is not None and v != r.get(f):
                r[f] = v; n += 1
    return n


def few_shot(entity, field=None, limit=12):
    sql = "SELECT field,original,corrected FROM corrections WHERE entity=?"
    args = [entity]
    if field:
        sql += " AND field=?"; args.append(field)
    rows = db.q(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
    return [f'{r["field"]}: "{r["original"]}" -> "{r["corrected"]}"' for r in rows]
