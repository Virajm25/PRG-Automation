@echo off
cd /d %~dp0
if not exist .venv ( py -3 -m venv .venv && .venv\Scripts\pip install -r requirements.txt )
start "" http://127.0.0.1:5057
.venv\Scripts\python app.py
