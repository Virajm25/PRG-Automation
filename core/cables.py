"""Cable schedule and ferrule derivation.

Cable numbers/types are rarely printed on panel drawings, so rows are *derived* from what is: feeder circuits (power cables to
motors) and analog/temperature I/O (instrument cables).  Every derived row carries source='derived' and a remark so it is
reviewed in the editor before export.  Cable numbers printed on the drawing (io_rows.cable_no) are kept as-is.
"""
import re

THREE_PHASE = re.compile(r"DOL|STAR|VFD|SOFT|RDOL|FDOL|FWD|REV", re.I)
CONDUCTOR_BY_TYPE = {"AI": "1P x 1.0 sq.mm shielded", "AO": "1P x 1.0 sq.mm shielded", "RTD": "1T (3-wire) x 1.0 sq.mm shielded", "TC": "1P thermocouple ext. cable"}


def _size(s):
    m = re.search(r"(\d+(?:\.\d+)?)\s*SQ", s or "", re.I)
    return m.group(1) if m else ""


def build_cables(circuits, io_rows, panel=""):
    out, seen = [], set()
    for c in circuits:
        f = c.get("feeder_no", "")
        size = _size(c.get("cable_size"))
        if not f or not size:
            continue
        three = THREE_PHASE.search(c.get("starter_type", "") or "DOL") is not None
        cores = "3C + PE" if three else "2C + PE"
        m_col = re.search(r"\(([A-Z]{2}(?:/[A-Z]{2})+)\)", c.get("cable_size", ""))
        if m_col:                           # core colours printed on the SLD give the real core count
            cores = f"{m_col.group(1).count('/') + 1}C ({m_col.group(1)})"
        no = f"{f}-PWR"
        if no in seen:
            continue
        seen.add(no)
        out.append(dict(cable_no=no, cable_type=f"Power cable, {size} sq.mm, Cu, XLPE/PVC armoured",
                        cores=f"{cores} x {size} sq.mm", source_equipment=f"{panel or 'MCC'} - feeder {f} ({c.get('rating_kw','')})".replace(" ()", ""),
                        dest_equipment=c.get("description", ""), length_m="", sheet=c.get("sheet", ""),
                        remarks="derived from SLD; core count inferred from starter type - verify", source="derived"))
    for r in io_rows:
        if r.get("spare") or not r.get("tag"):
            continue
        if r.get("cable_no"):
            no = r["cable_no"]
            if no not in seen:
                seen.add(no)
                out.append(dict(cable_no=no, cable_type="", cores="", source_equipment=r.get("field_device", ""),
                                dest_equipment=f"PLC {r.get('plc_address','')}", length_m="", sheet=r.get("sheet", ""),
                                remarks="cable number from drawing", source="drawing"))
        elif r["module_type"] in CONDUCTOR_BY_TYPE:
            no = f"IC-{r['tag']}"
            if no in seen:
                continue
            seen.add(no)
            out.append(dict(cable_no=no, cable_type="Instrument cable, overall shielded", cores=CONDUCTOR_BY_TYPE[r["module_type"]],
                            source_equipment=r.get("field_device") or r.get("description", ""), dest_equipment=f"PLC {r.get('plc_address','')} ({r['module_type']})",
                            length_m="", sheet=r.get("sheet", ""), remarks="derived from I/O list - verify", source="derived"))
    return out


def build_ferrules(io_rows, drawn_wires):
    """Drawn wire numbers (EPLAN style) + one ferrule per field wire landed on a PLC terminal strip."""
    out = []
    for w in drawn_wires:
        ref = w.get("to_ref", "")
        a, _, b = ref.partition(".")
        out.append(dict(w, from_terminal=w.get("device_ref", ""), to_terminal=f"Sheet {a} / col {b}" if a else "",
                        cable_ref=w.get("cable_ref", ""), core_no=w.get("core_no", ""), source="drawing"))
    for r in io_rows:
        if r.get("spare") or not r.get("terminal"):
            continue
        no = r.get("tag") or r.get("plc_address")
        if not no:
            continue
        out.append(dict(sheet=r["sheet"], wire_no=no, ferrule_no=no, from_terminal=f"PLC {r['plc_address']}".strip(),
                        to_terminal=r["terminal"], device_ref=r.get("field_device") or r.get("description", ""),
                        cable_ref=r.get("cable_no", ""), core_no="", to_ref="", source="derived-io"))
    return out


def attach_cables_to_io(io_rows, cables):
    """Fill io.cable_no for instrument rows from derived cables so I/O list, terminals and ferrules agree."""
    by_tag = {c["cable_no"]: c for c in cables if c["cable_no"].startswith("IC-")}
    for r in io_rows:
        if not r.get("cable_no") and f"IC-{r.get('tag')}" in by_tag:
            r["cable_no"] = f"IC-{r['tag']}"


# ----------------------------------------------------------------------------------------------------------- assembly
def symbol_devices(res, existing):
    """Devices recognised from schematic symbols (tag + class + rating) that the part-number scan did not already list."""
    have = {d.get("tag") for d in existing if d.get("tag")}
    out, seen = [], set()
    for s in res.get("symbols", []):
        tag = s["tag"]
        if tag in have or tag in seen or s["role"] in ("other",) and re.match(r"^[A-Z]\d?$", tag):
            continue
        seen.add(tag)
        out.append(dict(sheet=s["sheet"], tag=tag, make="", part_no="", rating=s["rating"], desc=s["role"], dev_class=s["dev_class"], source="symbol"))
    return out


def _traced_ferrules(nets):
    out = []
    for n in nets:
        ends = [e.replace("?:", "pin ") for e in n["ends"]]
        if not n["wire_no"] and len([e for e in n["ends"] if ":" in e and not e.startswith("?:")]) < 2:
            continue            # unnumbered nets are kept only when both ends are a resolved device pin
        a = ends[0] if ends else ""
        for b in (ends[1:] or [""]):
            out.append(dict(sheet=n["sheet"], wire_no=n["wire_no"] or "(unnumbered)", ferrule_no=n["wire_no"] or "", from_terminal=a, to_terminal=b,
                            device_ref=a.split(":")[0], cable_ref="", core_no=n["marks"].split("/")[0] if n["marks"] else "", to_ref="", source="traced"))
    return out


def assemble(res, panel=""):
    """Choose between sheet-read data (terminal-detail sheets, traced nets) and derived rows. Returns (cables, ferrules, terminals)."""
    td_t, td_c, td_f = res.get("td_terminals", []), res.get("td_cables", []), res.get("td_ferrules", [])
    has_td = len(td_t) >= 6
    cables = build_cables(res.get("circuits", []), res["io"], panel)
    if has_td:
        covered = {c["cable_no"].split("-", 1)[-1] for c in td_c}
        strips = " ".join(t["strip"] for t in td_t)
        cables = [c for c in cables if not (c["cable_no"].endswith("-PWR") and c["cable_no"][:-4] + "-" in strips + "-") and not c["cable_no"].startswith("IC-")]
        cables = td_c + cables
    attach_cables_to_io(res["io"], cables)
    terminals = list(td_t) if has_td else list(res["terminals"])
    if not has_td:
        cable_of = {r["tag"]: r["cable_no"] for r in res["io"] if r["tag"] and r.get("cable_no")}
        for t in terminals:
            t["cable_no"] = cable_of.get(t["signal_tag"], "")
    drawn = res["wires"]
    ferr = _traced_ferrules(res.get("nets", []))
    if has_td:
        ferr += td_f + [dict(w, from_terminal=w.get("device_ref", ""), to_terminal=w.get("to_ref", "") and f"Sheet {w['to_ref']}", source="drawing") for w in drawn if w.get("wire_no")]
    else:
        ferr += build_ferrules(res["io"], drawn)
    # one row per (wire, from, to)
    seen, uniq = set(), []
    for f in ferr:
        k = (f["wire_no"], f["from_terminal"], f["to_terminal"], f["sheet"])
        if k not in seen:
            seen.add(k); uniq.append(f)
    return cables, uniq, terminals
