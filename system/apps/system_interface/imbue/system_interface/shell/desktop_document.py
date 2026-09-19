"""The desktop document: what one client's arrangement of one view holds, and the pure editor over it.

A layout file used to hold dockview's serialized grid. The shell is a desktop now, so it holds
this instead (contracts.md section 6): the floating windows with their geometry and stacking
order, where the desktop's launcher icons sit, and whether the dock hides itself.

Everything here is pure. The browser edits the same document for the user's own gestures and
saves it through the layout route; the shell edits it for an agent's op and writes the file
itself, so an op lands whether or not a browser is connected.

The window list IS the stacking order: last is topmost. A window carries the address it shows
and the tab id its page was minted under, which is the same identity a dockview panel's params
carried, so tab rebinds, deletes and the app contract are unchanged by the move.
"""

from collections.abc import Sequence
from enum import auto
from typing import Any
from typing import Final

from pydantic import Field
from pydantic import model_validator

from imbue.imbue_common.enums import LowerCaseStrEnum
from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.model_update import to_update
from imbue.imbue_common.pure import pure
from imbue.system_interface.shell.errors import LayoutOpError
from imbue.system_interface.shell.errors import WindowNotFoundError
from imbue.system_interface.shell.primitives import Address
from imbue.system_interface.shell.primitives import TabId

# The desktop a document is placed against when it records no size of its own: a never-arranged
# view, or an op on a client whose browser has not saved since the desktop landed. The browser
# records its real desktop area on every save, and nudges a window back into view on restore, so
# these numbers only have to be a sane shape rather than this client's true viewport.
NOMINAL_DESKTOP_WIDTH: Final[int] = 1200
NOMINAL_DESKTOP_HEIGHT: Final[int] = 800

# A window small enough to be untouchable cannot be dragged open again, so no edit here may
# produce one (the browser holds its own resize to the same floor).
MIN_WINDOW_WIDTH: Final[int] = 340
MIN_WINDOW_HEIGHT: Final[int] = 200

# What a window an op opens is sized at, and how far each one steps down and across from the
# last, which is what keeps a run of opens from stacking into one pile.
DEFAULT_WINDOW_WIDTH: Final[int] = 900
DEFAULT_WINDOW_HEIGHT: Final[int] = 620
CASCADE_ORIGIN_X: Final[int] = 120
CASCADE_ORIGIN_Y: Final[int] = 72
CASCADE_STEP: Final[int] = 30
CASCADE_LENGTH: Final[int] = 7

# The share of the desktop a split gives the window it opens. Half, because a split is the
# gesture for putting two things side by side; ``--ratio`` scales the split point from here.
DEFAULT_SPLIT_RATIO: Final[float] = 0.5
MIN_SPLIT_RATIO: Final[float] = 0.05
MAX_SPLIT_RATIO: Final[float] = 0.95

# The version stamped into every document written, so a later shape change can tell what it is
# reading rather than guessing from the keys present.
DESKTOP_DOCUMENT_VERSION: Final[int] = 1


class Direction(LowerCaseStrEnum):
    """Which side of its anchor a split or a move puts a window on (a wire value from ``layout.py``)."""

    LEFT = auto()
    RIGHT = auto()
    ABOVE = auto()
    BELOW = auto()
    WITHIN = auto()


class DesktopSize(FrozenModel):
    """The desktop area a document's geometry was laid out against."""

    width: int = Field(gt=0, description="The desktop area's width in CSS pixels")
    height: int = Field(gt=0, description="The desktop area's height in CSS pixels")


NOMINAL_DESKTOP_SIZE: Final[DesktopSize] = DesktopSize(
    width=NOMINAL_DESKTOP_WIDTH, height=NOMINAL_DESKTOP_HEIGHT
)


class WindowRect(FrozenModel):
    """Where a window sits on the desktop, in CSS pixels from the desktop area's top left."""

    x: int = Field(description="Distance from the desktop's left edge")
    y: int = Field(description="Distance from the desktop's top edge")
    width: int = Field(ge=MIN_WINDOW_WIDTH, description="The window's width")
    height: int = Field(ge=MIN_WINDOW_HEIGHT, description="The window's height")


class DesktopWindow(FrozenModel):
    """One floating window: the instance it shows, where it sits, and how it is showing it."""

    tab_id: TabId = Field(description="The page's id, minted when the page was first opened")
    address: Address = Field(description="The instance the window shows")
    rect: WindowRect = Field(description="Where the window sits, or the whole desktop while maximized")
    is_minimized: bool = Field(default=False, description="Whether the window is out of sight with its page still loaded")
    is_maximized: bool = Field(default=False, description="Whether the window fills the desktop")
    restore_rect: WindowRect | None = Field(
        default=None, description="Where a maximized window goes back to; None when it is not maximized"
    )
    last_focused_ms: int = Field(
        default=0, description="Epoch milliseconds the window was last raised, 0 when never"
    )
    # What the window was called the last time its instance was listed. A window outlives the
    # instance leaving its app's list (contracts.md section 6), and a restored window whose
    # instance is gone has no title to read: without this the user would be shown the address,
    # which is developer text. Never authoritative -- the live title wins whenever there is one.
    last_known_title: str | None = Field(
        default=None, description="The title the window last showed, for when its instance is not listed"
    )

    @model_validator(mode="after")
    def _maximized_windows_carry_a_restore_rect(self) -> "DesktopWindow":
        if self.is_maximized and self.restore_rect is None:
            raise LayoutOpError(f"the maximized window {self.tab_id} carries no rect to restore to")
        return self


class GridCell(FrozenModel):
    """One cell of the desktop's icon grid, counted from its top left."""

    column: int = Field(ge=0, description="How many cells in from the desktop's left edge")
    row: int = Field(ge=0, description="How many cells down from the desktop's top edge")


class IconArrangement(FrozenModel):
    """Where the desktop's launcher icons sit: the cell of the desktop's grid the user put each in.

    Cells rather than pixels, and cells rather than a flat index, because the grid spans the whole
    desktop and a narrower desktop has fewer columns: a column and a row still mean something when
    the grid changes shape, and an icon whose cell is off the edge is DRAWN somewhere reachable
    without its saved cell being rewritten, so widening the desktop restores the arrangement.

    Two icons sharing a cell is not refused here -- the browser resolves it as it draws, by giving
    the cell to the first to claim it -- because a file that is merely untidy should still open.
    """

    # Keyed by entry name -- an app's registry name, or the shell's own built-in entry -- because
    # which entries exist is decided by the registry at render time rather than by this document.
    # An entry with no cell here has never been moved and flows along the top row.
    cell_by_entry: dict[str, GridCell] = Field(
        default_factory=dict, description="The grid cell each moved entry occupies"
    )


class DockPreferences(FrozenModel):
    """What the user chose about the dock itself."""

    is_hiding: bool = Field(default=False, description="Whether the dock slides away until it is hovered")


class DesktopDocument(FrozenModel):
    """One client's desktop for one view: its windows in stacking order, its icons, and its dock."""

    version: int = Field(default=DESKTOP_DOCUMENT_VERSION, description="The document shape this was written in")
    windows: tuple[DesktopWindow, ...] = Field(
        default=(), description="Every open window, bottom of the stack first"
    )
    icons: IconArrangement = Field(default_factory=IconArrangement, description="Where the launcher icons sit")
    dock: DockPreferences = Field(default_factory=DockPreferences, description="The dock's own settings")
    desktop_size: DesktopSize | None = Field(
        default=None, description="The desktop area this geometry was laid out against, None until a browser saves"
    )

    @model_validator(mode="after")
    def _one_window_per_page_and_per_instance(self) -> "DesktopDocument":
        tab_ids = [window.tab_id for window in self.windows]
        if len(set(tab_ids)) != len(tab_ids):
            raise LayoutOpError("the same page cannot be open in two windows of one desktop")
        # An instance has exactly one live page, machine-wide, so two windows on one address
        # would be two frames fighting over one document.
        addresses = [window.address for window in self.windows]
        if len(set(addresses)) != len(addresses):
            raise LayoutOpError("the same instance cannot be open in two windows of one desktop")
        return self


EMPTY_DESKTOP: Final[DesktopDocument] = DesktopDocument()


@pure
def desktop_size_of(document: DesktopDocument) -> DesktopSize:
    """The desktop the document's geometry is in, or the nominal one for a document no browser has saved."""
    return document.desktop_size if document.desktop_size is not None else NOMINAL_DESKTOP_SIZE


@pure
def window_by_tab_id(document: DesktopDocument, tab_id: TabId) -> DesktopWindow | None:
    for window in document.windows:
        if window.tab_id == tab_id:
            return window
    return None


@pure
def window_for_address(document: DesktopDocument, address: Address) -> DesktopWindow | None:
    """The window showing ``address``: an exact match, else any window of the app for a bare app address."""
    for window in document.windows:
        if window.address == address:
            return window
    if address.key is not None:
        return None
    for window in document.windows:
        if window.address.app == address.app:
            return window
    return None


@pure
def require_window_for_address(document: DesktopDocument, address: Address) -> DesktopWindow:
    window = window_for_address(document, address)
    if window is None:
        raise WindowNotFoundError(f"{address} has no window on this desktop")
    return window


@pure
def addresses_of(document: DesktopDocument) -> list[Address]:
    return [window.address for window in document.windows]


@pure
def _clamped_rect(rect: WindowRect, desktop_size: DesktopSize) -> WindowRect:
    """A rect that fits the desktop and starts inside it, without shrinking past the minimum window."""
    width = max(MIN_WINDOW_WIDTH, min(rect.width, desktop_size.width))
    height = max(MIN_WINDOW_HEIGHT, min(rect.height, desktop_size.height))
    return WindowRect(
        x=max(0, min(rect.x, desktop_size.width - width)),
        y=max(0, min(rect.y, desktop_size.height - height)),
        width=width,
        height=height,
    )


@pure
def cascade_rect(window_count: int, desktop_size: DesktopSize) -> WindowRect:
    """Where the next window an op opens goes: a default-sized window, stepped along the cascade."""
    offset = (window_count % CASCADE_LENGTH) * CASCADE_STEP
    return _clamped_rect(
        WindowRect(
            x=CASCADE_ORIGIN_X + offset,
            y=CASCADE_ORIGIN_Y + offset,
            width=DEFAULT_WINDOW_WIDTH,
            height=DEFAULT_WINDOW_HEIGHT,
        ),
        desktop_size,
    )


@pure
def tiled_rects(desktop_size: DesktopSize, direction: Direction, ratio: float) -> tuple[WindowRect, WindowRect]:
    """The two halves a split lands on: the anchor's side, then the side the new window takes.

    A split is the tiling gesture -- put these two where both can be seen -- so it spends the whole
    desktop on the pair rather than subdividing whatever the anchor happened to occupy. ``ratio`` is
    the share the new window takes, so the default half-and-half is ``0.5``.
    """
    held = min(max(ratio, MIN_SPLIT_RATIO), MAX_SPLIT_RATIO)
    is_horizontal = direction in (Direction.LEFT, Direction.RIGHT)
    if is_horizontal:
        placed_width = max(MIN_WINDOW_WIDTH, round(desktop_size.width * held))
        anchor_width = max(MIN_WINDOW_WIDTH, desktop_size.width - placed_width)
        placed_x = 0 if direction is Direction.LEFT else desktop_size.width - placed_width
        anchor_x = desktop_size.width - anchor_width if direction is Direction.LEFT else 0
        anchor = WindowRect(x=anchor_x, y=0, width=anchor_width, height=desktop_size.height)
        placed = WindowRect(x=placed_x, y=0, width=placed_width, height=desktop_size.height)
        return anchor, placed
    placed_height = max(MIN_WINDOW_HEIGHT, round(desktop_size.height * held))
    anchor_height = max(MIN_WINDOW_HEIGHT, desktop_size.height - placed_height)
    placed_y = 0 if direction is Direction.ABOVE else desktop_size.height - placed_height
    anchor_y = desktop_size.height - anchor_height if direction is Direction.ABOVE else 0
    anchor = WindowRect(x=0, y=anchor_y, width=desktop_size.width, height=anchor_height)
    placed = WindowRect(x=0, y=placed_y, width=desktop_size.width, height=placed_height)
    return anchor, placed


@pure
def _with_windows(document: DesktopDocument, windows: Sequence[DesktopWindow]) -> DesktopDocument:
    return document.model_copy_update(to_update(document.field_ref().windows, tuple(windows)))


@pure
def _replaced(
    document: DesktopDocument, tab_id: TabId, replacement: DesktopWindow
) -> DesktopDocument:
    return _with_windows(
        document,
        [replacement if window.tab_id == tab_id else window for window in document.windows],
    )


@pure
def _raised(document: DesktopDocument, tab_id: TabId) -> DesktopDocument:
    """The document with one window moved to the top of the stack, its order otherwise kept."""
    raised = window_by_tab_id(document, tab_id)
    if raised is None:
        raise WindowNotFoundError(f"no window carries the page {tab_id}")
    return _with_windows(
        document, [window for window in document.windows if window.tab_id != tab_id] + [raised]
    )


@pure
def add_window(
    document: DesktopDocument, address: Address, tab_id: TabId, rect: WindowRect | None
) -> DesktopDocument:
    """The document with a new window on top showing ``address``, cascaded when no rect is given."""
    placement = (
        cascade_rect(len(document.windows), desktop_size_of(document))
        if rect is None
        else _clamped_rect(rect, desktop_size_of(document))
    )
    window = DesktopWindow(tab_id=tab_id, address=address, rect=placement)
    return _with_windows(document, [*document.windows, window])


@pure
def focus_window(document: DesktopDocument, tab_id: TabId) -> DesktopDocument:
    """The document with one window raised to the top of the stack and brought back out of the dock."""
    raised = _raised(document, tab_id)
    window = window_by_tab_id(raised, tab_id)
    if window is None or not window.is_minimized:
        return raised
    return _replaced(raised, tab_id, window.model_copy_update(to_update(window.field_ref().is_minimized, False)))


@pure
def minimize_window(document: DesktopDocument, tab_id: TabId) -> DesktopDocument:
    """The document with one window put away: out of sight, its page loaded, its instance untouched.

    This is what an agent's ``close`` op means on a desktop. Stopping what a window shows is the
    separate ``stop`` verb, which the window's own close button is the gesture for.
    """
    window = window_by_tab_id(document, tab_id)
    if window is None:
        raise WindowNotFoundError(f"no window carries the page {tab_id}")
    return _replaced(document, tab_id, window.model_copy_update(to_update(window.field_ref().is_minimized, True)))


@pure
def remove_window(document: DesktopDocument, tab_id: TabId) -> DesktopDocument:
    """The document without one window. The instance behind it is not touched."""
    if window_by_tab_id(document, tab_id) is None:
        raise WindowNotFoundError(f"no window carries the page {tab_id}")
    return _with_windows(document, [window for window in document.windows if window.tab_id != tab_id])


@pure
def _restored_to(window: DesktopWindow, rect: WindowRect) -> DesktopWindow:
    """The window at ``rect``, out of the dock and out of maximized (a tiled window is neither)."""
    return window.model_copy_update(
        to_update(window.field_ref().rect, rect),
        to_update(window.field_ref().is_minimized, False),
        to_update(window.field_ref().is_maximized, False),
        to_update(window.field_ref().restore_rect, None),
    )


@pure
def split_beside(
    document: DesktopDocument,
    anchor_tab_id: TabId,
    address: Address,
    tab_id: TabId,
    direction: Direction,
    ratio: float,
) -> DesktopDocument:
    """The document with the anchor tiled to one side of the desktop and a new window filling the other.

    ``within`` has no side to take, so it opens the new window over the anchor's own rect, on top
    of it -- the same answer ``move --direction within`` gives.
    """
    anchor = window_by_tab_id(document, anchor_tab_id)
    if anchor is None:
        raise WindowNotFoundError(f"no window carries the page {anchor_tab_id}")
    if direction is Direction.WITHIN:
        return add_window(document, address, tab_id, anchor.rect)
    anchor_rect, placed_rect = tiled_rects(desktop_size_of(document), direction, ratio)
    tiled = _replaced(document, anchor_tab_id, _restored_to(anchor, anchor_rect))
    return add_window(tiled, address, tab_id, placed_rect)


@pure
def move_window_beside(
    document: DesktopDocument,
    tab_id: TabId,
    anchor_tab_id: TabId,
    direction: Direction,
    ratio: float,
) -> DesktopDocument:
    """The document with one open window snapped beside its anchor, its page untouched.

    The same tiling as a split, over a window that is already open: ``move`` never reloads what
    it moves. ``within`` gives the moved window the anchor's own rect and puts it directly above
    the anchor in the stack -- these two occupy one slot, and this one is on top.
    """
    if tab_id == anchor_tab_id:
        raise LayoutOpError("a window cannot be moved relative to itself")
    moved = window_by_tab_id(document, tab_id)
    anchor = window_by_tab_id(document, anchor_tab_id)
    if moved is None:
        raise WindowNotFoundError(f"no window carries the page {tab_id}")
    if anchor is None:
        raise WindowNotFoundError(f"no window carries the page {anchor_tab_id}")
    if direction is Direction.WITHIN:
        return _raised(_replaced(document, tab_id, _restored_to(moved, anchor.rect)), tab_id)
    anchor_rect, placed_rect = tiled_rects(desktop_size_of(document), direction, ratio)
    tiled = _replaced(document, anchor_tab_id, _restored_to(anchor, anchor_rect))
    return _raised(_replaced(tiled, tab_id, _restored_to(moved, placed_rect)), tab_id)


@pure
def rebind_tab_in_document(document: DesktopDocument, tab_id: TabId, address: Address) -> DesktopDocument:
    """The document with the window carrying ``tab_id`` pointed at another instance; the same object when none does.

    A window already showing the target closes: the rebound window is the one the user acted in,
    and one instance has one page.
    """
    window = window_by_tab_id(document, tab_id)
    if window is None or window.address == address:
        return document
    kept = [
        candidate
        for candidate in document.windows
        if candidate.tab_id == tab_id or candidate.address != address
    ]
    rebound = window.model_copy_update(to_update(window.field_ref().address, address))
    return _with_windows(document, [rebound if candidate.tab_id == tab_id else candidate for candidate in kept])


@pure
def without_addresses(document: DesktopDocument, addresses: Sequence[Address]) -> DesktopDocument:
    """The document without the windows showing any of ``addresses``; the same object when it shows none."""
    doomed = set(addresses)
    kept = [window for window in document.windows if window.address not in doomed]
    if len(kept) == len(document.windows):
        return document
    return _with_windows(document, kept)


# ---------- reading a dockview arrangement ----------

# CLEANUP: drop ``desktop_from_dockview_document`` and the ``dockview`` branch of the layout
# validators that call it once every workspace has saved a desktop layout -- that is, one full
# release after the desktop shell shipped, when no client or seed file can still hold a grid.


@pure
def _instance_panels_of(dockview: dict[str, Any]) -> list[dict[str, Any]]:
    """Each panel of a serialized dockview grid that showed an instance, in the file's own order.

    A New Tab launcher panel named no instance and has no desktop counterpart: the desktop, its
    ``+`` menu and its Make something window are what replaced that page, so it is dropped.
    """
    panels = dockview.get("panels")
    if not isinstance(panels, dict):
        return []
    found: list[dict[str, Any]] = []
    for entry in panels.values():
        if not isinstance(entry, dict):
            continue
        params = entry.get("params")
        if not isinstance(params, dict) or params.get("kind") != "instance":
            continue
        found.append(params)
    return found


@pure
def desktop_from_dockview_document(dockview: dict[str, Any]) -> DesktopDocument:
    """The desktop a workspace's saved dockview arrangement becomes: one cascaded window per docked tab.

    Stacking follows how recently each tab was looked at, so the tab the user left active comes
    back on top, and a panel whose params are unreadable is dropped rather than costing the whole
    arrangement. An arrangement that docked one instance in two panes becomes one window, since
    the instance has one page either way.
    """
    window_by_address: dict[Address, DesktopWindow] = {}
    ordered = sorted(_instance_panels_of(dockview), key=lambda params: params.get("lastFocusedMs") or 0)
    for params in ordered:
        raw_address = params.get("address")
        raw_tab_id = params.get("tabId")
        if not isinstance(raw_address, str) or not isinstance(raw_tab_id, str):
            continue
        try:
            address = Address(raw_address)
            tab_id = TabId(raw_tab_id)
        except ValueError:
            continue
        window_by_address[address] = DesktopWindow(
            tab_id=tab_id,
            address=address,
            rect=cascade_rect(len(window_by_address), NOMINAL_DESKTOP_SIZE),
            last_focused_ms=params.get("lastFocusedMs") or 0,
            last_known_title=_panel_title(params),
        )
    return DesktopDocument(windows=tuple(window_by_address.values()))


def _panel_title(params: dict[str, Any]) -> str | None:
    """What a dockview panel was called: the name the user gave it, else the one its app gave it.

    Carried over so a migrated window whose instance has since gone still knows what it was
    called, rather than falling back to its app's name on the first reload after the migration.
    """
    for key in ("customTitle", "title"):
        value = params.get(key)
        if isinstance(value, str) and value != "":
            return value
    return None
