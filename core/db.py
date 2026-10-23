"""SQLite project database (one file per installation; project-wise tables)."""
import sqlite3, os, json, threading, time

DB_PATH = os.environ.get("PRG_DB", os.path.join(os.path.dirname(__file__), "..", "data", "prg.db"))
_lock = threading.Lock()

TABLES = {
    "io": ("io_rows", ["rack", "slot", "module_name", "module_type", "module_model", "plc_address", "channel", "tag", "description",
                       "signal_type", "field_device", "cable_no", "terminal", "remarks", "spare", "sheet", "conf", "source"]),
    "circuits": ("circuits", ["feeder_no", "description", "rating_kw", "starter_type", "mcb", "contactor", "olr", "cable_size", "terminal", "controls", "sheet"]),
    "devices": ("devices", ["sheet", "tag", "dev_class", "make", "part_no", "rating", "catalogue_no", "descr", "panel", "source"]),
    "wires": ("wires", ["sheet", "wire_no", "ferrule_no", "from_terminal", "to_terminal", "device_ref", "cable_ref", "core_no", "to_ref"]),
    "terminals": ("terminals", ["strip", "terminal_no", "terminal_type", "incoming_wire", "outgoing_wire", "cable_no", "core_no",
                                "signal_tag", "plc_address", "spare", "sheet"]),
    "cables": ("cables", ["cable_no", "cable_type", "cores", "source_equipment", "dest_equipment", "length_m", "remarks"]),
    "xrefs": ("xrefs", ["sheet", "direction", "target_sheet", "target_col", "label", "text"]),
    "panel_specs": ("panel_specs", ["sheet", "category", "property_name", "property_value", "unit", "raw_text"]),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT, client TEXT, created REAL);
CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY, project_id INT, filename TEXT, path TEXT, pages INT, status TEXT,
  progress INT DEFAULT 0, message TEXT DEFAULT '', panel TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS sheets(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, sheet INT, title TEXT, label TEXT, kind TEXT, draw_type TEXT DEFAULT '', draw_type_label TEXT DEFAULT '', drawing_no TEXT DEFAULT '', revision TEXT DEFAULT '', section_view TEXT DEFAULT '', scanned INT);
CREATE TABLE IF NOT EXISTS modules(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, rack TEXT, slot TEXT, module_type TEXT, label TEXT,
  model TEXT, channels INT, sheets TEXT, slot_derived INT);
CREATE TABLE IF NOT EXISTS io_rows(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, %(io)s);
CREATE TABLE IF NOT EXISTS circuits(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, %(circuits)s);
CREATE TABLE IF NOT EXISTS devices(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, %(devices)s);
CREATE TABLE IF NOT EXISTS wires(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, %(wires)s);
CREATE TABLE IF NOT EXISTS terminals(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, %(terminals)s);
CREATE TABLE IF NOT EXISTS cables(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, %(cables)s);
CREATE TABLE IF NOT EXISTS xrefs(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, %(xrefs)s);
CREATE TABLE IF NOT EXISTS panel_specs(id INTEGER PRIMARY KEY, project_id INT, doc_id INT, %(panel_specs)s);
CREATE TABLE IF NOT EXISTS corrections(id INTEGER PRIMARY KEY, entity TEXT, field TEXT, original TEXT, corrected TEXT, context TEXT, created REAL);
CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY, entity TEXT, field TEXT, original TEXT, corrected TEXT, hits INT DEFAULT 0,
  UNIQUE(entity, field, original));
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def conn():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init():
    cols = {k: ", ".join(f"{c} TEXT" for c in v[1]) for k, v in TABLES.items()}
    with _lock, conn() as c:
        c.executescript(SCHEMA % cols)
    # Migration: add new columns to sheets if they don't exist (for existing DBs)
    _migrate()


def _migrate():
    """Add any missing columns to existing tables (non-destructive)."""
    new_cols = [
        ("sheets", "draw_type", "TEXT DEFAULT ''"),
        ("sheets", "draw_type_label", "TEXT DEFAULT ''"),
        ("sheets", "drawing_no", "TEXT DEFAULT ''"),
        ("sheets", "revision", "TEXT DEFAULT ''"),
        ("sheets", "section_view", "TEXT DEFAULT ''"),
    ]
    with _lock, conn() as c:
        existing = {(r[0], r[1]) for r in c.execute(
            "SELECT m.name, p.name FROM sqlite_master m "
            "JOIN pragma_table_info(m.name) p WHERE m.type='table'"
        )}
        for tbl, col, typedef in new_cols:
            if (tbl, col) not in existing:
                try:
                    c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typedef}")
                except Exception:
                    pass


def q(sql, args=(), one=False):
    with _lock, conn() as c:
        cur = c.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
    return (rows[0] if rows else None) if one else rows


def x(sql, args=()):
    with _lock, conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def insert_many(table_key, project_id, doc_id, rows):
    tbl, cols = TABLES[table_key]
    with _lock, conn() as c:
        c.executemany(f"INSERT INTO {tbl}(project_id,doc_id,{','.join(cols)}) VALUES (?,?,{','.join('?'*len(cols))})",
                      [(project_id, doc_id, *[_s(r.get(k, "")) for k in cols]) for r in rows])


def _s(v):
    if isinstance(v, bool):
        return "1" if v else ""
    return "" if v is None else str(v)


def get_setting(key, default=""):
    r = q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else (os.environ.get(key.upper(), default))


def set_setting(key, value):
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
