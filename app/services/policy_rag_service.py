import logging
from typing import Annotated, Any

from fastapi import Depends
from langchain.embeddings import init_embeddings
from langchain_postgres import PGVector

from app.common.psycopg_pool_conf import psycopg_pool
from app.core.config import settings
from app.db.session import engine
from app.repositories.policy_rag_repository import PolicyRagRepository
from app.schemas.policy_rag_schema import (
    PolicyRagEmbeddingItem,
    PolicyRagEmbeddingResponse,
    PolicyRagSearchResponse,
    PolicyRagSearchResult,
)


POLICY_RAG_COLLECTION_NAME = "policy_documents"


class PolicyRagService:
    def __init__(self) -> None:
        self.logger = logging.getLogger(f"{__name__}.PolicyRagService")

    async def ingest_embeddings(
        self,
        limit: int = 100,
        source_type: str | None = None,
    ) -> PolicyRagEmbeddingResponse:
        async with psycopg_pool.connection() as conn:
            targets = await PolicyRagRepository.find_embedding_targets(
                conn=conn,
                limit=limit,
                source_type=source_type,
            )

        if not targets:
            return PolicyRagEmbeddingResponse(
                requested_count=0,
                embedded_count=0,
                collection_name=POLICY_RAG_COLLECTION_NAME,
                items=[],
            )

        texts = [target["chunk_text"] for target in targets]
        metadatas = [self._build_metadata(target) for target in targets]
        ids = [str(target["chunk_id"]) for target in targets]

        vectorstore = self._vectorstore()
        await vectorstore.aadd_texts(
            texts=texts,
            metadatas=metadatas,
            ids=ids,
        )

        return PolicyRagEmbeddingResponse(
            requested_count=len(targets),
            embedded_count=len(ids),
            collection_name=POLICY_RAG_COLLECTION_NAME,
            items=[
                PolicyRagEmbeddingItem(
                    chunk_id=target["chunk_id"],
                    document_id=target["document_id"],
                    policy_id=target["policy_id"],
                    policy_code=target["policy_code"],
                    policy_name=target["policy_name"],
                )
                for target in targets
            ],
        )

    async def search(
        self,
        query: str,
        k: int = 5,
        source_type: str | None = None,
        policy_ids: list[int | str] | None = None,
    ) -> PolicyRagSearchResponse:
        filter_value = self._search_filter(source_type, policy_ids)
        results = await self._vectorstore().asimilarity_search_with_score(
            query=query,
            k=k,
            filter=filter_value,
        )

        search_results = [
            self._to_search_result(document=document, distance=distance)
            for document, distance in results
        ]
        return PolicyRagSearchResponse(
            query=query,
            result_count=len(search_results),
            results=search_results,
        )

    def _search_filter(
        self,
        source_type: str | None,
        policy_ids: list[int | str] | None,
    ) -> dict[str, Any] | None:
        conditions: list[dict[str, Any]] = []
        if source_type:
            conditions.append({"source_type": source_type})

        policy_keys = [str(policy_id) for policy_id in policy_ids or []]
        if policy_keys:
            conditions.append(
                {
                    "$or": [
                        {"policy_id": {"$in": policy_keys}},
                        {"policy_code": {"$in": policy_keys}},
                    ]
                }
            )

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _vectorstore(self) -> PGVector:
        embedding_kwargs = {}
        if settings.openai_api_key:
            embedding_kwargs["api_key"] = settings.openai_api_key

        return PGVector(
            embeddings=init_embeddings(
                model="openai:text-embedding-3-large",
                **embedding_kwargs,
            ),
            collection_name=POLICY_RAG_COLLECTION_NAME,
            connection=engine,
            async_mode=True,
            use_jsonb=True,
        )

    def _build_metadata(self, target: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(target.get("metadata_json") or {})
        metadata.update(
            {
                "chunk_id": target["chunk_id"],
                "document_id": target["document_id"],
                "chunk_index": target["chunk_index"],
                "policy_id": target["policy_id"],
                "policy_code": target["policy_code"],
                "policy_name": target["policy_name"],
                "source_title": target["source_title"],
                "source_url": target["source_url"],
                "source_type": target["source_type"],
                "chunk_hash": target["chunk_hash"],
            }
        )
        return metadata

    def _to_search_result(self, document, distance: float) -> PolicyRagSearchResult:
        metadata = document.metadata or {}
        return PolicyRagSearchResult(
            chunk_id=self._to_int(metadata.get("chunk_id")),
            document_id=self._to_int(metadata.get("document_id")),
            policy_id=self._to_int(metadata.get("policy_id")),
            policy_code=self._to_str(metadata.get("policy_code")),
            policy_name=self._to_str(metadata.get("policy_name")),
            section=self._to_str(metadata.get("section")),
            source_type=self._to_str(metadata.get("source_type")),
            source_url=self._to_str(metadata.get("source_url")),
            chunk_text=document.page_content,
            distance=float(distance),
        )

    def _to_int(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _to_str(self, value: Any) -> str | None:
        if value is None:
            return None
        return str(value)


PolicyRagServiceDep = Annotated[
    PolicyRagService,
    Depends(PolicyRagService),
]
