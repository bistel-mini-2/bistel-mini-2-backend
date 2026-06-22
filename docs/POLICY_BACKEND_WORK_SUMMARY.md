# Policy Backend & RAG Workflow Summary

> Issue: docs: 정책 백엔드 및 RAG 작업 흐름 문서화 #92

이 문서는 정책 데이터 import부터 문서 기반 RAG, 판단/비교 Agent까지 이어지는 백엔드 작업 흐름을 정리한다.
정책 파트의 DB 저장 구조, 문서 처리 방식, RAG 검색 구조, 현재 처리 결과와 남은 이슈를 한 번에 확인하는 것을 목표로 한다.

## 1. 작업 목적

정책 관련 백엔드 작업의 목표는 공공데이터 기반 정책 정보를 DB에 안정적으로 저장하고, 저장된 정책 원문과 참고 문서를 RAG 근거로 활용해 사용자 조건에 맞는 추천/판단/비교 기능을 구현하는 것이다.

정책 데이터는 단순 목록 조회에만 쓰이지 않는다. 사용자가 입력한 가족 상황, 소득, 지역, 자녀 나이, 특수 조건 등을 기준으로 정책을 판단해야 하므로 다음 흐름이 필요하다.

```text
공공데이터/JSON
  -> 정책 DB 저장
  -> 정책 상세 원문 저장
  -> 정책 참고 문서 저장
  -> chunk 생성
  -> embedding 저장
  -> RAG 검색
  -> Policy Assessment Agent 판정
  -> 추천/비교/지원 가능성 결과 제공
```

## 2. 전체 처리 흐름

정책 관련 데이터는 크게 구조화 데이터와 비정형 문서 데이터로 나뉜다.

| 구분 | 설명 | 주요 저장 위치 |
| --- | --- | --- |
| 정책 기본 정보 | 정책명, 제공기관, 분류, 신청 방법 등 목록/상세 화면에 필요한 정보 | `policy`, `policy_detail` |
| 정책 조건 | 나이, 지역, 소득, 대상 조건 등 rule filter와 판단에 필요한 정보 | `policy_condition` |
| 신청 체크리스트 | 신청 전 사용자가 확인해야 하는 항목 | `policy_checklist_template` |
| 필수 제출 서류 | 사용자가 신청 시 실제 제출해야 하는 서류 | `required_document` |
| 정책 상세 원문 | 공공데이터 상세 응답을 RAG 근거로 재구성한 원문 | `policy_document`, `source_type = POLICY_DETAIL` |
| 정책 참고 문서 | 정책 안내 PDF, 사업 지침, 서식 등 정책 관련 참고자료 | `policy_document`, `source_type = POLICY_REFERENCE` |
| RAG chunk | 원문/참고문서를 검색 가능한 단위로 나눈 텍스트 | `policy_document_chunk` |
| embedding | chunk 검색을 위한 vector 저장 | `langchain_pg_embedding` |
| 판단 결과 | 정책별 최종 판정 상태와 evidence | `policy_assessment` |

## 3. 정책 관련 주요 테이블

### 3.1 `policy`

정책의 기준 테이블이다.

- 내부 PK는 `policy_id`를 사용한다.
- 외부 노출 식별자는 공공데이터의 `servId` 기반 `policy_code`를 사용한다.
- 정책명, 제공기관, 카테고리, 활성 여부 등 정책의 공통 정보를 저장한다.

### 3.2 `policy_detail`

정책 상세 화면과 RAG 원문 구성에 필요한 상세 설명을 저장한다.

- 쉬운 요약
- 지원 대상
- 지원 내용
- 신청 방법
- 신청 기간 텍스트
- 유의 사항

공공데이터에서 명확한 시작일/종료일 태그가 내려오지 않는 경우가 있어, 현재는 `application_period_text`처럼 문자열 설명 중심으로 저장한다.

### 3.3 `policy_condition`

추천/판단의 rule filter에서 사용할 조건을 저장한다.

- 나이 조건
- 지역 조건
- 소득 조건
- 대상 조건
- 기타 특수 조건

이 테이블의 조건은 명확하게 탈락시킬 수 있는 정책을 먼저 거르기 위한 구조화 데이터로 사용한다.

### 3.4 `policy_checklist_template`

사용자가 정책 신청을 준비할 때 확인해야 하는 체크리스트 템플릿을 저장한다.

예시:

- 신청 자격 확인
- 신청 기간 확인
- 온라인/방문 신청 가능 여부 확인
- 필요 서류 준비

사용자별 체크리스트 진행 상태와 분리되어야 하므로, 정책 데이터 import 재실행 시 사용자 진행 상태가 `CASCADE`로 삭제되지 않도록 주의했다.

### 3.5 `required_document`

사용자가 실제 신청 시 제출해야 하는 필수 서류를 저장한다.

정책 관련 PDF, 사업 지침, 안내서 같은 참고 문서는 이 테이블에 넣지 않는다.

구분 기준:

| 데이터 | 저장 위치 |
| --- | --- |
| 주민등록등본, 가족관계증명서, 신청서, 진단서 등 신청자가 제출해야 하는 서류 | `required_document` |
| 사업 안내 PDF, 행정 지침, 제도 설명서, 서식 모음, 참고자료 | `policy_document` |

초기에는 정책 관련 문서명도 `required_document`에 섞여 들어가는 문제가 있었고, 이후 필수 제출 서류와 정책 참고 문서를 분리하도록 수정했다.

### 3.6 `policy_document`

RAG의 원천 문서 역할을 한다.

현재 주요 `source_type`은 두 가지다.

| source_type | 의미 | raw_text |
| --- | --- | --- |
| `POLICY_DETAIL` | 정책 상세 데이터를 기반으로 만든 원문 | 정책 상세 설명을 섹션별로 조합한 텍스트 |
| `POLICY_REFERENCE` | 정책 관련 참고 문서, PDF, 지침, 서식 | PDF에서 추출한 텍스트 |

`POLICY_DETAIL`과 `POLICY_REFERENCE`는 같은 테이블에 저장하지만, `source_type`으로 처리 범위를 분리한다.
이렇게 하면 RAG 검색 시 정책 상세만 검색하거나, 참고 문서만 검색하거나, 둘을 함께 검색할 수 있다.

### 3.7 `policy_document_chunk`

`policy_document.raw_text`를 RAG 검색 단위로 나눈 chunk를 저장한다.

주요 값:

- `chunk_id`
- `document_id`
- `chunk_index`
- `chunk_text`
- `metadata_json`

`metadata_json`에는 검색 결과를 Agent evidence로 연결하기 위한 정보가 들어간다.

예시:

```json
{
  "policy_id": 331,
  "policy_code": "WLF00005442",
  "policy_name": "긴급돌봄 지원사업",
  "source_type": "POLICY_REFERENCE",
  "source_title": "2026년 긴급돌봄 지원사업 안내_최종.pdf",
  "source_url": "https://...",
  "section": "관련 문서",
  "evidence_role": "reference",
  "file_type": "PDF"
}
```

### 3.8 `langchain_pg_embedding`

LangChain `PGVector`가 사용하는 embedding 저장 테이블이다.

`policy_document_chunk.chunk_id`를 문자열 id로 사용해 chunk와 embedding을 연결한다.
RAG 검색은 이 테이블의 vector를 기반으로 수행된다.

### 3.9 `policy_assessment`

Policy Assessment Agent의 최종 판단 결과를 저장한다.

주요 저장 대상:

- 내부 5상태 판정
- 외부 3상태 매핑 결과
- matched conditions
- missing conditions
- conflicting conditions
- manual check points
- RAG evidence
- assessment type

`assessment_type`은 추천용 다건 판단과 특정 정책 상세 판단을 구분하기 위해 사용한다.

## 4. 정책 JSON Import 작업

공공데이터 또는 JSON으로 변환한 정책 데이터를 DB에 저장하는 작업을 먼저 진행했다.

주요 처리 내용:

- 정책 기본 정보 저장
- 정책 상세 정보 저장
- 정책 조건 저장
- 신청 체크리스트 템플릿 저장
- 필수 제출 서류 저장
- 정책 참고 문서 메타데이터 저장

import 작업에서 중요했던 기준은 재실행 가능성이다.

정책 데이터는 공공데이터 변경이나 매핑 수정으로 여러 번 다시 넣을 수 있어야 한다.
따라서 import 재실행 시 기존 정책 관련 데이터는 갱신하되, 사용자별 진행 상태처럼 운영 중 생성되는 데이터가 삭제되지 않도록 조정했다.

특히 사용자 체크리스트 진행 상태가 정책 체크리스트 템플릿 삭제와 함께 `CASCADE`로 사라지는 문제가 있었고, 정책 import 재실행 시 사용자 진행 데이터가 보존되도록 처리 방향을 정리했다.

## 5. 필수 제출 서류와 정책 참고 문서 분리

초기 데이터 확인 과정에서 `required_document`에 신청 시 필요한 필수 서류뿐 아니라 정책 관련 안내 문서까지 들어간 문제가 있었다.

수정 기준은 다음과 같다.

| 항목 | 예시 | 저장 위치 |
| --- | --- | --- |
| 필수 제출 서류 | 신청서, 진단서, 가족관계증명서, 주민등록등본 | `required_document` |
| 정책 관련 문서 | 사업안내 PDF, 운영지침, 서식 모음, 법령 PDF | `policy_document` |

이 분리는 신청 준비 기능과 RAG 기능을 명확하게 나누기 위해 필요했다.

- `required_document`: 사용자가 실제로 준비해야 하는 제출물
- `policy_document`: Agent가 판단 근거로 검색할 수 있는 정책 자료

## 6. 정책 상세 데이터 RAG 처리

정책 상세 데이터는 `POLICY_DETAIL` 문서로 저장한다.

### 6.1 raw_text 구성

정책 상세 데이터는 섹션별로 나눠 원문을 구성한다.

현재 주요 섹션:

- 기본 정보
- 요약
- 지원 대상
- 지원 내용
- 신청 방법
- 신청 기간
- 유의 사항

각 섹션은 다음과 같은 형태로 문서화된다.

```text
정책명: 긴급돌봄 지원사업
섹션: 지원 대상
내용:
...
```

### 6.2 chunk 생성

`POLICY_DETAIL`은 정책별 상세 설명이 비교적 짧기 때문에 작은 chunk 크기를 사용한다.

현재 기준:

- chunk size: 500
- chunk overlap: 50

생성된 chunk는 `policy_document_chunk`에 저장된다.

### 6.3 embedding 저장

생성된 chunk 중 embedding이 없는 대상을 조회해 `PGVector`에 저장한다.

사용 모델:

```text
openai:text-embedding-3-large
```

처리 결과:

- `POLICY_DETAIL` 정책 문서 77개 처리
- chunk 602개 생성
- embedding 602개 저장
- embedding 누락 0개 확인

## 7. POLICY_REFERENCE PDF 처리

`POLICY_REFERENCE`는 정책 관련 PDF나 지침 문서를 RAG 근거로 활용하기 위한 데이터다.

처리 흐름:

```text
policy_document(source_type = POLICY_REFERENCE)
  -> source_url 기준 PDF 다운로드
  -> PDF 텍스트 추출
  -> policy_document.raw_text 저장
  -> policy_document_chunk 생성
  -> langchain_pg_embedding 저장
```

### 7.1 PDF 다운로드 기준

대상 조건:

- `source_type = POLICY_REFERENCE`
- `source_url` 존재
- `source_title`이 PDF로 보이는 문서
- `raw_text`가 비어 있는 문서

같은 `source_url`을 여러 정책이 공유하는 경우가 있어서 URL 기준으로 그룹핑해 한 번 다운로드한 뒤, 연결된 각 `policy_document`에 같은 raw text를 저장한다.

### 7.2 텍스트 추출

PDF는 `pypdf.PdfReader`로 텍스트를 추출한다.

현재 지원 상태:

| 파일 타입 | 지원 여부 |
| --- | --- |
| PDF | 지원 |
| HWP | 미지원 |
| HWPX | 미지원 |
| HTML/JSON 응답 | 미지원 |

### 7.3 chunk 생성

PDF 참고 문서는 정책 상세보다 길기 때문에 더 큰 chunk 크기를 사용한다.

현재 기준:

- chunk size: 2000
- chunk overlap: 200

metadata에는 다음 정보를 포함한다.

- `source_type = POLICY_REFERENCE`
- `section = 관련 문서`
- `evidence_role = reference`
- `file_type = PDF`
- `source_title`
- `source_url`

### 7.4 embedding 저장

`POLICY_REFERENCE` chunk도 기존 RAG embedding API와 같은 흐름으로 저장한다.

처리 기준:

```text
source_type = POLICY_REFERENCE
embedding이 없는 chunk
```

### 7.5 처리 결과

최종 DB 처리 결과:

| 항목 | 수량 |
| --- | ---: |
| `POLICY_REFERENCE` PDF 문서 | 57개 |
| `raw_text` 저장 완료 | 51개 |
| 처리 제외/잔여 문서 | 6개 |
| 생성된 `POLICY_REFERENCE` chunk | 11,322개 |
| embedding 완료 chunk | 11,322개 |
| embedding 누락 chunk | 0개 |

처리 중 검색 품질을 해치는 깨진 chunk 3개가 발견되어 `policy_document_chunk`와 `langchain_pg_embedding`에서 함께 제거했다.

제거한 chunk:

| chunk_id | 문서 |
| --- | --- |
| `28930020067` | 2025 에너지바우처 사업안내서.pdf |
| `28930020068` | 2025 에너지바우처 사업안내서.pdf |
| `24530010098` | 2026년 장애인복지_사업안내_2.pdf |

## 8. PDF 처리 제외 문서

현재 방식으로 처리되지 않은 PDF 문서는 6개다.

| document_id | 문서명 | 사유 |
| --- | --- | --- |
| `2823001` | 2026년 국가예방접종 지침(의료기관용).pdf | 응답이 실제 PDF가 아니라 `<DOCUMENTSAFER_...` 형식 |
| `3003001` | (누리집 탑재용) 2026년도 특수교육 운영계획.pdf | PDF 응답은 오지만 텍스트 추출 결과 없음 |
| `3003002` | (누리집 탑재용) 2026년도 특수교육 운영계획.pdf | PDF 응답은 오지만 텍스트 추출 결과 없음 |
| `3083001` | 2026년 아이돌봄 지원사업 안내.pdf | PDF 응답은 오지만 텍스트 추출 결과 없음 |
| `3083002` | 2026년 아이돌봄 지원사업 안내.pdf | PDF 응답은 오지만 텍스트 추출 결과 없음 |
| `3303001` | 아동수당서식모음.pdf | 응답이 PDF가 아니라 JSON 에러 응답 |

이 문서들은 향후 OCR, 다른 PDF parser, 원본 다운로드 URL 재확인, 문서 보안 래퍼 처리 등을 검토해야 한다.

## 9. RAG 검색 구조

RAG 검색은 `PolicyRagService`에서 처리한다.

검색 대상은 `policy_document_chunk`와 `langchain_pg_embedding`에 저장된 chunk다.

지원하는 주요 필터:

- `source_type`
- `policy_ids`
- `policy_code`

예시:

```text
source_type = POLICY_DETAIL
source_type = POLICY_REFERENCE
policy_ids = [331]
```

정책 상세 판단에서는 `POLICY_DETAIL`이 기본 근거가 되고, 참고 문서까지 필요한 경우 `POLICY_REFERENCE`를 함께 사용한다.

중요한 점은 `POLICY_REFERENCE` 문서 중 다수가 하나의 정책만 설명하는 문서가 아니라 여러 정책이 포함된 통합 지침이라는 것이다.
따라서 참고 문서 검색은 단독으로 사용하기보다 정책 후보가 정해진 뒤 `policy_id` 또는 `policy_code` 필터와 함께 쓰는 것이 더 안전하다.

## 10. Policy Assessment Agent

Policy Assessment Agent는 추천과 지원 가능성 판단에서 공통으로 사용하는 정책별 최종 판정 엔진이다.

입력:

- 사용자 프로필
- 정규화된 사용자 조건
- 정책 조건
- RAG 검색 evidence
- 누락 정보
- 충돌 정보

출력:

- 내부 5상태 판정
- 외부 3상태 매핑
- 판단 근거
- 추가 확인 사항
- evidence

### 10.1 내부 5상태

| 상태 | 의미 |
| --- | --- |
| `LIKELY_MATCH` | 현재 정보로 추천 가능성이 높음 |
| `NEEDS_MORE_INFO` | 핵심 필드 1개가 부족하거나 모호하지만 결과 변동이 작음 |
| `NOT_MATCH` | 핵심 조건이 불일치함 |
| `INSUFFICIENT_PROFILE` | 핵심 필드 2개 이상 부족해 판단 신뢰도가 낮음 |
| `CONFLICTING_PROFILE` | 입력 충돌로 정규화 결과가 다중이거나 rule filter 결과가 갈림 |

### 10.2 외부 3상태 매핑

내부 5상태는 사용자 응답에서는 더 단순한 3상태로 매핑한다.

| 내부 상태 | 외부 상태 |
| --- | --- |
| `LIKELY_MATCH` | 추천 가능 |
| `NEEDS_MORE_INFO` | 추가 정보 필요 |
| `INSUFFICIENT_PROFILE` | 추가 정보 필요 |
| `CONFLICTING_PROFILE` | 추가 정보 필요 |
| `NOT_MATCH` | 추천 불가 |

### 10.3 판단 근거

Assessment 결과에는 다음 근거가 포함된다.

- `matched_conditions`
- `missing_conditions`
- `conflicting_conditions`
- `manual_check_points`
- `evidence`

RAG 검색 결과는 evidence로 연결되며, chunk metadata의 `source_type`, `section`, `evidence_role`, `source_url` 등을 통해 어떤 자료를 근거로 판단했는지 추적할 수 있다.

## 11. 추천/비교 기능에서의 사용 흐름

정책 추천과 비교는 같은 판단 구조를 공유한다.

### 11.1 추천 흐름

```text
사용자 조건 입력
  -> Condition Agent 정규화
  -> Rule Filter로 명확한 탈락 정책 제외
  -> 후보 정책별 RAG evidence 검색
  -> Policy Assessment Agent 판정
  -> 추천 결과 저장/응답
```

### 11.2 특정 정책 지원 가능성 판단

```text
사용자 조건 + 특정 policy_code
  -> 정책 조건 조회
  -> 정책 상세/참고 문서 RAG 검색
  -> Policy Assessment Agent 판정
  -> 가능/불가능/추가 정보 필요와 근거 제공
```

### 11.3 정책 비교

```text
비교할 정책 2개 이상 선택
  -> 사용자 조건 기준으로 각 정책 Assessment 실행
  -> 혜택/조건/신청 난이도/필요 서류/주의사항 비교
  -> 사용자 상황에 맞는 선택 가이드 제공
```

## 12. 검증 내용

정책 관련 작업에서 확인한 내용은 다음과 같다.

| 검증 항목 | 결과 |
| --- | --- |
| 정책 JSON import 후 DB 저장 확인 | 완료 |
| 필수 제출 서류와 정책 참고 문서 분리 확인 | 완료 |
| `POLICY_DETAIL` chunk 생성 확인 | 완료 |
| `POLICY_DETAIL` embedding 저장 확인 | 완료 |
| `POLICY_REFERENCE` PDF 다운로드 및 raw_text 저장 확인 | 완료 |
| `POLICY_REFERENCE` chunk 생성 확인 | 완료 |
| `POLICY_REFERENCE` embedding 저장 확인 | 완료 |
| embedding 누락 chunk 확인 | 0개 |
| RAG 검색 결과 반환 확인 | 완료 |
| 깨진 chunk 제거 | 3개 제거 |
| 작업 후 브랜치 상태 | clean |

## 13. 현재 남은 이슈

### 13.1 일부 PDF 처리 불가

일부 문서는 확장자나 제목은 PDF지만 실제 응답이 PDF가 아니다.

예시:

- `<DOCUMENTSAFER_...` 응답
- JSON 에러 응답

이 경우 현재 PDF parser로는 처리할 수 없다.

### 13.2 PDF 텍스트 추출 한계

일부 PDF는 실제 PDF로 내려오지만 텍스트 추출 결과가 비어 있다.
스캔 이미지 기반 PDF이거나 보안/인코딩 문제일 가능성이 있다.

개선 후보:

- OCR 적용
- 다른 PDF parser 검토
- 원본 URL 재수집
- 다운로드 응답 포맷 분석

### 13.3 통합 지침 문서의 검색 정확도

`POLICY_REFERENCE` 문서는 정책 하나만 설명하지 않고 여러 정책을 포함하는 경우가 많다.
예를 들어 모자보건사업 안내 PDF 하나가 여러 정책에 연결될 수 있다.

따라서 참고 문서 검색은 전체 검색으로만 사용하면 현재 사용자가 보고 있는 정책과 다른 정책의 chunk가 상위에 나올 수 있다.

권장 방식:

```text
정책 후보를 먼저 좁힌 뒤
policy_id 또는 policy_code 필터와 함께 POLICY_REFERENCE 검색
```

### 13.4 문서 처리 책임 분리

현재 `POLICY_REFERENCE` 처리 흐름은 다운로드, raw_text 저장, chunk 생성이 같은 흐름에 묶여 있다.
embedding은 별도 흐름으로 분리되어 있다.

향후 운영성을 높이려면 다음처럼 나눌 수 있다.

```text
1. 문서 다운로드 및 raw_text 저장
2. raw_text 기반 chunk 생성
3. chunk embedding 저장
4. 검색 품질 진단 및 재처리
```

이렇게 나누면 실패 문서 재처리, chunk size 변경, parser 교체가 더 쉬워진다.

## 14. 관련 이슈

정책 백엔드/RAG 흐름과 연결된 주요 이슈는 다음과 같다.

| 이슈 | 내용 |
| --- | --- |
| #45 | JSON으로 변환한 데이터 DB에 저장하기 |
| #55 | 정책 관련 문서와 필수 제출 서류 저장 분리 |
| #58 | import 재실행 시 사용자 체크리스트 진행 상태 CASCADE 소실 방지 |
| #59 | 정책 데이터 import 구조 정리 |
| #65 | 정책 상세 데이터 기반 RAG chunk 생성 |
| #69 | 정책 RAG embedding 저장 및 검색 구현 |
| #73 | 정책 RAG 검색 기반 판단/비교 Agent 구현 |
| #76 | 정책 RAG chunk 및 embedding 전체 배치 처리 개선 |
| #78 | Policy Assessment Agent 5상태 판정 구현 |
| #85 | POLICY_REFERENCE 문서 다운로드 및 RAG 처리 |
| #92 | 정책 백엔드 및 RAG 작업 흐름 문서화 |

## 15. 정리

정책 백엔드 작업은 단순히 공공데이터를 DB에 넣는 작업에서 끝나지 않는다.

정책 조건은 구조화해서 rule filter와 Agent 판단에 사용하고, 정책 상세와 참고 문서는 RAG 검색 가능한 문서로 변환해 판단 근거로 사용한다.
최종적으로 Policy Assessment Agent가 구조화 조건과 RAG evidence를 결합해 추천 가능성, 추가 정보 필요, 추천 불가 상태를 판정한다.

현재 `POLICY_DETAIL`과 처리 가능한 `POLICY_REFERENCE` PDF는 chunk와 embedding까지 완료되어 RAG 검색에 사용할 수 있는 상태다.
남은 작업은 처리 실패 PDF의 재수집/대체 parser 검토, 통합 지침 검색 정확도 개선, 문서 처리 단계 분리다.
