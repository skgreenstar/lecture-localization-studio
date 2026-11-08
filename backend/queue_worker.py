"""Durable SQLite-backed worker queue. Run exactly one worker for GPU jobs by default."""
import json,os,sqlite3,time,uuid,threading,traceback
from pathlib import Path
DB=Path(__file__).parent/"data"/"studio.sqlite3"
LEASE=120
MAX_ATTEMPTS=3
def connect():
    c=sqlite3.connect(DB,timeout=30,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c
def initialize():
    DB.parent.mkdir(exist_ok=True)
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS durable_jobs(
          id TEXT PRIMARY KEY, project_id TEXT, kind TEXT NOT NULL,
          payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
          progress INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
          max_attempts INTEGER NOT NULL DEFAULT 3, leased_until REAL,
          worker_id TEXT, error TEXT, output TEXT, created_at REAL NOT NULL,
          updated_at REAL NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_durable_claim ON durable_jobs(status,leased_until,created_at)")
        c.execute("""CREATE TABLE IF NOT EXISTS tts_cache(
          cache_key TEXT PRIMARY KEY, path TEXT NOT NULL, created_at REAL NOT NULL)""")
initialize()
def enqueue(kind,project_id,payload):
    jid=uuid.uuid4().hex
    with connect() as c:
        c.execute("INSERT INTO durable_jobs(id,project_id,kind,payload,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                  (jid,project_id,kind,json.dumps(payload),"queued",time.time(),time.time()))
    return jid
def claim(worker_id):
    now=time.time()
    with connect() as c:
        c.execute("BEGIN IMMEDIATE")
        row=c.execute("""SELECT * FROM durable_jobs WHERE
          status='queued' OR (status='running' AND leased_until<?)
          ORDER BY created_at LIMIT 1""",(now,)).fetchone()
        if not row:
            c.execute("COMMIT");return None
        if row["attempts"]>=row["max_attempts"]:
            c.execute("UPDATE durable_jobs SET status='failed',error='Retry limit reached',updated_at=? WHERE id=?",(now,row["id"]))
            c.execute("COMMIT");return None
        c.execute("""UPDATE durable_jobs SET status='running',worker_id=?,leased_until=?,
            attempts=attempts+1,updated_at=? WHERE id=?""",(worker_id,now+LEASE,now,row["id"]))
        c.execute("COMMIT")
        return dict(row)
def heartbeat(jid,worker_id):
    with connect() as c:
        c.execute("UPDATE durable_jobs SET leased_until=?,updated_at=? WHERE id=? AND worker_id=? AND status='running'",
                  (time.time()+LEASE,time.time(),jid,worker_id))
def progress(jid,value):
    with connect() as c:c.execute("UPDATE durable_jobs SET progress=?,updated_at=? WHERE id=?",(max(0,min(99,int(value))),time.time(),jid))
def finish(jid,worker_id,output):
    with connect() as c:c.execute("""UPDATE durable_jobs SET status='completed',progress=100,output=?,
        leased_until=NULL,updated_at=? WHERE id=? AND worker_id=?""",(str(output),time.time(),jid,worker_id))
def fail(jid,worker_id,error):
    with connect() as c:c.execute("""UPDATE durable_jobs SET status=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'queued' END,
        error=?,leased_until=NULL,worker_id=NULL,updated_at=? WHERE id=? AND worker_id=?""",
        (str(error)[:2000],time.time(),jid,worker_id))
def status(jid):
    with connect() as c:r=c.execute("SELECT * FROM durable_jobs WHERE id=?",(jid,)).fetchone()
    return dict(r) if r else None
def run_forever():
    from tasks import execute
    worker_id=uuid.uuid4().hex
    print("worker started",worker_id,flush=True)
    while True:
        job=claim(worker_id)
        if not job:time.sleep(1);continue
        stop=threading.Event()
        def beat():
            while not stop.wait(20):heartbeat(job["id"],worker_id)
        t=threading.Thread(target=beat,daemon=True);t.start()
        try:
            output=execute(job,lambda n:progress(job["id"],n))
            finish(job["id"],worker_id,output)
        except Exception as exc:
            traceback.print_exc()
            fail(job["id"],worker_id,exc)
        finally:
            stop.set();t.join(timeout=2)
if __name__=="__main__":run_forever()
