import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status

from app.ai.nodes.chat.chat_nodes import BRANCH_LLM_TAG
from app.common.exceptions import AppException
from app.db.models.chat_message import ChatMessage
from app.db.models.chat_session import ChatSession
from app.repositories.chat_repository import ChatRepository
from app.repositories.policy_repository import PolicyRepository
from app.services import chat_service as chat_service_module
from app.services.chat_service import ChatService


def _session(user_id: int = 1, chat_session_id: int = 10) -> ChatSession:
    session = ChatSession(
        chat_session_id=chat_session_id,
        user_id=user_id,
        title=None,
    )
    return session


def _new_message(chat_message_id: int) -> ChatMessage:
    msg = ChatMessage(
        chat_message_id=chat_message_id,
        chat_session_id=10,
        role="user",
        message_type="TEXT",
        content="x",
        sequence_no=1,
    )
    return msg


def _graph_result(
    *,
    intent: str = "recommend",
    policies: list[dict] | None = None,
    evidences: list[dict] | None = None,
    policy_links: list[dict] | None = None,
) -> dict:
    policies = policies if policies is not None else [
        {
            "policy_id": "WLF1",
            "slug": "WLF1",
            "policy_name": "정책1",
            "summary": None,
            "tag": None,
            "tagTone": None,
        },
    ]
    evidences = evidences if evidences is not None else [
        {
            "chunk_id": 101,
            "snippet": "근거1",
            "source_title": "정책1",
            "source_url": "https://example.com/1",
            "evidence_role": None,
        },
    ]
    policy_links = policy_links if policy_links is not None else [
        {"policy_slug": "WLF1", "action_type": "RECOMMENDED"},
    ]
    api_action = {
        "recommend": "recommend",
        "compare": "compare",
        "eligibility": "eligibility",
        "apply": "apply",
        "policy_summary": "chat",
        "unclear": None,
    }.get(intent)
    return {
        "assistant_payload": {
            "content": "테스트 답변",
            "user_status": None,
            "sources": [],
            "policies": policies,
            "evidences": evidences,
            "actions": [api_action] if api_action else [],
            "disclaimer": intent != "unclear",
        },
        "supervisor_decision": {"intent": intent, "raw": "{}"},
        "evidences_to_save": [
            {
                "chunk_id": ev["chunk_id"],
                "snippet": ev.get("snippet"),
                "evidence_role": ev.get("evidence_role"),
            }
            for ev in evidences
            if ev.get("chunk_id") is not None
        ],
        "policy_links_to_save": policy_links,
    }


def _patch_repo_for_send(monkeypatch, *, session: ChatSession) -> dict[str, AsyncMock]:
    saved_messages: list[ChatMessage] = []

    async def fake_save_message(db, message: ChatMessage) -> ChatMessage:
        message.chat_message_id = 100 + len(saved_messages)
        saved_messages.append(message)
        return message

    mocks = {
        "find_session_by_id": AsyncMock(return_value=session),
        "find_recent_messages": AsyncMock(return_value=[]),
        "find_recent_assistant_policy": AsyncMock(return_value=None),
        "next_sequence_no": AsyncMock(side_effect=[1, 2]),
        "save_message": AsyncMock(side_effect=fake_save_message),
        "update_last_message_at": AsyncMock(),
        "bulk_save_message_policies": AsyncMock(),
        "bulk_save_message_evidences": AsyncMock(),
        "update_session_slot": AsyncMock(),
        "session_exists": AsyncMock(return_value=True),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(ChatRepository, name, mock)
    mocks["_saved_messages"] = saved_messages
    return mocks


def test_send_message_persists_normalized_outputs(monkeypatch) -> None:
    session = _session()
    mocks = _patch_repo_for_send(monkeypatch, session=session)
    recent_policy = {
        "policy_id": 42,
        "slug": "WLF1",
        "policy_name": "정책1",
        "action_type": "RECOMMENDED",
    }
    mocks["find_recent_assistant_policy"].return_value = recent_policy

    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={"WLF1": 42}),
    )
    run_graph = AsyncMock(return_value=_graph_result())
    monkeypatch.setattr(
        chat_service_module,
        "_run_supervisor_graph",
        run_graph,
    )

    response = asyncio.run(
        ChatService.send_message(
            db=AsyncMock(),
            user_id=1,
            chat_session_id=10,
            content="추천해줘",
        )
    )

    # 정규화 INSERT 호출 검증
    mocks["bulk_save_message_policies"].assert_awaited_once()
    policy_args = mocks["bulk_save_message_policies"].await_args.args
    policy_rows = policy_args[1]
    assert policy_rows == [{
        "chat_message_id": 101,
        "policy_id": 42,
        "action_type": "RECOMMENDED",
    }]

    mocks["bulk_save_message_evidences"].assert_awaited_once()
    evidence_rows = mocks["bulk_save_message_evidences"].await_args.args[1]
    assert evidence_rows == [{
        "chat_message_id": 101,
        "chunk_id": 101,
        "snippet": "근거1",
        "evidence_role": None,
    }]

    # 응답에 action_type / chunk_id 포함
    assistant = response.assistant_message
    assert assistant.policies[0].action_type == "RECOMMENDED"
    assert assistant.policies[0].policy_id == "42"
    assert assistant.evidences[0].chunk_id == "101"
    run_graph.assert_awaited_once()
    assert run_graph.await_args.kwargs["recent_assistant_policy"] == recent_policy

    # structured_json에는 복원 시 카드 메타가 유지되도록 policies 원본 payload를 보관한다.
    assistant_msg_obj = mocks["_saved_messages"][1]
    assert assistant_msg_obj.role == "assistant"
    assert assistant_msg_obj.structured_json["policies"] == _graph_result()["assistant_payload"]["policies"]
    assert "evidences" not in assistant_msg_obj.structured_json
    assert assistant_msg_obj.structured_json["_supervisor"]["intent"] == "recommend"
    assert assistant_msg_obj.structured_json["actions"] == ["recommend"]


def test_update_session_title_updates_owned_session(monkeypatch) -> None:
    session = _session(user_id=1, chat_session_id=10)
    updated_at = datetime(2026, 6, 23, 10, 30, 0)

    find_mock = AsyncMock(return_value=session)
    update_mock = AsyncMock(return_value=updated_at)
    db = AsyncMock()
    monkeypatch.setattr(ChatRepository, "find_session_by_id", find_mock)
    monkeypatch.setattr(ChatRepository, "update_title", update_mock)

    response = asyncio.run(
        ChatService.update_session_title(
            db=db,
            user_id=1,
            chat_session_id=10,
            title="수정 제목",
        )
    )

    find_mock.assert_awaited_once()
    update_mock.assert_awaited_once_with(db, 10, "수정 제목")
    assert response.chat_session_id == "10"
    assert response.title == "수정 제목"
    assert response.updated_at == updated_at


def test_update_session_title_raises_404_for_other_user(monkeypatch) -> None:
    session = _session(user_id=2, chat_session_id=10)
    monkeypatch.setattr(
        ChatRepository, "find_session_by_id", AsyncMock(return_value=session),
    )
    update_mock = AsyncMock()
    monkeypatch.setattr(ChatRepository, "update_title", update_mock)

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            ChatService.update_session_title(
                db=AsyncMock(),
                user_id=1,
                chat_session_id=10,
                title="수정 제목",
            )
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    update_mock.assert_not_awaited()


def test_update_session_title_raises_404_for_missing_session(monkeypatch) -> None:
    monkeypatch.setattr(
        ChatRepository, "find_session_by_id", AsyncMock(return_value=None),
    )
    update_mock = AsyncMock()
    monkeypatch.setattr(ChatRepository, "update_title", update_mock)

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            ChatService.update_session_title(
                db=AsyncMock(),
                user_id=1,
                chat_session_id=10,
                title="수정 제목",
            )
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    update_mock.assert_not_awaited()


def test_delete_session_cancels_running_work_and_deletes_owned_session(monkeypatch) -> None:
    session = _session(user_id=1, chat_session_id=10)
    db = AsyncMock()
    find_mock = AsyncMock(return_value=session)
    delete_mock = AsyncMock()
    cancel_mock = MagicMock()
    monkeypatch.setattr(ChatRepository, "find_session_by_id", find_mock)
    monkeypatch.setattr(ChatRepository, "delete_session", delete_mock)
    monkeypatch.setattr(
        chat_service_module.chat_cancel_registry, "cancel", cancel_mock,
    )

    response = asyncio.run(
        ChatService.delete_session(
            db=db,
            user_id=1,
            chat_session_id=10,
        )
    )

    assert response.chat_session_id == "10"
    assert response.deleted is True
    cancel_mock.assert_called_once_with(10)
    delete_mock.assert_awaited_once_with(db, session)
    db.commit.assert_awaited_once()


def test_bulk_delete_sessions_is_all_or_nothing(monkeypatch) -> None:
    db = AsyncMock()
    monkeypatch.setattr(
        ChatRepository,
        "find_sessions_by_user_and_ids",
        AsyncMock(return_value=[_session(user_id=1, chat_session_id=10)]),
    )
    delete_mock = AsyncMock()
    cancel_many_mock = MagicMock()
    monkeypatch.setattr(ChatRepository, "delete_sessions_by_ids", delete_mock)
    monkeypatch.setattr(
        chat_service_module.chat_cancel_registry, "cancel_many", cancel_many_mock,
    )

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            ChatService.bulk_delete_sessions(
                db=db,
                user_id=1,
                chat_session_ids=[10, 11],
            )
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    cancel_many_mock.assert_not_called()
    delete_mock.assert_not_awaited()
    db.commit.assert_not_awaited()


def test_bulk_delete_sessions_cancels_and_deletes_all_owned_sessions(monkeypatch) -> None:
    db = AsyncMock()
    sessions = [
        _session(user_id=1, chat_session_id=10),
        _session(user_id=1, chat_session_id=11),
    ]
    monkeypatch.setattr(
        ChatRepository,
        "find_sessions_by_user_and_ids",
        AsyncMock(return_value=sessions),
    )
    monkeypatch.setattr(
        ChatRepository,
        "delete_sessions_by_ids",
        AsyncMock(return_value=2),
    )
    cancel_many_mock = MagicMock()
    monkeypatch.setattr(
        chat_service_module.chat_cancel_registry, "cancel_many", cancel_many_mock,
    )

    response = asyncio.run(
        ChatService.bulk_delete_sessions(
            db=db,
            user_id=1,
            chat_session_ids=[10, 11, 10],
        )
    )

    assert response.deleted_count == 2
    assert response.deleted_session_ids == ["10", "11"]
    cancel_many_mock.assert_called_once_with([10, 11])
    ChatRepository.delete_sessions_by_ids.assert_awaited_once_with(db, [10, 11])
    db.commit.assert_awaited_once()


def test_send_message_raises_404_without_assistant_save_when_session_deleted_midflight(monkeypatch) -> None:
    session = _session()
    mocks = _patch_repo_for_send(monkeypatch, session=session)
    mocks["session_exists"].return_value = False
    db = AsyncMock()

    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={"WLF1": 42}),
    )
    monkeypatch.setattr(
        chat_service_module,
        "_run_supervisor_graph",
        AsyncMock(return_value=_graph_result()),
    )

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            ChatService.send_message(
                db=db,
                user_id=1,
                chat_session_id=10,
                content="추천해줘",
            )
        )

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    assert len(mocks["_saved_messages"]) == 1
    mocks["bulk_save_message_policies"].assert_not_awaited()
    mocks["bulk_save_message_evidences"].assert_not_awaited()
    mocks["update_last_message_at"].assert_not_awaited()
    db.rollback.assert_awaited_once()


def test_send_message_skips_unknown_policy_slug(monkeypatch) -> None:
    session = _session()
    mocks = _patch_repo_for_send(monkeypatch, session=session)

    # find_ids_by_codes가 둘 중 하나만 반환
    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={"WLF_KNOWN": 7}),
    )
    monkeypatch.setattr(
        chat_service_module,
        "_run_supervisor_graph",
        AsyncMock(return_value=_graph_result(
            policy_links=[
                {"policy_slug": "WLF_KNOWN", "action_type": "RECOMMENDED"},
                {"policy_slug": "WLF_UNKNOWN", "action_type": "RECOMMENDED"},
            ],
        )),
    )

    asyncio.run(
        ChatService.send_message(
            db=AsyncMock(), user_id=1, chat_session_id=10, content="x",
        )
    )

    policy_rows = mocks["bulk_save_message_policies"].await_args.args[1]
    assert len(policy_rows) == 1
    assert policy_rows[0]["policy_id"] == 7


def test_send_message_policy_summary_creates_no_policy_link(monkeypatch) -> None:
    session = _session()
    mocks = _patch_repo_for_send(monkeypatch, session=session)

    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        chat_service_module,
        "_run_supervisor_graph",
        AsyncMock(return_value=_graph_result(
            intent="policy_summary",
            policy_links=[],  # policy_summary intent는 graph가 빈 배열을 반환
        )),
    )

    response = asyncio.run(
        ChatService.send_message(
            db=AsyncMock(), user_id=1, chat_session_id=10, content="이게 뭐야?",
        )
    )

    # bulk_save_message_policies는 빈 리스트로 호출 (Repository 안에서 early return)
    policy_rows = mocks["bulk_save_message_policies"].await_args.args[1]
    assert policy_rows == []

    # 응답 actions는 chat (API 매핑)
    assert response.assistant_message.actions == ["chat"]
    # policy 카드는 graph가 정책 정보 자체는 노출했으나 action_type은 None
    assert all(p.action_type is None for p in response.assistant_message.policies)


def test_send_message_fallback_when_graph_fails(monkeypatch) -> None:
    session = _session()
    mocks = _patch_repo_for_send(monkeypatch, session=session)

    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={}),
    )

    async def boom(**kwargs: Any) -> dict:
        # 실제 _run_supervisor_graph는 예외를 잡아 fallback dict를 반환하므로 그 동작을 흉내
        return {
            "assistant_payload": chat_service_module._fallback_payload(),
            "supervisor_decision": {"intent": "unclear", "raw": "graph_error"},
            "evidences_to_save": [],
            "policy_links_to_save": [],
        }

    monkeypatch.setattr(chat_service_module, "_run_supervisor_graph", boom)

    response = asyncio.run(
        ChatService.send_message(
            db=AsyncMock(), user_id=1, chat_session_id=10, content="x",
        )
    )

    assert "죄송합니다" in response.assistant_message.content
    mocks["bulk_save_message_policies"].assert_awaited_once_with(
        mocks["bulk_save_message_policies"].await_args.args[0], [],
    )
    mocks["bulk_save_message_evidences"].assert_awaited_once_with(
        mocks["bulk_save_message_evidences"].await_args.args[0], [],
    )


def test_send_message_schedules_title_generation_for_first_message(monkeypatch) -> None:
    session = _session()
    _patch_repo_for_send(monkeypatch, session=session)

    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={"WLF1": 42}),
    )
    monkeypatch.setattr(
        chat_service_module,
        "_run_supervisor_graph",
        AsyncMock(return_value=_graph_result()),
    )
    schedule_mock = MagicMock()
    monkeypatch.setattr(
        chat_service_module, "_schedule_title_generation", schedule_mock,
    )

    asyncio.run(
        ChatService.send_message(
            db=AsyncMock(), user_id=1, chat_session_id=10, content="추천해줘",
        )
    )

    schedule_mock.assert_called_once_with(10, "추천해줘")


def test_send_message_skips_title_generation_when_title_exists(monkeypatch) -> None:
    session = _session()
    session.title = "기존 제목"
    _patch_repo_for_send(monkeypatch, session=session)

    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={"WLF1": 42}),
    )
    monkeypatch.setattr(
        chat_service_module,
        "_run_supervisor_graph",
        AsyncMock(return_value=_graph_result()),
    )
    schedule_mock = MagicMock()
    monkeypatch.setattr(
        chat_service_module, "_schedule_title_generation", schedule_mock,
    )

    asyncio.run(
        ChatService.send_message(
            db=AsyncMock(), user_id=1, chat_session_id=10, content="추천해줘",
        )
    )

    schedule_mock.assert_not_called()


def test_send_message_skips_title_generation_when_history_not_empty(monkeypatch) -> None:
    session = _session()
    mocks = _patch_repo_for_send(monkeypatch, session=session)
    # 첫 메시지가 아니라 이미 이전 대화가 있는 상태
    mocks["find_recent_messages"].return_value = [
        ChatMessage(
            chat_message_id=99,
            chat_session_id=10,
            role="user",
            message_type="TEXT",
            content="이전 질문",
            sequence_no=1,
        )
    ]

    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={"WLF1": 42}),
    )
    monkeypatch.setattr(
        chat_service_module,
        "_run_supervisor_graph",
        AsyncMock(return_value=_graph_result()),
    )
    schedule_mock = MagicMock()
    monkeypatch.setattr(
        chat_service_module, "_schedule_title_generation", schedule_mock,
    )

    asyncio.run(
        ChatService.send_message(
            db=AsyncMock(), user_id=1, chat_session_id=10, content="추가 질문",
        )
    )

    schedule_mock.assert_not_called()


def test_list_messages_includes_normalized_data(monkeypatch) -> None:
    session = _session()

    def make_msg(msg_id: int, role: str, seq: int) -> ChatMessage:
        return ChatMessage(
            chat_message_id=msg_id,
            chat_session_id=10,
            role=role,
            message_type="TEXT",
            content=f"msg-{msg_id}",
            sequence_no=seq,
        )

    messages = [
        make_msg(201, "user", 1),
        make_msg(202, "assistant", 2),
    ]
    messages[1].structured_json = {
        "policies": [
            {
                "policy_id": "42",
                "slug": "WLF1",
                "policy_name": "정책1",
                "recommendation_request_id": "237",
                "source_ref_id": "237",
                "selected_conditions": {"region": "seoul"},
                "merged_condition_json": {"region": "seoul", "income": "mid1"},
            }
        ],
        "actions": ["recommend"],
    }

    monkeypatch.setattr(
        ChatRepository, "find_session_by_id", AsyncMock(return_value=session),
    )
    monkeypatch.setattr(
        ChatRepository, "find_messages_by_session", AsyncMock(return_value=messages),
    )
    monkeypatch.setattr(
        ChatRepository,
        "find_policies_by_message_ids",
        AsyncMock(return_value={
            202: [
                {
                    "policy_id": "42",
                    "slug": "WLF1",
                    "policy_name": "정책1",
                    "summary": "정책 설명",
                    "action_type": "RECOMMENDED",
                },
            ],
        }),
    )
    monkeypatch.setattr(
        ChatRepository,
        "find_evidences_by_message_ids",
        AsyncMock(return_value={
            202: [
                {
                    "chunk_id": "101",
                    "snippet": "근거1",
                    "evidence_role": "SUMMARY",
                    "source_title": "정책1",
                    "source_url": "https://example.com/1",
                },
            ],
        }),
    )

    response = asyncio.run(
        ChatService.list_messages(
            db=AsyncMock(), user_id=1, chat_session_id=10,
        )
    )

    assert len(response.messages) == 2
    user_item = response.messages[0]
    assert user_item.role == "user"
    assert user_item.policies == []
    assert user_item.evidences == []

    assistant_item = response.messages[1]
    assert assistant_item.role == "assistant"
    assert len(assistant_item.policies) == 1
    assert assistant_item.policies[0].action_type == "RECOMMENDED"
    assert assistant_item.policies[0].summary == "정책 설명"
    assert assistant_item.policies[0].recommendation_request_id == "237"
    assert assistant_item.policies[0].source_ref_id == "237"
    assert assistant_item.policies[0].selected_conditions == {"region": "seoul"}
    assert assistant_item.policies[0].merged_condition_json == {
        "region": "seoul",
        "income": "mid1",
    }
    assert len(assistant_item.evidences) == 1
    # evidence_role validator로 lowercase 변환
    assert assistant_item.evidences[0].evidence_role == "summary"
    assert assistant_item.evidences[0].chunk_id == "101"


# --- streaming -------------------------------------------------------------


class _FakeGraph:
    def __init__(self, events: list[dict] | None = None, raise_after: int | None = None) -> None:
        self._events = events or []
        self._raise_after = raise_after
        self.states: list[dict] = []

    def astream_events(self, state: dict, version: str = "v2"):
        self.states.append(state)
        events = self._events
        raise_after = self._raise_after

        async def gen():
            for index, event in enumerate(events):
                yield event
                if raise_after is not None and index + 1 >= raise_after:
                    raise RuntimeError("boom")

        return gen()


def _token_event(text: str, *, tags: list[str] | None = None) -> dict:
    return {
        "event": "on_chat_model_stream",
        "tags": tags if tags is not None else [BRANCH_LLM_TAG],
        "data": {"chunk": SimpleNamespace(content=text)},
    }


def _chain_end_event(output: dict) -> dict:
    return {"event": "on_chain_end", "data": {"output": output}}


def _parse_sse_chunks(chunks: list[str]) -> list[dict]:
    parsed: list[dict] = []
    for chunk in chunks:
        assert chunk.startswith("data: ")
        assert chunk.endswith("\n\n")
        parsed.append(json.loads(chunk[len("data: ") : -2]))
    return parsed


async def _collect(agen) -> list[str]:
    return [item async for item in agen]


def test_send_message_stream_emits_tokens_then_done(monkeypatch) -> None:
    session = _session()
    db = AsyncMock()
    mocks = _patch_repo_for_send(monkeypatch, session=session)
    recent_policy = {
        "policy_id": 42,
        "slug": "WLF1",
        "policy_name": "정책1",
        "action_type": "RECOMMENDED",
    }
    mocks["find_recent_assistant_policy"].return_value = recent_policy

    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={"WLF1": 42}),
    )

    graph_result = _graph_result()
    fake_events = [
        _token_event("안녕"),
        _token_event("하세요"),
        # supervisor 등 다른 태그의 토큰은 무시되어야 함
        _token_event("ignored", tags=["supervisor"]),
        _chain_end_event(graph_result),
    ]
    fake_graph = _FakeGraph(events=fake_events)
    monkeypatch.setattr(chat_service_module, "chat_supervisor_graph", fake_graph)

    chunks = asyncio.run(_collect(
        ChatService.send_message_stream(db=db, session=session, content="추천해줘")
    ))
    events = _parse_sse_chunks(chunks)

    # intent, token, token, done 순서 (버퍼링: supervisor_decision 확정 후 intent 먼저, 이후 토큰 flush)
    assert [e["type"] for e in events] == ["intent", "token", "token", "done"]
    assert events[0]["intent"] == "recommendation"
    assert events[1]["delta"] == "안녕"
    assert events[2]["delta"] == "하세요"

    # done payload에 ChatMessageSendResponse 구조 포함
    payload = events[3]["payload"]
    assert payload["chat_session_id"] == "10"
    assert payload["assistant_message"]["content"] == "테스트 답변"
    assert payload["assistant_message"]["policies"][0]["action_type"] == "RECOMMENDED"
    assert fake_graph.states[0]["recent_assistant_policy"] == recent_policy

    # 정규화 INSERT가 한 번만 호출되었는지
    mocks["bulk_save_message_policies"].assert_awaited_once()
    mocks["bulk_save_message_evidences"].assert_awaited_once()
    mocks["update_last_message_at"].assert_awaited_once()


def test_send_message_stream_error_event_when_graph_raises(monkeypatch) -> None:
    session = _session()
    db = AsyncMock()
    mocks = _patch_repo_for_send(monkeypatch, session=session)

    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={}),
    )

    # intent 확정 전 raise — 토큰은 버퍼에만 있고 flush 전에 실패 → error 이벤트만 종료
    monkeypatch.setattr(
        chat_service_module,
        "chat_supervisor_graph",
        _FakeGraph(events=[_token_event("부분")], raise_after=1),
    )

    chunks = asyncio.run(_collect(
        ChatService.send_message_stream(db=db, session=session, content="x")
    ))
    events = _parse_sse_chunks(chunks)

    assert [e["type"] for e in events] == ["error"]
    assert events[0]["code"] == "INTERNAL_SERVER_ERROR"

    # 유저 메시지·assistant 메시지·정규화 row가 모두 저장되지 않아야 함
    # (P1-1 수정: 그래프 실패 시 user message도 남지 않도록 commit을 함께 묶음)
    mocks["save_message"].assert_not_awaited()
    mocks["bulk_save_message_policies"].assert_not_awaited()
    mocks["bulk_save_message_evidences"].assert_not_awaited()
    mocks["update_last_message_at"].assert_not_awaited()
    db.commit.assert_not_awaited()


def test_send_message_stream_cancelled_before_persist_saves_nothing(monkeypatch) -> None:
    session = _session()
    db = AsyncMock()
    mocks = _patch_repo_for_send(monkeypatch, session=session)
    cancel_event = asyncio.Event()
    cancel_event.set()

    monkeypatch.setattr(
        chat_service_module.chat_cancel_registry,
        "register",
        MagicMock(return_value=cancel_event),
    )
    unregister_mock = MagicMock()
    monkeypatch.setattr(
        chat_service_module.chat_cancel_registry,
        "unregister",
        unregister_mock,
    )
    monkeypatch.setattr(
        chat_service_module,
        "chat_supervisor_graph",
        _FakeGraph(events=[
            _token_event("저장되면 안 됨"),
            _chain_end_event(_graph_result()),
        ]),
    )

    chunks = asyncio.run(_collect(
        ChatService.send_message_stream(db=db, session=session, content="x")
    ))
    events = _parse_sse_chunks(chunks)

    assert [e["type"] for e in events] == ["error"]
    assert events[0]["code"] == "NOT_FOUND"
    mocks["save_message"].assert_not_awaited()
    mocks["bulk_save_message_policies"].assert_not_awaited()
    mocks["bulk_save_message_evidences"].assert_not_awaited()
    db.commit.assert_not_awaited()
    unregister_mock.assert_called_once_with(10, cancel_event)


# --- FOLLOW_UP 시나리오 -------------------------------------------------------


def _follow_up_session() -> ChatSession:
    """FOLLOW_UP_REQUIRED 슬롯이 있는 세션."""
    session = _session()
    session.slot_json = {
        "recent_policies": [
            {
                "policy_id": 42,
                "slug": "WLF1",
                "policy_name": "정책1",
                "last_action": "ELIGIBILITY",
                "eligibility_status": "FOLLOW_UP_REQUIRED",
                "eligibility_request_id": 99,
                "follow_up_questions": [{"question_text": "소득이 얼마인가요?"}],
            }
        ]
    }
    return session


def test_follow_up_recommendation_clears_eligibility_slot(monkeypatch) -> None:
    """버그 1: FOLLOW_UP → recommendation 경로 시 슬롯의 eligibility_status가 None으로 클리어."""
    session = _follow_up_session()
    db = AsyncMock()
    mocks = _patch_repo_for_send(monkeypatch, session=session)
    monkeypatch.setattr(PolicyRepository, "find_ids_by_codes", AsyncMock(return_value={"WLF1": 42}))

    monkeypatch.setattr(
        chat_service_module,
        "_classify_follow_up_intent",
        AsyncMock(return_value="recommendation"),
    )

    graph_result = _graph_result()
    monkeypatch.setattr(
        chat_service_module,
        "chat_supervisor_graph",
        _FakeGraph(events=[_chain_end_event(graph_result)]),
    )

    chunks = asyncio.run(_collect(
        ChatService.send_message_stream(db=db, session=session, content="다른 정책 추천해줘")
    ))
    events = _parse_sse_chunks(chunks)

    assert events[0]["type"] == "intent"
    assert events[0]["intent"] == "recommendation"
    assert events[-1]["type"] == "done"

    # 슬롯 저장 호출 확인 — eligibility_status가 None으로 클리어됨
    mocks["update_session_slot"].assert_awaited_once()
    saved_slot = mocks["update_session_slot"].await_args.args[2]
    policy_entry = saved_slot["recent_policies"][0]
    assert policy_entry["slug"] == "WLF1"
    assert policy_entry["eligibility_status"] is None
    assert policy_entry["follow_up_questions"] == []


def test_follow_up_general_uses_outer_db_and_rollback_on_failure(monkeypatch) -> None:
    """버그 2: _run_follow_up_eligibility가 외부 db를 받고, 저장 실패 시 rollback이 호출됨."""
    session = _follow_up_session()
    db = AsyncMock()
    mocks = _patch_repo_for_send(monkeypatch, session=session)
    # 저장 단계에서 실패 시뮬레이션
    mocks["save_message"].side_effect = Exception("저장 실패")

    monkeypatch.setattr(
        chat_service_module,
        "_classify_follow_up_intent",
        AsyncMock(return_value="general"),
    )
    monkeypatch.setattr(
        chat_service_module,
        "_map_follow_up_answers",
        AsyncMock(return_value=[]),
    )
    run_follow_up_mock = AsyncMock(return_value={
        "status": "ELIGIBLE",
        "request_id": 100,
        "user_status": "eligible",
        "assessment_status": None,
        "follow_up_questions": [],
        "summary": None,
        "criteria": [],
        "policies": [],
        "evidences": [],
    })
    monkeypatch.setattr(chat_service_module, "_run_follow_up_eligibility", run_follow_up_mock)
    monkeypatch.setattr(
        chat_service_module,
        "_adapt_eligibility_result",
        MagicMock(return_value=("분석 완료", "eligible", [], [])),
    )

    chunks = asyncio.run(_collect(
        ChatService.send_message_stream(db=db, session=session, content="네, 소득은 200만원입니다")
    ))
    events = _parse_sse_chunks(chunks)

    # 저장 실패 → error SSE
    assert events[-1]["type"] == "error"
    # 외부 db에 rollback 호출됨 (eligibility 분석도 함께 롤백)
    db.rollback.assert_awaited()
    # 핵심: _run_follow_up_eligibility 첫 번째 인자가 외부 db (트랜잭션 통합 검증)
    assert run_follow_up_mock.await_args.args[0] is db


def test_intent_emitted_only_once_when_chain_end_fires_twice(monkeypatch) -> None:
    """버그 3: on_chain_end가 supervisor_decision을 두 번 포함해도 intent SSE는 한 번만."""
    session = _session()
    db = AsyncMock()
    mocks = _patch_repo_for_send(monkeypatch, session=session)
    monkeypatch.setattr(PolicyRepository, "find_ids_by_codes", AsyncMock(return_value={"WLF1": 42}))

    graph_result = _graph_result()
    # 두 번의 on_chain_end — 두 번째도 supervisor_decision 포함 (브랜치 노드가 **state로 spread하는 상황)
    fake_events = [
        _token_event("안녕"),
        _chain_end_event(graph_result),                      # supervisor 노드 완료 → intent 발행
        _token_event("하세요"),
        _chain_end_event(graph_result),                      # 브랜치 노드 완료 → 무시
    ]
    monkeypatch.setattr(
        chat_service_module,
        "chat_supervisor_graph",
        _FakeGraph(events=fake_events),
    )

    chunks = asyncio.run(_collect(
        ChatService.send_message_stream(db=db, session=session, content="추천해줘")
    ))
    events = _parse_sse_chunks(chunks)

    intent_events = [e for e in events if e["type"] == "intent"]
    assert len(intent_events) == 1, "intent SSE는 정확히 한 번만 발행되어야 함"
    assert intent_events[0]["intent"] == "recommendation"


def test_build_next_slot_logs_warning_on_slug_mismatch(caplog) -> None:
    """버그 4: eligibility_slot_update의 slug가 recent_policies에 없으면 경고 로그."""
    import logging
    from app.services.chat_service import _build_next_slot

    with caplog.at_level(logging.WARNING, logger="app.services.chat_service"):
        result = _build_next_slot(
            policy_links=[],
            branch_policies=[],
            slug_to_policy_id={},
            current_slot={
                "recent_policies": [
                    {"policy_id": 1, "slug": "WLF1", "policy_name": "정책1", "last_action": "ELIGIBILITY"},
                ]
            },
            eligibility_slot_update={
                "slug": "NONEXISTENT",   # recent_policies에 없는 slug
                "eligibility_request_id": 99,
                "follow_up_questions": [],
                "eligibility_status": "ELIGIBLE",
            },
        )

    assert any("NONEXISTENT" in r.message for r in caplog.records), \
        "slug 미매칭 시 slug 이름을 포함한 경고 로그가 있어야 함"
    # 슬롯은 변경 없이 유지
    assert result is not None
    assert result["recent_policies"][0]["slug"] == "WLF1"
