# Bistel Mini 2 Backend Onboarding

이 문서는 `bistel-mini-2-backend` 프로젝트에 처음 참여하는 팀원이 로컬 개발 환경을 만들고, GitHub 협업 흐름에 따라 작업할 수 있도록 정리한 안내서입니다.

## 프로젝트 개요

`bistel-mini-2-backend`는 FastAPI + LangChain 기반 백엔드 프로젝트입니다. 정책 데이터 조회, 사용자 조건 기반 추천, RAG 기반 질의응답 기능을 단계적으로 구현하기 위한 서버 역할을 합니다.

현재 레포는 백엔드 서버 실행, API 문서 확인, GitHub 협업 자동화, 팀 문서화를 중심으로 관리합니다.

## 백엔드 레포 역할

- FastAPI 서버를 실행합니다.
- `/docs`에서 API 문서를 제공합니다.
- 정책 목록/상세, 추천, RAG 질의응답 API를 구현할 공간을 제공합니다.
- 프론트엔드 또는 테스트 클라이언트가 호출할 API를 제공합니다.
- GitHub Actions로 PR 제목, 브랜치명, PR 본문 이슈 연결, Discord 알림을 검사합니다.

## 로컬 개발 환경 준비

기준 환경은 Python 3.12와 VS Code입니다.

필요한 도구:

- Python 3.12
- Git
- VS Code
- VS Code Python Extension

레포를 받은 뒤 프로젝트 폴더로 이동합니다.

```bash
cd bistel-mini-2-backend
```

## 가상환경 생성

macOS/Linux:

```bash
python3 -m venv venv
```

Windows PowerShell:

```powershell
py -m venv venv
```

## 가상환경 활성화

macOS/Linux:

```bash
source venv/bin/activate
```

Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

활성화되면 터미널 앞에 `(venv)`가 표시됩니다.

## requirements 설치

```bash
pip install -r requirements.txt
```

`requirements.txt`는 수업 버전 기준으로 관리합니다. 개인 판단으로 패키지를 추가하거나 버전을 바꾸지 않습니다.

## `.env` 설정 방법

`.env.example`을 복사해서 `.env`를 만듭니다.

macOS/Linux:

```bash
cp .env.example .env
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

`.env`에는 로컬에서만 사용하는 값을 넣습니다.

```env
OPENAI_API_KEY=your_openai_api_key
DATABASE_URL=your_database_url
```

주의:

- `.env`는 Git에 올리지 않습니다.
- API Key, DB URL, 비밀번호 같은 민감정보를 코드나 문서에 직접 적지 않습니다.
- 공유가 필요한 환경 변수 이름은 `.env.example`에 예시로만 작성합니다.

## 서버 실행 방법

```bash
uvicorn app.main:app --reload
```

브라우저에서 아래 주소를 확인합니다.

- 서버 확인: `http://localhost:8000`
- API 문서: `http://localhost:8000/docs`

## GitHub 협업 기본 흐름

작업은 항상 이슈에서 시작합니다.

1. GitHub Issues 탭에서 이슈를 생성합니다.
2. 생성된 이슈 번호를 확인합니다.
3. 이슈 번호를 포함한 브랜치를 생성합니다.
4. 로컬에서 작업합니다.
5. 커밋 메시지 규칙에 맞게 커밋합니다.
6. 작업 브랜치를 GitHub에 push합니다.
7. Pull Request를 생성합니다.
8. 팀원 리뷰를 받습니다.
9. 자동화 검사를 통과한 뒤 merge합니다.

## 예시 흐름

이슈 번호가 `#6`이고 문서 정리 작업을 하는 경우:

```bash
git checkout develop
git pull origin develop
git checkout -b docs/6-project-docs
```

작업 후 커밋합니다.

```bash
git add docs/
git commit -m "docs: 협업 문서 정리"
git push origin docs/6-project-docs
```

PR을 만들 때 제목은 아래처럼 작성합니다.

```text
docs: 협업 문서 정리
```

PR 본문의 관련 이슈 항목에는 아래처럼 작성합니다.

```text
Closes #6
```

이 PR이 merge되면 연결된 이슈 `#6`이 자동으로 닫힙니다.

여러 이슈를 함께 닫아야 할 때는 PR 본문에 여러 줄로 작성합니다.

```text
Closes #6
Fixes #7
Resolves #8
```

## 자주 쓰는 Git 명령어

```bash
git status
git branch
git checkout develop
git pull origin develop
git checkout -b docs/6-project-docs
git add .
git commit -m "docs: 협업 문서 정리"
git push origin docs/6-project-docs
```

브랜치 목록 확인:

```bash
git branch
```

원격 브랜치까지 확인:

```bash
git branch -a
```

최신 develop 반영:

```bash
git checkout develop
git pull origin develop
```

## 자주 하는 실수

- `main` 또는 `develop`에 직접 push합니다.
- `.env`를 커밋합니다.
- `venv/`를 커밋합니다.
- `__pycache__/`나 `*.pyc` 파일을 커밋합니다.
- 이슈 번호 없이 브랜치를 만듭니다.
- PR 본문에 `Closes #이슈번호`를 빼먹습니다.
- PR merge 후 닫혀야 할 이슈 번호를 PR 본문에 작성하지 않습니다.
- PR 제목에서 `type: 작업 내용` 형식을 지키지 않습니다.
- 브랜치명에서 숫자 이슈 번호를 빼먹습니다.

실수했을 때는 혼자 억지로 해결하기보다 팀원에게 현재 상태와 에러 메시지를 공유합니다.

## 자동화 검사 설명

PR을 만들면 GitHub Actions가 자동으로 검사합니다.

- PR Title Check: PR 제목이 `type: 작업 내용` 형식인지 확인합니다.
- Branch Name Check: 브랜치명이 `type/issue-number-description` 형식인지 확인합니다.
- PR Linked Issue Check: PR 본문에 `Closes #이슈번호` 같은 이슈 연결 문구가 있는지 확인합니다.
- Discord PR Notify: PR 생성과 PR merge 완료를 Discord로 알립니다.
- Close Linked Issue: PR이 merge되면 PR 본문의 이슈 번호를 찾아 연결된 이슈를 닫습니다.

자동화가 실패하면 Actions 로그를 열어 어떤 규칙을 어겼는지 확인하고 수정합니다.
