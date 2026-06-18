import string

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserPublicResponse(BaseModel):
    user_id: int = Field(examples=[1])
    email: EmailStr = Field(examples=["user@example.com"])
    nickname: str = Field(examples=["parent_user"])

    model_config = ConfigDict(from_attributes=True)


class UserResponse(UserPublicResponse):
    role: str = Field(examples=["USER"])


class UserNicknameUpdateRequest(BaseModel):
    nickname: str = Field(min_length=2, max_length=100, examples=["new_nickname"])

    @field_validator("nickname")
    @classmethod
    def validate_nickname(cls, value: str) -> str:
        nickname = value.strip()
        if len(nickname) < 2:
            raise ValueError("Nickname must be at least 2 characters")
        return nickname


class UserPasswordUpdateRequest(BaseModel):
    current_password: str = Field(min_length=1, examples=["Passw0rd!"])
    new_password: str = Field(
        min_length=8,
        max_length=72,
        examples=["NewPassw0rd!"],
    )

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("Password must not contain whitespace")
        if not any(character.isascii() and character.isalpha() for character in value):
            raise ValueError("Password must contain at least one English letter")
        if not any(character.isdigit() for character in value):
            raise ValueError("Password must contain at least one number")
        if not any(character in string.punctuation for character in value):
            raise ValueError("Password must contain at least one special character")
        return value


class UserPasswordUpdateResponse(BaseModel):
    changed: bool = Field(examples=[True])
