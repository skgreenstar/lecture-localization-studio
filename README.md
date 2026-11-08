# Lecture Localization Studio

Local-first lecture translation and dubbing studio, designed for Apple Silicon.

## Architecture
- React + TypeScript frontend
- FastAPI + SQLite backend
- Segment-based translation review and approval
- TTS regeneration job interface (inference worker integration pending)

## Development

Backend:
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

This is an MVP foundation. Automatic transcription, translation, actual MLX TTS inference, and MP4 export are not yet integrated.
