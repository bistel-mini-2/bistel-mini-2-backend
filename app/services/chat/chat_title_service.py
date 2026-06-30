import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.repositories.chat_repository import ChatRepository


logger = logging.getLogger(__name__)


_LLM_MODEL = "gpt-5.4-mini"
TITLE_MAX_LENGTH = 10
FALLBACK_TITLE = "새 상담"

_TITLE_SYSTEM_PROMPT = """사용자의 첫 챗봇 메시지를 한국어로 짧게 요약한 세션 제목을 만드세요.
- 명사형/명사구로 작성하세요.
- 10자 이내.
- 따옴표·마침표·콜론 등 부호 없이.
- 한 줄.
- 의미를 알 수 없으면 빈 문자열로 응답하세요."""


def _llm() -> ChatOpenAI:
    kwargs: dict = {"model": _LLM_MODEL, "temperature": 0.2}
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    return ChatOpenAI(**kwargs)


def _sanitize(raw: str) -> str:
    first_line = (raw or "").splitlines()[0] if raw else ""
    cleaned = first_line.strip().strip("\"'`")
    if len(cleaned) > TITLE_MAX_LENGTH:
        cleaned = cleaned[:TITLE_MAX_LENGTH]
    return cleaned


async def generate_session_title(user_content: str) -> str:
    content = (user_content or "").strip()
    if not content:
        return FALLBACK_TITLE
    try:
        response = await _llm().ainvoke(
            [
                SystemMessage(content=_TITLE_SYSTEM_PROMPT),
                HumanMessage(content=content),
            ]
        )
        raw = response.content if isinstance(response.content, str) else str(response.content)
        title = _sanitize(raw)
        return title or FALLBACK_TITLE
    except Exception:
        logger.exception("Chat session title generation failed")
        return FALLBACK_TITLE


async def assign_title_if_missing(chat_session_id: int, user_content: str) -> None:
    """세션 제목이 비어있을 때만 LLM으로 제목을 생성해 저장한다.

    호출자 트랜잭션과 분리하기 위해 별도 DB 세션을 연다.
    """
    async with AsyncSessionLocal() as db:
        try:
            session = await ChatRepository.find_session_by_id(db, chat_session_id)
            if session is None or session.title:
                return
            title = await generate_session_title(user_content)
            await ChatRepository.update_title_if_missing(db, chat_session_id, title)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "Failed to assign chat session title: %s", chat_session_id
            )
