# Lecture Localization Studio

Local-first lecture localization app for Korean videos: React + TypeScript, FastAPI + SQLite, faster-whisper, Ollama, MLX-Audio, and FFmpeg.

## Implemented
- Multiple project library: upload, rename, delete, tags, search, sorting, language progress.
- Segment review: timestamp navigation, per-language translation versioning, approve latest translation.
- Background API jobs for Korean transcription, Ollama translation, per-segment MLX TTS, and dubbed MP4 export.
- SRT subtitle download. Source video is stored once per project; translations are separated by language.
- API jobs return explicit failure details if a model or tool is missing.

## Prerequisites
- Python 3.11+, Node 20+, FFmpeg + ffprobe
- Ollama running locally, with `ollama pull qwen3:8b`
- For transcription: `pip install -r backend/requirements-ai.txt`
- For speech synthesis on Apple Silicon: install `mlx-audio`, `numpy`, `soundfile` in your Python environment. Confirm the configured TTS model supports your target language/voice.

## Run
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-ai.txt
uvicorn main:app --reload --port 8000
```
In another terminal:
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173.

## Workflow
1. Upload one video per project; multiple projects are supported.
2. Open the project, click **Korean STT**, then **Check status**.
3. Select a language, click **Translate**, then **Check status**.
4. Review and approve every segment. Edit translations before approval as needed.
5. Regenerate TTS on each approved segment. This requires MLX-Audio and a compatible model.
6. Click **Export MP4** after all segments have approved TTS, then download the resulting video. SRT can be downloaded independently.

## Testing
```bash
cd backend
pip install pytest httpx
python -m pytest -q
```
Tests exercise API flows without downloading AI models. **End-to-end STT, translation, TTS, MP4 and frontend build have not been verified on the target Apple Silicon machine.** The MLX-Audio model API may vary by version and voice/language compatibility. Long-running jobs use a persistent SQLite-backed worker queue; run the separate worker process. Concurrent multi-hour video processing, automatic retries, progress percentages, subtitle/video alignment quality, and resource scheduling need further production hardening. Export mixes generated speech at segment timestamps, but does not time-stretch audio to fit every original sentence duration, so overlap is possible.

## Environment
- `WHISPER_MODEL` (default `small`)
- `WHISPER_DEVICE` (default `cpu`)
- `OLLAMA_URL` (default `http://127.0.0.1:11434`)
- `TTS_MODEL` (default `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`)

**Security:** local development only. No authentication or file size quota; do not expose the server to the internet.
