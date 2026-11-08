"""Idempotent task execution, per-translation cache, restartable translation."""
import hashlib,json,os,sqlite3,time,uuid
from pathlib import Path
from adapters import stt,translation,tts,tts_identity
from pipeline import render_dubbed_video
from queue_worker import connect,DB
DATA=DB.parent
def digest(*parts):
    return hashlib.sha256(json.dumps(parts,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def execute(job,progress):
    kind=job["kind"];payload=json.loads(job["payload"]);pid=job["project_id"]
    with connect() as c:
        p=c.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone() if pid else None
    if kind in ("stt","translate","export") and not p:raise RuntimeError("Project no longer exists")
    if kind=="stt":
        from pipeline import command
        import math
        duration=float(p["duration"])
        if duration<=0:raise RuntimeError("Video duration unavailable; check ffprobe")
        chunk_seconds=int(os.getenv("STT_CHUNK_SECONDS","600"))
        chunk_seconds=max(60,min(chunk_seconds,1800))
        with connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS stt_chunks(
              project_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, status TEXT NOT NULL,
              segment_count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project_id,chunk_index))""")
            if c.execute("SELECT 1 FROM segments WHERE project_id=? LIMIT 1",(pid,)).fetchone() and not c.execute("SELECT 1 FROM stt_chunks WHERE project_id=? LIMIT 1",(pid,)).fetchone():
                return "Existing manually authored transcript; refusing to overwrite"
        count=math.ceil(duration/chunk_seconds)
        for idx in range(count):
            with connect() as c:
                done=c.execute("SELECT status FROM stt_chunks WHERE project_id=? AND chunk_index=?",(pid,idx)).fetchone()
            if done and done["status"]=="completed":
                progress((idx+1)*100/count);continue
            start=idx*chunk_seconds
            length=min(chunk_seconds,duration-start)
            chunk=DATA/pid/f"stt-chunk-{idx:05}.wav"
            try:
                command(["ffmpeg","-hide_banner","-loglevel","error","-y","-ss",str(start),"-i",str(p["video"]),
                         "-t",str(length),"-vn","-ac","1","-ar","16000",str(chunk)])
                rows=stt().transcribe(chunk)
                with connect() as c:
                    c.execute("BEGIN IMMEDIATE")
                    # Chunk commit is atomic; completed chunks survive worker restarts.
                    for row in rows:
                        c.execute("INSERT INTO segments VALUES(?,?,?,?,?)",
                            (uuid.uuid4().hex,pid,start+row["start"],start+row["end"],row["source"]))
                    c.execute("INSERT OR REPLACE INTO stt_chunks VALUES(?,?,?,?)",(pid,idx,"completed",len(rows)))
                    c.execute("COMMIT")
            finally:chunk.unlink(missing_ok=True)
            progress((idx+1)*100/count)
        return f"{count} STT chunks completed"
    if kind=="translate":
        lang=payload["language"]
        with connect() as c:rows=c.execute("SELECT id,source FROM segments WHERE project_id=? ORDER BY start",(pid,)).fetchall()
        if not rows:raise RuntimeError("No transcript")
        for i,row in enumerate(rows):
            with connect() as c:
                existing=c.execute("SELECT 1 FROM translations WHERE segment_id=? AND language=?",(row["id"],lang)).fetchone()
            if not existing:
                result=translation().translate(row["source"],lang,payload.get("model"))
                with connect() as c:
                    if not c.execute("SELECT 1 FROM translations WHERE segment_id=? AND language=?",(row["id"],lang)).fetchone():
                        c.execute("INSERT INTO translations VALUES(?,?,?,?,?,0)",(uuid.uuid4().hex,row["id"],lang,result,1))
            progress((i+1)*100/len(rows))
        with connect() as c:c.execute("INSERT OR IGNORE INTO project_languages VALUES(?,?)",(pid,lang))
        return f"{len(rows)} segments checked"
    if kind=="tts":
        tid=payload["translation_id"];voice=payload["voice"]
        with connect() as c:
            row=c.execute("""SELECT t.*,s.project_id FROM translations t JOIN segments s ON s.id=t.segment_id
                WHERE t.id=?""",(tid,)).fetchone()
            if not row or not row["approved"]:raise RuntimeError("Translation is not approved")
            current=c.execute("SELECT id FROM translations WHERE segment_id=? AND language=? ORDER BY version DESC LIMIT 1",(row["segment_id"],row["language"])).fetchone()
            if current["id"]!=tid:raise RuntimeError("Translation version changed")
        key=digest(row["text"],row["language"],voice,tts_identity())
        target=DATA/"tts-cache"/(key+".wav")
        with connect() as c:cached=c.execute("SELECT path FROM tts_cache WHERE cache_key=?",(key,)).fetchone()
        if not cached or not Path(cached["path"]).is_file() or Path(cached["path"]).stat().st_size==0:
            target.parent.mkdir(parents=True,exist_ok=True)
            tmp=target.with_name(key+"."+uuid.uuid4().hex+".wav")
            try:
                tts().synthesize(row["text"],row["language"],voice,tmp)
                if not tmp.exists() or tmp.stat().st_size==0:raise RuntimeError("TTS produced empty audio")
                os.replace(tmp,target)
            finally:tmp.unlink(missing_ok=True)
            with connect() as c:c.execute("INSERT OR REPLACE INTO tts_cache VALUES(?,?,?)",(key,str(target),time.time()))
        else:target=Path(cached["path"])
        with connect() as c:
            current=c.execute("SELECT id FROM translations WHERE segment_id=? AND language=? ORDER BY version DESC LIMIT 1",(row["segment_id"],row["language"])).fetchone()
            if current["id"]!=tid:raise RuntimeError("Translation changed during TTS")
            if not c.execute("SELECT 1 FROM audio_assets WHERE translation_id=? AND path=?",(tid,str(target))).fetchone():
                c.execute("INSERT INTO audio_assets VALUES(?,?,?)",(uuid.uuid4().hex,tid,str(target)))
        return str(target)
    if kind=="export":
        lang=payload["language"]
        with connect() as c:
            total=c.execute("SELECT COUNT(*) FROM segments WHERE project_id=?",(pid,)).fetchone()[0]
            rows=c.execute("""SELECT s.start,s.end,a.path,t.id FROM segments s
                JOIN translations t ON t.segment_id=s.id AND t.language=? AND t.approved=1
                JOIN audio_assets a ON a.translation_id=t.id
                WHERE s.project_id=? AND t.version=(SELECT MAX(x.version) FROM translations x WHERE x.segment_id=s.id AND x.language=t.language)
                AND a.rowid=(SELECT MAX(a2.rowid) FROM audio_assets a2 WHERE a2.translation_id=t.id)
                ORDER BY s.start""",(lang,pid)).fetchall()
        if not total or len(rows)!=total:raise RuntimeError(f"Approved audio missing: {len(rows)}/{total}")
        for row in rows:
            if not Path(row["path"]).is_file():raise RuntimeError("Missing cached audio")
        revision=digest([(r["id"],r["path"],r["start"],r["end"]) for r in rows])
        target=DATA/pid/("dubbed_"+lang+"_"+revision[:12]+".mp4")
        if not target.exists() or target.stat().st_size==0:
            tmp=target.with_name(target.stem+"."+uuid.uuid4().hex+".tmp.mp4")
            try:
                render_dubbed_video(Path(p["video"]),[(r["start"],r["end"],Path(r["path"])) for r in rows],tmp,float(p["duration"]))
                # Never publish an export if its translation revisions changed while rendering.
                with connect() as c:
                    fresh=c.execute("""SELECT s.start,s.end,a.path,t.id FROM segments s
                        JOIN translations t ON t.segment_id=s.id AND t.language=? AND t.approved=1
                        JOIN audio_assets a ON a.translation_id=t.id
                        WHERE s.project_id=? AND t.version=(SELECT MAX(x.version) FROM translations x WHERE x.segment_id=s.id AND x.language=t.language)
                        AND a.rowid=(SELECT MAX(a2.rowid) FROM audio_assets a2 WHERE a2.translation_id=t.id)
                        ORDER BY s.start""",(lang,pid)).fetchall()
                if digest([(r["id"],r["path"],r["start"],r["end"]) for r in fresh])!=revision:
                    raise RuntimeError("Translations changed during export; retry with the latest revisions")
                os.replace(tmp,target)
            finally:tmp.unlink(missing_ok=True)
        return str(target)
    raise ValueError("Unknown task kind")
