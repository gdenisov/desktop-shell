from typing import Any
from typing import Final

from app_instances.data_types import InstanceLifetime
from app_instances.data_types import InstanceRecord
from app_instances.data_types import InstanceStatus
from app_instances.primitives import InstanceKey
from app_manifest.manifest import DefaultShortcut
from app_manifest.manifest import ShortcutMode
from app_manifest.primitives import ActionId
from app_manifest.primitives import AppName
from app_manifest.registry import RegistryAction
from app_manifest.registry import RegistryRow
from loguru import logger
from pydantic import AwareDatetime
from pydantic import Field
from pydantic import model_validator

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.primitives import NonEmptyStr
from imbue.imbue_common.pure import pure
from imbue.system_interface.shell.desktop_document import DesktopDocument
from imbue.system_interface.shell.desktop_document import DesktopWindow
from imbue.system_interface.shell.desktop_document import EMPTY_DESKTOP
from imbue.system_interface.shell.desktop_document import desktop_from_dockview_document
from imbue.system_interface.shell.primitives import Address
from imbue.system_interface.shell.primitives import ClientActivityKind
from imbue.system_interface.shell.primitives import ClientId
from imbue.system_interface.shell.primitives import DeviceKind
from imbue.system_interface.shell.primitives import ProjectId
from imbue.system_interface.shell.primitives import SaveId
from imbue.system_interface.shell.primitives import ViewId
from imbue.system_interface.shell.primitives import address_for


class Shortcut(FrozenModel):
    """One rail entry of a project: an app's action, in focus or new mode."""

    app: AppName = Field(description="The registered app")
    action: ActionId = Field(description="The action the row runs")
    mode: ShortcutMode = Field(description="Focus the app's most recent tab first, or always run the action")


class Project(FrozenModel):
    """A named view: its display metadata, its shared tab set, and its shortcuts (contracts.md section 6)."""

    id: ProjectId = Field(description="The slugified name, stable across renames")
    name: str = Field(description="Free-form name shown in the UI")
    color: str = Field(description="Accent color as a '#RRGGBB' string")
    glyph: int = Field(description="Index into the frontend's squiggle glyph table")
    tabs: tuple[Address, ...] = Field(description="Every instance the project shows, in the order added")
    shortcuts: tuple[Shortcut, ...] = Field(description="The rail rows, in rail order")


# CLEANUP: drop everything under this heading -- the two folds and the ``_as_desktop_layout`` validators on
# ``LayoutRecord`` and ``LayoutSaveRequest`` that call them, plus the tests of the older shapes -- once every
# workspace has saved a desktop layout. Two older shapes exist: a file from before the workspace app model
# carried a ``tabs`` block beside the dockview document, which was the truth of each panel's identity, and a
# file from before the desktop shell carried the dockview grid itself.

# The ``kind`` the ``params`` of a dockview panel showing an instance carried. A launcher panel (the New Tab
# page) carried another kind and named no instance.
INSTANCE_PANEL_KIND: Final[str] = "instance"


@pure
def fold_legacy_tabs_into_dockview(data: Any) -> Any:
    """A layout body in the oldest shape, with its ``tabs`` block folded into each panel's ``params``; any other value unchanged."""
    if not isinstance(data, dict) or "tabs" not in data:
        return data
    without_tabs = {key: value for key, value in data.items() if key != "tabs"}
    tabs = data["tabs"]
    dockview = without_tabs.get("dockview")
    if not isinstance(tabs, dict) or not isinstance(dockview, dict) or not isinstance(dockview.get("panels"), dict):
        return without_tabs
    panels = dict(dockview["panels"])
    for panel_id, tab in tabs.items():
        entry = panels.get(panel_id)
        if not isinstance(tab, dict) or not isinstance(entry, dict):
            continue
        panels[panel_id] = {
            **entry,
            "params": {
                "kind": INSTANCE_PANEL_KIND,
                "address": tab.get("address"),
                "tabId": tab.get("tab_id"),
                "lastFocusedMs": tab.get("last_focused_ms", 0),
            },
        }
    return {**without_tabs, "dockview": {**dockview, "panels": panels}}


def fold_dockview_into_desktop(data: Any) -> Any:
    """A layout body that still carries a dockview grid, as the desktop that grid becomes (contracts.md section 6).

    Each docked tab becomes a cascaded window; the grid itself is dropped, so the arrangement is
    rewritten in the new shape the next time the client saves.
    """
    if not isinstance(data, dict) or "dockview" not in data:
        return data
    without_dockview = {key: value for key, value in data.items() if key != "dockview"}
    dockview = data["dockview"]
    if without_dockview.get("desktop") is not None:
        return without_dockview
    # A file that was saved with no arrangement at all carries a desktop that has never been
    # arranged either, rather than no key for one.
    if not isinstance(dockview, dict):
        return {**without_dockview, "desktop": None}
    migrated = desktop_from_dockview_document(dockview)
    logger.info("Read a saved dockview arrangement as a desktop of {} window(s)", len(migrated.windows))
    return {**without_dockview, "desktop": migrated.model_dump(mode="json")}


@pure
def as_desktop_layout_body(data: Any) -> Any:
    """A layout body in any shape this shell has ever written, as one carrying a desktop document."""
    return fold_dockview_into_desktop(fold_legacy_tabs_into_dockview(data))


class LayoutRecord(FrozenModel):
    """One client's desktop for one view (contracts.md section 6): its windows, its icons, and its dock."""

    desktop: DesktopDocument | None = Field(description="The client's desktop, None for a view it has never arranged")
    device_kind: DeviceKind = Field(description="The device kind the arrangement was made on")
    updated_at: AwareDatetime | None = Field(
        description="When the arrangement was last saved, None for the empty layout"
    )

    @model_validator(mode="before")
    @classmethod
    def _as_desktop_layout(cls, data: Any) -> Any:
        return as_desktop_layout_body(data)


@pure
def windows_of(layout: LayoutRecord) -> tuple[DesktopWindow, ...]:
    """Every window the layout holds, bottom of the stack first; empty for a view never arranged."""
    return () if layout.desktop is None else layout.desktop.windows


@pure
def desktop_of(layout: LayoutRecord) -> DesktopDocument:
    """The layout's desktop, or the empty one: an edit of a never-arranged view starts from a bare desktop."""
    return layout.desktop if layout.desktop is not None else EMPTY_DESKTOP


class ClientRecord(FrozenModel):
    """What the shell keeps about one browser context (contracts.md section 7)."""

    id: ClientId = Field(description="The client's stored id")
    device_kind: DeviceKind = Field(description="Desktop or mobile")
    active_view: ViewId = Field(description="The view the client is on")
    last_seen: AwareDatetime = Field(description="When the client last reported")


class InventoryInstance(FrozenModel):
    """One instance as the shell lists it: the app's record plus its address; the synthesized record of a single-instance app has an empty key."""

    key: str = Field(description="The app-scoped key; empty for a single-instance app's one record")
    url: str = Field(description="Where the instance's page is, as a path under the app's origin")
    title: str = Field(description="What users see")
    status: InstanceStatus = Field(description="What the instance is doing")
    lifetime: InstanceLifetime = Field(description="Whether it lives until deleted or only while referenced")
    last_active: AwareDatetime | None = Field(description="When it was last active, None when unknown")
    renameable: bool = Field(description="Whether the rename route is accepted")
    stoppable: bool = Field(description="Whether the stop and start routes are accepted")
    labels: dict[str, str] = Field(default_factory=dict, description="The app's free-form string facts about it")

    @pure
    def address(self, app: AppName) -> Address:
        return address_for(app, None if self.key == "" else InstanceKey(self.key))


@pure
def inventory_instance_from_record(record: InstanceRecord) -> InventoryInstance:
    return InventoryInstance(
        key=str(record.key),
        url=str(record.url),
        title=str(record.title),
        status=record.status,
        lifetime=record.lifetime,
        last_active=record.last_active,
        renameable=record.renameable,
        stoppable=record.stoppable,
        labels=dict(record.labels),
    )


@pure
def synthesized_single_instance(row: RegistryRow, is_running: bool) -> InventoryInstance:
    """The one record a single-instance app carries (contracts.md section 8)."""
    return InventoryInstance(
        key="",
        url="/",
        title=str(row.display_name) if row.display_name is not None else str(row.name),
        status=InstanceStatus.IDLE if is_running else InstanceStatus.STOPPED,
        lifetime=InstanceLifetime.EXPLICIT,
        last_active=None,
        # The app-level Stop and Start are the single-instance app's; its one record has none of its own.
        renameable=False,
        stoppable=False,
    )


class AppInventoryEntry(FrozenModel):
    """One app of the inventory: its registry row, whether it runs, and its instances as last fetched."""

    row: RegistryRow = Field(description="The registry row, validated on read")
    is_running: bool = Field(description="Derived from supervisord or a TCP probe, never stored")
    instances: tuple[InventoryInstance, ...] = Field(description="The app's instances, in the app's list order")
    # False until the app's instances API has answered a list once (a single-instance app's one
    # record is synthesized, so it counts as listed): an empty list that was never fetched is
    # not evidence that an address is missing, and a client shows nothing as unavailable on it.
    is_listed: bool = Field(description="Whether the instance list is the app's own answer rather than the seed")
    # A record the shell has held for less than the grace period is not deleted for being
    # unreferenced: the create that made it has returned but the tab docking it may not have
    # been saved yet.
    first_seen_at_by_key: dict[str, float] = Field(
        description="Monotonic seconds each key was first listed, for the referenced-deletion grace"
    )

    @pure
    def address_of(self, instance: InventoryInstance) -> Address:
        return instance.address(self.row.name)

    @pure
    def addresses(self) -> list[Address]:
        return [self.address_of(instance) for instance in self.instances]


@pure
def app_wire_json(entry: AppInventoryEntry) -> dict[str, Any]:
    """The ``app`` object of contracts.md section 8."""
    row = entry.row
    return {
        "name": str(row.name),
        "display_name": str(row.display_name) if row.display_name is not None else str(row.name),
        "icon": row.icon or "",
        "label": row.label,
        "url": str(row.url),
        "internal": row.internal,
        "program": row.program or "",
        "critical": row.critical,
        "instances_url": instances_url_of(row),
        "has_instances": row.instances,
        # The app lists its own instances inside its window, so the desktop leaves them to it:
        # the dock carries the app's windows rather than one tile per instance (contracts.md
        # section 6), and the instances are reached from inside the app and from search.
        "browses_instances": row.browses_instances,
        "actions": [action_wire_json(action) for action in effective_actions(row)],
        "default_shortcut": default_shortcut_wire_json(row.default_shortcut),
        "launcher_rank": row.launcher_rank,
        "is_running": entry.is_running,
        "is_listed": entry.is_listed,
        "instances": [instance.model_dump(mode="json") for instance in entry.instances],
    }


@pure
def instances_url_of(row: RegistryRow) -> str:
    """Where the app's instances API is reached: its ``instances_url``, else its ``url`` (contracts.md section 3)."""
    return str(row.instances_url) if row.instances_url is not None else str(row.url)


@pure
def action_wire_json(action: RegistryAction) -> dict[str, Any]:
    return {"id": str(action.id), "label": str(action.label), "params": [str(param) for param in action.params]}


@pure
def default_shortcut_wire_json(shortcut: DefaultShortcut | None) -> dict[str, str] | None:
    if shortcut is None:
        return None
    return {"action": str(shortcut.action), "mode": shortcut.mode.value}


# The one action every single-instance app has, synthesized by the shell (contracts.md section 2).
OPEN_ACTION: RegistryAction = RegistryAction(id=ActionId("open"), label=NonEmptyStr("Open"))


@pure
def effective_actions(row: RegistryRow) -> tuple[RegistryAction, ...]:
    """The actions an app offers: its declared ones, or the synthesized ``open`` for a single-instance app."""
    if row.instances:
        return row.actions
    display = str(row.display_name) if row.display_name is not None else str(row.name)
    return (RegistryAction(id=OPEN_ACTION.id, label=NonEmptyStr(f"Open {display}")),)


class ClientStateReport(FrozenModel):
    """The inbound ``client_state`` WebSocket message (contracts.md section 8)."""

    client_id: ClientId = Field(description="The reporting client")
    device_kind: DeviceKind = Field(description="Desktop or mobile")
    active_view: ViewId = Field(description="The view the client is on now")
    previous_view: str = Field(default="", description="The view it was on before, empty on connect")


class ClientActivityReport(FrozenModel):
    """The body of ``POST /api/client-activity`` (contracts.md section 5)."""

    client_id: ClientId = Field(description="The client the activity belongs to")
    device_kind: DeviceKind = Field(description="Desktop or mobile")
    view_id: ViewId = Field(description="The view the client was on")
    kind: ClientActivityKind = Field(description="A message sent to an instance, or a view switch")
    app: str = Field(default="", description="The app a message went to")
    key: str = Field(default="", description="The instance key a message went to")
    text: str = Field(default="", description="The message text, truncated at write time")
    from_view_id: str = Field(default="", description="For a view switch, the view left")


class TabInstanceReport(FrozenModel):
    """The body of ``POST /api/tabs/<tab_id>/instance`` (contracts.md section 5)."""

    app: AppName = Field(description="The app that owns the tab's instance")
    key: str = Field(description="The key the tab now shows")


class LayoutSaveRequest(FrozenModel):
    """The body of ``POST /api/layouts/<view_id>`` (contracts.md section 6)."""

    client_id: ClientId = Field(description="The saving client")
    save_id: SaveId = Field(description="The save id the window minted, echoed in the layout_updated broadcast")
    base_updated_at: AwareDatetime | None = Field(
        default=None,
        description="The updated_at of the arrangement the window last fetched or saved; None for one it only saw empty",
    )
    device_kind: DeviceKind = Field(description="The device kind the arrangement was made on")
    desktop: DesktopDocument | None = Field(description="The desktop as the window has it now")

    @model_validator(mode="before")
    @classmethod
    def _as_desktop_layout(cls, data: Any) -> Any:
        return as_desktop_layout_body(data)


class ClientReportOutcome(FrozenModel):
    """What recording a ``client_state`` report came to: the record, and whether its active view moved."""

    record: ClientRecord = Field(description="The client record as written")
    is_active_view_changed: bool = Field(description="Whether the stored active view differs from before the report")


class LayoutEditOutcome(FrozenModel):
    """What editing a client's layout under the state lock came to: the arrangement now in force, and whether it was written."""

    layout: LayoutRecord = Field(description="The arrangement after the edit, stamped when it was written")
    is_written: bool = Field(
        description="Whether the edit changed the arrangement and was written to the client's file"
    )
