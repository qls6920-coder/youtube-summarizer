import os
import re
import httpx
import json
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


def get_transcript(video_id: str) -> str:
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
        try:
            transcript = transcript_list.find_transcript(["ko", "en"])
        except Exception:
            transcript = transcript_list.find_generated_transcript(["ko", "en"])
        fetched = transcript.fetch()
        return " ".join(snippet.text for snippet in fetched)
    except (TranscriptsDisabled, NoTranscriptFound):
        raise HTTPException(
            status_code=422,
            detail="이 영상에는 자막이 없어 요약할 수 없습니다. 자막이 있는 영상을 사용해주세요."
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"자막 로드 실패: {str(e)}")


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
    return f"다음은 YouTube 영상의 자막입니다.\n\n{trimmed}\n\n위 내용을 {instruction}"


@app.post("/summarize")
async def summarize(req: SummarizeRequest):
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY 환경 변수가 설정되지 않았습니다.")

    video_id = extract_video_id(req.url)
    if not video_id:
        raise HTTPException(status_code=400, detail="올바른 YouTube URL이 아닙니다.")

    transcript = get_transcript(video_id)
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

        async with httpx.AsyncClient(timeout=60) as client:
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