# 도담 복지정책 상담 백엔드 (Dodam Backend)

복지 정책 추천, 지원 가능성 분석, 비교, 신청 안내, 챗봇 질의응답을 제공하는 FastAPI 백엔드 서버입니다.  
2차 미니프로젝트의 백엔드 레포로, 정책 데이터 조회부터 AI 기반 추천/분석/대화 흐름까지 한 서버에서 제공합니다.

## 프로젝트 한눈에 보기

| 구분 | 내용 |
| --- | --- |
| 해결할 문제 | 복지정책 이름을 모르는 사용자도 자신의 상황을 설명하면 관련 정책과 판단 근거, 다음 확인 항목을 찾을 수 있게 합니다. |
| 핵심 흐름 | 사용자 질문 → 의도·조건 정리 → 정책 검색 → 규칙/LLM 응답 → 출처·추가 질문 → SSE 전달 |
| 저장소 책임 | 정책 검색·추천·지원 가능성 분석과 대화 요청 lifecycle을 제공하는 FastAPI/RAG 백엔드 |
| 공개 서비스 | [도담 웹 서비스](https://dodam-frontend.vercel.app) |
| 배포 API | [Backend API](https://dodam-backend.onrender.com) · [Swagger 문서](https://dodam-backend.onrender.com/docs) |
| 상태 확인 | [Liveness](https://dodam-backend.onrender.com/health/live) · [Readiness](https://dodam-backend.onrender.com/health/ready) |

> **배포 확인 (2026-08-25 KST):** 웹 서비스, API 문서, liveness, readiness의 HTTP 응답을 확인했습니다. 이 확인은 공개 엔드포인트의 접근 가능성을 뜻하며, 인증·검색·추천·채팅 전체 흐름의 운영 E2E 검증을 대신하지 않습니다.

## 주요 기능

- 정책 목록 조회
- 정책 상세 조회
- 사용자 가족 프로필 저장/조회
- 사용자 조건 기반 정책 추천
- 정책별 지원 가능성 분석
- 정책 비교 및 선택 가이드
- 정책 신청 정보/체크리스트 조회
- RAG 기반 정책 질의응답
- 챗봇 의도 분류, 슬롯 이어받기, 정책 선택 유도, SSE 스트리밍

## 기술 스택

- Python
- FastAPI
- LangChain
- LangGraph
- OpenAI API
- Uvicorn
- PostgreSQL
- SQLAlchemy

## 프로젝트 구조

```text
bistel-mini-2-backend/
├── .github/              # GitHub Actions, PR/Issue 템플릿
├── app/
│   ├── api/              # FastAPI 라우터
│   ├── common/           # 공통 응답, 예외, AI 상태 Enum
│   ├── core/             # 설정, 보안, 의존성
│   ├── db/               # DB 세션과 모델
│   ├── repositories/     # DB 접근 계층
│   ├── schemas/          # Pydantic 요청/응답 스키마
│   ├── services/         # 비즈니스 로직
│   └── static/           # 정적 리소스
├── docs/                 # 온보딩, 협업 규칙, API/DB/AI 워크플로우 문서
├── app/main.py           # FastAPI 앱 진입점
└── requirements.txt      # Python 패키지 목록
```

## 실행 방법

Python 3.12 기준으로 실행합니다.

### 1. 가상환경 생성

macOS/Linux:

```bash
python3 -m venv venv
```

Windows PowerShell:

```powershell
py -m venv venv
```

### 2. 가상환경 활성화

macOS/Linux:

```bash
source venv/bin/activate
```

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

### 3. 패키지 설치

```bash
pip install -r requirements.txt
```

### 4. 서버 실행

```bash
uvicorn app.main:app --reload
```

실행 후 아래 주소로 접속합니다.

- 서버 확인: `http://localhost:8000`
- API 문서: `http://localhost:8000/docs`

## 환경 변수 설정

`.env.example`을 복사해서 `.env` 파일을 만듭니다.

macOS/Linux:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

`.env`에는 로컬 실행에 필요한 값을 작성합니다.

```env
APP_ENV=local
APP_NAME=policy-rag-backend
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres
PSYCOPG_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres
DATA_GO_KR_SERVICE_KEY=your_data_go_kr_service_key
JWT_SECRET_KEY=replace-with-a-long-random-secret
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=20160
```

주의:

- `.env`는 Git에 올리지 않습니다.
- API Key, DB URL, 비밀번호 같은 민감정보는 코드에 직접 작성하지 않습니다.
- 공유가 필요한 환경 변수 이름은 `.env.example`에 예시로만 작성합니다.

## 협업 규칙 요약

1. GitHub Issue를 먼저 생성합니다.
2. 이슈 번호를 포함한 브랜치를 생성합니다.
3. 작업 후 커밋 메시지 규칙에 맞게 커밋합니다.
4. 브랜치를 push하고 Pull Request를 생성합니다.
5. PR 본문에 `Closes #이슈번호`를 작성합니다.
6. 리뷰와 자동화 검사를 통과한 뒤 merge합니다.
7. PR이 merge되면 연결된 이슈가 자동으로 닫힙니다.

브랜치명 형식:

```text
type/issue-number-description
```

예시:

```text
feature/2-policy-list
docs/6-project-docs
fix/4-cors-error
```

커밋 메시지와 PR 제목 형식:

```text
type: 작업 내용
```

예시:

```text
feat: 정책 목록 조회 API 추가
docs: README 작성
fix: CORS 설정 오류 수정
```

기본 원칙:

- `main`과 `develop`에는 직접 push하지 않습니다.
- 작업은 이슈 기반 브랜치에서 진행합니다.
- PR 본문에는 `Closes #이슈번호`를 작성합니다.
- 여러 이슈를 닫아야 하면 `Closes #6`, `Fixes #7`처럼 여러 줄로 작성합니다.

## 문서 링크

- [프로젝트 참여 및 실행 가이드](docs/ONBOARDING.md)
- [GitHub 협업 규칙](docs/CONVENTION.md)
- [백엔드 코드 작성 규칙](docs/CODE_CONVENTION.md)
- [AI/API 명세서](docs/API_SPEC_AI.md)
- [AI 워크플로우](docs/AGENT_WORKFLOW.md)
- [필드 매핑 및 AI 상태 규칙](docs/FIELD_MAPPING.md)
- [데이터베이스 스키마 적용 가이드](docs/DB_SETUP.md)

## 주의사항

아래 파일과 산출물은 Git에 올리지 않습니다.

- `venv/`
- `.env`
- `__pycache__/`
- `*.pyc`
- 대용량 원본 문서
- 임베딩 결과 파일
