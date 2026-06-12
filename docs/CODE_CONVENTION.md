# Backend Code Convention

이 문서는 `bistel-mini-2-backend` 프로젝트에서 FastAPI 백엔드 코드를 작성할 때 지킬 규칙입니다.

## Python 코드 스타일

- 함수명과 변수명은 `snake_case`를 사용합니다.
- 클래스명은 `PascalCase`를 사용합니다.
- 상수명은 `UPPER_SNAKE_CASE`를 사용합니다.
- 한 함수가 너무 많은 일을 하지 않도록 작게 나눕니다.
- 이름만 보고 역할을 알 수 있게 작성합니다.

예시:

```python
def get_policy_list():
    pass


class PolicyResponse:
    pass


DEFAULT_PAGE_SIZE = 20
```

## 파일/폴더 네이밍

- Python 파일은 소문자와 underscore를 사용합니다.
- 기능별로 폴더를 분리합니다.
- 공통으로 쓰는 코드는 `api/common/`에 둡니다.
- 한 파일이 너무 커지면 역할별로 나눕니다.

예시:

```text
policy_router.py
policy_service.py
policy_schema.py
```

## 현재 프로젝트 폴더 역할

- `api/common/`: 공통 설정, 예외 처리, 공통 유틸
- `api/policy/`: 정책 목록/상세 관련 API
- `api/recommend/`: 사용자 조건 기반 정책 추천 API
- `api/rag/`: RAG 기반 질의응답 API
- `static/common/`: 정적 리소스
- `templates/`: 템플릿 파일
- `docs/`: 문서

## FastAPI 작성 규칙

- 라우터는 기능별로 분리합니다.
- 요청 모델과 응답 모델은 분리합니다.
- 요청/응답 검증에는 Pydantic schema를 사용합니다.
- API 경로는 명확한 명사형을 사용합니다.
- 라우터 함수 안에 비즈니스 로직을 길게 작성하지 않습니다.

API 경로 예시:

```text
/policies
/policies/{policy_id}
/recommend
/rag/chat
```

## 계층 분리 기준

- router: HTTP 요청/응답 처리, path/query/body 파라미터 연결
- service: 비즈니스 로직, 외부 API 호출, LangChain 호출
- schema: 요청/응답 모델, 데이터 검증 규칙
- model: DB 엔티티
- rag: 문서 검색, 임베딩, 질의응답 관련 로직

기능이 커지면 아래처럼 분리합니다.

```text
api/policy/
├── router.py
├── service.py
├── schema.py
└── model.py
```

## 환경 변수 규칙

- `.env`는 로컬에서만 사용합니다.
- `.env`는 Git에 올리지 않습니다.
- `.env.example`에는 예시값만 작성합니다.
- API Key를 코드에 직접 작성하지 않습니다.
- DB URL, 비밀번호, Discord Webhook URL 같은 민감정보도 코드에 직접 작성하지 않습니다.

## 로그 규칙

- 단순 확인을 제외하면 `print()`보다 `logging` 사용을 권장합니다.
- 에러 상황에서는 원인을 추적할 수 있는 메시지를 남깁니다.
- 민감정보가 로그에 찍히지 않도록 주의합니다.

## 예외 처리 규칙

- API 오류 응답에는 FastAPI `HTTPException`을 사용합니다.
- 상태 코드는 상황에 맞게 사용합니다.
- 같은 형태의 예외가 반복되면 추후 `api/common/`의 공통 예외 처리 모듈로 분리할 수 있습니다.

예시:

```python
from fastapi import HTTPException


raise HTTPException(status_code=404, detail="Policy not found")
```

## RAG 관련 규칙

- RAG 관련 코드는 `api/rag/` 또는 별도 서비스 계층으로 분리합니다.
- 원본 문서와 임베딩 결과는 Git에 올리지 않습니다.
- 대용량 파일은 Git이 아니라 별도 공유 방식으로 관리합니다.
- 문서 전처리 결과는 꼭 필요한 경우 작은 샘플만 관리합니다.
- API Key나 외부 서비스 설정값은 환경 변수로 관리합니다.

## Git에 올리지 말아야 할 것

- `.env`
- `venv/`
- `__pycache__/`
- `*.pyc`
- 대용량 원본 PDF
- 임베딩 결과 파일
- 개인 로컬 설정 파일
- 민감정보가 포함된 테스트 데이터

## 코드 리뷰 기준

리뷰할 때는 아래 항목을 확인합니다.

- 기능이 한 파일에 과하게 몰리지 않았는지
- API 경로와 함수명이 명확한지
- 요청 모델과 응답 모델이 분리되어 있는지
- 예외 처리가 호출자에게 이해 가능하게 작성되었는지
- `.env`, `venv/`, `__pycache__/` 같은 불필요한 파일이 포함되지 않았는지
- 실행 방법이나 환경 변수가 바뀌었다면 문서에 반영되었는지
