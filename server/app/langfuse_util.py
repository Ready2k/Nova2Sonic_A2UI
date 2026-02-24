import os
from langfuse.langchain import CallbackHandler

def get_langfuse_callback():
    """
    Returns an unconfigured Langfuse CallbackHandler.
    Session and User IDs should be passed via Langchain config metadata:
    config={"metadata": {"langfuse_session_id": "...", "langfuse_user_id": "..."}}
    """
    # Keys are picked up from environment variables:
    # LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
    return CallbackHandler()
