import sys

from app.services.chat import chat_title_service as _chat_title_service


sys.modules[__name__] = _chat_title_service
