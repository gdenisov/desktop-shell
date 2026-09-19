from datetime import timedelta
from pathlib import Path

import pytest

from imbue.imbue_common.model_update import to_update
from imbue.system_interface.shell.data_types import LayoutRecord
from imbue.system_interface.shell.errors import StaleLayoutSaveError
from imbue.system_interface.shell.layouts import LayoutStore
from imbue.system_interface.shell.layouts import empty_layout
from imbue.system_interface.shell.layouts import is_stale_save
from imbue.system_interface.shell.layouts import rebind_tab_in_layout
from imbue.system_interface.shell.layouts import strip_addresses_from_layout
from imbue.system_interface.shell.layouts import unreferenced_addresses
from imbue.system_interface.shell.primitives import Address
from imbue.system_interface.shell.primitives import ClientId
from imbue.system_interface.shell.primitives import DeviceKind
from imbue.system_interface.shell.primitives import TabId
from imbue.system_interface.shell.testing import TEST_NOW
from imbue.system_interface.shell.testing import addresses_of_layout
from imbue.system_interface.shell.testing import desktop_showing
from imbue.system_interface.shell.testing import layout_showing
from imbue.system_interface.shell.testing import page_id_for_test

_FILES = Address("app:files")
_TERMINAL_1 = Address("app:terminal?instance=terminal-1")
_UNKNOWN_PAGE = TabId("tab-00000000000000ff")


def _layout(device_kind: DeviceKind = DeviceKind.DESKTOP) -> LayoutRecord:
    """A desktop showing the two test instances, saved by a client on ``device_kind``."""
    showing = layout_showing(_FILES, _TERMINAL_1)
    return showing.model_copy_update(to_update(showing.field_ref().device_kind, device_kind))


def test_read_falls_back_from_own_to_seed_to_empty(tmp_path: Path) -> None:
    store = LayoutStore(state_directory=tmp_path)
    assert store.read_layout("everything", "c1", DeviceKind.DESKTOP) == empty_layout(DeviceKind.DESKTOP)

    saved = store.save_browser_layout("everything", "c1", _layout(), None, TEST_NOW)
    assert saved is not None and saved.updated_at == TEST_NOW
    assert store.read_layout("everything", "c1", DeviceKind.MOBILE) == saved
    # Another desktop client inherits the seed; a mobile one has no seed yet.
    assert store.read_layout("everything", "c2", DeviceKind.DESKTOP) == saved
    assert store.read_layout("everything", "c2", DeviceKind.MOBILE) == empty_layout(DeviceKind.MOBILE)
    assert (tmp_path / "layouts" / "everything" / "c1.json").is_file()
    assert (tmp_path / "layouts" / "everything" / "seed.desktop.json").is_file()


def test_all_client_layouts_skips_seeds_and_unreadable_files(tmp_path: Path) -> None:
    store = LayoutStore(state_directory=tmp_path)
    store.save_browser_layout("everything", "c1", _layout(), None, TEST_NOW)
    store.save_browser_layout("alpha", "c2", _layout(DeviceKind.MOBILE), None, TEST_NOW)
    (tmp_path / "layouts" / "alpha" / "broken.json").write_text("{")
    stored = store.all_client_layouts()
    assert [(str(item.view_id), str(item.client_id)) for item in stored] == [("alpha", "c2"), ("everything", "c1")]
    assert store.referenced_addresses() == {_FILES, _TERMINAL_1}


def test_pages_are_found_and_rebound_by_id(tmp_path: Path) -> None:
    store = LayoutStore(state_directory=tmp_path)
    store.save_browser_layout("everything", "c1", _layout(), None, TEST_NOW)
    store.save_browser_layout("alpha", "c1", _layout(), None, TEST_NOW)
    page = page_id_for_test(1)
    assert [str(found.stored.view_id) for found in store.find_tab(page)] == ["alpha", "everything"]
    rebound = Address("app:terminal?instance=terminal-2")
    rewritten = store.rebind_tab(page, rebound, TEST_NOW)
    assert {str(stored.view_id) for stored in rewritten} == {"alpha", "everything"}
    layout = store.read_layout("alpha", "c1", DeviceKind.DESKTOP)
    assert addresses_of_layout(layout) == [_FILES, rebound]
    # The rebind moves the address alone: the window keeps its page and its place on the desktop.
    assert layout.desktop is not None
    rebound_window = layout.desktop.windows[1]
    assert rebound_window.tab_id == page
    assert rebound_window.rect == desktop_showing(_FILES, _TERMINAL_1).windows[1].rect
    assert store.find_tab(_UNKNOWN_PAGE) == []
    # The seeds follow, so a client that arrives later starts from the rebound window too.
    assert addresses_of_layout(store.read_layout("alpha", "c9", DeviceKind.DESKTOP)) == [_FILES, rebound]
    untouched = _layout()
    assert rebind_tab_in_layout(untouched, _UNKNOWN_PAGE, rebound) is untouched


def test_removed_addresses_leave_every_desktop(tmp_path: Path) -> None:
    store = LayoutStore(state_directory=tmp_path)
    store.save_browser_layout("everything", "c1", _layout(), None, TEST_NOW)
    rewritten = store.remove_addresses_everywhere([_TERMINAL_1], TEST_NOW)
    assert len(rewritten) == 1
    assert addresses_of_layout(store.read_layout("everything", "c1", DeviceKind.DESKTOP)) == [_FILES]
    # Removing the last window leaves the desktop itself, which still carries the icons and the dock.
    store.remove_addresses_everywhere([_FILES], TEST_NOW)
    emptied = store.read_layout("everything", "c1", DeviceKind.DESKTOP)
    assert emptied.desktop is not None and emptied.desktop.windows == ()
    # Nothing to remove rewrites nothing.
    assert store.remove_addresses_everywhere([_FILES], TEST_NOW) == []
    # The seed was stripped too, rather than overwritten with a client's layout.
    assert addresses_of_layout(store.read_layout("everything", "c9", DeviceKind.DESKTOP)) == []


def test_a_browser_save_is_refused_when_the_stored_arrangement_is_newer(tmp_path: Path) -> None:
    store = LayoutStore(state_directory=tmp_path)
    first = store.save_browser_layout("everything", "c1", _layout(), None, TEST_NOW)
    assert first is not None
    later = TEST_NOW + timedelta(seconds=5)
    # The shell edited the file after the window fetched it.
    store.write_client_layout("everything", "c1", strip_addresses_from_layout(_layout(), [_FILES]), later)
    with pytest.raises(StaleLayoutSaveError):
        store.save_browser_layout("everything", "c1", _layout(), TEST_NOW, later + timedelta(seconds=1))
    # A window that fetched the newer arrangement may save over it.
    saved = store.save_browser_layout("everything", "c1", _layout(), later, later + timedelta(seconds=2))
    assert saved is not None and saved.updated_at == later + timedelta(seconds=2)
    # A window that never fetched anything is stale against any stored arrangement.
    with pytest.raises(StaleLayoutSaveError):
        store.save_browser_layout("everything", "c1", _layout(), None, later + timedelta(seconds=3))
    assert is_stale_save(None, None) is False
    assert is_stale_save(empty_layout(DeviceKind.DESKTOP), None) is False


def test_a_browser_save_that_changes_nothing_is_skipped(tmp_path: Path) -> None:
    store = LayoutStore(state_directory=tmp_path)
    first = store.save_browser_layout("everything", "c1", _layout(), None, TEST_NOW)
    assert first is not None
    assert store.save_browser_layout("everything", "c1", _layout(), TEST_NOW, TEST_NOW + timedelta(seconds=1)) is None
    assert store.read_layout("everything", "c1", DeviceKind.DESKTOP).updated_at == TEST_NOW


def test_an_edit_reads_the_stored_arrangement_at_the_write_and_skips_a_change_of_nothing(tmp_path: Path) -> None:
    store = LayoutStore(state_directory=tmp_path)
    seen: list[LayoutRecord] = []

    def drop_files(layout: LayoutRecord) -> LayoutRecord:
        seen.append(layout)
        return strip_addresses_from_layout(layout, [_FILES])

    # A client with no arrangement of the view starts from the seed of its device kind, then from its own file.
    store.save_browser_layout("everything", "seed-maker", _layout(), None, TEST_NOW)
    first = store.edit_client_layout(
        "everything", "c1", DeviceKind.DESKTOP, drop_files, TEST_NOW + timedelta(seconds=1)
    )
    assert first.is_written is True and addresses_of_layout(first.layout) == [_TERMINAL_1]
    assert addresses_of_layout(seen[0]) == [_FILES, _TERMINAL_1]
    assert store.read_client_layout("everything", "c1") == first.layout
    # The edit is handed what is stored when it runs, not an earlier snapshot: a browser save in between is what it sees.
    store.save_browser_layout("everything", "c1", _layout(), first.layout.updated_at, TEST_NOW + timedelta(seconds=2))
    second = store.edit_client_layout(
        "everything", "c1", DeviceKind.DESKTOP, drop_files, TEST_NOW + timedelta(seconds=3)
    )
    assert addresses_of_layout(seen[-1]) == [_FILES, _TERMINAL_1] and second.is_written is True
    # An edit that changes nothing is neither written nor stamped.
    third = store.edit_client_layout(
        "everything", "c1", DeviceKind.DESKTOP, drop_files, TEST_NOW + timedelta(seconds=4)
    )
    assert third.is_written is False and third.layout == second.layout
    assert store.read_client_layout("everything", "c1") == second.layout


def test_the_shells_own_write_leaves_the_seed_alone(tmp_path: Path) -> None:
    store = LayoutStore(state_directory=tmp_path)
    store.save_browser_layout("everything", "c1", _layout(), None, TEST_NOW)
    edited = strip_addresses_from_layout(_layout(), [_FILES])
    written = store.write_client_layout("everything", "c1", edited, TEST_NOW + timedelta(seconds=1))
    assert addresses_of_layout(written) == [_TERMINAL_1]
    assert store.read_client_layout("everything", "c1") == written
    assert store.read_client_layout("everything", "c9") is None
    assert addresses_of_layout(store.read_layout("everything", "c9", DeviceKind.DESKTOP)) == [_FILES, _TERMINAL_1]


def test_stripping_an_address_nothing_shows_changes_nothing() -> None:
    untouched = strip_addresses_from_layout(_layout(), [Address("app:browser")])
    assert untouched == _layout()
    assert unreferenced_addresses([_FILES, _TERMINAL_1], {_FILES}) == [_TERMINAL_1]


def test_client_and_view_layouts_can_be_deleted(tmp_path: Path) -> None:
    store = LayoutStore(state_directory=tmp_path)
    store.save_browser_layout("everything", "c1", _layout(), None, TEST_NOW)
    store.save_browser_layout("alpha", "c1", _layout(), None, TEST_NOW)
    store.save_browser_layout("alpha", "c2", _layout(), None, TEST_NOW)
    assert store.delete_client_layouts(ClientId("c1")) == 2
    assert [str(stored.client_id) for stored in store.all_client_layouts()] == ["c2"]
    store.delete_view_layouts("alpha")
    assert store.all_client_layouts() == []
    assert not (tmp_path / "layouts" / "alpha").exists()
    store.delete_view_layouts("never-existed")
