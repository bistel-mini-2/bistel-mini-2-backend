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

## 7. RegionCode 허용 값

`policy.region_code`, `user_profile.region_code`, 필터 파라미터에 사용.

`national`, `seoul`, `busan`, `daegu`, `incheon`, `gwangju`, `daejeon`, `ulsan`, `sejong`,
`gyeonggi`, `gangwon`, `chungbuk`, `chungnam`, `jeonbuk`, `jeonnam`, `gyeongbuk`, `gyeongnam`, `jeju`

- 현재 적재된 정책 데이터는 중앙정부 전국 단위 정책으로 region 정보 없음
- 추후 지자체 정책 추가 시 해당 시도 코드 사용
