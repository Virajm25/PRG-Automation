"""Learning from user corrections (works offline, no model retraining).

Every edited cell is stored as a correction and generalised into rules that are applied on the next extraction:
 1. CELL rules     - (entity, field, original -> corrected): identical values are auto-corrected.
 2. TOKEN rules    - when a correction replaces single words (e.g. AEARTION -> AERATION) the word substitution is learned and applied inside
                     every text field of every table, not only on identical cells.
 3. PART memory    - corrected device class / make is remembered per part number.
 4. SUFFIX memory  - corrected device class is remembered per tag suffix (-ILSW, -OLR, ...), so new tags of the same family are classed correctly.
 5. FEW-SHOT       - the latest corrections are injected into Claude prompts as examples so the AI follows the company's naming.
 Rules count their hits; they can be reviewed, deleted, exported and imported (carry knowledge to another installation).
"""
import json, re, time, difflib
from . import db

FIELDS_BY_ENTITY = {"io": ["description", "tag", "field_device", "signal_type"], "devices": ["dev_class", "make", "descr"],
                    "bom_lines": ["descr", "make", "part_no", "rating"], "cables": ["cable_type", "cores", "source_equipment", "dest_equipment"],
                    "wires": ["device_ref", "cable_ref"], "terminals": ["terminal_type", "signal_tag", "outgoing_wire"], "circuits": ["description", "starter_type"]}
TEXT_FIELDS = {"io": ["description", "field_device"], "devices": ["descr"], "bom_lines": ["descr"], "cables": ["source_equipment", "dest_equipment", "cable_type"],
               "terminals": ["outgoing_wire"], "circuits": ["description"], "wires": ["device_ref"]}
WORD = re.compile(r"[A-Za-z]{3,}")


def _upsert(entity, field, original, corrected):
    db.x("INSERT INTO rules(entity,field,original,corrected,hits) VALUES(?,?,?,?,0) "
         "ON CONFLICT(entity,field,original) DO UPDATE SET corrected=excluded.corrected", (entity, field, original, corrected))


def _suffix(tag):
    m = re.search(r"-([A-Za-z]{1,6})\d*$", tag or "")
    return m.group(1).upper() if m else ""


def record(entity, field, original, corrected, context=None):
    original, corrected = (original or "").strip(), (corrected or "").strip()
    if original == corrected:
        return
    db.x("INSERT INTO corrections(entity,field,original,corrected,context,created) VALUES(?,?,?,?,?,?)",
         (entity, field, original, corrected, json.dumps(context or {}), time.time()))
    if original:
        _upsert(entity, field, original, corrected)
    ctx = context or {}
    if entity == "devices" and field in ("dev_class", "make") and ctx.get("part_no"):
        _upsert("devices", f"{field}@part", ctx["part_no"].upper(), corrected)
    if entity == "devices" and field == "dev_class" and _suffix(ctx.get("tag")):
        _upsert("devices", "dev_class@suffix", _suffix(ctx["tag"]), corrected)
    if field in TEXT_FIELDS.get(entity, []) and original and corrected:
        a, b = original.split(), corrected.split()
        if len(a) == len(b):
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
                if op == "replace" and i2 - i1 == j2 - j1:
                    for x, y in zip(a[i1:i2], b[j1:j2]):
                        if WORD.fullmatch(x) and WORD.fullmatch(y) and x.upper() != y.upper() and difflib.SequenceMatcher(a=x.upper(), b=y.upper()).ratio() >= 0.6:
                            _upsert("*", "token", x.upper(), y.upper())     # only near-misses (spelling), never arbitrary renames


def load_rules():
    return {(r["entity"], r["field"], r["original"]): r["corrected"] for r in db.q("SELECT entity,field,original,corrected FROM rules")}


def _case_like(src, repl):
    return repl if not src.isalpha() or src.isupper() else (repl.title() if src[:1].isupper() else repl.lower())


def apply_rules(entity, rows, rules=None):
    """Mutates rows in place. Returns number of auto-corrections made (and counts rule hits)."""
    rules = rules if rules is not None else load_rules()
    tokens = {k[2]: v for k, v in rules.items() if k[0] == "*" and k[1] == "token"}
    hits, n = {}, 0

    def hit(key):
        hits[key] = hits.get(key, 0) + 1
    for r in rows:
        if entity == "devices":
            pn = (r.get("part_no") or "").upper()
            for f in ("dev_class", "make"):
                v = rules.get(("devices", f"{f}@part", pn))
                if v and r.get(f) != v:
                    r[f] = v; n += 1; hit(("devices", f"{f}@part", pn))
            sfx = _suffix(r.get("tag"))
            v = rules.get(("devices", "dev_class@suffix", sfx)) if sfx else None
            if v and r.get("dev_class") in ("Other", "", None):
                r["dev_class"] = v; n += 1; hit(("devices", "dev_class@suffix", sfx))
        for f in FIELDS_BY_ENTITY.get(entity, []):
            cur = (r.get(f) or "").strip()
            v = rules.get((entity, f, cur))
            if v is not None and v != r.get(f):
                r[f] = v; n += 1; hit((entity, f, cur))
        if tokens:
            for f in TEXT_FIELDS.get(entity, []):
                val = r.get(f)
                if not isinstance(val, str) or not val:
                    continue
                new = WORD.sub(lambda m: _case_like(m.group(0), tokens[m.group(0).upper()]) if m.group(0).upper() in tokens else m.group(0), val)
                if new != val:
                    for w in WORD.findall(val):
                        if w.upper() in tokens:
                            hit(("*", "token", w.upper()))
                    r[f] = new; n += 1
    for (e, f, o), c in hits.items():
        db.x("UPDATE rules SET hits=hits+? WHERE entity=? AND field=? AND original=?", (c, e, f, o))
    return n


def few_shot(entity, field=None, limit=12):
    sql = "SELECT field,original,corrected FROM corrections WHERE entity=?"
    args = [entity]
    if field:
        sql += " AND field=?"; args.append(field)
    rows = db.q(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))
    return [f'{r["field"]}: "{r["original"]}" -> "{r["corrected"]}"' for r in rows]


def export_rules():
    return db.q("SELECT entity,field,original,corrected,hits FROM rules ORDER BY id")


def import_rules(rows):
    n = 0
    for r in rows:
        if r.get("entity") and r.get("field") and r.get("original") is not None and r.get("corrected") is not None:
            _upsert(r["entity"], r["field"], str(r["original"]), str(r["corrected"])); n += 1
    return n


def delete_rule(rule_id):
    db.x("DELETE FROM rules WHERE id=?", (rule_id,))
