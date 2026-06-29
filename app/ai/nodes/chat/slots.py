from __future__ import annotations

from app.ai.states.chat_state import Intent


# 슬롯 필링: intent별 필수 조건. 비어 있으면 그 intent로 가기 전에 되묻는다.
# 추천만 게이팅한다. 너무 많은 intent를 막으면 흐름이 딱딱해진다.
REQUIRED_SLOTS: dict[Intent, tuple[str, ...]] = {
    "recommend": ("child_age",),
}

# 추천 입력 위저드의 스텝 순서. 필수(child_age) + 선택(income, special).
RECOMMEND_WIZARD_FIELDS: tuple[str, ...] = ("child_age", "income", "region", "special")

# 자녀 나이 -> 생애단계(stage) 파생. child_age는 직접 필터가 아니라서 stage로
# 변환해 policy_raw_import.lifeArray 필터를 살린다.
CHILD_AGE_TO_STAGE: dict[str, str] = {
    "preborn": "pregnant",
    "0": "infant",
    "1": "infant",
    "2-5": "infant",
    "6-12": "child",
    "13+": "teen",
}

SLOT_LABELS: dict[str, str] = {
    "stage": "생애단계",
    "child_age": "자녀 나이",
    "income": "소득",
    "region": "지역",
    "special": "가구 특성",
    "summary_target": "요약 대상",
}

SLOT_QUESTIONS: dict[str, str] = {
    "stage": "지금 생애단계가 어떻게 되세요?",
    "region": "어느 지역에 사세요?",
    "child_age": "자녀 나이가 어떻게 되나요?",
    "income": "소득 구간을 알려주실 수 있을까요?",
    "special": "해당하는 가구 특성이 있나요? (없으면 건너뛰기)",
    "summary_target": "무엇을 요약할까요?",
}

# 칩 옵션은 {label(표시), value(엔진 코드)} 쌍이다. value는 반드시
# app/common/policy_types.py의 enum 코드와 일치해야 한다.
SLOT_OPTIONS: dict[str, list[dict[str, str]]] = {
    "stage": [
        {"label": "임신·출산", "value": "pregnant"},
        {"label": "영유아", "value": "infant"},
        {"label": "아동", "value": "child"},
        {"label": "청소년", "value": "teen"},
    ],
    "child_age": [
        {"label": "태아", "value": "preborn"},
        {"label": "0세", "value": "0"},
        {"label": "1세", "value": "1"},
        {"label": "2~5세", "value": "2-5"},
        {"label": "6~12세", "value": "6-12"},
        {"label": "13세 이상", "value": "13+"},
    ],
    "income": [
        {"label": "중위 50% 이하", "value": "low"},
        {"label": "중위 100% 이하", "value": "mid1"},
        {"label": "중위 150% 이하", "value": "mid2"},
        {"label": "중위 150% 초과", "value": "high"},
        {"label": "모름", "value": "unknown"},
    ],
    "region": [
        {"label": "전국", "value": "national"},
        {"label": "서울", "value": "seoul"},
        {"label": "경기", "value": "gyeonggi"},
        {"label": "인천", "value": "incheon"},
        {"label": "부산", "value": "busan"},
        {"label": "대구", "value": "daegu"},
        {"label": "대전", "value": "daejeon"},
        {"label": "광주", "value": "gwangju"},
        {"label": "울산", "value": "ulsan"},
        {"label": "세종", "value": "sejong"},
        {"label": "강원", "value": "gangwon"},
        {"label": "충북", "value": "chungbuk"},
        {"label": "충남", "value": "chungnam"},
        {"label": "전북", "value": "jeonbuk"},
        {"label": "전남", "value": "jeonnam"},
        {"label": "경북", "value": "gyeongbuk"},
        {"label": "경남", "value": "gyeongnam"},
        {"label": "제주", "value": "jeju"},
    ],
    "special": [
        {"label": "한부모", "value": "single"},
        {"label": "다문화", "value": "multi"},
        {"label": "장애", "value": "disabled"},
        {"label": "다자녀", "value": "many"},
        {"label": "맞벌이", "value": "dual"},
        {"label": "저소득", "value": "low_income"},
        {"label": "국가유공", "value": "veteran"},
    ],
    "summary_target": [
        {"label": "이 정책", "value": "policy"},
        {"label": "방금 추천 결과", "value": "recommendation_result"},
        {"label": "지원가능성 분석 결과", "value": "eligibility_result"},
    ],
}

PROFILE_LABELS = {
    "stage": "가족 구성",
    "child_age": "자녀 연령대",
    "income": "가구 소득",
    "region": "거주 지역",
    "special": "특수 상황",
}

PROFILE_OPTION_LABELS: dict[str, dict[str, str]] = {
    "stage": {
        "pregnant": "임신 준비·임신 중",
        "newborn": "출산 직후·신생아",
        "infant": "영유아",
        "child": "아동",
        "teen": "청소년",
    },
    "child_age": {
        "preborn": "출생 전",
        "0": "0세 (12개월 미만)",
        "1": "1세",
        "2-5": "2~5세",
        "6-12": "6~12세",
        "13+": "13세 이상",
    },
    "income": {
        "low": "중위소득 50% 이하",
        "mid1": "중위소득 51~100%",
        "mid2": "중위소득 101~150%",
        "high": "중위소득 150% 초과",
        "unknown": "잘 모르겠어요",
    },
    "region": {
        "national": "전국",
        "seoul": "서울",
        "busan": "부산",
        "daegu": "대구",
        "incheon": "인천",
        "gwangju": "광주",
        "daejeon": "대전",
        "ulsan": "울산",
        "sejong": "세종",
        "gyeonggi": "경기",
        "gangwon": "강원",
        "chungbuk": "충북",
        "chungnam": "충남",
        "jeonbuk": "전북",
        "jeonnam": "전남",
        "gyeongbuk": "경북",
        "gyeongnam": "경남",
        "jeju": "제주",
    },
    "special": {
        "single": "한부모·조손 가정",
        "multi": "다문화·탈북민 가정",
        "disabled": "장애인 가구",
        "many": "다자녀 가정(2명 이상)",
        "dual": "맞벌이 가구",
        "low_income": "저소득 가구",
        "single_parent": "한부모·조손 가정",
        "multi_child": "다자녀 가정(2명 이상)",
        "veteran": "국가유공",
    },
}

INCOME_BRACKET_TO_PROFILE_CODE = {
    "50": "low",
    "100": "mid1",
    "150": "mid2",
    "999": "high",
}

CONFIRM_OPTIONS = [
    {"label": "네, 이대로", "value": "yes"},
    {"label": "아니요, 다시 입력", "value": "no"},
]
CONFIRM_NO_HINTS = ("아니", "아뇨", "다시", "새로", "바꿔", "변경", "수정", "다른", "no")
CONFIRM_YES_HINTS = (
    "네",
    "예",
    "응",
    "그래",
    "좋아",
    "이대로",
    "진행",
    "맞아",
    "ㅇㅇ",
    "yes",
    "그걸로",
)
STAGE_CODE_TO_KO = {
    "pregnant": "임신·출산",
    "newborn": "영유아",
    "infant": "영유아",
    "child": "아동",
    "teen": "청소년",
}
