import logging
import asyncio
import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.policy_types import LIFE_STAGE_TO_DB
from app.repositories.recommendation_candidate_repository import (
    RecommendationCandidateRepository,
)
from app.services.policy_rag_service import PolicyRagService


CANDIDATE_STATUS_CANDIDATE = "CANDIDATE"
CANDIDATE_STATUS_UNCERTAIN = "UNCERTAIN"
CANDIDATE_STATUS_EXCLUDED = "EXCLUDED"

SPECIAL_RECOMMENDATION_TERMS = {
    "single": "한부모",
    "multi": "다문화",
    "disabled": "장애",
    "many": "다자녀",
    "dual": "맞벌이",
    "low_income": "저소득",
    "veteran": "보훈",
}

INCOME_LEVEL_TO_PERCENT = {
    "low": 50,
    "mid1": 100,
    "mid2": 150,
    "high": 200,
}

LOW_INCOME_POLICY_KEYWORDS = (
    "저소득",
    "기초생활",
    "기초생활수급",
    "생계급여",
    "의료급여",
    "주거급여",
    "교육급여",
    "차상위",
)

INCOME_LIMIT_PATTERNS = (
    re.compile(r"기준\s*중위소득\s*(\d{2,3})\s*%?\s*(?:이하|미만|이내|내)"),
    re.compile(r"중위소득\s*(\d{2,3})\s*%?\s*(?:이하|미만|이내|내)"),
)

# 정책 텍스트에 저소득 관련 키워드만 있고 구체적인 중위소득 % 기준이
# 명시되지 않은 경우 차상위 계층 기준(중위소득 100%)을 잠정 상한으로 적용한다.
DEFAULT_LOW_INCOME_LIMIT_PERCENT = 100.0

# 사용자 소득 구간이 이 값을 초과하면 "저소득층 중심" 정책과는
# 명확히 맞지 않는다고 판단해 제외한다.
LOW_INCOME_POLICY_EXCLUSION_THRESHOLD_PERCENT = 150.0

# 특정 가구 특성(special) 전용 정책을 식별하는 키워드.
# 정책명에 강하게 나타나면 제외(EXCLUDED), 지원대상 본문에만 나타나면
# 추가 확인(NEEDS_CONFIRMATION)으로 처리한다.
SPECIAL_TARGET_NAME_KEYWORDS = {
    "single": ("한부모", "조손"),
    "multi": ("다문화", "탈북", "북한이탈"),
    "disabled": ("장애",),
}
# 소득/수급 자격 기반 특수대상(의료급여·기초생활 등) 식별 키워드.
MEDICAL_LOW_INCOME_TARGET_KEYWORDS = (
    "의료급여",
    "기초생활",
    "차상위",
    "생계급여",
    "주거급여",
    "교육급여",
    "수급권자",
)
SPECIAL_TARGET_LABELS = {
    "single": "한부모/조손 가구",
    "multi": "다문화/탈북 가구",
    "disabled": "장애 가구",
    "low_income": "저소득/수급 자격",
}


@dataclass
class PolicyCandidate:
    policy: Any
    detail: Any
    retrieval_score: float
    candidate_status: str
    filter_match_json: dict[str, Any]
    matched_rules: list[str]

    @property
    def match_score(self) -> float:
        return self.retrieval_score


class RecommendationCandidateService:
    def __init__(
        self,
        repository: RecommendationCandidateRepository | None = None,
        rag_service: PolicyRagService | None = None,
    ) -> None:
        self.logger = logging.getLogger(
            f"{__name__}.RecommendationCandidateService"
        )
        self.repository = repository or RecommendationCandidateRepository()
        self.rag_service = rag_service or PolicyRagService()
        self.vector_search_timeout_seconds = 20

    async def search_candidates(
        self,
        db: AsyncSession,
        condition: dict[str, Any],
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        query_terms = self._query_terms(condition)
        db_rows = await self.repository.find_policy_rows(
            db=db,
            query_terms=query_terms,
            limit=limit,
        )
        vector_matches = await self._find_vector_matches(
            condition=condition,
            query_terms=query_terms,
            limit=limit,
        )
        vector_rows = await self.repository.find_policy_rows_by_ids(
            db=db,
            policy_ids=[
                policy_id
                for policy_id in vector_matches
                if policy_id not in self._policy_ids(db_rows)
            ],
        )
        return (
            self._merge_candidate_rows(
                db_rows=db_rows,
                vector_rows=vector_rows,
                vector_matches=vector_matches,
                query_terms=query_terms,
            ),
            query_terms,
        )

    async def rule_filter_candidates(
        self,
        db: AsyncSession,
        rows: list[dict[str, Any]],
        condition: dict[str, Any],
        query_terms: list[str],
    ) -> list[PolicyCandidate]:
        rules_by_policy = await self.repository.find_policy_rules(
            db,
            [int(row["policy_id"]) for row in rows],
        )
        candidates = [
            self._rule_filter_candidate(
                row=row,
                condition=condition,
                query_terms=query_terms,
                policy_rules=rules_by_policy.get(int(row["policy_id"]), []),
            )
            for row in rows
        ]
        candidates = [
            candidate
            for candidate in candidates
            if self._should_keep_candidate(candidate, query_terms)
        ]
        candidates.sort(
            key=lambda candidate: (
                candidate.candidate_status == CANDIDATE_STATUS_EXCLUDED,
                -candidate.retrieval_score,
            )
        )
        return candidates

    async def save_candidates(
        self,
        db: AsyncSession,
        request_id: int,
        candidates: list[PolicyCandidate],
    ) -> None:
        await self.repository.replace_candidates(db, request_id, candidates)

    async def save_rerank_scores(
        self,
        db: AsyncSession,
        request_id: int,
        rerank_scores: dict[int, float],
    ) -> None:
        await self.repository.update_rerank_scores(db, request_id, rerank_scores)

    async def _find_vector_matches(
        self,
        condition: dict[str, Any],
        query_terms: list[str],
        limit: int,
    ) -> dict[int, dict[str, Any]]:
        query = self._vector_query(condition, query_terms)
        if not query:
            return {}

        matches: dict[int, dict[str, Any]] = {}
        source_weights = {
            "POLICY_DETAIL": 0.3,
            "POLICY_REFERENCE": 0.25,
        }
        for source_type, source_weight in source_weights.items():
            try:
                response = await asyncio.wait_for(
                    self.rag_service.search(
                        query=query,
                        k=max(limit * 2, 30),
                        source_type=source_type,
                    ),
                    timeout=self.vector_search_timeout_seconds,
                )
            except Exception as exc:
                self.logger.warning(
                    "Vector candidate search failed for %s: %s",
                    source_type,
                    exc,
                )
                continue

            for result in response.results:
                if result.policy_id is None:
                    continue
                policy_id = int(result.policy_id)
                score = self._distance_to_score(result.distance)
                policy_match = matches.setdefault(
                    policy_id,
                    {
                        "score": 0.0,
                        "sources": {},
                    },
                )
                source_match = policy_match["sources"].setdefault(
                    source_type,
                    {
                        "score": 0.0,
                        "weighted_score": 0.0,
                        "chunks": [],
                    },
                )
                source_match["score"] = max(source_match["score"], score)
                source_match["weighted_score"] = max(
                    source_match["weighted_score"],
                    round(score * source_weight, 4),
                )
                if len(source_match["chunks"]) < 3:
                    source_match["chunks"].append(
                        {
                            "chunk_id": result.chunk_id,
                            "document_id": result.document_id,
                            "section": result.section,
                            "source_type": result.source_type,
                            "source_url": result.source_url,
                            "score": round(score, 4),
                        }
                    )

        for policy_match in matches.values():
            weighted_score = sum(
                source["weighted_score"]
                for source in policy_match["sources"].values()
            )
            policy_match["score"] = min(round(weighted_score, 4), 0.27)
        return matches

    def _merge_candidate_rows(
        self,
        db_rows: list[dict[str, Any]],
        vector_rows: list[dict[str, Any]],
        vector_matches: dict[int, dict[str, Any]],
        query_terms: list[str],
    ) -> list[dict[str, Any]]:
        rows_by_policy: dict[int, dict[str, Any]] = {}

        for row in db_rows:
            policy_id = int(row["policy_id"])
            candidate_row = dict(row)
            candidate_row["candidate_search"] = {
                "db_keyword": self._db_keyword_match(row, query_terms),
                "vector": vector_matches.get(policy_id, {}),
            }
            rows_by_policy[policy_id] = candidate_row

        for row in vector_rows:
            policy_id = int(row["policy_id"])
            candidate_row = rows_by_policy.setdefault(policy_id, dict(row))
            candidate_row["candidate_search"] = {
                "db_keyword": self._db_keyword_match(row, query_terms, matched=False),
                "vector": vector_matches.get(policy_id, {}),
            }

        for policy_id, candidate_row in rows_by_policy.items():
            candidate_search = candidate_row.get("candidate_search") or {}
            if "vector" not in candidate_search:
                candidate_search["vector"] = vector_matches.get(policy_id, {})
            candidate_search["score"] = self._candidate_search_score(
                candidate_search
            )
            candidate_row["candidate_search"] = candidate_search

        return sorted(
            rows_by_policy.values(),
            key=lambda row: (
                -float(row.get("candidate_search", {}).get("score") or 0.0),
                int(row["policy_id"]),
            ),
        )

    def _rule_filter_candidate(
        self,
        row: dict[str, Any],
        condition: dict[str, Any],
        query_terms: list[str],
        policy_rules: list[dict[str, Any]],
    ) -> PolicyCandidate:
        policy = self._policy_namespace(row)
        detail = self._detail_namespace(row)
        text_value = self._policy_text(row)
        tags = self._string_list(row.get("tags"))
        matched_rules: list[dict[str, Any]] = []
        uncertain_rules: list[dict[str, Any]] = []
        excluded_rules: list[dict[str, Any]] = []
        candidate_search = row.get("candidate_search") or {}
        score = self._candidate_search_score(candidate_search)
        self._apply_candidate_search_rule(candidate_search, matched_rules)

        score += self._apply_stage_rule(condition, text_value, tags, matched_rules)
        score += self._apply_child_age_rule(condition, text_value, matched_rules)
        score += self._apply_region_rule(
            policy,
            condition,
            matched_rules,
            uncertain_rules,
            excluded_rules,
        )
        score += self._apply_income_rule(
            condition,
            text_value,
            matched_rules,
            uncertain_rules,
            excluded_rules,
        )
        score += self._apply_special_rule(condition, text_value, tags, matched_rules)
        score += self._apply_needs_rule(condition, text_value, matched_rules)
        score += self._apply_policy_rules(
            condition=condition,
            policy_rules=policy_rules,
            matched_rules=matched_rules,
            uncertain_rules=uncertain_rules,
            excluded_rules=excluded_rules,
        )
        self._apply_special_target_rule(
            policy=policy,
            target_description=row.get("target_description"),
            condition=condition,
            uncertain_rules=uncertain_rules,
            excluded_rules=excluded_rules,
        )

        candidate_status = self._candidate_status(uncertain_rules, excluded_rules)
        filter_match_json = {
            "matched_rules": matched_rules,
            "uncertain_rules": uncertain_rules,
            "excluded_rules": excluded_rules,
            "score_reason": self._score_reason(
                matched_rules,
                uncertain_rules,
                excluded_rules,
            ),
            "query_terms": query_terms,
            "candidate_search": candidate_search,
        }
        return PolicyCandidate(
            policy=policy,
            detail=detail,
            retrieval_score=(
                0.0
                if candidate_status == CANDIDATE_STATUS_EXCLUDED
                else min(round(score, 4), 1.0)
            ),
            candidate_status=candidate_status,
            filter_match_json=filter_match_json,
            matched_rules=[
                str(rule.get("reason") or rule.get("field") or "")
                for rule in matched_rules
                if rule.get("reason") or rule.get("field")
            ],
        )

    def _vector_query(
        self,
        condition: dict[str, Any],
        query_terms: list[str],
    ) -> str:
        terms = [*query_terms]
        for need in self._string_list(condition.get("needs")):
            terms.extend(self._need_terms(need))

        income = self._first(condition, "income", "income_level", "income_bracket")
        if income and income != "unknown":
            terms.extend(["소득", "중위소득", "지원대상"])

        if not terms:
            return ""
        terms.extend(["지원대상", "선정기준", "신청조건", "복지 혜택"])
        return " ".join(self._deduplicate(terms))

    def _db_keyword_match(
        self,
        row: dict[str, Any],
        query_terms: list[str],
        matched: bool = True,
    ) -> dict[str, Any]:
        if not matched:
            return {
                "matched": False,
                "score": 0.0,
                "matched_terms": [],
            }

        if not query_terms:
            return {
                "matched": True,
                "score": 0.02,
                "matched_terms": [],
            }

        text_value = self._policy_text(row)
        matched_terms = [
            term
            for term in query_terms
            if term and term.lower() in text_value.lower()
        ]
        score = 0.04 + min(len(matched_terms), 4) * 0.01
        return {
            "matched": True,
            "score": round(min(score, 0.08), 4),
            "matched_terms": matched_terms,
        }

    def _candidate_search_score(self, candidate_search: dict[str, Any]) -> float:
        db_keyword = candidate_search.get("db_keyword") or {}
        vector = candidate_search.get("vector") or {}
        db_score = float(db_keyword.get("score") or 0.0)
        vector_score = float(vector.get("score") or 0.0)
        return min(round(db_score + vector_score, 4), 0.35)

    def _apply_candidate_search_rule(
        self,
        candidate_search: dict[str, Any],
        matched_rules: list[dict[str, Any]],
    ) -> None:
        search_score = self._candidate_search_score(candidate_search)
        if search_score <= 0:
            return

        search_sources: list[str] = []
        db_keyword = candidate_search.get("db_keyword") or {}
        if db_keyword.get("matched"):
            search_sources.append("DB_KEYWORD")

        vector_sources = (candidate_search.get("vector") or {}).get("sources") or {}
        search_sources.extend(
            f"VECTOR_{source_type}"
            for source_type in sorted(vector_sources)
        )
        matched_rules.append(
            self._matched_rule(
                "candidate_search",
                search_sources,
                {"score": search_score},
                search_score,
                "DB/RAG 후보 검색 단계에서 포착",
            )
        )

    def _distance_to_score(self, distance: float | None) -> float:
        if distance is None:
            return 0.0
        return round(1 / (1 + max(float(distance), 0.0)), 4)

    def _apply_stage_rule(
        self,
        condition: dict[str, Any],
        text_value: str,
        tags: list[str],
        matched_rules: list[dict[str, Any]],
    ) -> float:
        stage = self._first(condition, "stage", "life_stage", "target_stage")
        stage_label = LIFE_STAGE_TO_DB.get(str(stage)) if stage else None
        if stage_label and (stage_label in text_value or stage_label in tags):
            matched_rules.append(
                self._matched_rule(
                    "stage",
                    stage,
                    stage_label,
                    0.25,
                    "생애주기 조건과 관련",
                )
            )
            return 0.25
        return 0.0

    def _apply_child_age_rule(
        self,
        condition: dict[str, Any],
        text_value: str,
        matched_rules: list[dict[str, Any]],
    ) -> float:
        child_age = self._first(condition, "childAge", "child_age", "child_age_range")
        if child_age and any(
            term in text_value for term in self._child_age_terms(str(child_age))
        ):
            matched_rules.append(
                self._matched_rule(
                    "childAge",
                    child_age,
                    "text",
                    0.15,
                    "자녀 연령 조건과 관련",
                )
            )
            return 0.15
        return 0.0

    def _apply_region_rule(
        self,
        policy: Any,
        condition: dict[str, Any],
        matched_rules: list[dict[str, Any]],
        uncertain_rules: list[dict[str, Any]],
        excluded_rules: list[dict[str, Any]],
    ) -> float:
        region = self._first(condition, "region", "region_code")
        if str(policy.region_scope or "").upper() == "NATIONAL":
            matched_rules.append(
                self._matched_rule("region", region, "NATIONAL", 0.15, "전국 정책")
            )
            return 0.15
        if region and policy.region_code == region:
            matched_rules.append(
                self._matched_rule(
                    "region",
                    region,
                    policy.region_code,
                    0.2,
                    "거주 지역 조건과 일치",
                )
            )
            return 0.2
        if region and policy.region_code:
            excluded_rules.append(
                {
                    "field": "region",
                    "condition_value": region,
                    "policy_value": policy.region_code,
                    "result": "mismatch",
                    "reason": "지역 정책의 region_code가 사용자 지역과 다릅니다.",
                }
            )
            return 0.0
        if region:
            uncertain_rules.append(
                {
                    "field": "region",
                    "condition_value": region,
                    "reason": "정책 지역 정보가 부족해 지역 조건은 추가 확인이 필요합니다.",
                }
            )
        return 0.0

    def _apply_income_rule(
        self,
        condition: dict[str, Any],
        text_value: str,
        matched_rules: list[dict[str, Any]],
        uncertain_rules: list[dict[str, Any]],
        excluded_rules: list[dict[str, Any]],
    ) -> float:
        income = self._first(condition, "income", "income_level", "income_bracket")
        has_income_signal = (
            "소득" in text_value
            or "중위소득" in text_value
            or self._has_low_income_keyword(text_value)
        )
        if not has_income_signal:
            return 0.0
        if not income:
            uncertain_rules.append(
                {
                    "field": "income",
                    "condition_value": None,
                    "reason": "사용자 소득 정보가 없어 정책 소득 기준은 추가 확인 필요",
                }
            )
            return 0.0

        user_income_percent = self._income_percent(income)
        policy_income_limit = self._policy_income_limit_percent(text_value)
        if (
            user_income_percent is not None
            and policy_income_limit is not None
            and user_income_percent > policy_income_limit
        ):
            excluded_rules.append(
                {
                    "field": "income",
                    "condition_value": income,
                    "policy_value": f"중위소득 {policy_income_limit}% 이하",
                    "result": "income_limit_mismatch",
                    "reason": (
                        "사용자 소득 구간이 정책의 중위소득 기준을 초과합니다."
                    ),
                }
            )
            return 0.0

        if (
            user_income_percent is not None
            and user_income_percent > LOW_INCOME_POLICY_EXCLUSION_THRESHOLD_PERCENT
            and self._has_low_income_keyword(text_value)
        ):
            excluded_rules.append(
                {
                    "field": "income",
                    "condition_value": income,
                    "policy_value": "저소득층 중심 정책",
                    "result": "low_income_policy_mismatch",
                    "reason": (
                        "150% 초과 소득 구간은 저소득층 중심 정책과 맞지 않습니다."
                    ),
                }
            )
            return 0.0

        if user_income_percent is not None and policy_income_limit is not None:
            matched_rules.append(
                self._matched_rule(
                    "income",
                    income,
                    f"중위소득 {policy_income_limit}% 이하",
                    0.12,
                    "소득 구간이 정책 기준 범위 안에 있음",
                )
            )
            return 0.12

        if self._has_low_income_keyword(text_value):
            uncertain_rules.append(
                {
                    "field": "income",
                    "condition_value": income,
                    "policy_value": "저소득층 중심 정책",
                    "reason": "정책이 저소득층을 언급하지만 정확한 소득 상한은 추가 확인 필요",
                }
            )
            return 0.0

        uncertain_rules.append(
            {
                "field": "income",
                "condition_value": income,
                "reason": "정책 소득 기준을 텍스트에서 직접 비교하기 어려워 추가 확인 필요",
            }
        )
        return 0.0

    def _apply_special_rule(
        self,
        condition: dict[str, Any],
        text_value: str,
        tags: list[str],
        matched_rules: list[dict[str, Any]],
    ) -> float:
        score = 0.0
        for item in self._string_list(condition.get("special")):
            label = SPECIAL_RECOMMENDATION_TERMS.get(item)
            if label and (label in text_value or label in tags):
                matched_rules.append(
                    self._matched_rule(
                        "special",
                        item,
                        label,
                        0.08,
                        f"가구 특성({label})과 관련",
                    )
                )
                score += 0.08
        return min(score, 0.16)

    def _apply_special_target_rule(
        self,
        policy: Any,
        target_description: Any,
        condition: dict[str, Any],
        uncertain_rules: list[dict[str, Any]],
        excluded_rules: list[dict[str, Any]],
    ) -> None:
        # 가구 특성 전용 정책인데 사용자 특성이 해당하지 않으면 제외/추가확인 처리.
        # 정책명에 강하게 드러나면 제외, 지원대상 본문에만 있으면 추가확인.
        policy_name = str(self._none_if_null(policy.policy_name) or "")
        target_text = str(self._none_if_null(target_description) or "")
        user_special = set(self._string_list(condition.get("special")))

        for flag, keywords in SPECIAL_TARGET_NAME_KEYWORDS.items():
            if flag in user_special:
                continue
            name_hit = any(keyword in policy_name for keyword in keywords)
            target_hit = name_hit or any(keyword in target_text for keyword in keywords)
            if not target_hit:
                continue
            label = SPECIAL_TARGET_LABELS.get(flag, flag)
            if name_hit:
                excluded_rules.append(
                    {
                        "field": "special",
                        "condition_value": sorted(user_special),
                        "policy_value": label,
                        "result": "special_target_mismatch",
                        "reason": (
                            f"{label} 대상 전용 정책으로 보이지만 "
                            "해당 가구 특성이 입력되지 않았습니다."
                        ),
                    }
                )
            else:
                uncertain_rules.append(
                    {
                        "field": "special",
                        "condition_value": sorted(user_special),
                        "policy_value": label,
                        "reason": (
                            f"지원 대상에 {label} 조건이 있어 해당 여부 확인이 필요합니다."
                        ),
                    }
                )

        self._apply_low_income_target_rule(
            policy_name=policy_name,
            target_text=target_text,
            condition=condition,
            user_special=user_special,
            uncertain_rules=uncertain_rules,
            excluded_rules=excluded_rules,
        )

    def _apply_low_income_target_rule(
        self,
        policy_name: str,
        target_text: str,
        condition: dict[str, Any],
        user_special: set[str],
        uncertain_rules: list[dict[str, Any]],
        excluded_rules: list[dict[str, Any]],
    ) -> None:
        income = self._first(condition, "income", "income_level", "income_bracket")
        if "low_income" in user_special or income == "low":
            return  # 저소득/수급 자격이 있으면 강등하지 않는다.

        name_hit = any(
            keyword in policy_name for keyword in MEDICAL_LOW_INCOME_TARGET_KEYWORDS
        )
        target_hit = name_hit or any(
            keyword in target_text for keyword in MEDICAL_LOW_INCOME_TARGET_KEYWORDS
        )
        if not target_hit:
            return

        label = SPECIAL_TARGET_LABELS["low_income"]
        income_known = income not in (None, "", "unknown")
        if name_hit and income_known:
            excluded_rules.append(
                {
                    "field": "income",
                    "condition_value": income,
                    "policy_value": label,
                    "result": "special_target_mismatch",
                    "reason": (
                        f"{label} 대상 정책으로 보이며 사용자 소득/수급 자격이 "
                        "해당하지 않습니다."
                    ),
                }
            )
        else:
            uncertain_rules.append(
                {
                    "field": "income",
                    "condition_value": income,
                    "policy_value": label,
                    "reason": (
                        f"{label} 관련 정책으로 보여 수급 자격 확인이 필요합니다."
                    ),
                }
            )

    def _apply_needs_rule(
        self,
        condition: dict[str, Any],
        text_value: str,
        matched_rules: list[dict[str, Any]],
    ) -> float:
        score = 0.0
        for need in self._string_list(condition.get("needs")):
            if any(term in text_value for term in self._need_terms(need)):
                matched_rules.append(
                    self._matched_rule(
                        "needs",
                        need,
                        "policy_text",
                        0.08,
                        f"요청 관심사({need})와 관련",
                    )
                )
                score += 0.08
        return min(score, 0.16)

    def _apply_policy_rules(
        self,
        condition: dict[str, Any],
        policy_rules: list[dict[str, Any]],
        matched_rules: list[dict[str, Any]],
        uncertain_rules: list[dict[str, Any]],
        excluded_rules: list[dict[str, Any]],
    ) -> float:
        score = 0.0
        for rule in policy_rules:
            field_name = str(rule.get("field_name") or "")
            condition_value = self._condition_value(condition, field_name)
            rule_value = rule.get("value_json")
            operator = str(rule.get("operator") or "").upper()
            if rule.get("manual_check_required") is True:
                uncertain_rules.append(
                    {
                        "field": field_name,
                        "condition_value": condition_value,
                        "policy_value": rule_value,
                        "reason": (
                            rule.get("manual_check_reason")
                            or "정책 룰 수동 확인 필요"
                        ),
                    }
                )
                continue
            if condition_value in (None, "", []):
                if rule.get("is_hard_filter") is True:
                    uncertain_rules.append(
                        {
                            "field": field_name,
                            "condition_value": condition_value,
                            "policy_value": rule_value,
                            "reason": "사용자 조건이 없어 hard rule은 추가 확인 필요",
                        }
                    )
                continue
            match_result = self._rule_matches(operator, condition_value, rule_value)
            if match_result is True:
                matched_rules.append(
                    self._matched_rule(
                        field_name,
                        condition_value,
                        rule_value,
                        0.05,
                        "policy_rule 조건과 일치",
                    )
                )
                score += 0.05
            elif match_result is None:
                uncertain_rules.append(
                    {
                        "field": field_name,
                        "condition_value": condition_value,
                        "policy_value": rule_value,
                        "reason": "policy_rule 비교 방식이 모호해 추가 확인 필요",
                    }
                )
            elif rule.get("is_hard_filter") is True:
                excluded_rules.append(
                    {
                        "field": field_name,
                        "condition_value": condition_value,
                        "policy_value": rule_value,
                        "result": "hard_rule_mismatch",
                        "reason": "policy_rule hard filter와 사용자 조건이 맞지 않습니다.",
                    }
                )
        return min(score, 0.2)

    def _rule_matches(
        self,
        operator: str,
        condition_value: Any,
        rule_value: Any,
    ) -> bool | None:
        values = self._rule_values(rule_value)
        if operator == "EQ":
            return str(condition_value) in {str(value) for value in values[:1]}
        if operator == "IN":
            if isinstance(condition_value, list):
                return bool(
                    {str(item) for item in condition_value}
                    & {str(value) for value in values}
                )
            return str(condition_value) in {str(value) for value in values}
        if operator in {"GTE", "LTE"}:
            condition_number = self._to_number(condition_value)
            rule_number = self._to_number(values[0] if values else None)
            if condition_number is None or rule_number is None:
                return None
            if operator == "GTE":
                return condition_number >= rule_number
            return condition_number <= rule_number
        if operator == "EXISTS":
            return condition_value not in (None, "", [])
        return None

    def _rule_values(self, rule_value: Any) -> list[Any]:
        if isinstance(rule_value, list):
            return rule_value
        if isinstance(rule_value, dict):
            for key in ("value", "values", "allowed", "in"):
                value = rule_value.get(key)
                if isinstance(value, list):
                    return value
                if value is not None:
                    return [value]
            return list(rule_value.values())
        return [rule_value]

    def _income_percent(self, value: Any) -> float | None:
        if value in (None, "", [], "unknown"):
            return None
        normalized = str(value).strip()
        if normalized in INCOME_LEVEL_TO_PERCENT:
            return float(INCOME_LEVEL_TO_PERCENT[normalized])
        return self._to_number(normalized)

    def _policy_income_limit_percent(self, text_value: str) -> float | None:
        limits: list[float] = []
        for pattern in INCOME_LIMIT_PATTERNS:
            for match in pattern.finditer(text_value):
                number = self._to_number(match.group(1))
                if number is not None:
                    limits.append(number)
        if limits:
            return max(limits)
        if self._has_low_income_keyword(text_value):
            return DEFAULT_LOW_INCOME_LIMIT_PERCENT
        return None

    def _has_low_income_keyword(self, text_value: str) -> bool:
        return any(keyword in text_value for keyword in LOW_INCOME_POLICY_KEYWORDS)

    def _condition_value(self, condition: dict[str, Any], field_name: str) -> Any:
        aliases = {
            "region_code": ("region", "region_code"),
            "region": ("region", "region_code"),
            "life_stage": ("stage", "life_stage", "target_stage"),
            "target_stage": ("stage", "life_stage", "target_stage"),
            "stage": ("stage", "life_stage", "target_stage"),
            "income_level": ("income", "income_level", "income_bracket"),
            "income": ("income", "income_level", "income_bracket"),
            "child_age": ("childAge", "child_age", "child_age_range"),
            "child_age_range": ("childAge", "child_age", "child_age_range"),
            "special": ("special", "special_flags"),
            "special_flags": ("special", "special_flags"),
        }
        return self._first(condition, *(aliases.get(field_name, (field_name,))))

    def _should_keep_candidate(
        self,
        candidate: PolicyCandidate,
        query_terms: list[str],
    ) -> bool:
        if candidate.candidate_status == CANDIDATE_STATUS_EXCLUDED:
            return True
        return bool(
            candidate.matched_rules
            or candidate.filter_match_json["uncertain_rules"]
            or not query_terms
        )

    def _candidate_status(
        self,
        uncertain_rules: list[dict[str, Any]],
        excluded_rules: list[dict[str, Any]],
    ) -> str:
        if excluded_rules:
            return CANDIDATE_STATUS_EXCLUDED
        if uncertain_rules:
            return CANDIDATE_STATUS_UNCERTAIN
        return CANDIDATE_STATUS_CANDIDATE

    def _score_reason(
        self,
        matched_rules: list[dict[str, Any]],
        uncertain_rules: list[dict[str, Any]],
        excluded_rules: list[dict[str, Any]],
    ) -> str:
        if excluded_rules:
            return "명확한 hard rule 불일치로 최종 추천 후보에서 제외합니다."
        if uncertain_rules:
            return "일부 조건은 추가 확인이 필요하지만 후보로 유지합니다."
        if matched_rules:
            return "사용자 조건과 정책 정보가 일부 일치해 후보로 유지합니다."
        return "비교 가능한 기본 후보입니다."

    def _matched_rule(
        self,
        field: str,
        condition_value: Any,
        policy_value: Any,
        score_delta: float,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "field": field,
            "condition_value": condition_value,
            "policy_value": policy_value,
            "result": "match",
            "score_delta": score_delta,
            "reason": reason,
        }

    def _policy_namespace(self, row: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(
            policy_id=int(row["policy_id"]),
            policy_code=self._none_if_null(row.get("policy_code")),
            policy_name=self._none_if_null(row.get("policy_name")),
            main_category=self._none_if_null(row.get("main_category")),
            sub_category=self._none_if_null(row.get("sub_category")),
            provider_name=self._none_if_null(row.get("provider_name")),
            region_scope=self._none_if_null(row.get("region_scope")),
            region_code=self._none_if_null(row.get("region_code")),
            benefit_type=self._none_if_null(row.get("benefit_type")),
        )

    def _detail_namespace(self, row: dict[str, Any]) -> SimpleNamespace:
        return SimpleNamespace(
            easy_summary=row.get("easy_summary"),
            target_description=row.get("target_description"),
            benefit_description=row.get("benefit_description"),
            application_method=row.get("application_method"),
            application_period_text=row.get("application_period_text"),
            caution=row.get("caution"),
        )

    def _policy_text(self, row: dict[str, Any]) -> str:
        values = [
            row.get("policy_name"),
            row.get("main_category"),
            row.get("sub_category"),
            row.get("provider_name"),
            row.get("region_scope"),
            row.get("region_code"),
            row.get("benefit_type"),
            row.get("easy_summary"),
            row.get("target_description"),
            row.get("benefit_description"),
            row.get("application_method"),
            row.get("caution"),
            *self._string_list(row.get("tags")),
        ]
        return " ".join(
            str(cleaned)
            for value in values
            if (cleaned := self._none_if_null(value)) is not None
        )

    def _query_terms(self, condition: dict[str, Any]) -> list[str]:
        terms: list[str] = []
        stage = self._first(condition, "stage", "life_stage", "target_stage")
        stage_label = LIFE_STAGE_TO_DB.get(str(stage)) if stage else None
        if stage_label:
            terms.append(stage_label)
        child_age = self._first(condition, "childAge", "child_age", "child_age_range")
        if child_age:
            terms.extend(self._child_age_terms(str(child_age)))
        for item in self._string_list(condition.get("special")):
            label = SPECIAL_RECOMMENDATION_TERMS.get(item)
            if label:
                terms.append(label)
        for need in self._string_list(condition.get("needs")):
            terms.extend(self._need_terms(need))
        return self._deduplicate(terms)

    def _child_age_terms(self, child_age: str) -> list[str]:
        if child_age == "preborn":
            return ["임신", "출산", "태아"]
        if child_age in {"0", "1", "2-5"}:
            return ["영유아", "아동", "보육", "양육"]
        if child_age == "6-12":
            return ["아동", "초등"]
        if child_age == "13+":
            return ["청소년", "중고등"]
        return [child_age]

    def _need_terms(self, need: str) -> list[str]:
        return [
            token
            for token in need.replace("/", " ").split()
            if len(token.strip()) >= 2
        ][:4]

    def _first(self, condition: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = condition.get(key)
            if value not in (None, "", []):
                return value
        return None

    def _string_list(self, value: Any) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if item not in (None, "")]
        return [str(value)]

    def _deduplicate(self, values: list[str]) -> list[str]:
        deduplicated: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduplicated.append(normalized)
        return deduplicated

    def _to_number(self, value: Any) -> float | None:
        if value in (None, "", []):
            return None
        income_percent = INCOME_LEVEL_TO_PERCENT.get(str(value).strip())
        if income_percent is not None:
            return float(income_percent)
        try:
            return float(str(value).replace("%", "").strip())
        except ValueError:
            return None

    def _policy_ids(self, rows: list[dict[str, Any]]) -> set[int]:
        return {int(row["policy_id"]) for row in rows}

    def _none_if_null(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().upper() == "NULL":
            return None
        return value
