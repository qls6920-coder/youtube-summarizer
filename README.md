# YouTube 영상 요약기

YouTube 영상의 자막을 가져와 Claude AI로 요약하는 웹 애플리케이션입니다.

## 프로젝트 구조

```
youtube-summarizer/
├── backend/
│   ├── main.py           # FastAPI 서버
│   ├── requirements.txt  # Python 패키지 목록
│   └── .env.example      # 환경변수 예시
└── frontend/
    └── index.html        # 프론트엔드 (단일 HTML 파일)
```

## 빠른 시작

### 1. 백엔드 설정

```bash
cd backend

# 가상환경 생성 (선택)
python -m venv venv
source venv/bin/activate      # macOS/Linux
# venv\Scripts\activate       # Windows

# 패키지 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일을 열어 ANTHROPIC_API_KEY 값을 입력하세요

# 서버 실행
uvicorn main:app --reload --port 8000
```

### 2. 프론트엔드 실행

`frontend/index.html` 파일을 브라우저에서 바로 열거나,
간단한 HTTP 서버로 실행합니다:

```bash
cd frontend
python -m http.server 3000
# 브라우저에서 http://localhost:3000 접속
```

### 3. 사용하기

1. 브라우저에서 `index.html` 열기
2. 백엔드 서버 주소 확인 (기본값: `http://localhost:8000`)
3. YouTube URL 입력 → 요약 방식 선택 → "요약하기" 클릭

## 배포 시 주의사항

### CORS 설정
`backend/main.py`에서 실제 프론트엔드 도메인으로 변경하세요:

```python
allow_origins=["https://your-frontend-domain.com"],
```

### 환경변수
서버 환경에서 `ANTHROPIC_API_KEY`를 환경변수로 설정하세요:

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 프로덕션 실행

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2
```

## API

### POST /summarize

영상 요약을 스트리밍으로 반환합니다.

**Request Body:**
```json
{
  "url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "mode": "bullet",    // bullet | summary | detailed
  "language": "ko"     // ko | en | ja | zh
}
```

**Response:** `text/event-stream` (SSE 스트리밍)

### GET /health

서버 상태 확인용 엔드포인트.

## 제한사항

- 자막이 없는 영상은 요약 불가
- 자동 생성 자막(Auto-generated)도 지원
- 영상 길이가 길 경우 앞부분(약 4000자)만 요약에 사용
