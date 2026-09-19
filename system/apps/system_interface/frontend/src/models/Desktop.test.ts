import { describe, expect, it } from "vitest";

import { emptyDesktop, parseDesktopDocument, windowForAddress, windowForTabId } from "./Desktop";

const FILES = "app:files?instance=files-1";

const SAVED = {
  version: 1,
  windows: [
    {
      tab_id: "tab-0000000000000001",
      address: FILES,
      rect: { x: 10, y: 20, width: 800, height: 600 },
      is_minimized: true,
      is_maximized: false,
      restore_rect: null,
      last_focused_ms: 99,
      last_known_title: "Notes",
    },
  ],
  icons: { cell_by_entry: { files: { column: 3, row: 1 } } },
  dock: { is_hiding: true },
  desktop_size: { width: 1440, height: 900 },
};

describe("parseDesktopDocument", () => {
  it("reads a saved desktop back exactly as it was written", () => {
    const parsed = parseDesktopDocument(SAVED);
    expect(parsed).toEqual(SAVED);
  });

  it("is not a desktop for anything that is not one", () => {
    expect(parseDesktopDocument(null)).toBeNull();
    expect(parseDesktopDocument("a desktop")).toBeNull();
  });

  it("defaults everything a document does not say", () => {
    const parsed = parseDesktopDocument({});
    expect(parsed).toEqual(emptyDesktop());
  });

  it("drops a window it cannot read rather than the whole desktop", () => {
    const parsed = parseDesktopDocument({
      windows: [
        { tab_id: "tab-0000000000000001", address: FILES, rect: { x: 0, y: 0, width: 400, height: 300 } },
        { address: "app:terminal", rect: { x: 0, y: 0, width: 400, height: 300 } },
        { tab_id: "tab-0000000000000003", address: "app:terminal" },
        "not a window",
      ],
    });
    expect(parsed?.windows.map((window) => window.address)).toEqual([FILES]);
  });

  it("has no remembered title for a window that was saved without one", () => {
    // Every desktop written before the title was remembered, and every window whose instance was
    // never listed under a name: both read back as "nothing remembered" rather than as unreadable.
    const parsed = parseDesktopDocument({
      windows: [{ tab_id: "tab-0000000000000001", address: FILES, rect: { x: 0, y: 0, width: 400, height: 300 } }],
    });
    expect(parsed?.windows[0].last_known_title).toBeNull();
  });

  it("reads the two older ways icons were placed, rather than losing the arrangement", () => {
    // The first: a flat index into the six-column block they were originally pinned to.
    const indexed = parseDesktopDocument({ icons: { cell_by_entry: { chat: 0, files: 7 } } });
    expect(indexed?.icons.cell_by_entry.chat).toEqual({ column: 0, row: 0 });
    expect(indexed?.icons.cell_by_entry.files).toEqual({ column: 1, row: 1 });

    // The second: free pixel positions, read as the cell they fall nearest to.
    const free = parseDesktopDocument({
      icons: { position_by_entry: { chat: { x: 36, y: 36 }, terminal: { x: 306, y: 142 } } },
    });
    expect(free?.icons.cell_by_entry.chat).toEqual({ column: 0, row: 0 });
    expect(free?.icons.cell_by_entry.terminal).toEqual({ column: 3, row: 1 });
  });

  it("keeps one window per instance: one page cannot be in two windows", () => {
    const parsed = parseDesktopDocument({
      windows: [
        { tab_id: "tab-0000000000000001", address: FILES, rect: { x: 0, y: 0, width: 400, height: 300 } },
        { tab_id: "tab-0000000000000002", address: FILES, rect: { x: 9, y: 9, width: 400, height: 300 } },
      ],
    });
    expect(parsed?.windows).toHaveLength(1);
    expect(parsed?.windows[0].tab_id).toBe("tab-0000000000000001");
  });

  it("ignores an icon cell that is not a cell", () => {
    const parsed = parseDesktopDocument({
      icons: { cell_by_entry: { files: { column: 1, row: 2 }, chat: { column: "x", row: 1 }, terminal: null } },
    });
    expect(parsed?.icons.cell_by_entry).toEqual({ files: { column: 1, row: 2 } });
  });

  it("ignores a desktop size that could not have been measured", () => {
    expect(parseDesktopDocument({ desktop_size: { width: 0, height: 900 } })?.desktop_size).toBeNull();
    expect(parseDesktopDocument({ desktop_size: "wide" })?.desktop_size).toBeNull();
  });
});

describe("finding a window", () => {
  it("finds it by what it shows and by the page it carries", () => {
    const document = parseDesktopDocument(SAVED);
    expect(document).not.toBeNull();
    expect(windowForAddress(document!, FILES)?.tab_id).toBe("tab-0000000000000001");
    expect(windowForTabId(document!, "tab-0000000000000001")?.address).toBe(FILES);
    expect(windowForAddress(document!, "app:terminal")).toBeUndefined();
  });
});
