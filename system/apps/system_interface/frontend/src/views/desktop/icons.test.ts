import { describe, expect, it } from "vitest";

import { appRecord } from "../../testing/records";
import {
  CELL_HEIGHT,
  CELL_WIDTH,
  GRID_ORIGIN_X,
  GRID_ORIGIN_Y,
  MAKE_SOMETHING_ENTRY,
  cellAt,
  cellOrigin,
  desktopEntries,
  iconGridFor,
  launchableApps,
  nearestFreeCell,
  placedIcons,
  cellContaining,
  withIconMovedTo,
  withRoomMadeAt,
} from "./icons";

const APPS = [
  appRecord("terminal", { launcher_rank: 40 }),
  appRecord("chat", { launcher_rank: 10 }),
  appRecord("roadmap"),
  appRecord("files", { launcher_rank: 20 }),
  appRecord("system_interface", { internal: true }),
];

function names(entries: { name: string }[]): string[] {
  return entries.map((entry) => entry.name);
}

describe("launchableApps", () => {
  it("is every app the user can open, ranked ones first and an unranked one after them", () => {
    expect(names(launchableApps(APPS))).toEqual(["chat", "files", "terminal", "roadmap"]);
  });
});

describe("desktopEntries", () => {
  it("leads with Make something and then follows the registry, so a new app appears on its own", () => {
    expect(names(desktopEntries(APPS))).toEqual([MAKE_SOMETHING_ENTRY, "chat", "files", "terminal", "roadmap"]);
  });

  it("calls each app what the app calls itself", () => {
    const entries = desktopEntries([appRecord("chat", { display_name: "Chat" })]);
    expect(entries[1].label).toBe("Chat");
  });
});

describe("the desktop's grid", () => {
  it("spans the whole desktop, so an icon can live at the far edge", () => {
    const grid = iconGridFor({ width: 1440, height: 848 });
    expect(grid.columns).toBe(Math.floor((1440 - GRID_ORIGIN_X) / CELL_WIDTH));
    expect(grid.rows).toBe(Math.floor((848 - GRID_ORIGIN_Y) / CELL_HEIGHT));
    expect(grid.columns).toBeGreaterThan(6);
  });

  it("is never empty, however small the desktop gets", () => {
    expect(iconGridFor({ width: 10, height: 10 })).toEqual({ columns: 1, rows: 1 });
  });

  it("knows the cell a point lies in, as against the nearest corner", () => {
    const grid = iconGridFor({ width: 1000, height: 800 });
    // Deep inside the second cell of the first row: the corner-nearest reading would round it
    // up to the third, but the point is IN the second.
    expect(cellContaining(36 + 90 + 80, 36 + 10, grid)).toEqual({ column: 1, row: 0 });
    expect(cellContaining(0, 0, grid)).toEqual({ column: 0, row: 0 });
  });

  it("snaps a point to the cell it falls in, not to the pixel", () => {
    const grid = iconGridFor({ width: 1440, height: 848 });
    expect(cellAt(GRID_ORIGIN_X + 4, GRID_ORIGIN_Y + 4, grid)).toEqual({ column: 0, row: 0 });
    // Just past halfway into the next cell rounds on to it.
    expect(cellAt(GRID_ORIGIN_X + CELL_WIDTH * 2 + 6, GRID_ORIGIN_Y + CELL_HEIGHT + 8, grid)).toEqual({
      column: 2,
      row: 1,
    });
    // A point off the edge lands on the last cell rather than outside the grid.
    expect(cellAt(99999, 99999, grid)).toEqual({ column: grid.columns - 1, row: grid.rows - 1 });
  });
});

describe("nearestFreeCell", () => {
  const grid = { columns: 4, rows: 4 };

  it("is the cell asked for when nothing is in it", () => {
    expect(nearestFreeCell({ column: 2, row: 2 }, new Set(), grid)).toEqual({ column: 2, row: 2 });
  });

  it("steps to the closest free cell rather than stacking two icons", () => {
    const taken = new Set(["2,2"]);
    const found = nearestFreeCell({ column: 2, row: 2 }, taken, grid);
    expect(found).not.toEqual({ column: 2, row: 2 });
    expect(Math.hypot(found.column - 2, found.row - 2)).toBeLessThanOrEqual(1);
  });
});

describe("placedIcons", () => {
  const entries = desktopEntries(APPS);
  const DESKTOP = { width: 1440, height: 848 };

  it("flows an untouched desktop along the top row, in order", () => {
    const placed = placedIcons(entries, { cell_by_entry: {} }, DESKTOP);

    expect(names(placed.map((icon) => icon.entry))).toEqual(names(entries));
    expect(placed.map((icon) => icon.cell)).toEqual([
      { column: 0, row: 0 },
      { column: 1, row: 0 },
      { column: 2, row: 0 },
      { column: 3, row: 0 },
      { column: 4, row: 0 },
    ]);
    // Drawn at the cell's own corner, not at wherever a pointer happened to be.
    expect(placed[0]).toMatchObject(cellOrigin({ column: 0, row: 0 }));
  });

  it("puts an icon in the cell it was given, anywhere on the desktop", () => {
    const far = { column: 11, row: 5 };
    const placed = placedIcons(entries, { cell_by_entry: { terminal: far } }, DESKTOP);

    const terminal = placed.find((icon) => icon.entry.name === "terminal");
    expect(terminal?.cell).toEqual(far);
    expect(terminal).toMatchObject(cellOrigin(far));
  });

  it("never lets two icons share a cell, whatever the file says", () => {
    const placed = placedIcons(
      entries,
      { cell_by_entry: { chat: { column: 2, row: 2 }, files: { column: 2, row: 2 } } },
      DESKTOP,
    );

    const cells = placed.map((icon) => `${icon.cell.column},${icon.cell.row}`);
    expect(new Set(cells).size).toBe(cells.length);
    // The first to claim it keeps it.
    expect(placed.find((icon) => icon.entry.name === "chat")?.cell).toEqual({ column: 2, row: 2 });
  });

  it("brings an icon whose cell fell off a narrowed desktop back on screen", () => {
    const arrangement = { cell_by_entry: { chat: { column: 12, row: 6 } } };
    const grid = iconGridFor({ width: 500, height: 400 });

    const narrow = placedIcons(entries, arrangement, { width: 500, height: 400 });

    for (const icon of narrow) {
      expect(icon.cell.column).toBeLessThan(grid.columns);
      expect(icon.cell.row).toBeLessThan(grid.rows);
    }
    // The saved arrangement is untouched, so the wide desktop gets it back exactly.
    expect(arrangement.cell_by_entry.chat).toEqual({ column: 12, row: 6 });
    const wide = placedIcons(entries, arrangement, DESKTOP);
    expect(wide.find((icon) => icon.entry.name === "chat")?.cell).toEqual({ column: 12, row: 6 });
  });

  it("ignores a cell saved for something that is no longer installed", () => {
    const placed = placedIcons(entries, { cell_by_entry: { gone: { column: 0, row: 0 } } }, DESKTOP);
    expect(names(placed.map((icon) => icon.entry))).toEqual(names(entries));
    expect(placed[0].cell).toEqual({ column: 0, row: 0 });
  });
});

describe("withRoomMadeAt", () => {
  // Five columns wide, a few rows tall: cells read 1..5 on the first row, 6..10 on the next.
  const area = { width: 36 + 90 * 5 + 10, height: 36 + 106 * 3 + 10 };
  const entry = (name: string) => ({ name, label: name.toUpperCase(), app: null });
  const at = (column: number, row: number) => ({ column, row });

  it("held further along, slides the icons in between one place back and takes the cell held over", () => {
    const entries = [entry("2"), entry("3"), entry("4"), entry("5")];
    const arrangement = { cell_by_entry: { "2": at(1, 0), "3": at(2, 0), "4": at(3, 0), "5": at(4, 0) } };
    const placed = placedIcons(entries, arrangement, area);

    const made = withRoomMadeAt(arrangement, placed, "2", at(3, 0), area);

    expect(made.cell_by_entry["3"]).toEqual(at(1, 0));
    expect(made.cell_by_entry["4"]).toEqual(at(2, 0));
    expect(made.cell_by_entry["5"]).toEqual(at(4, 0));
    expect(made.cell_by_entry["2"]).toEqual(at(3, 0));
    // And drawn, nobody shares a cell: the held icon is really in the cell it is held over.
    const drawn = placedIcons(entries, made, area);
    expect(new Set(drawn.map((icon) => `${icon.cell.column},${icon.cell.row}`)).size).toBe(4);
  });

  it("held earlier, slides the icons in between one place on", () => {
    const entries = [entry("1"), entry("2"), entry("3"), entry("4")];
    const arrangement = { cell_by_entry: { "1": at(0, 0), "2": at(1, 0), "3": at(2, 0), "4": at(3, 0) } };
    const placed = placedIcons(entries, arrangement, area);

    const made = withRoomMadeAt(arrangement, placed, "4", at(1, 0), area);

    expect(made.cell_by_entry["2"]).toEqual(at(2, 0));
    expect(made.cell_by_entry["3"]).toEqual(at(3, 0));
    expect(made.cell_by_entry["1"]).toEqual(at(0, 0));
    expect(made.cell_by_entry["4"]).toEqual(at(1, 0));
  });

  it("moves the icon under the hand even when the held icon comes first in the desktop's order", () => {
    // Make something, Terminal side by side; Make something held over Terminal. Terminal takes
    // the cell Make something left, and is not put straight back by a tie for it.
    const entries = [entry("make-something"), entry("terminal")];
    const arrangement = { cell_by_entry: { "make-something": at(1, 1), terminal: at(0, 1) } };
    const placed = placedIcons(entries, arrangement, area);

    const made = withRoomMadeAt(arrangement, placed, "make-something", at(0, 1), area);

    expect(made.cell_by_entry.terminal).toEqual(at(1, 1));
    expect(made.cell_by_entry["make-something"]).toEqual(at(0, 1));
    const drawn = placedIcons(entries, made, area);
    expect(drawn.find((icon) => icon.entry.name === "terminal")?.cell).toEqual(at(1, 1));
  });

  it("slides the other way when the default would carry an icon onto another row and the other way keeps every row", () => {
    // Terminal at the start of the second row, Make something after it; Files, on the first
    // row, held over Terminal. Sliding Terminal back would lift it onto the first row, so
    // Terminal and Make something slide on along their own row instead.
    const entries = [entry("chats"), entry("files"), entry("terminal"), entry("make")];
    const arrangement = {
      cell_by_entry: { chats: at(0, 0), files: at(1, 0), terminal: at(0, 1), make: at(1, 1) },
    };
    const placed = placedIcons(entries, arrangement, area);

    const made = withRoomMadeAt(arrangement, placed, "files", at(0, 1), area);

    expect(made.cell_by_entry.terminal).toEqual(at(1, 1));
    expect(made.cell_by_entry.make).toEqual(at(2, 1));
    expect(made.cell_by_entry.chats).toEqual(at(0, 0));
    expect(made.cell_by_entry.files).toEqual(at(0, 1));
  });

  it("keeps the default when the other way would flip a row too", () => {
    // A full second row: sliding on would push its last icon onto a third row, so the default
    // (back onto the first row) stands.
    const entries = [entry("a"), entry("b"), entry("c"), entry("d"), entry("e"), entry("f")];
    const arrangement = {
      cell_by_entry: { a: at(0, 0), b: at(0, 1), c: at(1, 1), d: at(2, 1), e: at(3, 1), f: at(4, 1) },
    };
    const placed = placedIcons(entries, arrangement, area);

    const made = withRoomMadeAt(arrangement, placed, "a", at(0, 1), area);

    expect(made.cell_by_entry.b).toEqual(at(4, 0));
    expect(made.cell_by_entry.f).toEqual(at(4, 1));
  });

  it("skips over a free cell in between", () => {
    const entries = [entry("2"), entry("4")];
    const arrangement = { cell_by_entry: { "2": at(1, 0), "4": at(3, 0) } };
    const placed = placedIcons(entries, arrangement, area);

    const made = withRoomMadeAt(arrangement, placed, "2", at(3, 0), area);

    expect(made.cell_by_entry["4"]).toEqual(at(2, 0));
  });

  it("changes nothing when held over its own cell", () => {
    const entries = [entry("a"), entry("b")];
    const arrangement = { cell_by_entry: { a: at(0, 0), b: at(1, 0) } };
    const placed = placedIcons(entries, arrangement, area);

    const made = withRoomMadeAt(arrangement, placed, "a", at(0, 0), area);

    expect(made.cell_by_entry).toEqual({ a: at(0, 0), b: at(1, 0) });
  });
});

describe("withIconMovedTo", () => {
  const entries = desktopEntries(APPS);
  const DESKTOP = { width: 1440, height: 848 };
  const empty = { cell_by_entry: {} };
  const placed = placedIcons(entries, empty, DESKTOP);

  it("snaps the drop to a cell rather than keeping the pointer's pixel", () => {
    const wanted = { column: 6, row: 3 };
    const near = cellOrigin(wanted);

    const moved = withIconMovedTo(empty, placed, "terminal", near.x + 11, near.y + 9, DESKTOP);

    expect(moved.cell_by_entry.terminal).toEqual(wanted);
  });

  it("goes to the nearest free cell when the one under the pointer is taken", () => {
    // chat is in the first cell of the flow; dropping terminal on it must not stack them.
    const first = cellOrigin({ column: 0, row: 0 });

    const moved = withIconMovedTo(empty, placed, "terminal", first.x, first.y, DESKTOP);

    expect(moved.cell_by_entry.terminal).not.toEqual({ column: 0, row: 0 });
    const cells = Object.values(moved.cell_by_entry).map((cell) => `${cell.column},${cell.row}`);
    expect(new Set(cells).size).toBe(cells.length);
  });

  it("writes every icon's cell, so installing an app does not shuffle the arrangement", () => {
    const moved = withIconMovedTo(empty, placed, "terminal", 800, 400, DESKTOP);
    expect(Object.keys(moved.cell_by_entry).sort()).toEqual(names(entries).sort());
  });

  it("keeps the cell the user gave an icon that is only displaced by a narrow desktop", () => {
    const arrangement = { cell_by_entry: { chat: { column: 12, row: 6 } } };
    const narrow = { width: 500, height: 400 };
    const displaced = placedIcons(entries, arrangement, narrow);

    // Moving a different icon while the desktop is narrow must not overwrite chat's real cell.
    const moved = withIconMovedTo(arrangement, displaced, "terminal", 100, 100, narrow);

    expect(moved.cell_by_entry.chat).toEqual({ column: 12, row: 6 });
  });
});
