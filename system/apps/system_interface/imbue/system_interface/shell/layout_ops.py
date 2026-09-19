"""Server-side support for the agent-driven layout surface, over addresses.

``system/scripts/layout.py`` posts ``{op, args, requester}`` to ``POST /api/layout/broadcast``
(``routes.py``): the read ops (``inspect``, ``context``) are answered from the state files and the
client-activity log, ``load`` switches a client's view, the document ops are applied by the shell to
the target client's desktop (``desktop_document.py``), and the transient ops are sent to that
client's windows. The script's ``list`` and ``views`` read ``GET /api/inventory`` instead. This module
holds the op tables, the op arguments, and the pure summary ``inspect`` answers with.
"""

from collections.abc import Mapping
from typing import Any
from typing import Final

from pydantic import Field

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.pure import pure
from imbue.system_interface.shell.data_types import LayoutRecord
from imbue.system_interface.shell.data_types import windows_of
from imbue.system_interface.shell.desktop_document import DEFAULT_SPLIT_RATIO
from imbue.system_interface.shell.desktop_document import Direction
from imbue.system_interface.shell.desktop_document import desktop_size_of

# The ops the endpoint dispatches on. Anything else is a 400.
READ_OPS: Final[frozenset[str]] = frozenset({"inspect", "context"})
LOAD_OP: Final[str] = "load"
# Ops the shell applies to the target client's layout file (the file is the truth of the arrangement).
DOCUMENT_OPS: Final[frozenset[str]] = frozenset({"open", "focus", "split", "close", "move"})
# Ops that change what is on screen without changing the saved document: they alone reach the
# browser as a ``layout_op`` message.
TRANSIENT_OPS: Final[frozenset[str]] = frozenset({"maximize", "restore", "refresh", "reload_system_interface"})
KNOWN_OPS: Final[frozenset[str]] = READ_OPS | {LOAD_OP} | DOCUMENT_OPS | TRANSIENT_OPS

# Ops that name an instance or an app in ``args.address``.
ADDRESSED_OPS: Final[frozenset[str]] = frozenset({"open", "focus", "split", "close", "move", "maximize", "refresh"})

# Ops that dock a panel, and may therefore create the instance it shows.
CREATING_OPS: Final[frozenset[str]] = frozenset({"open", "split"})

# The one non-address an addressed op accepts: the requester's own instance, which the op's
# ``requester`` names.
SELF_ADDRESS: Final[str] = "self"


@pure
def is_known_op(op: str) -> bool:
    return op in KNOWN_OPS


@pure
def is_document_op(op: str) -> bool:
    return op in DOCUMENT_OPS


@pure
def is_transient_op(op: str) -> bool:
    return op in TRANSIENT_OPS


@pure
def is_addressed_op(op: str) -> bool:
    return op in ADDRESSED_OPS


@pure
def is_creating_op(op: str) -> bool:
    return op in CREATING_OPS


class DocumentOpArguments(FrozenModel):
    """The arguments of a document op, as ``layout.py`` posts them (contracts.md section 12)."""

    address: str = Field(
        default="", description="The instance or app the op names; ``self`` for the requester's own instance"
    )
    relative_to: str = Field(default=SELF_ADDRESS, description="The anchor of a split or a move")
    direction: Direction = Field(default=Direction.RIGHT, description="Which side of the anchor a split or a move takes")
    ratio: float = Field(default=DEFAULT_SPLIT_RATIO, description="The share of the desktop the placed window takes")
    # Kept so an older ``layout.py`` and every existing caller still parse. A desktop has no
    # groups to open a panel into, so a tiling gesture is what a split is either way.
    new_group: bool = Field(default=False, description="Accepted and ignored: the desktop has no tab groups")
    action: str = Field(default="", description="The action a create runs; empty for the app's primary action")
    params: dict[str, str] = Field(default_factory=dict, description="The create's params")


@pure
def layout_inspect(layout: LayoutRecord | None, title_by_address: Mapping[str, str]) -> dict[str, Any]:
    """A client's desktop as the ``inspect`` op reports it: every window, bottom of the stack first.

    A window whose instance no longer appears in any app's list keeps its window and is reported
    under the title it last had, so an agent reading this sees what the user sees rather than a
    nameless row.
    """
    if layout is None or layout.desktop is None:
        return {"desktop_size": None, "windows": []}
    desktop_size = desktop_size_of(layout.desktop)
    return {
        "desktop_size": desktop_size.model_dump(mode="json"),
        "windows": [
            {
                "address": str(window.address),
                "tab_id": str(window.tab_id),
                "title": title_by_address.get(str(window.address)) or window.last_known_title,
                "rect": window.rect.model_dump(mode="json"),
                "is_minimized": window.is_minimized,
                "is_maximized": window.is_maximized,
                # The list is the stacking order, so the last window named is the one on top.
                "is_on_top": index == len(windows_of(layout)) - 1,
            }
            for index, window in enumerate(windows_of(layout))
        ],
    }
