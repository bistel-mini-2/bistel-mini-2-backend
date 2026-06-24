from pydantic import BaseModel


class PolicyImportResponse(BaseModel):
    raw_count: int
    imported_policy_count: int
    imported_detail_count: int
    imported_required_document_count: int
    imported_policy_document_count: int
    imported_tag_count: int
    imported_policy_rule_count: int
    imported_checklist_template_count: int
    skipped_count: int
