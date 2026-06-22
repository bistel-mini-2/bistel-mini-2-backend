from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class PolicyEligibilityRequestCreate(BaseModel):
    user_conditions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_user_conditions(self) -> "PolicyEligibilityRequestCreate":
        if not self.user_conditions:
            raise ValueError("user_conditions is required")
        return self


class PolicyEligibilityRequestResponse(BaseModel):
    request_id: str
    status: Literal["loading"]
