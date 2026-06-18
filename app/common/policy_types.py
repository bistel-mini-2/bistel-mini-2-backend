from enum import StrEnum

# -----------------------------------------------
# 정책 DB 직접 필터용 (policy_raw_import 구조화 필드)
# lifeArray, trgterIndvdlArray 에 한글 값으로 저장됨
# -----------------------------------------------

class LifeStage(StrEnum):
    """SelectedConditions.stage -> policy_raw_import.lifeArray 필터"""
    PREGNANT = "pregnant"
    NEWBORN = "newborn"
    INFANT = "infant"
    CHILD = "child"
    TEEN = "teen"
    YOUNG_ADULT = "young_adult"
    MIDDLE_AGED = "middle_aged"
    ELDERLY = "elderly"


# DB 필터 시 영문값 -> 한글값 변환에 사용
LIFE_STAGE_TO_DB: dict[str, str] = {
    "pregnant": "임신 · 출산",
    "newborn": "영유아",
    "infant": "영유아",
    "child": "아동",
    "teen": "청소년",
    "young_adult": "청년",
    "middle_aged": "중장년",
    "elderly": "노년",
}


class SpecialCondition(StrEnum):
    """SelectedConditions.special[] -> policy_raw_import.trgterIndvdlArray 필터"""
    SINGLE_PARENT = "single"
    MULTICULTURAL = "multi"
    DISABILITY = "disabled"
    MULTI_CHILD = "many"
    LOW_INCOME = "dual"
    VETERAN = "veteran"


# DB 필터 시 영문값 -> 한글값 변환에 사용
SPECIAL_CONDITION_TO_DB: dict[str, str] = {
    "single": "한부모·조손",
    "multi": "다문화·탈북민",
    "disabled": "장애인",
    "many": "다자녀",
    "dual": "저소득",
    "veteran": "보훈대상자",
}


# -----------------------------------------------
# 유저 프로필 매칭용 (policy JSON에 구조화 필드 없음)
# income: slctCritCn 자유 텍스트에 기술됨 -> AI 매칭 또는 policy_rule 테이블로 처리
# region: 현재 적재 데이터(중앙정부 전국 정책)에 없음 -> 지자체 정책 추가 시 활성화
# -----------------------------------------------

class ChildAge(StrEnum):
    """SelectedConditions.childAge -> 유저 프로필 매칭용 (policy JSON 필드 없음)"""
    PREBORN = "preborn"
    AGE_0 = "0"
    AGE_1 = "1"
    AGE_2_5 = "2-5"
    AGE_6_12 = "6-12"
    AGE_13_PLUS = "13+"


class IncomeLevel(StrEnum):
    """SelectedConditions.income -> user_profile.income_bracket 매칭용
    정책 조건은 slctCritCn 자유 텍스트에 있어 직접 필터 불가
    """
    LOW = "low"
    MID1 = "mid1"
    MID2 = "mid2"
    HIGH = "high"
    UNKNOWN = "unknown"


# user_profile.income_bracket 저장값 (기준 중위소득 %)
INCOME_LEVEL_TO_DB: dict[str, str | None] = {
    "low": "50",
    "mid1": "100",
    "mid2": "150",
    "high": "200",
    "unknown": None,
}


class RegionCode(StrEnum):
    """SelectedConditions.region -> user_profile.region_code / policy.region_code
    현재 적재 정책(중앙정부 전국)에는 region 데이터 없음
    """
    NATIONAL = "national"
    SEOUL = "seoul"
    BUSAN = "busan"
    DAEGU = "daegu"
    INCHEON = "incheon"
    GWANGJU = "gwangju"
    DAEJEON = "daejeon"
    ULSAN = "ulsan"
    SEJONG = "sejong"
    GYEONGGI = "gyeonggi"
    GANGWON = "gangwon"
    CHUNGBUK = "chungbuk"
    CHUNGNAM = "chungnam"
    JEONBUK = "jeonbuk"
    JEONNAM = "jeonnam"
    GYEONGBUK = "gyeongbuk"
    GYEONGNAM = "gyeongnam"
    JEJU = "jeju"


class HouseholdType(StrEnum):
    """user_profile.household_type 허용 값"""
    SINGLE = "single"
    NUCLEAR = "nuclear"
    SINGLE_PARENT = "single_parent"
    MULTI_CHILD = "multi_child"
    EXTENDED = "extended"


# -----------------------------------------------
# 프론트 필드명 -> 내부 Python 변수명 (API spec 3.3 기준)
# -----------------------------------------------

POLICY_FILTER_ALIAS: dict[str, str] = {
    "stage": "target_stage",
    "childAge": "child_age",
    "income": "income_level",
    "region": "region_code",
    "special": "special_conditions",
}
