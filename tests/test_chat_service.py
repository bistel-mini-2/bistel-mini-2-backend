import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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
        "next_sequence_no": AsyncMock(side_effect=[1, 2]),
        "save_message": AsyncMock(side_effect=fake_save_message),
        "update_last_message_at": AsyncMock(),
        "bulk_save_message_policies": AsyncMock(),
        "bulk_save_message_evidences": AsyncMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(ChatRepository, name, mock)
    mocks["_saved_messages"] = saved_messages
    return mocks


def test_send_message_persists_normalized_outputs(monkeypatch) -> None:
    session = _session()
    mocks = _patch_repo_for_send(monkeypatch, session=session)

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

    # structured_json에 policies/evidences 빠지고 메타만 보관
    assistant_msg_obj = mocks["_saved_messages"][1]
    assert assistant_msg_obj.role == "assistant"
    assert "policies" not in assistant_msg_obj.structured_json
    assert "evidences" not in assistant_msg_obj.structured_json
    assert assistant_msg_obj.structured_json["_supervisor"]["intent"] == "recommend"
    assert assistant_msg_obj.structured_json["actions"] == ["recommend"]


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
    assert len(assistant_item.evidences) == 1
    # evidence_role validator로 lowercase 변환
    assert assistant_item.evidences[0].evidence_role == "summary"
    assert assistant_item.evidences[0].chunk_id == "101"
