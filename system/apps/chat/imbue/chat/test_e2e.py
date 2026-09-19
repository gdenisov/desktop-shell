"""End-to-end tests for the chat pages using Playwright.

These tests serve the shell and the chat app together (``running_workspace``: two threaded
Werkzeug servers over a registry holding the chat row at the chat's own URL, with mocked agent
discovery behind the chat), then use Playwright to open a chat from the shell exactly as a user
would and assert on the chat page inside its frame. The shell's own behaviour is the shell
package's suite; what is tested here is the chat document.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Generator
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
from app_instances.testing import free_port
from playwright.sync_api import Frame
from playwright.sync_api import FrameLocator
from playwright.sync_api import Page
from playwright.sync_api import expect

from imbue.chat.accounts import account_dir
from imbue.chat.agent_discovery import MngrMessenger
from imbue.chat.models import ChatSnapshot
from imbue.chat.testing import FIXTURE_AGENT_ID
from imbue.chat.testing import FIXTURE_CHAT_ADDRESS
from imbue.chat.testing import FIXTURE_SESSION_ID
from imbue.chat.testing import RecordingMngrMessenger
from imbue.chat.testing import RunningWorkspace
from imbue.chat.testing import STARTER_PROJECT_ID
from imbue.chat.testing import STARTER_PROJECT_NAME
from imbue.chat.testing import SummaryWritingMngrMessenger
from imbue.chat.testing import is_e2e_browser_installed
from imbue.chat.testing import running_workspace
from imbue.mngr.utils.polling import wait_for
from imbue.system_interface.app_context import DEFAULT_STATIC_DIRECTORY as SHELL_STATIC_DIRECTORY
from imbue.system_interface.shell.primitives import EVERYTHING_VIEW_ID
from imbue.system_interface.shell.primitives import EVERYTHING_VIEW_NAME


def _playwright_browsers_installed() -> bool:
    """Check whether a launchable browser is present (Fortress or Playwright's cache)."""
    return is_e2e_browser_installed()


def _frontends_built() -> bool:
    """Whether both bundles exist: the chat page (``static/chat.html``) and the shell that frames it."""
    return (Path(__file__).parent / "static" / "chat.html").is_file() and (
        SHELL_STATIC_DIRECTORY / "index.html"
    ).is_file()


pytestmark = [
    pytest.mark.release,
    pytest.mark.skipif(not _playwright_browsers_installed(), reason="Playwright browsers not installed"),
    pytest.mark.skipif(
        not _frontends_built(),
        reason="The chat or shell frontend is not built (run `npm run build` in system/); skipping e2e.",
    ),
]

_TRIGGER_TIMEOUT_MS = 20000


def _chat(page: Page, agent_id: str = FIXTURE_AGENT_ID) -> FrameLocator:
    """The chat's page, framed at the chat origin under the instance's address.

    Every chat assertion goes through it: the shell document holds no chat markup, only
    the frame.
    """
    return page.frame_locator(f'iframe[data-address="app:chat?instance={agent_id}"]')


def _chat_frame(page: Page, agent_id: str = FIXTURE_AGENT_ID) -> Frame:
    """The chat page's own frame, for the evaluate and wait calls that need its document.

    Polled: the frame is created when the pane docks and loads a beat later.
    """
    for _ in range(150):
        for frame in page.frames:
            if frame.url.rstrip("/").endswith(f"/{agent_id}"):
                return frame
        page.wait_for_timeout(100)
    raise TimeoutError(f"the chat page for {agent_id} never loaded in a frame")


def _running_e2e_server(
    tmp_path: Path,
    session_events: list[dict[str, Any]] | None = None,
    is_account_signed_in: bool = True,
) -> AbstractContextManager[RunningWorkspace]:
    """The two-server workspace, the shell and the chat each on a free port of their own."""
    return running_workspace(
        tmp_path,
        free_port(),
        free_port(),
        session_events=session_events,
        is_account_signed_in=is_account_signed_in,
    )


@pytest.fixture
def e2e_server(tmp_path: Path) -> Generator[RunningWorkspace, None, None]:
    """Start the shell and the chat with the fixture agent and the starter project."""
    with _running_e2e_server(tmp_path) as server:
        yield server


# ---------- helpers ----------


def _client_layout_files(state_dir: Path, view_id: str) -> list[Path]:
    """The per-client layout files a view holds (the seeds beside them are not counted)."""
    view_dir = state_dir / "layouts" / view_id
    if not view_dir.is_dir():
        return []
    return [path for path in view_dir.glob("*.json") if not path.name.startswith("seed.")]


def _wait_for_layout_saved(state_dir: Path, view_id: str, containing: str | None = None) -> None:
    def _saved() -> bool:
        files = _client_layout_files(state_dir, view_id)
        if containing is None:
            return bool(files)
        return any(containing in path.read_text() for path in files)

    wait_for(
        _saved,
        timeout=15.0,
        poll_interval=0.1,
        error_message=f"autosave never wrote a layout for {view_id}"
        + (f" holding {containing}" if containing else ""),
    )


def _wait_for_view(page: Page, view_id: str) -> None:
    """The desktop names the view it has mounted; the active view itself lives in the shell's client record."""
    page.wait_for_selector(f'#root[data-view-id="{view_id}"]', state="attached", timeout=15000)
    expect(page.locator("#icons .icon").first).to_be_visible(timeout=15000)


def _dock_tile(page: Page, address: str) -> Any:
    return page.locator(f'#dock .dock-item[data-address="{address}"]')


def _window(page: Page, address: str) -> Any:
    return page.locator(f'.window[data-address="{address}"]')


def _open_from_dock(page: Page, address: str) -> None:
    """Open a window on something already running, the way a user does: its tile in the dock.

    The dock is what is RUNNING machine-wide rather than what this desktop shows, so a tile is
    there for an instance the current view has never held -- which is how a chat listed under
    Everything is reached from inside a project.
    """
    tile = _dock_tile(page, address)
    expect(tile).to_be_visible(timeout=15000)
    tile.click()
    expect(_window(page, address)).to_be_visible(timeout=15000)


def _start_new_chat(page: Page) -> FrameLocator:
    """Click the desktop's Chat icon, which always starts a new one, and return the new window's frame."""
    expect(page.locator("#icons .icon").first).to_be_visible(timeout=15000)
    page.locator('#icons .icon[data-entry="chat"]').click()
    frame = page.frame_locator('iframe[data-address^="app:chat?instance="]')
    expect(page.locator('iframe[data-address^="app:chat?instance="]').first).to_be_attached(timeout=15000)
    return frame


def _open_fixture_chat(page: Page) -> None:
    """Open the fixture chat from its dock tile and wait for its transcript."""
    _open_from_dock(page, FIXTURE_CHAT_ADDRESS)
    expect(_chat(page).locator(".message-list").first).to_be_visible(timeout=15000)


def _broadcast_layout_op(base_url: str, op: str, args: dict[str, Any], view: str = STARTER_PROJECT_NAME) -> None:
    """POST a layout op to the loopback ``/api/layout/broadcast`` endpoint, retrying until the client has registered."""
    payload = json.dumps({"op": op, "args": {**args, "view": view}, "requester": FIXTURE_CHAT_ADDRESS}).encode()
    request = urllib.request.Request(
        f"{base_url}/api/layout/broadcast",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    def _attempt() -> bool:
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return bool(response.status == 200)
        except urllib.error.HTTPError as e:
            if e.code == 412:
                return False
            raise AssertionError(
                f"layout op {op!r} refused with HTTP {e.code}: {e.read().decode(errors='replace')}"
            ) from e
        except (TimeoutError, urllib.error.URLError):
            return False

    wait_for(
        _attempt,
        timeout=15.0,
        poll_interval=0.2,
        error_message=f"layout broadcast for op {op!r} never succeeded (client registration missing?)",
    )


def _switch_view(page: Page, base_url: str, view_name: str, view_id: str) -> None:
    """Move the client to another view.

    The desktop has no view UI this pass (the projects API and its tab sets are untouched, but
    nothing on screen switches between them), so the switch is driven the one way that exists:
    an agent's ``load``, which is also what a chat does when it sends someone to a project.
    """
    _broadcast_layout_op(base_url, "load", {}, view=view_name)
    _wait_for_view(page, view_id)


# ---------- the chat page ----------


@pytest.mark.timeout(60, func_only=False)
def test_chat_transcript_area_is_pure_white(e2e_server: RunningWorkspace, page: Page) -> None:
    """The chat conversation panel renders on a pure-white background, scoped to the chat token."""
    page.goto(e2e_server.shell_url)
    _open_fixture_chat(page)

    content = _chat(page).locator(".app-content")
    expect(content).to_be_visible(timeout=15000)
    expect(content.locator(".message-list")).to_have_count(1)

    content_bg = _chat_frame(page).eval_on_selector(".app-content", "e => getComputedStyle(e).backgroundColor")
    assert content_bg == "rgb(255, 255, 255)", f"chat transcript area should be pure white, got {content_bg}"
    footer_bg = _chat_frame(page).eval_on_selector(".app-footer", "e => getComputedStyle(e).backgroundColor")
    assert footer_bg == "rgb(255, 255, 255)", f"composer footer should be pure white, got {footer_bg}"
    shell_bg = page.eval_on_selector("html", "e => getComputedStyle(e).getPropertyValue('--color-bg').trim()")
    assert shell_bg not in ("#ffffff", "#fff", "rgb(255, 255, 255)"), (
        f"shared shell --color-bg should stay off-white, got {shell_bg}"
    )


@pytest.mark.timeout(60, func_only=False)
def test_conversation_and_composer_render(e2e_server: RunningWorkspace, page: Page) -> None:
    """The opened chat shows both sides of its conversation and a composer whose send button follows the text."""
    page.goto(e2e_server.shell_url)
    _open_fixture_chat(page)

    expect(_chat(page).locator(".message-user").first).to_contain_text("Hello agent!")
    expect(_chat(page).locator(".message-assistant").first).to_contain_text("Hello! How can I help you?")

    textarea = _chat(page).locator(".message-input-textbox")
    expect(textarea).to_be_visible(timeout=15000)
    send_button = _chat(page).locator(".message-input-send-button")
    expect(send_button).to_have_count(0)
    textarea.fill("test message")
    expect(send_button).to_be_visible()


@pytest.mark.timeout(60, func_only=False)
def test_composer_bar_survives_a_shorter_window(e2e_server: RunningWorkspace, page: Page) -> None:
    """A window that gets shorter keeps the whole composer on screen.

    Everything below the dock is positioned in pixels -- the panes, and the live surfaces
    mirroring them -- so a row that grows with the viewport but cannot shrink back leaves
    the chat laid out at the old height with the model bar below the bottom edge.
    """
    page.set_viewport_size({"width": 1200, "height": 900})
    page.goto(e2e_server.shell_url)
    _open_fixture_chat(page)

    under_bar = _chat(page).locator(".composer-under-bar")
    expect(under_bar).to_be_visible(timeout=15000)

    page.set_viewport_size({"width": 1200, "height": 848})
    expect(under_bar).to_be_visible()
    wait_for(
        lambda: _chat_frame(page).eval_on_selector(
            ".composer-under-bar", "e => e.getBoundingClientRect().bottom <= window.innerHeight"
        ),
        timeout=10.0,
        error_message="the composer's model bar stayed below the bottom of the shortened window",
    )


_TOOL_CALL_SESSION_EVENTS: list[dict[str, Any]] = [
    {
        "type": "user",
        "uuid": "uuid-tc-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": "user", "content": "Read test.txt"},
    },
    {
        "type": "assistant",
        "uuid": "uuid-tc-2",
        "timestamp": "2026-01-01T00:00:01Z",
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-6",
            "content": [
                {"type": "text", "text": "Let me read that file."},
                {"type": "tool_use", "id": "toolu_tc1", "name": "Read", "input": {"file": "test.txt"}},
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    },
    {
        "type": "user",
        "uuid": "uuid-tc-3",
        "timestamp": "2026-01-01T00:00:02Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_tc1", "content": "file contents here"}],
        },
    },
]


@pytest.mark.timeout(60, func_only=False)
def test_tool_calls_render_as_collapsible(tmp_path: Path, page: Page) -> None:
    """Tool calls render as collapsible blocks that expand to show input/output."""
    with _running_e2e_server(tmp_path, session_events=_TOOL_CALL_SESSION_EVENTS) as server:
        page.goto(server.shell_url)
        _open_fixture_chat(page)

        expect(_chat(page).locator(".message-assistant").first).to_be_visible(timeout=15000)
        tool_block = _chat(page).locator(".tool-call-block").first
        expect(tool_block).to_be_visible(timeout=10000)
        expect(tool_block).to_contain_text("Read")

        tool_details = _chat(page).locator(".tool-call-details").first
        expect(tool_details).to_be_hidden()
        _chat(page).locator(".tool-call-header").first.click()
        expect(tool_details).to_be_visible()
        expect(tool_details).to_contain_text("file contents here")


@pytest.mark.timeout(60, func_only=False)
def test_live_stream_delivers_new_events(e2e_server: RunningWorkspace, page: Page) -> None:
    """New events written to the session file appear in the UI as they stream in."""
    page.goto(e2e_server.shell_url)
    _open_fixture_chat(page)
    expect(_chat(page).locator(".message-user").first).to_be_visible(timeout=15000)

    new_event = {
        "type": "user",
        "uuid": "uuid-new-1",
        "timestamp": "2026-01-01T00:01:00Z",
        "message": {"role": "user", "content": "This is a new streamed message!"},
    }
    with open(e2e_server.session_file, "a") as f:
        f.write(json.dumps(new_event) + "\n")

    expect(_chat(page).locator(".message-user", has_text="This is a new streamed message!")).to_be_visible(
        timeout=10000
    )


# A conversation whose transcript ends with an unresolved enqueue, so the Claude queue
# populator surfaces one currently-queued message while a turn is in flight.
_QUEUED_SESSION_EVENTS: list[dict[str, Any]] = [
    {
        "type": "user",
        "uuid": "uuid-q-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": "user", "content": "Kick off the big refactor"},
    },
    {
        "type": "assistant",
        "uuid": "uuid-q-2",
        "timestamp": "2026-01-01T00:00:01Z",
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-6",
            "content": [{"type": "text", "text": "On it -- starting now."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 4},
        },
    },
    {
        "type": "user",
        "uuid": "uuid-q-3",
        "timestamp": "2026-01-01T00:00:03Z",
        "message": {"role": "user", "content": "Now run the tests"},
    },
    {
        "type": "queue-operation",
        "operation": "enqueue",
        "timestamp": "2026-01-01T00:00:05Z",
        "sessionId": "e2e-session-001",
        "content": "actually also update the changelog",
    },
]


@pytest.mark.timeout(60, func_only=False)
def test_queued_message_group_renders_with_actions(tmp_path: Path, page: Page) -> None:
    """A harness-queued message renders as a distinct group with the shoulder-tap action."""
    with _running_e2e_server(tmp_path, session_events=_QUEUED_SESSION_EVENTS) as server:
        page.goto(server.shell_url)
        _open_fixture_chat(page)

        expect(_chat(page).locator(".message-user", has_text="Kick off the big refactor").first).to_be_visible(
            timeout=15000
        )
        group = _chat(page).locator(".queued-group")
        expect(group).to_be_visible(timeout=15000)
        expect(_chat(page).locator(".queued-message .message-user-bubble .message-content")).to_contain_text(
            "actually also update the changelog"
        )
        expect(_chat(page).locator(".queued-header-label")).to_contain_text("Queued messages")
        flush_button = _chat(page).locator(".queued-action--flush")
        expect(flush_button).to_be_visible()
        expect(flush_button).to_contain_text("Shoulder tap")
        expect(_chat(page).locator(".queued-action--interrupt")).to_have_count(0)


@pytest.mark.timeout(60, func_only=False)
def test_chat_recovers_from_a_failed_transcript_load(tmp_path: Path, page: Page) -> None:
    """A chat whose transcript fetch failed recovers on Refresh, without reloading the page."""
    with _running_e2e_server(tmp_path) as server:
        events_url = "**/api/chats/*/events"
        page.route(
            events_url,
            lambda route: route.fulfill(status=503, content_type="text/plain", body="Backend not yet available"),
        )
        page.goto(server.shell_url)
        _open_from_dock(page, FIXTURE_CHAT_ADDRESS)

        error = _chat(page).locator(".message-list-error")
        expect(error).to_be_visible(timeout=15000)
        expect(error.locator("p")).to_have_text("Error: request failed (HTTP 503)")

        page.unroute(events_url)
        error.locator(".message-list-reload").click()

        expect(_chat(page).locator(".message-user", has_text="Hello agent!").first).to_be_visible(timeout=15000)
        expect(_chat(page).locator(".message-list-error")).to_have_count(0)


# ---------- layout ops ----------


def _make_long_conversation_events(pair_count: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for i in range(pair_count):
        events.append(
            {
                "type": "user",
                "uuid": f"long-u-{i}",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": f"msg-{i}"},
            }
        )
        events.append(
            {
                "type": "assistant",
                "uuid": f"long-a-{i}",
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-6",
                    "content": [{"type": "text", "text": f"reply-{i}"}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            }
        )
    return events


def _visible_user_messages(page: Page) -> list[str]:
    return _chat_frame(page).evaluate(
        "() => Array.from(document.querySelectorAll('.message-user')).map((e) => (e.textContent || '').trim())"
    )


def _min_message_index(messages: list[str]) -> int:
    indices = [int(m[len("msg-") :]) for m in messages if m.startswith("msg-") and m[len("msg-") :].isdigit()]
    return min(indices) if indices else -1


@pytest.mark.timeout(120, func_only=False)
def test_a_minimized_window_preserves_its_chats_scroll_window(tmp_path: Path, page: Page) -> None:
    """Putting a chat's window away (and bringing it back) must not move its loaded window.

    A minimized window keeps its page loaded and merely stops showing it, with ``display: none``:
    the page keeps running and its scroll element reports every metric as 0, which the paging
    logic must not read as a jump to the very start of the conversation.
    """
    events = _make_long_conversation_events(150)
    with _running_e2e_server(tmp_path, session_events=events) as server:
        page.goto(server.shell_url)
        _wait_for_view(page, STARTER_PROJECT_ID)
        _open_fixture_chat(page)
        _chat_frame(page).wait_for_function(
            "() => { const el = document.querySelector('.app-content'); return el && el.scrollHeight > el.clientHeight * 2; }",
            timeout=15000,
        )
        # The shell edits the client's saved arrangement, so the window the browser just opened
        # has to be saved before an op can name it.
        _wait_for_layout_saved(server.state_dir, STARTER_PROJECT_ID, containing=FIXTURE_CHAT_ADDRESS)
        page.wait_for_timeout(1000)

        _chat_frame(page).evaluate(
            "() => { const el = document.querySelector('.app-content'); el.scrollTop = el.scrollHeight - el.clientHeight - 1500; }"
        )
        page.wait_for_timeout(1000)
        before_hidden = _visible_user_messages(page)
        scroll_top_before = _chat_frame(page).evaluate("() => document.querySelector('.app-content').scrollTop")
        assert before_hidden, "expected user messages to be rendered after scrolling up"
        assert "msg-0" not in before_hidden, f"setup should not be at the start: {before_hidden[:3]}"
        anchor_message = before_hidden[0]
        assert _min_message_index(before_hidden) >= 50, f"setup should be reading mid-history: {before_hidden[:3]}"

        # The window's own minimize button: the page stays loaded, and nothing is showing it.
        _window(page, FIXTURE_CHAT_ADDRESS).locator('.win-btn[data-action="minimize"]').click()
        _chat_frame(page).wait_for_function(
            "() => { const el = document.querySelector('.app-content'); return el && el.clientHeight === 0; }",
            timeout=_TRIGGER_TIMEOUT_MS,
        )

        with open(server.session_file, "a") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "user",
                        "uuid": "long-u-streamed",
                        "timestamp": "2026-01-01T00:02:00Z",
                        "message": {"role": "user", "content": "streamed-while-hidden"},
                    }
                )
                + "\n"
            )
        page.wait_for_timeout(3000)

        during_hidden = _visible_user_messages(page)
        assert anchor_message in during_hidden, (
            f"a minimized window lost its place: anchor {anchor_message!r} no longer rendered ({during_hidden[:3]}...)"
        )
        assert "msg-0" not in during_hidden, (
            f"a minimized window jumped to the start of the conversation: {during_hidden[:3]}"
        )

        # The dock tile brings it back, which is the gesture that undoes a minimize.
        _dock_tile(page, FIXTURE_CHAT_ADDRESS).click()
        _chat_frame(page).wait_for_function(
            "() => { const el = document.querySelector('.app-content'); return el && el.clientHeight > 0; }",
            timeout=_TRIGGER_TIMEOUT_MS,
        )
        page.wait_for_timeout(1000)
        after_restore = _visible_user_messages(page)
        scroll_top_after = _chat_frame(page).evaluate("() => document.querySelector('.app-content').scrollTop")
        assert "msg-0" not in after_restore, (
            f"after the window came back it jumped to the start: {after_restore[:3]}"
        )
        assert anchor_message in after_restore, (
            f"after the window came back the reader was not returned to their place: {after_restore[:3]}"
        )
        assert abs(scroll_top_after - scroll_top_before) < 50, (
            f"scroll position drifted across minimize/restore: {scroll_top_before} -> {scroll_top_after}"
        )


# ---------- projects and views ----------


@pytest.mark.timeout(120, func_only=False)
def test_switching_views_preserves_chat_transcript(tmp_path: Path, page: Page) -> None:
    """A chat window restored by a view switch still shows its own transcript.

    Each view has its own desktop, and the chat's page is one page machine-wide: opening the
    chat under Everything and then coming back to the project rebuilds the project's desktop,
    whose window must bind to that same page rather than to an empty one.
    """
    with _running_e2e_server(tmp_path) as server:
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(server.shell_url)
        _wait_for_view(page, STARTER_PROJECT_ID)
        _open_fixture_chat(page)
        expect(_chat(page).locator(".message-user", has_text="Hello agent!").first).to_be_visible(timeout=15000)
        _wait_for_layout_saved(server.state_dir, STARTER_PROJECT_ID, containing=FIXTURE_CHAT_ADDRESS)

        _switch_view(page, server.shell_url, EVERYTHING_VIEW_NAME, EVERYTHING_VIEW_ID)
        # A desktop nobody has arranged in this view holds no windows, so the chat is opened again.
        expect(_window(page, FIXTURE_CHAT_ADDRESS)).to_have_count(0)
        _open_from_dock(page, FIXTURE_CHAT_ADDRESS)
        expect(_chat(page).locator(".message-user", has_text="Hello agent!").first).to_be_visible(timeout=15000)
        _wait_for_layout_saved(server.state_dir, EVERYTHING_VIEW_ID, containing=FIXTURE_CHAT_ADDRESS)

        _switch_view(page, server.shell_url, STARTER_PROJECT_NAME, STARTER_PROJECT_ID)
        expect(_window(page, FIXTURE_CHAT_ADDRESS)).to_be_visible(timeout=15000)
        expect(_chat(page).locator(".message-user", has_text="Hello agent!").first).to_be_visible(timeout=15000)
        expect(_chat(page).locator(".message-list-empty")).to_have_count(0)
        expect(_chat(page).locator(".message-list-not-found")).to_have_count(0)


# ---------- starting a chat ----------


@pytest.mark.timeout(120, func_only=False)
def test_a_new_chat_with_nothing_signed_in_offers_the_provider_chooser_in_its_own_tab(
    tmp_path: Path, page: Page
) -> None:
    """The tab opens either way: with no account the chat waits for one, and its page shows the
    chooser, so signing in happens where the chat will be rather than on the shell."""
    with _running_e2e_server(tmp_path, is_account_signed_in=False) as server:
        page.goto(server.shell_url)
        chat = _start_new_chat(page)
        expect(chat.locator(".message-list-awaiting-account")).to_contain_text(
            "Sign in to a provider to start this chat", timeout=15000
        )
        expect(chat.locator('[data-e2e="provider-chooser"]')).to_be_visible(timeout=10000)
        # The shell itself renders no chooser: the sign-in lives in the chat's page.
        assert page.locator('[data-e2e="provider-chooser"]').count() == 0
        # The chat is listed as an instance waiting on the user, so the tab is back on reload
        # (once the arrangement holding it has been saved).
        _wait_for_layout_saved(server.state_dir, STARTER_PROJECT_ID, containing="app:chat?instance=")
        page.reload()
        chat = page.frame_locator('iframe[data-address^="app:chat?instance="]')
        expect(chat.locator(".message-list-awaiting-account")).to_be_visible(timeout=15000)


@pytest.mark.timeout(120, func_only=False)
def test_a_new_chat_with_an_account_starts_at_once_and_shows_its_composer_when_it_lands(
    tmp_path: Path, page: Page
) -> None:
    """With an account signed in the create runs immediately on it; the page says so while the
    create runs, and the composer arrives when the agent registers."""
    with _running_e2e_server(tmp_path) as server:
        page.goto(server.shell_url)
        chat = _start_new_chat(page)
        expect(chat.locator(".message-list-creating")).to_contain_text("Starting the chat", timeout=15000)
        expect(chat.locator(".message-input-textbox")).to_be_visible(timeout=15000)
        expect(chat.locator(".message-list-creating")).to_have_count(0, timeout=15000)
        assert chat.locator('[data-e2e="provider-chooser"]').count() == 0


@pytest.mark.timeout(120, func_only=False)
def test_a_create_that_fails_keeps_the_tab_with_the_reason_and_a_retry(
    tmp_path: Path, page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed ``mngr create`` is a notice in the chat's own tab, with what mngr printed and a
    "Try again" on the same account, not a tab that vanishes; the retry lands the chat under
    the same id once mngr cooperates."""
    monkeypatch.setenv("FAKE_MNGR_CREATE_EXIT_CODE", "3")
    with _running_e2e_server(tmp_path) as server:
        page.goto(server.shell_url)
        chat = _start_new_chat(page)
        failed = chat.locator(".message-list-create-failed")
        expect(failed).to_contain_text("This chat could not be started", timeout=20000)
        expect(failed).to_contain_text("exited with code 3")
        expect(failed).to_contain_text("create failed on purpose")
        expect(failed.locator(".message-list-create-retry")).to_be_visible()
        # The composer stays under the notice: a message held through the failure is back in it.
        expect(chat.locator(".message-input-textbox")).to_be_visible()
        # The instance stays listed, in the error state, so the tab survives a reload.
        _wait_for_layout_saved(server.state_dir, STARTER_PROJECT_ID, containing="app:chat?instance=")
        page.reload()
        chat = page.frame_locator('iframe[data-address^="app:chat?instance="]')
        expect(chat.locator(".message-list-create-failed")).to_be_visible(timeout=15000)
        # The retry runs the create again on the same account (the fake mngr reads its exit
        # status per run), and the composer replaces the notice when the agent registers.
        monkeypatch.delenv("FAKE_MNGR_CREATE_EXIT_CODE")
        chat.locator(".message-list-create-retry").click()
        expect(chat.locator(".message-input-textbox")).to_be_visible(timeout=20000)
        expect(chat.locator(".message-list-create-failed")).to_have_count(0, timeout=15000)


# ---------- switching a chat to another harness (the handoff, spec section 5) ----------


def _open_provider_menu(chat: FrameLocator) -> None:
    """Open the composer's model card and its provider menu."""
    chat.locator(".model-selector-trigger").click()
    chat.locator('[data-card-row="providers"]').click()
    expect(chat.locator('[data-model-popover="flyout"]')).to_be_visible()


def _choose_pending_account(chat: FrameLocator, provider: str, label: str) -> None:
    """Press an account in the provider menu and arm the switch from the dialog it opens.

    The row shows the provider and the harness as two spans, so it is found by the provider word;
    the strip above the composer then names the account by its composed label.
    """
    _open_provider_menu(chat)
    chat.locator('[data-model-popover="flyout"] button', has_text=provider).first.click()
    dialog = chat.locator(".modal-card")
    expect(dialog).to_contain_text("Switch to")
    dialog.get_by_role("button", name="Switch this chat").click()
    expect(chat.locator(".message-input-switch-strip")).to_contain_text(
        f"Your next message switches this chat to {label}"
    )


def _switch_and_send(chat: FrameLocator, message: str) -> None:
    """Type the message and press Switch and send; the choice was made in the dialog, so nothing asks again."""
    chat.locator(".message-input-textbox").fill(message)
    switch_button = chat.locator(".message-input-send-button--switch")
    expect(switch_button).to_contain_text("Switch and send")
    switch_button.click()


def _settled_chat_snapshot(server: RunningWorkspace) -> ChatSnapshot:
    """The chat's snapshot once its switch has left the record.

    The node turns "done" on the ``agent_switch`` marker, which the backend emits when it adopts
    the successor -- before it delivers the prompt and the held sends and clears the record -- so
    the page runs ahead of the record by those deliveries. Reading the snapshot the instant the
    page says done races them.
    """
    manager = server.chat_state.agent_manager

    def _settled() -> bool:
        snapshot = manager.get_chat_snapshot(FIXTURE_AGENT_ID)
        return snapshot is not None and snapshot.handoff is None

    wait_for(_settled, timeout=30.0, poll_interval=0.1, error_message="the switch never left the chat's record")
    snapshot = manager.get_chat_snapshot(FIXTURE_AGENT_ID)
    assert snapshot is not None
    return snapshot


def _switched_workspace(
    tmp_path: Path,
    messenger: MngrMessenger | None = None,
    additional_accounts: tuple[tuple[str, str], ...] = (("openai", "OpenAI"),),
    session_events: Sequence[Mapping[str, Any]] | None = None,
) -> AbstractContextManager[RunningWorkspace]:
    return running_workspace(
        tmp_path,
        free_port(),
        free_port(),
        session_events=session_events,
        additional_accounts=additional_accounts,
        messenger=messenger,
    )


# A transcript with no user turn: the seeded welcome, as claude records a slash command, and the
# agent's greeting. A switch off it is a fresh start.
_WELCOME_ONLY_SESSION_EVENTS: list[dict[str, Any]] = [
    {
        "type": "user",
        "uuid": "uuid-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {
            "role": "user",
            "content": "<command-message>welcome</command-message>\n<command-name>/welcome</command-name>",
        },
    },
    {
        "type": "assistant",
        "uuid": "uuid-2",
        "timestamp": "2026-01-01T00:00:01Z",
        "message": {
            "role": "assistant",
            "model": "claude-opus-4-6",
            "content": [{"type": "text", "text": "Welcome! What shall we build?"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    },
]


@pytest.mark.timeout(90, func_only=False)
def test_a_chat_with_no_user_turn_switches_at_once_and_leaves_no_handoff_node(tmp_path: Path, page: Page) -> None:
    """A fresh start through the browser: with only the welcome behind it, pressing another account asks nothing
    and switches at once, the live node stands in while the switch runs, and once it has landed nothing stands
    between the two agents' turns."""
    with _switched_workspace(tmp_path, session_events=_WELCOME_ONLY_SESSION_EVENTS) as server:
        page.goto(server.shell_url)
        _open_fixture_chat(page)
        chat = _chat(page)
        expect(chat.locator(".message-input-textbox")).to_be_visible(timeout=15000)
        expect(chat.locator(".message-list")).to_contain_text("Welcome! What shall we build?")
        _open_provider_menu(chat)
        chat.locator('[data-model-popover="flyout"] button', has_text="OpenAI").first.click()

        # Nothing to hand over, so nothing asks and nothing is armed: the switch runs at once, and the
        # live node reports it while it does.
        expect(chat.locator('[data-handoff-status="active"]')).to_contain_text("Handing off to Codex")
        expect(chat.locator(".modal-card")).to_have_count(0)
        expect(chat.locator(".message-input-switch-strip")).to_have_count(0)

        manager = server.chat_state.agent_manager

        def is_switched() -> bool:
            snapshot = manager.get_chat_snapshot(FIXTURE_AGENT_ID)
            return snapshot is not None and snapshot.handoff is None and snapshot.active_agent.harness.value == "codex"

        wait_for(is_switched, timeout=30.0)
        # No summary was asked of the retiring agent, and no node is left behind once the switch has
        # landed: the greeting still reads as Claude's reply, with the successor's segment straight after.
        messenger = manager._messenger
        assert isinstance(messenger, RecordingMngrMessenger)
        assert messenger.sent == []
        expect(chat.locator("[data-handoff-status]")).to_have_count(0, timeout=15000)
        expect(chat.locator(".message-list")).to_contain_text("Welcome! What shall we build?")
        snapshot = manager.get_chat_snapshot(FIXTURE_AGENT_ID)
        assert snapshot is not None and len(snapshot.agent_ids) == 2
        chat.locator(".model-selector-trigger").click()
        expect(chat.locator('[data-card-row="providers"]')).to_contain_text("OpenAI")


@pytest.mark.timeout(90, func_only=False)
def test_a_chat_switches_to_another_harness_from_the_page(tmp_path: Path, page: Page) -> None:
    """The whole switch through the browser: the dialog, the armed strip, Switch and send, the held message,
    the handoff node's progress and completion, and the provider row on the new account."""
    with _switched_workspace(tmp_path, messenger=SummaryWritingMngrMessenger()) as server:
        page.goto(server.shell_url)
        _open_fixture_chat(page)
        chat = _chat(page)
        expect(chat.locator(".message-input-textbox")).to_be_visible(timeout=15000)
        # An ordinary send button until a lane is pending.
        chat.locator(".message-input-textbox").fill("draft")
        expect(chat.locator(".message-input-send-button--switch")).to_have_count(0)
        chat.locator(".message-input-textbox").fill("")

        _choose_pending_account(chat, "OpenAI", "OpenAI (Codex)")
        _switch_and_send(chat, "Carry on in Codex")

        # The typed message stays visible as a held bubble, the handoff node reports the switch's
        # progress, and the strip above the composer reports the switch rather than a turn.
        held = chat.locator(".held-send", has_text="Carry on in Codex")
        expect(held).to_be_visible(timeout=15000)
        expect(chat.locator('[data-handoff-status="active"]')).to_contain_text("Handing off to Codex")
        expect(chat.locator('.agent-activity-indicator[data-state^="HANDOFF_"]')).to_be_visible()
        expect(chat.locator(".message-input-send-button--switch")).to_have_count(0)
        expect(chat.locator(".message-input-switch-strip")).to_have_count(0)

        # The switch completes against the fake mngr: the node closes live, and the chat is on Codex.
        expect(chat.locator('[data-handoff-status="done"]')).to_contain_text(
            "Handed off from Claude Code to Codex", timeout=30000
        )
        snapshot = _settled_chat_snapshot(server)
        # The typed message rode inside the successor's prompt, so the switch marker shows it as the
        # successor's opening bubble, and the held bubble that stood in for it is gone.
        opening = chat.locator('.message-list .message-user[id$=":message"]')
        expect(opening).to_have_count(1)
        expect(opening).to_contain_text("Carry on in Codex")
        expect(chat.locator(".outgoing-message")).to_have_count(0)
        assert snapshot.active_agent.harness.value == "codex"
        assert snapshot.active_agent.account_id == server.account_ids[1]
        assert len(snapshot.agent_ids) == 2
        # The provider row follows the new account, and the armed switch is spent.
        chat.locator(".model-selector-trigger").click()
        provider_row = chat.locator('[data-card-row="providers"]')
        expect(provider_row).to_contain_text("OpenAI")
        expect(provider_row).not_to_contain_text("after your next message")


@pytest.mark.timeout(90, func_only=False)
def test_a_chat_changes_account_in_place_from_the_page(tmp_path: Path, page: Page) -> None:
    """The rebind through the browser: a second account on the chat's own lane is a switch too, armed at once
    with no dialog, and the chat comes back on the same agent with its transcript, now read from the new
    account's folder."""
    with _switched_workspace(tmp_path, additional_accounts=(("anthropic", "Anthropic"),)) as server:
        page.goto(server.shell_url)
        _open_fixture_chat(page)
        chat = _chat(page)
        expect(chat.locator(".message-input-textbox")).to_be_visible(timeout=15000)
        expect(chat.locator(".message-list")).to_contain_text("Hello agent!")
        _open_provider_menu(chat)
        chat.locator('[data-model-popover="flyout"] button', has_text="Anthropic 2").first.click()
        # A rebind keeps the agent and its conversation, so nothing asks: the press arms the switch at
        # once, and the strip offers no dialog to change it from.
        expect(chat.locator(".message-input-switch-strip")).to_contain_text("Anthropic 2 (Claude Code)")
        expect(chat.locator(".modal-card")).to_have_count(0)
        expect(chat.locator(".message-input-switch-change")).to_have_count(0)

        chat.locator(".message-input-textbox").fill("Carry on on the other account")
        switch_button = chat.locator(".message-input-send-button--switch")
        expect(switch_button).to_contain_text("Switch and send")
        switch_button.click()

        # The rebind runs against the fake mngr and lands the same agent on the second account.
        manager = server.chat_state.agent_manager
        second_account = server.account_ids[1]

        def is_rebound() -> bool:
            snapshot = manager.get_chat_snapshot(FIXTURE_AGENT_ID)
            return (
                snapshot is not None
                and snapshot.handoff is None
                and snapshot.active_agent.account_id == second_account
            )

        wait_for(is_rebound, timeout=30.0)
        snapshot = manager.get_chat_snapshot(FIXTURE_AGENT_ID)
        assert snapshot is not None and snapshot.agent_ids == (FIXTURE_AGENT_ID,)
        # The session file followed the agent into the new account's folder, and the confirming
        # message went through the ordinary send path once the agent was back.
        assert list((account_dir(second_account) / "projects").rglob(f"{FIXTURE_SESSION_ID}.jsonl"))
        messenger = manager._messenger
        assert isinstance(messenger, RecordingMngrMessenger)
        wait_for(lambda: (FIXTURE_AGENT_ID, "Carry on on the other account") in messenger.sent, timeout=10.0)
        # The page keeps the transcript, no handoff node remains (the agent did not change), the held
        # bubble is gone, and the provider row names the new account with the choice spent.
        expect(chat.locator(".message-list")).to_contain_text("Hello agent!")
        expect(chat.locator(".held-send")).to_have_count(0, timeout=15000)
        expect(chat.locator("[data-handoff-status]")).to_have_count(0)
        expect(chat.locator(".message-input-cancel-switch-button")).to_have_count(0)
        chat.locator(".model-selector-trigger").click()
        provider_row = chat.locator('[data-card-row="providers"]')
        expect(provider_row).to_contain_text("Anthropic 2")
        expect(provider_row).not_to_contain_text("after your next message")


@pytest.mark.timeout(90, func_only=False)
def test_a_switch_is_cancelled_while_the_summary_is_written_and_the_message_comes_back(
    tmp_path: Path, page: Page
) -> None:
    """Cancel during summarizing: the confirming message returns to the composer, the pending lane
    stays, and the chat is still its one agent."""
    with _switched_workspace(tmp_path) as server:
        page.goto(server.shell_url)
        _open_fixture_chat(page)
        chat = _chat(page)
        expect(chat.locator(".message-input-textbox")).to_be_visible(timeout=15000)
        _choose_pending_account(chat, "OpenAI", "OpenAI (Codex)")
        _switch_and_send(chat, "Carry on in Codex")

        # The recording messenger never writes the summary, so summarizing lasts the idle grace
        # period, long enough to call the switch off. The node reports the switch meanwhile.
        cancel = chat.locator(".message-input-cancel-switch-button")
        expect(cancel).to_be_visible(timeout=15000)
        expect(chat.locator('[data-handoff-status="active"]')).to_contain_text("Handing off to Codex")
        cancel.click()

        expect(chat.locator(".message-input-textbox")).to_have_value("Carry on in Codex", timeout=15000)
        expect(chat.locator(".message-input-cancel-switch-button")).to_have_count(0)
        expect(chat.locator(".held-send")).to_have_count(0)
        # The armed switch survives the cancel, so the next send offers it again. The recording
        # messenger never lands the summary request in the fixture transcript, so no node is left
        # behind here (a real agent records the request, and the node then reads as called off:
        # ``handoff-node.test.ts``).
        expect(chat.locator(".message-input-send-button--switch")).to_contain_text("Switch and send")
        snapshot = server.chat_state.agent_manager.get_chat_snapshot(FIXTURE_AGENT_ID)
        assert snapshot is not None and snapshot.handoff is None and len(snapshot.agent_ids) == 1
        expect(chat.locator("[data-handoff-status]")).to_have_count(0)


@pytest.mark.timeout(120, func_only=False)
def test_a_failed_switch_shows_its_reason_and_retries_on_a_third_account(
    tmp_path: Path, page: Page, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A create that fails leaves the failed page over the composer; a retry on another account completes."""
    with _switched_workspace(
        tmp_path,
        messenger=SummaryWritingMngrMessenger(),
        additional_accounts=(("openai", "OpenAI"), ("google", "Google")),
    ) as server:
        page.goto(server.shell_url)
        _open_fixture_chat(page)
        chat = _chat(page)
        expect(chat.locator(".message-input-textbox")).to_be_visible(timeout=15000)
        _choose_pending_account(chat, "OpenAI", "OpenAI (Codex)")
        # The fake mngr's create fails while this is set; the successor's create inherits it.
        monkeypatch.setenv("FAKE_MNGR_CREATE_EXIT_CODE", "3")
        _switch_and_send(chat, "Carry on in Codex")

        notice = chat.locator(".handoff-failed-notice")
        expect(notice).to_be_visible(timeout=30000)
        expect(notice.locator(".handoff-failed-title")).to_have_text("Could not start Codex")
        expect(notice.locator(".handoff-failed-reason")).to_contain_text("mngr create exited with code 3")
        expect(chat.locator(".held-send", has_text="Carry on in Codex")).to_be_visible()
        snapshot = server.chat_state.agent_manager.get_chat_snapshot(FIXTURE_AGENT_ID)
        assert snapshot is not None and snapshot.handoff is not None
        assert snapshot.handoff.phase.value == "failed" and snapshot.status.value == "error"

        # Retry on the third account, with the create working again.
        monkeypatch.delenv("FAKE_MNGR_CREATE_EXIT_CODE")
        notice.locator(".handoff-retry-account").select_option(server.account_ids[2])
        notice.locator(".handoff-retry-button").click()

        expect(chat.locator('[data-handoff-status="done"]')).to_contain_text(
            "Handed off from Claude Code to Antigravity CLI", timeout=30000
        )
        expect(chat.locator(".handoff-failed-notice")).to_have_count(0)
        settled = _settled_chat_snapshot(server)
        assert settled.active_agent.harness.value == "antigravity"
        assert settled.active_agent.account_id == server.account_ids[2]
        # The lane picked before the failure is spent too: the next send is an ordinary one.
        chat.locator(".model-selector-trigger").click()
        provider_row = chat.locator('[data-card-row="providers"]')
        expect(provider_row).to_contain_text("Google")
        expect(provider_row).not_to_contain_text("next:")
