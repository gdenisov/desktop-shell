"""Searching what was said in a chat.

Every fixture here is the shape a real ``events/<harness>/common_transcript/events.jsonl`` on this
machine actually holds -- key for key, including the keys the search must NOT read. That is
load-bearing rather than tidy: the search reads one field off one event type, and a fixture that
agrees with the code about a field neither the converter nor any harness writes would pass while
the feature found nothing at all in the workspace it runs in. It has happened in both directions
here, so ``test_the_fixtures_here_are_the_shape_a_real_transcript_has`` checks these shapes against
a real file whenever the machine running the tests has one.
"""

import json
import os
from pathlib import Path
from typing import Any
from typing import Final

import pytest
from app_instances.primitives import SearchQuery

from imbue.chat.transcript_search import TRANSCRIPT_SCAN_BYTES
from imbue.chat.transcript_search import TranscriptCache
from imbue.chat.transcript_search import search_transcripts
from imbue.chat.transcript_search import snippet_from_transcript_line
from imbue.chat.transcript_search import transcript_paths

_EMITTER = "claude/common_transcript"
_TIMESTAMP = "2026-09-17T19:39:56.306Z"

# The first line of every transcript: no prose, and nothing anyone said.
_HEADER: dict[str, Any] = {"type": "header", "event_id": "header", "emitter": _EMITTER, "schema_version": 1}


def _step(message: str, source: str, event_id: str) -> dict[str, Any]:
    """A turn: what a person typed (``user``), what the mind said (``agent``), or what a skill or
    the framework put in front of it (``system``)."""
    return {
        "type": "step",
        "event_id": event_id,
        "emitter": _EMITTER,
        "timestamp": _TIMESTAMP,
        "source": source,
        "message": message,
    }


def _said(message: str, event_id: str = "e-user") -> dict[str, Any]:
    return _step(message, "user", event_id)


def _replied(
    message: str, event_id: str = "e-agent", tool_calls: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The mind's turn, which carries the tool calls it made alongside what it said."""
    event = {
        **_step(message, "agent", event_id),
        "model_name": "claude-opus-5",
        "llm_call_count": 1,
        "metrics": {"input_tokens": 10, "output_tokens": 20},
        "extra": {},
    }
    if tool_calls is not None:
        event["tool_calls"] = tool_calls
    return event


def _injected(message: str, event_id: str = "e-system") -> dict[str, Any]:
    return _step(message, "system", event_id)


def _tool_output(text: str, event_id: str = "e-obs") -> dict[str, Any]:
    """A tool's output, which is its own event type and carries no prose at all."""
    return {
        "type": "observation",
        "event_id": event_id,
        "emitter": _EMITTER,
        "timestamp": _TIMESTAMP,
        "results": [{"content": text, "extra": {"tool_name": "Bash"}}],
    }


def _converter_log(message: str, event_id: str = "e-log") -> dict[str, Any]:
    """The converter's own log line, written in the same shape as a transcript event."""
    return {
        "type": "common_transcript",
        "event_id": event_id,
        "source": "logs/common_transcript",
        "timestamp": _TIMESTAMP,
        "level": "INFO",
        "message": message,
        "pid": 57639,
    }


def _found(
    host_dir: Path, agent_ids: list[str], query: SearchQuery, cache: TranscriptCache | None = None
) -> str | None:
    """What a search of these agents found, read as the result row would show it.

    A fresh cache per search unless one is passed: these tests write files and search them in
    the same instant, which is exactly what the process-lived cache is not for.
    """
    outcome = search_transcripts(host_dir, agent_ids, query, TRANSCRIPT_SCAN_BYTES, cache or _fresh_cache())
    return None if outcome.snippet is None else str(outcome.snippet)


def _fresh_cache() -> TranscriptCache:
    return TranscriptCache(max_bytes=TRANSCRIPT_SCAN_BYTES * 4)


def _write_transcript(host_dir: Path, agent_id: str, events: list[dict[str, Any]], harness: str = "claude") -> Path:
    path = host_dir / "agents" / agent_id / "events" / harness / "common_transcript" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{json.dumps(event)}\n" for event in [_HEADER, *events]))
    return path


# Where this machine keeps its agents, read as this module is IMPORTED -- which is before the
# autouse fixture that points MNGR_HOST_DIR at an empty tmp dir for every test in the package.
# That isolation is right for all of them but one: the shape lock below is specifically about the
# files on the real disk, and an isolated world has none.
_MACHINE_HOST_DIR: Final[Path | None] = Path(os.environ["MNGR_HOST_DIR"]) if "MNGR_HOST_DIR" in os.environ else None


def _real_transcripts() -> list[Path]:
    """Every transcript this machine has actually written, the converter's own log left out."""
    if _MACHINE_HOST_DIR is None or not (_MACHINE_HOST_DIR / "agents").is_dir():
        return []
    return [
        path
        for path in sorted((_MACHINE_HOST_DIR / "agents").glob("*/events/*/common_transcript/events.jsonl"))
        if path.parent.parent.name != "logs" and path.stat().st_size > 0
    ]


def test_a_phrase_someone_said_is_found_with_the_line_it_was_said_on(tmp_path: Path) -> None:
    _write_transcript(
        tmp_path,
        "agent-1",
        [
            _said("let's make the dock show only what is running"),
            _replied("Understood, the dock is what is RUNNING."),
        ],
    )

    snippet = _found(tmp_path, ["agent-1"], SearchQuery("only what is running"))

    assert snippet is not None
    assert "only what is running" in snippet
    assert "let's make the dock show" in snippet


def test_what_the_mind_said_back_is_searched_too(tmp_path: Path) -> None:
    _write_transcript(tmp_path, "agent-1", [_replied("The dock is what is RUNNING, not what is open.")])

    snippet = _found(tmp_path, ["agent-1"], SearchQuery("not what is open"))

    assert snippet is not None and "not what is open" in snippet


def test_the_search_is_indifferent_to_case_and_finds_nothing_that_was_not_said(tmp_path: Path) -> None:
    _write_transcript(tmp_path, "agent-1", [_said("Rethink the System Interface")])

    assert _found(tmp_path, ["agent-1"], SearchQuery("system INTERFACE")) is not None
    assert _found(tmp_path, ["agent-1"], SearchQuery("something else entirely")) is None
    assert _found(tmp_path, ["agent-unknown"], SearchQuery("anything")) is None


def test_the_record_around_what_was_said_is_not_searched(tmp_path: Path) -> None:
    """Ids, timestamps, the model, the emitter: all on the line, none of it said by anyone.

    Every string here really is in the transcript, so the raw scan finds each one -- and each is
    developer text the user has never seen, so a hit on it would be a result row that looks like
    nothing at all.
    """
    _write_transcript(tmp_path, "agent-1", [_said("a perfectly ordinary sentence"), _replied("and an answer")])

    for unsaid in ("e-user", _TIMESTAMP, _EMITTER, "claude-opus-5", "llm_call_count"):
        assert _found(tmp_path, ["agent-1"], SearchQuery(unsaid)) is None, f"searching found {unsaid}"


def test_a_tools_name_and_the_paths_it_touched_are_not_searched(tmp_path: Path) -> None:
    """A search for a file name must find the sentence about it, not every command that read it.

    The tool's input is on the turn that made the call and its output is an event of its own, so
    both are excluded by not being read at all rather than by parsing prose back out of them.
    """
    _write_transcript(
        tmp_path,
        "agent-1",
        [
            _replied(
                "Looking now.",
                tool_calls=[{"tool_name": "Read", "input": {"file_path": "/srv/payroll.csv"}, "id": "toolu_1"}],
            ),
            _tool_output("name,salary\nada,100"),
            _said("what is in payroll.csv?", event_id="e-user-2"),
        ],
    )

    # The word is in the tool's input and in the sentence; the sentence is what comes back.
    snippet = _found(tmp_path, ["agent-1"], SearchQuery("payroll.csv"))
    assert snippet is not None and "what is in payroll.csv?" in snippet
    # And nothing about the call or its output is something anyone said.
    assert _found(tmp_path, ["agent-1"], SearchQuery("toolu_1")) is None
    assert _found(tmp_path, ["agent-1"], SearchQuery("file_path")) is None
    assert _found(tmp_path, ["agent-1"], SearchQuery("name,salary")) is None


def test_text_the_framework_injected_is_not_something_anyone_said(tmp_path: Path) -> None:
    """A skill's body and a system reminder are in the transcript and in nobody's conversation.

    This is the false positive the user saw most: a search for a word in a skill turned up every
    chat that had ever run it, under a snippet of instructions they had never read.
    """
    _write_transcript(
        tmp_path,
        "agent-1",
        [
            _injected("Skill: update-system-interface. Always rebuild the bundle after editing."),
            _said("rebuild the bundle when you are done", event_id="e-user-2"),
        ],
    )

    snippet = _found(tmp_path, ["agent-1"], SearchQuery("rebuild the bundle"))

    assert snippet == "rebuild the bundle when you are done"
    assert _found(tmp_path, ["agent-1"], SearchQuery("update-system-interface")) is None


def test_the_converters_own_log_is_not_a_conversation(tmp_path: Path) -> None:
    """It is written in the same shape, in a directory beside the transcripts, and it is logs."""
    path = tmp_path / "agents" / "agent-1" / "events" / "logs" / "common_transcript" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(f"{json.dumps(_converter_log('Common transcript converter started'))}\n")
    _write_transcript(tmp_path, "agent-1", [_said("started the converter myself")])

    assert transcript_paths(tmp_path, "agent-1") == [
        tmp_path / "agents" / "agent-1" / "events" / "claude" / "common_transcript" / "events.jsonl"
    ]
    assert _found(tmp_path, ["agent-1"], SearchQuery("converter started")) is None
    assert _found(tmp_path, ["agent-1"], SearchQuery("started the converter")) is not None


def test_a_snippet_is_prose_rather_than_the_json_it_was_read_out_of(tmp_path: Path) -> None:
    _write_transcript(tmp_path, "agent-1", [_said("ship the icon row")])

    snippet = _found(tmp_path, ["agent-1"], SearchQuery("icon row"))

    assert snippet == "ship the icon row"


def test_the_newest_agent_of_a_chat_is_searched_first(tmp_path: Path) -> None:
    """A chat that moved to another agent is several transcripts; the live one is where the user is."""
    _write_transcript(tmp_path, "agent-old", [_said("the dock, as it was long ago")])
    _write_transcript(tmp_path, "agent-new", [_said("the dock, as it is now")])

    snippet = _found(tmp_path, ["agent-old", "agent-new"], SearchQuery("the dock"))

    assert snippet is not None and "as it is now" in snippet


def test_a_damaged_line_costs_that_line_and_nothing_else(tmp_path: Path) -> None:
    path = _write_transcript(tmp_path, "agent-1", [_said("the dock again")])
    path.write_text("{ this line is not json but mentions the dock\n" + path.read_text())

    snippet = _found(tmp_path, ["agent-1"], SearchQuery("the dock"))

    assert snippet is not None and "the dock again" in snippet


def test_only_the_tail_of_a_long_conversation_is_searched(tmp_path: Path) -> None:
    """A chat runs to megabytes and the box asks again on every keystroke, so the read is bounded."""
    ancient = _said("the thing I said at the very beginning", event_id="e-0")
    filler = [_replied("x" * 2000, event_id=f"f{index}") for index in range(TRANSCRIPT_SCAN_BYTES // 2000 + 20)]
    recent = _said("the thing I said just now", event_id="e-1")
    _write_transcript(tmp_path, "agent-1", [ancient, *filler, recent])

    assert _found(tmp_path, ["agent-1"], SearchQuery("just now")) is not None
    assert _found(tmp_path, ["agent-1"], SearchQuery("very beginning")) is None


def test_a_snippet_is_one_line_around_the_match(tmp_path: Path) -> None:
    line = json.dumps(_said("before\n\nthe   needle\nafter")).encode()
    snippet = snippet_from_transcript_line(line, SearchQuery("needle"))
    assert snippet is not None and "needle" in snippet
    assert "\n" not in snippet

    assert snippet_from_transcript_line(b"not json at all", SearchQuery("json")) is None
    assert snippet_from_transcript_line(b'"a bare string"', SearchQuery("bare")) is None


def test_a_snippet_is_cut_between_words_and_says_so(tmp_path: Path) -> None:
    # A match deep in a long turn: what is shown starts and ends on whole words, with an ellipsis
    # at each edge that was cut, rather than opening mid-word ("nt `agent-f8...").
    words = [f"word{index}" for index in range(80)]
    words[40] = "needle"
    line = json.dumps(_said(" ".join(words))).encode()
    snippet = snippet_from_transcript_line(line, SearchQuery("needle"))
    assert snippet is not None
    assert snippet.startswith("…word") and snippet.endswith("…")
    assert " needle " in snippet
    inner = snippet.strip("…").split(" ")
    assert inner[0] in words and inner[-1] in words, inner

    # A short turn is shown whole: nothing was cut, so nothing says so.
    whole = snippet_from_transcript_line(json.dumps(_said("a short needle here")).encode(), SearchQuery("needle"))
    assert whole == "a short needle here"


def test_every_transcript_an_agent_has_written_is_searched(tmp_path: Path) -> None:
    """An agent's harness names its own directory, and a chat that was handed over has more than one."""
    _write_transcript(tmp_path, "agent-1", [_said("said under claude")], harness="claude")
    _write_transcript(tmp_path, "agent-1", [_said("said under codex")], harness="codex")

    assert len(transcript_paths(tmp_path, "agent-1")) == 2
    assert _found(tmp_path, ["agent-1"], SearchQuery("said under codex")) is not None
    assert _found(tmp_path, ["agent-1"], SearchQuery("said under claude")) is not None


def _rewrite_keeping_the_stamp(path: Path, events: list[dict[str, Any]]) -> None:
    """Overwrite a transcript so that its size and mtime are what they were: what a cache checks."""
    before = path.stat()
    text = "".join(f"{json.dumps(event)}\n" for event in [_HEADER, *events])
    assert len(text.encode()) == before.st_size, "the fixture must be rewritten at the same size"
    path.write_text(text)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))


def test_a_transcript_that_has_not_changed_is_not_scanned_from_disk_again(tmp_path: Path) -> None:
    """The box asks again on every keystroke; the second keystroke should not cost a read."""
    path = _write_transcript(tmp_path, "agent-1", [_said("the first thing that was said")])
    cache = _fresh_cache()
    assert _found(tmp_path, ["agent-1"], SearchQuery("first thing"), cache) is not None

    # The file changes under the cache without its size or mtime moving -- which is not something
    # a running harness does, and is exactly what makes the cache's answer visible from outside:
    # the new words are not scanned for, because the scan is over what was read before.
    _rewrite_keeping_the_stamp(path, [_said("the other thing that was said")])
    assert _found(tmp_path, ["agent-1"], SearchQuery("other thing"), cache) is None
    # A search with no cache of its own reads the disk and sees the file as it is.
    assert _found(tmp_path, ["agent-1"], SearchQuery("other thing")) is not None
    # And the old words are not shown either: the line is read back from the file for the
    # snippet, and the file no longer says them.
    assert _found(tmp_path, ["agent-1"], SearchQuery("first thing"), cache) is None


def test_a_transcript_that_grew_is_read_again(tmp_path: Path) -> None:
    """A harness appends to its transcript as the chat goes on; the next keystroke sees the new turn."""
    cache = _fresh_cache()
    _write_transcript(tmp_path, "agent-1", [_said("what was said first")])
    assert _found(tmp_path, ["agent-1"], SearchQuery("said first"), cache) is not None

    _write_transcript(tmp_path, "agent-1", [_said("what was said first"), _said("what was said next")])
    assert _found(tmp_path, ["agent-1"], SearchQuery("said next"), cache) is not None


def test_the_cache_holds_no_more_than_it_was_allowed(tmp_path: Path) -> None:
    """Bounded like the search it serves: a cache of one search's budget cannot outgrow it."""
    small = TranscriptCache(max_bytes=64, listing_ttl_seconds=0)
    for index in range(3):
        _write_transcript(tmp_path, f"agent-{index}", [_said(f"said in chat number {index}")])
    for index in range(3):
        assert _found(tmp_path, [f"agent-{index}"], SearchQuery(f"number {index}"), small) is not None
    # Every one of those was too big to keep, and the search still answered from disk each time.
    assert small.held_bytes == 0


def test_an_agents_transcript_files_are_listed_again_once_the_listing_is_stale(tmp_path: Path) -> None:
    """A second harness's file appears once the listing's time is up, not before."""
    remembering = TranscriptCache(max_bytes=TRANSCRIPT_SCAN_BYTES, listing_ttl_seconds=3600)
    forgetting = TranscriptCache(max_bytes=TRANSCRIPT_SCAN_BYTES, listing_ttl_seconds=0)
    _write_transcript(tmp_path, "agent-1", [_said("said under claude")], harness="claude")
    for cache in (remembering, forgetting):
        assert _found(tmp_path, ["agent-1"], SearchQuery("under claude"), cache) is not None

    _write_transcript(tmp_path, "agent-1", [_said("said under codex")], harness="codex")
    assert _found(tmp_path, ["agent-1"], SearchQuery("under codex"), remembering) is None
    assert _found(tmp_path, ["agent-1"], SearchQuery("under codex"), forgetting) is not None


def test_the_fixtures_here_are_the_shape_a_real_transcript_has() -> None:
    """The lock on every test above: the shapes they search are the shapes on this machine's disk.

    Skipped where the machine has written no transcript (a clean CI checkout). It is the one test
    here that cannot be satisfied by agreeing with the code, because its other side is a file
    neither this file nor the search wrote.

    Read across every transcript rather than one, because a single chat need not contain every
    shape -- a sub-agent that nobody typed at has no ``user`` turn in it at all.
    """
    transcripts = _real_transcripts()
    if not transcripts:
        pytest.skip("this machine has written no transcript to check the fixtures against")

    real_keys: set[frozenset[str]] = set()
    spoken_sources: set[str] = set()
    for path in transcripts:
        for line in path.read_text(errors="replace").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            real_keys.add(frozenset(str(key) for key in event))
            if event.get("type") == "step":
                spoken_sources.add(str(event.get("source")))

    # A turn really is a ``step`` carrying ``message``, and the speakers really are told apart by
    # ``source``: that is the whole basis for what the search reads and what it passes over.
    assert {"user", "agent"} <= spoken_sources, f"the transcripts do not tell the speakers apart: {spoken_sources}"
    fixtures = {
        "a person's turn": _said("x"),
        "the mind's turn": _replied("x"),
        "the mind's turn with a tool call": _replied("x", tool_calls=[{"tool_name": "Read"}]),
        "an injected turn": _injected("x"),
        "a tool's output": _tool_output("x"),
        "the header": _HEADER,
    }
    for description, fixture in fixtures.items():
        assert frozenset(fixture) in real_keys, (
            f"{description} is not a shape any transcript on this machine holds: {sorted(fixture)}"
        )
