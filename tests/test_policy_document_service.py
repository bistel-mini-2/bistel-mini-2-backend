from app.services.policy_document_service import PolicyDocumentService


def test_build_policy_detail_documents_uses_condition_profile_chunks():
    service = PolicyDocumentService()
    source = {
        "policy_id": 236,
        "condition_profile_id": 10,
        "policy_code": "WLF00006313",
        "policy_name": "농식품바우처",
        "official_url": "https://example.com/policy",
        "condition_json": {
            "policy_name": "농식품바우처",
            "condition_tree": {
                "operator": "AND",
                "conditions": [
                    {
                        "field": "income_status",
                        "operator": "EQ",
                        "value": "basic_livelihood_recipient",
                        "source_text": "생계급여 수급가구",
                    },
                    {
                        "operator": "OR",
                        "conditions": [
                            {
                                "field": "stage",
                                "operator": "EQ",
                                "value": "pregnant",
                                "source_text": "임산부",
                            },
                            {
                                "field": "household_member_age",
                                "operator": "LTE",
                                "value": 34,
                                "source_text": "34세 이하",
                            },
                        ],
                    },
                ],
            },
            "exclusions": [
                {
                    "field": "program_overlap",
                    "operator": "EQ",
                    "value": "nutrition_plus",
                    "source_text": "영양플러스 사업 이용자 제외",
                }
            ],
        },
        "target_summary": "생계급여 수급가구 중 임산부 또는 34세 이하 가구원 포함",
        "confidence": 0.95,
        "review_required": False,
        "quality_flags": [],
        "source_fields": ["target_description"],
        "condition_profile_updated_at": "2026-06-26T10:00:00",
        "source_text": "공식 지원대상 원문",
        "benefit_description": "농식품 구매 비용 지원",
        "application_method": "온라인 신청",
        "application_period_text": "상시",
        "caution": None,
    }

    raw_text, documents = service._build_policy_detail_documents(source)

    condition_document = next(
        document for document in documents if document.metadata["section"] == "조건 구조"
    )
    assert "조건 그룹: OR" in condition_document.page_content
    assert "field: stage" in condition_document.page_content
    assert "field: household_member_age" in condition_document.page_content
    assert condition_document.metadata["condition_profile_id"] == 10
    assert condition_document.metadata["source_basis"] == "policy_condition_profile"
    assert condition_document.metadata["condition_group"] == "condition_tree"
    assert condition_document.metadata["condition_operator"] == "AND"
    assert "공식 지원대상 원문" in raw_text


def test_split_policy_detail_documents_preserves_condition_groups():
    service = PolicyDocumentService()
    _, documents = service._build_policy_detail_documents(
        {
            "policy_id": 1,
            "condition_profile_id": 2,
            "policy_code": "WLF00000001",
            "policy_name": "테스트 정책",
            "official_url": None,
            "condition_json": {
                "condition_tree": {
                    "operator": "OR",
                    "conditions": [
                        {"field": "stage", "operator": "EQ", "value": "pregnant"},
                        {"field": "age", "operator": "LTE", "value": 34},
                    ],
                }
            },
            "target_summary": "테스트 요약",
            "confidence": None,
            "review_required": False,
            "quality_flags": [],
            "source_fields": [],
            "condition_profile_updated_at": None,
            "source_text": None,
            "target_description": None,
            "benefit_description": None,
            "application_method": None,
            "application_period_text": None,
            "caution": None,
        }
    )

    chunks = service.split_policy_detail_documents(documents)
    condition_chunks = [
        chunk for chunk in chunks if chunk.metadata["section"] == "조건 구조"
    ]

    assert len(condition_chunks) == 1
    assert "조건 그룹: OR" in condition_chunks[0].page_content
    assert "field: stage" in condition_chunks[0].page_content
    assert "field: age" in condition_chunks[0].page_content
