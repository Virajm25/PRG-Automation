# PRG Drawing Data Extractor - User Manual

## 1. What it does
Reads electrical-drawing PDFs (vector or scanned) and produces: I/O list, ferrule list, BOM, terminal schedule, cable schedule,
device & instrument list, and cross references. Everything can be reviewed and edited before export.

## 2. Install and start
1. Run `PRG-Extractor-Setup.exe` and follow the wizard. A Start-menu entry (and optional desktop icon) is created.
2. Start **PRG Drawing Data Extractor**. Project data is stored in `%LOCALAPPDATA%\PRG-Extractor` (database `prg.db`, uploaded PDFs).
3. Scanned PDFs need OCR: install *Tesseract-OCR* (https://github.com/UB-Mannheim/tesseract/wiki) to its default folder, or set the
   `TESSERACT_CMD` environment variable. Installers built with a `tesseract` folder already include it.

## 3. Typical workflow
1. **Projects** (start screen): *+ New project* - one project per panel / drawing package; all data is stored project-wise. Search finds projects by name or client.
2. **Drawings & sheets**: drop PDFs on the upload area or *Browse...* (several at once = batch). Each file shows a progress bar and its result.
   The sheet register below lists every page with its detected drawing type; click a page number to preview it.
3. **Overview**: one card per deliverable with its row count and how many rows **need review** (orange). The left menu shows the same badges.
4. Open a deliverable from the left menu (numbered as in the scope of work: 2 I/O List ... 8 Cross Reference). Use the sub-tabs, the
   search box (<kbd>/</kbd>), the filter drop-down and **Needs review only** to work through the flagged rows. Click a column header to sort.
5. **Edit** any cell: click, type, <kbd>Enter</kbd> saves (green flash), <kbd>Esc</kbd> cancels. *+ Add row* and *Delete* on each row.
   Orange bar = check this row (low confidence, AI-read or derived value). Edits are learned (see *Learned corrections*).
6. **Preview**: click any sheet number - the drawing opens beside the table. ◀ ▶ or the arrow keys change page, − / Fit / + zoom, ⛶ full screen,
   drag the divider to resize, <kbd>Esc</kbd> closes.
7. **Export** (top right, <kbd>Ctrl</kbd>+<kbd>E</kbd>): tick the documents and choose Excel, PDF, CSV or XML. *Export table* on each page exports just that table.
8. The status bar shows whether AI and OCR are available and where the project data is stored.

## 4. Tabs
| Tab | Content |
|---|---|
| I/O List | Rack, slot, module type, DI/DO/AI/AO/RTD/TC address columns, spare I/O, description, tag, signal type, field device, cable, terminal, remarks |
| I/O Summary | Channels / used / spare per module |
| Feeders / Circuits | Motor feeders from SLD and legend sheets (kW, starter, MCB, contactor, OLR, cable size) |
| BOM (totals) | Printed BOM sheets + catalogue parts on schematics; duplicate lines merged, quantities totalled |
| BOM Lines | Raw BOM lines (editable) |
| Device List / Summary | All devices with class (push button, selector, lamp, relay, contactor, MCB, MCCB, PLC module, PSU, HMI, VFD, sensor/transmitter, terminal block, ...) and counts per class |
| Terminal Schedule / Blocks | Per-terminal incoming wire, outgoing wire, cable, core, type, spare; per-strip used/spare totals |
| Ferrules / Wires | Ferrule no., wire no., from/to terminal, device, cable, core |
| Cable Schedule | Cable no., type, cores, source, destination, length, remarks |
| Device X-Ref, Contact X-Ref, Coil / Contact, Sheet Refs | Cross references |
| Panel Specs / Sheets | GA specifications; drawing register with automatic page classification |

## 4a. What is recognised and how
- **Terminal-detail sheets** ("IN PANEL / ON FIELD") are read block by block: strip name, terminal numbers, in-panel wire or PLC address, core colour,
  field description and cable size. These give the terminal schedule, the ferrule list (source `terminal-sheet`) and one field cable per strip
  (cores, colours, size, destination). Printed cable tags (e.g. `DBK-DI3`, `BL-01`) are used; otherwise `W-<strip>` is generated and flagged.
- **Schematic symbols and wires** (vector sheets): device tags with their pins (A1/A2, 13/14, 95/96, X1/X2, 1/L1 ...), ratings and printed
  cross-references are recognised; coils, contacts (NO/NC), main poles, overloads, lamps, protective devices and switches are classified.
  Wires are traced from the drawing geometry: each wire number is tied to the device pins at its two ends (ferrule source `traced`).
- **Coil / Contact tab**: coil location, every contact with page, column, type and pins, and the printed cross-references with page numbers resolved.
- **Specification, GA and legend sheets**: every ruled table (PANEL DETAILS, legend / cut-out schedule), every "NN ITEM : value" line of the
  General Specifications (including wiring and terminal colour coding), the internal-GA mounting rows, the dimension call-outs and the
  drawing index are stored in *Panel Specs*. The legend gives the front-panel item schedule (item no. -> device, feeder, cut-out size).
- **PLC sheets**: besides the channel data, each I/O row records the module terminal (X0, C1, V1+/I1+ ...), where the signal is used
  (sheet.column links) and the wire / device it connects through.
- **Symbols & Pins / Sheet Annotations**: every recognised symbol with its pins, and *all remaining printed text* on each sheet (descriptions,
  ratings, supplies, notes, vertical labels) with the nearest device - so nothing printed on a page is dropped.
- **GA layout by vision** (when an AI provider is configured): door items, colours, sizes and view dimensions read from the drawing image.
- **Scanned schematics**: with `ANTHROPIC_API_KEY` set, the vision model recognises symbols, wires and contacts on raster pages (source `ai-vision`).

## 5. Read from the drawing vs. derived
Values read directly from the drawing have source `drawing`, `vector` or `ocr`. Rows the program derives because the drawing does not print
them (e.g. cable numbers `1F-PWR` and `IC-<tag>`, ferrules for PLC terminals, core counts) are marked `derived` with a remark - **check these
before export**. Cable length is never invented; enter it manually.

## 6. Learning from corrections
Every edit is saved as a correction and generalised: identical cells are corrected automatically; single-word spelling fixes (e.g. AEARTION -> AERATION)
are applied inside every description in every table; a corrected device class is remembered per part number **and per tag family** (e.g. `-ILSW`).
The **Learned Rules** tab lists every rule with how often it fired; *Forget* removes a wrong rule, *Export / Import rules* moves knowledge between installations. *Re-extract* re-processes all drawings of a project with the learned rules.
**AI is on by default** when `ANTHROPIC_API_KEY` is set in the `.env` file (installed app: `%LOCALAPPDATA%\PRG-Extractor\.env`). After each extraction Claude fills blank
field-device names and tags (marked "suggested by AI - verify"), classifies unknown devices, and reads PLC or scanned sheets the parsers could not (rows marked
`ai-vision`, low confidence so they show in review). The top bar shows "AI: on/off"; *Re-run AI* repeats the pass. Only text or the one sheet image being read is sent.

## 7. Limits
Accuracy depends on drawing style. The extractor is tuned for the supplied EPLAN/AutoCAD-style packages; unseen layouts may need correction in
the editor (which then trains the rules). Net-level connectivity (tracing every wire between symbols) is not performed.
