"""Local inference adapters. External model binaries are optional and fail explicitly."""
import json, os, subprocess, tempfile, wave, math
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError

def command(args, timeout=7200):
    return subprocess.run(args,check=True,capture_output=True,text=True,timeout=timeout).stdout

def transcribe(video:Path, model="small"):
    """Use faster-whisper if installed; return sentence/timestamp segments."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install optional STT dependency: pip install faster-whisper") from exc
    size=os.getenv("WHISPER_MODEL",model)
    device=os.getenv("WHISPER_DEVICE","cpu")
    engine=WhisperModel(size,device=device,compute_type="int8" if device=="cpu" else "float16")
    segments,_=engine.transcribe(str(video),language="ko",vad_filter=True,beam_size=5)
    return [{"start":float(s.start),"end":float(s.end),"source":s.text.strip()} for s in segments if s.text.strip()]

def translate(text,language,model="qwen3:8b"):
    languages={"en":"English","ja":"Japanese","zh":"Simplified Chinese","es":"Spanish","fr":"French","de":"German"}
    if language not in languages:raise ValueError("Unsupported language")
    prompt=(f"Translate this Korean lecture transcript accurately into {languages[language]}. "
        "Preserve technical terminology, meaning, and all numbers. Return only the translation, "
        "without explanations, notes, or thinking tags.\nKorean:\n"+text)
    body=json.dumps({"model":model,"prompt":prompt,"stream":False,"options":{"temperature":0.1}}).encode()
    endpoint=os.getenv("OLLAMA_URL","http://127.0.0.1:11434").rstrip("/")+"/api/generate"
    req=urlrequest.Request(endpoint,data=body,headers={"Content-Type":"application/json"})
    try:
        with urlrequest.urlopen(req,timeout=300) as response: output=json.load(response)
    except (URLError,TimeoutError) as exc:raise RuntimeError("Ollama unavailable; start ollama serve and pull the translation model") from exc
    answer=output.get("response","").strip()
    import re
    answer=re.sub(r"<think>.*?</think>","",answer,flags=re.S).strip()
    if not answer:raise RuntimeError("Translation model returned empty text")
    return answer

def synthesize(text,language,voice,target:Path):
    try:
        from mlx_audio.tts.utils import load_model
        import numpy as np
        import soundfile as sf
    except ImportError as exc:raise RuntimeError("Install mlx-audio, numpy, and soundfile on Apple Silicon") from exc
    model=load_model(os.getenv("TTS_MODEL","mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"))
    lang={"en":"English","ja":"Japanese","zh":"Chinese","es":"Spanish","fr":"French","de":"German"}.get(language,language)
    chunks=list(model.generate(text,voice=voice,lang_code=lang))
    if not chunks:raise RuntimeError("TTS returned no audio")
    arr=np.concatenate([np.asarray(x.audio).reshape(-1) for x in chunks])
    target.parent.mkdir(parents=True,exist_ok=True)
    sf.write(str(target),arr,int(chunks[0].sample_rate))
    return target

def render_dubbed_video(video:Path,clips:list,output:Path,duration:float):
    """Stream segment audio to a continuous WAV, avoiding thousands of FFmpeg inputs.
    Each clip is (start,end,path). Refuse large tempo adjustments rather than hide overlap.
    """
    import soundfile as sf
    import numpy as np
    if not clips:raise ValueError("No clips")
    if duration<=0:raise ValueError("Invalid video duration")
    output.parent.mkdir(parents=True,exist_ok=True)
    sample_rate=24000
    # Stream a PCM WAV on disk; memory use is bounded to one segment.
    audio_file=output.with_suffix(".mix.wav")
    ordered=sorted(clips,key=lambda c:c[0])
    try:
        with sf.SoundFile(str(audio_file),mode="w",samplerate=sample_rate,channels=1,subtype="PCM_16") as out:
            cursor=0
            for idx,(start,end,path) in enumerate(ordered):
                next_start=ordered[idx+1][0] if idx+1<len(ordered) else duration
                if end<=start or start<0 or next_start<start:raise ValueError("Invalid segment timeline")
                available=min(end,next_start,duration)-start
                if available<=0:raise ValueError("Overlapping or zero-length segment")
                with sf.SoundFile(str(path)) as inp:
                    data=inp.read(dtype="float32",always_2d=True)
                    source_rate=inp.samplerate
                mono=data.mean(axis=1)
                original_length=len(mono)/source_rate
                if original_length>available*1.25:
                    raise ValueError(f"Segment {idx+1} exceeds time slot by more than 25%; edit or regenerate shorter speech")
                if original_length>available:
                    temp=output.with_name(output.stem+f".segment{idx}.wav")
                    fixed=output.with_name(output.stem+f".fixed{idx}.wav")
                    try:
                        sf.write(str(temp),mono,source_rate)
                        command(["ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(temp),
                                 "-af",f"atempo={original_length/available:.6f},aresample={sample_rate}",
                                 "-ac","1",str(fixed)])
                        mono,source_rate=sf.read(str(fixed),dtype="float32")
                    finally:
                        temp.unlink(missing_ok=True);fixed.unlink(missing_ok=True)
                if source_rate!=sample_rate:
                    from math import ceil
                    positions=np.arange(ceil(len(mono)*sample_rate/source_rate))*source_rate/sample_rate
                    mono=np.interp(positions,np.arange(len(mono)),mono).astype("float32")
                begin=round(start*sample_rate)
                if begin<cursor:raise ValueError("Audio collision")
                while cursor<begin:
                    n=min(65536,begin-cursor);out.write(np.zeros(n,dtype="float32"));cursor+=n
                allowed=round(min(end,next_start,duration)*sample_rate)-begin
                if len(mono)>allowed+round(sample_rate*.02):raise ValueError("Audio still exceeds its segment slot")
                mono=mono[:allowed]
                out.write(mono);cursor+=len(mono)
            total=round(duration*sample_rate)
            while cursor<total:
                n=min(65536,total-cursor);out.write(np.zeros(n,dtype="float32"));cursor+=n
        command(["ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(video),"-i",str(audio_file),
                 "-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-b:a","192k",
                 "-t",str(duration),"-movflags","+faststart",str(output)])
        if not output.exists() or output.stat().st_size==0:raise RuntimeError("Empty MP4 output")
        return output
    finally:audio_file.unlink(missing_ok=True)

def write_srt(rows,output:Path):
    def ts(sec):
        ms=max(0,round(float(sec)*1000))
        h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text("\n\n".join(f"{i}\n{ts(row['start'])} --> {ts(row['end'])}\n{row['text']}" for i,row in enumerate(rows,1))+"\n",encoding="utf-8")
    return output
