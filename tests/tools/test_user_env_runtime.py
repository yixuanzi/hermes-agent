import queue
import threading


def test_user_env_identity_propagates_to_worker_thread():
    from tools.thread_context import propagate_context_to_thread
    from tools.user_env_runtime import (
        get_current_user_env_identity,
        reset_current_user_env_identity,
        set_current_user_env_identity,
    )

    result_queue = queue.Queue()

    def worker():
        identity = get_current_user_env_identity()
        result_queue.put(identity.user_key if identity else None)

    token = set_current_user_env_identity("slack", "u123", "alice")
    try:
        thread = threading.Thread(target=propagate_context_to_thread(worker))
        thread.start()
        thread.join(timeout=5)
    finally:
        reset_current_user_env_identity(token)

    assert result_queue.get(timeout=1) == "slack.u123"
