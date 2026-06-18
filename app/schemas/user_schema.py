from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserPublicResponse(BaseModel):
    user_id: int = Field(examples=[1])
    email: EmailStr = Field(examples=["user@example.com"])
    nickname: str = Field(examples=["parent_user"])

    model_config = ConfigDict(from_attributes=True)


class UserResponse(UserPublicResponse):
    role: str = Field(examples=["USER"])
