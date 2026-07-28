import asyncio
from types import SimpleNamespace

from app.ai.retrievers import (
    HybridPolicyRetriever,
    RetrievalHit,
    RetrievalStrategy,
    SqlKeywordPolicyRetriever,
    VectorPolicyRetriever,
    build_policy_retriever,
)
from app.ai.agents.policy_summary_agent import PolicySummaryGeneration
from app.ai.graphs import policy_summary_graph as graph_module
from app.ai.graphs.policy_summary_graph import PolicySummaryGraphRunner
from app.ai.tools.policy_chunk_search_tool import search_policy_chunks
from app.repositories.policy_rag_repository import PolicyRagRepository
from app.schemas.policy_rag_schema import (
    PolicyRagSearchResponse,
    PolicyRagSearchResult,
)


class StubRetriever:
    def __init__(self, hits: list[RetrievalHit]) -> None:
        self.hits = hits
        self.calls: list[dict[str, object]] = []

    async def retrieve(
        self,
        query,
        *,
        top_k=5,
        source_type=None,
        policy_ids=None,
    ):
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "source_type": source_type,
                "policy_ids": policy_ids,
            }
        )
        return self.hits[:top_k]


def make_hit(chunk_id: int, score: float = 0.5) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        policy_id=100,
        chunk_text=f"근거 {chunk_id}",
        source_title="테스트 정책",
        source_url="https://example.com/policy",
        score=score,
    )


def test_vector_retriever_converts_distance_to_relevance_score():
    class FakeRagService:
        async def search(self, **kwargs):
            assert kwargs == {
                "query": "출산 지원",
                "k": 3,
                "source_type": "POLICY_DETAIL",
                "policy_ids": [100],
            }
            return PolicyRagSearchResponse(
                query="출산 지원",
                result_count=1,
                results=[
                    PolicyRagSearchResult(
                        chunk_id=1,
                        policy_id=100,
                        chunk_text="출산 지원 근거",
                        distance=0.25,
                    )
                ],
            )

    hits = asyncio.run(
        VectorPolicyRetriever(rag_service=FakeRagService()).retrieve(
            "출산 지원",
            top_k=3,
            source_type="POLICY_DETAIL",
            policy_ids=[100],
        )
    )

    assert hits[0].chunk_id == 1
    assert hits[0].score == 0.8


def test_sql_keyword_retriever_maps_repository_row(monkeypatch):
    class FakeRepository:
        @staticmethod
        async def search_chunks_by_keywords(**kwargs):
            assert kwargs["query"] == "청년 지원"
            assert kwargs["limit"] == 2
            assert kwargs["policy_ids"] == ["P100"]
            return [
                {
                    "chunk_id": 10,
                    "document_id": 20,
                    "policy_id": 100,
                    "policy_code": "P100",
                    "policy_name": "청년 지원",
                    "chunk_text": "청년에게 지원금을 지급합니다.",
                    "metadata_json": {
                        "section": "지원 내용",
                        "evidence_role": "benefit",
                    },
                    "source_type": "POLICY_DETAIL",
                    "source_title": "청년 지원 상세",
                    "source_url": "https://example.com/100",
                    "keyword_score": 1.0,
                }
            ]

    class FakeConnectionContext:
        async def __aenter__(self):
            return SimpleNamespace()

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(
        "app.ai.retrievers.policy_retriever.psycopg_pool.connection",
        lambda: FakeConnectionContext(),
    )

    hits = asyncio.run(
        SqlKeywordPolicyRetriever(repository=FakeRepository).retrieve(
            "청년 지원",
            top_k=2,
            policy_ids=["P100"],
        )
    )

    assert hits == [
        RetrievalHit(
            chunk_id=10,
            document_id=20,
            policy_id=100,
            policy_code="P100",
            policy_name="청년 지원",
            section="지원 내용",
            source_type="POLICY_DETAIL",
            source_title="청년 지원 상세",
            source_url="https://example.com/100",
            evidence_role="benefit",
            metadata={
                "section": "지원 내용",
                "evidence_role": "benefit",
            },
            chunk_text="청년에게 지원금을 지급합니다.",
            score=1.0,
        )
    ]


def test_sql_keyword_retriever_parses_string_metadata():
    hit = SqlKeywordPolicyRetriever._to_hit(
        {
            "chunk_id": 10,
            "document_id": 20,
            "policy_id": 100,
            "policy_code": "P100",
            "policy_name": "청년 지원",
            "chunk_text": "지원 대상 근거",
            "metadata_json": (
                '{"section": "지원 대상", "evidence_role": "target"}'
            ),
            "source_type": "POLICY_DETAIL",
            "source_title": "청년 지원 상세",
            "source_url": "https://example.com/100",
            "keyword_score": 1.0,
        }
    )

    assert hit.section == "지원 대상"
    assert hit.evidence_role == "target"
    assert hit.metadata == {
        "section": "지원 대상",
        "evidence_role": "target",
    }


def test_hybrid_retriever_uses_rrf_and_rewards_shared_hits():
    keyword = StubRetriever([make_hit(1), make_hit(2)])
    vector = StubRetriever([make_hit(2), make_hit(3)])
    retriever = HybridPolicyRetriever(
        keyword_retriever=keyword,
        vector_retriever=vector,
        rrf_k=60,
    )

    hits = asyncio.run(retriever.retrieve("지원", top_k=3, policy_ids=[100]))

    assert [hit.chunk_id for hit in hits] == [2, 1, 3]
    assert hits[0].score > hits[1].score
    assert keyword.calls[0]["top_k"] == 6
    assert vector.calls[0]["policy_ids"] == [100]


def test_factory_exposes_three_retrieval_strategies():
    assert isinstance(
        build_policy_retriever(RetrievalStrategy.SQL_KEYWORD),
        SqlKeywordPolicyRetriever,
    )
    assert isinstance(
        build_policy_retriever(RetrievalStrategy.VECTOR),
        VectorPolicyRetriever,
    )
    assert isinstance(
        build_policy_retriever(RetrievalStrategy.HYBRID),
        HybridPolicyRetriever,
    )


def test_sql_keyword_repository_normalizes_query_and_policy_keys():
    assert PolicyRagRepository._search_keywords(
        "청년 지원, 청년! a 2026"
    ) == ["청년", "지원", "2026"]
    assert PolicyRagRepository._policy_keys(
        [100, "100", " P200 ", "", 100]
    ) == ([100], ["P200"])


def test_policy_chunk_tool_accepts_retriever_layer():
    retriever = StubRetriever([make_hit(1, score=0.9)])

    chunks = asyncio.run(
        search_policy_chunks(
            query="지원 대상",
            policy_ids=[100],
            top_k=1,
            retriever=retriever,
        )
    )

    assert chunks[0].chunk_id == 1
    assert chunks[0].score == 0.9
    assert retriever.calls[0]["query"] == "지원 대상"


def test_policy_summary_graph_passes_injected_retriever(monkeypatch):
    retriever = StubRetriever([])
    captured: dict[str, object] = {}

    class FakeAgent:
        async def summarize(self, policy, evidence_chunks):
            return PolicySummaryGeneration(summary="요약", evidence=[])

    async def fake_search_policy_chunks(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        graph_module,
        "search_policy_chunks",
        fake_search_policy_chunks,
    )

    asyncio.run(
        PolicySummaryGraphRunner(
            agent=FakeAgent(),
            retriever=retriever,
        ).run(
            {
                "policy_id": 100,
                "name": "청년 지원 정책",
                "target_description": "청년을 지원합니다.",
            }
        )
    )

    assert captured["retriever"] is retriever
    assert captured["policy_ids"] == [100]
