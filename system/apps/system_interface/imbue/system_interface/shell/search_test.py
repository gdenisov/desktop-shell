from pathlib import Path

import httpx
import pytest
from app_instances.data_types import InstanceStatus
from app_instances.primitives import SearchQuery

from imbue.imbue_common.model_update import to_update
from imbue.system_interface.shell.data_types import AppInventoryEntry
from imbue.system_interface.shell.primitives import MatchedOn
from imbue.system_interface.shell.search import search_everything_started
from imbue.system_interface.shell.search import title_matches
from imbue.system_interface.shell.testing import build_inventory
from imbue.system_interface.shell.testing import instance_record
from imbue.system_interface.shell.testing import registry_row_toml
from imbue.system_interface.shell.testing import write_registry
from imbue.system_interface.shell.testing import FakeInstanceFetcher
from imbue.system_interface.ws_broadcaster import WebSocketBroadcaster

_SEARCHING_APP_URL = "http://localhost:7900"
_PLAIN_APP_URL = "http://localhost:7901"


def _entries(
    tmp_path: Path, broadcaster: WebSocketBroadcaster, fetcher: FakeInstanceFetcher
) -> list[AppInventoryEntry]:
    """Two apps: one that searches inside its instances, one that does not, plus a hidden one."""
    registry = write_registry(
        tmp_path / "apps.toml",
        registry_row_toml("searching", _SEARCHING_APP_URL, True, program="searching", instance_search=True),
        registry_row_toml("plain", _PLAIN_APP_URL, True, program="plain"),
        registry_row_toml("hidden", "http://localhost:7902", True, program="hidden", is_internal=True),
    )
    inventory = build_inventory(registry, broadcaster, fetcher=fetcher)
    fetcher.list(
        _SEARCHING_APP_URL,
        instance_record("one", title="Roadmap review"),
        instance_record("two", title="Untitled", status=InstanceStatus.STOPPED),
    )
    fetcher.list(_PLAIN_APP_URL, instance_record("viewer", title="Roadmap notes"))
    fetcher.list("http://localhost:7902", instance_record("secret", title="Roadmap secrets"))
    for name in ("searching", "plain", "hidden"):
        inventory.refetch_now(name)
    return list(inventory.entries())


def _client_answering(handler: "httpx.MockTransport") -> httpx.Client:
    return httpx.Client(transport=handler)


def test_titles_are_matched_by_the_shell_itself_running_or_stopped(
    tmp_path: Path, broadcaster: WebSocketBroadcaster, fetcher: FakeInstanceFetcher
) -> None:
    entries = _entries(tmp_path, broadcaster, fetcher)

    found = title_matches(entries, "roadmap")

    assert [(result.app, result.key) for result in found] == [("searching", "one"), ("plain", "viewer")]
    assert all(result.matched_on is MatchedOn.TITLE and result.snippet == "" for result in found)
    # An app the user cannot open is not somewhere the user has started anything.
    assert "hidden" not in {result.app for result in found}


def test_a_stopped_instance_is_found_by_title_and_says_so(
    tmp_path: Path, broadcaster: WebSocketBroadcaster, fetcher: FakeInstanceFetcher
) -> None:
    entries = _entries(tmp_path, broadcaster, fetcher)
    found = title_matches(entries, "untitled")
    assert [(result.key, result.status) for result in found] == [("two", "stopped")]


def test_only_the_apps_that_declare_the_capability_are_asked_about_their_contents(
    tmp_path: Path, broadcaster: WebSocketBroadcaster, fetcher: FakeInstanceFetcher
) -> None:
    entries = _entries(tmp_path, broadcaster, fetcher)
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json={"matches": [{"key": "two", "snippet": "  we talked   about the dock  "}]})

    results = search_everything_started(
        _client_answering(httpx.MockTransport(answer)), entries, SearchQuery("dock")
    )

    assert len(asked) == 1 and asked[0].startswith(f"{_SEARCHING_APP_URL}/_instances/search")
    assert "q=dock" in asked[0]
    assert [(result.app, result.key, result.matched_on) for result in results] == [
        ("searching", "two", MatchedOn.CONTENT)
    ]
    # The snippet is collapsed onto one line for the result row.
    assert results[0].snippet == "we talked about the dock"
    assert results[0].title == "Untitled" and results[0].status == "stopped"


def test_a_title_match_is_never_repeated_as_a_content_match(
    tmp_path: Path, broadcaster: WebSocketBroadcaster, fetcher: FakeInstanceFetcher
) -> None:
    entries = _entries(tmp_path, broadcaster, fetcher)

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"matches": [{"key": "one", "snippet": "roadmap again"}]})

    results = search_everything_started(
        _client_answering(httpx.MockTransport(answer)), entries, SearchQuery("roadmap")
    )

    assert [(result.app, result.key) for result in results] == [("searching", "one"), ("plain", "viewer")]
    assert results[0].matched_on is MatchedOn.TITLE


@pytest.mark.parametrize(
    "answer_body",
    [
        {"matches": [{"key": "gone", "snippet": "an instance the app no longer lists"}]},
        {"matches": [{"key": "two"}]},
        {"matches": "not a list"},
        {"something": "else"},
    ],
)
def test_an_app_that_answers_a_search_oddly_contributes_nothing(
    tmp_path: Path, broadcaster: WebSocketBroadcaster, fetcher: FakeInstanceFetcher, answer_body: object
) -> None:
    entries = _entries(tmp_path, broadcaster, fetcher)

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answer_body)

    assert (
        search_everything_started(_client_answering(httpx.MockTransport(answer)), entries, SearchQuery("dock"))
        == []
    )


def test_an_app_that_fails_or_refuses_does_not_sink_the_whole_search(
    tmp_path: Path, broadcaster: WebSocketBroadcaster, fetcher: FakeInstanceFetcher
) -> None:
    entries = _entries(tmp_path, broadcaster, fetcher)

    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nobody home", request=request)

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "still starting"})

    for handler in (explode, refuse):
        results = search_everything_started(
            _client_answering(httpx.MockTransport(handler)), entries, SearchQuery("roadmap")
        )
        # The titles the shell matched itself still come back.
        assert [result.key for result in results] == ["one", "viewer"]


def test_an_app_that_is_not_running_is_not_asked(
    tmp_path: Path, broadcaster: WebSocketBroadcaster, fetcher: FakeInstanceFetcher
) -> None:
    entries = _entries(tmp_path, broadcaster, fetcher)
    stopped = [
        entry.model_copy_update(to_update(entry.field_ref().is_running, False))
        if entry.row.name == "searching"
        else entry
        for entry in entries
    ]
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json={"matches": []})

    search_everything_started(_client_answering(httpx.MockTransport(answer)), stopped, SearchQuery("dock"))

    assert asked == []
