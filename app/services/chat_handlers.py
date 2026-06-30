import sys

from app.services.chat import chat_handlers as _chat_handlers

sys.modules[__name__] = _chat_handlers
