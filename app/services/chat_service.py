import sys

from app.services.chat import chat_service as _chat_service


sys.modules[__name__] = _chat_service
