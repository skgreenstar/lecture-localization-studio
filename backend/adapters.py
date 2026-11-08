"""Pluggable inference backends. Configure via environment without changing orchestration."""
import os
from pathlib import Path
from typing import Protocol
import pipeline

class STTAdapter(Protocol):
    def transcribe(self, media: Path) -> list[dict]: ...
class TranslationAdapter(Protocol):
    def translate(self, text: str, language: str) -> str: ...
class TTSAdapter(Protocol):
    def synthesize(self, text: str, language: str, voice: str, output: Path) -> Path: ...

class WhisperSTT:
    def transcribe(self, media):return pipeline.transcribe(media)
class OllamaTranslation:
    def translate(self,text,language):
        return pipeline.translate(text,language,os.getenv("TRANSLATION_MODEL","qwen3:8b"))
class MLXTTS:
    def synthesize(self,text,language,voice,output):
        return pipeline.synthesize(text,language,voice,output)

STT_REGISTRY={"whisper":WhisperSTT}
TRANSLATION_REGISTRY={"ollama":OllamaTranslation}
TTS_REGISTRY={"mlx":MLXTTS}

def select(registry,env,default):
    key=os.getenv(env,default)
    if key not in registry:raise ValueError(f"Unsupported {env}={key}; available: {', '.join(registry)}")
    return registry[key]()
def stt():return select(STT_REGISTRY,"STT_BACKEND","whisper")
def translation():return select(TRANSLATION_REGISTRY,"TRANSLATION_BACKEND","ollama")
def tts():return select(TTS_REGISTRY,"TTS_BACKEND","mlx")
def tts_identity():
    return ":".join([os.getenv("TTS_BACKEND","mlx"),os.getenv("TTS_MODEL","mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"),os.getenv("TTS_REVISION","default")])
