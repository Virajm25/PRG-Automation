@echo off
REM Builds a single-folder Windows app: dist\PRG-Extractor\PRG-Extractor.exe  (untested on this machine - see README)
cd /d %~dp0
py -3 -m venv .venv && .venv\Scripts\pip install -r requirements.txt pyinstaller
.venv\Scripts\pyinstaller --noconfirm --name PRG-Extractor --add-data "templates;templates" --collect-all pdfplumber --collect-all pypdfium2 desktop.py
