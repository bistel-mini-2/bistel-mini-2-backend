# Git Convention

이 문서는 `bistel-mini-2-backend` 프로젝트의 GitHub 협업 규칙을 정리합니다.

## 브랜치 전략

- `main`: 최종 제출/배포용 브랜치입니다.
- `develop`: 개발 통합용 브랜치입니다.
- `feature/*`: 기능 개발 브랜치입니다.
- `fix/*`: 버그 수정 브랜치입니다.
- `docs/*`: 문서 작업 브랜치입니다.
- `chore/*`: 설정/자동화 작업 브랜치입니다.

`main`과 `develop`에는 직접 push하지 않고 PR로만 반영합니다.

## 작업 흐름

1. GitHub Issue를 생성합니다.
2. 이슈 번호를 포함한 브랜치를 생성합니다.
3. 작업 후 커밋 메시지 규칙에 맞게 커밋합니다.
4. GitHub에 push하고 PR을 생성합니다.
5. PR 본문에 `Closes #이슈번호`를 작성합니다.
6. GitHub Actions 자동 검사를 통과합니다.
7. 팀원 리뷰 후 `develop` 또는 지정된 브랜치로 merge합니다.

## 브랜치명 규칙

브랜치명에는 반드시 이슈 번호를 포함합니다.

```text
type/issue-number-description
```

허용 type은 `feature`, `fix`, `docs`, `chore`, `refactor`, `test`입니다.

```text
feature/2-policy-list
feature/3-rag-chat
fix/4-cors-error
docs/5-api-spec
chore/1-backend-automation-docs
```

검사 정규식은 다음과 같습니다.

```text
^(feature|fix|docs|chore|refactor|test)/[0-9]+-[a-z0-9-]+$
```

## 커밋 메시지 규칙

커밋 메시지는 다음 형식을 사용합니다.

```text
type: 작업 내용
```

허용 type은 `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`입니다.

```text
feat: 정책 목록 조회 API 추가
fix: CORS 설정 오류 수정
docs: README 실행 방법 추가
chore: 백엔드 협업 자동화 설정
```

커밋 템플릿은 아래 명령으로 설정할 수 있습니다.

```bash
git config commit.template .gitmessage
```

## PR 제목 규칙

PR 제목도 커밋 메시지와 같은 형식을 사용합니다.

```text
type: 작업 내용
```

검사 정규식은 다음과 같습니다.

```text
^(feat|fix|docs|style|refactor|test|chore): .+
```

## PR 본문과 이슈 연결

PR 본문에는 관련 이슈를 닫는 키워드를 작성합니다.

```text
Closes #이슈번호
```

GitHub Actions에서는 `Closes #숫자`, `Fixes #숫자`, `Resolves #숫자` 형식을 허용하며 대소문자는 구분하지 않습니다.

## Discord PR 알림

Discord 알림을 사용하려면 GitHub Repository Secret에 `DISCORD_WEBHOOK_URL`을 등록해야 합니다.

- PR 생성 시 알림을 보냅니다.
- PR이 merge 완료되었을 때만 merge 알림을 보냅니다.
- PR이 merge 없이 closed 된 경우에는 알림을 보내지 않습니다.
- Secret이 없으면 workflow는 실패하지 않고 안내 메시지만 출력합니다.
- 이 workflow는 알림용이므로 required check로 설정하지 않습니다.

## GitHub Ruleset 권장 설정

`main`과 `develop` 브랜치에는 다음 Ruleset 설정을 권장합니다.

- PR 필수
- force push 금지
- 브랜치 삭제 금지
- status check 필수
- 필수 status check: PR Title Check, Branch Name Check, PR Linked Issue Check
- Discord PR Notify는 알림용이므로 필수 status check에서 제외

## Notion 정리 기준

Notion에는 다음 문서 내용을 기준으로 복사해 정리하면 됩니다.

- `docs/ONBOARDING.md`
- `docs/CONVENTION.md`
- `docs/CODE_CONVENTION.md`
