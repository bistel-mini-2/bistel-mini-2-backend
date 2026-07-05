# AI 서비스 아키텍처 시각화 설계

## 1. 목적

`mini-2`의 전체 AI 구조를 한 화면에서 이해할 수 있는 기술 문서용 다이어그램을 제작한다. 발표용 요약 슬라이드가 아니라, 프로젝트 문서·README·포트폴리오에서 재사용할 수 있도록 실제 코드의 책임과 데이터 흐름을 충분히 드러낸다.

최종 산출물은 편집 가능한 SVG와 확인용 PNG다.

## 2. 표현 범위

다이어그램은 다음 세 흐름을 함께 보여준다.

1. 사전 데이터 구축: 정책 원문 수집부터 chunk·embedding 저장까지
2. 실시간 AI 실행: 사용자 요청부터 Handler 및 독립 LangGraph 실행까지
3. 응답과 영속화: 근거 포함 구조화 응답, SSE 전송, 메시지·판정 결과 저장까지

신청 준비는 독립 AI 그래프로 표현하지 않는다. 선택된 정책의 신청 정보와 체크리스트를 조회·저장하는 후속 서비스로 표시한다.

## 3. 레이아웃

전체 캔버스는 가로형 혼합 구조를 사용한다.

- 상단: 제목, 한 줄 설명, 범례
- 좌측 하단: 사전 정책 데이터 구축 파이프라인
- 중앙 상단: 사용자와 Frontend, FastAPI 진입점
- 중앙: ChatService와 Handler Router를 포함한 실시간 오케스트레이션
- 중앙 하단: 추천·지원 가능성·비교·정책 요약 독립 LangGraph
- 우측: OpenAI 모델과 외부 정책 원문
- 하단 중앙: PostgreSQL + pgvector 및 결과 저장

중앙 AI 실행 영역을 가장 크게 배치한다. 사전 구축 흐름은 점선, 실시간 요청은 실선, DB 저장은 굵은 실선으로 구분한다.

## 4. 구성요소

### 4.1 사용자 및 Frontend

- 추천
- 지원 가능성 확인
- 정책 비교
- 정책 요약
- AI 채팅
- 신청 준비
- Next.js API client
- SSE / Polling

### 4.2 FastAPI 서비스 계층

- Controllers
- Services
- Repositories
- Schemas
- Auth / JWT
- `ChatService`
- 요청 생명주기와 저장 트랜잭션

### 4.3 채팅 오케스트레이션

- 최근 대화와 `slot_json` 로드
- Follow-Up 상태 우선 처리
- LLM 구조화 의도 분류
- 저장 프로필 확인
- 부족한 조건 수집
- Handler 직접 라우팅
- 공통 응답 품질 검증

채팅 라우팅은 LangGraph가 아니라 Python Handler 기반임을 시각적으로 명시한다.

### 4.4 독립 AI 워크플로우

- Recommendation Graph
- Eligibility Graph
- Comparison Graph
- Policy Summary Graph

각 워크플로우는 필요에 따라 다음 공통 기능을 사용한다.

- 조건 추출 및 프로필 병합
- 규칙 기반 후보 필터링
- RAG 근거 검색
- LLM 기반 판정·재정렬·요약
- 추가 질문 생성
- 정책 링크와 근거 연결

### 4.5 데이터 구축 및 RAG

- 공공데이터·복지로·PDF/HTML 정책 문서
- 정책 원문 수집
- PDF 텍스트 추출 및 OpenAI Vision 보완
- chunk 생성
- `text-embedding-3-large` embedding
- PostgreSQL + pgvector 저장 및 유사도 검색

### 4.6 외부 AI

- 주요 채팅·판정·요약 모델: `gpt-5.4-mini`
- 정책 문서 Vision 보완: `gpt-4o`
- embedding: `text-embedding-3-large`

### 4.7 저장 데이터

- 정책 및 정책 chunk
- 사용자·가족 프로필
- 채팅 세션·메시지·`slot_json`
- AI 요청 생명주기 상태
- 추천 후보
- 정책별 assessment
- assessment evidence
- follow-up question
- 비교 이력과 신청 준비 상태

## 5. 핵심 데이터 흐름

### 5.1 사전 구축

`외부 정책 원문 → 수집·텍스트 추출 → chunk → embedding → PostgreSQL/pgvector`

### 5.2 일반 실시간 요청

`사용자 → Frontend → FastAPI → 기능별 Service/Graph → RAG·규칙·LLM → 구조화 응답 → DB 저장 → SSE/Polling → 화면`

### 5.3 채팅 요청

`채팅 입력 → ChatService → Follow-Up/진행 상태 확인 → Intent Classifier → Handler Router → 기능별 Graph 또는 DB 서비스 → 공통 품질 검증 → 응답·근거 저장 → SSE done`

## 6. 시각 디자인

브라운 계열의 큰 면은 사용하지 않는다. 코랄·살구·피치·크림을 중심으로 따뜻하고 밝게 구성한다.

| 용도 | 색상 |
|---|---|
| 배경 | `#FFF9F3` |
| 제목·핵심선 | `#D94F5C` |
| 핵심 AI 영역 | `#F36B72` |
| 서비스 영역 | `#FFB38A` |
| 데이터 영역 | `#FFD4BE` |
| 보조 카드 | `#FFE4E8` |
| 기본 카드 | `#FFFFFF` |
| 본문 글자 | `#403836` |
| 연결선 | `#D9826B` |

- 핵심 AI 영역만 진한 코랄을 사용한다.
- 카드 모서리는 부드럽게 라운드 처리한다.
- 영역 제목은 영문 키워드와 쉬운 한국어 설명을 함께 쓴다.
- 작은 코드 파일명보다 역할명을 우선 표기하고, 필요한 곳에만 실제 클래스명을 보조 표기한다.
- 아이콘은 사용자, API, AI, DB, 문서 정도로 제한한다.

## 7. 오류·복구 표현

다이어그램을 복잡하게 만들지 않도록 오류 흐름은 별도 박스로 늘리지 않는다. 대신 FastAPI 영역에 다음 항목을 짧게 표기한다.

- 요청 상태: processing / completed / failed / cancelled
- 연결 종료 후 복구
- idempotency key
- 최종 저장 성공 후 SSE `done`

## 8. 정확성 및 시각 검증

- 채팅 Supervisor Graph가 현재 구조에 남아 있는 것처럼 표현하지 않는다.
- 추천·지원 가능성·비교·정책 요약만 독립 LangGraph로 표시한다.
- 신청 준비를 AI Graph로 표시하지 않는다.
- 규칙 판정과 LLM 판단을 같은 박스로 뭉개지 않는다.
- RAG 검색과 PostgreSQL 일반 저장 역할을 구분한다.
- SVG 텍스트 잘림, 연결선 교차, 100% 확대 시 가독성을 확인한다.
- PNG 렌더링 후 작은 글자와 색상 대비를 육안 검수한다.

## 9. 산출물

- `output/architecture/ai-service-architecture.svg`
- `output/architecture/ai-service-architecture.png`

