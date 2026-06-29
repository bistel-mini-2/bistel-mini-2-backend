import asyncio
from collections import defaultdict


_cancel_events: dict[int, set[asyncio.Event]] = defaultdict(set)


def register(chat_session_id: int) -> asyncio.Event:
    event = asyncio.Event()
    _cancel_events[chat_session_id].add(event)
    return event


def unregister(chat_session_id: int, event: asyncio.Event) -> None:
    events = _cancel_events.get(chat_session_id)
    if not events:
        return
    events.discard(event)
    if not events:
        _cancel_events.pop(chat_session_id, None)


def cancel(chat_session_id: int) -> None:
    for event in list(_cancel_events.get(chat_session_id, ())):
        event.set()


def cancel_many(chat_session_ids: list[int]) -> None:
    for chat_session_id in chat_session_ids:
        cancel(chat_session_id)
