# Field Mapping & Policy Identifier Rules

## 1. Policy Slug 규칙

### 식별자 이중 구조

| 용도 | 필드 | 예시 |
|------|------|------|
| 외부 노출 (URL, API 응답) | `policy_code` (slug) | `WLF00004611` |
| 내부 FK 참조 | `policy_id` (bigint PK) | `1`, `42` |

### Slug 생성 규칙

- 공공데이터 API의 `servId` 값을 `policy_code`로 사용
- 형식: `WLF{8자리숫자}` (정부 API 원본 ID)
- API 경로: `GET /api/v1/policies/{policy_code}`
- 응답에는 `policy_id`(string 변환)와 `slug` 둘 다 포함 (API spec 4.2 호환)

**샘플 slug**:
- `WLF00000024`
- `WLF00000028`
- `WLF00000030`
- `WLF00000037`
- `WLF00000040`
- `WLF00001121`

---

## 2. 프론트 입력 필드 → 내부 변수 매핑

`SelectedConditions` (API spec 3.3) 기준.

| 프론트 필드 | 내부 변수명 | 타입 | DB 연관 |
|------------|------------|------|---------|
| `stage` | `target_stage` | `LifeStage` | `family_member.life_stage` |
| `childAge` | `child_age` | `ChildAge` | `family_member.birth_year`에서 파생 |
| `income` | `income_level` | `IncomeLevel` | `user_profile.income_bracket` |
| `region` | `region_code` | `RegionCode` | `user_profile.region_code` / `policy.region_code` |
| `special[]` | `special_conditions` | `SpecialCondition[]` | `policy_rule` (rule_type 매칭) |

---

## 3. LifeStage 허용 값

| 값 | 의미 | 정부 API 코드 |
|----|------|--------------|
| `pregnant` | 임신 중 | `007` |
| `newborn` | 신생아 (0~12개월) | `001` |
| `infant` | 영유아 (1~6세) | `001` |
| `child` | 아동 (7~12세) | `002` |
| `teen` | 청소년 (13~18세) | `003` |

---

## 4. ChildAge 허용 값

| 값 | 의미 |
|----|------|
| `preborn` | 태아 |
| `0` | 0세 |
| `1` | 1세 |
| `2-5` | 2~5세 |
| `6-12` | 6~12세 |
| `13+` | 13세 이상 |

---

## 5. IncomeLevel 허용 값

| 값 | 의미 | DB income_bracket |
|----|------|-------------------|
| `low` | 기준 중위소득 50% 이하 | `"50"` |
| `mid1` | 50~100% | `"100"` |
| `mid2` | 100~150% | `"150"` |
| `high` | 150% 초과 | `"200"` |
| `unknown` | 미입력 | `null` |

---

## 6. SpecialCondition 허용 값

| 값 | 의미 | 정부 API 코드 |
|----|------|--------------|
| `single` | 한부모·조손 가족 | `060` |
| `multi` | 다문화·탈북민 | `010` |
| `disabled` | 장애인 | `040` |
| `many` | 다자녀 가구 | `020` |
| `dual` | 저소득 | `050` |
| `veteran` | 보훈대상자 | `030` |

---

## 7. AI 상태 계층 구조

AI 기능(추천, 지원가능성 분석)은 상태를 3계층으로 분리한다.

### 계층 개요

| 계층 | Enum | 저장 위치 | 외부 노출 |
|------|------|-----------|-----------|
| 비동기 요청 생명주기 | `RequestStatus` | `recommendation_request.request_status` | O (API `status` 필드) |
| 내부 판단 결과 | `AssessmentStatus` | `policy_assessment.assessment_status` | X (내부 전용) |
| 사용자 노출 결과 | `UserStatus` | — | O (API `user_status` 필드) |

### RequestStatus — 비동기 요청 생명주기

추천 요청(`recommendation_request`) 및 지원가능성 요청의 처리 단계.

| 값 | 의미 |
|----|------|
| `PROCESSING` | 에이전트 실행 중 |
| `COMPLETED` | 정상 완료 |
| `FOLLOW_UP_REQUIRED` | 후속 질문 필요 (결과 확정 전) |
| `FAILED` | 처리 실패 |

### AssessmentStatus — 내부 판단 5상태

`policy_assessment.assessment_status` 컬럼에 저장. 사용자에게 직접 노출하지 않는다.

| 값 | 의미 |
|----|------|
| `LIKELY_MATCH` | 현재 정보 기준 추천 가능성 높음 |
| `NEEDS_MORE_INFO` | 추가 정보에 따라 정밀도가 달라질 수 있으나 방향은 유지 |
| `NOT_MATCH` | 핵심 조건 불충족 |
| `INSUFFICIENT_PROFILE` | 핵심 필드 2개 이상 누락으로 신뢰도 낮음 |
| `CONFLICTING_PROFILE` | 입력 충돌로 정규화 결과가 불안정 |

### UserStatus — 사용자 노출 3상태

`AssessmentStatus`를 사용자에게 보여줄 수 있는 3단계로 변환한 값. API 응답 `user_status` 필드에 사용.

| 값 | 의미 | 매핑 대상 AssessmentStatus |
|----|------|---------------------------|
| `RECOMMENDABLE` | 추천 가능 | `LIKELY_MATCH` |
| `NEEDS_CONFIRMATION` | 추가 확인 필요 | `NEEDS_MORE_INFO`, `INSUFFICIENT_PROFILE`, `CONFLICTING_PROFILE` |
| `DIFFICULT_TO_RECOMMEND` | 추천 어려움 | `NOT_MATCH` |

### 구현 위치

- Enum 정의: `app/common/ai_status.py`
- `AssessmentStatus → UserStatus` 변환 함수: `map_assessment_to_user_status()`
- Graph 적용 위치: Recommendation Graph / Eligibility Graph의 `User Status Mapping Node`

---

## 8. RegionCode 허용 값

`policy.region_code`, `user_profile.region_code`, 필터 파라미터에 사용.

`national`, `seoul`, `busan`, `daegu`, `incheon`, `gwangju`, `daejeon`, `ulsan`, `sejong`,
`gyeonggi`, `gangwon`, `chungbuk`, `chungnam`, `jeonbuk`, `jeonnam`, `gyeongbuk`, `gyeongnam`, `jeju`

- 현재 적재된 정책 데이터는 중앙정부 전국 단위 정책으로 region 정보 없음
- 추후 지자체 정책 추가 시 해당 시도 코드 사용
