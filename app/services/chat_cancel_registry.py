import sys

from app.services.chat import chat_cancel_registry as _chat_cancel_registry


sys.modules[__name__] = _chat_cancel_registry
