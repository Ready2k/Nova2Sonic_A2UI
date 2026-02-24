import os
from langfuse.callback import CallbackHandler

def get_langfuse_callback(session_id: str, user_id: str = None):
    """
    Returns a Langfuse CallbackHandler configured for the given session.
    """
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    base_url = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        return None

    return CallbackHandler(
        public_key=public_key,
        secret_key=secret_key,
        host=base_url,
        session_id=session_id,
        user_id=user_id
    )
