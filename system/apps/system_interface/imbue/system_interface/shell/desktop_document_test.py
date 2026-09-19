import pytest
from pydantic import ValidationError

from imbue.system_interface.shell.desktop_document import DEFAULT_SPLIT_RATIO
from imbue.system_interface.shell.desktop_document import EMPTY_DESKTOP
from imbue.system_interface.shell.desktop_document import MIN_WINDOW_HEIGHT
from imbue.system_interface.shell.desktop_document import MIN_WINDOW_WIDTH
from imbue.system_interface.shell.desktop_document import NOMINAL_DESKTOP_SIZE
from imbue.system_interface.shell.desktop_document import DesktopDocument
from imbue.system_interface.shell.desktop_document import DesktopSize
from imbue.system_interface.shell.desktop_document import DesktopWindow
from imbue.system_interface.shell.desktop_document import Direction
from imbue.system_interface.shell.desktop_document import IconArrangement
from imbue.system_interface.shell.desktop_document import GridCell
from imbue.system_interface.shell.desktop_document import WindowRect
from imbue.system_interface.shell.desktop_document import add_window
from imbue.system_interface.shell.desktop_document import cascade_rect
from imbue.system_interface.shell.desktop_document import focus_window
from imbue.system_interface.shell.desktop_document import minimize_window
from imbue.system_interface.shell.desktop_document import move_window_beside
from imbue.system_interface.shell.desktop_document import rebind_tab_in_document
from imbue.system_interface.shell.desktop_document import remove_window
from imbue.system_interface.shell.desktop_document import split_beside
from imbue.system_interface.shell.desktop_document import tiled_rects
from imbue.system_interface.shell.desktop_document import window_for_address
from imbue.system_interface.shell.desktop_document import without_addresses
from imbue.system_interface.shell.errors import LayoutOpError
from imbue.system_interface.shell.errors import WindowNotFoundError
from imbue.system_interface.shell.primitives import Address
from imbue.system_interface.shell.primitives import TabId
from imbue.system_interface.shell.testing import desktop_showing
from imbue.system_interface.shell.testing import page_id_for_test

_FILES = Address("app:files")
_TERMINAL_1 = Address("app:terminal?instance=terminal-1")
_TERMINAL_2 = Address("app:terminal?instance=terminal-2")
_NEW_TAB = TabId("tab-00000000000000ff")
_SMALL_DESKTOP = DesktopSize(width=1000, height=700)


def test_a_new_window_lands_on_top_of_the_stack_stepped_along_the_cascade() -> None:
    opened = add_window(desktop_showing(_FILES), _TERMINAL_1, _NEW_TAB, None)
    assert [window.address for window in opened.windows] == [_FILES, _TERMINAL_1]
    first, second = opened.windows
    assert (second.rect.x, second.rect.y) != (first.rect.x, first.rect.y)


def test_a_window_is_placed_where_it_is_asked_to_be_but_never_off_the_desktop() -> None:
    desktop = DesktopDocument(desktop_size=_SMALL_DESKTOP)
    placed = add_window(
        desktop, _FILES, _NEW_TAB, WindowRect(x=9000, y=9000, width=4000, height=4000)
    )
    rect = placed.windows[0].rect
    assert rect.width == _SMALL_DESKTOP.width and rect.height == _SMALL_DESKTOP.height
    assert rect.x == 0 and rect.y == 0


def test_focusing_raises_a_window_and_takes_it_back_out_of_the_dock() -> None:
    desktop = minimize_window(desktop_showing(_FILES, _TERMINAL_1), page_id_for_test(0))
    assert desktop.windows[0].is_minimized is True
    raised = focus_window(desktop, page_id_for_test(0))
    assert [window.address for window in raised.windows] == [_TERMINAL_1, _FILES]
    assert raised.windows[-1].is_minimized is False


def test_putting_a_window_away_keeps_it_in_the_document_so_it_comes_back_instantly() -> None:
    desktop = minimize_window(desktop_showing(_FILES, _TERMINAL_1), page_id_for_test(1))
    assert [window.address for window in desktop.windows] == [_FILES, _TERMINAL_1]
    assert desktop.windows[1].is_minimized is True
    # Its place in the stack is untouched: minimizing is not a raise.
    assert desktop.windows[1].tab_id == page_id_for_test(1)


def test_removing_a_window_leaves_the_others_alone() -> None:
    desktop = remove_window(desktop_showing(_FILES, _TERMINAL_1), page_id_for_test(0))
    assert [window.address for window in desktop.windows] == [_TERMINAL_1]


def test_every_verb_refuses_a_page_this_desktop_does_not_hold() -> None:
    desktop = desktop_showing(_FILES)
    for act in (focus_window, minimize_window, remove_window):
        with pytest.raises(WindowNotFoundError):
            act(desktop, _NEW_TAB)


@pytest.mark.parametrize(
    ("direction", "expected_anchor", "expected_placed"),
    [
        (Direction.RIGHT, (0, 0, 600, 800), (600, 0, 600, 800)),
        (Direction.LEFT, (600, 0, 600, 800), (0, 0, 600, 800)),
        (Direction.BELOW, (0, 0, 1200, 400), (0, 400, 1200, 400)),
        (Direction.ABOVE, (0, 400, 1200, 400), (0, 0, 1200, 400)),
    ],
)
def test_a_split_tiles_the_pair_over_the_whole_desktop(
    direction: Direction, expected_anchor: tuple[int, int, int, int], expected_placed: tuple[int, int, int, int]
) -> None:
    anchor, placed = tiled_rects(NOMINAL_DESKTOP_SIZE, direction, DEFAULT_SPLIT_RATIO)
    assert (anchor.x, anchor.y, anchor.width, anchor.height) == expected_anchor
    assert (placed.x, placed.y, placed.width, placed.height) == expected_placed


def test_a_split_ratio_decides_how_much_the_new_window_takes() -> None:
    wide = DesktopSize(width=2000, height=1000)
    anchor, placed = tiled_rects(wide, Direction.RIGHT, 0.25)
    assert placed.width == 500 and anchor.width == 1500
    # An absurd ratio is held to what can still be dragged open again.
    _, hair_thin = tiled_rects(wide, Direction.RIGHT, 0.0001)
    assert hair_thin.width >= MIN_WINDOW_WIDTH


def test_splitting_moves_the_anchor_aside_and_opens_the_new_window_beside_it() -> None:
    desktop = desktop_showing(_FILES)
    split = split_beside(desktop, page_id_for_test(0), _TERMINAL_1, _NEW_TAB, Direction.RIGHT, DEFAULT_SPLIT_RATIO)
    anchor, placed = split.windows
    assert anchor.address == _FILES and placed.address == _TERMINAL_1
    assert (anchor.rect.x, anchor.rect.width) == (0, 600)
    assert (placed.rect.x, placed.rect.width) == (600, 600)
    # The new window is on top, and both fill the desktop's height.
    assert split.windows[-1].tab_id == _NEW_TAB
    assert anchor.rect.height == NOMINAL_DESKTOP_SIZE.height


def test_splitting_within_opens_the_new_window_over_its_anchor() -> None:
    desktop = desktop_showing(_FILES)
    split = split_beside(desktop, page_id_for_test(0), _TERMINAL_1, _NEW_TAB, Direction.WITHIN, DEFAULT_SPLIT_RATIO)
    assert split.windows[1].rect == split.windows[0].rect
    assert split.windows[-1].address == _TERMINAL_1


def test_moving_snaps_an_open_window_beside_its_anchor_and_raises_it() -> None:
    desktop = desktop_showing(_FILES, _TERMINAL_1, _TERMINAL_2)
    moved = move_window_beside(desktop, page_id_for_test(2), page_id_for_test(0), Direction.RIGHT, DEFAULT_SPLIT_RATIO)
    by_address = {window.address: window for window in moved.windows}
    assert (by_address[_FILES].rect.x, by_address[_FILES].rect.width) == (0, 600)
    assert (by_address[_TERMINAL_2].rect.x, by_address[_TERMINAL_2].rect.width) == (600, 600)
    # The window that was moved is the one on top, and nothing else moved.
    assert moved.windows[-1].address == _TERMINAL_2
    assert by_address[_TERMINAL_1].rect == desktop.windows[1].rect


def test_moving_within_puts_a_window_in_its_anchors_place_directly_above_it() -> None:
    desktop = desktop_showing(_FILES, _TERMINAL_1, _TERMINAL_2)
    moved = move_window_beside(desktop, page_id_for_test(2), page_id_for_test(0), Direction.WITHIN, DEFAULT_SPLIT_RATIO)
    by_address = {window.address: window for window in moved.windows}
    assert by_address[_TERMINAL_2].rect == by_address[_FILES].rect
    assert moved.windows[-1].address == _TERMINAL_2


def test_moving_brings_a_window_back_from_the_dock_and_out_of_maximized() -> None:
    maximized = DesktopWindow(
        tab_id=page_id_for_test(1),
        address=_TERMINAL_1,
        rect=WindowRect(x=0, y=0, width=1200, height=800),
        is_minimized=True,
        is_maximized=True,
        restore_rect=WindowRect(x=10, y=10, width=400, height=300),
    )
    desktop = DesktopDocument(
        windows=(desktop_showing(_FILES).windows[0], maximized), desktop_size=NOMINAL_DESKTOP_SIZE
    )
    moved = move_window_beside(desktop, page_id_for_test(1), page_id_for_test(0), Direction.RIGHT, DEFAULT_SPLIT_RATIO)
    placed = moved.windows[-1]
    assert placed.is_minimized is False and placed.is_maximized is False and placed.restore_rect is None


def test_a_window_cannot_be_moved_relative_to_itself() -> None:
    with pytest.raises(LayoutOpError):
        move_window_beside(
            desktop_showing(_FILES), page_id_for_test(0), page_id_for_test(0), Direction.RIGHT, DEFAULT_SPLIT_RATIO
        )


def test_a_window_is_found_by_its_exact_address_or_by_its_app_alone() -> None:
    desktop = desktop_showing(_TERMINAL_1)
    assert window_for_address(desktop, _TERMINAL_1) is not None
    # A bare app address is what an agent types when it means "whichever one is open".
    assert window_for_address(desktop, Address("app:terminal")) is not None
    assert window_for_address(desktop, _FILES) is None


def test_rebinding_points_a_window_at_another_instance_and_closes_the_one_it_replaces() -> None:
    desktop = desktop_showing(_FILES, _TERMINAL_1, _TERMINAL_2)
    rebound = rebind_tab_in_document(desktop, page_id_for_test(1), _TERMINAL_2)
    assert [window.address for window in rebound.windows] == [_FILES, _TERMINAL_2]
    assert rebound.windows[1].tab_id == page_id_for_test(1)
    # A rebind to what the window already shows changes nothing at all.
    assert rebind_tab_in_document(desktop, page_id_for_test(1), _TERMINAL_1) is desktop


def test_dropping_deleted_instances_leaves_the_desktop_itself_standing() -> None:
    desktop = desktop_showing(_FILES, _TERMINAL_1)
    stripped = without_addresses(desktop, [_FILES])
    assert [window.address for window in stripped.windows] == [_TERMINAL_1]
    assert without_addresses(desktop, [Address("app:browser")]) is desktop
    assert without_addresses(desktop, [_FILES, _TERMINAL_1]).windows == ()


def test_one_instance_cannot_be_open_in_two_windows_of_one_desktop() -> None:
    with pytest.raises(ValidationError):
        DesktopDocument(
            windows=(
                DesktopWindow(tab_id=page_id_for_test(0), address=_FILES, rect=cascade_rect(0, NOMINAL_DESKTOP_SIZE)),
                DesktopWindow(tab_id=page_id_for_test(1), address=_FILES, rect=cascade_rect(1, NOMINAL_DESKTOP_SIZE)),
            )
        )


def test_a_maximized_window_must_say_where_it_goes_back_to() -> None:
    with pytest.raises(ValidationError):
        DesktopWindow(
            tab_id=page_id_for_test(0),
            address=_FILES,
            rect=WindowRect(x=0, y=0, width=1200, height=800),
            is_maximized=True,
        )


def test_an_icon_sits_in_a_cell_of_the_desktops_grid() -> None:
    arrangement = IconArrangement(
        cell_by_entry={"files": GridCell(column=0, row=0), "terminal": GridCell(column=11, row=5)}
    )
    assert arrangement.cell_by_entry["terminal"].column == 11
    # A file naming one cell twice still opens: the browser gives the cell to the first to claim
    # it as it draws, rather than the whole desktop being refused for being untidy.
    IconArrangement(cell_by_entry={"files": GridCell(column=2, row=2), "terminal": GridCell(column=2, row=2)})
    # Off the top or the left is not a cell, though.
    with pytest.raises(ValidationError):
        GridCell(column=-1, row=0)


def test_a_window_is_never_shrunk_below_what_can_be_grabbed() -> None:
    with pytest.raises(ValueError):
        WindowRect(x=0, y=0, width=MIN_WINDOW_WIDTH - 1, height=MIN_WINDOW_HEIGHT)
    tiny = DesktopSize(width=10, height=10)
    rect = cascade_rect(0, tiny)
    assert rect.width == MIN_WINDOW_WIDTH and rect.height == MIN_WINDOW_HEIGHT


def test_an_empty_desktop_is_what_a_fresh_view_starts_from() -> None:
    assert EMPTY_DESKTOP.windows == ()
    assert EMPTY_DESKTOP.icons.cell_by_entry == {}
    assert EMPTY_DESKTOP.dock.is_hiding is False
