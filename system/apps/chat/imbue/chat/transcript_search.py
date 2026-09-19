"""Finding a phrase in what was actually said in a chat (contracts.md section 4.4).

The shell matches every instance's title itself, from the list it already holds. What is
*inside* a chat only this app can see, so it serves the optional instance-search capability over
its agents' transcripts, and the desktop's search box shows the matching line under the chat's
name.

A chat's transcript is the JSONL its agents' harnesses write, at
``<host dir>/agents/<agent id>/events/<harness>/common_transcript/events.jsonl``. This searches
it the cheap way, because the box asks again on every keystroke: only the tail of each file is
read, the needle is looked for in the raw bytes of a line before anything is parsed, and only a
line that holds it is decoded into the snippet the result row shows. A long conversation
therefore costs a scan rather than a parse, and a chat stops being read the moment it matches.

The keystrokes come faster than the disk answers, so the reads are held in a ``TranscriptCache``
between them: a transcript whose size and mtime have not moved is scanned from memory -- as the
lowercased bytes the scan wants, so the scan is one ``find`` and the memory is one copy -- and
the list of an agent's transcript files, a glob, which is the single slowest thing here on a
sandboxed filesystem, is kept for a few seconds. The caller owns the cache (the instance source
holds one for the process's life), so a test that writes its own files sees them at once.
"""

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from typing import BinaryIO
from typing import Final

from app_instances.primitives import MAX_MATCH_SNIPPET_LENGTH
from app_instances.primitives import MatchSnippet
from app_instances.primitives import SearchQuery
from loguru import logger
from pydantic import Field
from pydantic import PrivateAttr

from imbue.imbue_common.frozen_model import FrozenModel
from imbue.imbue_common.mutable_model import MutableModel

# How much of one chat may be read to search it, from the end of its transcripts. A chat that has
# run for months is read from its end; anything shorter than this is read whole, which is nearly
# every chat, so a search usually reaches the first thing the user ever typed in it.
TRANSCRIPT_SCAN_BYTES: Final[int] = 8 * 1024 * 1024

# How many lines one transcript may decode before it gives up on finding something that was
# actually said, so a word that appears all over the machine's own output stays cheap.
MAX_LINES_DECODED_PER_TRANSCRIPT: Final[int] = 40

# How much text is shown either side of the match in the result row.
SNIPPET_MARGIN: Final[int] = 90

# How long an agent's list of transcript files is trusted before it is globbed again. A file
# appears there once per harness an agent has ever run under, so the list changes about never;
# a new one is searchable this many seconds late.
TRANSCRIPT_LISTING_TTL_SECONDS: Final[float] = 10.0

# What one line of a transcript has to be for the search to read it. Taken from the files
# themselves rather than from any schema (``transcript_search_test`` re-checks it against a real
# one), because what is on disk is what a search either finds or misses:
#
#   {"type": "step", "source": "user",   "message": "<what the person typed>", ...}
#   {"type": "step", "source": "agent",  "message": "<what the mind said>", "tool_calls": [...]}
#   {"type": "step", "source": "system", "message": "<a skill body, a reminder>"}
#   {"type": "observation", "results": [...]}                  <- a tool's output
#   {"type": "common_transcript", "source": "logs/...", "message": "converter started"}
#
# So: prose is ``message`` on a ``step`` that a person or the mind spoke. Everything else is the
# record AROUND the conversation, and each exclusion is one class of nonsense result the user
# reported -- a tool's name and the paths in its input (``tool_calls``, never read), a tool's
# output (an ``observation``, a different event type), the machine's own log lines, and the text a
# framework injected that nobody ever read (``source: "system"``, which is how a skill's body used
# to come back as a hit). None of it needs prose parsed out of it; it is simply not read.
_PROSE_FIELD: Final[str] = "message"
_SPOKEN_EVENT_TYPE: Final[str] = "step"
_SPOKEN_SOURCES: Final[frozenset[str]] = frozenset({"user", "agent"})

_TRANSCRIPT_GLOB: Final[str] = "events/*/common_transcript/events.jsonl"

# The one directory the glob above reaches that is not a harness: the converter's own log, which
# is written in the same shape beside the transcripts and is not a conversation.
_LOG_EMITTER_DIR: Final[str] = "logs"


def transcript_paths(host_dir: Path, agent_id: str) -> list[Path]:
    """Every transcript file one agent has written, newest first."""
    agent_events = host_dir / "agents" / agent_id
    if not agent_events.is_dir():
        return []
    paths = [path for path in agent_events.glob(_TRANSCRIPT_GLOB) if path.parent.parent.name != _LOG_EMITTER_DIR]
    return sorted(paths, key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)


class TranscriptTail(FrozenModel):
    """The end of one transcript as the scan sees it: lowercased, and where in the file it starts.

    Only the lowercased bytes are kept -- the scan is a plain ``find`` over them, which is the
    fastest thing Python does to a byte string -- and the line a hit sits on is read back from
    the file in its own case for the snippet. Hits are rare and bounded; the scan is every
    keystroke.
    """

    path: Path = Field(description="The transcript this is the end of")
    offset: int = Field(ge=0, description="Where in the file the tail starts, in bytes")
    lowered: bytes = Field(description="The tail, lowercased for the scan")


def _read_tail(path: Path, max_bytes: int) -> TranscriptTail:
    """The end of the file, bounded; an unreadable file is nothing rather than an error."""
    if max_bytes <= 0:
        return TranscriptTail(path=path, offset=0, lowered=b"")
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                # The seek lands mid-line; that partial line is dropped rather than mis-parsed.
                handle.readline()
            offset = handle.tell()
            return TranscriptTail(path=path, offset=offset, lowered=handle.read().lower())
    except OSError as e:
        logger.debug("Skipped an unreadable transcript at {}: {}", path, e)
        return TranscriptTail(path=path, offset=0, lowered=b"")


def _read_span(handle: BinaryIO, offset: int, length: int) -> bytes:
    """``length`` bytes of the file from ``offset``, as written; nothing when they cannot be read."""
    try:
        handle.seek(offset)
        return handle.read(length)
    except OSError as e:
        logger.debug("Skipped an unreadable transcript line at {}: {}", handle.name, e)
        return b""


class _CachedTail(FrozenModel):
    """One transcript's tail as it was last read, with what tells whether the file has moved on."""

    mtime_ns: int = Field(description="The file's mtime when the tail was read")
    size: int = Field(ge=0, description="The file's size when the tail was read")
    tail: TranscriptTail = Field(description="The tail that was read")
    max_bytes: int = Field(ge=0, description="How much the reader was allowed at the time")


class _CachedListing(FrozenModel):
    """One agent's transcript files, and when they were listed."""

    listed_at: float = Field(description="``time.monotonic()`` when the glob ran")
    paths: tuple[Path, ...] = Field(description="The transcript files, newest first")


class TranscriptCache(MutableModel):
    """What the search has already read, so the next keystroke costs a stat rather than a read.

    A tail is reused while the file's size and mtime are what they were when it was read and the
    caller is not allowed more of the file than was read then. The cache holds at most
    ``max_bytes`` of tails, dropping the least recently used past that, so it can never hold more
    than one search is allowed to read. Safe to share between request threads.
    """

    max_bytes: int = Field(ge=0, frozen=True, description="The most the cache may hold, in bytes of tails")
    listing_ttl_seconds: float = Field(
        default=TRANSCRIPT_LISTING_TTL_SECONDS,
        ge=0,
        frozen=True,
        description="How long an agent's transcript listing is trusted before it is globbed again",
    )
    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _tails: OrderedDict[Path, _CachedTail] = PrivateAttr(default_factory=OrderedDict)
    _held_bytes: int = PrivateAttr(default=0)
    _listings: dict[tuple[Path, str], _CachedListing] = PrivateAttr(default_factory=dict)

    @property
    def held_bytes(self) -> int:
        """How much the cache holds right now, in bytes of tails."""
        with self._lock:
            return self._held_bytes

    def transcript_paths(self, host_dir: Path, agent_id: str) -> list[Path]:
        """Every transcript file one agent has written, newest first; globbed at most once per TTL."""
        key = (host_dir, agent_id)
        now = time.monotonic()
        with self._lock:
            listing = self._listings.get(key)
            if listing is not None and now - listing.listed_at < self.listing_ttl_seconds:
                return list(listing.paths)
        paths = transcript_paths(host_dir, agent_id)
        with self._lock:
            self._listings[key] = _CachedListing(listed_at=now, paths=tuple(paths))
        return paths

    def tail(self, path: Path, max_bytes: int) -> TranscriptTail:
        """The end of the file, bounded, from memory when the file has not changed since."""
        try:
            stat = path.stat()
        except OSError:
            return _read_tail(path, max_bytes)
        with self._lock:
            cached = self._tails.get(path)
            if (
                cached is not None
                and cached.mtime_ns == stat.st_mtime_ns
                and cached.size == stat.st_size
                and (cached.max_bytes >= max_bytes or cached.tail.offset == 0)
            ):
                self._tails.move_to_end(path)
                return cached.tail
        tail = _read_tail(path, max_bytes)
        with self._lock:
            self._forget_locked(path)
            if len(tail.lowered) <= self.max_bytes:
                self._tails[path] = _CachedTail(
                    mtime_ns=stat.st_mtime_ns, size=stat.st_size, tail=tail, max_bytes=max_bytes
                )
                self._held_bytes += len(tail.lowered)
                while self._held_bytes > self.max_bytes:
                    oldest, _ = next(iter(self._tails.items()))
                    self._forget_locked(oldest)
        return tail

    def _forget_locked(self, path: Path) -> None:
        dropped = self._tails.pop(path, None)
        if dropped is not None:
            self._held_bytes -= len(dropped.tail.lowered)


def _line_bounds(blob: bytes, index: int) -> tuple[int, int]:
    """Where the JSONL line the byte at ``index`` sits in starts and ends, in ``blob``."""
    start = blob.rfind(b"\n", 0, index) + 1
    end = blob.find(b"\n", index)
    return (start, len(blob) if end < 0 else end)


def _snippet_around(text: str, query: str) -> MatchSnippet | None:
    """The part of ``text`` around the first occurrence of ``query``, on one line.

    Cut at word boundaries where it is cut at all, so the row reads as words rather than opening
    or closing mid-word (``nt `agent-f8...`` was the complaint); an edge that was cut wears an
    ellipsis. The match itself is never cut, however long the query.
    """
    index = text.lower().find(query.lower())
    if index < 0:
        return None
    start = max(0, index - SNIPPET_MARGIN)
    end = min(len(text), index + len(query) + SNIPPET_MARGIN)
    if start > 0:
        first_space = text.find(" ", start, index)
        if first_space >= 0:
            start = first_space + 1
    if end < len(text):
        last_space = text.rfind(" ", index + len(query), end)
        if last_space >= 0:
            end = last_space
    leading = "…" if start > 0 else ""
    trailing = "…" if end < len(text) else ""
    return MatchSnippet(f"{leading}{text[start:end]}{trailing}"[:MAX_MATCH_SNIPPET_LENGTH])


def _spoken_text_of(event: dict[str, Any]) -> str:
    """What was said in one transcript event: its prose, or nothing when it is not a turn at all."""
    if event.get("type") != _SPOKEN_EVENT_TYPE or event.get("source") not in _SPOKEN_SOURCES:
        return ""
    spoken = event.get(_PROSE_FIELD)
    return spoken if isinstance(spoken, str) else ""


def snippet_from_transcript_line(line: bytes, query: SearchQuery) -> MatchSnippet | None:
    """The snippet one matching line yields, or None when its prose does not hold the query.

    A line whose raw bytes hold the query but whose prose does not was matched inside a tool
    payload or an event id, which is not something the user said: it is passed over rather than
    shown as a hit with no readable context.
    """
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    return _snippet_around(_spoken_text_of(event), str(query))


class TranscriptSearchOutcome(FrozenModel):
    """What searching one chat came to: what was found, and how much of the chat had to be read."""

    snippet: MatchSnippet | None = Field(description="The matching line, or None when nothing matched")
    bytes_read: int = Field(ge=0, description="How much transcript this search read, for the caller's budget")


def _snippet_in_tail(tail: TranscriptTail, query: SearchQuery) -> MatchSnippet | None:
    """The first thing said in one transcript that matches, or None when nothing said matches.

    The needle is looked for in the whole tail at once, which is a scan rather than a parse; only
    the line each hit sits on is read back from the file and decoded. A hit inside a tool payload
    or an event id is stepped over -- it is not something anyone said -- up to a bounded number
    of times, so a word that happens to appear in a lot of machine output cannot turn one search
    into a long one. A line the file no longer holds as it was scanned (it was rewritten
    underneath) does not decode, and is stepped over the same way.
    """
    needle = str(query).lower().encode("utf-8", errors="ignore")
    if not needle:
        return None
    index = tail.lowered.find(needle)
    if index < 0:
        return None
    # Opened once for however many hits are stepped over: on a sandboxed filesystem the open is
    # the expensive part of a small read.
    try:
        handle = tail.path.open("rb")
    except OSError as e:
        logger.debug("Skipped an unreadable transcript at {}: {}", tail.path, e)
        return None
    with handle:
        for _ in range(MAX_LINES_DECODED_PER_TRANSCRIPT):
            if index < 0:
                return None
            start, end = _line_bounds(tail.lowered, index)
            snippet = snippet_from_transcript_line(_read_span(handle, tail.offset + start, end - start), query)
            if snippet is not None:
                return snippet
            index = tail.lowered.find(needle, index + len(needle))
    return None


def search_transcripts(
    host_dir: Path, agent_ids: Sequence[str], query: SearchQuery, max_bytes_to_read: int, cache: TranscriptCache
) -> TranscriptSearchOutcome:
    """The first thing said in these agents' transcripts that matches, newest agent first.

    ``max_bytes_to_read`` is how much of this chat may be read; a chat longer than that is
    searched from its end, which is where what the user is reaching for nearly always is. A
    transcript the ``cache`` already holds costs a stat rather than a read.
    """
    bytes_read = 0
    for agent_id in reversed(list(agent_ids)):
        for path in cache.transcript_paths(host_dir, agent_id):
            tail = cache.tail(path, max_bytes_to_read - bytes_read)
            bytes_read += len(tail.lowered)
            snippet = _snippet_in_tail(tail, query)
            if snippet is not None:
                return TranscriptSearchOutcome(snippet=snippet, bytes_read=bytes_read)
    return TranscriptSearchOutcome(snippet=None, bytes_read=bytes_read)
