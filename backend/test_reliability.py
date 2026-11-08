import time
import os
import os, tempfile, wave
from pathlib import Path
from unittest.mock import patch
from queue_worker import enqueue,claim,status,finish,connect,cancel,retry
from tasks import digest
from pipeline import render_dubbed_video
import pytest

def test_cache_identity_is_deterministic():
    assert digest("hello","en","Ryan","model:v1")==digest("hello","en","Ryan","model:v1")
    assert digest("hello","en","Ryan","model:v1")!=digest("hello!","en","Ryan","model:v1")

def test_durable_job_claim_and_complete():
    jid=enqueue("test",None,{"hello":"world"})
    # Isolate the test from jobs queued by other test cases.
    with connect() as c:c.execute("UPDATE durable_jobs SET created_at=-1 WHERE id=?",(jid,))
    job=claim("pytest-worker")
    assert job is not None
    assert job["id"]==jid
    assert status(jid)["status"]=="running"
    finish(jid,"pytest-worker","ok")
    assert status(jid)["status"]=="completed"
    assert status(jid)["progress"]==100

def test_reject_overlong_timeline(tmp_path):
    # A fake video path is sufficient because the preflight rejects before FFmpeg.
    import numpy as np
    import soundfile as sf
    wav=tmp_path/"voice.wav"
    sf.write(wav,np.zeros(24000*3,dtype="float32"),24000)
    with pytest.raises(ValueError,match="25%"):
        render_dubbed_video(tmp_path/"video.mp4",[(0,1,wav)],tmp_path/"out.mp4",3)

def test_cancel_queued_job():
    jid=enqueue("test",None,{})
    assert cancel(jid)
    assert status(jid)["status"]=="cancelled"
    assert not retry(jid)

def test_retry_failed_job():
    jid=enqueue("test",None,{})
    with connect() as c:c.execute("UPDATE durable_jobs SET status='failed' WHERE id=?",(jid,))
    assert retry(jid)
    assert status(jid)["status"]=="queued"
    assert cancel(jid)

def test_enqueue_deduplicates_active_jobs():
    from queue_worker import enqueue
    payload={"language":"en","unique":"dedup-test-"+os.urandom(8).hex()}
    a=enqueue("translate","test-project",payload)
    b=enqueue("translate","test-project",payload)
    assert a==b

def test_manual_retry_resets_lease():
    from queue_worker import enqueue,connect,retry,status
    jid=enqueue("test",None,{"unique":os.urandom(8).hex()})
    with connect() as c:
        c.execute("UPDATE durable_jobs SET status='failed',leased_until=? WHERE id=?",(time.time()+3600,jid))
    assert retry(jid)
    job=status(jid)
    assert job["status"]=="queued"
    assert job["leased_until"] is None
