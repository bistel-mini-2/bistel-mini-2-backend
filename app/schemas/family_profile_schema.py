from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FamilyStage(StrEnum):
    PREGNANT = "pregnant"
    NEWBORN = "newborn"
    INFANT = "infant"
    CHILD = "child"
    TEEN = "teen"


class FamilyChildAge(StrEnum):
    PREBORN = "preborn"
    AGE_0 = "0"
    AGE_1 = "1"
    AGE_2_5 = "2-5"
    AGE_6_12 = "6-12"
    AGE_13_PLUS = "13+"


class FamilyIncome(StrEnum):
    LOW = "low"
    MID1 = "mid1"
    MID2 = "mid2"
    HIGH = "high"
    UNKNOWN = "unknown"


class FamilyRegion(StrEnum):
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


class FamilySpecialCondition(StrEnum):
    SINGLE_PARENT = "single"
    MULTICULTURAL = "multi"
    DISABILITY = "disabled"
    MULTI_CHILD = "many"
    DUAL_INCOME = "dual"
    LOW_INCOME = "low_income"


class FamilyProfileRequest(BaseModel):
    stage: FamilyStage = Field(examples=["newborn"])
    child_age: FamilyChildAge = Field(alias="childAge", examples=["0"])
    income: FamilyIncome = Field(examples=["mid1"])
    region: FamilyRegion = Field(examples=["seoul"])
    special: list[FamilySpecialCondition] = Field(
        default_factory=list,
        examples=[["many"]],
    )

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "stage": "newborn",
                    "childAge": "0",
                    "income": "mid1",
                    "region": "seoul",
                    "special": ["many"],
                }
            ]
        },
    )

    @field_validator("special")
    @classmethod
    def deduplicate_special(
        cls,
        value: list[FamilySpecialCondition],
    ) -> list[FamilySpecialCondition]:
        deduplicated: list[FamilySpecialCondition] = []
        for item in value:
            if item not in deduplicated:
                deduplicated.append(item)
        return deduplicated


class FamilyProfileData(FamilyProfileRequest):
    updated_at: datetime | None = None


class FamilyProfileResponse(BaseModel):
    family_profile: FamilyProfileData | None
