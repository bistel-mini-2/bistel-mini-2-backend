import base64
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
    ) -> PolicyDocumentChunkIngestResponse:
        items: list[PolicyDocumentChunkIngestItem] = []
        skipped: list[PolicyDocumentChunkSkipItem] = []
        failed: list[dict[str, str]] = []

        async with psycopg_pool.connection() as conn:
            sources = await PolicyDocumentRepository.find_policy_detail_sources(
                conn,
                limit,
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

                    chunk_documents = self.split_documents(section_documents)
                    async with conn.transaction():
                        document_id = (
                            await PolicyDocumentRepository.upsert_policy_detail_document(
                                conn=conn,
                                policy_id=source["policy_id"],
                                source_title=self._source_title(source),
                                source_url=source.get("official_url"),
                                raw_text=raw_text,
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
                            document_id=document_id,
                            policy_code=source["policy_code"],
                            policy_name=source["policy_name"],
                            raw_text_length=len(raw_text),
                            chunk_count=chunk_count,
                        )
                    )
                except Exception as exc:
                    self.logger.exception("Failed to ingest policy detail chunks")
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
                    self.logger.exception("Failed to ingest policy reference document")
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
                        "Failed to ingest policy reference document with OpenAI Vision"
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
                                "Extract only the visible text from this PDF. "
                                "Preserve the original wording as much as possible. "
                                "For tables, write each row as plain text. "
                                "Do not summarize or explain."
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
        for section, evidence_role, content in self._section_values(source):
            cleaned_content = self._clean_text(content)
            if not cleaned_content:
                continue

            section_documents.append(
                Document(
                    page_content=self._format_section_text(
                        policy_name=source["policy_name"],
                        section=section,
                        content=cleaned_content,
                    ),
                    metadata={
                        "policy_id": source["policy_id"],
                        "policy_code": source["policy_code"],
                        "source_type": "POLICY_DETAIL",
                        "source_title": self._source_title(source),
                        "source_url": source.get("official_url"),
                        "section": section,
                        "evidence_role": evidence_role,
                    },
                )
            )

        raw_text = "\n\n".join(document.page_content for document in section_documents)
        return raw_text, section_documents

    def _section_values(self, source: dict[str, Any]) -> list[tuple[str, str, Any]]:
        return [
            (
                "기본 정보",
                "summary",
                self._join_lines(
                    [
                        ("정책명", source.get("policy_name")),
                        ("대분류", source.get("main_category")),
                        ("소분류", source.get("sub_category")),
                        ("제공기관", source.get("provider_name")),
                        ("급여유형", source.get("benefit_type")),
                    ]
                ),
            ),
            ("요약", "summary", source.get("easy_summary")),
            ("지원 대상", "target", source.get("target_description")),
            ("지원 내용", "benefit", source.get("benefit_description")),
            ("신청 방법", "application", source.get("application_method")),
            ("신청 기간", "application", source.get("application_period_text")),
            ("유의 사항", "caution", source.get("caution")),
        ]

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
        return str(value).replace("\x00", "").strip()


PolicyDocumentServiceDep = Annotated[
    PolicyDocumentService,
    Depends(PolicyDocumentService),
]
