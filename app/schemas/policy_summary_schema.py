from typing import Literal

from pydantic import BaseModel, Field


PolicySummaryStatus = Literal["loading", "done", "error"]


class PolicySummaryResponse(BaseModel):
    status: PolicySummaryStatus
    summary: str | None = None
    evidence: list[str] = Field(default_factory=list)

