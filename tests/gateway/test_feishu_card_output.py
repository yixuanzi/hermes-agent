"""Feishu three-element CardKit output coverage.

The card replaces a fan of text/post bubbles with one surface per speaker:
header title, a collapsible execution trace, and a streamed rich-text body.
These tests pin the parts that are easy to regress — which CardKit endpoint
each area is updated through, where a card boundary falls, and that any
CardKit failure still gets the user their reply through the legacy path.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def _ensure_feishu_mocks():
    if importlib.util.find_spec("lark_oapi") is None and "lark_oapi" not in sys.modules:
        mock = MagicMock()
        for name in ("lark_oapi", "lark_oapi.api.im.v1", "lark_oapi.event"):
            sys.modules.setdefault(name, mock)


_ensure_feishu_mocks()

from gateway.config import PlatformConfig  # noqa: E402
from gateway.platforms.base import MessageEvent, ProcessingOutcome, SendResult  # noqa: E402
from plugins.platforms.feishu import feishu_cardkit  # noqa: E402
from plugins.platforms.feishu.adapter import FeishuAdapter  # noqa: E402

CHAT_ID = "oc_card"


# ---------------------------------------------------------------------------
# Fake CardKit surface
# ---------------------------------------------------------------------------

class _FakeBuilder:
    """Accept any ``.field(value)`` chain and materialize a plain dict."""

    def __init__(self):
        self._payload = {}

    def __getattr__(self, name):
        def _setter(value):
            self._payload[name] = value
            return self

        return _setter

    def build(self):
        return dict(self._payload)


class _FakeModel:
    @staticmethod
    def builder():
        return _FakeBuilder()


class _Resp:
    def __init__(self, *, ok=True, code=0, msg="", **data):
        self._ok = ok
        self.code = code
        self.msg = msg
        self.data = SimpleNamespace(**data) if data else None

    def success(self):
        return self._ok


class _FakeCardKit:
    """Records every CardKit call the manager makes."""

    def __init__(self):
        self.creates = []
        self.settings_calls = []
        self.element_updates = []
        self.element_contents = []
        self.element_patches = []
        self.create_fails = False
        #: Error codes to fail the next content updates with, consumed in
        #: order.  Empty = every call succeeds.
        self.content_fail_codes: list = []
        self._next_card = 0

        outer = self

        class _CardApi:
            def create(self, request):
                outer.creates.append(request)
                if outer.create_fails:
                    return _Resp(ok=False, code=200621, msg="type of element is not supported")
                outer._next_card += 1
                return _Resp(card_id=f"card_{outer._next_card}")

            def settings(self, request):
                outer.settings_calls.append(request)
                return _Resp(card_id="ok")

        class _ElementApi:
            def update(self, request):
                outer.element_updates.append(request)
                return _Resp(card_id="ok")

            def content(self, request):
                outer.element_contents.append(request)
                if outer.content_fail_codes:
                    code = outer.content_fail_codes.pop(0)
                    return _Resp(ok=False, code=code, msg=f"injected {code}")
                return _Resp(card_id="ok")

            def patch(self, request):
                outer.element_patches.append(request)
                return _Resp(card_id="ok")

        self.v1 = SimpleNamespace(card=_CardApi(), card_element=_ElementApi())

    # -- readers used by the assertions ---------------------------------
    def created_cards(self):
        return [json.loads(req["request_body"]["data"]) for req in self.creates]

    def trace_texts(self):
        return [
            json.loads(req["request_body"]["element"])["content"]
            for req in self.element_updates
        ]

    def body_texts(self):
        # ``content`` is the element's plain full text — deliberately NOT
        # json-decoded here, so a regression that re-wraps it in
        # {"text": ...} shows up as a failing assertion rather than passing
        # silently and rendering the wrapper in the chat.
        return [req["request_body"]["content"] for req in self.element_contents]

    def settings_payloads(self):
        return [
            json.loads(req["request_body"]["settings"]) for req in self.settings_calls
        ]


@pytest.fixture
def card_adapter(monkeypatch):
    """A connected adapter whose CardKit calls are recorded, not sent."""
    for name in (
        "CreateCardRequest", "CreateCardRequestBody",
        "ContentCardElementRequest", "ContentCardElementRequestBody",
        "UpdateCardElementRequest", "UpdateCardElementRequestBody",
        "PatchCardElementRequest", "PatchCardElementRequestBody",
        "SettingsCardRequest", "SettingsCardRequestBody",
    ):
        monkeypatch.setattr(feishu_cardkit, name, _FakeModel, raising=False)
    monkeypatch.setattr(feishu_cardkit, "load_cardkit", lambda: True)
    # Card copy is env-overridable, and a real deployment's .env sets these.
    # Assert against the defaults, not against whatever the host is configured
    # with, or these tests pass or fail depending on the machine.
    for env_name in (
        "FEISHU_CARD_TITLE", "FEISHU_CARD_DELEGATE_TITLE", "FEISHU_CARD_TRACE_TITLE",
    ):
        monkeypatch.delenv(env_name, raising=False)

    adapter = FeishuAdapter(PlatformConfig(enabled=True))
    cardkit = _FakeCardKit()
    adapter._client = MagicMock()
    adapter._client.cardkit = cardkit
    adapter._card_output_enabled = True
    # Flush immediately so a test does not have to model the coalescing window.
    adapter._card_manager._flush_interval = 0.0

    async def _run_blocking(func, *args):
        # The real _run_blocking hands the blocking SDK call to a thread pool,
        # so it always suspends.  Yield here too: a fixture that never
        # suspends serializes every code path by accident and hides exactly
        # the concurrency bugs these tests exist to catch.
        await asyncio.sleep(0)
        return func(*args)

    async def _deliver_card(**kwargs):
        await asyncio.sleep(0)
        return _Resp(message_id="om_card")

    monkeypatch.setattr(adapter, "_run_blocking", _run_blocking)
    monkeypatch.setattr(
        adapter,
        "_feishu_send_with_retry",
        AsyncMock(side_effect=_deliver_card),
    )
    adapter._cardkit = cardkit
    return adapter


async def _settle():
    """Let the manager's flush task drain."""
    for _ in range(6):
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)


def _begin_turn(adapter, *, thread_id=None):
    adapter._card_manager.begin_turn(chat_id=CHAT_ID, thread_id=thread_id)


def _progress(content):
    return {"hermes_progress": True}


# ---------------------------------------------------------------------------
# Card shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_send_creates_one_three_element_card(card_adapter):
    _begin_turn(card_adapter)

    await card_adapter.send(CHAT_ID, "⚙️ Bash: \"ls\"", metadata={"hermes_progress": True})
    await card_adapter.send(CHAT_ID, "Here is the answer.")
    await _settle()

    cardkit = card_adapter._cardkit
    assert len(cardkit.creates) == 1, "one card per speaker per turn"
    card = cardkit.created_cards()[0]

    assert card["schema"] == "2.0"
    assert card["config"]["streaming_mode"] is True
    assert card["header"]["title"]["content"] == feishu_cardkit.CardCopy().main_title

    elements = card["body"]["elements"]
    panel = elements[0]
    assert panel["tag"] == "collapsible_panel"
    assert panel["element_id"] == feishu_cardkit.PANEL_ELEMENT_ID
    # A panel without header.title is rejected outright (code 10002).
    assert panel["header"]["title"]["content"]
    assert panel["elements"][0]["element_id"] == feishu_cardkit.TRACE_ELEMENT_ID
    assert elements[-1]["element_id"] == feishu_cardkit.BODY_ELEMENT_ID

    # Exactly one interactive message carries the card into the chat.
    deliveries = card_adapter._feishu_send_with_retry.await_args_list
    assert len(deliveries) == 1
    assert deliveries[0].kwargs["msg_type"] == "interactive"
    assert json.loads(deliveries[0].kwargs["payload"]) == {
        "type": "card", "data": {"card_id": "card_1"}
    }


@pytest.mark.asyncio
async def test_tool_lines_and_body_use_their_own_endpoints(card_adapter):
    _begin_turn(card_adapter)

    await card_adapter.send(CHAT_ID, "⚙️ Read: \"a.py\"", metadata={"hermes_progress": True})
    await _settle()
    await card_adapter.send(CHAT_ID, "The file defines two functions.")
    await _settle()

    cardkit = card_adapter._cardkit
    # Trace → element replace (cumulative text, no streaming animation), each
    # step an explicit list item so Feishu cannot collapse the line breaks.
    assert cardkit.trace_texts() == ["- ⚙️ Read: \"a.py\""]
    assert all(
        req["element_id"] == feishu_cardkit.TRACE_ELEMENT_ID
        for req in cardkit.element_updates
    )
    # Body → streaming content endpoint.
    assert cardkit.body_texts() == ["The file defines two functions."]
    assert all(
        req["element_id"] == feishu_cardkit.BODY_ELEMENT_ID
        for req in cardkit.element_contents
    )


@pytest.mark.asyncio
async def test_progress_bubbles_accumulate_in_the_panel(card_adapter):
    """A second progress bubble appends to the trace instead of replacing it."""
    _begin_turn(card_adapter)

    first = await card_adapter.send(
        CHAT_ID, "⚙️ Read", metadata={"hermes_progress": True}
    )
    await _settle()
    # The gateway edits its bubble with the cumulative lines...
    await card_adapter.edit_message(CHAT_ID, first.message_id, "⚙️ Read\n⚙️ Grep")
    await _settle()
    # ...and starts a fresh bubble after content lands.
    await card_adapter.send(CHAT_ID, "⚙️ Bash", metadata={"hermes_progress": True})
    await _settle()

    assert card_adapter._cardkit.trace_texts()[-1] == "- ⚙️ Read\n- ⚙️ Grep\n- ⚙️ Bash"


@pytest.mark.asyncio
async def test_streamed_edits_send_prefix_growing_body_text(card_adapter):
    """The content endpoint takes the element's full text, never a delta."""
    _begin_turn(card_adapter)

    first = await card_adapter.send(CHAT_ID, "Hel")
    await _settle()
    await card_adapter.edit_message(CHAT_ID, first.message_id, "Hello wor")
    await _settle()
    await card_adapter.edit_message(CHAT_ID, first.message_id, "Hello world!")
    await _settle()

    texts = card_adapter._cardkit.body_texts()
    assert texts == ["Hel", "Hello wor", "Hello world!"]
    for earlier, later in zip(texts, texts[1:]):
        assert later.startswith(earlier), "prefix growth is what animates the typewriter"


@pytest.mark.asyncio
async def test_segment_break_keeps_both_segments_in_one_body(card_adapter):
    _begin_turn(card_adapter)

    await card_adapter.send(CHAT_ID, "First segment.")
    await _settle()
    await card_adapter.send(CHAT_ID, "Second segment.")
    await _settle()

    assert len(card_adapter._cardkit.creates) == 1
    assert card_adapter._cardkit.body_texts()[-1] == "First segment.\n\nSecond segment."


# ---------------------------------------------------------------------------
# Turn boundaries
# ---------------------------------------------------------------------------

def _event(message_id="om_in", thread_id=None):
    adapter_source = SimpleNamespace(
        chat_id=CHAT_ID, thread_id=thread_id, user_id="ou_u", chat_type="dm"
    )
    return MessageEvent(text="hi", source=adapter_source, message_id=message_id)


@pytest.mark.asyncio
async def test_turn_end_collapses_the_panel_and_stops_streaming(card_adapter):
    card_adapter._reactions_enabled = lambda: False
    await card_adapter.on_processing_start(_event())
    await card_adapter.send(CHAT_ID, "⚙️ Bash", metadata={"hermes_progress": True})
    await card_adapter.send(CHAT_ID, "Done.")
    await _settle()

    await card_adapter.on_processing_complete(_event(), ProcessingOutcome.SUCCESS)
    await _settle()

    cardkit = card_adapter._cardkit
    patched = json.loads(cardkit.element_patches[-1]["request_body"]["partial_element"])
    assert patched["expanded"] is False
    assert patched["header"]["title"]["content"]
    assert cardkit.element_patches[-1]["element_id"] == feishu_cardkit.PANEL_ELEMENT_ID

    settings = json.loads(cardkit.settings_calls[-1]["request_body"]["settings"])
    assert settings["config"]["streaming_mode"] is False
    assert settings["config"]["summary"]["content"] == feishu_cardkit.CardCopy().summary_done


@pytest.mark.asyncio
async def test_failed_turn_marks_the_summary_as_unfinished(card_adapter):
    card_adapter._reactions_enabled = lambda: False
    await card_adapter.on_processing_start(_event())
    await card_adapter.send(CHAT_ID, "Partial.")
    await _settle()
    await card_adapter.on_processing_complete(_event(), ProcessingOutcome.FAILURE)
    await _settle()

    settings = json.loads(
        card_adapter._cardkit.settings_calls[-1]["request_body"]["settings"]
    )
    assert settings["config"]["summary"]["content"] == feishu_cardkit.CardCopy().summary_failed


@pytest.mark.asyncio
async def test_output_outside_a_turn_stays_on_the_text_path(card_adapter):
    """A cron push or slash-command reply must not become an empty-trace card."""
    result = await card_adapter.send(CHAT_ID, "Reminder: standup at 10.")

    assert not card_adapter._cardkit.creates
    assert card_adapter._feishu_send_with_retry.await_args.kwargs["msg_type"] == "text"
    assert result.success is True


@pytest.mark.asyncio
async def test_sequence_increases_across_every_mutation(card_adapter):
    _begin_turn(card_adapter)
    await card_adapter.send(CHAT_ID, "⚙️ Bash", metadata={"hermes_progress": True})
    await _settle()
    await card_adapter.send(CHAT_ID, "Answer.")
    await _settle()
    await card_adapter._card_manager.close_route(
        feishu_cardkit.FeishuCardOutputManager.route_key(CHAT_ID, None)
    )
    await _settle()

    cardkit = card_adapter._cardkit
    sequences = [
        req["request_body"]["sequence"]
        for req in (
            cardkit.element_updates
            + cardkit.element_contents
            + cardkit.element_patches
            + cardkit.settings_calls
        )
    ]
    assert sequences == sorted(set(sequences)), "CardKit rejects a repeated sequence (99992402)"


# ---------------------------------------------------------------------------
# Delegate cards
# ---------------------------------------------------------------------------

def _delegate_output(adapter, *, agent_name="research-bot"):
    output = adapter.build_delegate_foreground_runtime(
        channel_id=CHAT_ID, thread_ts=None, user_id="ou_u", chat_type="dm"
    )["output"]
    output.bind_a2a_interaction_session(SimpleNamespace(agent_name=agent_name))
    return output


@pytest.mark.asyncio
async def test_delegate_gets_its_own_card_and_main_reopens_a_new_one(card_adapter):
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await card_adapter.send(CHAT_ID, "Delegating to research-bot.")
    await _settle()
    await output._emit_async("delegate", "tool_call", "WebSearch feishu cardkit", session_id="ctx-1")
    await output._emit_async("delegate", "ai_delta", "Remote ", session_id="ctx-1")
    await output._emit_async("delegate", "ai_delta", "answer.", session_id="ctx-1")
    await output._emit_async("delegate", "ai", "Remote answer.", session_id="ctx-1")
    await _settle()
    await card_adapter.send(CHAT_ID, "Summarizing what the delegate found.")
    await _settle()

    cards = card_adapter._cardkit.created_cards()
    titles = [card["header"]["title"]["content"] for card in cards]
    copy = feishu_cardkit.CardCopy()
    assert titles == [
        copy.main_title,
        copy.delegate_title.format(agent="research-bot"),
        copy.main_title,
    ], "main → delegate → main is three cards, not one"

    # The delegate's own trace and body landed on the delegate card, and the
    # main card was sealed before the delegate one opened.
    assert any("WebSearch" in text for text in card_adapter._cardkit.trace_texts())
    assert "Remote answer." in card_adapter._cardkit.body_texts()
    assert len(card_adapter._cardkit.settings_calls) >= 2


@pytest.mark.asyncio
async def test_two_delegations_in_one_turn_do_not_share_a_card(card_adapter):
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter, agent_name="alpha")

    await output._emit_async("delegate", "ai_delta", "from alpha", session_id="ctx-a")
    await _settle()
    await output._emit_async("delegate", "ai_delta", "from beta", session_id="ctx-b")
    await _settle()

    assert len(card_adapter._cardkit.creates) == 2


@pytest.mark.asyncio
async def test_delegate_without_streaming_still_delivers_its_final_text(card_adapter):
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await output._emit_async("delegate", "ai", "Only a final answer.", session_id="ctx-1")
    await _settle()

    assert "Only a final answer." in card_adapter._cardkit.body_texts()


# ---------------------------------------------------------------------------
# Degradation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_create_failure_falls_back_to_text_post(card_adapter):
    card_adapter._cardkit.create_fails = True
    _begin_turn(card_adapter)

    result = await card_adapter.send(CHAT_ID, "**Bold** answer.")

    assert result.success is True
    # Both the rich and the minimal retry shape were attempted before giving up.
    assert len(card_adapter._cardkit.creates) == 2
    assert card_adapter._feishu_send_with_retry.await_args.kwargs["msg_type"] == "post"


@pytest.mark.asyncio
async def test_failed_route_stays_on_the_text_path_without_retrying_cards(card_adapter):
    card_adapter._cardkit.create_fails = True
    _begin_turn(card_adapter)

    await card_adapter.send(CHAT_ID, "first")
    creates_after_first = len(card_adapter._cardkit.creates)
    await card_adapter.send(CHAT_ID, "second")

    assert len(card_adapter._cardkit.creates) == creates_after_first


@pytest.mark.asyncio
async def test_cards_disabled_keeps_the_legacy_path(card_adapter):
    card_adapter._card_output_enabled = False
    _begin_turn(card_adapter)

    await card_adapter.send(CHAT_ID, "plain reply")

    assert not card_adapter._cardkit.creates
    assert card_adapter._feishu_send_with_retry.await_args.kwargs["msg_type"] == "text"


def test_card_output_is_opt_in(monkeypatch):
    """Unset means off: an unconfigured deployment keeps the text/post path."""
    monkeypatch.delenv("FEISHU_CARD_OUTPUT", raising=False)
    assert FeishuAdapter._load_settings({}).card_output is False

    monkeypatch.setenv("FEISHU_CARD_OUTPUT", "")
    assert FeishuAdapter._load_settings({}).card_output is False


@pytest.mark.parametrize("raw", ["true", "1", "yes", "on", "TRUE", " true "])
def test_card_output_accepts_the_usual_spellings_of_on(monkeypatch, raw):
    """``FEISHU_CARD_OUTPUT=1`` must not silently read as off."""
    monkeypatch.setenv("FEISHU_CARD_OUTPUT", raw)
    assert FeishuAdapter._load_settings({}).card_output is True


@pytest.mark.parametrize("raw", ["false", "0", "no", "off", "nonsense"])
def test_card_output_stays_off_for_anything_else(monkeypatch, raw):
    monkeypatch.setenv("FEISHU_CARD_OUTPUT", raw)
    assert FeishuAdapter._load_settings({}).card_output is False


def test_yaml_card_output_overrides_the_env(monkeypatch):
    monkeypatch.setenv("FEISHU_CARD_OUTPUT", "true")
    assert FeishuAdapter._load_settings({"card_output": False}).card_output is False

    monkeypatch.delenv("FEISHU_CARD_OUTPUT", raising=False)
    assert FeishuAdapter._load_settings({"card_output": True}).card_output is True


@pytest.mark.asyncio
async def test_edit_of_a_sealed_block_is_not_re_sent_as_a_message(card_adapter):
    """A late finalize edit must not duplicate text the card already shows."""
    _begin_turn(card_adapter)
    sent = await card_adapter.send(CHAT_ID, "Answer.")
    await _settle()
    await card_adapter._card_manager.close_route(
        feishu_cardkit.FeishuCardOutputManager.route_key(CHAT_ID, None)
    )
    await _settle()
    before = card_adapter._feishu_send_with_retry.await_count

    result = await card_adapter.edit_message(CHAT_ID, sent.message_id, "Answer.")

    assert result.success is True
    assert card_adapter._feishu_send_with_retry.await_count == before


# ---------------------------------------------------------------------------
# Card JSON builder
# ---------------------------------------------------------------------------

def test_minimal_card_shape_drops_only_optional_fields():
    copy = feishu_cardkit.CardCopy()
    minimal = feishu_cardkit.build_card_json(
        title="t", trace_text="", body_text="", copy=copy, minimal=True
    )

    assert "summary" not in minimal["config"]
    assert "template" not in minimal["header"]
    panel = minimal["body"]["elements"][0]
    assert "expanded" not in panel
    # The load-bearing parts survive: panel header, and both addressable
    # element ids the update endpoints target.
    assert panel["header"]["title"]["content"] == copy.trace_title
    ids = {el.get("element_id") for el in minimal["body"]["elements"]}
    assert {feishu_cardkit.PANEL_ELEMENT_ID, feishu_cardkit.BODY_ELEMENT_ID} <= ids


def _session():
    return feishu_cardkit.FeishuCardSession(
        route_key="r", owner="main", title="t", chat_id=CHAT_ID
    )


def test_body_text_keeps_its_prefix_when_clipped():
    session = _session()
    session.add_block("body", "A" * (feishu_cardkit.MAX_BODY_BYTES + 500))
    rendered = session.body_text()

    assert len(rendered.encode("utf-8")) <= feishu_cardkit.MAX_BODY_BYTES
    assert rendered.startswith("A" * 100)


def test_cjk_body_is_budgeted_in_bytes_not_characters():
    """The card cap is 30 KB and CJK costs 3 bytes/char.

    Counting characters let a long Chinese answer sail past the real limit and
    get the whole update rejected (error 200860).
    """
    session = _session()
    session.add_block("body", "中" * feishu_cardkit.MAX_BODY_BYTES)
    rendered = session.body_text()

    assert len(rendered.encode("utf-8")) <= feishu_cardkit.MAX_BODY_BYTES
    # Truncation must not leave a mangled half-character behind.
    assert "\ufffd" not in rendered
    assert rendered.startswith("中中中")


def test_trace_text_keeps_the_recent_steps_when_clipped():
    session = _session()
    session.add_block("trace", "old\n" * 4000 + "newest step")
    rendered = session.trace_text()

    assert len(rendered.encode("utf-8")) <= feishu_cardkit.MAX_TRACE_BYTES
    assert rendered.endswith("newest step")


def test_cjk_trace_clipping_does_not_split_a_character():
    session = _session()
    session.add_block("trace", "步" * feishu_cardkit.MAX_TRACE_BYTES + "尾")
    rendered = session.trace_text()

    assert len(rendered.encode("utf-8")) <= feishu_cardkit.MAX_TRACE_BYTES
    assert "\ufffd" not in rendered
    assert rendered.endswith("尾")


# ---------------------------------------------------------------------------
# Retraction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fresh_final_replacement_leaves_the_answer_once(card_adapter):
    """The stream consumer's fresh-final path must not double the answer.

    It re-sends the finished reply as a new message and deletes the stale
    preview; in a card both are blocks of the same body, so the delete has to
    actually retract the superseded text.
    """
    _begin_turn(card_adapter)

    preview = await card_adapter.send(CHAT_ID, "Partial ans")
    await _settle()
    fresh = await card_adapter.send(CHAT_ID, "Partial answer, complete.")
    await _settle()
    assert await card_adapter.delete_message(CHAT_ID, preview.message_id) is True
    await _settle()

    assert fresh.message_id != preview.message_id
    assert card_adapter._cardkit.body_texts()[-1] == "Partial answer, complete."


@pytest.mark.asyncio
async def test_retracting_the_only_block_clears_the_body(card_adapter):
    _begin_turn(card_adapter)
    sent = await card_adapter.send(CHAT_ID, "…")
    await _settle()

    assert await card_adapter.delete_message(CHAT_ID, sent.message_id) is True
    await _settle()

    # Blanked, not left showing the retracted text — and never an empty
    # payload, which would be an invalid element update.
    assert card_adapter._cardkit.body_texts()[-1] == " "


@pytest.mark.asyncio
async def test_delete_of_a_real_message_id_stays_unsupported(card_adapter):
    """Feishu has no bot message-recall API; only card blocks are retractable."""
    assert await card_adapter.delete_message(CHAT_ID, "om_real_message") is False


@pytest.mark.asyncio
async def test_retracted_trace_line_no_longer_counts_toward_the_step_total(card_adapter):
    card_adapter._reactions_enabled = lambda: False
    await card_adapter.on_processing_start(_event())
    first = await card_adapter.send(CHAT_ID, "⚙️ Read", metadata={"hermes_progress": True})
    await card_adapter.send(CHAT_ID, "⚙️ Bash", metadata={"hermes_progress": True})
    await _settle()
    await card_adapter.delete_message(CHAT_ID, first.message_id)
    await _settle()
    await card_adapter.on_processing_complete(_event(), ProcessingOutcome.SUCCESS)
    await _settle()

    patched = json.loads(
        card_adapter._cardkit.element_patches[-1]["request_body"]["partial_element"]
    )
    assert patched["header"]["title"]["content"] == (
        feishu_cardkit.CardCopy().trace_title_done.format(steps=1)
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_body_content_is_plain_markdown_not_a_json_wrapper(card_adapter):
    """The streaming endpoint takes the raw text.

    Wrapping it in {"text": ...} is accepted with code 0 and then rendered
    literally — braces, escaped newlines and all — which is how a card ends up
    showing its own payload instead of the answer.
    """
    _begin_turn(card_adapter)
    markdown = "## 标题\n\n- 第一项\n- 第二项\n\n**加粗** 与 `代码`"

    await card_adapter.send(CHAT_ID, markdown)
    await _settle()

    sent = card_adapter._cardkit.body_texts()[-1]
    assert sent == markdown
    assert not sent.startswith("{")
    assert "\\n" not in sent, "literal backslash-n means the text was JSON-encoded"


def test_normalize_markdown_moves_a_fence_to_the_line_start():
    """Feishu only renders a fence that starts its line.

    Agents routinely indent one inside a list item, where it would otherwise
    show up as literal backticks.
    """
    text = "1. 运行：\n    ```bash\n    ls -la\n    ```\n"
    normalized = feishu_cardkit.normalize_markdown(text)

    lines = normalized.split("\n")
    assert lines[1] == "```bash"
    assert lines[3] == "```"
    # Only the fence lines move; the code itself keeps its indentation.
    assert lines[2] == "    ls -la"


def test_normalize_markdown_leaves_fence_free_text_untouched():
    text = "# 标题\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n> 引用"
    assert feishu_cardkit.normalize_markdown(text) == text


# ---------------------------------------------------------------------------
# Card chrome
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_declares_streaming_and_full_width(card_adapter):
    _begin_turn(card_adapter)
    await card_adapter.send(CHAT_ID, "hi")
    await _settle()

    config = card_adapter._cardkit.created_cards()[0]["config"]
    assert config["streaming_mode"] is True
    # JSON 2.0 supports shared cards only, and the streaming endpoint refuses
    # an exclusive card outright (300302).
    assert config["update_multi"] is True
    assert config["streaming_config"]["print_strategy"] == "fast"
    # ``width_mode`` is the 2.0 spelling; ``wide_screen_mode`` is 1.0 and does
    # nothing here. Width is what decides whether a table or code block reads.
    assert config["width_mode"] == "fill"
    assert "wide_screen_mode" not in config


@pytest.mark.asyncio
async def test_trace_panel_reads_as_collapsible_secondary_content(card_adapter):
    _begin_turn(card_adapter)
    await card_adapter.send(CHAT_ID, "hi")
    await _settle()

    panel = card_adapter._cardkit.created_cards()[0]["body"]["elements"][0]
    assert panel["expanded"] is True
    assert panel["header"]["icon"]["token"] == "down-small-ccm_outlined"
    assert panel["header"]["icon_expanded_angle"] == -180
    # The trace is secondary to the answer, so it renders one step down.
    assert panel["elements"][0]["text_size"] == "notation"


@pytest.mark.asyncio
async def test_element_ids_satisfy_the_platform_naming_rule(card_adapter):
    """element_id: letters/digits/underscore, leading letter, max 20 chars."""
    import re

    for element_id in (
        feishu_cardkit.PANEL_ELEMENT_ID,
        feishu_cardkit.TRACE_ELEMENT_ID,
        feishu_cardkit.BODY_ELEMENT_ID,
    ):
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,19}", element_id), element_id


# ---------------------------------------------------------------------------
# Update resilience
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_closed_streaming_mode_is_reopened_and_the_update_retried(card_adapter):
    """Feishu closes streaming mode on its own after a quiet stretch.

    A turn that pauses on a slow tool would otherwise lose every remaining
    chunk of its answer.
    """
    cardkit = card_adapter._cardkit
    _begin_turn(card_adapter)
    await card_adapter.send(CHAT_ID, "start")
    await _settle()

    cardkit.content_fail_codes = [200850]
    sent = await card_adapter.send(CHAT_ID, "the rest of the answer")
    await _settle()

    assert sent.success is True
    reopened = [
        payload for payload in cardkit.settings_payloads()
        if payload["config"].get("streaming_mode") is True
    ]
    assert reopened, "streaming mode should have been switched back on"
    assert "the rest of the answer" in cardkit.body_texts()[-1]


@pytest.mark.asyncio
async def test_a_transient_update_failure_is_retried_not_dropped(card_adapter):
    """A rejected update used to leave the area stale until the next edit."""
    cardkit = card_adapter._cardkit
    _begin_turn(card_adapter)

    cardkit.content_fail_codes = [200810]  # card busy in an interaction
    await card_adapter.send(CHAT_ID, "answer text")
    await _settle()
    await _settle()

    assert cardkit.body_texts()[-1] == "answer text"


@pytest.mark.asyncio
async def test_a_permanently_failing_update_stops_retrying(card_adapter):
    """Retries are bounded — a hard failure must not spin forever."""
    cardkit = card_adapter._cardkit
    _begin_turn(card_adapter)

    cardkit.content_fail_codes = [10002] * 50
    await card_adapter.send(CHAT_ID, "answer text")
    for _ in range(8):
        await _settle()

    assert len(cardkit.element_contents) <= feishu_cardkit._MAX_RENDER_RETRIES + 2


def test_trace_lines_become_list_items_so_breaks_survive():
    """Feishu may collapse a single newline (soft break).

    The trace is a sequence of independent steps, so the separation has to be
    structural — otherwise the whole log renders as one run-on paragraph.
    """
    rendered = feishu_cardkit.format_trace_lines("⚙️ Read\n⚙️ Grep\n\n⚙️ Bash")

    assert rendered == "- ⚙️ Read\n- ⚙️ Grep\n- ⚙️ Bash"


def test_trace_formatting_does_not_double_bullet_existing_blocks():
    rendered = feishu_cardkit.format_trace_lines(
        "- already a bullet\n> a quote\n1. numbered\n| a | b |\nplain"
    )

    assert rendered.split("\n") == [
        "- already a bullet",
        "> a quote",
        "1. numbered",
        "| a | b |",
        "- plain",
    ]


def test_a_one_line_command_folds_onto_its_tool_line():
    """The gateway renders a terminal call as a header line plus a fence.

    Bulleting those lines independently put the command *inside* the code box
    as "- <command>" and counted every fence line as its own step. The command
    belongs on the tool's own line, the way the delegate adapter renders it.
    """
    raw = "💻 terminal\n```\nmcporter call 'exa.web_search_exa'\n```"

    assert feishu_cardkit.format_trace_lines(raw) == (
        "- 💻 terminal: `mcporter call 'exa.web_search_exa'`"
    )
    assert feishu_cardkit._count_trace_steps(raw) == 1


def test_a_headerless_command_block_does_not_attach_to_the_previous_step():
    """Back-to-back terminal calls: the gateway drops the repeated header.

    That second block is its own call, so folding it onto the first command's
    line would claim one step ran two commands.
    """
    rendered = feishu_cardkit.format_trace_lines(
        "💻 terminal\n```\nls\n```\n```\npwd\n```"
    )

    assert rendered.split("\n") == ["- 💻 terminal: `ls`", "- `pwd`"]


def test_a_multi_line_command_keeps_its_block_as_one_step():
    """Verbose mode streams the full command; a script can't fit on a line.

    It stays a code block — but attached to its tool line and, critically,
    with its own lines left alone instead of bulleted into the code box.
    """
    raw = "💻 terminal\n```\nfor f in *.py; do\n  echo $f\ndone\n```"

    assert feishu_cardkit.format_trace_lines(raw).split("\n") == [
        "- 💻 terminal",
        "```",
        "for f in *.py; do",
        "  echo $f",
        "done",
        "```",
    ]
    assert feishu_cardkit._count_trace_steps(raw) == 1


def test_a_command_containing_backticks_still_renders_as_inline_code():
    rendered = feishu_cardkit.format_trace_lines("💻 terminal\n```\necho `date`\n```")

    assert rendered == "- 💻 terminal: `` echo `date` ``"


def test_card_copy_honours_env_overrides(monkeypatch):
    """Deployments rebrand the card without touching code."""
    import plugins.platforms.feishu.adapter as feishu_adapter

    monkeypatch.setenv("FEISHU_CARD_TITLE", "Aegis")
    monkeypatch.setenv("FEISHU_CARD_DELEGATE_TITLE", "Work Agent · {agent}")
    copy = feishu_adapter._card_copy_from_env()

    assert copy.main_title == "Aegis"
    assert copy.delegate_title.format(agent="aisoc") == "Work Agent · aisoc"
    # Untouched fields keep their defaults.
    assert copy.trace_title == feishu_cardkit.CardCopy().trace_title


# ---------------------------------------------------------------------------
# Delegate lifecycle chatter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delegate_status_events_are_not_shown(card_adapter):
    """"entered foreground loop" / "return to main" are transport plumbing.

    They describe the delegation machinery, not the user's task, and the agent
    narrates the real outcome itself.  They must reach neither the card nor a
    chat message.
    """
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)
    before_messages = card_adapter._feishu_send_with_retry.await_count

    for status in ("entered foreground loop", "return to main", "input timeout"):
        await output._emit_async("delegate", "status", status, session_id="ctx-1")
    await _settle()

    cardkit = card_adapter._cardkit
    # No card was opened just to hold plumbing, and nothing was rendered.
    assert not cardkit.creates
    assert not cardkit.trace_texts()
    assert not cardkit.body_texts()
    # And no fallback text message either.
    assert card_adapter._feishu_send_with_retry.await_count == before_messages


@pytest.mark.asyncio
async def test_delegate_errors_are_still_surfaced(card_adapter):
    """A status is chatter; a failure is the user's business."""
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await output._emit_async("delegate", "error", "remote refused the task", session_id="ctx-1")
    await _settle()

    assert any(
        "remote refused the task" in text for text in card_adapter._cardkit.body_texts()
    )


@pytest.mark.asyncio
async def test_status_still_reaches_the_chat_when_cards_are_off(card_adapter):
    """The suppression is scoped to card mode, not to the adapter."""
    card_adapter._card_output_enabled = False
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await output._emit_async("delegate", "status", "entered foreground loop", session_id="ctx-1")
    await _settle()

    sent = card_adapter._feishu_send_with_retry.await_args.kwargs
    assert "entered foreground loop" in sent["payload"]


# ---------------------------------------------------------------------------
# One card per exchange in a foreground loop
# ---------------------------------------------------------------------------

async def _delegate_exchange(output, text, *, session_id="ctx-loop"):
    """One user→remote-agent round trip, as the a2a tool emits it."""
    await output._emit_async("delegate", "ai_delta", text, session_id=session_id)
    await output._emit_async("delegate", "ai", text, session_id=session_id)
    await _settle()


@pytest.mark.asyncio
async def test_each_loop_exchange_gets_its_own_card(card_adapter):
    """A foreground loop holds one A2A context for every turn.

    Owner change alone therefore cannot end a delegate card there, so without
    an explicit per-exchange boundary every follow-up answer piles into the
    first card.
    """
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await _delegate_exchange(output, "第一轮回答")
    await _delegate_exchange(output, "第二轮回答")
    await _delegate_exchange(output, "第三轮回答")

    cardkit = card_adapter._cardkit
    assert len(cardkit.creates) == 3, "three exchanges, three cards"
    copy = feishu_cardkit.CardCopy()
    expected = copy.delegate_title.format(agent="research-bot")
    assert [card["header"]["title"]["content"] for card in cardkit.created_cards()] == [
        expected
    ] * 3
    # Each card carries only its own exchange.
    assert cardkit.body_texts()[-1] == "第三轮回答"


@pytest.mark.asyncio
async def test_a_finished_exchange_is_sealed_before_the_next_one_opens(card_adapter):
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await _delegate_exchange(output, "answer one")
    sealed_after_first = len(card_adapter._cardkit.settings_calls)
    await _delegate_exchange(output, "answer two")

    assert sealed_after_first >= 1, "the first exchange should already be sealed"
    settings = card_adapter._cardkit.settings_payloads()[sealed_after_first - 1]
    assert settings["config"]["streaming_mode"] is False


@pytest.mark.asyncio
async def test_tool_calls_stay_on_the_exchange_they_belong_to(card_adapter):
    """A card holds one exchange: its own trace and its own answer."""
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await output._emit_async("delegate", "tool_call", "SearchOne alpha", session_id="ctx-loop")
    await _delegate_exchange(output, "first answer")
    await output._emit_async("delegate", "tool_call", "SearchTwo beta", session_id="ctx-loop")
    await _delegate_exchange(output, "second answer")

    cardkit = card_adapter._cardkit
    assert len(cardkit.creates) == 2
    traces = cardkit.trace_texts()
    # The second card's trace never mentions the first exchange's tool.
    assert "SearchTwo" in traces[-1]
    assert "SearchOne" not in traces[-1]


@pytest.mark.asyncio
async def test_main_agent_output_after_a_loop_opens_its_own_card(card_adapter):
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await _delegate_exchange(output, "delegate answer")
    await card_adapter.send(CHAT_ID, "主 agent 汇总")
    await _settle()

    cardkit = card_adapter._cardkit
    titles = [card["header"]["title"]["content"] for card in cardkit.created_cards()]
    copy = feishu_cardkit.CardCopy()
    assert titles == [
        copy.delegate_title.format(agent="research-bot"),
        copy.main_title,
    ]
    assert cardkit.body_texts()[-1] == "主 agent 汇总"


# ---------------------------------------------------------------------------
# Concurrent delta delivery
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_deltas_land_in_one_body_block(card_adapter):
    """Delegate events arrive as independent fire-and-forget tasks.

    ``_FeishuDelegateOutputAdapter.emit`` schedules every event with
    ``create_task`` / ``run_coroutine_threadsafe``, so a burst of deltas runs
    concurrently.  Each one used to observe "no block yet" while the first was
    still awaiting its network call, so each opened its own body block — and
    body blocks join with a blank line, which rendered the head of the answer
    as one fragment per paragraph.
    """
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)
    fragments = ["我先", "加", "载该", "工具的", "schema，再真", "实", "调", "用。"]

    await asyncio.gather(*[
        output._emit_async("delegate", "ai_delta", fragment, session_id="ctx-1")
        for fragment in fragments
    ])
    await _settle()

    rendered = card_adapter._cardkit.body_texts()[-1]
    assert rendered == "".join(fragments), "fragments must concatenate, not stack"
    assert "\n" not in rendered, "a blank line here means one block per delta"


@pytest.mark.asyncio
async def test_concurrent_deltas_keep_the_typewriter_prefix_property(card_adapter):
    """Every streamed update must extend the previous one.

    Feishu animates the difference only when the new text is a prefix superset
    of the old; otherwise it replaces the element and the typewriter dies.
    """
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await asyncio.gather(*[
        output._emit_async("delegate", "ai_delta", chunk, session_id="ctx-1")
        for chunk in ("alpha ", "beta ", "gamma ", "delta")
    ])
    await _settle()

    texts = card_adapter._cardkit.body_texts()
    for earlier, later in zip(texts, texts[1:]):
        assert later.startswith(earlier), f"{later!r} does not extend {earlier!r}"


@pytest.mark.asyncio
async def test_delta_burst_opens_exactly_one_card(card_adapter):
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await asyncio.gather(*[
        output._emit_async("delegate", "ai_delta", str(index), session_id="ctx-1")
        for index in range(12)
    ])
    await _settle()

    assert len(card_adapter._cardkit.creates) == 1


@pytest.mark.asyncio
async def test_a_tool_call_between_bursts_starts_a_new_paragraph(card_adapter):
    """A tool boundary ends the text segment; the next deltas are a new block."""
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await asyncio.gather(*[
        output._emit_async("delegate", "ai_delta", part, session_id="ctx-1")
        for part in ("先看", "一下。")
    ])
    await _settle()
    await output._emit_async("delegate", "tool_call", "WebSearch cve", session_id="ctx-1")
    await _settle()
    await asyncio.gather(*[
        output._emit_async("delegate", "ai_delta", part, session_id="ctx-1")
        for part in ("结果", "如下。")
    ])
    await _settle()

    assert card_adapter._cardkit.body_texts()[-1] == "先看一下。\n\n结果如下。"


@pytest.mark.asyncio
async def test_final_event_does_not_repeat_text_streamed_before_a_tool(card_adapter):
    """A tool boundary ends the text segment, not the exchange.

    ``states[owner]`` is cleared at a tool call, so using it to answer "did
    this delegate stream anything?" made a streamed-then-tool-then-answer
    exchange look like it had never streamed — and the turn-final event
    appended the whole response on top of the text already on screen.
    """
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await output._emit_async("delegate", "ai_delta", "先看一下。", session_id="ctx-1")
    await _settle()
    await output._emit_async("delegate", "tool_call", "WebSearch cve", session_id="ctx-1")
    await _settle()
    await output._emit_async("delegate", "ai_delta", "结果如下。", session_id="ctx-1")
    await _settle()
    await output._emit_async(
        "delegate", "ai", "先看一下。结果如下。", session_id="ctx-1"
    )
    await _settle()

    rendered = card_adapter._cardkit.body_texts()[-1]
    assert rendered == "先看一下。\n\n结果如下。"
    assert rendered.count("结果如下。") == 1


@pytest.mark.asyncio
async def test_a_non_streaming_delegate_still_gets_its_answer_after_a_tool(card_adapter):
    """The append path is still needed when nothing streamed at all."""
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await output._emit_async("delegate", "tool_call", "WebSearch cve", session_id="ctx-1")
    await _settle()
    await output._emit_async("delegate", "ai", "只有最终答案。", session_id="ctx-1")
    await _settle()

    assert card_adapter._cardkit.body_texts()[-1] == "只有最终答案。"


@pytest.mark.asyncio
async def test_the_streamed_flag_resets_between_exchanges(card_adapter):
    """Exchange two must not inherit exchange one's "already streamed"."""
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)

    await output._emit_async("delegate", "ai_delta", "第一轮", session_id="ctx-loop")
    await output._emit_async("delegate", "ai", "第一轮", session_id="ctx-loop")
    await _settle()
    # Second exchange never streams — its answer arrives only as the final event.
    await output._emit_async("delegate", "ai", "第二轮答案", session_id="ctx-loop")
    await _settle()

    assert len(card_adapter._cardkit.creates) == 2
    assert card_adapter._cardkit.body_texts()[-1] == "第二轮答案"


# ---------------------------------------------------------------------------
# Transient status notices bypass the card
# ---------------------------------------------------------------------------

HEARTBEAT = "⏳ Working — 3 min — iteration 1/90, a2a_delegate"


@pytest.mark.asyncio
async def test_a_heartbeat_does_not_splice_into_a_live_answer(card_adapter):
    """The liveness heartbeat is not part of the reply.

    Appended to the card it lands in the middle of whatever answer is
    streaming, which is what "异常插入委派agent消息流" looks like.
    """
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)
    await output._emit_async("delegate", "ai_delta", "远程答案。", session_id="ctx-1")
    await _settle()

    result = await card_adapter.send(
        CHAT_ID, HEARTBEAT, metadata={"hermes_card_bypass": True}
    )
    await _settle()

    assert result.success is True
    # Delivered as an ordinary message, not a card block handle.
    assert not card_adapter._card_manager.owns_message(result.message_id)
    assert card_adapter._feishu_send_with_retry.await_args.kwargs["msg_type"] == "text"
    # The card kept only the delegate's answer, and stayed open.
    assert card_adapter._cardkit.body_texts()[-1] == "远程答案。"
    assert len(card_adapter._cardkit.creates) == 1


@pytest.mark.asyncio
async def test_non_conversational_sends_bypass_the_card_too(card_adapter):
    """The gateway's existing name for a lifecycle send is honoured as well."""
    _begin_turn(card_adapter)
    await card_adapter.send(CHAT_ID, "the answer")
    await _settle()

    result = await card_adapter.send(
        CHAT_ID, "🔄 Restarting…", metadata={"non_conversational": True}
    )
    await _settle()

    assert not card_adapter._card_manager.owns_message(result.message_id)
    assert card_adapter._cardkit.body_texts()[-1] == "the answer"


@pytest.mark.asyncio
async def test_tool_progress_still_goes_into_the_panel(card_adapter):
    """Execution chrome has a home in the card and must not be bypassed.

    A progress send can carry both markers (the gateway builds its progress
    metadata through the same non-conversational helper), so the progress
    marker has to win.
    """
    _begin_turn(card_adapter)

    await card_adapter.send(
        CHAT_ID,
        "⚙️ Bash",
        metadata={"hermes_progress": True, "non_conversational": True},
    )
    await _settle()

    assert card_adapter._cardkit.trace_texts()[-1] == "- ⚙️ Bash"


@pytest.mark.asyncio
async def test_delegate_interaction_ack_is_a_plain_message(card_adapter):
    """"✅ Delegate interaction resolved." acknowledges a button, not an answer."""
    _begin_turn(card_adapter)
    output = _delegate_output(card_adapter)
    await output._emit_async("delegate", "ai_delta", "正在处理。", session_id="ctx-1")
    await _settle()

    card_adapter._delegate_interactions["i-1"] = {
        "interaction_id": "i-1",
        "kind": "approval",
        "chat_id": CHAT_ID,
        "thread_id": None,
    }
    await card_adapter.resolve_delegate_interaction({"interaction_id": "i-1"})
    await _settle()

    sent = card_adapter._feishu_send_with_retry.await_args.kwargs
    assert "Delegate interaction resolved" in sent["payload"]
    assert sent["msg_type"] == "text"
    assert card_adapter._cardkit.body_texts()[-1] == "正在处理。"


@pytest.mark.asyncio
async def test_a_bypassed_notice_keeps_thread_routing(card_adapter):
    """Skipping the card must not drop the topic the notice belongs to."""
    _begin_turn(card_adapter, thread_id="omt_topic")
    card_adapter._delegate_interactions["i-2"] = {
        "interaction_id": "i-2",
        "kind": "approval",
        "chat_id": CHAT_ID,
        "thread_id": "omt_topic",
    }

    await card_adapter.resolve_delegate_interaction({"interaction_id": "i-2"})
    await _settle()

    assert card_adapter._feishu_send_with_retry.await_args.kwargs["metadata"] == {
        "hermes_card_bypass": True,
        "thread_id": "omt_topic",
    }


@pytest.mark.asyncio
async def test_bypassed_notices_are_editable_as_real_messages(card_adapter):
    """The heartbeat re-edits its own bubble; that must stay a real message."""
    _begin_turn(card_adapter)

    sent = await card_adapter.send(
        CHAT_ID, HEARTBEAT, metadata={"hermes_card_bypass": True}
    )
    edited = await card_adapter.edit_message(
        CHAT_ID, sent.message_id, "⏳ Working — 4 min"
    )

    assert edited.success is True
    # Went through the im update path, not the card element path.
    assert not card_adapter._cardkit.element_contents


def test_bypass_decision_table():
    """The one place that decides card vs. plain message.

    Exercised directly because the callers are spread across the gateway and
    the adapter, and every one of them depends on these four answers.
    """
    decide = FeishuAdapter._card_bypass_requested

    # Ordinary content: the card owns it.
    assert decide(None) is False
    assert decide({}) is False
    assert decide({"thread_id": "omt_1"}) is False

    # Transient notices: explicit opt-out, or the gateway's lifecycle marker.
    assert decide({"hermes_card_bypass": True}) is True
    assert decide({"non_conversational": True}) is True

    # Execution chrome has a home inside the card, so the progress marker
    # wins even when a lifecycle marker rides along with it.
    assert decide({"hermes_progress": True}) is False
    assert decide({"hermes_progress": True, "non_conversational": True}) is False
    assert decide({"hermes_progress": True, "hermes_card_bypass": True}) is False


# ---------------------------------------------------------------------------
# Attachments in card mode
# ---------------------------------------------------------------------------
#
# A file is never card content.  The gateway normally strips attachments out
# of a reply before the adapter sees it, but card mode adds text entry points
# that skip that pass (mid-turn commentary, delegate output), and anything
# arriving that way used to render as a literal path inside the card.  These
# pin the contract: same trigger rules as the non-card path, delivered as its
# own message, never twice in one turn.


@pytest.fixture
def media_adapter(card_adapter, monkeypatch):
    """``card_adapter`` with the uploaders stubbed and media env neutralized."""
    # The delivery allowlist reads the environment; a developer's .env must not
    # decide whether these pass — same reasoning as the card-title delenv above.
    for env_name in (
        "HERMES_MEDIA_DELIVERY_STRICT",
        "HERMES_MEDIA_ALLOW_DIRS",
        "HERMES_MEDIA_TRUST_RECENT_FILES",
        "HERMES_MEDIA_TRUST_RECENT_SECONDS",
    ):
        monkeypatch.delenv(env_name, raising=False)
    for name in ("send_document", "send_image_file", "send_video", "send_voice"):
        monkeypatch.setattr(
            card_adapter,
            name,
            AsyncMock(return_value=SendResult(success=True, message_id="om_file")),
        )
    return card_adapter


def _deliverable(tmp_path, name="report.md", data=b"hello world"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


@pytest.mark.asyncio
async def test_a_media_tag_in_card_text_is_uploaded_as_its_own_message(
    media_adapter, tmp_path
):
    path = _deliverable(tmp_path)
    _begin_turn(media_adapter)

    await media_adapter.send(CHAT_ID, f"报告好了。\nMEDIA:{path}")
    await _settle()

    media_adapter.send_document.assert_awaited_once()
    assert media_adapter.send_document.await_args.kwargs["file_path"] == path
    body = media_adapter._cardkit.body_texts()[-1]
    assert "MEDIA:" not in body
    assert "报告好了。" in body
    assert "report.md" in body  # the receipt line names the file


@pytest.mark.asyncio
async def test_a_bare_deliverable_path_is_uploaded_too(media_adapter, tmp_path):
    """Card mode must not change *what* counts as a deliverable.

    The non-card path auto-detects bare absolute paths; a reply that mentions
    one has to produce the same attachment whether the card is on or off.
    """
    path = _deliverable(tmp_path, "notes.txt")
    _begin_turn(media_adapter)

    await media_adapter.send(CHAT_ID, f"写好了，文件在 {path}")
    await _settle()

    media_adapter.send_document.assert_awaited_once()
    assert media_adapter.send_document.await_args.kwargs["file_path"] == path


@pytest.mark.asyncio
async def test_a_media_only_reply_still_gets_a_card_body(media_adapter, tmp_path):
    """The receipt line is what keeps the body from being empty.

    CardKit rejects a blank element, and an empty body would fall through to
    the legacy path and post the raw tag as text.
    """
    path = _deliverable(tmp_path)
    _begin_turn(media_adapter)

    await media_adapter.send(CHAT_ID, f"MEDIA:{path}")
    await _settle()

    media_adapter.send_document.assert_awaited_once()
    assert media_adapter._cardkit.body_texts()[-1].strip() == "📎 已发送文件：report.md"


@pytest.fixture
def uploads(card_adapter, monkeypatch):
    """Record real uploads.

    Stubbed *below* the per-turn ledger — stubbing the public senders would
    skip the very check these tests exist to prove.
    """
    recorded: list = []

    async def _record(**kwargs):
        recorded.append(kwargs["file_path"])
        return SendResult(success=True, message_id="om_file")

    for env_name in (
        "HERMES_MEDIA_DELIVERY_STRICT",
        "HERMES_MEDIA_ALLOW_DIRS",
        "HERMES_MEDIA_TRUST_RECENT_FILES",
        "HERMES_MEDIA_TRUST_RECENT_SECONDS",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(
        card_adapter, "_send_uploaded_file_message", AsyncMock(side_effect=_record)
    )
    return recorded


@pytest.mark.asyncio
async def test_the_same_file_is_not_uploaded_twice_in_one_turn(
    card_adapter, uploads, tmp_path
):
    """``_send_with_retry`` re-sends byte-identical content after a failure."""
    path = _deliverable(tmp_path)
    _begin_turn(card_adapter)

    await card_adapter.send(CHAT_ID, f"Done. MEDIA:{path}")
    await card_adapter.send(CHAT_ID, f"Done. MEDIA:{path}")
    await _settle()

    assert uploads == [path]


@pytest.mark.asyncio
async def test_the_gateways_own_upload_is_deduped_against_the_card_path(
    card_adapter, uploads, tmp_path
):
    """Two entry points, one file.

    Commentary can route a path through the card safety net while the final
    reply routes the same path through the gateway's own extraction, which
    calls the sender directly.  Only one upload may reach Feishu.
    """
    path = _deliverable(tmp_path)
    _begin_turn(card_adapter)

    await card_adapter.send(CHAT_ID, f"先看一下 MEDIA:{path}")
    # The gateway's own dispatch loop, calling the sender directly.
    await card_adapter.send_document(chat_id=CHAT_ID, file_path=path, metadata=None)
    await _settle()

    assert uploads == [path]


@pytest.mark.asyncio
async def test_a_failed_upload_does_not_burn_the_file_for_the_turn(
    card_adapter, tmp_path, monkeypatch
):
    """A claim is released when its upload fails, so a retry can still land."""
    path = _deliverable(tmp_path)
    monkeypatch.setattr(
        card_adapter,
        "_send_uploaded_file_message",
        AsyncMock(side_effect=[
            SendResult(success=False, error="upload rejected"),
            SendResult(success=True, message_id="om_file"),
        ]),
    )
    _begin_turn(card_adapter)

    first = await card_adapter.send_document(chat_id=CHAT_ID, file_path=path)
    second = await card_adapter.send_document(chat_id=CHAT_ID, file_path=path)

    assert first.success is False
    assert second.success is True


@pytest.mark.asyncio
async def test_a_later_turn_can_send_the_same_file_again(media_adapter, tmp_path):
    """The ledger is per-turn: "send me that file again" has to work."""
    path = _deliverable(tmp_path)
    media_adapter._reactions_enabled = lambda: False

    await media_adapter.on_processing_start(_event())
    await media_adapter.send(CHAT_ID, f"MEDIA:{path}")
    await _settle()
    await media_adapter.on_processing_complete(_event(), ProcessingOutcome.SUCCESS)
    await _settle()

    await media_adapter.on_processing_start(_event(message_id="om_in2"))
    await media_adapter.send(CHAT_ID, f"再发一次 MEDIA:{path}")
    await _settle()

    assert media_adapter.send_document.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, sender, kwarg",
    [
        ("chart.png", "send_image_file", "image_path"),
        ("clip.mp4", "send_video", "video_path"),
        ("report.pdf", "send_document", "file_path"),
    ],
)
async def test_attachments_route_by_kind(media_adapter, tmp_path, name, sender, kwarg):
    """Same routing table as the gateway's dispatch loop."""
    path = _deliverable(tmp_path, name)
    _begin_turn(media_adapter)

    await media_adapter.send(CHAT_ID, f"给你 MEDIA:{path}")
    await _settle()

    getattr(media_adapter, sender).assert_awaited_once()
    assert getattr(media_adapter, sender).await_args.kwargs[kwarg] == path


@pytest.mark.asyncio
async def test_audio_marked_as_voice_routes_to_the_voice_sender(media_adapter, tmp_path):
    path = _deliverable(tmp_path, "memo.ogg")
    _begin_turn(media_adapter)

    await media_adapter.send(CHAT_ID, f"[[audio_as_voice]] MEDIA:{path}")
    await _settle()

    media_adapter.send_voice.assert_awaited_once()
    assert media_adapter.send_voice.await_args.kwargs["audio_path"] == path


@pytest.mark.asyncio
async def test_a_failed_upload_is_reported_not_swallowed(media_adapter, tmp_path):
    """A silent drop is the one outcome worse than a failed send.

    The notice lands at the end of the card (it goes out through ``send``,
    like any other text) — what matters is that it is there at all.
    """
    path = _deliverable(tmp_path)
    media_adapter.send_document = AsyncMock(
        return_value=SendResult(success=False, error="upload rejected")
    )
    _begin_turn(media_adapter)

    await media_adapter.send(CHAT_ID, f"MEDIA:{path}")
    await _settle()

    assert "Couldn't deliver" in media_adapter._cardkit.body_texts()[-1]


@pytest.mark.asyncio
async def test_progress_and_bypass_sends_never_upload(media_adapter, tmp_path):
    """Execution chrome and lifecycle notices are not attachment requests."""
    path = _deliverable(tmp_path)
    _begin_turn(media_adapter)

    await media_adapter.send(CHAT_ID, f"MEDIA:{path}", metadata={"hermes_progress": True})
    await media_adapter.send(
        CHAT_ID, f"MEDIA:{path}", metadata={"hermes_card_bypass": True}
    )
    await _settle()

    media_adapter.send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_media_tag_outside_a_turn_is_left_to_the_legacy_path(
    media_adapter, tmp_path
):
    """No turn, no card — and therefore no card-side extraction either."""
    path = _deliverable(tmp_path)

    await media_adapter.send(CHAT_ID, f"MEDIA:{path}")
    await _settle()

    media_adapter.send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_path_inside_a_code_block_is_not_an_attachment_request(
    media_adapter, tmp_path
):
    """Reusing the gateway's extractors is what buys this.

    A reply that documents the MEDIA syntax must not ship the file it names.
    """
    path = _deliverable(tmp_path)
    _begin_turn(media_adapter)

    await media_adapter.send(CHAT_ID, f"用法：\n```\nMEDIA:{path}\n```")
    await _settle()

    media_adapter.send_document.assert_not_awaited()
    assert f"MEDIA:{path}" in media_adapter._cardkit.body_texts()[-1]


@pytest.mark.asyncio
async def test_files_still_ship_when_the_card_declines_the_text(
    media_adapter, tmp_path, monkeypatch
):
    """CardKit can refuse a send mid-turn (cooldown after a delivery failure).

    The text then falls back to the legacy text/post path — but the files were
    already taken out of it, so this branch is the only thing left that can
    deliver them.
    """
    path = _deliverable(tmp_path)
    _begin_turn(media_adapter)
    monkeypatch.setattr(
        media_adapter._card_manager, "deliver", AsyncMock(return_value=None)
    )

    result = await media_adapter.send(CHAT_ID, f"报告好了。\nMEDIA:{path}")
    await _settle()

    assert result.success is True
    media_adapter.send_document.assert_awaited_once()
    payload = media_adapter._feishu_send_with_retry.await_args.kwargs["payload"]
    assert "MEDIA:" not in payload
    assert "report.md" in payload
