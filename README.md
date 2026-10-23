# PRG Automation

Electrical drawing data extraction application for PDF-based engineering packages.

## Deliverables map
| Item | Where |
|---|---|
| Application / source | this repository (`app.py`, `desktop.py`, `core/`, `templates/`) |
| Windows installer | `build_windows.bat` -> `installer\Output\PRG-Extractor-Setup.exe` |
| Database | SQLite `prg.db` (schema in `core/db.py`) |
| User manual / technical docs / training | `documentation/` |

## Local run

```powershell
pip install -r requirements-desktop.txt   # requirements-dev.txt adds pandas/xlrd for tests/validate.py
python app.py
```

Open http://127.0.0.1:5057.

## Claude AI configuration

AI is automatic. Put `ANTHROPIC_API_KEY=...` in `.env` (copy `.env.example`; for the installed app place it in `%LOCALAPPDATA%\PRG-Extractor\.env`) and restart. After every extraction the app then:

| Step | Model | Only when |
|---|---|---|
| Field-device names, tag suggestions, signal kind | Haiku (`claude-haiku-5-5`) | an I/O row has a blank field device or tag |
| Device classification | Haiku | the rules left the class as "Other" |
| GA / spec sheet extraction | Haiku | the rule-based parser found nothing |
| Reading PLC sheets with no channels, scanned sheets without Tesseract | Sonnet (`claude-sonnet-5-5`) | the parsers/OCR could not read the sheet |

No Opus-class model is used. Override with `PRG_MODEL_FAST` / `PRG_MODEL_STRONG`. Without a key the app works fully with the rule-based extractors. Never commit `.env`.

### Free alternatives to Claude

If `ANTHROPIC_API_KEY` is empty, the app uses the first free provider it finds a key for (or force one with `PRG_AI_PROVIDER`). Same automatic AI pass, same guardrails; only the model changes.

| Provider | Key / setting in `.env` | Default models (text / image) | Notes |
|---|---|---|---|
| Google Gemini | `GEMINI_API_KEY` | gemini-3.8-flash / gemini-3.8-flash | Free tier, best free vision quality; rate-limited |
| Groq | `GROQ_API_KEY` | llama-3.3-70b-versatile / llama-4-scout (vision) | Free tier, fast |
| OpenRouter | `OPENROUTER_API_KEY` | llama-3.3-70b-instruct:free / gemma-3-27b-it:free | Free models, tight daily limits |
| Ollama (local) | `OLLAMA_MODEL`, `OLLAMA_VISION_MODEL` | qwen2.5:7b / llama3.2-vision | No key, fully private, needs a capable PC |

Free tiers may log or train on prompts and have request limits (the client throttles itself and retries on 429). Use Ollama, or Claude, for confidential client drawings. Free model names change often; override with `PRG_MODEL_FAST` / `PRG_MODEL_STRONG`. Expect lower accuracy than Claude on dense drawings: AI output is always flagged for review.

## Vercel note

Vercel deployment is suitable for the UI/API demo, but Vercel serverless storage is ephemeral. Production deployments should use external object storage for PDFs and a hosted database instead of the local SQLite/runtime `data` directory. OCR and large PDF batches may also exceed serverless limits.

How the Vercel build is set up (`vercel.json`, `.vercelignore`):
- one Python function, `api/index.py` (the Flask app), with every URL rewritten to it; `maxDuration` is 300 s
- `requirements.txt` holds only runtime packages; desktop (`requirements-desktop.txt`) and test (`requirements-dev.txt`) packages are not installed on Vercel
- sample drawings (`Docs/`), tests, installer and documentation are excluded from the deployment
- on Vercel an upload is extracted inside the request (background threads are frozen after the response), files over ~4.5 MB are refused by Vercel, and the automatic AI pass is skipped - use *Re-run AI*
- set `GEMINI_API_KEY` / `ANTHROPIC_API_KEY` etc. under Project Settings → Environment Variables; no Tesseract on Vercel, so scanned pages need an AI provider

## MongoDB Atlas configuration

Copy .env.example to .env for local development, or add the same variables under Vercel Project Settings → Environment Variables. The /api/health endpoint reports MongoDB connectivity. The current SQLite adapter remains the local fallback; the application data-layer migration to MongoDB must be enabled before relying on Atlas as the production database.

