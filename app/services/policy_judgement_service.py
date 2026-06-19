import logging
from typing import Annotated

from fastapi import Depends
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.common.ai_status import (
    AssessmentStatus,
    UserStatus,
    map_assessment_to_user_status,
)
from app.core.config import settings
from app.schemas.policy_judgement_schema import (
    PolicyJudgementRequest,
    PolicyJudgementResponse,
)
from app.schemas.policy_rag_schema import PolicyRagSearchResult
from app.services.policy_rag_service import PolicyRagService


class _PolicyJudgementModel(BaseModel):
    assessment_status: AssessmentStatus = Field(
        description="정책 근거와 사용자 조건을 비교한 내부 판단 상태"
    )
    summary: str = Field(description="판단 결과 한 줄 요약")
    answer: str = Field(description="사용자에게 전달할 자연어 답변")
    reasons: list[str] = Field(description="판단에 사용한 핵심 이유")
    missing_information: list[str] = Field(
        description="추가 확인이 필요한 사용자 조건이나 정책 정보"
    )


class PolicyJudgementService:
    def __init__(self) -> None:
        self.logger = logging.getLogger(f"{__name__}.PolicyJudgementService")
        self.rag_service = PolicyRagService()

    async def judge(
        self,
        request: PolicyJudgementRequest,
    ) -> PolicyJudgementResponse:
        search_response = await self.rag_service.search(
            query=self._build_search_query(request),
            k=request.k,
            source_type=request.source_type,
        )

        if not search_response.results:
            return PolicyJudgementResponse(
                question=request.question,
                user_status=UserStatus.NEEDS_CONFIRMATION,
                answer="질문과 관련된 정책 근거를 찾지 못했습니다. 정책명이나 조건을 더 구체적으로 입력해 주세요.",
                summary="관련 정책 근거를 찾지 못했습니다.",
                reasons=[],
                missing_information=["판단에 사용할 정책 근거"],
                evidence_chunks=[],
            )

        judgement = await self._generate_judgement(
            request=request,
            evidence_chunks=search_response.results,
        )

        return PolicyJudgementResponse(
            question=request.question,
            user_status=map_assessment_to_user_status(judgement.assessment_status),
            answer=judgement.answer,
            summary=judgement.summary,
            reasons=judgement.reasons,
            missing_information=judgement.missing_information,
            evidence_chunks=search_response.results,
        )

    async def _generate_judgement(
        self,
        request: PolicyJudgementRequest,
        evidence_chunks: list[PolicyRagSearchResult],
    ) -> _PolicyJudgementModel:
        llm = self._llm().with_structured_output(
            _PolicyJudgementModel,
            method="function_calling",
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content=self._system_prompt()),
                HumanMessage(
                    content=self._user_prompt(
                        request=request,
                        evidence_chunks=evidence_chunks,
                    )
                ),
            ]
        )
        return response

    def _llm(self) -> ChatOpenAI:
        kwargs = {}
        if settings.openai_api_key:
            kwargs["api_key"] = settings.openai_api_key
        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            **kwargs,
        )

    def _build_search_query(self, request: PolicyJudgementRequest) -> str:
        if request.user_context:
            return f"{request.question}\n사용자 조건: {request.user_context}"
        return request.question

    def _system_prompt(self) -> str:
        return """
당신은 복지 정책 판단/비교를 돕는 백엔드 Agent입니다.
반드시 제공된 정책 근거 chunk 안의 내용만 사용하세요.
근거에 없는 내용은 추측하지 말고 추가 확인이 필요하다고 판단하세요.

assessment_status 선택 기준:
- LIKELY_MATCH: 사용자 조건과 정책 근거가 대체로 부합합니다.
- NEEDS_MORE_INFO: 정책 근거는 있으나 사용자 조건이 부족해 추가 확인이 필요합니다.
- NOT_MATCH: 정책 근거상 사용자 조건과 명확히 맞지 않습니다.
- INSUFFICIENT_PROFILE: 사용자 조건이 거의 없어 판단하기 어렵습니다.
- CONFLICTING_PROFILE: 사용자 조건끼리 충돌하거나 근거와 충돌합니다.

답변은 한국어로 작성하고, 이유는 근거 chunk의 정책명/섹션 내용에 연결해 설명하세요.
""".strip()

    def _user_prompt(
        self,
        request: PolicyJudgementRequest,
        evidence_chunks: list[PolicyRagSearchResult],
    ) -> str:
        evidence_text = "\n\n".join(
            self._format_evidence(index=index, chunk=chunk)
            for index, chunk in enumerate(evidence_chunks, start=1)
        )
        return f"""
질문:
{request.question}

사용자 조건:
{request.user_context or "제공되지 않음"}

정책 근거 chunk:
{evidence_text}
""".strip()

    def _format_evidence(
        self,
        index: int,
        chunk: PolicyRagSearchResult,
    ) -> str:
        return f"""
[근거 {index}]
정책명: {chunk.policy_name}
정책코드: {chunk.policy_code}
섹션: {chunk.section}
출처유형: {chunk.source_type}
내용:
{chunk.chunk_text}
""".strip()


PolicyJudgementServiceDep = Annotated[
    PolicyJudgementService,
    Depends(PolicyJudgementService),
]
