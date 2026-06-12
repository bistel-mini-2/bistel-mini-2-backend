# Bistelligence Mini 2 Backend

복지 정책 추천 및 RAG 기반 질의응답을 위한 FastAPI 백엔드 서버입니다.  
2차 미니프로젝트의 백엔드 레포로, 정책 데이터 조회, 사용자 조건 기반 추천, 문서 기반 질의응답 기능을 단계적으로 구현합니다.

## 주요 기능 예정

- 정책 목록 조회
- 정책 상세 조회
- 사용자 조건 기반 정책 추천
- RAG 기반 정책 질의응답
- 정책 요약 및 비교 기능

## 기술 스택

- Python
- FastAPI
- LangChain
- LangGraph
- OpenAI API
- Uvicorn
- PostgreSQL

## 프로젝트 구조

```text
bistel-mini-2-backend/
├── .github/              # GitHub Actions, PR/Issue 템플릿
├── api/
│   ├── common/           # 공통 설정, 예외 처리, 공통 유틸
│   ├── policy/           # 정책 목록/상세 API
│   ├── recommend/        # 사용자 조건 기반 정책 추천 API
│   └── rag/              # RAG 기반 질의응답 API
├── static/
│   └── common/           # 정적 리소스
├── templates/            # Jinja2 템플릿
├── docs/                 # 온보딩, 협업 규칙, 코드 컨벤션 문서
├── main.py               # FastAPI 앱 진입점
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
python main.py
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
OPENAI_API_KEY=your_openai_api_key
DATABASE_URL=your_database_url
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

- 프로젝트 참여 및 실행 가이드
- GitHub 협업 규칙
- 백엔드 코드 작성 규칙

## 주의사항

아래 파일과 산출물은 Git에 올리지 않습니다.

- `venv/`
- `.env`
- `__pycache__/`
- `*.pyc`
- 대용량 원본 문서
- 임베딩 결과 파일
