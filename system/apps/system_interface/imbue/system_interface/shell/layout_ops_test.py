from imbue.system_interface.shell.data_types import LayoutRecord
from imbue.system_interface.shell.desktop_document import NOMINAL_DESKTOP_SIZE
from imbue.system_interface.shell.desktop_document import DesktopDocument
from imbue.system_interface.shell.desktop_document import DesktopWindow
from imbue.system_interface.shell.desktop_document import cascade_rect
from imbue.system_interface.shell.desktop_document import minimize_window
from imbue.system_interface.shell.layout_ops import layout_inspect
from imbue.system_interface.shell.layouts import StoredLayout
from imbue.system_interface.shell.primitives import Address
from imbue.system_interface.shell.primitives import ClientId
from imbue.system_interface.shell.primitives import DeviceKind
from imbue.system_interface.shell.primitives import ViewId
from imbue.system_interface.shell.testing import desktop_showing
from imbue.system_interface.shell.testing import layout_showing
from imbue.system_interface.shell.testing import page_id_for_test

_FILES = Address("app:files")
_TERMINAL_1 = Address("app:terminal?instance=terminal-1")


def _stored(client_id: str, view_id: str = "everything") -> StoredLayout:
    return StoredLayout(
        view_id=ViewId(view_id), client_id=ClientId(client_id), layout=layout_showing(_FILES, _TERMINAL_1)
    )


def test_inspect_reports_every_window_with_its_geometry_and_what_is_on_top() -> None:
    summary = layout_inspect(_stored("c1").layout, {"app:files": "Files"})
    assert summary["desktop_size"] == NOMINAL_DESKTOP_SIZE.model_dump(mode="json")
    first, second = summary["windows"]
    assert first["address"] == "app:files" and first["title"] == "Files" and first["tab_id"] == str(page_id_for_test(0))
    assert first["rect"]["width"] > 0 and first["rect"]["height"] > 0
    assert first["is_minimized"] is False and first["is_maximized"] is False
    # The list is the stacking order, so only the last window is the one on top.
    assert first["is_on_top"] is False and second["is_on_top"] is True
    # An instance no app lists has no title to report, and is still reported.
    assert second["address"] == "app:terminal?instance=terminal-1" and second["title"] is None


def test_inspect_reports_a_window_that_has_been_put_away() -> None:
    put_away = LayoutRecord(
        desktop=minimize_window(desktop_showing(_FILES, _TERMINAL_1), page_id_for_test(0)),
        device_kind=DeviceKind.DESKTOP,
        updated_at=None,
    )
    assert layout_inspect(put_away, {})["windows"][0]["is_minimized"] is True


def test_inspect_of_a_view_with_no_desktop_reports_nothing() -> None:
    assert layout_inspect(None, {}) == {"desktop_size": None, "windows": []}


def test_inspect_names_a_window_whose_instance_is_gone_by_what_it_was_called() -> None:
    """A window outlives its instance, so an agent reading the desktop sees what the user sees:
    the name the window still carries, rather than a nameless row."""
    kept = LayoutRecord(
        desktop=DesktopDocument(
            windows=(
                DesktopWindow(
                    tab_id=page_id_for_test(0),
                    address=_FILES,
                    rect=cascade_rect(0, NOMINAL_DESKTOP_SIZE),
                    last_known_title="Notes",
                ),
            ),
            desktop_size=NOMINAL_DESKTOP_SIZE,
        ),
        device_kind=DeviceKind.DESKTOP,
        updated_at=None,
    )

    # Nothing lists it, so there is no live title to take.
    assert layout_inspect(kept, {})["windows"][0]["title"] == "Notes"
    # And the live title wins the moment there is one, however stale the memory.
    assert layout_inspect(kept, {"app:files": "Renamed"})["windows"][0]["title"] == "Renamed"
