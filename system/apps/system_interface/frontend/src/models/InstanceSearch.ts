/**
 * Searching everything the user has started (contracts.md section 6).
 *
 * The dock shows only what is running, so this is the way back to anything that was stopped. The
 * shell answers with both halves at once: instances whose title matches, and instances whose own
 * contents match, from the apps that can look inside themselves.
 */

import { apiUrl } from "@imbue/workspace-ui/src/base-path";
import type { AppRecord } from "./Inventory";

/** Why a result matched: its title, or something inside it. */
export type MatchedOn = "title" | "content";

/** One thing the user has started that a query found. */
export interface SearchResult {
  app: string;
  app_display_name: string;
  icon: string;
  label: string;
  key: string;
  url: string;
  title: string;
  status: string;
  matched_on: MatchedOn;
  /** The matching line, for a content match; empty for a title match. */
  snippet: string;
}

function asResult(value: unknown): SearchResult | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const strings = ["app", "app_display_name", "key", "url", "title", "status"] as const;
  if (strings.some((field) => typeof record[field] !== "string")) return null;
  const matchedOn = record.matched_on === "content" ? "content" : "title";
  return {
    app: record.app as string,
    app_display_name: record.app_display_name as string,
    icon: typeof record.icon === "string" ? record.icon : "",
    label: typeof record.label === "string" ? record.label : "",
    key: record.key as string,
    url: record.url as string,
    title: record.title as string,
    status: record.status as string,
    matched_on: matchedOn,
    snippet: typeof record.snippet === "string" ? record.snippet : "",
  };
}

/** What the desktop shows under a result: the line that matched, or which app it belongs to. */
export function resultSubtitle(result: SearchResult): string {
  return result.matched_on === "content" && result.snippet !== "" ? result.snippet : result.app_display_name;
}

/** A run of text in a result row, and whether it is the part the query matched (drawn bold). */
export interface TextSegment {
  text: string;
  isMatch: boolean;
}

/** ``text`` cut into what matched ``query`` and what did not, case-insensitively, in order. */
export function highlightedSegments(text: string, query: string): TextSegment[] {
  const needle = query.trim().toLowerCase();
  if (needle === "" || text === "") return text === "" ? [] : [{ text, isMatch: false }];
  const segments: TextSegment[] = [];
  const lowered = text.toLowerCase();
  let from = 0;
  for (let at = lowered.indexOf(needle); at >= 0; at = lowered.indexOf(needle, from)) {
    if (at > from) segments.push({ text: text.slice(from, at), isMatch: false });
    segments.push({ text: text.slice(at, at + needle.length), isMatch: true });
    from = at + needle.length;
  }
  if (from < text.length) segments.push({ text: text.slice(from), isMatch: false });
  return segments;
}

/** About how many characters of a snippet a row shows before clipping the rest. */
const SNIPPET_VISIBLE_CHARS = 40;
/** How much of a snippet is kept before its match once the lead-in has to be cut. */
const SNIPPET_LEAD_CHARS = 16;

/**
 * The snippet with its match in view: a row shows one line and clips the rest, so a match deep
 * in the line would be hidden behind the ellipsis. The lead-in is cut to a few words, at a word
 * boundary, and an ellipsis says so. A snippet whose match is already within what the row shows
 * is left as it is.
 */
export function snippetInView(snippet: string, query: string, leadChars: number = SNIPPET_LEAD_CHARS): string {
  const needle = query.trim().toLowerCase();
  if (needle === "") return snippet;
  const at = snippet.toLowerCase().indexOf(needle);
  if (at < 0 || at + needle.length <= SNIPPET_VISIBLE_CHARS) return snippet;
  const earliest = at - leadChars;
  const wordStart = snippet.indexOf(" ", earliest);
  const start = wordStart >= 0 && wordStart < at ? wordStart + 1 : earliest;
  return `…${snippet.slice(start)}`;
}

/** Whether a result is something that has been stopped, which the row says so the user knows. */
export function isStoppedResult(result: SearchResult): boolean {
  return result.status === "stopped";
}

/**
 * The title half of the answer, from the instance list the page already holds: what the shell
 * itself would say for the same query (every instance of every app the user can open whose
 * title contains it, most recently active first), only with no round trip. The box paints these
 * the instant a key is pressed, and the shell's answer -- the same rows plus what was found
 * inside things -- replaces them when it lands.
 */
export function titleMatchesFromInventory(apps: readonly AppRecord[], query: string): SearchResult[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return [];
  const matched: { at: number; result: SearchResult }[] = [];
  for (const app of apps) {
    if (app.internal) continue;
    for (const instance of app.instances) {
      if (!instance.title.toLowerCase().includes(needle)) continue;
      const at = instance.last_active === null ? 0 : Date.parse(instance.last_active);
      matched.push({
        at: Number.isFinite(at) ? at : 0,
        result: {
          app: app.name,
          app_display_name: app.display_name,
          icon: app.icon,
          label: app.label,
          key: instance.key,
          url: instance.url,
          title: instance.title,
          status: instance.status,
          matched_on: "title",
          snippet: "",
        },
      });
    }
  }
  return matched.sort((left, right) => right.at - left.at).map((candidate) => candidate.result);
}

/**
 * Everything the user has started that matches. A search the shell could not answer is no results
 * rather than an error: the box asks again on the next keystroke.
 */
export async function searchEverything(query: string): Promise<SearchResult[]> {
  try {
    const response = await fetch(apiUrl(`/api/search?q=${encodeURIComponent(query)}`));
    if (!response.ok) return [];
    const body = (await response.json()) as { results?: unknown };
    if (!Array.isArray(body.results)) return [];
    return body.results.flatMap((value) => {
      const result = asResult(value);
      return result === null ? [] : [result];
    });
  } catch {
    return [];
  }
}
