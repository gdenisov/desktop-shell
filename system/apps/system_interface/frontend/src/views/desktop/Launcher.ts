/**
 * The ``+`` menu: what the field beside the ``+`` offers, above it.
 *
 * Before anything is typed it is the menu -- a row per app, the recently used ones first, and
 * for the chat both "Chats" and "New Chat" (see ``launcherMenu.ts``). Typing turns it into the
 * search: the app rows the query names stay, and under them come the things the query found by
 * title or by what was said inside them, with the matching text in bold and brought into view.
 * Search is the only way back to something that has been stopped -- the dock shows what is
 * running -- so a stopped result says so, and opening it is what starts it.
 */

import m from "mithril";
import type { SearchResult } from "../../models/InstanceSearch";
import { highlightedSegments, isStoppedResult, resultSubtitle, snippetInView } from "../../models/InstanceSearch";
import type { LauncherItem } from "../../models/launcherMenu";
import { appIconMarkup } from "../components/appIcon";
import { glyph } from "./glyphs";
import { tintForApp } from "./tints";

export interface LauncherAttrs {
  isOpen: boolean;
  query: string;
  /** The menu rows the query keeps (all of them while nothing is typed). */
  items: LauncherItem[];
  /** What the query found; empty while the query is empty or nothing matches. */
  results: SearchResult[];
  /** Whether the shell has still to answer the query that is in the field now. */
  isSearching: boolean;
  onPickItem: (item: LauncherItem) => void;
  onOpenResult: (result: SearchResult) => void;
}

/** What the field says before anything is typed into it. */
export const SEARCH_PLACEHOLDER = "Search for something to open";
const ROW_GLYPH_SIZE = 14;

function tileStyle(tint: { background: string; foreground: string }): string {
  return `background:${tint.background};color:${tint.foreground}`;
}

/** Text with the query's matches in bold. */
function highlighted(text: string, query: string): m.Children {
  return highlightedSegments(text, query).map((segment) =>
    segment.isMatch ? m("mark.result-match", segment.text) : segment.text,
  );
}

/** The glyph on a "new" row: a plus, since it starts something rather than opening what exists. */
function newGlyphMarkup(): string {
  return glyph("plus", 2.2);
}

function itemRow(item: LauncherItem, query: string, onPick: (item: LauncherItem) => void): m.Vnode {
  const tint = tintForApp(item.app.name);
  const markup =
    item.kind === "new"
      ? newGlyphMarkup()
      : appIconMarkup(item.app.icon, ROW_GLYPH_SIZE, glyph("app", 1.8), item.app.name);
  return m(
    "button.result-row.launcher-item",
    {
      key: item.key,
      "data-item": item.key,
      "data-entry": item.app.name,
      "data-kind": item.kind,
      onclick: () => onPick(item),
    },
    [
      m("span.result-tile", { style: tileStyle(tint) }, m.trust(markup)),
      m("span.result-text", m("span.result-title", highlighted(item.label, query))),
    ],
  );
}

/** One search result: what it is, what was matched in it, and whether it is stopped. */
export function resultRow(result: SearchResult, query: string, onOpen: (result: SearchResult) => void): m.Vnode {
  const tint = tintForApp(result.app);
  const subtitle =
    result.matched_on === "content" ? snippetInView(resultSubtitle(result), query) : resultSubtitle(result);
  return m(
    "button.result-row",
    {
      key: `${result.app}:${result.key}`,
      "data-address": `${result.app}:${result.key}`,
      onclick: () => onOpen(result),
    },
    [
      m(
        "span.result-tile",
        { style: tileStyle(tint) },
        m.trust(appIconMarkup(result.icon, ROW_GLYPH_SIZE, glyph("app", 1.8), result.app)),
      ),
      m("span.result-text", [
        m("span.result-title", highlighted(result.title, query)),
        m("span.result-sub", highlighted(subtitle, query)),
      ]),
      isStoppedResult(result) ? m("span.result-state", "stopped") : null,
    ],
  );
}

export function Launcher(): m.Component<LauncherAttrs> {
  return {
    view(vnode) {
      const attrs = vnode.attrs;
      const isSearching = attrs.query.trim() !== "";
      const rows = attrs.items.map((item) => itemRow(item, attrs.query, attrs.onPickItem));
      return m("div#launcher", { class: attrs.isOpen ? "is-open" : "" }, [
        rows.length > 0 ? m("div#launcher-items.launcher-items", rows) : null,
        isSearching
          ? m(
              "div#dock-results.launcher-results",
              { class: rows.length > 0 ? "launcher-results--after-items" : "" },
              attrs.results.length > 0
                ? attrs.results.map((result) => resultRow(result, attrs.query, attrs.onOpenResult))
                : m("div.launcher-empty", attrs.isSearching ? "Looking..." : "Nothing matches"),
            )
          : null,
      ]);
    },
  };
}
