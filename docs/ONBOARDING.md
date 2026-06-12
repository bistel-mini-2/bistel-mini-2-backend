# Onboarding

이 문서는 GitHub를 처음 사용하는 팀원도 `bistel-mini-2-backend` 프로젝트에 참여할 수 있도록 기본 흐름을 정리합니다.

## 프로젝트 개요

이 프로젝트는 FastAPI + LangChain 기반 백엔드입니다. 현재는 서버 실행 확인용 기본 구조와 협업 자동화 설정을 중심으로 관리합니다.

## 레포 구조

```text
bistel-mini-2-backend/
├── api/
│   ├── common/
│   ├── policy/
│   ├── recommend/
│   └── rag/
├── static/
│   └── common/
├── templates/
│   └── index.html
├── docs/
├── .github/
├── main.py
├── requirements.txt
└── .env.example
```

## 개발 환경 세팅

처음 받은 뒤 프로젝트 폴더로 이동합니다.

```bash
cd bistel-mini-2-backend
```

## 가상환경 생성 및 활성화

macOS/Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

Windows PowerShell:

```powershell
py -m venv venv
.\venv\Scripts\Activate.ps1
```

활성화되면 터미널 앞에 `(venv)`가 표시됩니다.

## requirements 설치

```bash
pip install -r requirements.txt
```

수업 버전의 `requirements.txt`를 사용하므로 임의로 패키지를 추가하거나 버전을 바꾸지 않습니다.

## 서버 실행 방법

```bash
python main.py
```

브라우저에서 다음 주소를 확인합니다.

- `http://localhost:8000/`
- `http://localhost:8000/docs`

## 이슈 생성 방법

1. GitHub Repository의 Issues 탭으로 이동합니다.
2. New issue를 클릭합니다.
3. 기능 추가, 버그 제보, 작업 요청 중 알맞은 템플릿을 선택합니다.
4. 작업 내용, 필요 이유, 완료 조건, 참고 사항을 작성합니다.
5. 생성된 이슈 번호를 확인합니다.

## 이슈 번호 기반 브랜치 생성

브랜치명은 `type/issue-number-description` 형식을 사용합니다.

```bash
git checkout develop
git pull origin develop
git checkout -b feature/2-policy-list
```

예시:

```text
feature/2-policy-list
feature/3-rag-chat
fix/4-cors-error
docs/5-api-spec
chore/1-backend-automation-docs
```

## 커밋 방법

커밋 메시지는 `type: 작업 내용` 형식으로 작성합니다.

```bash
git add .
git commit -m "feat: 정책 목록 조회 API 추가"
```

커밋 템플릿을 사용하려면 한 번만 설정합니다.

```bash
git config commit.template .gitmessage
```

그 다음부터는 아래처럼 커밋하면 템플릿이 열립니다.

```bash
git commit
```

## PR 작성 방법

1. 작업 브랜치를 push합니다.
2. GitHub에서 Compare & pull request를 클릭합니다.
3. PR 제목을 `type: 작업 내용` 형식으로 작성합니다.
4. PR 템플릿의 항목을 채웁니다.
5. 관련 이슈에 `Closes #이슈번호`를 작성합니다.

예시:

```text
Closes #2
```

PR이 merge되면 GitHub가 해당 이슈를 자동으로 닫습니다.

## GitHub Actions 자동 검사

PR을 만들면 다음 검사가 실행됩니다.

- PR Title Check: PR 제목이 `type: 작업 내용` 형식인지 확인합니다.
- Branch Name Check: 브랜치명이 `type/issue-number-description` 형식인지 확인합니다.
- PR Linked Issue Check: PR 본문에 `Closes #이슈번호` 같은 연결 문구가 있는지 확인합니다.
- Discord PR Notify: PR 생성과 merge 완료를 Discord로 알립니다.

## Discord PR 알림

Discord 알림을 사용하려면 GitHub Repository Secret에 `DISCORD_WEBHOOK_URL`을 등록해야 합니다.

Secret이 없으면 알림 workflow는 실패하지 않고 안내 메시지만 출력합니다. 이 workflow는 알림용이므로 required check로 걸지 않습니다.

## main/develop 직접 push 금지

`main`과 `develop`은 팀 공용 브랜치입니다. 직접 push하지 않고 반드시 작업 브랜치에서 PR을 만들어 merge합니다.

권장 흐름:

```text
Issue 생성 -> 브랜치 생성 -> 작업 -> 커밋 -> push -> PR -> 리뷰 -> merge
```

## 자주 쓰는 Git 명령어

```bash
git status
git branch
git checkout develop
git pull origin develop
git checkout -b feature/2-policy-list
git add .
git commit -m "feat: 정책 목록 조회 API 추가"
git push origin feature/2-policy-list
```

## 자주 하는 실수와 해결 방법

- PR 제목이 틀린 경우: `feat: 작업 내용`처럼 콜론 뒤에 공백을 포함해 수정합니다.
- 브랜치명이 틀린 경우: 새 브랜치를 올바른 이름으로 만들고 다시 push합니다.
- PR 본문에 이슈 번호를 빼먹은 경우: `Closes #이슈번호`를 추가합니다.
- `.env`, `venv/`, `__pycache__`가 포함된 경우: Git에 추가하지 말고 `.gitignore` 대상인지 확인합니다.
- 서버가 실행되지 않는 경우: 가상환경 활성화와 `pip install -r requirements.txt` 실행 여부를 확인합니다.
- 8000번 포트가 사용 중인 경우: 기존 서버를 종료한 뒤 다시 `python main.py`를 실행합니다.
- VS Code에서 import 경고가 나는 경우: Python 인터프리터가 프로젝트의 `venv`로 선택되어 있는지 확인합니다.
