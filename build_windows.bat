@echo off
REM Builds dist\PRG-Extractor\PRG-Extractor.exe and, if Inno Setup 6 is installed, installer\Output\PRG-Extractor-Setup.exe
REM Optional: copy a Tesseract-OCR install folder to .\tesseract to bundle OCR for scanned PDFs.
cd /d %~dp0
if not exist .venv ( py -3 -m venv .venv ) 
.venv\Scripts\pip install -r requirements-desktop.txt pyinstaller || exit /b 1
set EXTRA=
if exist tesseract set EXTRA=--add-data "tesseract;tesseract"
.venv\Scripts\pyinstaller --noconfirm --clean --name PRG-Extractor --windowed --add-data "templates;templates" %EXTRA% --collect-all pdfplumber --collect-all pypdfium2 --collect-all webview desktop.py || exit /b 1
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% ( %ISCC% installer\PRG-Extractor.iss ) else ( echo Inno Setup 6 not found - skipping installer. Install from https://jrsoftware.org/isinfo.php )
