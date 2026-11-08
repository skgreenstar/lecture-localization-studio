from pathlib import Path
import sqlite3, uuid, shutil, subprocess, json, threading, time
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from fastapi import BackgroundTasks
from fastapi.responses import Response
from pipeline import transcribe, translate, synthesize, render_dubbed_video, write_srt

DATA=Path(__file__).parent/"data"
DATA.mkdir(exist_ok=True)
DB=DATA/"studio.sqlite3"
app=FastAPI(title="Lecture Localization Studio",version="0.1.0")
app.add_middleware(CORSMiddleware,allow_origins=["http://localhost:5173","http://127.0.0.1:5173"],allow_methods=["*"],allow_headers=["*"])
def db():
    c=sqlite3.connect(DB,timeout=30)
    c.row_factory=sqlite3.Row
    return c
with db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,title TEXT NOT NULL,video TEXT,duration REAL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS segments(id TEXT PRIMARY KEY,project_id TEXT,start REAL,end REAL,source TEXT);
    CREATE TABLE IF NOT EXISTS translations(id TEXT PRIMARY KEY,segment_id TEXT,language TEXT,text TEXT,version INTEGER,approved INTEGER DEFAULT 0,UNIQUE(segment_id,language,version));
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,translation_id TEXT,status TEXT,error TEXT);
    CREATE TABLE IF NOT EXISTS audio_assets(id TEXT PRIMARY KEY,translation_id TEXT,path TEXT);
    CREATE TABLE IF NOT EXISTS pipeline_jobs(id TEXT PRIMARY KEY,project_id TEXT,language TEXT,kind TEXT,status TEXT,error TEXT,output TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS project_tags(project_id TEXT,tag TEXT,PRIMARY KEY(project_id,tag));
    CREATE TABLE IF NOT EXISTS project_languages(project_id TEXT,language TEXT,PRIMARY KEY(project_id,language));
    """)
    for column, definition in [("created_at","TEXT DEFAULT ''"),("updated_at","TEXT DEFAULT ''")]:
        try: c.execute(f"ALTER TABLE projects ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError: pass
def uid(): return uuid.uuid4().hex
def latest(c,sid,lang):
    return c.execute("SELECT * FROM translations WHERE segment_id=? AND language=? ORDER BY version DESC LIMIT 1",(sid,lang)).fetchone()
class ProjectUpdate(BaseModel):
    title:str=Field(min_length=1,max_length=200)
class TagsIn(BaseModel):
    tags:list[str]=Field(default_factory=list,max_length=20)
class LanguageIn(BaseModel):
    language:str=Field(pattern="^(en|ja|zh|es|fr|de)$")
@app.get("/api/projects/library")
def library(q:str="",tag:str="",sort:str="recent"):
    with db() as c:
        rows=c.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        out=[]
        for p in rows:
            tags=[r["tag"] for r in c.execute("SELECT tag FROM project_tags WHERE project_id=?",(p["id"],))]
            if q and q.lower() not in p["title"].lower():continue
            if tag and tag not in tags:continue
            langs=[r["language"] for r in c.execute("SELECT language FROM project_languages WHERE project_id=?",(p["id"],))]
            total=c.execute("SELECT COUNT(*) n FROM segments WHERE project_id=?",(p["id"],)).fetchone()["n"]
            progress={}
            for lang in langs:
                done=c.execute("""SELECT COUNT(*) n FROM segments s WHERE s.project_id=? AND EXISTS
                    (SELECT 1 FROM translations t WHERE t.segment_id=s.id AND t.language=? AND t.approved=1 AND t.version=
                    (SELECT MAX(t2.version) FROM translations t2 WHERE t2.segment_id=s.id AND t2.language=?))""",(p["id"],lang,lang)).fetchone()["n"]
                progress[lang]={"approved":done,"total":total,"percent":round(100*done/total) if total else 0}
            out.append({**dict(p),"tags":tags,"languages":langs,"progress":progress,"segment_count":total})
        if sort=="name":out.sort(key=lambda x:x["title"].lower())
        elif sort=="oldest":out.reverse()
        return out
@app.patch("/api/projects/{pid}")
def rename_project(pid:str,payload:ProjectUpdate):
    with db() as c:
        r=c.execute("UPDATE projects SET title=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(payload.title.strip(),pid))
        if not r.rowcount:raise HTTPException(404,"Project not found")
    return {"id":pid,"title":payload.title.strip()}
@app.put("/api/projects/{pid}/tags")
def set_tags(pid:str,payload:TagsIn):
    tags=list(dict.fromkeys(t.strip()[:40] for t in payload.tags if t.strip()))
    with db() as c:
        if not c.execute("SELECT 1 FROM projects WHERE id=?",(pid,)).fetchone():raise HTTPException(404,"Project not found")
        c.execute("DELETE FROM project_tags WHERE project_id=?",(pid,))
        c.executemany("INSERT INTO project_tags VALUES(?,?)",[(pid,t) for t in tags])
    return {"tags":tags}
@app.post("/api/projects/{pid}/languages")
def add_language(pid:str,payload:LanguageIn):
    with db() as c:
        if not c.execute("SELECT 1 FROM projects WHERE id=?",(pid,)).fetchone():raise HTTPException(404,"Project not found")
        c.execute("INSERT OR IGNORE INTO project_languages VALUES(?,?)",(pid,payload.language))
    return {"language":payload.language}
@app.delete("/api/projects/{pid}")
def delete_project(pid:str):
    with db() as c:
        p=c.execute("SELECT video FROM projects WHERE id=?",(pid,)).fetchone()
        if not p:raise HTTPException(404,"Project not found")
        c.execute("DELETE FROM jobs WHERE translation_id IN (SELECT t.id FROM translations t JOIN segments s ON s.id=t.segment_id WHERE s.project_id=?)",(pid,))
        c.execute("DELETE FROM audio_assets WHERE translation_id IN (SELECT t.id FROM translations t JOIN segments s ON s.id=t.segment_id WHERE s.project_id=?)",(pid,))
        c.execute("DELETE FROM translations WHERE segment_id IN (SELECT id FROM segments WHERE project_id=?)",(pid,))
        for table in ("segments","project_tags","project_languages"):
            c.execute(f"DELETE FROM {table} WHERE project_id=?",(pid,))
        c.execute("DELETE FROM projects WHERE id=?",(pid,))
    folder=DATA/pid
    if folder.is_dir():shutil.rmtree(folder)
    return {"deleted":True}
class SegmentIn(BaseModel):
    start:float=Field(ge=0)
    end:float
    source:str=Field(min_length=1)
    translation:str=""
    language:str="en"
class TranslationIn(BaseModel):
    text:str=Field(min_length=1)
    expected_version:int=Field(ge=0)
class TranslateIn(BaseModel):
    language:str="en"
    model:str="qwen3:8b"
class TTSIn(BaseModel):
    voice:str="Ryan"
@app.get("/api/health")
def health(): return {"ok":True}
@app.get("/api/projects")
def projects():
    with db() as c: return [dict(r) for r in c.execute("SELECT * FROM projects")]
@app.post("/api/projects")
async def upload(file:UploadFile=File(...)):
    suffix=Path(file.filename or "").suffix.lower()
    if suffix not in (".mp4",".mov",".mkv",".webm"): raise HTTPException(400,"Unsupported video format")
    pid=uid();folder=DATA/pid;folder.mkdir()
    target=folder/("source"+suffix)
    with target.open("wb") as out: shutil.copyfileobj(file.file,out)
    duration=0
    try:
        p=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(target)],capture_output=True,text=True,check=True)
        duration=float(p.stdout.strip())
    except (OSError,ValueError,subprocess.CalledProcessError): pass
    with db() as c: c.execute("INSERT INTO projects(id,title,video,duration) VALUES(?,?,?,?)",(pid,Path(file.filename).stem,str(target),duration))
    with db() as c: c.execute("INSERT OR IGNORE INTO project_languages VALUES(?,?)",(pid,"en"))
    return {"id":pid,"title":Path(file.filename).stem,"duration":duration}
@app.get("/api/projects/{pid}/video")
def video(pid:str):
    with db() as c: p=c.execute("SELECT video FROM projects WHERE id=?",(pid,)).fetchone()
    if not p: raise HTTPException(404,"Project not found")
    return FileResponse(p["video"])
@app.get("/api/projects/{pid}/segments")
def segments(pid:str,language:str="en",q:str=""):
    with db() as c:
        rows=c.execute("SELECT * FROM segments WHERE project_id=? ORDER BY start",(pid,)).fetchall()
        result=[]
        for row in rows:
            t=latest(c,row["id"],language)
            if q and q.lower() not in row["source"].lower() and (not t or q.lower() not in t["text"].lower()): continue
            audio=c.execute("SELECT id FROM audio_assets WHERE translation_id=? ORDER BY rowid DESC LIMIT 1",(t["id"],)).fetchone() if t else None
            result.append({**dict(row),"translation":dict(t) if t else None,"audio_id":audio["id"] if audio else None})
        return result
@app.post("/api/projects/{pid}/segments")
def add_segment(pid:str,payload:SegmentIn):
    if payload.end<=payload.start: raise HTTPException(400,"Invalid timestamps")
    sid=uid()
    with db() as c:
        if not c.execute("SELECT 1 FROM projects WHERE id=?",(pid,)).fetchone(): raise HTTPException(404,"Project not found")
        c.execute("INSERT INTO segments VALUES(?,?,?,?,?)",(sid,pid,payload.start,payload.end,payload.source))
        c.execute("INSERT OR IGNORE INTO project_languages VALUES(?,?)",(pid,payload.language))
        if payload.translation.strip(): c.execute("INSERT INTO translations VALUES(?,?,?,?,?,0)",(uid(),sid,payload.language,payload.translation,1))
    return {"id":sid}
@app.put("/api/segments/{sid}/translations/{lang}")
def edit(sid:str,lang:str,payload:TranslationIn):
    with db() as c:
        if not c.execute("SELECT 1 FROM segments WHERE id=?",(sid,)).fetchone(): raise HTTPException(404,"Segment not found")
        current=latest(c,sid,lang);v=current["version"] if current else 0
        if v!=payload.expected_version: raise HTTPException(409,"Version conflict")
        tid=uid()
        c.execute("INSERT INTO translations VALUES(?,?,?,?,?,0)",(tid,sid,lang,payload.text,v+1))
        c.execute("INSERT OR IGNORE INTO project_languages VALUES(?,?)",(c.execute("SELECT project_id FROM segments WHERE id=?",(sid,)).fetchone()["project_id"],lang))
    return {"id":tid,"version":v+1,"approved":False}
@app.post("/api/translations/{tid}/approve")
def approve(tid:str):
    with db() as c:
        t=c.execute("SELECT * FROM translations WHERE id=?",(tid,)).fetchone()
        if not t: raise HTTPException(404,"Translation not found")
        if latest(c,t["segment_id"],t["language"])["id"]!=tid: raise HTTPException(409,"Outdated version")
        c.execute("UPDATE translations SET approved=1 WHERE id=?",(tid,))
    return {"approved":True}
@app.post("/api/translations/{tid}/tts")
def tts(tid:str,payload:TTSIn,background:BackgroundTasks):
    with db() as c:
        t=c.execute("SELECT * FROM translations WHERE id=?",(tid,)).fetchone()
        if not t: raise HTTPException(404,"Translation not found")
        if not t["approved"] or latest(c,t["segment_id"],t["language"])["id"]!=tid: raise HTTPException(409,"Approve current translation first")
        jid=uid()
        c.execute("INSERT INTO jobs VALUES(?,?,?,?)",(jid,tid,"queued",None))
    background.add_task(run_tts,jid,tid,payload.voice)
    return {"job_id":jid,"status":"queued"}
@app.get("/api/jobs/{jid}")
def job(jid:str):
    with db() as c: r=c.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
    if not r: raise HTTPException(404,"Job not found")
    return dict(r)
@app.get("/api/audio/{aid}")
def audio(aid:str):
    with db() as c: r=c.execute("SELECT path FROM audio_assets WHERE id=?",(aid,)).fetchone()
    if not r or not Path(r["path"]).exists(): raise HTTPException(404,"Audio not found")
    return FileResponse(r["path"],media_type="audio/wav")
@app.post("/api/projects/{pid}/demo")
def demo(pid:str):
    sample=[(3,8,"트랜스포머는 어텐션 메커니즘을 사용합니다.","Transformers use an attention mechanism."),(9,15,"쿼리와 키의 유사도를 계산합니다.","We calculate the similarity between queries and keys."),(16,22,"소프트맥스를 적용해 가중치를 구합니다.","We apply softmax to obtain the weights.")]
    for a,b,ko,en in sample: add_segment(pid,SegmentIn(start=a,end=b,source=ko,translation=en))
    return {"created":len(sample)}

def run_tts(jid,tid,voice):
    with db() as c:
        c.execute("UPDATE jobs SET status='running' WHERE id=?",(jid,))
        t=c.execute("SELECT * FROM translations WHERE id=?",(tid,)).fetchone()
    try:
        target=DATA/"audio"/(uid()+".wav")
        synthesize(t["text"],t["language"],voice,target)
        with db() as c:
            c.execute("INSERT INTO audio_assets VALUES(?,?,?)",(uid(),tid,str(target)))
            c.execute("UPDATE jobs SET status='completed' WHERE id=?",(jid,))
    except Exception as exc:
        with db() as c:c.execute("UPDATE jobs SET status='failed',error=? WHERE id=?",(str(exc)[:1000],jid))

def queue_pipeline(pid,language,kind,background,model="qwen3:8b"):
    with db() as c:
        if not c.execute("SELECT 1 FROM projects WHERE id=?",(pid,)).fetchone():raise HTTPException(404,"Project not found")
        jid=uid()
        c.execute("INSERT INTO pipeline_jobs(id,project_id,language,kind,status) VALUES(?,?,?,?,?)",(jid,pid,language,kind,"queued"))
    background.add_task(run_pipeline,jid,pid,language,kind,model)
    return {"job_id":jid,"status":"queued"}

@app.post("/api/projects/{pid}/transcribe")
def start_transcription(pid:str,background:BackgroundTasks):
    return queue_pipeline(pid,"ko","stt",background)

@app.post("/api/projects/{pid}/translate")
def start_translation(pid:str,payload:TranslateIn,background:BackgroundTasks):
    if payload.language not in ("en","ja","zh","es","fr","de"):raise HTTPException(400,"Unsupported language")
    return queue_pipeline(pid,payload.language,"translate",background,payload.model)

@app.post("/api/projects/{pid}/export")
def start_export(pid:str,payload:TranslateIn,background:BackgroundTasks):
    return queue_pipeline(pid,payload.language,"export",background)

@app.get("/api/pipeline-jobs/{jid}")
def pipeline_job(jid:str):
    with db() as c:r=c.execute("SELECT * FROM pipeline_jobs WHERE id=?",(jid,)).fetchone()
    if not r:raise HTTPException(404,"Job not found")
    return dict(r)

@app.get("/api/projects/{pid}/exports/{language}")
def download_export(pid:str,language:str):
    target=DATA/pid/("dubbed_"+language+".mp4")
    if not target.exists():raise HTTPException(404,"Export not ready")
    return FileResponse(target,media_type="video/mp4",filename=target.name)

@app.get("/api/projects/{pid}/subtitles/{language}")
def download_subtitles(pid:str,language:str):
    with db() as c:
        if not c.execute("SELECT 1 FROM projects WHERE id=?",(pid,)).fetchone():raise HTTPException(404,"Project not found")
        rows=c.execute("""SELECT s.start,s.end,t.text FROM segments s JOIN translations t ON t.segment_id=s.id
          WHERE s.project_id=? AND t.language=? AND t.version=(SELECT MAX(x.version) FROM translations x WHERE x.segment_id=s.id AND x.language=t.language)
          ORDER BY s.start""",(pid,language)).fetchall()
    if not rows:raise HTTPException(409,"No translated segments")
    target=write_srt([dict(r) for r in rows],DATA/pid/("subtitles_"+language+".srt"))
    return FileResponse(target,media_type="application/x-subrip",filename=target.name)

def run_pipeline(jid,pid,language,kind,model):
    with db() as c:
        c.execute("UPDATE pipeline_jobs SET status='running' WHERE id=?",(jid,))
        p=c.execute("SELECT * FROM projects WHERE id=?",(pid,)).fetchone()
    try:
        if not p:raise RuntimeError("Project deleted")
        if kind=="stt":
            rows=transcribe(Path(p["video"]))
            with db() as c:
                # Re-transcription replaces existing segments; warn via explicit API semantics.
                existing=c.execute("SELECT COUNT(*) n FROM segments WHERE project_id=?",(pid,)).fetchone()["n"]
                if existing:raise RuntimeError("Project already has segments; create a new project to retranscribe safely")
                for r in rows:
                    c.execute("INSERT INTO segments VALUES(?,?,?,?,?)",(uid(),pid,r["start"],r["end"],r["source"]))
            output=str(len(rows))+" segments"
        elif kind=="translate":
            with db() as c:rows=c.execute("SELECT id,source FROM segments WHERE project_id=? ORDER BY start",(pid,)).fetchall()
            if not rows:raise RuntimeError("Transcribe the video first")
            count=0
            for row in rows:
                with db() as c:
                    if latest(c,row["id"],language):continue
                result=translate(row["source"],language,model)
                with db() as c:
                    if not latest(c,row["id"],language):
                        c.execute("INSERT INTO translations VALUES(?,?,?,?,?,0)",(uid(),row["id"],language,result,1))
                        count+=1
            with db() as c:c.execute("INSERT OR IGNORE INTO project_languages VALUES(?,?)",(pid,language))
            output=str(count)+" translated"
        elif kind=="export":
            with db() as c:
                rows=c.execute("""SELECT s.start,s.end,t.id AS tid,a.path FROM segments s
                    JOIN translations t ON t.segment_id=s.id AND t.language=? AND t.approved=1
                    JOIN audio_assets a ON a.translation_id=t.id
                    WHERE s.project_id=? AND t.version=(SELECT MAX(x.version) FROM translations x WHERE x.segment_id=s.id AND x.language=t.language)
                    AND a.rowid=(SELECT MAX(a2.rowid) FROM audio_assets a2 WHERE a2.translation_id=t.id)
                    ORDER BY s.start""",(language,pid)).fetchall()
                total=c.execute("SELECT COUNT(*) n FROM segments WHERE project_id=?",(pid,)).fetchone()["n"]
            if not total or len(rows)!=total:raise RuntimeError(f"Need approved TTS for all segments ({len(rows)}/{total})")
            target=DATA/pid/("dubbed_"+language+".mp4")
            render_dubbed_video(Path(p["video"]),[(r["start"],Path(r["path"])) for r in rows],target,float(p["duration"]))
            output=str(target)
        else:raise RuntimeError("Unknown job kind")
        with db() as c:c.execute("UPDATE pipeline_jobs SET status='completed',output=? WHERE id=?",(output,jid))
    except Exception as exc:
        with db() as c:c.execute("UPDATE pipeline_jobs SET status='failed',error=? WHERE id=?",(str(exc)[:1000],jid))
