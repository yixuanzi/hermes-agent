from aegis.backend.chat.runtime import AegisChatInputAdapter


def test_aegis_chat_input_read_line_timeout_returns_none_without_input():
    input_adapter = AegisChatInputAdapter()

    assert input_adapter.read_line(timeout=0.001) is None


def test_aegis_chat_input_read_line_timeout_returns_pushed_line():
    input_adapter = AegisChatInputAdapter()

    assert input_adapter.push_line("follow up") is True
    assert input_adapter.read_line(timeout=0.001) == "follow up"


def test_aegis_chat_input_read_line_timeout_returns_none_after_close():
    input_adapter = AegisChatInputAdapter()

    input_adapter.close()

    assert input_adapter.read_line(timeout=0.001) is None
