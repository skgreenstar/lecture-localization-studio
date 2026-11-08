# Architecture review — 2026-11-08

## Current state
- Frontend: Vite + React 19 + Astryx neutral theme; legacy hand-written layout CSS is progressively mapped to Astryx tokens. This is **incremental adoption**, not a full component-by-component rewrite.
- API: FastAPI; SQLite stores projects, segments, versioned translations, audio assets, and jobs.
- Inference: faster-whisper (Korean STT), Ollama (translation), MLX-Audio (TTS), FFmpeg (render).
- Storage: local video and generated WAV/MP4/SRT files.
- Execution: FastAPI BackgroundTasks (in-process, **not durable**, **not concurrent-job safe**).
- CI: Python API smoke tests and TypeScript/Vite build. No real Apple Silicon inference test.

## Architecture risks and decisions to revisit
1. **Job orchestration**: move from BackgroundTasks to a persistent worker queue with leasing, retries, cancellation, heartbeat and crash recovery; isolate GPU workers and enforce concurrency=1 for memory-heavy tasks.
2. **Long-form media**: process 2h+ video in bounded chunks; checkpoint transcription, translation, and TTS per segment; avoid loading whole media into memory.
3. **Temporal correctness**: TTS duration may exceed segment length. Introduce duration-aware TTS, silence gaps, overlap policy, and loudness normalization. Validate video codec compatibility when stream-copying.
4. **Language support**: MLX TTS voices/models differ by language. Make model/voice capabilities discoverable, validate before queuing, and avoid claiming unsupported combinations.
5. **Storage and schema**: introduce Alembic migrations, explicit foreign keys, cascading deletes, stable asset identifiers, atomic file writes, and orphan cleanup. Existing ALTER TABLE migration is minimal.
6. **API safety**: authentication before network exposure, upload size limits, streaming upload validation, rate limits, path confinement, and bounded background work.
7. **Export semantics**: define whether original audio is muted, mixed, or ducked. SRT output should offer approved-only or draft inclusion with explicit selection.
8. **Versioning**: every audio asset must reference exact approved translation version and model/voice configuration; export should pin a consistent project snapshot.
9. **Testing**: unit tests for adapters with mocks; integration tests with short synthetic media; UI E2E tests; Mac Apple Silicon hardware validation; 2-hour stress test.
10. **UI**: Astryx is beta; pin versions and retain a tested lockfile. Replace native controls with documented Astryx components gradually after checking accessibility and bundle size.

## Proposed target (not implemented)
```
React 19 / Astryx UI
        |
FastAPI (auth, projects, versioned segments, jobs)
        |                  |
SQLite -> PostgreSQL    local object store
        |
Durable job queue + scheduler
        |
STT worker / Ollama translation worker / MLX TTS worker / FFmpeg export worker
        |
Artifact manifests, segment checkpoints, QA metrics, export snapshots
```

## Validation status
GitHub commits confirm source changes, not successful runtime behavior. npm package registry was unreachable from the development environment, so React/Astryx install and production build have not been verified. Do not consider this production-ready.
