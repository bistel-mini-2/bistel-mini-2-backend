# Dodam U6 Product Lifecycle Baseline Command Prompt

Status: ready to run
Prepared At: 2026-08-18

아래 지시를 현재 Codex 작업의 명령 프롬프트로 사용한다.

```text
작업 위치:
- Backend: /Users/hb/Documents/kosa-course/projects/mini-2/back
- Frontend: /Users/hb/Documents/kosa-course/projects/mini-2/front

목표:
Dodam 제품화 계획의 U6(Product lifecycle baseline and deployment decision)만 수행한다.
사용자 입력부터 frontend, API, AI workflow, DB 저장, 복구, 결과 표시까지 현재 구현을 코드로 추적하고,
Vercel Hobby + Railway Hobby/Railway PostgreSQL 배포 결정을 검증 가능한 근거로 정리한다.

현재까지 완료된 선행 근거:
- U1~U4: retrieval repeated benchmark와 decision artifact 완료
- U5: generated grounding live capture/review 완료
- 현재 단계에서는 retrieval correctness, generated grounding, display quality, product lifecycle을 서로 다른 근거로 분리한다.

반드시 먼저 읽을 문서:
1. /Users/hb/Documents/kosa-course/projects/mini-2/back/docs/plans/2026-08-15-001-test-dodam-retrieval-evidence-plan.md
2. /Users/hb/Documents/kosa-course/projects/mini-2/back/docs/AGENT_WORKFLOW.md
3. /Users/hb/Documents/kosa-course/projects/mini-2/back/docs/API_SPEC_AI.md
4. /Users/hb/Documents/kosa-course/projects/mini-2/front/docs/API_SPEC_AI.md

작업 규칙:
1. U6만 수행한다. U7의 lifecycle 수정, U8 CI, U9 Docker/health, U10 실제 배포를 시작하지 않는다.
2. 제품 코드, 테스트 코드, schema, workflow, 환경 파일을 수정하지 않는다.
3. 허용되는 유일한 산출물은 backend의 docs/PRODUCT_LIFECYCLE_BASELINE.md다.
4. backend와 frontend는 별도 Git 저장소다. 각각 현재 branch, HEAD, git status를 확인한다.
5. 기존 수정, 삭제, 미추적 파일은 사용자 소유 변경으로 간주하고 덮어쓰거나 정리하지 않는다.
6. git reset, checkout 복구, stash, clean, commit, push, PR 생성은 하지 않는다.
7. OpenAI 유료 호출, live retrieval/generation 평가, 외부 배포, cloud resource 생성은 하지 않는다.
8. 비밀값은 존재 여부만 확인한다. API key, DB URL, token, 사용자 데이터 값을 출력하거나 문서에 기록하지 않는다.
9. 구현됨, 부분 구현, 문서만 존재, 미구현, 미검증을 엄격히 구분한다.
10. 파일 존재만으로 연결된 workflow나 배포 가능 상태라고 판단하지 않는다.
11. U1~U5 산출물을 재실행하거나 수정하지 않는다. 필요한 경우에는 읽기 근거로만 사용한다.
12. `docs/PRODUCT_LIFECYCLE_BASELINE.md`에 코드 감사 결과와 배포 판단을 함께 쓰되, 추정과 확인된 사실을 섞지 않는다.

검토할 실제 사용자 흐름:
사용자 입력
-> frontend submit 또는 SSE 연결
-> backend API request 생성
-> request lifecycle 상태 변경
-> LangGraph/service 실행
-> retrieval/rule/LLM/fallback
-> DB status/result 저장
-> SSE disconnect 또는 새로고침 후 조회 복구
-> frontend 결과 및 오류 표시

각 단계에서 반드시 확인할 항목:
- 실제 producer와 consumer 파일
- request/correlation ID 전달
- 인증 및 사용자 소유권
- 상태 enum과 허용 전이
- idempotency 또는 중복 방지
- timeout과 retry
- SSE disconnect 복구
- process restart 및 stale processing 복구
- LLM/embedding/DB 실패 처리
- deterministic fallback 표시
- DB 저장 여부
- backend test와 frontend test
- 구현 소유 저장소

필수 결과표:
각 단계에 대해 다음 열을 포함한다.
- Stage
- Backend evidence
- Frontend evidence
- Persisted state
- Failure/recovery behavior
- Test evidence
- Status: implemented / partial / missing / not verified
- Gap and impact

배포 결정 검토:
현재 working decision은 다음과 같다.
- Frontend: Vercel Hobby
- Backend: Railway Hobby
- Database: Railway PostgreSQL
- External AI: existing OpenAI API

다음 대안과 비교한다.
A. Vercel frontend + Railway backend/PostgreSQL
B. Render 중심 배포
C. 단일 host Docker Compose

비교 기준:
- Next.js 적합성
- FastAPI 및 SSE/장기 AI 요청
- PostgreSQL 영속성
- secret 관리
- sleep/cold start
- health/readiness
- preview deployment와 CI/CD 연결
- revision 추적과 rollback
- 예상 월 비용과 비용 상한
- 운영 및 종료 편의성

플랫폼의 현재 가격과 제한을 확인할 수 있다면 공식 문서만 사용하고 확인 날짜와 링크를 남긴다.
인터넷 접근이 없으면 기존 기억으로 확정하지 말고 not verified로 표시한다.
가격/제한 확인 시 우선순위:
- Vercel 공식 pricing/docs
- Railway 공식 pricing/docs
- Render 공식 pricing/docs
블로그, 커뮤니티 글, 과거 기억, 모델 지식은 가격/제한의 확정 근거로 사용하지 않는다.

배포 결정 문서에 반드시 포함할 내용:
1. 왜 Vercel + Railway 조합을 우선 채택하는지
2. Render와 단일 Compose를 채택하지 않는 이유
3. Vercel/Railway platform rollback이 DB migration rollback을 대신하지 않는다는 경계
4. Railway healthcheck가 지속 monitoring을 대신하지 않는다는 경계
5. 비용 상한과 공개 운영 기간 중 아직 사용자 확인이 필요한 항목
6. 실제 배포 전 U7~U10에서 해결해야 할 blocker

검증:
- 기본은 정적 코드·문서 감사다.
- 외부 호출과 DB 변경이 없는 기존 test만 필요 최소 범위로 실행할 수 있다.
- test 실행 시 cache나 tracked output을 만들지 않도록 하고, 실행하지 못한 검증은 숨기지 않는다.
- backend와 frontend의 기존 dirty change와 충돌 여부를 마지막에 다시 확인한다.
- 가격/제한 공식 문서 확인은 네트워크가 허용될 때만 수행하고, 실패하면 실패 사유와 함께 `not verified`로 남긴다.

docs/PRODUCT_LIFECYCLE_BASELINE.md 필수 구성:
1. Status and audit timestamp
2. Repository revisions and dirty-worktree boundary
3. End-to-end user lifecycle trace
4. Lifecycle status matrix
5. Existing strengths
6. Productization gaps ordered by severity
7. Deployment option decision matrix
8. Working decision: Vercel + Railway
9. Cost, privacy, secret, migration, rollback boundaries
10. U7 proposed scope, allowed files, acceptance, stop conditions
11. Portfolio claims: can say / cannot say yet

완료 보고:
계획 문서의 Confirmation Report 형식을 사용해 "Dodam Confirmation 6"을 한국어로 보고한다.
반드시 다음을 포함한다.
- 현재 완성된 사용자 흐름
- 가장 큰 제품화 공백 3개
- Vercel + Railway 선택 근거와 트레이드오프
- 변경 파일과 실행한 검증
- 아직 말할 수 없는 배포·운영 주장
- U7의 정확한 허용 범위

Confirmation 6 보고 후 반드시 멈춘다.
사용자가 승인하기 전에는 U7 구현을 시작하지 않는다.
```
