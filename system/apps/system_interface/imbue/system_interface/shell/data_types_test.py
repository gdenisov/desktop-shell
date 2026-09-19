from typing import Any

from imbue.system_interface.shell.data_types import LayoutRecord
from imbue.system_interface.shell.data_types import LayoutSaveRequest
from imbue.system_interface.shell.data_types import as_desktop_layout_body
from imbue.system_interface.shell.data_types import desktop_of
from imbue.system_interface.shell.data_types import fold_legacy_tabs_into_dockview
from imbue.system_interface.shell.data_types import windows_of
from imbue.system_interface.shell.desktop_document import EMPTY_DESKTOP
from imbue.system_interface.shell.primitives import Address
from imbue.system_interface.shell.primitives import DeviceKind
from imbue.system_interface.shell.primitives import TabId

_FILES = Address("app:files")
_TERMINAL_1 = Address("app:terminal?instance=terminal-1")
_TAB_A = TabId("tab-000000000000000a")
_TAB_B = TabId("tab-000000000000000b")


def _panel_params(address: Address, tab_id: TabId, last_focused_ms: int) -> dict[str, Any]:
    """The params a dockview panel carried, as the shell that wrote them spelled them."""
    return {
        "kind": "instance",
        "address": str(address),
        "tabId": str(tab_id),
        "lastFocusedMs": last_focused_ms,
    }


def _dockview(panels: dict[str, Any]) -> dict[str, Any]:
    return {"grid": {"root": {"type": "branch", "data": []}}, "panels": panels, "activeGroup": "g1"}


def test_a_saved_dockview_arrangement_reads_as_a_desktop_of_windows() -> None:
    """Every docked tab becomes a window; a New Tab panel and a damaged one have no counterpart."""
    body = {
        "dockview": _dockview(
            {
                "pa": {"id": "pa", "params": _panel_params(_FILES, _TAB_A, 7)},
                "pb": {"id": "pb", "params": {**_panel_params(_TERMINAL_1, _TAB_B, 99), "future": True}},
                "new-tab-1": {"id": "new-tab-1", "params": {"kind": "launcher"}},
                "damaged": {"id": "damaged", "params": {"kind": "instance", "address": "not-an-address"}},
                "bare": {"id": "bare"},
                "junk": "not a dict",
            }
        ),
        "device_kind": "desktop",
        "updated_at": None,
    }
    layout = LayoutRecord.model_validate(body)
    windows = windows_of(layout)
    assert [window.address for window in windows] == [_FILES, _TERMINAL_1]
    assert [window.tab_id for window in windows] == [_TAB_A, _TAB_B]
    # The tab that was looked at most recently comes back on top, which is last in the stack.
    assert windows[-1].address == _TERMINAL_1
    # The windows are cascaded rather than stacked on one spot.
    assert windows[0].rect.x != windows[1].rect.x and windows[0].rect.y != windows[1].rect.y
    assert all(not window.is_minimized and not window.is_maximized for window in windows)
    # Nothing of the grid survives into what is written back.
    assert "dockview" not in layout.model_dump(mode="json")


def test_a_migrated_window_keeps_the_name_its_tab_carried() -> None:
    """The name the tab carried comes across, so a migrated window whose instance has since gone
    still knows what it was called instead of falling back to its app's name."""
    layout = LayoutRecord.model_validate(
        {
            "dockview": _dockview(
                {
                    # The name the user gave it wins over the one its app gave it.
                    "pa": {
                        "id": "pa",
                        "params": {**_panel_params(_FILES, _TAB_A, 1), "title": "Files", "customTitle": "Notes"},
                    },
                    "pb": {"id": "pb", "params": {**_panel_params(_TERMINAL_1, _TAB_B, 2), "title": "Build log"}},
                }
            ),
            "device_kind": "desktop",
            "updated_at": None,
        }
    )

    assert [window.last_known_title for window in windows_of(layout)] == ["Notes", "Build log"]


def test_a_migrated_window_with_no_name_to_carry_has_none() -> None:
    layout = LayoutRecord.model_validate(
        {
            "dockview": _dockview({"pa": {"id": "pa", "params": {**_panel_params(_FILES, _TAB_A, 1), "title": ""}}}),
            "device_kind": "desktop",
            "updated_at": None,
        }
    )

    assert windows_of(layout)[0].last_known_title is None


def test_an_arrangement_that_docked_one_instance_twice_becomes_one_window() -> None:
    layout = LayoutRecord.model_validate(
        {
            "dockview": _dockview(
                {
                    "pa": {"id": "pa", "params": _panel_params(_FILES, _TAB_A, 1)},
                    "pb": {"id": "pb", "params": _panel_params(_FILES, _TAB_B, 2)},
                }
            ),
            "device_kind": "desktop",
            "updated_at": None,
        }
    )
    assert [window.address for window in windows_of(layout)] == [_FILES]


def test_a_layout_in_the_oldest_shape_is_read_through_its_tabs_block() -> None:
    """A file written before params-only layouts carried a ``tabs`` block, which was the truth of each panel's identity."""
    legacy = {
        "dockview": _dockview(
            {
                # The older browser wrote params too, but its ``tabs`` block was what it read back: the block wins.
                "pa": {
                    "id": "pa",
                    "params": {"kind": "instance", "address": "app:stale", "tabId": "tab-00000000000000ff"},
                },
                "new-tab-1": {"id": "new-tab-1", "params": {"kind": "launcher"}},
                "orphan": {"id": "orphan"},
            }
        ),
        "tabs": {
            "pa": {"address": str(_FILES), "tab_id": str(_TAB_A), "last_focused_ms": 7},
            "gone": {"address": str(_TERMINAL_1), "tab_id": str(_TAB_B), "last_focused_ms": 0},
        },
        "device_kind": "desktop",
        "updated_at": None,
    }
    layout = LayoutRecord.model_validate(legacy)
    window = windows_of(layout)[0]
    assert window.address == _FILES and window.tab_id == _TAB_A
    # A record for a panel the grid no longer names is dropped.
    assert len(windows_of(layout)) == 1

    # The empty legacy layout, and a body with no ``tabs`` at all, read as a view never arranged.
    assert LayoutRecord.model_validate(
        {"dockview": None, "tabs": {}, "device_kind": "desktop", "updated_at": None}
    ) == LayoutRecord(desktop=None, device_kind=DeviceKind.DESKTOP, updated_at=None)
    current = {"desktop": None, "device_kind": "desktop", "updated_at": None}
    assert fold_legacy_tabs_into_dockview(current) is current
    assert as_desktop_layout_body(current) is current
    assert as_desktop_layout_body("not a mapping") == "not a mapping"

    # The save body takes the same reading, so a window that saves before it reloads is understood.
    request = LayoutSaveRequest.model_validate(
        {
            "client_id": "c1",
            "save_id": "save-0000000000000001",
            "device_kind": "desktop",
            "dockview": legacy["dockview"],
            "tabs": legacy["tabs"],
        }
    )
    assert request.desktop is not None
    assert request.desktop.windows[0].address == _FILES


def test_a_view_never_arranged_reads_as_an_empty_desktop() -> None:
    layout = LayoutRecord(desktop=None, device_kind=DeviceKind.DESKTOP, updated_at=None)
    assert windows_of(layout) == ()
    assert desktop_of(layout) == EMPTY_DESKTOP
