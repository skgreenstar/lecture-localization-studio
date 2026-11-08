import os, tempfile, wave
from pathlib import Path
from unittest.mock import patch
from queue_worker import enqueue,claim,status,finish,connect
from tasks import digest
from pipeline import render_dubbed_video
import pytest

def test_cache_identity_is_deterministic():
    assert digest("hello","en","Ryan","model:v1")==digest("hello","en","Ryan","model:v1")
    assert digest("hello","en","Ryan","model:v1")!=digest("hello!","en","Ryan","model:v1")

def test_durable_job_claim_and_complete():
    jid=enqueue("test",None,{"hello":"world"})
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
