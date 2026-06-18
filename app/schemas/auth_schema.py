import string
from typing import Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)

from app.schemas.user_schema import UserResponse


class SignUpRequest(BaseModel):
    email: EmailStr = Field(max_length=255, examples=["user@example.com"])
    password: str = Field(
        min_length=8,
        max_length=72,
        examples=["Passw0rd!"],
    )
    nickname: str = Field(
        min_length=2,
        max_length=100,
        validation_alias=AliasChoices("nickname", "name"),
        examples=["parent_user"],
    )

    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "email": "user@example.com",
                    "password": "Passw0rd!",
                    "nickname": "parent_user",
                }
            ]
        },
    )

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("Password must not contain whitespace")
        if not any(character.isascii() and character.isalpha() for character in value):
            raise ValueError("Password must contain at least one English letter")
        if not any(character.isdigit() for character in value):
            raise ValueError("Password must contain at least one number")
        if not any(character in string.punctuation for character in value):
            raise ValueError("Password must contain at least one special character")
        return value

    @field_validator("nickname")
    @classmethod
    def validate_nickname(cls, value: str) -> str:
        nickname = value.strip()
        if len(nickname) < 2:
            raise ValueError("Nickname must be at least 2 characters")
        return nickname


class LoginRequest(BaseModel):
    email: EmailStr = Field(max_length=255, examples=["user@example.com"])
    password: str = Field(min_length=1, examples=["Passw0rd!"])

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "user@example.com",
                    "password": "Passw0rd!",
                }
            ]
        }
    )


class TokenResponse(BaseModel):
    access_token: str = Field(examples=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."])
    token_type: Literal["bearer"] = "bearer"
    user: UserResponse


class SignUpValidateResponse(BaseModel):
    valid: bool = Field(examples=[True])
