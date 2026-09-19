"""End-to-end tests for the workspace shell using Playwright.

These tests start a real Flask server (threaded Werkzeug) over a registry of stub apps served by
``app_instances``' in-memory source over loopback, then use Playwright to drive the shell exactly
as a user would. Every open goes through the New Tab page or a rail row, every verb through the
shell's relay, and every assertion on state reads the shell's own API or files. The shell knows no
app by name, so a stub app is every app.
"""

from __future__ import annotations

import contextlib
import json
import re
import struct
import threading
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any
from typing import Generator

import pytest
from app_instances.blueprint import build_instances_app
from app_instances.nudge import ShellNudger
from app_instances.sidecar import serve_in_background
from app_instances.testing import LOOPBACK_HOST
from app_instances.testing import StubInstanceSource
from app_instances.data_types import InstanceStatus
from app_instances.testing import free_port
from app_manifest.primitives import AppName
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect
from pydantic import Field

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.mngr.utils.polling import poll_until
from imbue.mngr.utils.polling import wait_for
from imbue.system_interface.config import Config
from imbue.system_interface.server import create_application
from imbue.system_interface.shell.primitives import EVERYTHING_VIEW_ID
from imbue.system_interface.shell.testing import instance_record
from imbue.system_interface.shell.testing import registry_row_toml
from imbue.system_interface.shell.testing import write_registry
from imbue.system_interface.testing import FakeTemplateCatalogFetcher
from imbue.system_interface.testing import build_test_state
from imbue.system_interface.testing import catalog_document
from imbue.system_interface.testing import catalog_template_document
from imbue.system_interface.testing import is_e2e_browser_installed
from imbue.system_interface.wsgi import make_threaded_server


def _playwright_browsers_installed() -> bool:
    """Check whether a launchable browser is present (Fortress or Playwright's cache)."""
    return is_e2e_browser_installed()


def _frontend_built() -> bool:
    """Check whether the frontend has been built (``static/index.html`` exists).

    Without a build the Flask server serves a "Frontend not built" placeholder, so
    every e2e test would ``page.goto()`` and then burn its per-test timeout waiting
    for selectors that can never appear. The path is resolved relative to this test
    module so it holds regardless of the cwd.
    """
    return (Path(__file__).parent / "static" / "index.html").is_file()


pytestmark = [
    pytest.mark.release,
    pytest.mark.skipif(not _playwright_browsers_installed(), reason="Playwright browsers not installed"),
    pytest.mark.skipif(
        not _frontend_built(),
        reason=("System interface frontend not built (run `cd system && npm run build`); skipping e2e."),
    ),
]

_PORT = 18765
_BASE_URL = f"http://127.0.0.1:{_PORT}"

# The one project every server starts with unless a test asks for none: what a migrated
# workspace has, and where a fresh browser lands (the first project, before Everything).
STARTER_PROJECT_NAME = "Project 1"
STARTER_PROJECT_ID = "project-1"
EVERYTHING_VIEW_NAME = "Everything"

# The stub app the machine offers: a multi-instance app whose instances live in memory,
# created by its ``new`` action and titled "Stub N". Every server seeds it with the fixture
# instance, the one the tests open first.
_STUB_APP_NAME = "docs"
_STUB_APP_DISPLAY_NAME = "Docs"
_STUB_NEW_ACTION_LABEL = "New docs"
_FIXTURE_KEY = "stub-1"
# The desktop's own launcher entry, which is not an app (``MAKE_SOMETHING_ENTRY`` in icons.ts).
MAKE_SOMETHING_ENTRY = "make-something"
_FIXTURE_TITLE = "Stub 1"
_FIXTURE_ADDRESS = f"app:{_STUB_APP_NAME}?instance={_FIXTURE_KEY}"

# A second stub app, offered when a test needs two apps on the machine (the launcher's filter).
_SECOND_APP_NAME = "notes"
_SECOND_APP_DISPLAY_NAME = "Notes"
_SECOND_APP_ADDRESS = f"app:{_SECOND_APP_NAME}?instance=stub-1"

# What a stub tab reads, and what a fresh stub instance's address looks like.
_STUB_TAB_TITLE_RE = re.compile(r"^Stub \d+$")

_TRIGGER_TIMEOUT_MS = 20000

# A one-template catalog for the New Tab page's "Start from a template" section: one shelf, one
# card, published from a repository the adopt message names.
_CATALOG_TEMPLATE_SLUG = "inbox-digest"
_CATALOG_TEMPLATE_TITLE = "Inbox Digest"
_CATALOG_TEMPLATE_REPOSITORY_URL = "https://github.com/someone/inbox-digest"
_CATALOG_DOCUMENT = catalog_document(
    catalog_template_document(
        _CATALOG_TEMPLATE_SLUG,
        title=_CATALOG_TEMPLATE_TITLE,
        description="A digest of your inbox.",
        what_it_is="Turns a noisy inbox into a scannable digest.",
        author="someone",
        repository_url=_CATALOG_TEMPLATE_REPOSITORY_URL,
        thumbnail="",
    ),
    shelves=[{"key": "popular", "title": "Most popular", "slugs": [_CATALOG_TEMPLATE_SLUG]}],
)


class E2EServer(FrozenModel):
    """Handle to a running e2e server and its fixtures."""

    model_config = {"arbitrary_types_allowed": True}

    base_url: str = Field(description="The shell's loopback URL")
    state_dir: Path = Field(description="The shell's state directory")
    stub_source: StubInstanceSource = Field(description="The stub app's in-memory instances")
    stub_url: str = Field(description="The stub app's loopback URL, where its pages are framed from")
    second_source: StubInstanceSource | None = Field(description="The second stub app's instances, when offered")


def _post_json(url: str, body: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def _post_no_content(url: str, body: dict[str, Any]) -> None:
    """POST a route that answers ``204``, which the JSON helper cannot read."""
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 204, f"{url} answered {response.status}"


def _get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


@contextlib.contextmanager
def _running_e2e_server(
    tmp_path: Path,
    port: int,
    stub_instances: tuple[str, ...] = (_FIXTURE_KEY,),
    is_stub_stoppable: bool = False,
    stopped_instances: tuple[str, ...] = (),
    is_second_app_offered: bool = False,
    project_names: tuple[str, ...] = (STARTER_PROJECT_NAME,),
    is_stub_taking_message: bool = False,
    is_stub_searching: bool = False,
    is_stub_browsing: bool = False,
    is_catalog_offered: bool = False,
    catalog_body: bytes | None = None,
) -> Generator[E2EServer, None, None]:
    """Run the shell with a stub app whose ``stub_instances`` are seeded as records titled after their keys.

    ``project_names`` are created through the shell's API before the browser lands, so the
    client's first view is the first of them (or Everything when there are none). Nothing is
    auto-opened: the first landing is the New Tab page. ``is_stub_taking_message`` declares a
    ``message`` param on the stub's ``new`` action, which is what makes it the app the page's seeded
    prompts go to. With ``is_catalog_offered`` the shell has a template catalog URL, answered by
    ``catalog_body`` -- or by nothing, so the page sees the catalog fail to load.
    """
    base_url = f"http://127.0.0.1:{port}"
    registry_path = tmp_path / "registry" / "apps.toml"
    stub_source = StubInstanceSource()
    stub_source.is_stoppable = is_stub_stoppable or bool(stopped_instances)
    for key in stub_instances:
        stub_source.records.append(
            instance_record(
                key,
                title=f"Stub {key.removeprefix('stub-')}",
                status=InstanceStatus.STOPPED if key in stopped_instances else InstanceStatus.IDLE,
                is_stoppable=stub_source.is_stoppable,
            )
        )
    stub_port = free_port()
    stub_url = f"http://{LOOPBACK_HOST}:{stub_port}"
    stub_source.is_searchable = is_stub_searching
    rows = [
        registry_row_toml(
            _STUB_APP_NAME,
            stub_url,
            is_multi_instance=True,
            actions=(("new", _STUB_NEW_ACTION_LABEL),),
            default_shortcut=("new", "focus"),
            display_name=_STUB_APP_DISPLAY_NAME,
            action_params={"new": ("message",)} if is_stub_taking_message else None,
            instance_search=is_stub_searching,
            browses_instances=is_stub_browsing,
        )
    ]
    second_source: StubInstanceSource | None = None
    second_port = free_port()
    if is_second_app_offered:
        second_source = StubInstanceSource()
        second_source.records.append(instance_record("stub-1", title="Note 1"))
        rows.append(
            registry_row_toml(
                _SECOND_APP_NAME,
                f"http://{LOOPBACK_HOST}:{second_port}",
                is_multi_instance=True,
                actions=(("new", "New notes"),),
                default_shortcut=("new", "focus"),
                display_name=_SECOND_APP_DISPLAY_NAME,
            )
        )
    write_registry(registry_path, *rows)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setenv("MINDS_APPS_FILE", str(registry_path))
        monkeypatch.setenv("MINDS_WORKSPACE_SERVER_URL", base_url)
        state_dir = tmp_path / "shell-state"
        config = Config(system_interface_host="127.0.0.1", system_interface_port=port)
        catalog_fetcher: FakeTemplateCatalogFetcher | None = None
        if is_catalog_offered:
            catalog_fetcher = FakeTemplateCatalogFetcher()
            if catalog_body is not None:
                catalog_fetcher.body_by_url[config.system_interface_template_catalog_url] = catalog_body
        state = build_test_state(
            config=config, shell_state_directory=state_dir, template_catalog_fetcher=catalog_fetcher
        )
        app = create_application(state)

        server = make_threaded_server("127.0.0.1", port, app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stub_server = serve_in_background(
            LOOPBACK_HOST,
            stub_port,
            build_instances_app(
                stub_source,
                ShellNudger(app_name=AppName(_STUB_APP_NAME), shell_url=base_url),
            ),
        )
        second_server = (
            serve_in_background(
                LOOPBACK_HOST,
                second_port,
                build_instances_app(
                    second_source,
                    ShellNudger(app_name=AppName(_SECOND_APP_NAME), shell_url=base_url),
                ),
            )
            if second_source is not None
            else contextlib.nullcontext()
        )
        with stub_server, second_server:
            try:
                wait_for(
                    lambda: _server_is_up(base_url),
                    timeout=10.0,
                    poll_interval=0.1,
                    error_message=f"workspace server did not come up at {base_url}",
                )
                for name in project_names:
                    _post_json(
                        f"{base_url}/api/projects",
                        {"name": name, "color": "#3B82F6", "glyph": 1},
                    )
                # Started only once the apps are serving: the first instance fetch must find them answering.
                state.shell.start()
                try:
                    yield E2EServer(
                        base_url=base_url,
                        state_dir=state_dir,
                        stub_source=stub_source,
                        stub_url=stub_url,
                        second_source=second_source,
                    )
                finally:
                    state.shell.stop()
            finally:
                server.shutdown()
                thread.join(timeout=5.0)


def _server_is_up(base_url: str) -> bool:
    try:
        urllib.request.urlopen(f"{base_url}/api/projects", timeout=0.5)
        return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


@pytest.fixture
def e2e_server(tmp_path: Path) -> Generator[E2EServer, None, None]:
    """Start the shell with the fixture instance and the starter project."""
    with _running_e2e_server(tmp_path, _PORT) as server:
        yield server


# ---------- helpers ----------


def _client_layout_files(state_dir: Path, view_id: str) -> list[Path]:
    """The per-client layout files a view holds (the seeds beside them are not counted)."""
    view_dir = state_dir / "layouts" / view_id
    if not view_dir.is_dir():
        return []
    return [path for path in view_dir.glob("*.json") if not path.name.startswith("seed.")]


def _broadcast_layout_op(
    base_url: str,
    op: str,
    args: dict[str, Any],
    view: str = STARTER_PROJECT_NAME,
    requester: str = "app:chat?instance=agent-e2e",
) -> None:
    """POST a layout op to the loopback ``/api/layout/broadcast`` endpoint.

    This is the same path ``system/scripts/layout.py`` drives, so issuing a ``split`` here
    exercises the real frontend handler. Mutating ops are view-targeted and only succeed
    once the page's ``client_state`` registration has landed, so a 412 is retried.

    ``requester`` defaults to a chat with no window on the desktop, which is what most of these
    tests want: an ``open`` with nothing to sit beside cascades. Pass an address that does have
    a window to exercise the tiling an open does for a real chat.
    """
    payload = json.dumps(
        {
            "op": op,
            "args": {**args, "view": view},
            "requester": requester,
        }
    ).encode()
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


def _stub_address(key: str) -> str:
    return f"app:{_STUB_APP_NAME}?instance={key}"


# A page for a stub instance's frame, served by a Playwright route rather than by the stub
# (which serves only its instances API). Its state is an ``<input>``: typing into it is a
# change no reload survives, because the served markup has it empty.
#
# It is painted one flat, unmistakable colour, so a test can ask whether the PAGE is at a pixel
# rather than whether something white is: the desktop, a window's own surface and a page are all
# near-white by design, and "the page has overrun the window's rounded corner" is exactly the kind
# of bug that hides between two whites.
_FRAMED_PAGE_COLOUR = (255, 0, 85)
_FRAMED_PAGE_HTML = (
    "<!doctype html><html><body style='margin:0;height:100vh;background:rgb(255,0,85)'>"
    "<input id='held' value='' /></body></html>"
)


# A framed page that takes the keyboard the moment it loads and tells the shell so, which is what
# any page with an input the user types into does (the chat's composer, a terminal). Nothing about
# it is a click, so the shell must not treat it as one.
_FOCUS_TAKING_PAGE_HTML = (
    "<!doctype html><html><body style='margin:0;height:100vh;background:rgb(255,0,85)'>"
    "<input id='held' autofocus value='' />"
    "<script>window.addEventListener('focus', () => parent.postMessage({type: 'shell:focused'}, '*'));"
    "window.addEventListener('load', () => document.getElementById('held').focus());</script>"
    "</body></html>"
)


def _serve_stub_pages(page: Page, server: E2EServer) -> None:
    page.route(
        f"{server.stub_url}/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=_FRAMED_PAGE_HTML),
    )


# Count every ``.si-live-surface`` that leaves the document from here on: removing an iframe
# destroys its document, so the mechanism is watched directly.
_WATCH_SURFACE_REMOVALS_JS = """
() => {
  window.__e2eRemovedSurfaces = [];
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.removedNodes) {
        if (node instanceof Element && node.classList.contains('si-live-surface')) {
          window.__e2eRemovedSurfaces.push(node.className);
        }
      }
    }
  });
  observer.observe(document.body, { childList: true, subtree: true });
}
"""

# The surfaces holding an address's frame, as a plain-object report. Identity is carried by
# ``__e2eStamp``, a property set on the ELEMENT rather than an attribute: nothing serializes
# it, so a surface that answers to it is necessarily the very element that was stamped.
_SURFACE_REPORT_JS = """
([address, stamp]) => {
  const surfaces = Array.from(document.querySelectorAll('.si-live-surface'))
    .filter((surface) => surface.querySelector(`iframe[data-address="${address}"]`) !== null);
  if (stamp) {
    for (const surface of surfaces) surface.__e2eStamp = stamp;
  }
  const shown = surfaces.filter((surface) => {
    const box = surface.getBoundingClientRect();
    return getComputedStyle(surface).display !== 'none' && box.width > 0 && box.height > 0;
  });
  return {
    count: surfaces.length,
    shownCount: shown.length,
    stamps: surfaces.map((surface) => surface.__e2eStamp ?? null),
    removals: (window.__e2eRemovedSurfaces ?? []).length,
  };
}
"""


def _surface_report(page: Page, address: str, stamp: str | None = None) -> dict[str, Any]:
    return page.evaluate(_SURFACE_REPORT_JS, [address, stamp])


def _wait_for_surface_shown(page: Page, address: str, stamp: str | None = None) -> dict[str, Any]:
    """The surface report once a surface holding ``address`` is on screen.

    The live layer places a page's surface on the animation frame after the dock has laid
    its pane out (a zero-sized pane keeps it hidden), so a report taken the instant the tab
    appears can find the element present but not yet shown; the wait is for that frame.
    """
    page.wait_for_function(
        f"([address, stamp]) => ({_SURFACE_REPORT_JS.strip()})([address, stamp]).shownCount >= 1",
        arg=[address, stamp],
        timeout=15000,
    )
    return _surface_report(page, address, stamp)




# ---------- the desktop ----------


def _wait_for_desktop(page: Page, view_id: str = STARTER_PROJECT_ID) -> None:
    """The desktop names the view it is showing; the active view itself lives on the client record."""
    page.wait_for_selector(f'#root[data-view-id="{view_id}"]', state="attached", timeout=15000)
    expect(page.locator("#icons .icon").first).to_be_visible(timeout=15000)


def _icon(page: Page, entry: str) -> Any:
    return page.locator(f'#icons .icon[data-entry="{entry}"]')


def _open_icon(page: Page, entry: str) -> None:
    """Open a desktop icon, which takes a double click: a single one only picks it out."""
    _icon(page, entry).dblclick()


def _dock_tile(page: Page, address: str) -> Any:
    return page.locator(f'#dock .dock-item[data-address="{address}"]')


def _window(page: Page, address: str) -> Any:
    return page.locator(f'.window[data-address="{address}"]')


def _window_rect(page: Page, address: str) -> dict[str, float]:
    return page.evaluate(
        "(address) => { const el = document.querySelector(`.window[data-address=\"${address}\"]`);"
        " const box = el.getBoundingClientRect();"
        " return {x: Math.round(box.x), y: Math.round(box.y), width: Math.round(box.width), height: Math.round(box.height)}; }",
        address,
    )


def _open_launcher(page: Page) -> None:
    """Open the ``+``: it widens into the field, and the menu comes up over it."""
    if page.locator(".dock-launch.is-open").count() == 0:
        page.locator("#dock-new").click()
    expect(page.locator("#launcher.is-open")).to_be_visible(timeout=10000)
    expect(page.locator("#dock-search-field")).to_be_visible(timeout=10000)


def _search(page: Page, query: str) -> None:
    """Type into the ``+``'s field, opening it first when it is closed."""
    _open_launcher(page)
    page.locator("#dock-search-field").fill(query)


def _close_search(page: Page) -> None:
    """Put the menu and its results away the way a user does: a press on the desktop, which the scrim takes."""
    page.locator("#launcher-scrim").click(position={"x": 5, "y": 5})
    expect(page.locator("#launcher.is-open")).to_have_count(0, timeout=10000)


def _result_row(page: Page, app: str, key: str) -> Any:
    """One row of the search results, over the dock."""
    return page.locator(f'#dock-results .result-row[data-address="{app}:{key}"]')


def _saved_desktop(state_dir: Path, view_id: str = STARTER_PROJECT_ID) -> dict[str, Any]:
    """The desktop the browser last saved for the view, out of the client's own layout file."""
    for path in _client_layout_files(state_dir, view_id):
        document = json.loads(path.read_text()).get("desktop")
        if document is not None:
            return document
    return {"windows": []}


def _saved_layout(state_dir: Path, view_id: str = STARTER_PROJECT_ID) -> dict[str, Any]:
    """The whole layout record the browser last saved, stamp and all, for comparing a file to itself."""
    for path in _client_layout_files(state_dir, view_id):
        record = json.loads(path.read_text())
        if record.get("desktop") is not None:
            return record
    return {}


def _wait_for_saved_window(state_dir: Path, address: str, view_id: str = STARTER_PROJECT_ID) -> dict[str, Any]:
    """Wait until the browser's autosave has written a window on ``address``, and answer it.

    An agent's op is applied to the saved file, so an op issued before the (debounced) save lands
    would edit an arrangement the browser has not finished describing.
    """
    found: dict[str, Any] = {}

    def _saved() -> bool:
        for window in _saved_desktop(state_dir, view_id).get("windows", []):
            if window.get("address") == address:
                found.update(window)
                return True
        return False

    wait_for(
        _saved,
        timeout=15.0,
        poll_interval=0.1,
        error_message=f"autosave never wrote a window on {address} into the desktop of {view_id}",
    )
    return found


def _make_window_tab_id(page: Page) -> str:
    """The page id of the Make something window, which is the desktop's own rather than an app's.

    Waited for: opening one is a redraw, and mithril redraws on the next frame rather than inside
    the click that asked for it.
    """
    page.wait_for_selector('.window:not([data-address^="app:"])', timeout=15000)
    tab_id = page.evaluate(
        "() => { const el = document.querySelector('.window:not([data-address^=\"app:\"])');"
        " return el === null ? null : el.getAttribute('data-tab-id'); }"
    )
    assert isinstance(tab_id, str), "the Make something window is not open"
    return tab_id


def _box_of(element: Any) -> dict[str, float]:
    """Where an element is on screen.

    Playwright answers ``None`` for an element that is not rendered; every caller here has already
    waited for the thing it is measuring, so an absent box is a broken test rather than a state
    worth handling.
    """
    box = element.bounding_box()
    assert box is not None, "the element has no box on screen"
    return box


def _rect_of(window: Any) -> dict[str, float]:
    """One window's rect, whether it is an app's window or the desktop's own."""
    box = _box_of(window)
    return {"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]}


def _shrink_window_to(page: Page, window: Any, width: int, height: int, x: int, y: int) -> None:
    """Put a window at a known rect, through the gestures rather than through the state.

    Taken in from the TOP and the RIGHT before it is moved, in that order, because a grip is only
    reachable where its window is: the desktop clips what hangs off it, and a default-sized window
    on a small screen hangs off the bottom, so the bottom grips cannot be the ones that fix it.
    """
    rect = _rect_of(window)
    # The n edge takes height off the top: height = start - delta, so shrinking pulls it DOWN.
    _drag(page, window.locator(".resize-n"), 0, int(rect["height"]) - height)
    page.wait_for_timeout(200)
    rect = _rect_of(window)
    _drag(page, window.locator(".resize-e"), width - int(rect["width"]), 0)
    page.wait_for_timeout(200)
    rect = _rect_of(window)
    _drag(page, window.locator(".titlebar"), x - int(rect["x"]), y - int(rect["y"]))
    page.wait_for_timeout(200)
    placed = _rect_of(window)
    desktop = page.evaluate(
        "() => { const box = document.querySelector('#desktop').getBoundingClientRect();"
        " return {width: box.width, height: box.height}; }"
    )
    assert placed["x"] >= 0 and placed["y"] >= 0, f"the window was not placed on the desktop: {placed}"
    assert placed["x"] + placed["width"] <= desktop["width"] + 1, f"the window hangs off the right: {placed}"
    assert placed["y"] + placed["height"] <= desktop["height"] + 1, (
        f"the window hangs off the bottom, so its bottom grips and corners are clipped away: {placed}"
    )


def _shrink_to(page: Page, address: str, width: int, height: int, x: int, y: int) -> None:
    _shrink_window_to(page, _window(page, address), width, height, x, y)


def _grip_icon(page: Page, entry: str, to_x: float, to_y: float) -> dict[str, Any]:
    """Press an icon and drag it to a point on the desktop, LEAVING the pointer down, and report
    what the desktop looks like mid-gesture. The caller releases."""
    start = _box_of(page.locator(f'#icons .icon[data-entry="{entry}"]'))
    page.mouse.move(start["x"] + start["width"] / 2, start["y"] + start["height"] / 2)
    page.mouse.down()
    page.mouse.move(to_x, to_y, steps=12)
    page.wait_for_timeout(200)
    return page.evaluate(
        """() => {
  const lifted = Array.from(document.querySelectorAll('#icons .icon.is-lifted'));
  const held = lifted[0] ?? null;
  const style = held === null ? null : getComputedStyle(held);
  const matrix = style === null ? null : new DOMMatrixReadOnly(style.transform);
  const windowZ = Array.from(document.querySelectorAll('.window')).map(
    (el) => Number.parseInt(getComputedStyle(el).zIndex, 10) || 0,
  );
  return {
    lifted_entries: lifted.map((el) => el.getAttribute('data-entry')),
    opacity: style === null ? null : Number.parseFloat(style.opacity),
    scale: matrix === null ? null : matrix.a,
    cursor: style === null ? null : style.cursor,
    icon_layer_z: Number.parseInt(getComputedStyle(document.getElementById('icons')).zIndex, 10) || 0,
    top_window_z: windowZ.length === 0 ? 0 : Math.max(...windowZ),
  };
}"""
    )


def _visible_text(page: Page) -> str:
    """Every word the desktop is actually showing, frames excluded.

    Read from the rendered text rather than from the markup, because an address legitimately
    appears in the DOM as an attribute -- it is how a window and its page are identified. What
    must never happen is the user READING one.
    """
    return page.evaluate("() => document.body.innerText")


def _make_the_fixture_instance_vanish(server: E2EServer) -> None:
    """Take the fixture instance out of its app's list, as a delete elsewhere or a forgetful app
    would, and tell the shell its list changed."""
    server.stub_source.records.clear()
    request = urllib.request.Request(f"{server.base_url}/api/apps/{_STUB_APP_NAME}/changed", method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 204


def _icon_position(page: Page, entry: str) -> dict[str, float]:
    """Where an icon sits on the desktop, and which cell of the grid it is in."""
    return page.evaluate(
        "(entry) => { const el = document.querySelector(`#icons .icon[data-entry=\"${entry}\"]`);"
        " return {x: Number(el.getAttribute('data-x')), y: Number(el.getAttribute('data-y')),"
        " column: Number(el.getAttribute('data-column')), row: Number(el.getAttribute('data-row'))}; }",
        entry,
    )


def _icon_cells(page: Page) -> list[str]:
    """Every icon's cell, as ``column,row`` strings: what proves no two share one."""
    return page.evaluate(
        "() => Array.from(document.querySelectorAll('#icons .icon'))"
        ".map((el) => `${el.getAttribute('data-column')},${el.getAttribute('data-row')}`)"
    )


def _wait_for_saved_icon(state_dir: Path, entry: str, view_id: str = STARTER_PROJECT_ID) -> dict[str, float]:
    """Wait for the browser's autosave to record the cell an icon was dropped in, and answer it."""
    found: dict[str, float] = {}

    def _saved() -> bool:
        cell = _saved_desktop(state_dir, view_id).get("icons", {}).get("cell_by_entry", {}).get(entry)
        if cell is None:
            return False
        found.update(cell)
        return True

    wait_for(
        _saved,
        timeout=15.0,
        poll_interval=0.1,
        error_message=f"the desktop never saved where {entry} was dropped",
    )
    return found


def _listed_instance_keys(server: E2EServer) -> list[str]:
    return [str(record.key) for record in server.stub_source.records]


def _css_length(page: Page, variable: str) -> float:
    """A length custom property as the page resolves it, in pixels.

    Read off ``#root``, which is where the dock's sizes are declared. Sizes asserted against this
    are checked against what the stylesheet actually says rather than against a number copied into
    the test, so changing a size in one place does not silently need changing in two.
    """
    raw = page.eval_on_selector(
        "#root", "(el, name) => getComputedStyle(el).getPropertyValue(name).trim()", variable
    )
    assert raw.endswith("px"), f"{variable} is not a pixel length: {raw!r}"
    return float(raw.removesuffix("px"))


def _dock_tile_metrics(page: Page, address: str) -> dict[str, float]:
    """A dock tile as it is actually drawn: how wide, how faint, and where its centre line is."""
    return page.evaluate(
        "(address) => { const el = document.querySelector(`#dock .dock-item[data-address=\"${address}\"]`);"
        " const box = el.getBoundingClientRect();"
        " return {width: box.width, centre_y: box.y + box.height / 2,"
        " opacity: Number.parseFloat(getComputedStyle(el).opacity)}; }",
        address,
    )


def _pixel_at(page: Page, x: float, y: float) -> tuple[int, int, int]:
    """The colour actually rendered at one point on screen.

    A one-pixel screenshot, decoded here rather than through an image library: a window's
    rounding is a painting question, and the tests that care about it have to ask what was
    painted. Asserting on the CSS instead is what let the square-cornered pages through --
    every declaration was present and correct, on an element that could not clip the page.
    """
    blob = page.screenshot(clip={"x": x, "y": y, "width": 1, "height": 1})
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", "the screenshot is not a PNG"
    offset = 8
    compressed = b""
    size: tuple[int, int] | None = None
    while offset + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[offset : offset + 4])
        kind = blob[offset + 4 : offset + 8]
        body = blob[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour_type = struct.unpack(">IIBB", body[:10])
            size = (width, height)
            assert depth == 8 and colour_type in (2, 6), f"unexpected PNG format {depth}/{colour_type}"
        if kind == b"IDAT":
            compressed += body
        offset += length + 12
    assert size == (1, 1), f"expected a one-pixel screenshot, got {size}"
    scanline = zlib.decompress(compressed)
    # One pixel wide and one tall: every filter predicts from a neighbour that does not exist,
    # which the format defines as zero, so the stored bytes are the colour whichever filter the
    # encoder picked.
    assert scanline[0] in (0, 1, 2, 3, 4), f"unknown PNG filter {scanline[0]}"
    return (scanline[1], scanline[2], scanline[3])


def _corner_points(rect: dict[str, float], inset: int = 3) -> dict[str, tuple[float, float]]:
    """A point just inside each corner of a rect, far enough in to clear a rounded corner's
    antialiasing and far enough out to be outside the rounded shape itself."""
    right = rect["x"] + rect["width"] - 1 - inset
    bottom = rect["y"] + rect["height"] - 1 - inset
    return {
        "top-left": (rect["x"] + inset, rect["y"] + inset),
        "top-right": (right, rect["y"] + inset),
        "bottom-left": (rect["x"] + inset, bottom),
        "bottom-right": (right, bottom),
    }


def _drag(page: Page, source: Any, delta_x: int, delta_y: int) -> None:
    box = _box_of(source)
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + delta_x, box["y"] + box["height"] / 2 + delta_y, steps=10)
    page.mouse.up()


@pytest.mark.timeout(30, func_only=False)
def test_page_loads_and_shows_title(e2e_server: E2EServer, page: Page) -> None:
    page.goto(e2e_server.base_url)
    expect(page).to_have_title("System Interface")


@pytest.mark.timeout(60, func_only=False)
def test_the_desktop_lands_with_an_icon_per_app_and_a_dock_of_what_is_running(
    e2e_server: E2EServer, page: Page
) -> None:
    """A fresh browser lands on the desktop: icons for what it can open, a dock for what is running."""
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)

    expect(_icon(page, "make-something")).to_be_visible()
    expect(_icon(page, _STUB_APP_NAME)).to_be_visible()
    # The dock is derived from the live instance list, so the fixture instance is in it with no
    # window open on it -- and dimmed, because nothing is showing it.
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_class(re.compile("is-offscreen"), timeout=15000)
    expect(page.locator(".window")).to_have_count(0)


@pytest.mark.timeout(90, func_only=False)
def test_an_app_icon_opens_a_window_on_its_instance_and_the_dock_lights_up(
    e2e_server: E2EServer, page: Page
) -> None:
    """The stub declares ``focus`` mode, so its icon goes to what is already running."""
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)

    _open_icon(page, _STUB_APP_NAME)

    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    expect(_window(page, _FIXTURE_ADDRESS).locator(".titlebar-title")).to_have_text(_FIXTURE_TITLE)
    _wait_for_surface_shown(page, _FIXTURE_ADDRESS)
    # Nothing was created: the app still lists the one instance it started with.
    assert [str(record.key) for record in e2e_server.stub_source.records] == [_FIXTURE_KEY]
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).not_to_have_class(re.compile("is-offscreen"))
    _wait_for_saved_window(e2e_server.state_dir, _FIXTURE_ADDRESS)


@pytest.mark.timeout(90, func_only=False)
def test_minimizing_puts_a_window_away_and_leaves_its_page_running(e2e_server: E2EServer, page: Page) -> None:
    """Minimized and never-opened are one state to the user, and the page behind it never reloads."""
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    page.evaluate(_WATCH_SURFACE_REMOVALS_JS)
    _wait_for_surface_shown(page, _FIXTURE_ADDRESS, stamp="held")
    # Something only this document holds: a reload would lose it.
    frame = page.frame_locator(f'iframe[data-address="{_FIXTURE_ADDRESS}"]')
    frame.locator("#held").fill("typed into the page")

    _window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="minimize"]').click()

    expect(_window(page, _FIXTURE_ADDRESS)).to_be_hidden(timeout=10000)
    # The dock still holds it, dimmed: it is running, and nothing is showing it.
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_class(re.compile("is-offscreen"))
    report = _surface_report(page, _FIXTURE_ADDRESS)
    assert report["count"] == 1 and report["shownCount"] == 0 and report["removals"] == 0

    _dock_tile(page, _FIXTURE_ADDRESS).click()

    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=10000)
    restored = _wait_for_surface_shown(page, _FIXTURE_ADDRESS)
    # The very same element, holding the very same document.
    assert restored["stamps"] == ["held"] and restored["removals"] == 0
    expect(frame.locator("#held")).to_have_value("typed into the page")


@pytest.mark.timeout(90, func_only=False)
def test_closing_a_window_stops_what_it_shows_and_never_deletes_it(tmp_path: Path, page: Page) -> None:
    """The X is the Stop verb: the instance keeps its record, its name and its transcript."""
    with _running_e2e_server(tmp_path, _PORT, is_stub_stoppable=True) as e2e_server:
        _closing_a_window_stops_it(e2e_server, page)


def _closing_a_window_stops_it(e2e_server: E2EServer, page: Page) -> None:
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)

    _window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="close"]').click()

    expect(_window(page, _FIXTURE_ADDRESS)).to_have_count(0, timeout=10000)
    wait_for(
        lambda: f"stop:{_FIXTURE_KEY}" in e2e_server.stub_source.calls,
        timeout=20.0,
        poll_interval=0.2,
        error_message="closing the window never stopped the instance",
    )
    # Stopped, not deleted: the app still holds the record, and nothing asked it to delete one.
    assert [str(record.key) for record in e2e_server.stub_source.records] == [_FIXTURE_KEY]
    assert not any(call.startswith("delete:") for call in e2e_server.stub_source.calls)
    # A stopped thing leaves the dock: it is found again through search.
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_count(0, timeout=20000)


@pytest.mark.timeout(90, func_only=False)
def test_the_desktop_comes_back_exactly_as_it_was_left(e2e_server: E2EServer, page: Page) -> None:
    """Windows, where they sit, the icon arrangement and the dock's own setting all survive a reload."""
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)

    _drag(page, _window(page, _FIXTURE_ADDRESS).locator(".titlebar"), 180, 140)
    page.locator("#dock-autohide").click()
    moved = _window_rect(page, _FIXTURE_ADDRESS)
    _wait_for_saved_window(e2e_server.state_dir, _FIXTURE_ADDRESS)
    wait_for(
        lambda: _saved_desktop(e2e_server.state_dir).get("dock", {}).get("is_hiding") is True,
        timeout=15.0,
        poll_interval=0.1,
        error_message="the dock's own setting was never saved",
    )

    page.reload()
    _wait_for_desktop(page)

    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    assert _window_rect(page, _FIXTURE_ADDRESS) == moved
    expect(page.locator("#dock.is-hidden")).to_have_count(1, timeout=10000)
    _wait_for_surface_shown(page, _FIXTURE_ADDRESS)


@pytest.mark.timeout(90, func_only=False)
def test_dragging_an_icon_rearranges_the_desktop_and_that_survives_a_reload(
    e2e_server: E2EServer, page: Page
) -> None:
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    desktop = page.evaluate(
        "() => { const box = document.querySelector('#desktop').getBoundingClientRect();"
        " return {width: Math.round(box.width), height: Math.round(box.height)}; }"
    )
    # Every icon flows from the top left until one is moved.
    assert page.locator("#icons .icon").first.get_attribute("data-entry") == MAKE_SOMETHING_ENTRY

    # Dragged by hand rather than through ``drag_to``, so the state DURING the gesture can be
    # looked at: the icon itself is what moves, lifted and at full opacity. It used to be the
    # browser's own drag-and-drop, which insists on a translucent ghost and a "copy" cursor.
    # Dropped far from where it started, in the bottom right: the whole desktop is available.
    dropped_x = desktop["width"] - 120
    dropped_y = desktop["height"] - 120
    held = _grip_icon(page, MAKE_SOMETHING_ENTRY, dropped_x, dropped_y)
    assert held["lifted_entries"] == [MAKE_SOMETHING_ENTRY], f"the icon was not picked up: {held}"
    assert held["opacity"] == 1.0, "the icon in the hand should be at full opacity, not a faded copy"
    assert held["scale"] > 1.0, f"the icon should lift as it is picked up, got scale {held['scale']}"
    assert held["cursor"] == "grabbing", f"the cursor should be a closed hand, got {held['cursor']}"
    # Above the windows for the length of the gesture, so an icon dragged across one stays in hand.
    assert held["icon_layer_z"] > held["top_window_z"], (
        f"the icon grid is not above the windows while arranging: {held}"
    )
    page.mouse.up()
    page.wait_for_timeout(400)

    landed = _icon_position(page, MAKE_SOMETHING_ENTRY)
    # In a cell out in the bottom right of the desktop -- the whole surface is available -- and in
    # a CELL: the icon sits at the cell's own corner, not at the pixel the pointer let go of.
    assert landed["column"] > 0 and landed["row"] > 0, landed
    assert landed["x"] > desktop["width"] / 2 and landed["y"] > desktop["height"] / 2, landed
    assert abs(landed["x"] - dropped_x) > 2 or abs(landed["y"] - dropped_y) > 2, (
        f"the icon stayed under the pointer instead of snapping to a cell: {landed}"
    )
    # No two icons in one cell.
    assert len(set(_icon_cells(page))) == len(_icon_cells(page)), _icon_cells(page)

    saved = _wait_for_saved_icon(e2e_server.state_dir, MAKE_SOMETHING_ENTRY)
    assert saved == {"column": landed["column"], "row": landed["row"]}, (saved, landed)

    page.reload()
    _wait_for_desktop(page)

    # Back in the cell it was put in, not back in the flow. Waited for rather than read once: the
    # desktop names its view before its arrangement has been fetched, and until that lands every
    # icon is in the default flow -- where this one started, so reading too early reads the
    # right answer to the wrong question.
    wait_for(
        lambda: (
            (_icon_position(page, MAKE_SOMETHING_ENTRY)["column"], _icon_position(page, MAKE_SOMETHING_ENTRY)["row"])
            == (landed["column"], landed["row"])
        ),
        timeout=15.0,
        poll_interval=0.1,
        error_message=f"the icon never came back to the cell it was put in ({landed})",
    )

    # Narrowed, every icon is still somewhere the user can reach.
    page.set_viewport_size({"width": 700, "height": 600})
    page.wait_for_timeout(600)
    assert page.evaluate(
        "() => Array.from(document.querySelectorAll('#icons .icon')).every((el) => {"
        " const box = el.getBoundingClientRect();"
        " return box.left >= 0 && box.top >= 0 && box.right <= window.innerWidth && box.bottom <= window.innerHeight; })"
    ), "an icon is off the narrowed desktop"
    assert len(set(_icon_cells(page))) == len(_icon_cells(page)), _icon_cells(page)


@pytest.mark.timeout(90, func_only=False)
def test_maximizing_fills_the_desktop_and_restoring_puts_the_window_back(
    e2e_server: E2EServer, page: Page
) -> None:
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    resting = _window_rect(page, _FIXTURE_ADDRESS)

    _window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="maximize"]').click()

    # The button becomes the restore once the window has actually filled the desktop.
    expect(_window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="restore"]')).to_be_visible(timeout=10000)
    filled = _window_rect(page, _FIXTURE_ADDRESS)
    desktop = page.evaluate(
        "() => { const box = document.querySelector('#desktop').getBoundingClientRect();"
        " return {width: Math.round(box.width), height: Math.round(box.height)}; }"
    )
    assert filled["width"] == desktop["width"] and filled["height"] == desktop["height"]

    _window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="restore"]').click()

    expect(_window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="maximize"]')).to_be_visible(timeout=10000)
    assert _window_rect(page, _FIXTURE_ADDRESS) == resting


@pytest.mark.timeout(120, func_only=False)
def test_a_restored_window_whose_instance_is_gone_is_named_and_not_an_address(
    e2e_server: E2EServer, page: Page
) -> None:
    """A saved desktop outlives the instances it holds windows for.

    Something stopped and then deleted, an app that forgot a session, a workspace restored from a
    backup: the window comes back and there is nothing to read its title from. It is named by what
    it was last called, never by its address, and it says plainly that what it held is gone.
    """
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    expect(_window(page, _FIXTURE_ADDRESS).locator(".titlebar-title")).to_have_text(_FIXTURE_TITLE)
    saved = _wait_for_saved_window(e2e_server.state_dir, _FIXTURE_ADDRESS)
    # The window is saved with what it is called, which is the whole of how it knows later.
    assert saved["last_known_title"] == _FIXTURE_TITLE

    _make_the_fixture_instance_vanish(e2e_server)
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_count(0, timeout=20000)
    page.reload(wait_until="domcontentloaded")
    _wait_for_desktop(page)

    # The window is still there: its app may list this again, and it reconnects when it does.
    restored = _window(page, _FIXTURE_ADDRESS)
    expect(restored).to_be_visible(timeout=15000)
    expect(restored.locator(".titlebar-title")).to_have_text(_FIXTURE_TITLE, timeout=10000)
    expect(restored).to_have_class(re.compile(r"\bis-unavailable\b"), timeout=10000)
    # It reads as an empty frame rather than as a window that has broken.
    expect(page.locator(".window-note")).to_contain_text("isn't here any more", timeout=10000)
    # And no dock tile, because the dock is what is RUNNING and this is not.
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_count(0)
    # Nowhere on the desktop is the user shown the address itself.
    assert _FIXTURE_ADDRESS not in _visible_text(page)

    # Its X has nothing left to stop, so what is left of the verb is clearing the window away.
    restored.locator('.win-btn[data-action="close"]').click()
    expect(_window(page, _FIXTURE_ADDRESS)).to_have_count(0, timeout=10000)
    wait_for(
        lambda: not any(
            window.get("address") == _FIXTURE_ADDRESS for window in _saved_desktop(e2e_server.state_dir)["windows"]
        ),
        timeout=15.0,
        poll_interval=0.1,
        error_message="closing the stale window never took it out of the saved desktop",
    )


@pytest.mark.timeout(120, func_only=False)
def test_a_minimized_window_whose_instance_is_gone_waits_out_of_sight(e2e_server: E2EServer, page: Page) -> None:
    """Minimized and unavailable at once shows nothing anywhere -- and comes back when its app does.

    It is the state the desktop is left in by putting something away and then losing it: there is
    no window on screen and no dock tile either, because nothing is running. The window is still
    in the arrangement, so the moment the app lists the instance again it is reachable.
    """
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    _window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="minimize"]').click()
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_hidden(timeout=10000)
    _wait_for_saved_window(e2e_server.state_dir, _FIXTURE_ADDRESS)

    _make_the_fixture_instance_vanish(e2e_server)
    page.reload(wait_until="domcontentloaded")
    _wait_for_desktop(page)

    # Nothing on screen and nothing in the dock: the window is out of sight and nothing is running.
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_hidden(timeout=15000)
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_count(0)
    assert _FIXTURE_ADDRESS not in _visible_text(page)
    # It was kept, though, rather than quietly dropped.
    assert any(
        window["address"] == _FIXTURE_ADDRESS for window in _saved_desktop(e2e_server.state_dir)["windows"]
    )

    # The app lists it again, and the way back is the dock tile, as it is for anything running.
    e2e_server.stub_source.records.append(instance_record(_FIXTURE_KEY, title=_FIXTURE_TITLE))
    request = urllib.request.Request(f"{e2e_server.base_url}/api/apps/{_STUB_APP_NAME}/changed", method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 204
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_count(1, timeout=20000)
    _dock_tile(page, _FIXTURE_ADDRESS).click()
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=10000)
    expect(_window(page, _FIXTURE_ADDRESS)).not_to_have_class(re.compile(r"\bis-unavailable\b"))


@pytest.mark.timeout(120, func_only=False)
def test_opening_the_desktop_and_touching_nothing_leaves_the_saved_arrangement_alone(
    e2e_server: E2EServer, page: Page
) -> None:
    """Restoring an arrangement is a read.

    The desktop is fitted to the screen it is on now -- a window parked off the right of a smaller
    one is drawn back in reach, and a page that takes the keyboard as it loads is not treated as a
    click. What must never happen is either of those being written back: the rect a user set on a
    big screen has to survive being looked at on a small one, and the order they stacked their
    windows in has to survive the pages loading. A restore that saves a reconstruction turns every
    inaccuracy in the restore into permanent loss, so the file is compared byte for byte.
    """
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    window = _window(page, _FIXTURE_ADDRESS)
    expect(window).to_be_visible(timeout=15000)
    # Parked out to the right, so the narrower screen below cannot hold it where it is.
    _shrink_window_to(page, window, width=420, height=300, x=740, y=120)
    # Waited out to the LAST save of the drag: each grip saves, and an earlier one holds an
    # intermediate rect that the narrow screen below would happen to fit.
    wait_for(
        lambda: _wait_for_saved_window(e2e_server.state_dir, _FIXTURE_ADDRESS)["rect"]["x"] > 700,
        timeout=15.0,
        poll_interval=0.1,
        error_message="the window was never saved out at the right of the desktop",
    )
    page.wait_for_timeout(2000)
    before = _saved_layout(e2e_server.state_dir)

    # Coming back, the page takes the keyboard as it loads, the way a composer or a terminal does.
    page.route(
        f"{e2e_server.stub_url}/**",
        lambda route: route.fulfill(status=200, content_type="text/html", body=_FOCUS_TAKING_PAGE_HTML),
    )
    page.set_viewport_size({"width": 700, "height": 520})
    page.reload(wait_until="domcontentloaded")
    _wait_for_desktop(page)
    restored = _window(page, _FIXTURE_ADDRESS)
    expect(restored).to_be_visible(timeout=15000)
    _wait_for_surface_shown(page, _FIXTURE_ADDRESS)
    # Drawn where it can be reached on the screen it is on now, whatever the file says.
    parked = before["desktop"]["windows"][0]["rect"]
    desktop = page.evaluate(
        "() => { const box = document.querySelector('#desktop').getBoundingClientRect();"
        " return {width: box.width, height: box.height}; }"
    )
    shown = _rect_of(restored)
    assert shown["x"] < parked["x"], f"the window was drawn at its saved {parked} on a {desktop} desktop"
    assert shown["x"] + 80 <= desktop["width"], (
        f"too little of {shown} is on a {desktop} desktop to grab (saved at {parked})"
    )
    # Well past every save delay, and past the page's own load.
    page.wait_for_timeout(3000)

    after = _saved_layout(e2e_server.state_dir)
    assert after.get("desktop", {}).get("windows") == before.get("desktop", {}).get("windows"), (
        "restoring the desktop rewrote the windows it restored"
    )
    assert after == before, "restoring the desktop saved a reconstruction of it"

    # And the next thing the user does, whatever it is, saves the desktop -- so the fitting must
    # not have been taken into the arrangement either. This is where a restore that quietly
    # reconstructs does its damage: one unrelated click and the window is at its fitted rect for
    # good, back on the big screen as well.
    page.locator("#dock-autohide").click()
    wait_for(
        lambda: _saved_desktop(e2e_server.state_dir).get("dock", {}).get("is_hiding") is True,
        timeout=15.0,
        poll_interval=0.1,
        error_message="the dock's own setting was never saved",
    )
    kept = _wait_for_saved_window(e2e_server.state_dir, _FIXTURE_ADDRESS)
    assert kept["rect"] == parked, (
        f"a save after the restore wrote the window back at the rect the narrow screen fitted it to: {kept['rect']}"
    )


@pytest.mark.timeout(90, func_only=False)
def test_a_window_is_rounded_at_every_corner_and_its_page_does_not_overrun_them(
    e2e_server: E2EServer, page: Page
) -> None:
    """The page a window frames has to be clipped to the window's own rounded outline.

    The page is not inside the window -- it is a layer of its own, drawn just under it -- so the
    window's ``border-radius`` and ``overflow: hidden`` cannot reach it, and a page that does not
    round its own bottom corners paints square ones over them. Asked of the pixels: the framed
    page is a flat, unmistakable colour, so "the page is here" is a question about what was
    painted rather than about which near-white won.
    """
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    _wait_for_surface_shown(page, _FIXTURE_ADDRESS)
    # Sized and parked so the desktop surrounds it on every side: a corner hanging off the desktop
    # would be clipped by the desktop instead, and would pass this test while looking wrong.
    _shrink_to(page, _FIXTURE_ADDRESS, width=520, height=360, x=200, y=140)
    rect = _window_rect(page, _FIXTURE_ADDRESS)
    page.wait_for_timeout(300)

    # The page is really there, and really that colour: without this the corner assertions below
    # would pass just as happily against a window with no page in it at all.
    middle = _pixel_at(page, rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
    assert middle == _FRAMED_PAGE_COLOUR, f"the framed page is not showing; the window's middle is {middle}"

    for name, (x, y) in _corner_points(rect).items():
        assert _pixel_at(page, x, y) != _FRAMED_PAGE_COLOUR, f"the page overruns the window's {name} corner"
    # ...and the page does reach INTO the corners, rather than being inset from them: 14px along
    # each diagonal is inside a 16px radius, so this is the page's own rounding being asserted
    # and not merely a gap between the page and the window's edge.
    for name, (x, y) in _corner_points(rect, inset=14).items():
        # The top corners are the title bar's own; the page starts below it.
        if name.startswith("top"):
            continue
        assert _pixel_at(page, x, y) == _FRAMED_PAGE_COLOUR, f"the page does not reach the {name} corner"

    # Maximized, the window gives up its rounding deliberately, and the page has to follow it --
    # a page still rounding its corners would leave the desktop showing through at the very
    # bottom of the screen.
    _window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="maximize"]').click()
    expect(_window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="restore"]')).to_be_visible(timeout=10000)
    page.wait_for_timeout(300)
    filled = _window_rect(page, _FIXTURE_ADDRESS)
    for name, (x, y) in _corner_points(filled).items():
        # The top corners are the window's own chrome either way.
        if name.startswith("top"):
            continue
        assert _pixel_at(page, x, y) == _FRAMED_PAGE_COLOUR, f"a maximized window is cut away at its {name} corner"


@pytest.mark.timeout(90, func_only=False)
def test_the_make_something_window_is_rounded_like_any_other(e2e_server: E2EServer, page: Page) -> None:
    """A window whose body is the desktop's own content, not a framed page, rounds the same way.

    Its content IS inside the window, so the window's own clipping does the work here -- which is
    exactly why it needs its own check: the fix for the framed pages cannot cover it.
    """
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, MAKE_SOMETHING_ENTRY)
    tab_id = _make_window_tab_id(page)
    make_window = page.locator(f'.window[data-tab-id="{tab_id}"]')
    expect(make_window).to_be_visible(timeout=15000)
    _shrink_window_to(page, make_window, width=520, height=360, x=200, y=140)
    rect = _rect_of(make_window)
    page.wait_for_timeout(300)

    # Whatever the body's own colour is (it is the window's surface, not the desktop's), a corner
    # the window failed to clip would be showing it.
    body = _pixel_at(page, rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] - 12)
    for name in ("bottom-left", "bottom-right"):
        x, y = _corner_points(rect)[name]
        assert _pixel_at(page, x, y) != body, f"the window's body overruns its {name} corner"
        inner_x, inner_y = _corner_points(rect, inset=14)[name]
        assert _pixel_at(page, inner_x, inner_y) == body, f"the body does not reach the {name} corner"


@pytest.mark.timeout(120, func_only=False)
def test_every_edge_and_corner_of_a_window_resizes_it(e2e_server: E2EServer, page: Page) -> None:
    """All eight grips, dragged for real.

    The page is drawn over the window's body, so a page that sat above its own window would
    swallow the pointer at every edge it reaches -- which is most of them. Asserting that the
    handles exist, or that they have a size, passes happily while none of them can be hit.
    """
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    _wait_for_surface_shown(page, _FIXTURE_ADDRESS)
    # Small, and well inside the desktop, so every grip has somewhere to be dragged to and the
    # desktop's own clipping is never what the drag runs into.
    _shrink_to(page, _FIXTURE_ADDRESS, width=520, height=360, x=260, y=150)

    for edge, delta_x, delta_y, wider, taller in [
        ("e", 40, 0, True, False),
        ("w", -40, 0, True, False),
        ("s", 0, 40, False, True),
        ("n", 0, -40, False, True),
        ("se", 30, 30, True, True),
        ("sw", -30, 30, True, True),
        ("ne", 30, -30, True, True),
        ("nw", -30, -30, True, True),
    ]:
        before = _window_rect(page, _FIXTURE_ADDRESS)
        handle = _window(page, _FIXTURE_ADDRESS).locator(f".resize-{edge}")
        # The grip is under the pointer where it overlaps the window, which is where it is dragged
        # from: a grip nothing can hit fails here rather than silently doing nothing.
        _drag(page, handle, delta_x, delta_y)
        page.wait_for_timeout(250)
        after = _window_rect(page, _FIXTURE_ADDRESS)
        if wider:
            assert after["width"] > before["width"], f"dragging the {edge} grip did not widen the window: {after}"
        else:
            assert after["width"] == before["width"], f"dragging the {edge} grip changed the width: {after}"
        if taller:
            assert after["height"] > before["height"], f"dragging the {edge} grip did not heighten it: {after}"
        else:
            assert after["height"] == before["height"], f"dragging the {edge} grip changed the height: {after}"

    # And the shape the drags left is what gets saved.
    saved = _wait_for_saved_window(e2e_server.state_dir, _FIXTURE_ADDRESS)
    final = _window_rect(page, _FIXTURE_ADDRESS)
    wait_for(
        lambda: _wait_for_saved_window(e2e_server.state_dir, _FIXTURE_ADDRESS)["rect"]["width"] == final["width"],
        timeout=10.0,
        poll_interval=0.1,
        error_message=f"the resized width was never saved (last saw {saved['rect']})",
    )


@pytest.mark.timeout(120, func_only=False)
def test_a_click_on_a_window_that_is_not_in_front_only_brings_it_forward(tmp_path: Path, page: Page) -> None:
    """A background window's own buttons are not live until it is in front.

    Its close button stops what it shows, so a click that lands there when the user was only
    reaching for the window is the accident worth preventing. The same shield covers its page,
    which is what makes the rule learnable rather than a per-control quirk.
    """
    with _running_e2e_server(tmp_path, _PORT, is_stub_stoppable=True) as e2e_server:
        _a_background_window_only_comes_forward(e2e_server, page)


def _a_background_window_only_comes_forward(e2e_server: E2EServer, page: Page) -> None:
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    _wait_for_surface_shown(page, _FIXTURE_ADDRESS)
    _shrink_to(page, _FIXTURE_ADDRESS, width=420, height=300, x=120, y=120)

    # A second window, which takes the front and leaves the first one behind it, not under it.
    _open_icon(page, MAKE_SOMETHING_ENTRY)
    front_tab = _make_window_tab_id(page)
    expect(page.locator(f'.window[data-tab-id="{front_tab}"]')).to_be_visible(timeout=15000)
    _drag(page, page.locator(f'.window[data-tab-id="{front_tab}"]').locator(".titlebar"), 360, 240)
    background = _window(page, _FIXTURE_ADDRESS)
    expect(background).not_to_have_class(re.compile(r"\bis-front\b"))
    expect(background.locator(".window-shield")).to_have_count(1)

    # Its close button: the press lands on the shield, so nothing is stopped and the window comes
    # forward instead.
    background.locator('.win-btn[data-action="close"]').click(force=True)
    page.wait_for_timeout(600)
    expect(background).to_have_class(re.compile(r"\bis-front\b"), timeout=10000)
    expect(background.locator(".window-shield")).to_have_count(0)
    # Nothing was stopped. Asserted on what the app was ASKED, not on what it still holds: a stop
    # leaves the record in place, so a surviving record would prove nothing.
    assert not any(call.startswith("stop:") for call in e2e_server.stub_source.calls), (
        "the background window's close button stopped what it was showing"
    )
    assert _listed_instance_keys(e2e_server) == [_FIXTURE_KEY]
    _wait_for_surface_shown(page, _FIXTURE_ADDRESS)

    # And now that it is in front, the same button does its job.
    background.locator('.win-btn[data-action="close"]').click()
    wait_for(
        lambda: f"stop:{_FIXTURE_KEY}" in e2e_server.stub_source.calls,
        timeout=20.0,
        poll_interval=0.2,
        error_message="the close button did not stop the instance once its window was in front",
    )


@pytest.mark.timeout(90, func_only=False)
def test_the_dock_shows_at_a_glance_what_is_on_screen_and_what_is_put_away(
    e2e_server: E2EServer, page: Page
) -> None:
    """The dock's two states differ in size, and in nothing else.

    A tile of a running thing is drawn at ``--dock-tile`` when its window is on screen and at the
    smaller ``--dock-tile-offscreen`` when it is put away. Those sizes are declared in the
    stylesheet and mirrored by ``DOCK_TILE_PX`` in dock.ts, so the rendered tile is measured
    against the stylesheet's own value here rather than either being trusted.

    Neither state is faded: the row used to distinguish the two by opacity and the user asked for
    that to go, so a tile that is the wrong *shade* is as much a regression as one that is the
    wrong size, and both are asserted.

    The ``+`` is deliberately *not* tile-sized any more -- it is the one control in the row that
    is not a running thing -- so it is measured against its own ``--dock-launch-size`` and
    asserted to sit between the two tile states rather than matching either.
    """
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    # Off the dock: a hovered tile is lifted and scaled, which is not the size being measured.
    page.mouse.move(700, 400)
    page.wait_for_timeout(300)

    declared_tile = _css_length(page, "--dock-tile")
    declared_offscreen = _css_length(page, "--dock-tile-offscreen")
    declared_launch = _css_length(page, "--dock-launch-size")
    assert declared_offscreen < declared_tile, (
        "a put-away tile must be declared smaller than an on-screen one: "
        f"{declared_offscreen} vs {declared_tile}"
    )

    plus_width = page.eval_on_selector(".dock-launch", "el => el.getBoundingClientRect().width")
    assert abs(plus_width - declared_launch) < 0.5, (
        f"the + should be drawn at --dock-launch-size: {plus_width} vs {declared_launch}"
    )
    on_screen = _dock_tile_metrics(page, _FIXTURE_ADDRESS)
    assert abs(on_screen["width"] - declared_tile) < 0.5, (
        f"an on-screen tile should be drawn at --dock-tile: {on_screen['width']} vs {declared_tile}"
    )
    assert on_screen["opacity"] == 1.0

    _window(page, _FIXTURE_ADDRESS).locator('.win-btn[data-action="minimize"]').click()
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_class(re.compile(r"\bis-offscreen\b"), timeout=10000)
    wait_for(
        lambda: _dock_tile_metrics(page, _FIXTURE_ADDRESS)["width"] < on_screen["width"],
        timeout=5.0,
        poll_interval=0.1,
        error_message="a minimized tile never became smaller than an on-screen one",
    )
    put_away = _dock_tile_metrics(page, _FIXTURE_ADDRESS)
    assert abs(put_away["width"] - declared_offscreen) < 0.5, (
        f"a put-away tile should be drawn at --dock-tile-offscreen: "
        f"{put_away['width']} vs {declared_offscreen}"
    )
    # The + sits between the two tile states: under an on-screen tile, over a put-away one.
    assert put_away["width"] < plus_width < on_screen["width"], (
        f"the + should sit between the two tile sizes: {put_away['width']}, {plus_width}, "
        f"{on_screen['width']}"
    )
    # The fade is gone: putting a window away changes its size, never its shade.
    assert put_away["opacity"] == 1.0, (
        f"a minimized tile should not be faded: opacity {put_away['opacity']}"
    )
    # One centre line: the row must not look like it is wobbling.
    assert abs(put_away["centre_y"] - on_screen["centre_y"]) < 0.5, (
        f"the tiles are not on one axis: {put_away['centre_y']} vs {on_screen['centre_y']}"
    )


@pytest.mark.timeout(90, func_only=False)
def test_renaming_from_the_title_bar_renames_the_instance(e2e_server: E2EServer, page: Page) -> None:
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)

    _window(page, _FIXTURE_ADDRESS).locator(".titlebar-title").dblclick()
    editor = _window(page, _FIXTURE_ADDRESS).locator(".title-rename")
    expect(editor).to_be_visible(timeout=5000)
    editor.fill("Design notes")
    editor.press("Enter")

    wait_for(
        lambda: [str(record.title) for record in e2e_server.stub_source.records] == ["Design notes"],
        timeout=20.0,
        poll_interval=0.2,
        error_message="the rename never reached the app",
    )
    expect(_window(page, _FIXTURE_ADDRESS).locator(".titlebar-title")).to_have_text("Design notes", timeout=15000)


@pytest.mark.timeout(120, func_only=False)
def test_an_app_that_browses_its_own_instances_is_in_the_dock_once_per_window(
    tmp_path: Path, page: Page
) -> None:
    """The dock stops listing an app's instances one by one once the app lists them itself.

    The complaint this answers: a dozen chats running at once made the dock almost entirely chats
    and crowded out everything else. An app that declares ``browses_instances`` keeps its own list
    inside its window, so the dock carries its WINDOWS -- and what is running without one is
    reached from that list, and from search, rather than from here.
    """
    with _running_e2e_server(
        tmp_path, _PORT, stub_instances=("stub-1", "stub-2", "stub-3"), is_stub_browsing=True
    ) as server:
        _serve_stub_pages(page, server)
        page.goto(server.base_url)
        _wait_for_desktop(page)

        # Three running, and the dock holds none of them: nothing has a window yet.
        expect(page.locator("#dock .dock-item")).to_have_count(0, timeout=15000)

        # The icon opens a window onto the most recently active of them -- the end of the app's
        # own list -- rather than starting a fourth.
        newest = _stub_address("stub-3")
        _open_icon(page, _STUB_APP_NAME)
        expect(_window(page, newest)).to_be_visible(timeout=15000)
        assert _listed_instance_keys(server) == ["stub-1", "stub-2", "stub-3"], "the icon started something new"

        # One window, one tile, carrying that window's own instance.
        expect(page.locator("#dock .dock-item")).to_have_count(1, timeout=15000)
        expect(_dock_tile(page, newest)).to_have_count(1)
        assert _dock_tile_metrics(page, newest)["width"] > 0

        # The other two are running and have no tile -- and are still findable, which is the
        # whole basis for leaving them out.
        expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_count(0)
        _search(page, _FIXTURE_TITLE)
        expect(_result_row(page, _STUB_APP_NAME, _FIXTURE_KEY)).to_be_visible(timeout=15000)
        _close_search(page)

        # Another click gives another window, on the next one with none: that is how a second
        # window of a browsing app is opened. The first window is moved off the icons first,
        # since a window over them is a window the user would move too.
        _shrink_to(page, newest, width=420, height=300, x=500, y=300)
        _open_icon(page, _STUB_APP_NAME)
        expect(page.locator(".window")).to_have_count(2, timeout=15000)
        expect(page.locator("#dock .dock-item")).to_have_count(2, timeout=15000)


@pytest.mark.timeout(120, func_only=False)
def test_a_browsing_window_follows_the_instance_its_app_points_it_at(tmp_path: Path, page: Page) -> None:
    """Picking another of its instances moves the WINDOW, and the window's name follows.

    This is what a row of the chat app's own list does: the app tells the shell that the window it
    was framed in now shows another of its instances (the tab route of contracts.md section 5), and
    the window re-addresses itself, re-points its frame, and takes that instance's name -- which is
    what makes renaming from the title bar rename the one on screen.
    """
    with _running_e2e_server(
        tmp_path, _PORT, stub_instances=("stub-1", "stub-2"), is_stub_browsing=True
    ) as server:
        _serve_stub_pages(page, server)
        page.goto(server.base_url)
        _wait_for_desktop(page)
        opened = _stub_address("stub-2")
        _open_icon(page, _STUB_APP_NAME)
        expect(_window(page, opened)).to_be_visible(timeout=15000)
        expect(_window(page, opened).locator(".titlebar-title")).to_have_text("Stub 2")
        saved = _wait_for_saved_window(server.state_dir, opened)

        # What the app asks for when a row of its own list is picked.
        _post_no_content(
            f"{server.base_url}/api/tabs/{saved['tab_id']}/instance",
            {"app": _STUB_APP_NAME, "key": _FIXTURE_KEY},
        )

        moved = _window(page, _FIXTURE_ADDRESS)
        expect(moved).to_be_visible(timeout=15000)
        # The same window, re-pointed: not a second one, and not a new page id.
        expect(page.locator(".window")).to_have_count(1)
        assert moved.get_attribute("data-tab-id") == saved["tab_id"]
        # Its name is the instance it now shows, which is what the user picked.
        expect(moved.locator(".titlebar-title")).to_have_text(_FIXTURE_TITLE, timeout=10000)
        # And the dock followed it: one tile, for what this window is showing now.
        expect(page.locator("#dock .dock-item")).to_have_count(1, timeout=15000)
        expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_count(1)
        _wait_for_surface_shown(page, _FIXTURE_ADDRESS)


@pytest.mark.timeout(90, func_only=False)
def test_search_finds_something_that_was_stopped_and_opening_it_starts_it(tmp_path: Path, page: Page) -> None:
    """The dock shows only what is running, so search is the way back to a stopped thing."""
    with _running_e2e_server(tmp_path, _PORT, stopped_instances=(_FIXTURE_KEY,)) as e2e_server:
        _search_finds_a_stopped_thing(e2e_server, page)


def _search_finds_a_stopped_thing(e2e_server: E2EServer, page: Page) -> None:
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_count(0, timeout=15000)

    _search(page, "Stub")

    row = _result_row(page, _STUB_APP_NAME, _FIXTURE_KEY)
    expect(row).to_be_visible(timeout=15000)
    expect(row.locator(".result-state")).to_have_text("stopped")

    row.click()

    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    wait_for(
        lambda: f"start:{_FIXTURE_KEY}" in e2e_server.stub_source.calls,
        timeout=20.0,
        poll_interval=0.2,
        error_message="opening a stopped result never started it",
    )


@pytest.mark.timeout(120, func_only=False)
def test_the_plus_opens_into_a_field_with_the_menu_over_it(e2e_server: E2EServer, page: Page) -> None:
    """One control for both ways in: the ``+`` widens into the search field, with the menu above.

    Closed, the ``+`` is a round button the size of a dock tile. Open, the same box is the field,
    the ``+`` still its glyph, and over it the menu of what can be opened: a row per app. Typing
    turns the menu into the search, with the matching text in bold. A press on the desktop puts
    it all away; so does Escape, once to clear and once to close.
    """
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)

    # Closed: a button, one dock tile wide, and no field to be seen.
    closed = _box_of(page.locator(".dock-launch"))
    assert abs(closed["width"] - closed["height"]) < 1, f"the closed + is not round: {closed}"
    expect(page.locator("#dock-search-field")).not_to_be_in_viewport()

    # Open: the same control, wider, with the field in it and the menu over it -- and the menu
    # holds rows, not a field of its own.
    _open_launcher(page)
    wait_for(
        lambda: _box_of(page.locator(".dock-launch"))["width"] >= 200,
        timeout=5.0,
        poll_interval=0.1,
        error_message="the + never widened into its field",
    )
    opened = _box_of(page.locator(".dock-launch"))
    assert abs(opened["x"] - closed["x"]) < 1, "the + moved when it opened"
    expect(page.locator("#launcher input")).to_have_count(0)
    row = page.locator(f'#launcher .launcher-item[data-entry="{_STUB_APP_NAME}"]')
    expect(row).to_be_visible()
    menu_box = _box_of(page.locator("#launcher"))
    dock_box = _box_of(page.locator("#dock"))
    assert menu_box["y"] + menu_box["height"] <= dock_box["y"] + 1, f"the menu {menu_box} is not above the dock {dock_box}"
    assert abs(menu_box["x"] - opened["x"]) < 1, "the menu is not over the field"

    # Typing turns the menu into the search: the app row the query names stays, and what was found
    # comes under it with the match in bold; the field stays typeable with the results up.
    page.locator("#dock-search-field").fill(_FIXTURE_TITLE)
    result = _result_row(page, _STUB_APP_NAME, _FIXTURE_KEY)
    expect(result).to_be_visible(timeout=15000)
    expect(result.locator(".result-title mark")).to_have_text(_FIXTURE_TITLE)
    expect(page.locator("#dock-search-field")).to_be_editable()
    page.locator("#dock-search-field").fill(f"{_FIXTURE_TITLE} ")

    # A press on the desktop puts it away: the control closes and the field empties.
    _close_search(page)
    expect(page.locator(".dock-launch.is-open")).to_have_count(0)
    assert page.locator("#dock-search-field").input_value() == ""

    # Escape from the field: the first clears what was typed, the second closes.
    _search(page, _FIXTURE_TITLE)
    expect(_result_row(page, _STUB_APP_NAME, _FIXTURE_KEY)).to_be_visible(timeout=15000)
    page.locator("#dock-search-field").press("Escape")
    expect(page.locator("#dock-results")).to_have_count(0, timeout=10000)
    expect(page.locator("#launcher.is-open")).to_have_count(1)
    page.locator("#dock-search-field").press("Escape")
    expect(page.locator("#launcher.is-open")).to_have_count(0, timeout=10000)


@pytest.mark.timeout(120, func_only=False)
def test_the_menu_is_apps_not_instances_and_a_new_chat_reuses_the_chat_window(tmp_path: Path, page: Page) -> None:
    """The menu lists apps, never the things running in them, and its rows do what they say.

    The stub browses its own instances here, as the chat does, so the menu offers it twice -- the
    app as it is, and a new instance of it -- and starting a new one from the menu re-points the
    window already open on the app rather than opening a second.
    """
    with _running_e2e_server(
        tmp_path, _PORT, stub_instances=("stub-1", "stub-2"), is_stub_browsing=True
    ) as server:
        _serve_stub_pages(page, server)
        page.goto(server.base_url)
        _wait_for_desktop(page)

        _open_launcher(page)
        rows = page.locator("#launcher .launcher-item")
        # The two running things are not rows: the search and the dock are for those.
        expect(rows.filter(has_text="Stub 1")).to_have_count(0)
        expect(rows.filter(has_text="Stub 2")).to_have_count(0)
        expect(page.locator('#launcher .launcher-item[data-kind="browse"]')).to_have_count(1)
        expect(page.locator('#launcher .launcher-item[data-kind="new"]')).to_have_count(1)
        # And every row is on screen, the last of them nearest the field: nothing to scroll for.
        menu_box = _box_of(page.locator("#launcher"))
        for index in range(rows.count()):
            row_box = _box_of(rows.nth(index))
            assert menu_box["y"] <= row_box["y"] and row_box["y"] + row_box["height"] <= menu_box["y"] + menu_box["height"] + 1, (
                f"row {index} {row_box} is not within the menu {menu_box}"
            )

        # The app-as-it-is row opens a window on its latest instance.
        page.locator('#launcher .launcher-item[data-kind="browse"]').click()
        expect(_window(page, _stub_address("stub-2"))).to_be_visible(timeout=15000)
        expect(page.locator("#launcher.is-open")).to_have_count(0)

        # "New" starts a third, and the window that was on Stub 2 now shows it: one window.
        _open_launcher(page)
        page.locator('#launcher .launcher-item[data-kind="new"]').click()
        wait_for(
            lambda: len(_listed_instance_keys(server)) == 3,
            timeout=15.0,
            poll_interval=0.2,
            error_message="the New row did not start a new instance",
        )
        newest = _stub_address(_listed_instance_keys(server)[-1])
        expect(_window(page, newest)).to_be_visible(timeout=15000)
        expect(page.locator(".window")).to_have_count(1)


@pytest.mark.timeout(120, func_only=False)
def test_a_crowded_dock_scrolls_its_row_rather_than_shrinking_the_tiles(tmp_path: Path, page: Page) -> None:
    """Every tile keeps its declared size however many things are running.

    The row of tiles is what gives way: it scrolls sideways inside the dock, and everything fixed
    around it -- the ``+``, the search field beside it, the trailing control -- keeps its place
    and its width. The user asked for tiles that hold their size, not for a row that never scrolls.

    Nothing here has a window on screen, so every tile is in the put-away state and is measured
    against ``--dock-tile-offscreen``. The point is that a crowded row does not squeeze them: the
    first and the last are the same size as each other and as the stylesheet says.
    """
    crowd = tuple(f"stub-{index}" for index in range(40))
    with _running_e2e_server(tmp_path, _PORT, stub_instances=crowd) as server:
        page.goto(server.base_url)
        _wait_for_desktop(page)
        expect(page.locator("#dock .dock-item")).to_have_count(len(crowd), timeout=20000)

        declared_offscreen = _css_length(page, "--dock-tile-offscreen")
        for key in ("stub-0", "stub-39"):
            tile = _dock_tile(page, _stub_address(key))
            expect(tile).to_have_class(re.compile(r"\bis-offscreen\b"))
            width = _dock_tile_metrics(page, _stub_address(key))["width"]
            assert abs(width - declared_offscreen) < 0.5, (
                f"{key} was squeezed by the crowd: {width} vs {declared_offscreen}"
            )

        dock_box = _box_of(page.locator("#dock"))
        for selector in (".dock-launch", "#dock .dock-items", ".dock-hint"):
            box = _box_of(page.locator(selector))
            assert box["x"] >= dock_box["x"] - 1, f"{selector} is off the dock's left edge: {box}"
            assert box["x"] + box["width"] <= dock_box["x"] + dock_box["width"] + 1, (
                f"{selector} runs off the dock's right edge: {box} of {dock_box}"
            )
        # The row holds more than it shows, and scrolls to the rest.
        row = page.locator("#dock .dock-items")
        assert page.evaluate("el => el.scrollWidth > el.clientWidth", row.element_handle())
        last = _box_of(_dock_tile(page, _stub_address("stub-39")))
        assert last["x"] > dock_box["x"] + dock_box["width"], "the last tile should start off screen"
        _dock_tile(page, _stub_address("stub-39")).scroll_into_view_if_needed()
        last = _box_of(_dock_tile(page, _stub_address("stub-39")))
        assert last["x"] + last["width"] <= dock_box["x"] + dock_box["width"] + 1, "the row did not scroll to it"

        # The + still opens to its full width in a crowded dock, and finds one of the crowd.
        _search(page, "Stub 23")
        wait_for(
            lambda: _box_of(page.locator(".dock-launch"))["width"] >= 200,
            timeout=5.0,
            poll_interval=0.1,
            error_message="the + gave up its width to the row",
        )
        assert _box_of(page.locator(".dock-launch"))["x"] < _box_of(row)["x"], "the + is not before the row of tiles"
        expect(_result_row(page, _STUB_APP_NAME, "stub-23")).to_be_visible(timeout=15000)


@pytest.mark.timeout(90, func_only=False)
def test_search_finds_what_was_said_inside_something(tmp_path: Path, page: Page) -> None:
    """An app that declares the search capability answers for its own contents; the shell merges."""
    with _running_e2e_server(tmp_path, _PORT, is_stub_searching=True) as server:
        server.stub_source.snippet_by_key = {_FIXTURE_KEY: "the part where we talked about the dock"}
        page.goto(server.base_url)
        _wait_for_desktop(page)

        _search(page, "dock")

        row = _result_row(page, _STUB_APP_NAME, _FIXTURE_KEY)
        expect(row).to_be_visible(timeout=15000)
        # The row shows the line that matched, which is what says why it is a result at all, with
        # the match itself in bold.
        expect(row.locator(".result-sub")).to_have_text("the part where we talked about the dock")
        expect(row.locator(".result-sub mark")).to_have_text("dock")
        assert float(row.locator(".result-sub mark").evaluate("el => getComputedStyle(el).fontWeight")) >= 600


@pytest.mark.timeout(90, func_only=False)
def test_a_match_deep_in_a_long_line_is_brought_into_view(tmp_path: Path, page: Page) -> None:
    """A row shows one line and clips the rest, so a match far along it would sit behind the ellipsis."""
    with _running_e2e_server(tmp_path, _PORT, is_stub_searching=True) as server:
        # Long enough that the match sits well past what one row shows, short enough for a snippet.
        lead = " ".join(f"word{index}" for index in range(24))
        server.stub_source.snippet_by_key = {_FIXTURE_KEY: f"{lead} and then the dock came up"}
        page.goto(server.base_url)
        _wait_for_desktop(page)

        _search(page, "dock")

        row = _result_row(page, _STUB_APP_NAME, _FIXTURE_KEY)
        expect(row).to_be_visible(timeout=15000)
        mark = row.locator(".result-sub mark")
        expect(mark).to_have_text("dock")
        # Cut in front of the match, said so with an ellipsis, and the match itself on screen
        # inside the row's own box rather than clipped off its right edge.
        assert row.locator(".result-sub").text_content().startswith("…")
        mark_box = _box_of(mark)
        text_box = _box_of(row.locator(".result-sub"))
        assert mark_box["x"] + mark_box["width"] <= text_box["x"] + text_box["width"] + 0.5, (
            f"the match {mark_box} is clipped by the row {text_box}"
        )


@pytest.mark.timeout(90, func_only=False)
def test_an_agents_open_lands_as_a_window_and_a_split_tiles_the_pair(tmp_path: Path, page: Page) -> None:
    with _running_e2e_server(tmp_path, _PORT, stub_instances=("stub-1", "stub-2")) as server:
        _serve_stub_pages(page, server)
        page.goto(server.base_url)
        _wait_for_desktop(page)

        _broadcast_layout_op(server.base_url, "open", {"address": _FIXTURE_ADDRESS})

        expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
        _wait_for_saved_window(server.state_dir, _FIXTURE_ADDRESS)

        second = _stub_address("stub-2")
        _broadcast_layout_op(
            server.base_url,
            "split",
            {"address": second, "relative_to": _FIXTURE_ADDRESS, "direction": "right"},
        )

        expect(_window(page, second)).to_be_visible(timeout=15000)
        anchor = _window_rect(page, _FIXTURE_ADDRESS)
        placed = _window_rect(page, second)
        # Tiled: the anchor took one half of the desktop and the new window the other.
        assert anchor["x"] == 0
        assert placed["x"] == anchor["width"]
        assert abs(anchor["width"] - placed["width"]) <= 1
        assert anchor["height"] == placed["height"]


@pytest.mark.timeout(90, func_only=False)
def test_an_agents_open_tiles_the_app_beside_the_window_that_asked_for_it(tmp_path: Path, page: Page) -> None:
    """An app an agent opens lands next to the requester's window, both readable at once.

    The requester here is a chat with a window already on the desktop, which is the real case:
    the user asks a chat for an app and the app should not cover the conversation that produced
    it. Asserted through the browser because the tiling has to survive the frontend's own
    placement, not just the saved arrangement.
    """
    with _running_e2e_server(tmp_path, _PORT, stub_instances=("stub-1", "stub-2")) as server:
        _serve_stub_pages(page, server)
        page.goto(server.base_url)
        _wait_for_desktop(page)

        requester = _stub_address("stub-2")
        _broadcast_layout_op(server.base_url, "open", {"address": requester}, requester=requester)
        expect(_window(page, requester)).to_be_visible(timeout=15000)
        _wait_for_saved_window(server.state_dir, requester)

        _broadcast_layout_op(server.base_url, "open", {"address": _FIXTURE_ADDRESS}, requester=requester)
        expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)

        anchor = _window_rect(page, requester)
        placed = _window_rect(page, _FIXTURE_ADDRESS)
        # Side by side, splitting the desktop: the opener on the left, the new app on the right.
        assert anchor["x"] == 0
        assert placed["x"] == anchor["width"]
        assert abs(anchor["width"] - placed["width"]) <= 1
        assert anchor["height"] == placed["height"]


@pytest.mark.timeout(90, func_only=False)
def test_an_agents_close_puts_a_window_away_without_stopping_it(tmp_path: Path, page: Page) -> None:
    """``close`` is minimize on a desktop: the window goes, the thing keeps running, ``stop`` is its own verb."""
    with _running_e2e_server(tmp_path, _PORT, is_stub_stoppable=True) as e2e_server:
        _an_agents_close_puts_a_window_away(e2e_server, page)


def _an_agents_close_puts_a_window_away(e2e_server: E2EServer, page: Page) -> None:
    _serve_stub_pages(page, e2e_server)
    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)
    _open_icon(page, _STUB_APP_NAME)
    expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    _wait_for_saved_window(e2e_server.state_dir, _FIXTURE_ADDRESS)

    _broadcast_layout_op(e2e_server.base_url, "close", {"address": _FIXTURE_ADDRESS})

    expect(_window(page, _FIXTURE_ADDRESS)).to_be_hidden(timeout=15000)
    # It is still running, still in the dock, and its page is still loaded.
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_have_class(re.compile("is-offscreen"), timeout=10000)
    assert not any(call.startswith("stop:") for call in e2e_server.stub_source.calls)
    assert _surface_report(page, _FIXTURE_ADDRESS)["count"] == 1


@pytest.mark.timeout(90, func_only=False)
def test_an_agents_move_snaps_a_window_beside_another_without_reloading_it(
    tmp_path: Path, page: Page
) -> None:
    with _running_e2e_server(tmp_path, _PORT, stub_instances=("stub-1", "stub-2")) as server:
        _serve_stub_pages(page, server)
        page.goto(server.base_url)
        _wait_for_desktop(page)
        second = _stub_address("stub-2")
        _broadcast_layout_op(server.base_url, "open", {"address": _FIXTURE_ADDRESS})
        expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
        _broadcast_layout_op(server.base_url, "open", {"address": second})
        expect(_window(page, second)).to_be_visible(timeout=15000)
        page.evaluate(_WATCH_SURFACE_REMOVALS_JS)
        _wait_for_surface_shown(page, second, stamp="moved")
        _wait_for_saved_window(server.state_dir, second)

        _broadcast_layout_op(
            server.base_url,
            "move",
            {"address": second, "relative_to": _FIXTURE_ADDRESS, "direction": "right"},
        )

        def _moved() -> bool:
            return _window_rect(page, second)["x"] == _window_rect(page, _FIXTURE_ADDRESS)["width"]

        wait_for(_moved, timeout=15.0, poll_interval=0.2, error_message="the move never landed on screen")
        # The page was not reloaded to move it: same element, same document.
        report = _surface_report(page, second)
        assert report["stamps"] == ["moved"] and report["removals"] == 0


@pytest.mark.timeout(60, func_only=False)
def test_the_desktop_still_comes_up_when_its_arrangement_cannot_be_fetched(
    e2e_server: E2EServer, page: Page
) -> None:
    """Nothing may leave the desktop empty: a failed fetch costs the arrangement, not the desktop."""
    page.route("**/api/layouts/**", lambda route: route.fulfill(status=503, body="{}"))

    page.goto(e2e_server.base_url)
    _wait_for_desktop(page)

    expect(_icon(page, _STUB_APP_NAME)).to_be_visible()
    expect(_dock_tile(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
    # The menu still opens and still offers everything the machine can open.
    _open_launcher(page)
    expect(page.locator(f'#launcher .launcher-item[data-entry="{_STUB_APP_NAME}"]')).to_be_visible()


@pytest.mark.timeout(90, func_only=False)
def test_make_something_is_one_window_and_its_tiles_seed_a_chat(tmp_path: Path, page: Page) -> None:
    with _running_e2e_server(
        tmp_path, _PORT, is_stub_taking_message=True, is_catalog_offered=True, catalog_body=_CATALOG_DOCUMENT
    ) as server:
        _serve_stub_pages(page, server)
        page.goto(server.base_url)
        _wait_for_desktop(page)

        _open_icon(page, "make-something")
        _open_icon(page, "make-something")

        # Asked for twice, open once.
        expect(page.locator(".window .make-body")).to_have_count(1, timeout=15000)
        expect(page.locator(".make-body .start-tile")).not_to_have_count(0)
        expect(page.locator(f'.make-body .template-card[data-template="{_CATALOG_TEMPLATE_SLUG}"]').first).to_be_visible(
            timeout=15000
        )

        page.locator('.make-body .start-tile[data-start="build-app"]').click()

        # The prompt went to the app that declares a ``message`` param, which made an instance for it.
        wait_for(
            lambda: any(
                call.startswith("create:new:") and "build a new app" in call.lower()
                for call in server.stub_source.calls
            ),
            timeout=20.0,
            poll_interval=0.2,
            error_message="the tile never seeded a chat with its prompt",
        )


@pytest.mark.timeout(90, func_only=False)
def test_two_windows_of_one_client_mirror_an_agent_made_arrangement(tmp_path: Path, page: Page) -> None:
    """Both windows of a browser show one desktop: the shell announces every write and they refetch."""
    with _running_e2e_server(tmp_path, _PORT) as server:
        _serve_stub_pages(page, server)
        page.goto(server.base_url)
        _wait_for_desktop(page)
        second_window = page.context.new_page()
        _serve_stub_pages(second_window, server)
        second_window.goto(server.base_url)
        _wait_for_desktop(second_window)

        _broadcast_layout_op(server.base_url, "open", {"address": _FIXTURE_ADDRESS})

        expect(_window(page, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
        expect(_window(second_window, _FIXTURE_ADDRESS)).to_be_visible(timeout=15000)
        second_window.close()
