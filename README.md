# Lecture Localization Studio

Local-first lecture localization app for Korean videos: React + TypeScript, FastAPI + SQLite, faster-whisper, Ollama, MLX-Audio, and FFmpeg.

## Implemented
- Multiple project library: upload, rename, delete, tags, search, sorting, language progress.
- Segment review: timestamp navigation, per-language translation versioning, approve latest translation.
- Durable worker jobs for Korean transcription, Ollama translation, per-segment MLX TTS, and dubbed MP4 export.
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
Start the independent AI worker in a second terminal (same Python environment):
```bash
cd backend
source .venv/bin/activate
python queue_worker.py
```
In a third terminal:
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
Tests exercise API flows without downloading AI models. **End-to-end STT, translation, TTS, MP4 and frontend build have not been verified on the target Apple Silicon machine.** The MLX-Audio model API may vary by version and voice/language compatibility. Long-running jobs use a persistent SQLite-backed worker queue; run the separate worker process. Multi-hour STT is checkpointed in 10-minute chunks; the queue retries failures with backoff, and export checks timing slots, applies limited time stretching and rejects excessive overlap. Real two-hour reliability, exact audio alignment quality, and resource scheduling still need validation on the target machine.

## Environment
- `WHISPER_MODEL` (default `small`)
- `WHISPER_DEVICE` (default `cpu`)
- `OLLAMA_URL` (default `http://127.0.0.1:11434`)
- `TTS_MODEL` (default `mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit`)

**Security:** local development only. No authentication or file size quota; do not expose the server to the internet.

## Job controls
- `GET /api/projects/{project_id}/pipeline-jobs`: recent project jobs
- `GET /api/pipeline-jobs/{job_id}`: progress, attempts and error
- `POST /api/pipeline-jobs/{job_id}/cancel`: cancel a **queued** job
- `POST /api/pipeline-jobs/{job_id}/retry`: retry a **failed** job

## Verification status
The repository has CI test definitions, but this revision has **not** been confirmed to pass GitHub Actions or exercised with a two-hour real video. Verify the worker startup, STT chunk restart, Ollama/MLX model APIs, generated WAV durations, and the final MP4 on the target machine before production use.

## Model adapters and job semantics
- Model selection is isolated in `backend/adapters.py`. Register new STT, translation, or TTS adapters without changing the API or job scheduler.
- `STT_BACKEND=whisper`, `TRANSLATION_BACKEND=ollama`, `TTS_BACKEND=mlx` select the adapter. `TRANSLATION_MODEL=qwen3:8b` and `TTS_MODEL=...` select the model.
- `TTS_REVISION` is included in the TTS content cache key. **Change it whenever the model weights or synthesis settings change** to avoid reusing incompatible speech.
- SQLite WAL stores jobs, leases and retry state. Use one worker for Apple Silicon GPU inference. Duplicate active requests are coalesced.
- A restarted worker resumes at committed STT chunks or untranslated segments. A completed TTS cache entry is reused for matching text, language, voice and model identity.
- Exports are versioned by translation/audio revision. If a segment cannot fit its allotted timeline with at most 25% speech acceleration, export fails with an actionable error instead of overlapping the next segment.
- The output currently **replaces** original audio; background sound/music preservation, per-word forced alignment and speaker diarization are not implemented.
