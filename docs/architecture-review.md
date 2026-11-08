# Technical Architecture Review

Status: proposed target architecture, **not yet fully implemented**.

## Current runtime
- Frontend: Vite + React 19 + TypeScript; Astryx theme and selected components alongside custom CSS.
- API: FastAPI; SQLite stores projects, transcript segments, translation versions, approvals, TTS assets and jobs.
- Files: original video and generated media on local filesystem.
- STT: faster-whisper (optional); translation: Ollama; TTS: MLX Audio (optional); export: FFmpeg.
- Execution: FastAPI BackgroundTasks; no durable queue or crash recovery.

## Key findings and priority
| Priority | Issue | Recommended change |
|---|---|---|
| P0 | BackgroundTasks are not durable for 2+ hour videos | Separate persistent worker with SQLite job lease/state, retry policy, startup recovery and cancellation |
| P0 | No verified end-to-end Apple Silicon inference | Pin model/API versions; run real Korean→English/Japanese fixtures and record latency/memory |
| P0 | TTS duration may exceed segment windows | Duration-aware alignment, silence gaps, optional time stretch, overlap detection and listening QA |
| P0 | No concurrency or resource budget | GPU/CPU semaphore, one inference job at a time on 32GB Mac, independent FFmpeg queue |
| P1 | Segments and translations lack explicit processing stages | State machine: uploaded→transcribed→translated→reviewed→voiced→exported |
| P1 | Editing a translation invalidates previous audio | Fingerprint (text,language,voice,model,settings); regenerate only affected segments |
| P1 | No API authentication or upload quota | Loopback-only binding, extension/MIME validation, size limits and safe paths |
| P1 | SQLite concurrency and migrations | WAL, busy timeout, transactional schema migrations, foreign keys |
| P1 | Current frontend uses partial Astryx migration | Adopt verified Astryx primitives page-by-page, use CLI docs, run visual regression |
| P2 | No durable metrics | Track segment-level processing time, memory, failed retries, inference model versions |

## Proposed data model
- Project(id,title,tags,source_video,created_at,updated_at)
- Segment(id,project_id,start_ms,end_ms,source_text,source_version)
- Translation(id,segment_id,language,version,text,approved_at)
- VoiceAsset(id,translation_id,model,voice,settings_hash,audio_path,duration_ms,status)
- Job(id,project_id,kind,status,attempts,progress,error,lease_until,created_at,updated_at)
- Export(id,project_id,language,manifest_hash,video_path,subtitle_path,status)

## Pipeline
1. Upload original video once; ffprobe duration/codecs, compute content hash.
2. Extract normalized audio; perform chunked ASR with overlap and timestamp reconciliation.
3. Translate batches using bounded concurrency; store drafts and terminology glossary.
4. Human approves segment translations. Every edit creates a new immutable version.
5. Generate TTS only for approved translation versions whose fingerprint is missing.
6. Normalize, align and mix audio; report segments that cannot fit the source timeline.
7. Produce MP4 and SRT; run ffprobe validation and optional media smoke checks.
8. Resume failed jobs without repeating successful segment work.

## Deployment
Local-only initial target: macOS Apple Silicon 32GB, one FastAPI process, one separate Python worker, SQLite WAL, filesystem artifact store, Ollama, FFmpeg. Do not expose the API to public networks. A multi-user server requires auth, Postgres, object storage and a distributed queue.

## Verification gates
- npm install && npm run build; TypeScript check; visual smoke tests in Chromium.
- pytest API CRUD/versioning/approval; test migrations from an older database.
- STT→translation→TTS→export on a short real lecture with measured quality and time.
- Crash/restart/resume, concurrent upload, insufficient disk, missing model, invalid file.
- 2-hour lecture soak test with memory peak, retry behavior, audio drift and output integrity.

## Open architectural decisions
1. Keep all inference strictly local, or permit optional cloud fallback?
2. Favor MLX Whisper vs faster-whisper on Apple Silicon after benchmark?
3. Choose a multilingual TTS model/voice strategy validated for each target language.
4. Whether original lecture audio should be muted, ducked, or retained beneath dubbing.
5. Whether subtitle approval is required for export or only voice approval.
