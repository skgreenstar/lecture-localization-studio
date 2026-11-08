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

def render_dubbed_video(video:Path, clips:list, output:Path, duration:float):
    """Mix approved segment WAVs onto silence at their timestamps; preserve source video."""
    if not clips:raise ValueError("No approved TTS audio to export")
    output.parent.mkdir(parents=True,exist_ok=True)
    args=["ffmpeg","-hide_banner","-loglevel","error","-y","-i",str(video)]
    filters=[]
    for i,(start,audio) in enumerate(clips):
        args+=["-i",str(audio)]
        delay=max(0,round(float(start)*1000))
        filters.append(f"[{i+1}:a]adelay={delay}:all=1,aresample=48000[a{i}]")
    refs="".join(f"[a{i}]" for i in range(len(clips)))
    filters.append(f"{refs}amix=inputs={len(clips)}:duration=longest:normalize=0[mixed]")
    args+=["-filter_complex",";".join(filters),"-map","0:v:0","-map","[mixed]","-c:v","copy","-c:a","aac","-b:a","192k","-movflags","+faststart","-t",str(duration),str(output)]
    command(args,timeout=7200)
    if not output.exists() or output.stat().st_size==0:raise RuntimeError("FFmpeg did not produce an output file")
    return output

def write_srt(rows,output:Path):
    def ts(sec):
        ms=max(0,round(float(sec)*1000))
        h,ms=divmod(ms,3600000);m,ms=divmod(ms,60000);s,ms=divmod(ms,1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text("\n\n".join(f"{i}\n{ts(row['start'])} --> {ts(row['end'])}\n{row['text']}" for i,row in enumerate(rows,1))+"\n",encoding="utf-8")
    return output
