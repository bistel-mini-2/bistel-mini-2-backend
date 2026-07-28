import asyncio
from types import SimpleNamespace

from app.ai.retrievers import (
    AdaptivePolicyRetriever,
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
        evidence_role=None,
    ):
        self.calls.append(
            {
                "query": query,
                "top_k": top_k,
                "source_type": source_type,
                "policy_ids": policy_ids,
                "evidence_role": evidence_role,
            }
        )
        return self.hits[:top_k]


def make_hit(
    chunk_id: int,
    score: float = 0.5,
    *,
    policy_id: int = 100,
    section: str | None = None,
    evidence_role: str | None = None,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        policy_id=policy_id,
        chunk_text=f"근거 {chunk_id}",
        source_title="테스트 정책",
        source_url="https://example.com/policy",
        score=score,
        section=section,
        evidence_role=evidence_role,
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


def test_sql_keyword_retriever_recovers_section_from_chunk_text():
    hit = SqlKeywordPolicyRetriever._to_hit(
        {
            "chunk_id": 10,
            "document_id": 20,
            "policy_id": 100,
            "policy_code": "P100",
            "policy_name": "청년 지원",
            "chunk_text": "정책명: 청년 지원\n섹션: 지원 내용\n내용:\n지원금",
            "metadata_json": None,
            "source_type": "POLICY_DETAIL",
            "source_title": "청년 지원 상세",
            "source_url": "https://example.com/100",
            "keyword_score": 1.0,
        }
    )

    assert hit.section == "지원 내용"


def test_hybrid_retriever_uses_weighted_rrf_and_rewards_shared_hits():
    keyword = StubRetriever([make_hit(1), make_hit(2)])
    vector = StubRetriever([make_hit(2), make_hit(3)])
    retriever = HybridPolicyRetriever(
        keyword_retriever=keyword,
        vector_retriever=vector,
        rrf_k=60,
    )

    hits = asyncio.run(retriever.retrieve("지원", top_k=3, policy_ids=[100]))

    assert [hit.chunk_id for hit in hits] == [2, 3, 1]
    assert hits[0].score > hits[1].score
    assert keyword.calls[0]["top_k"] == 6
    assert vector.calls[0]["policy_ids"] == [100]


def test_hybrid_retriever_prioritizes_vector_channel_by_default():
    keyword = StubRetriever([make_hit(1, policy_id=101)])
    vector = StubRetriever([make_hit(2, policy_id=102)])

    hits = asyncio.run(
        HybridPolicyRetriever(
            keyword_retriever=keyword,
            vector_retriever=vector,
        ).retrieve("지원", top_k=2)
    )

    assert [hit.chunk_id for hit in hits] == [2, 1]


def test_hybrid_retriever_limits_duplicate_policies_for_unscoped_search():
    keyword = StubRetriever(
        [
            make_hit(1, policy_id=101),
            make_hit(2, policy_id=101),
            make_hit(3, policy_id=102),
        ]
    )
    vector = StubRetriever(
        [
            make_hit(2, policy_id=101),
            make_hit(4, policy_id=103),
        ]
    )

    hits = asyncio.run(
        HybridPolicyRetriever(
            keyword_retriever=keyword,
            vector_retriever=vector,
        ).retrieve("지원", top_k=4)
    )

    assert [hit.policy_id for hit in hits] == [101, 103, 101, 102]
    assert [hit.policy_id for hit in hits].count(101) == 2


def test_hybrid_retriever_keeps_multiple_chunks_for_scoped_policy():
    keyword = StubRetriever(
        [
            make_hit(1, policy_id=101),
            make_hit(2, policy_id=101),
        ]
    )
    vector = StubRetriever(
        [
            make_hit(2, policy_id=101),
            make_hit(3, policy_id=101),
        ]
    )

    hits = asyncio.run(
        HybridPolicyRetriever(
            keyword_retriever=keyword,
            vector_retriever=vector,
        ).retrieve("지원", top_k=3, policy_ids=[101])
    )

    assert len(hits) == 3
    assert {hit.policy_id for hit in hits} == {101}


def test_adaptive_retriever_keeps_vector_when_requested_role_exists():
    vector = StubRetriever(
        [make_hit(1, section="지원 대상", evidence_role="TARGET")]
    )
    keyword = StubRetriever([make_hit(2)])

    hits = asyncio.run(
        AdaptivePolicyRetriever(
            vector_retriever=vector,
            keyword_retriever=keyword,
        ).retrieve(
            "지원 자격",
            top_k=1,
            policy_ids=[100],
            evidence_role="TARGET",
        )
    )

    assert [hit.chunk_id for hit in hits] == [1]
    assert len(vector.calls) == 1
    assert vector.calls[0]["top_k"] == 1
    assert keyword.calls == []


def test_adaptive_retriever_falls_back_without_requested_role():
    vector = StubRetriever(
        [
            make_hit(1, section="지원 내용"),
            make_hit(3, section="신청 방법"),
        ]
    )
    keyword = StubRetriever(
        [make_hit(2, section="지원 대상", evidence_role="TARGET")]
    )

    hits = asyncio.run(
        AdaptivePolicyRetriever(
            vector_retriever=vector,
            keyword_retriever=keyword,
        ).retrieve(
            "지원 자격",
            top_k=2,
            policy_ids=[100],
            evidence_role="TARGET",
        )
    )

    assert [hit.chunk_id for hit in hits] == [1, 2]
    assert len(vector.calls) == 1
    assert len(keyword.calls) == 1


def test_adaptive_retriever_keeps_relevant_vector_policy_without_diversity():
    vector = StubRetriever(
        [
            make_hit(1, policy_id=101),
            make_hit(2, policy_id=101),
        ]
    )
    keyword = StubRetriever([make_hit(3, policy_id=102)])

    hits = asyncio.run(
        AdaptivePolicyRetriever(
            vector_retriever=vector,
            keyword_retriever=keyword,
        ).retrieve("지원 정책", top_k=2)
    )

    assert {hit.policy_id for hit in hits} == {101}
    assert keyword.calls == []


def test_adaptive_retriever_ignores_non_section_role_when_vector_has_hits():
    vector = StubRetriever([make_hit(1, section="지원 내용")])
    keyword = StubRetriever([make_hit(2)])

    hits = asyncio.run(
        AdaptivePolicyRetriever(
            vector_retriever=vector,
            keyword_retriever=keyword,
        ).retrieve(
            "추천 근거",
            top_k=1,
            policy_ids=[100],
            evidence_role="recommendation_reason",
        )
    )

    assert [hit.chunk_id for hit in hits] == [1]
    assert keyword.calls == []


def test_adaptive_retriever_accepts_requested_role_from_section_metadata():
    vector = StubRetriever(
        [
            make_hit(
                1,
                section="공식 지원대상 원문",
                evidence_role="CAUTION",
            )
        ]
    )
    keyword = StubRetriever([make_hit(2)])

    hits = asyncio.run(
        AdaptivePolicyRetriever(
            vector_retriever=vector,
            keyword_retriever=keyword,
        ).retrieve(
            "지원 자격",
            top_k=1,
            policy_ids=[100],
            evidence_role="TARGET",
        )
    )

    assert [hit.chunk_id for hit in hits] == [1]
    assert keyword.calls == []


def test_adaptive_retriever_keeps_vector_when_fallback_adds_no_role():
    vector = StubRetriever([make_hit(1, section="지원 내용")])
    keyword = StubRetriever([make_hit(2, section="신청 방법")])

    hits = asyncio.run(
        AdaptivePolicyRetriever(
            vector_retriever=vector,
            keyword_retriever=keyword,
        ).retrieve(
            "지원 자격",
            top_k=1,
            policy_ids=[100],
            evidence_role="TARGET",
        )
    )

    assert [hit.chunk_id for hit in hits] == [1]
    assert len(keyword.calls) == 1


def test_factory_exposes_four_retrieval_strategies():
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
    assert isinstance(
        build_policy_retriever(RetrievalStrategy.ADAPTIVE),
        AdaptivePolicyRetriever,
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
