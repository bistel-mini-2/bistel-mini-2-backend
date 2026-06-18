from enum import StrEnum


class LifeStage(StrEnum):
    """SelectedConditions.stage 허용 값 (API spec 3.3)"""
    PREGNANT = "pregnant"
    NEWBORN = "newborn"
    INFANT = "infant"
    CHILD = "child"
    TEEN = "teen"
    YOUNG_ADULT = "young_adult"
    MIDDLE_AGED = "middle_aged"
    ELDERLY = "elderly"


# API spec 영문값 → DB lifeArray 한글값
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


class ChildAge(StrEnum):
    """SelectedConditions.childAge 허용 값 (API spec 3.3)"""
    PREBORN = "preborn"
    AGE_0 = "0"
    AGE_1 = "1"
    AGE_2_5 = "2-5"
    AGE_6_12 = "6-12"
    AGE_13_PLUS = "13+"


class IncomeLevel(StrEnum):
    """SelectedConditions.income 허용 값 (API spec 3.3)"""
    LOW = "low"
    MID1 = "mid1"
    MID2 = "mid2"
    HIGH = "high"
    UNKNOWN = "unknown"


# API spec income 값 → DB income_bracket 저장값
INCOME_LEVEL_TO_DB: dict[str, str] = {
    "low": "50",
    "mid1": "100",
    "mid2": "150",
    "high": "200",
    "unknown": "",
}


class RegionCode(StrEnum):
    """SelectedConditions.region 및 policy.region_code 허용 값"""
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


class SpecialCondition(StrEnum):
    """SelectedConditions.special[] 허용 값 (API spec 3.3)"""
    SINGLE_PARENT = "single"
    MULTICULTURAL = "multi"
    DISABILITY = "disabled"
    MULTI_CHILD = "many"
    LOW_INCOME = "dual"


# API spec 영문값 → DB trgterIndvdlArray 한글값
SPECIAL_CONDITION_TO_DB: dict[str, str] = {
    "single": "한부모·조손",
    "multi": "다문화·탈북민",
    "disabled": "장애인",
    "many": "다자녀",
    "dual": "저소득",
}


class HouseholdType(StrEnum):
    """user_profile.household_type 허용 값"""
    SINGLE = "single"
    NUCLEAR = "nuclear"
    SINGLE_PARENT = "single_parent"
    MULTI_CHILD = "multi_child"
    EXTENDED = "extended"


# 프론트 필드명 → 내부 Python 변수명 (API spec 3.3 → Pydantic field)
POLICY_FILTER_ALIAS: dict[str, str] = {
    "stage": "target_stage",
    "childAge": "child_age",
    "income": "income_level",
    "region": "region_code",
    "special": "special_conditions",
}
