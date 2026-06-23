# AI-Friendly API Specification

source:
  title: "API 명세서"
  notion_url: "https://app.notion.com/p/382b9912bc6280198f69ea55e6e0d915"
  source_database: "API 명세서 (통합본)"
  generated_at: "2026-06-18"
  purpose: "AI agents and developers should use this file as the compact contract map for frontend, backend, and internal service implementation."

## 1. Contract Principles

- `external_api` means public REST contracts called directly by the frontend.
- `internal_api` means service or graph contracts inside the same FastAPI server.
- Recommendation and eligibility analysis use `request_id` based asynchronous flow by default.
- Internal eligibility decisions are reduced to 3 user-facing states.
- Follow-up questions are allowed only when the answer is likely to change the result, and should be limited to at most 2 questions.
- Policy AI summary is a shared explanation-generation feature: policy detail renders it as cards, while chat renders the same engine output as conversational text.

## 2. Global Response Shape

```ts
type ApiResponse<T> = {
  success: boolean;
  data: T | null;
  error: null | {
    code: string;
    message: string;
    details?: unknown;
  };
  meta: Record<string, unknown>;
};
```

## 3. Shared Domain Rules

### 3.1 Policy Identifier

```yaml
identifier_contract:
  accepted_input: "policy_id or slug string"
  example: "parent-allowance"
  rule: "APIs must expose at least one stable string identifier through policy_id or slug."
  reason: "The current frontend dummy data and routing use string slugs instead of numeric ids."
```

### 3.2 AI Status Contracts

```ts
type RequestStatus =
  | "READY"
  | "PROCESSING"
  | "COMPLETED"
  | "FOLLOW_UP_REQUIRED"
  | "FAILED";

// Backend internal assessment state. Do not use this directly as a user-facing UI label.
type AssessmentStatus =
  | "LIKELY_MATCH"
  | "NEEDS_MORE_INFO"
  | "NOT_MATCH"
  | "INSUFFICIENT_PROFILE"
  | "CONFLICTING_PROFILE";

type UserStatus =
  | "RECOMMENDABLE"
  | "NEEDS_CONFIRMATION"
  | "DIFFICULT_TO_RECOMMEND";
```

- `status` is a `RequestStatus` and controls request lifecycle UI.
- `user_status` is a `UserStatus` and controls user-facing recommendation/eligibility judgment UI.
- `AssessmentStatus` is for backend internal judgment only. If it appears in a response, frontend screens still prefer `user_status`.
- `loading`, `success`, `warning`, and `error` are frontend UI variants, not API status values. Exception: recommendation polling `GET /api/v1/recommendations/requests/{request_id}` returns `loading | done | error` as its frontend-facing polling status while DB `request_status` remains `RequestStatus`.
- Condition Agent, Policy Assessment Agent, and Graph implementations import shared input/output contracts from `app.schemas.ai_contract`; enum values must stay identical to this section.
- Condition Agent uses `ConditionInput` and `ConditionResult`; request lifecycle rows are stored in `recommendation_request` or `eligibility_request`, and `search_policy_chunks` returns `EvidenceChunk[]`.

### 3.3 Frontend Condition Input

```ts
type SelectedConditions = {
  stage?: "pregnant" | "newborn" | "infant" | "child" | "teen" | string;
  childAge?: "preborn" | "0" | "1" | "2-5" | "6-12" | "13+" | string;
  income?: "low" | "mid1" | "mid2" | "high" | "unknown" | string;
  region?: "seoul" | "gyeonggi" | "metro" | "etc" | string;
  special?: Array<"single" | "multi" | "disabled" | "many" | "dual" | string>;
};

type Evidence = {
  snippet: string;
  source_title: string;
  source_url: string;
  evidence_role: string;
};

type PolicyAiSummary = {
  policy_id: string;
  slug: string;
  easy_summary: string;
  key_points: Array<{ label: string; content: string }>;
  target_summary: string;
  benefit_summary: string;
  application_summary: string;
  cautions: string[];
  next_actions: Array<{
    action: "eligibility" | "compare" | "apply" | "chat";
    label: string;
    href?: string;
  }>;
  evidences: Evidence[];
  generated_at: string;
  summary_version: string;
};

type PolicyAiSummaryResponse = {
  request_id?: string;
  status: RequestStatus;
  summary: PolicyAiSummary | null;
};
```

## 4. External REST APIs

### 4.1 Auth and User Profile

```yaml
- id: auth_login
  name: "로그인"
  method: POST
  path: "/api/v1/auth/login"
  auth: "none"
  priority: "high"
  owner: "추천/회원"
  request: "email, password"
  response: "access token 또는 세션, user summary"
  notes: "이후 내 정보/추천/챗/마이페이지 접근에 사용"

- id: auth_signup
  name: "회원가입"
  method: POST
  path: "/api/v1/auth/signup"
  auth: "none"
  priority: "medium"
  owner: "추천/회원"
  request: "email, password, nickname"
  response: "access_token, token_type, user summary"
  notes: "일반 회원가입 진입점. 가입 성공 시 바로 로그인된 상태로 access token을 발급한다. 회원가입 온보딩에서 입력한 가족 상황은 이 토큰으로 인증한 뒤 PUT /api/v1/family-profiles/me로 저장한다."

- id: auth_signup_validate
  name: "회원가입 사전 검증"
  method: POST
  path: "/api/v1/auth/signup/validate"
  auth: "none"
  priority: "medium"
  owner: "추천/회원"
  request: "email, password, nickname"
  response_schema:
    success: true
    data:
      valid: true
    error: null
    meta: {}
  error_codes:
    - EMAIL_ALREADY_EXISTS
    - NICKNAME_ALREADY_EXISTS
    - VALIDATION_ERROR
  notes: "계정을 생성하지 않고 회원가입 입력값과 이메일/닉네임 중복 여부를 검증한다. 회원가입 온보딩의 '가족 상황 입력하기' 단계 전환 전에 호출하며, 실제 계정 생성과 access token 발급은 POST /api/v1/auth/signup에서만 수행한다."

- id: users_me
  name: "내 계정 정보 조회"
  method: GET
  path: "/api/v1/users/me"
  auth: "required"
  priority: "medium"
  owner: "추천/회원"
  request: "none"
  response: "user_id, email, nickname, role"
  notes: "헤더와 인증 사용자 식별용 계정 요약. 가족 상황, 추천 조건, 가족 구성원 정보는 포함하지 않는다."

- id: users_me_update
  name: "내 계정 정보 수정"
  method: PATCH
  path: "/api/v1/users/me"
  auth: "required"
  priority: "medium"
  owner: "추천/회원"
  request_schema:
    nickname: "string"
  response: "user_id, email, nickname, role"
  notes: "현재는 닉네임 변경만 지원한다. 이메일 변경은 지원하지 않는다."

- id: users_me_password_update
  name: "내 비밀번호 변경"
  method: PUT
  path: "/api/v1/users/me/password"
  auth: "required"
  priority: "medium"
  owner: "추천/회원"
  request_schema:
    current_password: "string"
    new_password: "string"
  response_schema:
    success: true
    data:
      changed: true
    error: null
    meta: {}
  error_codes:
    - INVALID_CREDENTIALS
    - PASSWORD_UNCHANGED
    - VALIDATION_ERROR
  notes: "현재 비밀번호를 확인한 뒤 새 비밀번호를 bcrypt 해시로 저장한다. 새 비밀번호는 현재 비밀번호와 달라야 하며, 이메일 변경은 지원하지 않는다."

- id: family_profile_me_get
  name: "내 가족 프로필 조회"
  method: GET
  path: "/api/v1/family-profiles/me"
  auth: "required"
  priority: "high"
  owner: "추천/회원"
  request: "none"
  response_schema:
    success: true
    data:
      family_profile:
        stage: "pregnant | newborn | infant | child | teen"
        childAge: "preborn | 0 | 1 | 2-5 | 6-12 | 13+"
        income: "low | mid1 | mid2 | high | unknown"
        region: "seoul | busan | daegu | incheon | gwangju | daejeon | ulsan | sejong | gyeonggi | gangwon | chungbuk | chungnam | jeonbuk | jeonnam | gyeongbuk | gyeongnam | jeju"
        special: "Array<single | multi | disabled | many | dual>"
        updated_at: "datetime?"
    error: null
    meta: {}
  notes: "마이페이지 가족 프로필과 추천/지원가능성 분석의 기본 조건. users_me와 분리된 도메인 프로필 API다. 저장된 가족 프로필이 없으면 data.family_profile은 null이다."

- id: family_profile_me_update
  name: "내 가족 프로필 저장"
  method: PUT
  path: "/api/v1/family-profiles/me"
  auth: "required"
  priority: "high"
  owner: "추천/회원"
  request_schema:
    stage: "pregnant | newborn | infant | child | teen"
    childAge: "preborn | 0 | 1 | 2-5 | 6-12 | 13+"
    income: "low | mid1 | mid2 | high | unknown"
    region: "seoul | busan | daegu | incheon | gwangju | daejeon | ulsan | sejong | gyeonggi | gangwon | chungbuk | chungnam | jeonbuk | jeonnam | gyeongbuk | gyeongnam | jeju"
    special: "Array<single | multi | disabled | many | dual>"
  response_schema:
    success: true
    data:
      family_profile:
        stage: "string"
        childAge: "string"
        income: "string"
        region: "string"
        special: "string[]"
        updated_at: "datetime?"
    error: null
    meta: {}
  notes: "회원가입 온보딩, 마이페이지 가족 프로필 저장, 추천 조건 기본값 저장에 사용한다. 백엔드는 필요하면 내부 DB 필드(region_code, income_bracket, family_members 등)로 변환한다."
```

### 4.2 Policy Search and Detail

```yaml
- id: policies_list
  name: "정책 목록 조회"
  method: GET
  path: "/api/v1/policies"
  auth: "optional"
  priority: "high"
  owner: "탐색/상세"
  query_params:
    query: "string?; 정책명·카테고리·상세 내용·태그 검색"
    q: "string?; query의 이슈 #29 호환 별칭. 둘 다 있으면 query 우선"
    category: "string?; main_category 단일 필터"
    tags: "string[]?; 반복 파라미터 또는 쉼표 구분, 여러 태그는 AND 조건"
    region_code: "RegionCode?; national은 전국 정책"
    region: "RegionCode?; region_code의 이슈 #29 호환 별칭"
    stage: "LifeStage?; policy_tag의 생애주기 태그 필터"
    sort: "updated_at | name | category | relevance; 기본값 updated_at"
    page: "integer >= 1; 기본값 1"
    size: "integer 1..100; 기본값 20"
  response_schema:
    data:
      - policy_id: "string; 내부 bigint PK의 문자열 표현"
        slug: "string; 외부 URL/API 식별자인 policy_code"
        name: "string"
        category: "string?"
        sub_category: "string?"
        tags: "string[]"
        target_stage: "LifeStage[]"
        summary: "string?"
        benefit_summary: "string?"
        agency: "string?"
        benefit_type: "string?"
        application_status: "string?"
        application_start_date: "date?"
        application_end_date: "date?"
        deadline: "date?; application_end_date 호환 필드"
        application_period_text: "string?"
        region_scope: "string?"
        region_code: "string?"
        region: "string?; national 또는 region_code 호환 필드"
        official_url: "string?"
    meta:
      page: "integer"
      size: "integer"
      total: "integer"
      total_pages: "integer"
  notes: "검색·카테고리·태그·지역 필터와 정렬을 이 목록 API에 통합한다. 키워드 검색은 PostgreSQL ILIKE를 사용하고 policy.policy_name 및 policy.main_category에는 pg_trgm GIN 인덱스를 적용한다. region_code와 LOWER(policy_tag.tag_name)에도 검색 인덱스를 적용한다. 목록 배열은 전역 ApiResponse 규격에 따라 최상위 items가 아니라 data에 담는다. relevance는 query가 있을 때만 관련도순으로 동작한다. 신청 기간과 신청 상태는 응답 표시용이며 목록 필터에는 사용하지 않는다."

- id: policies_detail
  name: "정책 상세 조회"
  method: GET
  path: "/api/v1/policies/{policy_id}"
  auth: "optional"
  priority: "high"
  owner: "탐색/상세"
  path_params:
    - policy_id
  response_schema:
    success: true
    data:
      policy_id: "string"
      slug: "string"
      name: "string"
      icon: "string?"
      category: "string"
      tag: "string"
      tagTone: "string"
      summary: "string"
      amount: "string"
      period: "string"
      agency: "string"
      type: "string"
      voucher: "string"
      region: "string"
      status: "string"
      contact: "string"
      official_url: "string"
      easy_summary: "string"
      detail:
        target: "string"
        content: "string"
        method: "string"
        periodText: "string"
        documents: "string[]"
        cautions: "string[]"
      related:
        - policy_id: "string"
          slug: "string"
          name: "string"
          tag: "string"
          tagTone: "string"
          icon: "string?"
    error: null
    meta: {}
  notes: "정책 상세, 비교, 가능성 분석, 신청 준비, 챗 추천 카드가 공통으로 참조하는 핵심 정책 응답. official_url은 frontend dummy의 url과 동일 역할."

- id: policy_ai_summary_get
  name: "정책 AI 요약 조회"
  method: GET
  path: "/api/v1/policies/{policy_id}/ai-summary"
  auth: "optional"
  priority: "high"
  owner: "탐색/상세"
  path_params:
    - policy_id
  query_params:
    - audience
    - include_evidences
  response_schema:
    data:
      request_id: "string?"
      status: "READY | PROCESSING | COMPLETED | FOLLOW_UP_REQUIRED | FAILED"
      summary: "PolicyAiSummary | null"
      summary_schema_when_completed:
        policy_id: "string"
        slug: "string"
        easy_summary: "string"
        key_points:
          - label: "string"
            content: "string"
        target_summary: "string"
        benefit_summary: "string"
        application_summary: "string"
        cautions: "string[]"
        next_actions:
          - action: "eligibility | compare | apply | chat"
            label: "string"
            href: "string?"
        evidences:
          - snippet: "string"
            source_title: "string"
            source_url: "string"
            evidence_role: "summary | target | benefit | application | caution"
        generated_at: "datetime"
        summary_version: "string"
    meta:
      source: "cache | generated"
  notes: "status는 RequestStatus이며 프론트는 getRequestStatusUi(status)로 로딩/완료/오류 UI를 표시한다. COMPLETED이면 summary를 렌더링하고, PROCESSING이면 로딩 UI, FAILED이면 에러 UI를 표시한다. 정책 AI 요약은 사용자 추천 판단이 아니므로 user_status를 사용하지 않는다."

- id: policy_ai_summary_create
  name: "정책 AI 요약 생성/갱신"
  method: POST
  path: "/api/v1/policies/{policy_id}/ai-summary"
  auth: "required"
  priority: "high"
  owner: "탐색/상세"
  path_params:
    - policy_id
  body:
    force_refresh: "boolean?"
    audience: "general | parent | pregnant | low_income | custom?"
    user_context: "SelectedConditions?"
  response_schema:
    data:
      policy_id: "string"
      status: "READY | PROCESSING | COMPLETED | FOLLOW_UP_REQUIRED | FAILED"
      summary: "PolicyAiSummary?"
      request_id: "string?"
    meta:
      source: "cache | generated"
  notes: "status는 RequestStatus이며 API 값으로 loading/done/error를 내려주지 않는다. 프론트는 getRequestStatusUi(status)를 사용한다. 캐시가 있으면 COMPLETED와 summary를 즉시 반환하고, 생성 비용이 크면 PROCESSING과 request_id를 반환한다. 정책 AI 요약은 사용자 추천 판단이 아니므로 user_status를 사용하지 않는다."

- id: policy_related_list
  name: "관련 정책 조회"
  method: GET
  path: "/api/v1/policies/{policy_id}/related"
  auth: "optional"
  priority: "low"
  owner: "탐색/상세"
  path_params:
    - policy_id
  query_params:
    - limit
  response_schema:
    data:
      items:
        - policy_id: "string"
          slug: "string"
          name: "string"
          summary: "string"
          tag: "string"
          tagTone: "string"
          relation_reason: "string"
  notes: "기능명세서의 관련 정책 기능. 정책 상세 응답의 related에 포함해도 되지만 독립 조회 API가 있으면 캐시/지연 로딩에 유리하다."
```

### 4.3 Favorites

```yaml
- id: favorites_list
  name: "관심 정책 목록 조회"
  method: GET
  path: "/api/v1/users/me/favorites"
  auth: "required"
  priority: "medium"
  owner: "추천/회원"
  query_params:
    page: "integer >= 1; default 1"
    size: "integer 1..100; default 20"
  response: "items[{ policy_id, policy_slug, policy_name, category, region, saved_at }] and pagination meta"
  storage: "user_favorites(user_id, policy_id, saved_at)"
  notes: "policy_slug는 상세 이동과 DELETE 요청에 사용하는 호환 필드"

- id: favorites_add
  name: "관심 정책 추가"
  method: POST
  path: "/api/v1/favorites/{policy_slug}"
  auth: "required"
  priority: "medium"
  owner: "추천/회원"
  path_params:
    - policy_slug
  response: "favorite created; duplicate returns 409"
  notes: "정책 카드/상세에서 저장"

- id: favorites_remove
  name: "관심 정책 제거"
  method: DELETE
  path: "/api/v1/favorites/{policy_slug}"
  auth: "required"
  priority: "medium"
  owner: "추천/회원"
  path_params:
    - policy_slug
  response: "favorite removed; missing favorite returns 404"
  notes: "저장 해제"
```

### 4.4 Recommendation

```yaml
- id: recommendation_request_create
  name: "추천 요청 생성"
  method: POST
  path: "/api/v1/recommendations/requests"
  auth: "required"
  priority: "high"
  owner: "추천/회원"
  body:
    raw_query: "string?"
    selected_conditions: "SelectedConditions?"
    source_type: "FORM | CHAT | POLICY_DETAIL | COMPARE?"
    source_ref_id: "string?"
  validation:
    - "raw_query 또는 selected_conditions 중 최소 1개 필수"
  response:
    http_status: 202
    data:
      request_id: "string"
      status: "PROCESSING"
    meta:
      request_id: "string"
      follow_up_required: false
  notes: "서버 내부에서 stage, childAge, income, region, special[]을 정규화 모델로 변환 후 recommendation_request 생성. source_ref_id는 FK가 아닌 느슨한 출처 식별자이며 예시는 FORM=recommendation_form, CHAT=chat_message:{id}, POLICY_DETAIL={policy_code}."

- id: recommendation_request_get
  name: "추천 결과 조회"
  method: GET
  path: "/api/v1/recommendations/requests/{request_id}"
  auth: "required"
  priority: "high"
  owner: "추천/회원"
  path_params:
    - request_id
  query_params:
    - include_evidences
    - include_questions
  response_schema:
    success: true
    data:
      request_id: "string"
      status: "loading | done | error"
      results:
        - policy_id: "string"
          slug: "string"
          policy_name: "string"
          summary: "string"
          benefit_summary: "string"
          reason_summary: "string"
          match_score: "number?"
          evidence:
            - chunk_id: "string | number"
              policy_id: "string | number"
              snippet: "string"
              source_title: "string"
              source_url: "string"
              score: "number?"
              evidence_role: "string?"
          follow_up_questions: []
      recommendations: "results와 동일한 호환 필드"
      follow_up_questions:
        - field_name: "string"
          question_text: "string"
          reason: "string?"
          priority: "number"
      error_message: "string?"
    meta:
      request_id: "string"
      follow_up_required: "boolean"
  notes: "GET은 ConditionAgent, RecommendationGraphRunner, LLM, PolicyAssessment, RAG 검색을 실행하지 않고 저장된 request_status/result_json만 읽는다. READY/PROCESSING은 loading, COMPLETED/FOLLOW_UP_REQUIRED는 done, FAILED는 error로 변환한다. COMPLETED이면 result_json.results -> result_json.recommendations 순서로 fallback하고, FOLLOW_UP_REQUIRED이면 parsed_query_json.questions를 최대 2개 반환한다."

- id: recommendation_save
  name: "추천 결과 저장"
  method: POST
  path: "/api/v1/recommendations/requests/{request_id}/save"
  auth: "required"
  priority: "high"
  owner: "추천/회원"
  path_params:
    - request_id
  response:
    data:
      saved_recommendation_id: "string"
      request_id: "string"
      saved_at: "datetime"
  notes: "추천 결과 요약, 추천 정책 id 목록, 입력 요약을 이력으로 보존하는 방향 권장"

- id: recommendation_history_list
  name: "추천 이력 목록 조회"
  method: GET
  path: "/api/v1/mypage/recommendations"
  auth: "required"
  priority: "high"
  owner: "추천/회원"
  query_params:
    - page
    - size
  response_schema:
    data:
      items:
        - saved_recommendation_id: "string"
          request_id: "string"
          saved_at: "datetime"
          title: "string"
          note: "string?"
          recommendation_count: "number"
          top_policies:
            - policy_id: "string"
              slug: "string"
              policy_name: "string"
      pagination: "Pagination"
  notes: "마이페이지 추천 이력 탭의 date, note, count 렌더링 고려"
```

### 4.5 Eligibility Analysis

```yaml
- id: eligibility_request_create
  name: "지원가능성 분석 요청 생성"
  method: POST
  path: "/api/v1/eligibility/requests"
  auth: "required"
  priority: "high"
  owner: "판단/비교"
  body:
    policy_id: "string"
    raw_query: "string?"
    selected_conditions: "SelectedConditions?"
    source_type: "POLICY_DETAIL | CHAT | FORM?"
    source_ref_id: "string?"
  validation:
    - "policy_id 필수"
    - "raw_query 또는 selected_conditions 중 최소 1개 권장"
  response:
    http_status: 202
    data:
      request_id: "string"
      status: "PROCESSING"
    meta:
      request_id: "string"
      follow_up_required: false
  notes: "정책 단건 진입이므로 policy_id는 정책 상세 slug와 동일하게 받는 방향이 안전. 서버는 eligibility_request를 생성하고 RequestStatus 기반 폴링 상태를 저장한다. source_ref_id는 FK가 아닌 느슨한 출처 식별자다."

- id: eligibility_request_get
  name: "지원가능성 분석 결과 조회"
  method: GET
  path: "/api/v1/eligibility/requests/{request_id}"
  auth: "required"
  priority: "high"
  owner: "판단/비교"
  path_params:
    - request_id
  response_schema:
    data:
      request_id: "string"
      status: "READY | PROCESSING | COMPLETED | FOLLOW_UP_REQUIRED | FAILED"
      policy_id: "string"
      slug: "string"
      policy_name: "string"
      user_status: "RECOMMENDABLE | NEEDS_CONFIRMATION | DIFFICULT_TO_RECOMMEND?"
      banner_level: "high | mid | low?"
      summary: "string?"
      criteria:
        - label: "string"
          status: "ok | check | no"
          note: "string"
      matched_conditions: "string[]"
      missing_conditions: "string[]"
      conflicting_conditions: "string[]"
      manual_check_points: "string[]"
      evidences:
        - snippet: "string"
          source_title: "string"
          source_url: "string"
          evidence_role: "string"
      questions:
        - follow_up_id: "string"
          field_name: "string"
          question_text: "string"
          reason: "string"
      input_summary: "SelectedConditions?"
    meta:
      request_id: "string"
      follow_up_required: "boolean"
  notes: "EligibilityResult는 banner_level, summary, criteria[]가 있으면 바로 렌더링 가능"
```

### 4.6 Comparison

```yaml
- id: comparison_create
  name: "정책 비교"
  method: POST
  path: "/api/v1/comparisons"
  auth: "required"
  priority: "medium"
  owner: "판단/비교"
  body:
    policy_ids: "string[2]"
    raw_query: "string?"
    selected_conditions: "SelectedConditions?"
  validation:
    - "policy_ids는 정확히 2개"
  response_schema:
    data:
      comparison_id: "string"
      policies:
        - policy_id: "string"
          slug: "string"
          policy_name: "string"
          tag: "string"
          tagTone: "string"
          summary: "string"
          amount: "string"
          detail:
            target: "string"
            content: "string"
            method: "string"
      common_points: "string[]"
      differences: "string[]"
      selection_guide: "string"
      related_policies:
        - policy_id: "string"
          slug: "string"
          policy_name: "string"
      evidences:
        - policy_id: "string"
          snippet: "string"
          source_title: "string"
          source_url: "string"
          evidence_role: "string"
  notes: "비교 UI는 요약 카드용 summary, amount, tag, tagTone과 비교표용 detail.target/content/method가 필요"

- id: compare_history_list
  name: "비교 이력 목록 조회"
  method: GET
  path: "/api/v1/mypage/compare-history"
  auth: "required"
  priority: "medium"
  owner: "판단/비교"
  query_params:
    - page
    - size
  response_schema:
    data:
      items:
        - compare_history_id: "string"
          compared_at: "datetime"
          note: "string?"
          tag: "string?"
          policies:
            - policy_id: "string"
              slug: "string"
              policy_name: "string"
      pagination: "Pagination"
  notes: "마이페이지 비교 이력 탭은 비교한 두 정책 이름과 날짜, 메모/태그를 함께 사용"
```

### 4.7 Application Preparation and Checklist

```yaml
- id: application_preparation_create
  name: "신청 준비 저장"
  method: POST
  path: "/api/v1/policies/{slug}/apply"
  auth: "required"
  priority: "high"
  owner: "챗봇/신청"
  path_params:
    - slug
  response_schema:
    data:
      apply_id: "string"
      saved: true
      policy_id: "string (slug)"
      how_to_apply: "string?"
      apply_period: "string?"
      contact: "string?"
      official_url: "string?"
      checklist:
        - id: "string (template_item_id)"
          label: "string"
          done: "boolean"
      caution: "string?"
      progress_percent: "integer"
  notes: "DB 기반 신청 준비 저장 API. 사용자가 체크리스트 담기/저장하기를 누를 때 호출한다. POST는 idempotent — 기존 진행 상태 있으면 재사용, 없으면 user_policy_progress + user_policy_checklist_item 생성. checklist는 policy_checklist_template 기반 공식/기본 항목만 포함한다. AI가 사용자별 체크리스트 항목을 동적으로 생성하지 않는다. progress_percent는 동적 계산(캐시 미사용)."

- id: application_preparation_get
  name: "신청 준비 조회"
  method: GET
  path: "/api/v1/policies/{slug}/apply"
  auth: "required"
  priority: "medium"
  owner: "챗봇/신청"
  path_params:
    - slug
  response_schema:
    data:
      apply_id: "string | null"
      saved: "boolean"
      policy_id: "string (slug)"
      how_to_apply: "string?"
      apply_period: "string?"
      contact: "string?"
      official_url: "string?"
      checklist:
        - id: "string (template_item_id)"
          label: "string"
          done: "boolean"
      caution: "string?"
      progress_percent: "integer"
  notes: "신청 준비 화면 preview 조회 API. GET은 user_policy_progress 생성/upsert를 수행하지 않는다. 저장된 진행 상태가 없으면 apply_id=null, saved=false, 기본 체크리스트 done=false, progress_percent=0으로 반환한다. 저장된 진행 상태가 있으면 apply_id와 체크 상태를 함께 반환한다."

- id: mypage_progress_list
  name: "신청 진행 목록 조회"
  method: GET
  path: "/api/v1/users/me/apply-progress"
  auth: "required"
  priority: "medium"
  owner: "챗봇/신청"
  response_schema:
    data:
      - apply_id: "string"
        policy_id: "string (slug)"
        policy_name: "string"
        progress_percent: "integer"
        updated_at: "string (iso8601)"
  notes: "마이페이지 신청 진행 현황. progress_percent는 단일 쿼리 집계로 동적 계산. 정렬 updated_at DESC. progress_status는 응답 미포함(현재 미사용). #64에서 구현 예정."

- id: checklist_item_update
  name: "체크리스트 항목 수정"
  method: PATCH
  path: "/api/v1/apply/{apply_id}/checklist/{item_id}"
  auth: "required"
  priority: "medium"
  owner: "챗봇/신청"
  path_params:
    - apply_id
    - item_id
  body:
    done: "boolean"
  response_schema:
    data:
      item:
        id: "string (template_item_id)"
        label: "string"
        done: "boolean"
      progress_percent: "integer"
  notes: "item_id는 template_item_id. done set 의미(토글 아님). progress.updated_at도 갱신. progress_percent는 동적 계산. 다른 사용자의 apply_id 접근 시 404."
```

### 4.8 Chat

```yaml
- id: chat_session_create
  name: "채팅 세션 생성"
  method: POST
  path: "/api/v1/chat/sessions"
  auth: "required"
  priority: "medium"
  owner: "챗봇/신청"
  body:
    title: "string? (max 255)"
  response_schema:
    data:
      chat_session_id: "string"
      session_status: "string"
  notes: "새 상담 세션 시작. session_status는 default ACTIVE. title은 옵셔널이며 최대 255자."

- id: chat_session_list
  name: "채팅 세션 목록 조회"
  method: GET
  path: "/api/v1/chat/sessions"
  auth: "required"
  priority: "medium"
  owner: "챗봇/신청"
  response_schema:
    data:
      sessions:
        - chat_session_id: "string"
          title: "string?"
          session_status: "string"
          last_message_at: "string (iso8601)?"
          created_at: "string (iso8601)?"
          updated_at: "string (iso8601)?"
  notes: "본인 세션 목록. 정렬 last_message_at DESC NULLS LAST, created_at DESC. 페이지네이션 미사용(전체 반환)."

- id: chat_message_send
  name: "채팅 메시지 전송"
  method: POST
  path: "/api/v1/chat/sessions/{chat_session_id}/messages"
  auth: "required"
  priority: "high"
  owner: "챗봇/신청"
  path_params:
    - chat_session_id
  body:
    content: "string"
  response_schema:
    data:
      chat_session_id: "string"
      user_message_id: "string"
      assistant_message:
        chat_message_id: "string"
        content: "string"
        user_status: "UserStatus?"
        sources: "string[]"
        policies:
          - policy_id: "string"
            slug: "string"
            policy_name: "string"
            summary: "string?"
            tag: "string?"
            tagTone: "string?"
            action_type: "RECOMMENDED | COMPARED | ELIGIBILITY_TARGET | APPLY_TARGET"
        actions: "recommend | eligibility | compare | apply | chat []"
        evidences:
          - chunk_id: "string"
            snippet: "string"
            source_title: "string?"
            source_url: "string?"
            evidence_role: "summary | target | benefit | application | caution?"
        disclaimer: "boolean?"
    meta:
      chat_session_id: "string"
  notes: |
    supervisor routing 결과를 리치 답변 카드 구조로 변환해 응답. assistant_message 저장은 ChatService가 graph 종료 후 한 트랜잭션에서 chat_message + chat_message_policy + chat_message_evidence + chat_session 갱신을 함께 처리한다.
    policies[]는 chat_message_policy row로 정규화 저장하며, intent별 action_type 매핑은 다음과 같다:
      recommend → RECOMMENDED
      compare → COMPARED
      eligibility → ELIGIBILITY_TARGET
      apply → APPLY_TARGET
      policy_summary → (link 생성하지 않음. 거론된 정책은 evidence chunk → policy 역추적)
      unclear → (link 생성하지 않음)
    evidences[]는 chat_message_evidence row로 정규화 저장한다. evidence_role은 RAG가 분류한 경우에만 값이 있으며, DB는 대문자 SUMMARY|TARGET|BENEFIT|APPLICATION|CAUTION을 저장하고 응답에서는 소문자로 직렬화한다. chunk_id가 비어 있는 evidence는 저장하지 않는다.
    structured_json에는 supervisor_decision(intent, raw) 등 메시지 단위 메타만 남기고, policies/evidences는 정규화 테이블에서만 읽는다.

- id: chat_message_list
  name: "채팅 메시지 목록 조회"
  method: GET
  path: "/api/v1/chat/sessions/{chat_session_id}/messages"
  auth: "required"
  priority: "medium"
  owner: "챗봇/신청"
  path_params:
    - chat_session_id
  response_schema:
    data:
      chat_session_id: "string"
      messages:
        - chat_message_id: "string"
          role: "string"
          message_type: "string"
          content: "string?"
          sequence_no: "integer"
          created_at: "string (iso8601)?"
          user_status: "UserStatus?"
          sources: "string[]"
          actions: "recommend | eligibility | compare | apply | chat []"
          disclaimer: "boolean?"
          policies:
            - policy_id: "string"
              slug: "string"
              policy_name: "string"
              summary: "string?"
              tag: "string?"
              tagTone: "string?"
              action_type: "RECOMMENDED | COMPARED | ELIGIBILITY_TARGET | APPLY_TARGET"
          evidences:
            - chunk_id: "string"
              snippet: "string"
              source_title: "string?"
              source_url: "string?"
              evidence_role: "summary | target | benefit | application | caution?"
  notes: |
    대화 맥락 복원용. 정렬 sequence_no ASC. 본인 세션이 아니거나 존재하지 않는 chat_session_id 접근 시 404 NOT_FOUND.
    assistant 메시지의 policies / evidences는 chat_message_policy / chat_message_evidence를 join해 채운다. user/system 메시지에서는 빈 배열로 반환한다.
    policy_summary intent로 응답된 메시지는 chat_message_policy row가 없으므로 policies[]가 빈 배열이며, 거론된 정책은 evidences[].chunk_id → policy_document_chunk → policy_document → policy 역추적으로 조회한다.
```

## 5. Internal Service and Graph Contracts

```yaml
- id: recommendation_graph_run
  type: "graph contract"
  name: "RecommendationGraphRunner.run"
  category: "추천"
  owner: "추천/회원"
  request: "request_id, merged_condition_json, input_issues?, profile_conflict_json?"
  response: "result_json"
  notes: "외부 추천 요청 생성/조회 API 뒤에서 호출되는 유스케이스 계층. #77 request lifecycle을 재사용하며 ConditionAnalysis -> profile merge -> RecommendationGraphRunner(CandidateSearch -> RuleFilter -> CandidateSave -> PolicyAssessment -> AssessmentSave -> ResultBuild -> LlmRerank -> RerankSave -> FinalizeResult) -> result_json 저장 순서로 호출한다. LLM은 후보 목록 안에서만 순서/설명을 보강하며 실패 시 rule/assessment 기반 result_json으로 fallback한다."

- id: condition_analysis_service_analyze
  type: "service contract"
  name: "ConditionAnalysisService.analyze"
  category: "추천"
  owner: "추천/회원"
  request: "raw_query?, selected_conditions?"
  response_schema:
    parsed_query_json:
      region_code: "string?"
      income_bracket: "string?"
      life_stage: "string?"
      child_age_range: "string?"
      dual_income: "boolean?"
      household_type: "string?"
      special_flags: "string[]"
    input_issues:
      - field_name: "string"
        issue_type: "missing | ambiguous | invalid"
        message: "string"
        priority: "number"
    follow_up_candidates:
      - field_name: "string"
        question_text: "string"
        reason: "string"
  notes: "기능명세서의 AI 조건 분석. 자연어/필드 입력을 공통 조건 JSON으로 정규화하고, 내부 이상 상태는 그대로 사용자에게 노출하지 않는다."

- id: profile_condition_merge_service_merge
  type: "service contract"
  name: "ProfileConditionMergeService.merge"
  category: "추천"
  owner: "추천/회원"
  request: "user_id, parsed_query_json, saved_profile_json?"
  response_schema:
    merged_condition_json: "SelectedConditions normalized for rule engine"
    profile_conflict_json:
      - field_name: "string"
        saved_value: "unknown"
        current_value: "unknown"
        selected_value: "unknown"
        rule: "current_input_wins"
    missing_core_fields: "string[]"
  notes: "저장 프로필과 현재 입력을 병합한다. 충돌 시 현재 입력 우선 원칙을 적용하고, 후보 검색은 merged_condition_json 기준으로만 수행한다."

- id: recommendation_candidate_service_search
  type: "service contract"
  name: "RecommendationCandidateService.search_candidates / rule_filter_candidates / save_candidates"
  category: "추천"
  owner: "추천/회원"
  request: "request_id, merged_condition_json, limit"
  response_schema:
    candidates:
      - policy_id: "string"
        slug: "string"
        survived_reasons: "string[]"
        candidate_status: "CANDIDATE | UNCERTAIN | EXCLUDED"
        retrieval_score: "number"
        rerank_score: "null"
        filter_match_json: "object"
    excluded:
      - policy_id: "string"
        reason: "string"
  notes: "기능명세서의 정책 후보 검색. Candidate Search가 DB/RAG 기반 후보를 찾고 Rule Filter Node가 명확히 아닌 정책을 EXCLUDED로 분리한다. 애매한 조건은 UNCERTAIN으로 유지하며, 결과는 recommendation_candidate에 저장한다. LLM rerank 전까지 rerank_score는 null이다."

- id: eligibility_service_analyze
  type: "service contract"
  name: "EligibilityService.analyze"
  category: "지원가능성분석"
  owner: "판단/비교"
  request: "user_id, policy_id, raw_query?, selected_conditions?"
  response: "request_id, status, user_status, matched_conditions, missing_conditions, evidences"
  notes: "특정 정책 1건 정밀 분석 유스케이스"

- id: comparison_service_compare
  type: "service contract"
  name: "ComparisonService.compare"
  category: "비교"
  owner: "판단/비교"
  request: "user_id, policy_ids[2], raw_query?, selected_conditions?"
  response: "common_points, differences, selection_guide, evidences"
  notes: "V1은 동기 응답 기준"

- id: comparison_graph_run
  type: "graph contract"
  name: "ComparisonGraph.run"
  category: "비교"
  owner: "판단/비교"
  request: "user_id, policy_ids[2], raw_query?, selected_conditions?"
  response: "comparison_result, evidences, compare_history_id"
  notes: "Condition Agent를 거쳐 사용자 상황별 선택 가이드를 생성"

- id: chat_service_send_message
  type: "service contract"
  name: "ChatService.send_message"
  category: "챗봇"
  owner: "챗봇/신청"
  request: "chat_session_id, user_id, content"
  response: "assistant_message, linked_policies, evidences, user_status"
  notes: "채팅 메시지 저장 후 Supervisor Graph 실행"

- id: apply_preparation_service_create
  type: "service contract"
  name: "ApplyPreparationService.create"
  category: "신청"
  owner: "챗봇/신청"
  request: "user_id, policy_slug"
  response: "apply_id, saved, policy_id, how_to_apply, apply_period, contact, official_url, checklist, caution, progress_percent"
  notes: "신청 준비 저장 API 뒤에서 호출되는 DB 기반 유스케이스 계층. 추천 조건 입력은 /recommend 흐름이 담당하며, 신청 준비는 선택된 정책의 기본 체크리스트와 진행 상태를 조회/저장한다. GET preview는 progress를 생성하지 않고, POST 저장 시에만 user_policy_progress를 생성 또는 재사용한다. AI Graph를 실행하지 않는다."

- id: policy_summary_service_summarize
  type: "service contract"
  name: "PolicySummaryService.summarize"
  category: "정책탐색"
  owner: "탐색/상세"
  request: "policy_id, user_id?, user_context?, audience?, force_refresh?"
  response: "easy_summary, key_points, target_summary, benefit_summary, application_summary, cautions, next_actions, evidences, generated_at, summary_version"
  notes: "기능명세서의 정책 설명 생성 엔진. 정책 문서를 쉬운 설명으로 변환하는 로직 1개를 카드형 상세, 챗봇 대화 답변에서 함께 사용한다."

- id: policy_explanation_engine_generate
  type: "engine contract"
  name: "PolicyExplanationEngine.generate"
  category: "공통"
  owner: "탐색/상세"
  request: "policy_document, policy_chunks?, user_context?, render_mode: card | chat"
  response_schema:
    easy_summary: "string"
    key_points: "array"
    action_hints: "array"
    evidences: "array"
    rendered_text: "string?"
  notes: "정책 AI요약과 RAG 답변을 통합한 공통 엔진. 상세 화면은 card mode, 챗봇은 chat mode로 렌더링한다."

- id: rag_policy_chunk_search
  type: "tool contract"
  name: "search_policy_chunks"
  category: "공통"
  owner: "판단/비교"
  request: "query, policy_ids?, top_k?, evidence_role?"
  response_schema:
    chunks:
      - policy_id: "string"
        chunk_id: "string"
        snippet: "string"
        source_title: "string"
        source_url: "string"
        score: "number"
        evidence_role: "string"
  notes: "정책 chunk 의미 검색 + 근거 반환. 추천, 지원가능성분석, 비교, 챗봇 답변, AI 요약에서 공통으로 사용하는 근거 검색 모듈."
```

## 6. Internal UI/Response Contracts

```yaml
- id: policy_detail_response_contract
  name: "정책 상세 응답 필드 계약"
  type: "response contract"
  request: "policy_id"
  required_fields:
    - name
    - tag
    - tagTone
    - summary
    - amount
    - agency
    - type
    - voucher
    - region
    - status
    - contact
    - official_url
    - easy_summary
    - detail
    - related
    - ai_summary
    - next_actions
  used_by:
    - "/policies/[id]"
    - "/compare"
    - "/apply"
    - "/eligibility"
  notes: "정책 상세는 기본정보 + AI요약 + 다음 액션을 함께 보여준다. ai_summary는 별도 API로 지연 로딩하거나 정책 상세 응답에 포함할 수 있다."

- id: policy_ai_summary_response_contract
  name: "정책 AI 요약 응답 계약"
  type: "response contract"
  request: "policy_id, optional user context"
  required_fields:
    - status
    - summary
  summary_fields_when_completed:
    - easy_summary
    - key_points
    - target_summary
    - benefit_summary
    - application_summary
    - cautions
    - next_actions
    - evidences
    - generated_at
    - summary_version
  notes: "status는 RequestStatus이며 getRequestStatusUi(status)로 표시한다. COMPLETED일 때 summary 필드를 렌더링한다. 정책 AI 요약은 사용자 추천 판단이 아니므로 user_status를 사용하지 않는다."

- id: eligibility_ui_response_contract
  name: "지원 가능성 UI 응답 계약"
  type: "response contract"
  request: "assessment result"
  required_fields:
    - banner_level
    - summary
    - criteria_rows
    - action_hints
    - family_summary_source
  notes: "EligibilityResult는 criteria[]와 ok/check/no 표시 모델이 있으면 바로 붙기 쉬움"

- id: chat_ui_response_contract
  name: "채팅 응답 UI 계약"
  type: "response contract"
  request: "chat content"
  required_fields:
    - assistant_text
    - sources
    - policies
    - actions
    - disclaimer_flag
  notes: "front /chat은 리치 카드형 assistant payload를 전제로 구현됨"
```

## 7. Implementation Order for AI Agents

1. Implement common response wrapper and authentication boundary.
2. Implement policy identifier contract using stable string `policy_id`/`slug`.
3. Implement policy list/detail/search/filter because recommendation, comparison, eligibility, application prep, AI summary, and chat cards depend on these fields.
4. Implement `search_policy_chunks` so AI summary, eligibility, comparison, recommendation, and chat can return evidence-backed answers.
5. Implement policy AI summary APIs and `PolicyExplanationEngine.generate`.
6. Implement profile read/update, condition normalization, profile merge, and candidate search.
7. Implement async request lifecycle tables/services for recommendation and eligibility.
8. Implement recommendation create/get/save and recommendation history.
9. Implement eligibility create/get and convert internal states into `UserStatus` plus `banner_level`.
10. Implement comparison create and compare history.
11. Implement application preparation, progress, and checklist APIs.
12. Implement chat session/message APIs using supervisor routing output mapped to the chat UI payload.

## 8. Known Contract Gaps to Resolve During Implementation

- Exact authentication transport is not specified: token header, cookie session, or both.
- Pagination shape is referenced but not fully defined.
- Error code taxonomy is not defined.
- `policy_id` and `slug` may be duplicated fields; choose one canonical internal key and expose both only if frontend compatibility needs it.
- Async polling interval, timeout, and retry semantics are not specified.
- Follow-up answer submission endpoint is not explicitly listed; current design only exposes questions in result responses.
- AI summary cache policy is not specified: decide TTL, refresh trigger, summary_version format, and whether generation is sync or `request_id` based async.
- RAG chunk schema and indexing pipeline are not specified here, but `search_policy_chunks` is now a required shared internal tool contract.
- Admin APIs are categorized in the database schema but no concrete admin endpoint rows were found in the fetched source.
