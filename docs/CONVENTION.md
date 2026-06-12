# GitHub Collaboration Convention

이 문서는 `bistel-mini-2-backend` 프로젝트의 GitHub 협업 규칙입니다. GitHub README나 Notion 협업 가이드에 그대로 복사해서 사용할 수 있도록 작성했습니다.

## 브랜치 전략

- `main`: 최종 제출/배포용 브랜치입니다.
- `develop`: 개발 통합용 브랜치입니다.
- `feature/*`: 기능 개발 브랜치입니다.
- `fix/*`: 버그 수정 브랜치입니다.
- `docs/*`: 문서 작업 브랜치입니다.
- `chore/*`: 설정/자동화 작업 브랜치입니다.
- `refactor/*`: 리팩토링 브랜치입니다.
- `test/*`: 테스트 작업 브랜치입니다.

`main`과 `develop`에는 직접 push하지 않습니다. 모든 작업은 이슈 기반 브랜치에서 진행하고 PR로 반영합니다.

## 브랜치명 규칙

브랜치명은 아래 형식을 사용합니다.

```text
type/issue-number-description
```

예시:

```text
feature/2-policy-list
feature/3-rag-chat
fix/4-cors-error
docs/6-project-docs
chore/1-backend-automation-docs
```

규칙:

- type은 `feature`, `fix`, `docs`, `chore`, `refactor`, `test` 중 하나를 사용합니다.
- issue-number에는 GitHub 이슈 번호를 적습니다.
- description은 영어 소문자, 숫자, 하이픈을 사용합니다.

## 커밋 메시지 규칙

커밋 메시지는 아래 형식을 사용합니다.

```text
type: 작업 내용
```

허용 type:

- `feat`: 새로운 기능 추가
- `fix`: 버그 수정
- `docs`: 문서 수정
- `style`: 코드 의미에 영향을 주지 않는 스타일 변경
- `refactor`: 기능 변경 없는 코드 구조 개선
- `test`: 테스트 추가 또는 수정
- `chore`: 설정, 빌드, 자동화 등 기타 작업

예시:

```text
feat: 정책 목록 조회 API 추가
fix: CORS 설정 오류 수정
docs: README 작성
chore: GitHub Actions 설정 추가
```

## PR 제목 규칙

PR 제목은 커밋 메시지와 동일하게 작성합니다.

```text
type: 작업 내용
```

예시:

```text
docs: 협업 문서 정리
```

## PR 본문 규칙

PR 본문에는 관련 이슈를 연결해야 합니다.

```text
Closes #이슈번호
```

예시:

```text
Closes #6
```

`Closes`, `Fixes`, `Resolves` 키워드를 사용할 수 있습니다. PR이 merge되면 연결된 이슈를 닫는 자동화가 실행됩니다.

## 이슈 작성 기준

작업을 시작하기 전에 먼저 이슈를 작성합니다.

- 기능 추가: 새로운 API, 기능, 화면 연동이 필요한 경우
- 버그 수정: 실행 오류, API 오류, 잘못된 동작을 고치는 경우
- 문서 작업: README, Notion, 컨벤션 문서를 수정하는 경우
- 일반 작업: 설정, 자동화, 정리 작업을 하는 경우

이슈에는 작업 내용, 필요 이유, 완료 조건, 참고 사항을 작성합니다.

## PR 작성 기준

PR 템플릿의 항목을 채웁니다.

- 작업 내용
- 변경 사항
- 관련 이슈
- 테스트 결과
- 참고 사항
- 체크리스트

테스트하지 못한 항목이 있으면 숨기지 말고 이유를 적습니다.

## 자동화 규칙

PR에는 다음 자동화가 적용됩니다.

- PR Title Check: PR 제목 형식을 검사합니다.
- Branch Name Check: 브랜치명 형식을 검사합니다.
- PR Linked Issue Check: PR 본문에 이슈 연결 문구가 있는지 검사합니다.
- Discord PR Notify: PR 생성과 PR merge 완료를 Discord로 알립니다.
- Close Linked Issue: PR merge 후 본문의 이슈 번호를 찾아 이슈를 닫습니다.

검사에 실패하면 Actions 로그를 확인하고 규칙에 맞게 수정합니다.

## GitHub Ruleset 운영 기준

`main`과 `develop` 브랜치에는 다음 운영 기준을 권장합니다.

- 직접 push 금지
- PR 필수
- force push 금지
- 브랜치 삭제 제한
- status check 통과 필수
- 리뷰 승인 후 merge

Ruleset은 팀 상황에 맞게 조정하되, `main`과 `develop`이 실수로 깨지지 않는 것을 우선합니다.

## Discord 알림

Discord 알림은 GitHub Actions에서 처리합니다.

- PR 생성 시 알림을 보냅니다.
- PR merge 시 알림을 보냅니다.
- Discord 알림을 사용하려면 Repository Secret에 `DISCORD_WEBHOOK_URL`을 등록해야 합니다.
- Secret이 없거나 Webhook URL이 잘못되면 Discord 알림 workflow가 실패합니다.

## Notion 정리 기준

이 문서는 Notion 협업 가이드 페이지에 복사해서 사용할 수 있습니다.

Notion에는 아래 문서를 함께 정리하면 좋습니다.

- `docs/ONBOARDING.md`
- `docs/CONVENTION.md`
- `docs/CODE_CONVENTION.md`
