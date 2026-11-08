"""Run with: python -m pytest -q (requires pytest, httpx)."""
import io
from fastapi.testclient import TestClient
from main import app
client=TestClient(app)
def test_health():
    assert client.get("/api/health").json()["ok"]
def test_upload_and_library():
    r=client.post("/api/projects",files={"file":("test.mp4",b"not a valid video","video/mp4")})
    assert r.status_code==200,r.text
    pid=r.json()["id"]
    try:
        lib=client.get("/api/projects/library").json()
        assert any(p["id"]==pid for p in lib)
        assert client.patch(f"/api/projects/{pid}",json={"title":"Renamed"}).status_code==200
        assert client.put(f"/api/projects/{pid}/tags",json={"tags":["lecture","AI"]}).status_code==200
        assert client.post(f"/api/projects/{pid}/languages",json={"language":"ja"}).status_code==200
        s=client.post(f"/api/projects/{pid}/segments",json={"start":0,"end":2,"source":"안녕하세요","translation":"Hello","language":"en"})
        assert s.status_code==200,s.text
        sid=s.json()["id"]
        segments=client.get(f"/api/projects/{pid}/segments?language=en").json()
        tid=segments[0]["translation"]["id"]
        assert client.post(f"/api/translations/{tid}/approve").status_code==200
        assert client.put(f"/api/segments/{sid}/translations/en",json={"text":"Hello everyone","expected_version":0}).status_code==409
        assert client.put(f"/api/segments/{sid}/translations/en",json={"text":"Hello everyone","expected_version":1}).status_code==200
        assert client.get(f"/api/projects/{pid}/subtitles/en").status_code==200
    finally:
        assert client.delete(f"/api/projects/{pid}").status_code==200
def test_reject_invalid_extension():
    assert client.post("/api/projects",files={"file":("bad.txt",b"x","text/plain")}).status_code==400
