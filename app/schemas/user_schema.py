from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserResponse(BaseModel):
    user_id: int
    email: EmailStr
    nickname: str
    role: str
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)
