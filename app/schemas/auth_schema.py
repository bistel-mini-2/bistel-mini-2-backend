import string

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.user_schema import UserResponse


class SignUpRequest(BaseModel):
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=72)
    nickname: str = Field(min_length=2, max_length=100)

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
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


class SignUpResponse(BaseModel):
    user: UserResponse
    message: str
