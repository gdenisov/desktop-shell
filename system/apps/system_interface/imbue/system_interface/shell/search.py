"""Finding everything the user has started, running or stopped (contracts.md section 6, ``GET /api/search``).

The desktop's dock shows only what is running, so this is the way back to anything that was
stopped -- load-bearing rather than a nicety.

Two halves, and the split is what keeps the shell from knowing anything about any app. Titles the
shell matches itself, out of the instance lists it already holds. What is *inside* an instance only
its app can see, so the shell fans out to whichever apps declare the instance-search capability in
their manifests and merges what they answer. The shell names no app either way.
"""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from typing import Final

import httpx
from app_instances.blueprint import HTTP_OK
from app_instances.blueprint import SEARCH_PATH
from app_instances.data_types import InstanceMatch
from app_instances.primitives import SearchQuery
from loguru import logger
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.pure import pure
from imbue.system_interface.shell.data_types import AppInventoryEntry
from imbue.system_interface.shell.data_types import InventoryInstance
from imbue.system_interface.shell.data_types import instances_url_of
from imbue.system_interface.shell.primitives import MatchedOn

# A search runs while the user is still typing, so an app that is slow to answer is dropped from
# this keystroke rather than holding the whole result list up.
SEARCH_TIMEOUT_SECONDS: Final[float] = 3.0

# How many apps are asked at once. Every app is a loopback call, so the bound is about not
# starting a thread per app on a machine with many of them rather than about anyone's rate limit.
MAX_SEARCHED_APPS_AT_ONCE: Final[int] = 8

# What one app may contribute, and what the whole answer may hold: the result list is read at a
# glance, and one talkative app must not crowd out every other.
MAX_MATCHES_PER_APP: Final[int] = 8
MAX_RESULTS: Final[int] = 40


class SearchResult(FrozenModel):
    """One thing the user has started that a query found, and why it was found."""

    app: str = Field(description="The app that owns it")
    app_display_name: str = Field(description="What the app is called")
    icon: str = Field(description="The app's icon markup, empty when it registered none")
    label: str = Field(description="The app's origin label, for building the page's url")
    key: str = Field(description="The instance key")
    url: str = Field(description="The instance's page, as a path under the app's origin")
    title: str = Field(description="What the instance is called")
    status: str = Field(description="What the instance is doing, ``stopped`` included")
    matched_on: MatchedOn = Field(description="Whether the title matched, or something inside it")
    snippet: str = Field(description="The matching line, for a content match; empty for a title match")


@pure
def _result_for(
    entry: AppInventoryEntry, instance: InventoryInstance, matched_on: MatchedOn, snippet: str
) -> SearchResult:
    row = entry.row
    return SearchResult(
        app=str(row.name),
        app_display_name=str(row.display_name) if row.display_name is not None else str(row.name),
        icon=row.icon or "",
        label=row.label,
        key=instance.key,
        url=instance.url,
        title=instance.title,
        status=instance.status.value,
        matched_on=matched_on,
        snippet=snippet,
    )


@pure
def _searchable_entries(entries: Sequence[AppInventoryEntry]) -> list[AppInventoryEntry]:
    """The apps a query is put to: every one the user can open, in registry order."""
    return [entry for entry in entries if not entry.row.internal]


@pure
def title_matches(entries: Sequence[AppInventoryEntry], query: str) -> list[SearchResult]:
    """Every instance whose title contains the query, running or stopped, most recently active first."""
    lowered = query.lower()
    matched: list[tuple[float, SearchResult]] = []
    for entry in _searchable_entries(entries):
        for instance in entry.instances:
            if lowered not in instance.title.lower():
                continue
            recency = instance.last_active.timestamp() if instance.last_active is not None else 0.0
            matched.append((recency, _result_for(entry, instance, MatchedOn.TITLE, "")))
    return [result for _, result in sorted(matched, key=lambda pair: -pair[0])]


@pure
def _content_searching_entries(entries: Sequence[AppInventoryEntry]) -> list[AppInventoryEntry]:
    """The apps that can search inside their own instances and are up to answer."""
    return [
        entry
        for entry in _searchable_entries(entries)
        if entry.row.instances and entry.row.instance_search and entry.is_running
    ]


class _SearchAnswer(FrozenModel):
    """An app's answer to a search, read loosely: the matches are parsed one at a time below."""

    # An app's answer is cross-version wire data, so a key this shell does not know must not cost
    # the whole answer.
    model_config = ConfigDict(extra="ignore")

    matches: tuple[dict[str, Any], ...] = Field(default=(), description="The matches, unparsed")


def _ask_app(client: httpx.Client, entry: AppInventoryEntry, query: SearchQuery) -> list[InstanceMatch]:
    """What one app found inside its instances. An app that fails or answers oddly contributes nothing."""
    url = f"{instances_url_of(entry.row).rstrip('/')}{SEARCH_PATH}"
    try:
        response = client.get(
            url,
            params={"q": str(query), "limit": MAX_MATCHES_PER_APP},
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as e:
        logger.warning("Could not search inside {}: {}", entry.row.name, e)
        return []
    if response.status_code != HTTP_OK:
        logger.warning("The app {} refused a search with status {}", entry.row.name, response.status_code)
        return []
    try:
        answer = _SearchAnswer.model_validate_json(response.content)
    except ValidationError as e:
        logger.warning("The app {} answered a search unreadably: {}", entry.row.name, e.errors()[0]["msg"])
        return []
    return _parsed_matches(entry, answer.matches)


def _parsed_matches(entry: AppInventoryEntry, raw_matches: Sequence[dict[str, Any]]) -> list[InstanceMatch]:
    """The matches an app's answer holds; one that does not parse is skipped rather than costing the rest."""
    parsed: list[InstanceMatch] = []
    for raw_match in raw_matches[:MAX_MATCHES_PER_APP]:
        try:
            parsed.append(InstanceMatch.model_validate(raw_match))
        except ValidationError as e:
            logger.warning("Skipped an unreadable match from {}: {}", entry.row.name, e.errors()[0]["msg"])
    return parsed


def content_matches(
    client: httpx.Client,
    entries: Sequence[AppInventoryEntry],
    query: SearchQuery,
    already_found_keys: frozenset[tuple[str, str]],
) -> list[SearchResult]:
    """What the searching apps found inside their instances, asked in parallel and merged in registry order.

    A match whose key the app no longer lists is dropped: the result row is built from the
    inventory's own record, so there is nothing to open otherwise.
    """
    searching = _content_searching_entries(entries)
    if not searching:
        return []
    with ThreadPoolExecutor(max_workers=min(MAX_SEARCHED_APPS_AT_ONCE, len(searching))) as pool:
        # Every app is asked at once, and the answers are collected in the order the apps are
        # registered rather than the order they arrive, so one query always draws one list.
        in_flight = [pool.submit(_ask_app, client, entry, query) for entry in searching]
        matches_by_entry = [future.result() for future in in_flight]
    results: list[SearchResult] = []
    for entry, matches in zip(searching, matches_by_entry):
        instance_by_key = {instance.key: instance for instance in entry.instances}
        for match in matches:
            instance = instance_by_key.get(str(match.key))
            if instance is None or (str(entry.row.name), instance.key) in already_found_keys:
                continue
            results.append(_result_for(entry, instance, MatchedOn.CONTENT, str(match.snippet)))
    return results


def search_everything_started(
    client: httpx.Client, entries: Sequence[AppInventoryEntry], query: SearchQuery
) -> list[SearchResult]:
    """Everything the user has started that matches: by title first, then by what is inside it."""
    by_title = title_matches(entries, str(query))
    found_keys = frozenset((result.app, result.key) for result in by_title)
    by_content = content_matches(client, entries, query, found_keys)
    return (by_title + by_content)[:MAX_RESULTS]


@pure
def search_wire_json(results: Sequence[SearchResult]) -> dict[str, Any]:
    return {"results": [result.model_dump(mode="json") for result in results]}
