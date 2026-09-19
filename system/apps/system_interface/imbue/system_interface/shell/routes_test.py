"""Tests for the shell's HTTP routes (contracts.md sections 5 and 6) over a test state with a fake-fed inventory."""

import queue
from pathlib import Path
from typing import Any

import pytest
from app_instances.data_types import InstanceStatus
from app_instances.testing import StubInstanceSource
from flask import Flask
from flask.testing import FlaskClient

from imbue.system_interface.app_context import state_of
from imbue.system_interface.shell.data_types import ClientStateReport
from imbue.system_interface.shell.inventory import HttpInstanceFetcher
from imbue.system_interface.shell.liveness import probe_all_app_liveness
from imbue.system_interface.shell.primitives import Address
from imbue.system_interface.shell.primitives import ClientId
from imbue.system_interface.shell.primitives import DeviceKind
from imbue.system_interface.shell.primitives import TabId
from imbue.system_interface.shell.primitives import ViewId
from imbue.system_interface.shell.routes import _resolve_client
from imbue.system_interface.shell.state import ShellState
from imbue.system_interface.shell.testing import FakeInstanceFetcher
from imbue.system_interface.shell.testing import TEST_NOW
from imbue.system_interface.shell.testing import TEST_TERMINAL_URL
from imbue.system_interface.shell.testing import addresses_of_layout
from imbue.system_interface.shell.testing import desktop_showing
from imbue.system_interface.shell.testing import build_inventory
from imbue.system_interface.shell.testing import drain_messages
from imbue.system_interface.shell.testing import instance_record
from imbue.system_interface.shell.testing import layout_showing
from imbue.system_interface.shell.testing import registry_row_toml
from imbue.system_interface.shell.testing import shell_application
from imbue.system_interface.shell.testing import write_registry
from imbue.system_interface.shell.testing import write_two_app_registry
from imbue.system_interface.testing import FakeSupervisorServer
from imbue.system_interface.ws_broadcaster import WebSocketBroadcaster

_TERMINAL_1 = Address("app:terminal?instance=terminal-1")
_TERMINAL_2 = Address("app:terminal?instance=terminal-2")
_FILES = Address("app:files")
_TAB = TabId("tab-000000000000000a")
_NOT_LOOPBACK = {"REMOTE_ADDR": "10.0.0.7"}


def _shell(app: Flask) -> ShellState:
    return state_of(app).shell


def _register_client(app: Flask, client_id: str, view_id: str) -> "queue.Queue[str | None]":
    """A connected window of ``client_id`` on ``view_id``, recorded the way its ``client_state`` report would record it."""
    client_queue = _shell(app).broadcaster.register()
    _shell(app).broadcaster.set_client_info(client_queue, client_id, view_id, "desktop")
    _shell(app).clients.record_report(
        ClientStateReport(
            client_id=ClientId(client_id),
            device_kind=DeviceKind.DESKTOP,
            active_view=ViewId(view_id),
        ),
        TEST_NOW,
    )
    return client_queue


def _record_client(app: Flask, client_id: str, view_id: str) -> None:
    """A client the shell knows from an earlier visit, with no window open now."""
    _shell(app).clients.record_report(
        ClientStateReport(
            client_id=ClientId(client_id),
            device_kind=DeviceKind.DESKTOP,
            active_view=ViewId(view_id),
        ),
        TEST_NOW,
    )


def _window_addresses(layout: dict[str, Any]) -> list[str]:
    """The addresses an op's answer reports, bottom of the stack first."""
    return [window["address"] for window in layout["windows"]]


def _saved_desktop(*addresses: Address) -> dict[str, Any]:
    """A desktop as a window posts it: one plain window per address, in stacking order."""
    return desktop_showing(*addresses).model_dump(mode="json")


# ---------- section 5 ----------


def test_an_app_nudge_is_accepted_from_loopback_only(client: FlaskClient, app: Flask) -> None:
    try:
        assert client.post("/api/apps/terminal/changed").status_code == 204
        assert client.post("/api/apps/unknown/changed").status_code == 404
        assert client.post("/api/apps/terminal/changed", environ_base=_NOT_LOOPBACK).status_code == 403
    finally:
        _shell(app).inventory.stop()


def test_a_tab_report_rebinds_the_tab_everywhere_and_files_it_in_the_project(
    client: FlaskClient, app: Flask, fetcher: FakeInstanceFetcher
) -> None:
    shell = _shell(app)
    shell.projects.create_project("Alpha", "#111111", 0, ())
    shell.layouts.save_browser_layout("alpha", "c1", layout_showing(_TERMINAL_1), None, TEST_NOW)
    shell.layouts.save_browser_layout("everything", "c2", layout_showing(_TERMINAL_1), None, TEST_NOW)
    fetcher.list(TEST_TERMINAL_URL, instance_record("terminal-1"), instance_record("terminal-2"))
    client_queue = _register_client(app, "c1", "alpha")

    response = client.post(
        "/api/tabs/tab-0000000000000000/instance",
        json={"app": "terminal", "key": "terminal-2"},
    )

    assert response.status_code == 204
    alpha = shell.layouts.read_layout("alpha", "c1", DeviceKind.DESKTOP)
    everything = shell.layouts.read_layout("everything", "c2", DeviceKind.DESKTOP)
    assert addresses_of_layout(alpha) == [_TERMINAL_2]
    assert addresses_of_layout(everything) == [_TERMINAL_2]
    assert shell.projects.get_project("alpha").tabs == (_TERMINAL_2,)
    messages = drain_messages(client_queue)
    rebound = [message for message in messages if message["type"] == "tab_rebound"]
    assert {(message["client_id"], message["view_id"]) for message in rebound} == {
        ("c1", "alpha"),
        ("c2", "everything"),
    }
    assert rebound[0]["address"] == str(_TERMINAL_2) and rebound[0]["tab_id"] == "tab-0000000000000000"
    # The refetched list lands BEFORE the rebind: a window pointed at an instance it has not heard
    # of yet would drop its page and reload it when the list arrived (a chat created and shown in
    # the same window did exactly that).
    types = [message["type"] for message in messages]
    assert types.index("apps_updated") < types.index("tab_rebound")
    assert shell.inventory.find_instance(_TERMINAL_2) is not None


def test_a_tab_report_is_refused_when_it_names_no_tab_or_the_wrong_app(client: FlaskClient, app: Flask) -> None:
    _shell(app).layouts.save_browser_layout("everything", "c1", layout_showing(_FILES), None, TEST_NOW)
    assert (
        client.post(
            "/api/tabs/tab-00000000000000ff/instance",
            json={"app": "terminal", "key": "k"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/tabs/tab-0000000000000000/instance",
            json={"app": "terminal", "key": "k"},
        ).status_code
        == 400
    )
    assert client.post("/api/tabs/nope/instance", json={"app": "terminal", "key": "k"}).status_code == 400
    assert client.post("/api/tabs/tab-0000000000000000/instance", json={"app": "terminal"}).status_code == 400
    assert (
        client.post(
            "/api/tabs/tab-0000000000000000/instance",
            json={"app": "files", "key": ""},
            environ_base=_NOT_LOOPBACK,
        ).status_code
        == 403
    )


def test_client_activity_is_appended_by_kind(client: FlaskClient, app: Flask) -> None:
    base = {"client_id": "c1", "device_kind": "desktop", "view_id": "everything"}
    assert (
        client.post(
            "/api/client-activity",
            json={
                **base,
                "kind": "message",
                "app": "chat",
                "key": "agent-1",
                "text": "hi",
            },
        ).status_code
        == 204
    )
    assert (
        client.post(
            "/api/client-activity",
            json={**base, "kind": "view_switch", "from_view_id": "alpha"},
        ).status_code
        == 204
    )
    assert client.post("/api/client-activity", json={**base, "kind": "nope"}).status_code == 400
    assert (
        client.post(
            "/api/client-activity",
            json={**base, "kind": "message"},
            environ_base=_NOT_LOOPBACK,
        ).status_code
        == 403
    )
    events = _shell(app).activity.read_events()
    assert [(event["type"], event["client_id"]) for event in events] == [
        ("message", "c1"),
        ("view_switch", "c1"),
    ]
    assert events[0]["key"] == "agent-1" and events[1]["from_view_id"] == "alpha"


# ---------- section 6: the relay ----------


def test_instance_verbs_are_relayed_and_the_list_refetched(
    tmp_path: Path,
    broadcaster: WebSocketBroadcaster,
    stub_source: StubInstanceSource,
    stub_app_url: str,
) -> None:
    stub_source.records.append(instance_record("stub-1"))
    inventory = build_inventory(
        write_registry(
            tmp_path / "apps.toml",
            registry_row_toml("stub", stub_app_url, True, actions=[("new", "New")]),
        ),
        broadcaster,
        fetcher=HttpInstanceFetcher(),
    )
    inventory.refetch_now("stub")
    client = shell_application(tmp_path, inventory, broadcaster).test_client()

    created = client.post("/api/apps/stub/instances", json={"action": "new", "params": {}})
    assert created.status_code == 201 and created.get_json()["instance"]["key"] == "stub-2"
    assert inventory.find_instance(Address("app:stub?instance=stub-2")) is not None
    renamed = client.post("/api/apps/stub/instances/stub-2/rename", json={"title": "Renamed"})
    assert renamed.status_code == 200
    found = inventory.find_instance(Address("app:stub?instance=stub-2"))
    assert found is not None and found[1].title == "Renamed"
    assert client.post("/api/apps/stub/instances/stub-2/location", json={"path": "/deeper"}).status_code == 200
    # Stop and start pass the app's answer through and refetch on success, like every other verb.
    assert client.post("/api/apps/stub/instances/stub-2/stop").status_code == 400
    stub_source.is_stoppable = True
    assert client.post("/api/apps/stub/instances/stub-2/stop").status_code == 200
    found_stopped = inventory.find_instance(Address("app:stub?instance=stub-2"))
    assert found_stopped is not None and found_stopped[1].status == InstanceStatus.STOPPED
    assert client.post("/api/apps/stub/instances/stub-2/start").status_code == 200
    found_started = inventory.find_instance(Address("app:stub?instance=stub-2"))
    assert found_started is not None and found_started[1].status == InstanceStatus.IDLE
    assert client.post("/api/apps/stub/instances/stub-9/start").status_code == 404
    assert client.post("/api/apps/stub/instances/stub-2/delete").status_code == 204
    assert inventory.find_instance(Address("app:stub?instance=stub-2")) is None
    assert client.post("/api/apps/stub/instances/stub-9/rename", json={"title": "x"}).status_code == 404
    assert client.post("/api/apps/unknown/instances", json={"action": "new", "params": {}}).status_code == 404
    # A key that fails the key rule is refused by the shell, before the app (which would say 404) is asked.
    assert client.post("/api/apps/stub/instances/-not-a-key/rename", json={"title": "x"}).status_code == 400


def test_a_relayed_delete_drops_the_instance_from_every_tab_set_and_layout(
    tmp_path: Path,
    broadcaster: WebSocketBroadcaster,
    stub_source: StubInstanceSource,
    stub_app_url: str,
) -> None:
    stub_1 = Address("app:stub?instance=stub-1")
    stub_2 = Address("app:stub?instance=stub-2")
    stub_source.records.extend([instance_record("stub-1"), instance_record("stub-2")])
    inventory = build_inventory(
        write_registry(tmp_path / "apps.toml", registry_row_toml("stub", stub_app_url, True)),
        broadcaster,
        fetcher=HttpInstanceFetcher(),
    )
    inventory.refetch_now("stub")
    app = shell_application(tmp_path, inventory, broadcaster)
    shell = _shell(app)
    shell.projects.create_project("Alpha", "#111111", 0, ())
    shell.projects.add_tab("alpha", stub_1)
    shell.projects.add_tab("alpha", stub_2)
    shell.layouts.save_browser_layout("alpha", "c1", layout_showing(stub_1, stub_2), None, TEST_NOW)
    client_queue = broadcaster.register()

    assert app.test_client().post("/api/apps/stub/instances/stub-1/delete").status_code == 204

    assert shell.projects.get_project("alpha").tabs == (stub_2,)
    remaining = shell.layouts.read_layout("alpha", "c1", DeviceKind.DESKTOP)
    assert addresses_of_layout(remaining) == [stub_2]
    types = [message["type"] for message in drain_messages(client_queue)]
    assert "projects_updated" in types and "layout_updated" in types


def test_a_refused_delete_keeps_the_instance_in_its_tab_sets(
    tmp_path: Path,
    broadcaster: WebSocketBroadcaster,
    stub_source: StubInstanceSource,
    stub_app_url: str,
) -> None:
    stub_1 = Address("app:stub?instance=stub-1")
    stub_source.records.append(instance_record("stub-1"))
    inventory = build_inventory(
        write_registry(tmp_path / "apps.toml", registry_row_toml("stub", stub_app_url, True)),
        broadcaster,
        fetcher=HttpInstanceFetcher(),
    )
    inventory.refetch_now("stub")
    app = shell_application(tmp_path, inventory, broadcaster)
    shell = _shell(app)
    shell.projects.create_project("Alpha", "#111111", 0, ())
    shell.projects.add_tab("alpha", stub_1)

    stub_source.is_ready = False

    assert app.test_client().post("/api/apps/stub/instances/stub-1/delete").status_code >= 400

    assert shell.projects.get_project("alpha").tabs == (stub_1,)


# ---------- section 6: stop and start ----------


def test_stop_and_start_drive_the_supervised_program(
    tmp_path: Path,
    broadcaster: WebSocketBroadcaster,
    fake_supervisor: FakeSupervisorServer,
) -> None:
    fake_supervisor.statename_by_program["files"] = "RUNNING"
    fake_supervisor.statename_by_program["system_interface"] = "RUNNING"
    registry_path = write_two_app_registry(
        tmp_path,
        registry_row_toml(
            "system_interface",
            "http://localhost:8000",
            program="system_interface",
            is_critical=True,
        ),
        registry_row_toml("chat", "http://localhost:8000", True, program="system_interface"),
        registry_row_toml(
            "plain",
            "http://localhost:1",
        ),
    )
    inventory = build_inventory(registry_path, broadcaster, prober=probe_all_app_liveness)
    client = shell_application(tmp_path, inventory, broadcaster).test_client()

    stopped = client.post("/api/apps/files/stop")
    assert stopped.status_code == 200 and stopped.get_json() == {
        "name": "files",
        "is_running": False,
    }
    assert fake_supervisor.statename_by_program["files"] == "STOPPED"
    started = client.post("/api/apps/files/start")
    assert started.status_code == 200 and started.get_json() == {
        "name": "files",
        "is_running": True,
    }

    assert client.post("/api/apps/system_interface/stop").status_code == 400
    assert client.post("/api/apps/chat/stop").status_code == 400
    assert client.post("/api/apps/plain/stop").status_code == 400
    assert client.post("/api/apps/unknown/stop").status_code == 404


def test_an_unreachable_supervisord_is_a_502(
    tmp_path: Path, broadcaster: WebSocketBroadcaster, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MINDS_SUPERVISOR_SOCKET", str(tmp_path / "missing.sock"))
    client = shell_application(
        tmp_path,
        build_inventory(write_two_app_registry(tmp_path), broadcaster),
        broadcaster,
    ).test_client()
    assert client.post("/api/apps/files/stop").status_code == 502


# ---------- section 6: projects ----------


def test_projects_are_created_seeded_and_listed(client: FlaskClient, app: Flask) -> None:
    client_queue = _register_client(app, "c1", "everything")
    created = client.post("/api/projects", json={"name": "Research", "color": "#12B5A5", "glyph": 4})
    assert created.status_code == 201
    assert created.get_json() == {
        "id": "research",
        "name": "Research",
        "color": "#12B5A5",
        "glyph": 4,
        "tabs": [],
        "shortcuts": [
            {"app": "terminal", "action": "new", "mode": "new"},
            {"app": "files", "action": "open", "mode": "focus"},
        ],
    }
    assert client.get("/api/projects").get_json()["projects"][0]["id"] == "research"
    assert client.post("/api/projects", json={"name": "research!", "color": "#12B5A5", "glyph": 4}).status_code == 409
    assert client.post("/api/projects", json={"name": "Bad", "color": "red", "glyph": 4}).status_code == 400
    assert client.post("/api/projects", json={"name": "Bad"}).status_code == 400
    assert [message["type"] for message in drain_messages(client_queue)] == ["projects_updated"]


def test_project_settings_tabs_shortcuts_and_deletion(client: FlaskClient, app: Flask) -> None:
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    client.post("/api/projects", json={"name": "Beta", "color": "#111111", "glyph": 1})
    _shell(app).layouts.save_browser_layout("alpha", "c1", layout_showing(_TERMINAL_1), None, TEST_NOW)

    settings = client.post(
        "/api/projects/alpha/settings",
        json={"name": "Alpha 2", "color": "#222222", "glyph": 2},
    )
    assert settings.status_code == 200 and settings.get_json()["name"] == "Alpha 2"
    assert (
        client.post(
            "/api/projects/everything/settings",
            json={"name": "x", "color": "#222222", "glyph": 2},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/projects/missing/settings",
            json={"name": "x", "color": "#222222", "glyph": 2},
        ).status_code
        == 404
    )

    added = client.post("/api/projects/alpha/tabs", json={"address": str(_TERMINAL_1)})
    assert added.status_code == 200 and added.get_json()["tabs"] == [str(_TERMINAL_1)]
    assert client.post("/api/projects/alpha/tabs", json={"address": "terminal:terminal-1"}).status_code == 400
    removed = client.post("/api/projects/alpha/tabs/remove", json={"address": str(_TERMINAL_1)})
    assert removed.status_code == 200 and removed.get_json()["tabs"] == []

    assert (
        client.post(
            "/api/projects/alpha/shortcuts",
            json={"app": "terminal", "action": "open", "mode": "new"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/projects/alpha/shortcuts",
            json={"app": "nope", "action": "open", "mode": "new"},
        ).status_code
        == 400
    )
    flipped = client.post(
        "/api/projects/alpha/shortcuts",
        json={"app": "terminal", "action": "new", "mode": "focus"},
    )
    assert flipped.status_code == 200
    assert flipped.get_json()["shortcuts"][0] == {
        "app": "terminal",
        "action": "new",
        "mode": "focus",
    }
    pruned = client.post("/api/projects/alpha/shortcuts/remove", json={"app": "files", "action": "open"})
    assert [shortcut["app"] for shortcut in pruned.get_json()["shortcuts"]] == ["terminal"]

    deleted = client.post("/api/projects/alpha/delete")
    assert deleted.status_code == 200 and deleted.get_json() == {"fallback_view_id": "beta"}
    assert not (_shell(app).state_directory / "layouts" / "alpha").exists()
    assert client.post("/api/projects/alpha/delete").status_code == 404


# ---------- section 6: layouts ----------


def test_layouts_are_read_per_client_with_the_seed_as_fallback(client: FlaskClient, app: Flask) -> None:
    assert client.get("/api/layouts/missing?client=c1").status_code == 404
    assert client.get("/api/layouts/everything").status_code == 400
    assert client.get("/api/layouts/everything?client=c1&device=tablet").status_code == 400
    empty = client.get("/api/layouts/everything?client=c1&device=mobile").get_json()
    assert empty == {
        "desktop": None,
        "device_kind": "mobile",
        "updated_at": None,
    }

    client_queue = _register_client(app, "c1", "everything")
    body = {
        "client_id": "c1",
        "save_id": "save-0000000000000001",
        "device_kind": "desktop",
        "desktop": _saved_desktop(_TERMINAL_1),
    }
    saved = client.post("/api/layouts/everything", json=body)
    assert saved.status_code == 200 and saved.get_json()["updated_at"] is not None
    assert client.post("/api/layouts/missing", json=body).status_code == 404
    assert client.post("/api/layouts/everything", json={"client_id": "c1"}).status_code == 400
    assert client.post("/api/layouts/everything", json={**body, "save_id": "nope"}).status_code == 400

    own = client.get("/api/layouts/everything?client=c1").get_json()
    assert [window["address"] for window in own["desktop"]["windows"]] == [str(_TERMINAL_1)]
    assert own["updated_at"] == saved.get_json()["updated_at"]
    seeded = client.get("/api/layouts/everything?client=c2&device=desktop").get_json()
    assert seeded["desktop"] == own["desktop"]
    assert client.get("/api/layouts/everything?client=c2&device=mobile").get_json()["desktop"] is None
    # The write was announced with the window's own save id; a save that changes nothing is not.
    updates = [message for message in drain_messages(client_queue) if message["type"] == "layout_updated"]
    assert updates == [
        {
            "type": "layout_updated",
            "view_id": "everything",
            "client_id": "c1",
            "save_id": "save-0000000000000001",
        }
    ]
    again = {
        **body,
        "save_id": "save-0000000000000002",
        "base_updated_at": own["updated_at"],
    }
    unchanged = client.post("/api/layouts/everything", json=again)
    assert unchanged.status_code == 200 and unchanged.get_json() == {"updated_at": None}
    assert drain_messages(client_queue) == []
    # A save based on an older arrangement than the stored one is refused, and one based on the stored one lands.
    stale = {**again, "base_updated_at": None, "desktop": None}
    assert client.post("/api/layouts/everything", json=stale).status_code == 409
    fresh = {**again, "desktop": None}
    assert client.post("/api/layouts/everything", json=fresh).status_code == 200
    assert client.get("/api/layouts/everything?client=c1").get_json()["desktop"] is None


def test_a_window_saves_what_it_keeps_about_the_desktop_itself(client: FlaskClient, app: Flask) -> None:
    """The geometry, the stacking order, the icon arrangement and the dock's own setting all survive a save."""
    body = {
        "client_id": "c1",
        "save_id": "save-0000000000000001",
        "device_kind": "desktop",
        "desktop": {
            "windows": [
                {
                    "tab_id": str(_TAB),
                    "address": str(_TERMINAL_1),
                    "rect": {"x": 40, "y": 50, "width": 800, "height": 600},
                    "is_minimized": True,
                    "last_focused_ms": 12,
                },
                {
                    "tab_id": "tab-00000000000000bb",
                    "address": str(_FILES),
                    "rect": {"x": 0, "y": 0, "width": 1440, "height": 900},
                    "is_maximized": True,
                    "restore_rect": {"x": 10, "y": 20, "width": 700, "height": 500},
                },
            ],
            "icons": {"cell_by_entry": {"files": {"column": 3, "row": 1}}},
            "dock": {"is_hiding": True},
            "desktop_size": {"width": 1440, "height": 900},
        },
    }
    assert client.post("/api/layouts/everything", json=body).status_code == 200
    stored = client.get("/api/layouts/everything?client=c1").get_json()["desktop"]
    minimized, maximized = stored["windows"]
    assert minimized["rect"] == {"x": 40, "y": 50, "width": 800, "height": 600}
    assert minimized["is_minimized"] is True and minimized["last_focused_ms"] == 12
    assert maximized["is_maximized"] is True and maximized["restore_rect"]["width"] == 700
    assert stored["icons"] == {"cell_by_entry": {"files": {"column": 3, "row": 1}}}
    assert stored["dock"] == {"is_hiding": True}
    assert stored["desktop_size"] == {"width": 1440, "height": 900}


def test_a_save_from_a_window_that_still_holds_a_dockview_arrangement_is_read_as_a_desktop(
    client: FlaskClient, app: Flask
) -> None:
    """A window running the bundle from before the desktop shell posts a grid; its tabs become windows."""
    body = {
        "client_id": "c1",
        "save_id": "save-0000000000000001",
        "device_kind": "desktop",
        "dockview": {
            "panels": {"p0": {"params": {"kind": "instance", "address": "app:stale", "tabId": "tab-0000000000000000"}}}
        },
        "tabs": {"p0": {"address": str(_TERMINAL_1), "tab_id": str(_TAB), "last_focused_ms": 5}},
    }
    assert client.post("/api/layouts/everything", json=body).status_code == 200
    own = client.get("/api/layouts/everything?client=c1").get_json()
    assert "dockview" not in own and "tabs" not in own
    window = own["desktop"]["windows"][0]
    assert (window["address"], window["tab_id"]) == (str(_TERMINAL_1), str(_TAB))


def test_clients_and_the_inventory_document_are_served(client: FlaskClient, app: Flask) -> None:
    shell = _shell(app)
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    _register_client(app, "c1", "alpha")
    _record_client(app, "c2", "everything")
    shell.layouts.save_browser_layout("alpha", "c1", layout_showing(_TERMINAL_1), None, TEST_NOW)

    clients = client.get("/api/clients").get_json()["clients"]
    assert [(entry["id"], entry["is_connected"]) for entry in clients] == [
        ("c1", True),
        ("c2", False),
    ]

    document = client.get("/api/inventory").get_json()
    assert [project["id"] for project in document["projects"]] == ["alpha"]
    assert document["everything"] == {
        "id": "everything",
        "tabs": [str(_TERMINAL_1), str(_FILES)],
    }
    assert [entry["name"] for entry in document["apps"]] == ["terminal", "files"]
    assert {entry["id"]: entry["docked"] for entry in document["clients"]} == {
        "c1": [str(_TERMINAL_1)],
        "c2": [],
    }
    assert document["clients"][0]["active_view"] == "alpha" and document["clients"][0]["is_connected"] is True


def test_a_recorded_client_reads_the_seed_of_its_own_device_kind(client: FlaskClient, app: Flask) -> None:
    """The ``device`` query names the seed only for a client the shell has no record of."""
    mobile_body = {
        "client_id": "m1",
        "save_id": "save-0000000000000001",
        "device_kind": "mobile",
        "desktop": _saved_desktop(_FILES),
    }
    assert client.post("/api/layouts/everything", json=mobile_body).status_code == 200
    _shell(app).clients.record_report(
        ClientStateReport(
            client_id=ClientId("c2"),
            device_kind=DeviceKind.MOBILE,
            active_view=ViewId("everything"),
        ),
        TEST_NOW,
    )

    seeded = client.get("/api/layouts/everything?client=c2&device=desktop").get_json()
    assert seeded["device_kind"] == "mobile"
    assert [window["address"] for window in seeded["desktop"]["windows"]] == [str(_FILES)]


# ---------- the broadcast endpoint ----------


def _broadcast(
    client: FlaskClient,
    op: str,
    args: dict[str, Any] | None = None,
    agent_id: str = "agent-1",
) -> Any:
    """Post an op the way ``layout.py`` does from the chat of ``agent_id``."""
    requester = f"app:chat?instance={agent_id}"
    return client.post(
        "/api/layout/broadcast",
        json={"op": op, "args": args or {}, "requester": requester},
    )


def test_the_broadcast_endpoint_validates_its_input(client: FlaskClient) -> None:
    assert _broadcast(client, "context").status_code == 200
    assert client.post("/api/layout/broadcast", json={"op": "context"}, environ_base=_NOT_LOOPBACK).status_code == 403
    assert _broadcast(client, "explode").status_code == 400
    assert client.post("/api/layout/broadcast", json={"op": "open", "args": []}).status_code == 400
    assert client.post("/api/layout/broadcast", data="{", content_type="application/json").status_code == 400
    # A requester that is not an address is refused, not dropped: dropped, the op would lose its
    # attribution and ``self`` would be reported as unset although the caller sent one.
    refused = client.post("/api/layout/broadcast", json={"op": "context", "requester": "chat:agent-1"})
    assert refused.status_code == 400
    assert "requester" in refused.get_json()["detail"]
    for not_an_address in (7, 0, False, []):
        assert (
            client.post("/api/layout/broadcast", json={"op": "context", "requester": not_an_address}).status_code
            == 400
        )


def test_the_read_ops_answer_from_the_state_files_and_the_activity_log(client: FlaskClient, app: Flask) -> None:
    shell = _shell(app)
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    client.post("/api/projects/alpha/tabs", json={"address": str(_TERMINAL_1)})
    shell.layouts.save_browser_layout("alpha", "c1", layout_showing(_TERMINAL_1), None, TEST_NOW)
    shell.clients.record_report(
        ClientStateReport(
            client_id=ClientId("c1"),
            device_kind=DeviceKind.DESKTOP,
            active_view=ViewId("alpha"),
        ),
        TEST_NOW,
    )
    shell.activity.append_message("c1", "desktop", "alpha", "chat", "agent-1", "hello")
    _register_client(app, "c1", "alpha")
    # A second client that has connected and done nothing else: it has no event in the log.
    _register_client(app, "c9", "everything")

    # The script's list and views read GET /api/inventory; the op route's reads are inspect and context.
    assert _broadcast(client, "list").status_code == 400
    assert _broadcast(client, "views").status_code == 400

    inspected = _broadcast(client, "inspect", {"view": "Alpha"}).get_json()
    assert inspected["client_id"] == "c1"
    window = inspected["layout"]["windows"][0]
    assert (window["address"], window["tab_id"], window["title"]) == (
        str(_TERMINAL_1),
        "tab-0000000000000000",
        "Terminal 1",
    )
    assert window["is_on_top"] is True and window["rect"]["width"] > 0

    context = _broadcast(client, "context").get_json()["clients"]
    assert [entry["client_id"] for entry in context] == ["c1", "c9"]
    assert context[0]["is_connected"] is True and context[0]["active_view"] == "alpha"
    assert context[0]["recent_messages"][0]["address"] == "app:chat?instance=agent-1"
    assert context[1] == {
        "client_id": "c9",
        "device_kind": "desktop",
        "active_view": "everything",
        "last_seen": "",
        "is_connected": True,
        "recent_messages": [],
    }


def test_an_op_is_attributed_to_the_client_that_last_messaged_the_requesting_agent(
    client: FlaskClient, app: Flask
) -> None:
    """With several clients on the view, the requester's own client is the one that last messaged its chat;
    an explicit ``client`` outranks that, and with neither there is no client to answer for."""
    shell = _shell(app)
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    _register_client(app, "c1", "alpha")
    _register_client(app, "c7", "alpha")
    shell.layouts.save_browser_layout("alpha", "c7", layout_showing(_TERMINAL_1), None, TEST_NOW)

    assert _broadcast(client, "inspect", {"view": "alpha"}).get_json()["client_id"] is None

    shell.activity.append_message("c7", "desktop", "alpha", "chat", "agent-1", "hello")
    attributed = _broadcast(client, "inspect", {"view": "alpha"}).get_json()
    assert attributed["client_id"] == "c7"
    assert _window_addresses(attributed["layout"]) == [str(_TERMINAL_1)]
    assert _broadcast(client, "inspect", {"view": "alpha"}, agent_id="agent-2").get_json()["client_id"] is None
    assert _broadcast(client, "inspect", {"view": "alpha", "client": "c1"}).get_json()["client_id"] == "c1"
    # A client id names a layout file, so one outside the id's alphabet is refused before any read.
    assert _broadcast(client, "inspect", {"view": "alpha", "client": "../c1"}).status_code == 400


def test_a_bare_app_requester_is_attributed_to_no_client(app: Flask) -> None:
    """A requester that names an app and no instance has no client that last messaged it: the log is not
    searched under a made-up key."""
    shell = _shell(app)
    _register_client(app, "c7", "alpha")
    shell.activity.append_message("c7", "desktop", "alpha", "files", "None", "hello")
    _register_client(app, "c1", "alpha")

    assert _resolve_client(shell, {}, Address("app:files")) is None


def test_load_switches_the_requesting_agents_client(client: FlaskClient, app: Flask) -> None:
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    _shell(app).activity.append_message("c7", "desktop", "everything", "chat", "agent-1", "hello")
    client_queue = _register_client(app, "c7", "everything")
    _register_client(app, "c8", "everything")

    assert _broadcast(client, "load").status_code == 400
    assert _broadcast(client, "load", {"view": "Nowhere"}).status_code == 404
    loaded = _broadcast(client, "load", {"view": "alpha"})
    assert loaded.status_code == 200 and loaded.get_json() == {
        "ok": True,
        "view_id": "alpha",
        "target_client_id": "c7",
    }
    assert drain_messages(client_queue) == [{"type": "active_view_changed", "client_id": "c7", "view_id": "alpha"}]
    moved = _shell(app).clients.get_client("c7")
    assert moved is not None and str(moved.active_view) == "alpha"
    # A load onto the view the client already has moves nothing and says nothing.
    assert _broadcast(client, "load", {"view": "alpha"}).status_code == 200
    assert drain_messages(client_queue) == []
    # Two clients connected and an agent nobody messaged: nothing to switch, never everyone.
    assert _broadcast(client, "load", {"view": "alpha"}, agent_id="agent-2").status_code == 412
    assert _broadcast(client, "load", {"view": "alpha", "client": "nobody"}, agent_id="agent-2").status_code == 404
    explicit = _broadcast(client, "load", {"view": "alpha", "client": "c8"}, agent_id="agent-2")
    assert explicit.status_code == 200 and explicit.get_json()["target_client_id"] == "c8"


def test_document_ops_edit_the_target_clients_file_and_announce_the_write(client: FlaskClient, app: Flask) -> None:
    shell = _shell(app)
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    client_queue = _register_client(app, "c1", "alpha")

    assert _broadcast(client, "open", {"address": "terminal:terminal-1"}).status_code == 400
    assert _broadcast(client, "open", {"address": "app:nope"}).status_code == 404
    assert _broadcast(client, "open", {"address": "app:terminal?instance=terminal-9"}).status_code == 404

    opened = _broadcast(client, "open", {"address": str(_TERMINAL_1)})
    assert opened.status_code == 200
    answer = opened.get_json()
    assert (answer["view_id"], answer["client_id"], answer["created_address"]) == (
        "alpha",
        "c1",
        None,
    )
    assert _window_addresses(answer["layout"]) == [str(_TERMINAL_1)]
    # The file is the truth: written for this client, filed into the project, and announced with a shell-minted id.
    stored = shell.layouts.read_client_layout("alpha", "c1")
    assert stored is not None and addresses_of_layout(stored) == [_TERMINAL_1]
    assert stored.desktop is not None and stored.desktop.windows[0].rect.width > 0
    assert shell.projects.get_project("alpha").tabs == (_TERMINAL_1,)
    messages = drain_messages(client_queue)
    updates = [message for message in messages if message["type"] == "layout_updated"]
    assert len(updates) == 1 and updates[0]["client_id"] == "c1" and updates[0]["view_id"] == "alpha"
    assert updates[0]["save_id"].startswith("save-")
    assert "projects_updated" in [message["type"] for message in messages]
    # Opening an instance that already has a window raises it rather than opening a second one; since that
    # window is already on top, nothing is written or announced.
    assert _window_addresses(_broadcast(client, "open", {"address": str(_TERMINAL_1)}).get_json()["layout"]) == [
        str(_TERMINAL_1)
    ]
    assert shell.layouts.read_client_layout("alpha", "c1") == stored
    assert [message["type"] for message in drain_messages(client_queue) if message["type"] == "layout_updated"] == []

    # A split tiles the two windows over the desktop: the anchor moves aside for what lands beside it.
    split = _broadcast(
        client,
        "split",
        {
            "address": str(_FILES),
            "relative_to": str(_TERMINAL_1),
            "direction": "below",
            "ratio": 0.5,
        },
    )
    assert split.status_code == 200
    anchor, placed = split.get_json()["layout"]["windows"]
    assert (anchor["address"], placed["address"]) == (str(_TERMINAL_1), str(_FILES))
    assert anchor["rect"]["y"] == 0 and placed["rect"]["y"] == anchor["rect"]["height"]
    assert placed["is_on_top"] is True
    bad_anchor = _broadcast(client, "split", {"address": str(_TERMINAL_2), "relative_to": "app:nope"})
    assert bad_anchor.status_code == 404 and "app:nope" in bad_anchor.get_json()["detail"]
    assert _broadcast(client, "split", {"address": str(_FILES), "direction": "sideways"}).status_code == 400

    focused = _broadcast(client, "focus", {"address": str(_TERMINAL_1)})
    assert focused.status_code == 200
    assert _window_addresses(focused.get_json()["layout"]) == [str(_FILES), str(_TERMINAL_1)]
    assert _broadcast(client, "focus", {"address": "app:browser?instance=x"}).status_code == 404

    # A move snaps an open window beside its anchor without reopening it: same page, new geometry.
    moved = _broadcast(
        client,
        "move",
        {
            "address": str(_FILES),
            "relative_to": str(_TERMINAL_1),
            "direction": "right",
        },
    )
    assert moved.status_code == 200
    moved_windows = {window["address"]: window for window in moved.get_json()["layout"]["windows"]}
    assert moved_windows[str(_TERMINAL_1)]["rect"]["x"] == 0
    assert moved_windows[str(_FILES)]["rect"]["x"] == moved_windows[str(_TERMINAL_1)]["rect"]["width"]
    assert moved_windows[str(_FILES)]["is_on_top"] is True
    after_move = shell.layouts.read_client_layout("alpha", "c1")
    assert after_move is not None and after_move.desktop is not None
    moved_pages = {window.address: window.tab_id for window in after_move.desktop.windows}
    assert set(moved_pages) == {_TERMINAL_1, _FILES}

    # Closing puts a window away and leaves everything else alone: the instance keeps running, and the
    # window is still there to come back to.
    closed = _broadcast(client, "close", {"address": str(_FILES)})
    assert closed.status_code == 200
    put_away = {window["address"]: window for window in closed.get_json()["layout"]["windows"]}
    assert put_away[str(_FILES)]["is_minimized"] is True
    assert put_away[str(_TERMINAL_1)]["is_minimized"] is False
    assert put_away[str(_FILES)]["tab_id"] == moved_pages[_FILES]
    assert _broadcast(client, "close", {"address": "app:browser?instance=x"}).status_code == 404
    # Closing changes no tab set, and stops nothing.
    assert shell.projects.get_project("alpha").tabs == (_TERMINAL_1, _FILES)


def test_new_group_is_accepted_and_ignored(client: FlaskClient, app: Flask) -> None:
    """A desktop has no tab groups, so the flag every existing caller may still pass means nothing here."""
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    _register_client(app, "c1", "alpha")

    assert _broadcast(client, "open", {"address": str(_TERMINAL_1)}).status_code == 200
    opened = _broadcast(client, "open", {"address": str(_FILES), "new_group": True})
    assert opened.status_code == 200
    assert _window_addresses(opened.get_json()["layout"]) == [str(_TERMINAL_1), str(_FILES)]


def test_self_names_the_requesters_own_open_window(client: FlaskClient, app: Flask) -> None:
    """``self`` is the requester's own instance, read from the op's ``requester``: the target of any addressed
    op, and the anchor a split or a move defaults to. An op that carried no requester cannot mean it."""
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    _register_client(app, "c1", "alpha")

    def as_terminal_1(op: str, args: dict[str, Any]) -> Any:
        return client.post("/api/layout/broadcast", json={"op": op, "args": args, "requester": str(_TERMINAL_1)})

    assert as_terminal_1("open", {"address": str(_TERMINAL_1)}).status_code == 200
    assert as_terminal_1("open", {"address": str(_FILES)}).status_code == 200

    unattributed = client.post("/api/layout/broadcast", json={"op": "focus", "args": {"address": "self"}})
    assert unattributed.status_code == 400 and "requester" in unattributed.get_json()["detail"]

    # ``self`` resolves to the requester's own window, which focusing raises to the top.
    focused = as_terminal_1("focus", {"address": "self"})
    assert focused.status_code == 200 and _window_addresses(focused.get_json()["layout"])[-1] == str(_TERMINAL_1)

    # A move with no anchor is relative to self: within puts the moved window in the requester's own place.
    moved = as_terminal_1("move", {"address": str(_FILES), "direction": "within"})
    assert moved.status_code == 200
    windows = {window["address"]: window for window in moved.get_json()["layout"]["windows"]}
    assert windows[str(_FILES)]["rect"] == windows[str(_TERMINAL_1)]["rect"]
    assert windows[str(_FILES)]["is_on_top"] is True


def test_an_open_tiles_beside_the_requesters_own_window(client: FlaskClient, app: Flask) -> None:
    """An app an agent opens lands beside the chat that opened it, not cascaded on top of it.

    The user asked for this: an app opened for them should be readable next to the conversation
    that asked for it. ``open`` takes the same anchor a bare ``split`` would -- the requester's
    own window -- and the same default direction, so the new window takes the right half and the
    requester is resized into the left.
    """
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    _register_client(app, "c1", "alpha")

    def as_terminal_1(op: str, args: dict[str, Any]) -> Any:
        return client.post("/api/layout/broadcast", json={"op": op, "args": args, "requester": str(_TERMINAL_1)})

    # The requester's own window first: nothing to sit beside yet, so it opens on its own.
    assert as_terminal_1("open", {"address": str(_TERMINAL_1)}).status_code == 200
    opened = as_terminal_1("open", {"address": str(_FILES)})
    assert opened.status_code == 200

    windows = {window["address"]: window for window in opened.get_json()["layout"]["windows"]}
    anchor, placed = windows[str(_TERMINAL_1)]["rect"], windows[str(_FILES)]["rect"]
    # Side by side over the whole desktop, on one row, the new window on the right and in front.
    assert anchor["x"] == 0
    assert placed["x"] == anchor["width"]
    assert abs(anchor["width"] - placed["width"]) <= 1
    assert anchor["y"] == placed["y"] == 0
    assert anchor["height"] == placed["height"]
    assert windows[str(_FILES)]["is_on_top"] is True


def test_an_open_with_nothing_to_sit_beside_still_opens(client: FlaskClient, app: Flask) -> None:
    """The tiling is a preference, never a precondition: an open must always open.

    A ``split`` names its anchor and fails without it. An ``open`` has only the requester to go
    on, so a requester with no window on this desktop -- or no requester at all, as a loopback
    caller posts -- cascades the window as it always did rather than refusing.
    """
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    _register_client(app, "c1", "alpha")

    # The requesting chat has no window here, so there is nothing to tile against.
    opened = _broadcast(client, "open", {"address": str(_TERMINAL_1)})
    assert opened.status_code == 200
    only = opened.get_json()["layout"]["windows"][0]
    assert only["address"] == str(_TERMINAL_1)
    # Cascaded, not tiled: it is inset from the desktop's corner rather than pinned to it.
    assert only["rect"]["x"] > 0 and only["rect"]["y"] > 0

    # A split in the same position is the error case, which is the difference being asserted.
    refused = _broadcast(client, "split", {"address": str(_FILES)})
    assert refused.status_code == 404


def test_an_op_lands_with_no_browser_connected_and_never_on_a_guessed_client(client: FlaskClient, app: Flask) -> None:
    shell = _shell(app)
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    # Nothing connected and nothing recorded: there is no client to arrange for.
    assert _broadcast(client, "open", {"address": str(_TERMINAL_1)}).status_code == 412
    # A recorded client with no window open is a fine target when named.
    _record_client(app, "c1", "alpha")
    landed = _broadcast(client, "open", {"address": str(_TERMINAL_1), "client": "c1"})
    assert landed.status_code == 200 and _window_addresses(landed.get_json()["layout"]) == [str(_TERMINAL_1)]
    assert shell.layouts.read_client_layout("alpha", "c1") is not None
    assert _broadcast(client, "open", {"address": str(_TERMINAL_1), "client": "nobody"}).status_code == 404
    # Two clients connected and no attribution: refused with the clients listed, never applied to both.
    _register_client(app, "c2", "alpha")
    _register_client(app, "c3", "everything")
    refused = _broadcast(client, "open", {"address": str(_FILES)}, agent_id="agent-2")
    assert (
        refused.status_code == 412
        and "c2" in refused.get_json()["detail"]
        and "--client" in refused.get_json()["detail"]
    )
    assert shell.layouts.read_client_layout("alpha", "c2") is None
    # The client that last messaged the requesting agent is the one the op is for.
    shell.activity.append_message("c3", "desktop", "everything", "chat", "agent-2", "hello")
    attributed = _broadcast(client, "open", {"address": str(_FILES)}, agent_id="agent-2")
    assert attributed.status_code == 200 and attributed.get_json()["client_id"] == "c3"
    assert shell.layouts.read_client_layout("everything", "c3") is not None


def test_view_edits_that_views_file_and_switches_the_client_to_it(client: FlaskClient, app: Flask) -> None:
    shell = _shell(app)
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    client_queue = _register_client(app, "c1", "everything")

    assert _broadcast(client, "open", {"address": str(_TERMINAL_1), "view": "Nowhere"}).status_code == 404
    opened = _broadcast(client, "open", {"address": str(_TERMINAL_1), "view": "Alpha"})
    assert opened.status_code == 200 and opened.get_json()["view_id"] == "alpha"
    assert shell.layouts.read_client_layout("alpha", "c1") is not None
    assert shell.layouts.read_client_layout("everything", "c1") is None
    switched = shell.clients.get_client("c1")
    assert switched is not None and str(switched.active_view) == "alpha"
    types = [message["type"] for message in drain_messages(client_queue)]
    assert "layout_updated" in types and "active_view_changed" in types
    # A client that never visited the view starts from its seed, which another client saved.
    shell.layouts.save_browser_layout("alpha", "seed-maker", layout_showing(_FILES), None, TEST_NOW)
    _record_client(app, "c2", "everything")
    inherited = _broadcast(client, "open", {"address": str(_TERMINAL_1), "view": "alpha", "client": "c2"})
    assert _window_addresses(inherited.get_json()["layout"]) == [
        str(_FILES),
        str(_TERMINAL_1),
    ]


def test_open_of_a_bare_app_creates_through_the_relay_inside_the_op(
    tmp_path: Path,
    broadcaster: WebSocketBroadcaster,
    stub_source: StubInstanceSource,
    stub_app_url: str,
) -> None:
    inventory = build_inventory(
        write_registry(
            tmp_path / "apps.toml",
            registry_row_toml("stub", stub_app_url, True, actions=[("new", "New"), ("other", "Other")]),
        ),
        broadcaster,
        fetcher=HttpInstanceFetcher(),
    )
    inventory.refetch_now("stub")
    app = shell_application(tmp_path, inventory, broadcaster)
    client = app.test_client()
    _register_client(app, "c1", "everything")

    created = _broadcast(
        client,
        "open",
        {"address": "app:stub", "action": "new", "params": {"path": "/x"}},
    )
    assert created.status_code == 200
    assert created.get_json()["created_address"] == "app:stub?instance=stub-1"
    assert _window_addresses(created.get_json()["layout"]) == ["app:stub?instance=stub-1"]
    assert [(str(record.key), record.title, str(record.url)) for record in stub_source.records] == [
        ("stub-1", "Stub 1", "/x")
    ]
    assert "create:new:{'path': '/x'}" in stub_source.calls
    # An action the app does not declare is the app's own 400, passed through.
    assert _broadcast(client, "open", {"address": "app:stub", "action": "other"}).status_code == 400
    # A split of a bare app creates too, beside its anchor.
    split = _broadcast(
        client,
        "split",
        {"address": "app:stub", "relative_to": "app:stub?instance=stub-1"},
    )
    assert split.status_code == 200 and split.get_json()["created_address"] == "app:stub?instance=stub-2"
    # The app's refusal reaches the caller as the op's error, and nothing is docked.
    assert _broadcast(client, "open", {"address": "app:stub", "action": "nope"}).status_code == 400
    stub_source.is_ready = False
    assert _broadcast(client, "open", {"address": "app:stub"}).status_code == 503
    stored = _shell(app).layouts.read_client_layout("everything", "c1")
    assert stored is not None and len(addresses_of_layout(stored)) == 2


def test_transient_ops_reach_the_target_clients_windows(client: FlaskClient, app: Flask) -> None:
    client.post("/api/projects", json={"name": "Alpha", "color": "#111111", "glyph": 1})
    first_window = _register_client(app, "c1", "alpha")
    second_window = _register_client(app, "c1", "everything")
    other_client = _register_client(app, "c2", "alpha")
    _shell(app).activity.append_message("c1", "desktop", "alpha", "chat", "agent-1", "hello")

    assert _broadcast(client, "maximize", {"address": "terminal:terminal-1"}).status_code == 400
    assert _broadcast(client, "maximize", {"address": "app:nope"}).status_code == 404
    maximized = _broadcast(client, "maximize", {"address": str(_TERMINAL_1)})
    assert maximized.status_code == 200 and maximized.get_json()["target_client_id"] == "c1"
    for window in (first_window, second_window):
        assert drain_messages(window) == [
            {
                "type": "layout_op",
                "op": "maximize",
                "args": {"address": str(_TERMINAL_1)},
                "requester": "app:chat?instance=agent-1",
                "target_client_id": "c1",
            }
        ]
    assert drain_messages(other_client) == []
    # A refresh of a whole app, like the interface reload, reaches every window of every client.
    refreshed = _broadcast(client, "refresh", {"address": str(_FILES)}, agent_id="agent-9")
    assert refreshed.status_code == 200 and refreshed.get_json()["target_client_id"] is None
    for window in (first_window, second_window, other_client):
        assert [message["op"] for message in drain_messages(window)] == ["refresh"]
    # A refresh of one instance is that client's, and an agent nobody messaged has no client.
    assert _broadcast(client, "refresh", {"address": str(_TERMINAL_1)}, agent_id="agent-9").status_code == 412


def test_reload_system_interface_reaches_every_view_and_null_args_are_refused(client: FlaskClient, app: Flask) -> None:
    everything_queue = _register_client(app, "c1", "everything")
    alpha_queue = _register_client(app, "c2", "alpha")
    response = client.post("/api/layout/broadcast", json={"op": "reload_system_interface"})
    assert response.status_code == 200
    for client_queue in (everything_queue, alpha_queue):
        reloads = [message for message in drain_messages(client_queue) if message["type"] == "layout_op"]
        assert [message["op"] for message in reloads] == ["reload_system_interface"]
        assert reloads[0]["target_client_id"] is None
    assert client.post("/api/layout/broadcast", json={"op": "refresh", "args": None}).status_code == 400
