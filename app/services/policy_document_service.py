import logging
from typing import Annotated, Any

from fastapi import Depends
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.common.psycopg_pool_conf import psycopg_pool
from app.repositories.policy_document_repository import PolicyDocumentRepository
from app.schemas.policy_document_schema import (
    PolicyDocumentChunkIngestItem,
    PolicyDocumentChunkIngestResponse,
    PolicyDocumentChunkSkipItem,
)


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

    def split_documents(self, documents: list[Document]) -> list[Document]:
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=500,
            chunk_overlap=50,
            length_function=len,
        )
        return text_splitter.split_documents(documents)

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
