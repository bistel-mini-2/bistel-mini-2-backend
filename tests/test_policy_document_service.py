from app.services.policy_document_service import PolicyDocumentService
from app.services.policy_document_service import PdfExtractionResult


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


def test_extract_pdf_text_uses_pypdfium2_when_quality_is_good(monkeypatch):
    service = PolicyDocumentService()

    monkeypatch.setattr(
        service,
        "_extract_pdf_text_with_pypdfium2",
        lambda content: PdfExtractionResult(
            method="pypdfium2",
            text="정상 텍스트\n1. 지원 대상\n내용",
            quality_score=120,
            metrics={
                "text_length": 1200,
                "replacement_count": 0,
                "space_ratio": 0.1,
                "spaced_hangul_runs": 0,
            },
        ),
    )
    monkeypatch.setattr(
        service,
        "_extract_pdf_text_with_pymupdf",
        lambda content: (_ for _ in ()).throw(
            AssertionError("정상 pypdfium2 결과에서는 fallback을 호출하면 안 됩니다.")
        ),
    )

    result = service._extract_pdf_text(b"%PDF")

    assert result.method == "pypdfium2"
    assert result.text.startswith("정상 텍스트")


def test_extract_pdf_text_falls_back_to_pymupdf_when_pypdfium2_is_bad(monkeypatch):
    service = PolicyDocumentService()

    monkeypatch.setattr(
        service,
        "_extract_pdf_text_with_pypdfium2",
        lambda content: PdfExtractionResult(
            method="pypdfium2",
            text="짧음",
            quality_score=1,
            metrics={
                "text_length": 2,
                "replacement_count": 0,
                "space_ratio": 0.1,
                "spaced_hangul_runs": 0,
            },
        ),
    )
    monkeypatch.setattr(
        service,
        "_extract_pdf_text_with_pymupdf",
        lambda content: PdfExtractionResult(
            method="pymupdf",
            text="정상 텍스트\n1. 지원 대상\n충분한 내용",
            quality_score=130,
            metrics={
                "text_length": 1500,
                "replacement_count": 0,
                "space_ratio": 0.1,
                "spaced_hangul_runs": 0,
            },
        ),
    )
    monkeypatch.setattr(
        service,
        "_extract_pdf_text_with_pdfplumber",
        lambda content: (_ for _ in ()).throw(
            AssertionError("정상 PyMuPDF 결과에서는 pdfplumber를 호출하면 안 됩니다.")
        ),
    )

    result = service._extract_pdf_text(b"%PDF")

    assert result.method == "pymupdf"
    assert result.metrics["fallback_from"] == "pypdfium2"


def test_extract_pdf_text_recommends_ocr_when_fast_extractors_are_empty(monkeypatch):
    service = PolicyDocumentService()

    monkeypatch.setattr(
        service,
        "_extract_pdf_text_with_pypdfium2",
        lambda content: PdfExtractionResult(
            method="pypdfium2",
            text="",
            quality_score=0,
            metrics={"text_length": 0},
        ),
    )
    monkeypatch.setattr(
        service,
        "_extract_pdf_text_with_pymupdf",
        lambda content: PdfExtractionResult(
            method="pymupdf",
            text="",
            quality_score=0,
            metrics={"text_length": 0},
        ),
    )
    monkeypatch.setattr(
        service,
        "_extract_pdf_text_with_pdfplumber",
        lambda content: (_ for _ in ()).throw(
            AssertionError("텍스트가 없는 PDF는 느린 pdfplumber fallback을 호출하지 않습니다.")
        ),
    )

    result = service._extract_pdf_text(b"%PDF")

    assert result.method == "none"
    assert result.metrics["ocr_recommended"] is True


def test_normalize_pdf_page_text_restores_reversed_rotated_page():
    service = PolicyDocumentService()
    reversed_text = "\n".join(
        [
            "다니합 야해출제 를류서 청신",
            "다니습있 수 할청신 면하당해 에상대 원지",
            "다니입준기 정선 의업사 원지",
        ]
    )

    normalized, was_reversed = service._normalize_pdf_page_text(
        reversed_text,
        rotation=90,
    )

    assert was_reversed is True
    assert "신청 서류를 제출해야 합니다" in normalized
    assert "지원 대상에 해당하면 신청할 수 있습니다" in normalized
    assert "지원 사업의 선정 기준입니다" in normalized


def test_normalize_pdf_page_text_keeps_valid_rotated_page():
    service = PolicyDocumentService()
    valid_text = "\n".join(
        [
            "신청 서류를 제출해야 합니다",
            "지원 대상에 해당하면 신청할 수 있습니다",
            "지원 사업의 선정 기준입니다",
        ]
    )

    normalized, was_reversed = service._normalize_pdf_page_text(
        valid_text,
        rotation=90,
    )

    assert was_reversed is False
    assert normalized == valid_text


def test_build_policy_reference_documents_splits_header_sections():
    service = PolicyDocumentService()
    documents = service._build_policy_reference_documents(
        source={
            "policy_id": 1,
            "policy_code": "WLF00000001",
            "policy_name": "테스트 정책",
            "source_title": "사업안내.pdf",
            "source_url": "https://example.com/file.pdf",
        },
        raw_text=(
            "Ⅰ. 사업 개요\n"
            "사업 설명입니다.\n"
            "1. 지원 대상\n"
            "아동을 지원합니다.\n"
            "2. 신청 방법\n"
            "온라인으로 신청합니다."
        ),
        file_type="PDF",
        extraction_result=PdfExtractionResult(
            method="pypdfium2",
            text="",
            quality_score=123.4,
            metrics={"header_count": 3},
        ),
    )

    sections = [document.metadata["section"] for document in documents]

    assert sections == ["Ⅰ. 사업 개요"]
    assert "1. 지원 대상" in documents[0].page_content
    assert "2. 신청 방법" in documents[0].page_content
    assert documents[0].metadata["extraction_method"] == "pypdfium2"
    assert documents[0].metadata["extraction_quality_score"] == 123.4


def test_reference_sections_skip_toc_dot_leaders_and_pack_small_sections():
    service = PolicyDocumentService()

    sections = service._reference_sections(
        "\n".join(
            [
                "Ⅰ 모자보건사업 추진방향 ·········································1",
                "1. 지원 대상",
                "지원 대상 설명",
                "2. 신청 방법",
                "신청 방법 설명",
                "3. 제출 서류",
                "제출 서류 설명",
            ]
        )
    )

    assert len(sections) == 1
    assert sections[0]["section"] == "관련 문서"
    assert "1. 지원 대상" in sections[0]["content"]
    assert "3. 제출 서류" in sections[0]["content"]
