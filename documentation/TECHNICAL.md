# Technical Documentation

## Architecture
Flask app (`app.py`) serving a single-page UI (`templates/index.html`); `desktop.py` hosts it in a native pywebview window on 127.0.0.1:5057.
All processing is local. SQLite (`core/db.py`) stores projects, documents and extracted tables. Claude API calls (`core/llm.py` client, `core/ai_assist.py` prompts, `core/autoai.py` automatic pass) when `ANTHROPIC_API_KEY` is set; Haiku for text tasks, Sonnet for images only.

| Module | Role |
|---|---|
| `core/pdfio.py` | pdfplumber access, scanned-page detection, Tesseract OCR (auto-located; OCR words become pseudo-chars so parsers are shared) |
| `core/sheet_classify.py`, `pagemeta.py` | page type + title-block metadata |
| `core/io_extract.py` | PLC DI/DO/AI/AO/RTD/TC module sheets -> I/O rows |
| `core/devices.py` | make/part-number stacks, catalogue scan, device classification |
| `core/bom_extract.py` | printed BOM sheets -> BOM lines |
| `core/wires.py` | EPLAN wire numbers, device tags, sheet cross references |
| `core/circuits.py`, `ga_extract.py` | SLD feeders, GA/panel specifications |
| `core/spec_tables.py` | ruled tables, General Specifications key/value lines, internal-GA rows, dimension call-outs, table of contents |
| `tests/coverage.py` | per-page audit: share of printed engineering tokens that appear in extracted data, by sheet type |
| `core/terminal_detail.py` | terminal-detail sheets -> terminals, cables, ferrules (coordinate-based block/column parser) |
| `core/geometry.py` | vector symbol recognition + wire tracing: nets from solid axis-aligned lines (wire colour or fallback), pins, devices, wire numbers, coil/contact rows |
| `core/cables.py` | `assemble()` chooses sheet-read data over derived rows; derived cable schedule / ferrules as fallback |
| `core/autoai.py` | automatic Claude pass (vision for raster pages incl. symbols/wires/contacts, enrichment, classification) |
| `core/pipeline.py` | per-PDF orchestration (`process_pdf`) |
| `core/service.py` | background ingestion, learned-rule application, persistence |
| `core/exporters.py` | datasets -> XLSX / CSV / PDF / XML |
| `core/learning.py` | corrections -> rules (+ few-shot examples for the LLM) |
| `core/paths.py` | data folder: repo `data/` (source run), `%LOCALAPPDATA%\PRG-Extractor` (installed), `PRG_HOME` override |

## Database (SQLite, `prg.db`)
`projects`, `documents`, `sheets`, `modules`, `io_rows`, `circuits`, `devices`, `bom_items`, `wires`, `terminals`, `cables`, `xrefs`, `panel_specs`,
`corrections`, `rules`, `settings`. Per-document tables carry `project_id` + `doc_id`; column lists live in `db.TABLES` and new columns are added
by `db._migrate` on start. MongoDB Atlas is scaffolded (`core/mongo_db.py`, `/api/health`) but the data layer is SQLite.

## REST API (main)
`GET/POST /api/projects`, `POST /api/projects/<id>/upload`, `POST /api/projects/<id>/reprocess`, `GET /api/projects/<id>/table/<key>?q=&filter=`
(keys: io, summary, circuits, bom, bom_lines, devices, devsummary, terminals, blocks, wires, cables, xref, contacts, coils, sheetrefs, panel_specs),
`PATCH/POST/DELETE /api/row/<key>`, `GET /api/preview/<doc>/<sheet>.png`, `GET /api/export/<id>/<xlsx|pdf|csv|xml>`, `GET /api/learning`, `GET /api/health`.

## Build and package
`build_windows.bat` -> PyInstaller folder build (`dist\PRG-Extractor`) -> Inno Setup (`installer\PRG-Extractor.iss`) -> `installer\Output\PRG-Extractor-Setup.exe`.
To bundle OCR, copy a Tesseract-OCR installation to `.\tesseract` before building.

## Tests
`python tests/e2e.py [pdf names in Docs/]` - upload, extraction, every table, all four exports, learning.
`python tests/validate.py` compares I/O tags with the client reference Excel lists.

## Extending
New device classes: `PART_CLASSES` / `TAG_SUFFIX_CLASSES` in `core/devices.py`. New makes: `MAKES`. New BOM layouts: `core/bom_extract.py`.
New output table: add a dataset in `exporters.datasets()`, a key in `DATASET_KEYS` (app.py) and a tab in `index.html`.
