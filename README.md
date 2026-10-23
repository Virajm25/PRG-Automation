# PRG Automation

Electrical drawing data extraction application for PDF-based engineering packages.

## Local run

```powershell
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5057.

## Groq configuration

Set `GROQ_API_KEY`, `GROQ_TEXT_MODEL`, and `GROQ_VISION_MODEL` as environment variables. Do not commit API keys.

## Vercel note

Vercel deployment is suitable for the UI/API demo, but Vercel serverless storage is ephemeral. Production deployments should use external object storage for PDFs and a hosted database instead of the local SQLite/runtime `data` directory. OCR and large PDF batches may also exceed serverless limits.
