from pydantic import BaseModel


class PolicyRuleIngestItem(BaseModel):
    policy_id: int
    policy_code: str
    policy_name: str
    rule_count: int
    hard_rule_count: int
    exclusion_count: int
    manual_check_count: int


class PolicyRuleIngestSkipItem(BaseModel):
    policy_id: int
    policy_code: str
    policy_name: str
    reason: str


class PolicyRuleIngestResponse(BaseModel):
    requested_count: int
    completed_count: int
    skipped_count: int
    failed_count: int
    items: list[PolicyRuleIngestItem]
    skipped: list[PolicyRuleIngestSkipItem]
    failed: list[dict[str, str]]
