# Git Convention

이 프로젝트는 커밋 메시지와 PR 제목에 동일한 형식을 사용합니다.

```text
type: 작업 내용
```

## 허용 Type

| Type | 설명 |
| --- | --- |
| feat | 새로운 기능 추가 |
| fix | 버그 수정 |
| docs | 문서 수정 |
| style | 코드 의미에 영향을 주지 않는 스타일 변경 |
| refactor | 기능 변경 없는 코드 구조 개선 |
| test | 테스트 추가 또는 수정 |
| chore | 빌드, 설정, 기타 작업 |

## 예시

```text
feat: 정책 목록 조회 API 추가
fix: CORS 설정 오류 수정
docs: README 실행 방법 추가
chore: 백엔드 초기 구조 생성
```

## PR 제목 규칙

PR 제목은 다음 정규식에 맞아야 합니다.

```text
^(feat|fix|docs|style|refactor|test|chore): .+
```

## 커밋 메시지 템플릿 사용

아래 명령으로 커밋 메시지 템플릿을 설정할 수 있습니다.

```bash
git config commit.template .gitmessage
```
