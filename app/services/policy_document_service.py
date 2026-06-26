import base64
import json
import logging
from collections import defaultdict
from io import BytesIO
from typing import Annotated, Any

from fastapi import Depends
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openai import AsyncOpenAI
from pypdf import PdfReader
import requests

from app.common.psycopg_pool_conf import psycopg_pool
from app.core.config import settings
from app.repositories.policy_document_repository import PolicyDocumentRepository
from app.schemas.policy_document_schema import (
    PolicyDocumentChunkIngestItem,
    PolicyDocumentChunkIngestResponse,
    PolicyDocumentChunkSkipItem,
    PolicyReferenceDocumentIngestItem,
    PolicyReferenceDocumentIngestResponse,
    PolicyReferenceDocumentSkipItem,
)


POLICY_REFERENCE_VISION_MODEL = "gpt-4o"
POLICY_REFERENCE_OPENAI_MAX_BYTES = 50 * 1024 * 1024


class PolicyDocumentService:
    def __init__(self) -> None:
        self.logger = logging.getLogger(f"{__name__}.PolicyDocumentService")

    async def ingest_policy_detail_chunks(
        self,
        limit: int = 10,
        rebuild: bool = False,
    ) -> PolicyDocumentChunkIngestResponse:
        items: list[PolicyDocumentChunkIngestItem] = []
        skipped: list[PolicyDocumentChunkSkipItem] = []
        failed: list[dict[str, str]] = []

        async with psycopg_pool.connection() as conn:
            sources = await PolicyDocumentRepository.find_policy_detail_sources(
                conn,
                limit,
                rebuild=rebuild,
            )

            for source in sources:
                try:
                    raw_text, section_documents = self._build_policy_detail_documents(
                        source
                    )
                    if not raw_text:
                        skipped.append(
                            PolicyDocumentChunkSkipItem(
                                policy_id=source["policy_id"],
                                policy_code=source["policy_code"],
                                policy_name=source["policy_name"],
                                reason="chunk로 만들 정책 상세 텍스트가 없습니다.",
                            )
                        )
                        continue

                    chunk_documents = self.split_policy_detail_documents(
                        section_documents
                    )
                    async with conn.transaction():
                        document_id = (
                            await PolicyDocumentRepository.upsert_policy_detail_document(
                                conn=conn,
                                policy_id=source["policy_id"],
                                condition_profile_id=source.get(
                                    "condition_profile_id"
                                ),
                                source_title=self._source_title(source),
                                source_url=source.get("official_url"),
                                raw_text=raw_text,
                            )
                        )
                        deleted_embedding_count = (
                            await PolicyDocumentRepository.delete_policy_detail_embeddings_for_document(
                                conn=conn,
                                document_id=document_id,
                            )
                        )
                        chunk_count = (
                            await PolicyDocumentRepository.replace_document_chunks(
                                conn=conn,
                                document_id=document_id,
                                chunk_documents=chunk_documents,
                            )
                        )

                    items.append(
                        PolicyDocumentChunkIngestItem(
                            policy_id=source["policy_id"],
                            condition_profile_id=source.get("condition_profile_id"),
                            document_id=document_id,
                            policy_code=source["policy_code"],
                            policy_name=source["policy_name"],
                            raw_text_length=len(raw_text),
                            chunk_count=chunk_count,
                            deleted_embedding_count=deleted_embedding_count,
                        )
                    )
                except Exception as exc:
                    self.logger.exception("정책 상세 chunk 생성 중 오류 발생")
                    failed.append(
                        {
                            "policy_id": str(source.get("policy_id")),
                            "policy_code": str(source.get("policy_code")),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        return PolicyDocumentChunkIngestResponse(
            requested_count=len(sources),
            completed_count=len(items),
            skipped_count=len(skipped),
            failed_count=len(failed),
            items=items,
            skipped=skipped,
            failed=failed,
        )

    async def ingest_policy_reference_documents(
        self,
        limit: int = 10,
    ) -> PolicyReferenceDocumentIngestResponse:
        items: list[PolicyReferenceDocumentIngestItem] = []
        skipped: list[PolicyReferenceDocumentSkipItem] = []
        failed: list[dict[str, str]] = []

        async with psycopg_pool.connection() as conn:
            targets = await PolicyDocumentRepository.find_policy_reference_download_targets(
                conn=conn,
                limit=limit,
            )
            targets_by_url = self._group_by_source_url(targets)

            for source_url, documents in targets_by_url.items():
                try:
                    downloaded = self._download_reference_document(source_url)
                    file_type = self._detect_file_type(
                        content=downloaded["content"],
                        source_title=documents[0]["source_title"],
                        content_type=downloaded.get("content_type"),
                    )
                    raw_text = self._extract_text(
                        content=downloaded["content"],
                        file_type=file_type,
                    )
                    if not raw_text:
                        for document in documents:
                            skipped.append(
                                self._reference_skip_item(
                                    document=document,
                                    reason="추출된 텍스트가 없습니다.",
                                )
                            )
                        continue

                    for document in documents:
                        chunk_documents = self.split_reference_documents(
                            self._build_policy_reference_documents(
                                source=document,
                                raw_text=raw_text,
                                file_type=file_type,
                            )
                        )
                        async with conn.transaction():
                            await PolicyDocumentRepository.update_document_raw_text(
                                conn=conn,
                                document_id=document["document_id"],
                                raw_text=raw_text,
                            )
                            chunk_count = (
                                await PolicyDocumentRepository.replace_document_chunks(
                                    conn=conn,
                                    document_id=document["document_id"],
                                    chunk_documents=chunk_documents,
                                )
                            )

                        items.append(
                            PolicyReferenceDocumentIngestItem(
                                document_id=document["document_id"],
                                policy_id=document["policy_id"],
                                policy_code=document["policy_code"],
                                policy_name=document["policy_name"],
                                source_title=document["source_title"],
                                source_url=document["source_url"],
                                file_type=file_type,
                                raw_text_length=len(raw_text),
                                chunk_count=chunk_count,
                            )
                        )
                except ValueError as exc:
                    for document in documents:
                        skipped.append(
                            self._reference_skip_item(
                                document=document,
                                reason=str(exc),
                            )
                        )
                except Exception as exc:
                    self.logger.exception("정책 관련 문서 처리 중 오류 발생")
                    failed.append(
                        {
                            "source_url": source_url,
                            "document_ids": ",".join(
                                str(document["document_id"]) for document in documents
                            ),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        return PolicyReferenceDocumentIngestResponse(
            requested_url_count=len(targets_by_url),
            completed_count=len(items),
            skipped_count=len(skipped),
            failed_count=len(failed),
            items=items,
            skipped=skipped,
            failed=failed,
        )

    async def ingest_policy_reference_documents_with_openai_vision(
        self,
        limit: int = 10,
    ) -> PolicyReferenceDocumentIngestResponse:
        items: list[PolicyReferenceDocumentIngestItem] = []
        skipped: list[PolicyReferenceDocumentSkipItem] = []
        failed: list[dict[str, str]] = []

        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY가 설정되어 있지 않습니다.")

        async with psycopg_pool.connection() as conn:
            targets = await PolicyDocumentRepository.find_policy_reference_download_targets(
                conn=conn,
                limit=limit,
            )
            targets_by_url = self._group_by_source_url(targets)

            for source_url, documents in targets_by_url.items():
                try:
                    downloaded = self._download_reference_document(source_url)
                    file_type = self._detect_file_type(
                        content=downloaded["content"],
                        source_title=documents[0]["source_title"],
                        content_type=downloaded.get("content_type"),
                    )
                    if file_type != "PDF":
                        for document in documents:
                            skipped.append(
                                self._reference_skip_item(
                                    document=document,
                                    reason=(
                                        "OpenAI Vision 보완 추출은 PDF 문서만 "
                                        f"지원합니다: {file_type}"
                                    ),
                                )
                            )
                        continue

                    content = downloaded["content"]
                    if not self._is_pdf_content(content):
                        for document in documents:
                            skipped.append(
                                self._reference_skip_item(
                                    document=document,
                                    reason=(
                                        "문서명이 PDF이지만 실제 PDF 바이트가 "
                                        "아니어서 처리하지 않았습니다."
                                    ),
                                )
                            )
                        continue

                    if len(content) > POLICY_REFERENCE_OPENAI_MAX_BYTES:
                        for document in documents:
                            skipped.append(
                                self._reference_skip_item(
                                    document=document,
                                    reason=(
                                        "OpenAI 파일 입력 제한을 초과해 처리하지 "
                                        f"않았습니다: {len(content)} bytes"
                                    ),
                                )
                            )
                        continue

                    raw_text = await self._extract_pdf_text_with_openai_vision(
                        content=content,
                        filename=self._safe_pdf_filename(documents[0]["source_title"]),
                    )
                    if not raw_text:
                        for document in documents:
                            skipped.append(
                                self._reference_skip_item(
                                    document=document,
                                    reason="OpenAI Vision으로 추출된 텍스트가 없습니다.",
                                )
                            )
                        continue

                    for document in documents:
                        chunk_documents = self.split_reference_documents(
                            self._build_policy_reference_documents(
                                source=document,
                                raw_text=raw_text,
                                file_type="PDF",
                            )
                        )
                        async with conn.transaction():
                            await PolicyDocumentRepository.update_document_raw_text(
                                conn=conn,
                                document_id=document["document_id"],
                                raw_text=raw_text,
                            )
                            chunk_count = (
                                await PolicyDocumentRepository.replace_document_chunks(
                                    conn=conn,
                                    document_id=document["document_id"],
                                    chunk_documents=chunk_documents,
                                )
                            )

                        items.append(
                            PolicyReferenceDocumentIngestItem(
                                document_id=document["document_id"],
                                policy_id=document["policy_id"],
                                policy_code=document["policy_code"],
                                policy_name=document["policy_name"],
                                source_title=document["source_title"],
                                source_url=document["source_url"],
                                file_type="PDF",
                                raw_text_length=len(raw_text),
                                chunk_count=chunk_count,
                            )
                        )
                except ValueError as exc:
                    for document in documents:
                        skipped.append(
                            self._reference_skip_item(
                                document=document,
                                reason=str(exc),
                            )
                        )
                except Exception as exc:
                    self.logger.exception(
                        "OpenAI Vision 기반 정책 관련 문서 처리 중 오류 발생"
                    )
                    failed.append(
                        {
                            "source_url": source_url,
                            "document_ids": ",".join(
                                str(document["document_id"]) for document in documents
                            ),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        return PolicyReferenceDocumentIngestResponse(
            requested_url_count=len(targets_by_url),
            completed_count=len(items),
            skipped_count=len(skipped),
            failed_count=len(failed),
            items=items,
            skipped=skipped,
            failed=failed,
        )

    def split_documents(self, documents: list[Document]) -> list[Document]:
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            length_function=len,
        )
        return text_splitter.split_documents(documents)

    def split_policy_detail_documents(self, documents: list[Document]) -> list[Document]:
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1200,
            chunk_overlap=100,
            length_function=len,
        )

        split_documents: list[Document] = []
        for document in documents:
            if document.metadata.get("preserve_chunk"):
                metadata = dict(document.metadata)
                metadata.pop("preserve_chunk", None)
                split_documents.append(
                    Document(
                        page_content=document.page_content,
                        metadata=metadata,
                    )
                )
                continue

            for split_document in text_splitter.split_documents([document]):
                metadata = dict(split_document.metadata)
                metadata.pop("preserve_chunk", None)
                split_documents.append(
                    Document(
                        page_content=split_document.page_content,
                        metadata=metadata,
                    )
                )
        return split_documents

    def split_reference_documents(self, documents: list[Document]) -> list[Document]:
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=2000,
            chunk_overlap=200,
            length_function=len,
        )
        return text_splitter.split_documents(documents)

    def _group_by_source_url(
        self,
        targets: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for target in targets:
            grouped[target["source_url"]].append(target)
        return dict(grouped)

    def _download_reference_document(self, source_url: str) -> dict[str, Any]:
        response = requests.get(source_url, timeout=60)
        response.raise_for_status()
        return {
            "content": response.content,
            "content_type": response.headers.get("content-type"),
        }

    def _detect_file_type(
        self,
        content: bytes,
        source_title: str,
        content_type: str | None,
    ) -> str:
        lowered_title = source_title.lower()
        lowered_content_type = (content_type or "").lower()
        if content.startswith(b"%PDF") or ".pdf" in lowered_title:
            return "PDF"
        if (
            content.startswith(bytes.fromhex("d0cf11e0a1b11ae1"))
            or ".hwp" in lowered_title
        ):
            return "HWP"
        if content.startswith(b"PK\x03\x04") and ".hwpx" in lowered_title:
            return "HWPX"
        if "html" in lowered_content_type:
            return "HTML"
        return "UNKNOWN"

    def _extract_text(self, content: bytes, file_type: str) -> str:
        if file_type == "PDF":
            return self._extract_pdf_text(content)
        if file_type in {"HWP", "HWPX"}:
            raise ValueError(f"{file_type} 문서 텍스트 추출은 아직 지원하지 않습니다.")
        raise ValueError(f"지원하지 않는 문서 형식입니다: {file_type}")

    def _extract_pdf_text(self, content: bytes) -> str:
        reader = PdfReader(BytesIO(content))
        page_texts = [
            self._clean_text(page.extract_text())
            for page in reader.pages
        ]
        return "\n\n".join(page_text for page_text in page_texts if page_text)

    def _is_pdf_content(self, content: bytes) -> bool:
        return content.startswith(b"%PDF")

    async def _extract_pdf_text_with_openai_vision(
        self,
        content: bytes,
        filename: str,
    ) -> str:
        encoded_pdf = base64.b64encode(content).decode("ascii")
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.responses.create(
            model=POLICY_REFERENCE_VISION_MODEL,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_file",
                            "filename": filename,
                            "file_data": (
                                "data:application/pdf;base64,"
                                f"{encoded_pdf}"
                            ),
                        },
                        {
                            "type": "input_text",
                            "text": (
                                "이 PDF에서 보이는 텍스트만 추출하세요. "
                                "가능한 한 원문 표현을 유지하세요. "
                                "표는 각 행을 일반 텍스트로 풀어서 작성하세요. "
                                "요약하거나 설명하지 마세요."
                            ),
                        },
                    ],
                }
            ],
        )
        return self._clean_text(response.output_text)

    def _safe_pdf_filename(self, source_title: str) -> str:
        filename = self._clean_text(source_title) or "policy-reference.pdf"
        if not filename.lower().endswith(".pdf"):
            filename = f"{filename}.pdf"
        return filename

    def _build_policy_reference_documents(
        self,
        source: dict[str, Any],
        raw_text: str,
        file_type: str,
    ) -> list[Document]:
        return [
            Document(
                page_content=self._format_section_text(
                    policy_name=source["policy_name"],
                    section="관련 문서",
                    content=raw_text,
                ),
                metadata={
                    "policy_id": source["policy_id"],
                    "policy_code": source["policy_code"],
                    "source_type": "POLICY_REFERENCE",
                    "source_title": source["source_title"],
                    "source_url": source["source_url"],
                    "section": "관련 문서",
                    "evidence_role": "reference",
                    "file_type": file_type,
                },
            )
        ]

    def _reference_skip_item(
        self,
        document: dict[str, Any],
        reason: str,
    ) -> PolicyReferenceDocumentSkipItem:
        return PolicyReferenceDocumentSkipItem(
            document_id=document["document_id"],
            policy_id=document["policy_id"],
            policy_code=document["policy_code"],
            policy_name=document["policy_name"],
            source_title=document["source_title"],
            source_url=document.get("source_url"),
            reason=reason,
        )

    def _build_policy_detail_documents(
        self,
        source: dict[str, Any],
    ) -> tuple[str, list[Document]]:
        section_documents: list[Document] = []
        for section_value in self._section_values(source):
            cleaned_content = self._clean_text(section_value["content"])
            if not cleaned_content:
                continue

            metadata = self._policy_detail_metadata(source, section_value)
            section_documents.append(
                Document(
                    page_content=self._format_section_text(
                        policy_name=source["policy_name"],
                        section=section_value["section"],
                        content=cleaned_content,
                    ),
                    metadata=metadata,
                )
            )

        raw_text = "\n\n".join(document.page_content for document in section_documents)
        return raw_text, section_documents

    def _section_values(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        condition_json = self._dict_value(source.get("condition_json"))
        sections: list[dict[str, Any]] = [
            {
                "section": "정리된 지원 조건",
                "evidence_role": "target",
                "content": source.get("target_summary")
                or condition_json.get("target_summary"),
                "condition_group": "target_summary",
                "condition_operator": None,
                "preserve_chunk": True,
            },
            {
                "section": "조건 구조",
                "evidence_role": "target",
                "content": self._format_condition_tree(
                    condition_json.get("condition_tree")
                ),
                "condition_group": "condition_tree",
                "condition_operator": self._node_operator(
                    condition_json.get("condition_tree")
                ),
                "preserve_chunk": True,
            },
            {
                "section": "제외 조건",
                "evidence_role": "caution",
                "content": self._format_condition_items(
                    condition_json.get("exclusions")
                ),
                "condition_group": "exclusions",
                "condition_operator": "NOT",
                "preserve_chunk": True,
            },
            {
                "section": "추가 확인 조건",
                "evidence_role": "caution",
                "content": self._format_condition_items(
                    self._list_value(condition_json.get("unknowns"))
                    + self._list_value(
                        condition_json.get("unsupported_conditions")
                    )
                    + self._list_value(condition_json.get("special_notes"))
                ),
                "condition_group": "manual_check",
                "condition_operator": None,
                "preserve_chunk": True,
            },
            {
                "section": "공식 지원대상 원문",
                "evidence_role": "target",
                "content": source.get("source_text")
                or source.get("target_description"),
                "condition_group": "official_target_text",
                "condition_operator": None,
                "source_basis": "policy_detail",
            },
            {
                "section": "지원 내용",
                "evidence_role": "benefit",
                "content": source.get("benefit_description"),
                "condition_group": None,
                "condition_operator": None,
                "source_basis": "policy_detail",
            },
            {
                "section": "신청 방법",
                "evidence_role": "application",
                "content": source.get("application_method"),
                "condition_group": None,
                "condition_operator": None,
                "source_basis": "policy_detail",
            },
            {
                "section": "신청 기간",
                "evidence_role": "application",
                "content": source.get("application_period_text"),
                "condition_group": None,
                "condition_operator": None,
                "source_basis": "policy_detail",
            },
            {
                "section": "유의 사항",
                "evidence_role": "caution",
                "content": source.get("caution"),
                "condition_group": None,
                "condition_operator": None,
                "source_basis": "policy_detail",
            },
        ]
        return sections

    def _policy_detail_metadata(
        self,
        source: dict[str, Any],
        section_value: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = {
            "policy_id": source["policy_id"],
            "policy_code": source["policy_code"],
            "policy_name": source["policy_name"],
            "condition_profile_id": source.get("condition_profile_id"),
            "condition_profile_updated_at": self._to_metadata_value(
                source.get("condition_profile_updated_at")
            ),
            "condition_profile_confidence": self._to_metadata_value(
                source.get("confidence")
            ),
            "review_required": source.get("review_required"),
            "quality_flags": source.get("quality_flags"),
            "source_fields": source.get("source_fields"),
            "source_type": "POLICY_DETAIL",
            "source_basis": section_value.get(
                "source_basis",
                "policy_condition_profile",
            ),
            "source_title": self._source_title(source),
            "source_url": source.get("official_url"),
            "section": section_value["section"],
            "evidence_role": section_value["evidence_role"],
            "condition_group": section_value.get("condition_group"),
            "condition_operator": section_value.get("condition_operator"),
            "preserve_chunk": section_value.get("preserve_chunk", False),
        }
        return {key: value for key, value in metadata.items() if value is not None}

    def _format_condition_tree(self, node: Any, depth: int = 0) -> str:
        if not node:
            return ""
        if not isinstance(node, dict):
            return self._clean_text(node)

        indent = "  " * depth
        children = self._node_children(node)
        operator = self._node_operator(node)
        if children:
            header = f"{indent}- 조건 그룹: {operator or 'AND'}"
            lines = [header]
            for child in children:
                child_text = self._format_condition_tree(child, depth + 1)
                if child_text:
                    lines.append(child_text)
            return "\n".join(lines)

        return f"{indent}- {self._format_condition_leaf(node)}"

    def _format_condition_items(self, items: Any) -> str:
        if not items:
            return ""
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return self._clean_text(items)

        lines = []
        for item in items:
            if isinstance(item, dict):
                lines.append(f"- {self._format_condition_leaf(item)}")
            else:
                cleaned = self._clean_text(item)
                if cleaned:
                    lines.append(f"- {cleaned}")
        return "\n".join(lines)

    def _format_condition_leaf(self, node: dict[str, Any]) -> str:
        label_values = [
            ("field", node.get("field") or node.get("field_name")),
            ("operator", node.get("operator")),
            (
                "value",
                node.get("value")
                if "value" in node
                else node.get("value_json"),
            ),
            ("matching_strength", node.get("matching_strength")),
            ("source_text", node.get("source_text")),
            ("confidence", node.get("confidence")),
            ("note", node.get("note")),
            ("reason", node.get("reason")),
        ]
        return self._join_lines(label_values).replace("\n", ", ")

    def _node_children(self, node: dict[str, Any]) -> list[Any]:
        for key in ("children", "conditions", "items", "rules"):
            value = node.get(key)
            if isinstance(value, list):
                return value
        return []

    def _node_operator(self, node: Any) -> str | None:
        if not isinstance(node, dict):
            return None
        operator = (
            node.get("operator")
            or node.get("condition_operator")
            or node.get("group_operator")
            or node.get("logic")
            or node.get("type")
        )
        if operator is None:
            return None
        return str(operator).upper()

    def _dict_value(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    def _list_value(self, value: Any) -> list[Any]:
        if not value:
            return []
        if isinstance(value, list):
            return value
        return [value]

    def _to_metadata_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, (str, int, float, bool, list, dict)):
            return value
        try:
            return float(value)
        except (TypeError, ValueError):
            return str(value)

    def _join_lines(self, values: list[tuple[str, Any]]) -> str:
        lines = [
            f"{label}: {self._clean_text(value)}"
            for label, value in values
            if self._clean_text(value)
        ]
        return "\n".join(lines)

    def _format_section_text(
        self,
        policy_name: str,
        section: str,
        content: str,
    ) -> str:
        return f"정책명: {policy_name}\n섹션: {section}\n내용:\n{content}"

    def _source_title(self, source: dict[str, Any]) -> str:
        return f"{source['policy_name']} 정책 상세 데이터"

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, default=str)
        return str(value).replace("\x00", "").strip()


PolicyDocumentServiceDep = Annotated[
    PolicyDocumentService,
    Depends(PolicyDocumentService),
]
