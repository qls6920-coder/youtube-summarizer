import os
import re
import httpx
import json
import tempfile
import subprocess
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

load_dotenv()

app = FastAPI(title="YouTube Summarizer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

class SummarizeRequest(BaseModel):
    url: str
    mode: str = "bullet"
    language: str = "ko"


def extract_video_id(url: str) -> str | None:
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([^&\n?#]+)",
        r"youtube\.com/shorts/([^&\n?#]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def get_transcript_from_captions(video_id: str) -> str | None:
    """자막에서 텍스트 추출. 자막 없으면 None 반환"""
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(["ko", "en"])
        except Exception:
            transcript = transcript_list.find_generated_transcript(["ko", "en"])
        fetched = transcript.fetch()
        return " ".join(snippet.text for snippet in fetched)
    except Exception:
        return None


def get_transcript_from_audio(url: str) -> str:
    """오디오 다운로드 후 Whisper로 텍스트 변환"""
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "audio.mp3")

        # yt-dlp로 오디오 다운로드
        result = subprocess.run(
            [
                "yt-dlp",
                "-x", "--audio-format", "mp3",
                "--audio-quality", "0",
                "-o", audio_path,
                "--no-playlist",
                url,
            ],
            capture_output=True, text=True, timeout=120
        )

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"오디오 다운로드 실패: {result.stderr}")

        if not os.path.exists(audio_path):
            raise HTTPException(status_code=500, detail="오디오 파일을 찾을 수 없습니다.")

        # 파일 크기 확인 (Groq Whisper 최대 25MB)
        file_size = os.path.getsize(audio_path)
        if file_size > 25 * 1024 * 1024:
            raise HTTPException(status_code=422, detail="영상이 너무 깁니다. 25MB 이하의 오디오만 지원합니다.")

        # Groq Whisper API 호출
        import httpx as _httpx
        with open(audio_path, "rb") as f:
            response = _httpx.post(
                GROQ_WHISPER_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("audio.mp3", f, "audio/mpeg")},
                data={"model": "whisper-large-v3-turbo", "response_format": "text"},
                timeout=120,
            )

        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Whisper 변환 실패: {response.text}")

        return response.text


def build_prompt(transcript: str, mode: str, language: str) -> str:
    lang_map = {"ko": "한국어", "en": "English", "ja": "日本語", "zh": "中文"}
    lang_name = lang_map.get(language, "한국어")

    mode_instructions = {
        "bullet": f"핵심 요점 5~8개를 불릿 포인트(•) 형식으로 요약해주세요. {lang_name}로 작성하세요.",
        "summary": f"2~3개 단락(도입 / 핵심 내용 / 결론)으로 요약해주세요. {lang_name}로 작성하세요.",
        "detailed": f"주요 주제, 핵심 논점, 중요 사실, 결론을 포함해 구조적으로 상세 분석해주세요. {lang_name}로 작성하세요.",
    }

    instruction = mode_instructions.get(mode, mode_instructions["bullet"])
    trimmed = transcript[:4000]

    return f"다음은 YouTube 영상의 내용입니다.\n\n{trimmed}\n\n위 내용을 {instruction}"


@app.post("/summarize")
async def summarize(req: SummarizeRequest):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY 환경 변수가 설정되지 않았습니다.")

    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="올바른 YouTube URL이 아닙니다.")

    # 1) 자막 시도 → 없으면 Whisper로 음성 변환
    transcript = get_transcript_from_captions(video_id)
    if transcript:
        source = "자막"
    else:
        transcript = get_transcript_from_audio(req.url)
        source = "Whisper 음성인식"

    prompt = build_prompt(transcript, req.mode, req.language)

    async def stream_response():
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1024,
            "stream": True,
        }

        # 소스 정보 먼저 전송
        info = json.dumps({"type": "content_block_delta", "delta": {"text": f"[{source} 기반 요약]\n\n"}})
        yield f"data: {info}\n\n"

        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", GROQ_API_URL, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    yield f"data: {{\"error\": \"{body.decode()}\"}}\n\n"
                    return
                async for line in resp.aiter_lines():
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            data = json.loads(line[6:])
                            text = data["choices"][0]["delta"].get("content", "")
                            if text:
                                chunk = json.dumps({"type": "content_block_delta", "delta": {"text": text}})
                                yield f"data: {chunk}\n\n"
                        except Exception:
                            pass

    return StreamingResponse(stream_response(), media_type="text/event-stream")


@app.get("/health")
async def health():
    return {"status": "ok"}