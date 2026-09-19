/**
 * The desktop's launcher icons: which entries there are, and which cell of the desktop's grid
 * each one sits in.
 *
 * The entries are the built-in "Make something" plus every app the registry lists that the user
 * can open, in ``launcher_rank`` order -- so an app built later appears on the desktop with no
 * code change here.
 *
 * Where they sit is the user's. The grid spans the WHOLE desktop, so an icon can live at the
 * right edge or along the bottom, not only in a block at the top left -- but it lands in a cell
 * rather than at the pixel it was dropped on, and no two icons ever share one. A cell is sized for
 * the icon plus a two-line label ("Make something" is one), so labels never collide.
 *
 * What is saved is the CELL, as a column and a row, not a pixel position: that is what survives a
 * resize meaningfully. A narrower desktop has fewer columns, so an icon whose cell falls off the
 * edge is drawn at the nearest free cell that is on screen -- and because the saved cell is never
 * rewritten by that, widening the desktop again puts the arrangement back exactly as it was.
 *
 * An entry that has never been moved flows along the top row in the default order, which is what
 * puts a newly built app at the end of the row already there.
 */

import type { IconArrangement, GridCell } from "../../models/Desktop";
import type { AppRecord } from "../../models/Inventory";

/** The desktop's own entry, which is not an app: it opens the Make something window. */
export const MAKE_SOMETHING_ENTRY = "make-something";
export const MAKE_SOMETHING_LABEL = "Make something";

/**
 * One cell of the desktop's grid: the icon's own footprint plus room to breathe.
 *
 * Sized for a two-line label rather than a one-line one, because the labels are whatever the apps
 * are called and a cell that fits "Files" but not "Make something" would have them overlapping.
 */
export const CELL_WIDTH = 90;
export const CELL_HEIGHT = 106;
/** Where the grid starts, leaving the desktop's edge clear. */
export const GRID_ORIGIN_X = 36;
export const GRID_ORIGIN_Y = 36;

/** The size of the desktop an arrangement is drawn on. */
export interface IconArea {
  width: number;
  height: number;
}

/** How many cells the desktop has room for right now. */
export interface IconGrid {
  columns: number;
  rows: number;
}

/** The grid a desktop of this size holds: as many whole cells as fit, and never fewer than one. */
export function iconGridFor(area: IconArea): IconGrid {
  return {
    columns: Math.max(1, Math.floor((area.width - GRID_ORIGIN_X) / CELL_WIDTH)),
    rows: Math.max(1, Math.floor((area.height - GRID_ORIGIN_Y) / CELL_HEIGHT)),
  };
}

/** Where a cell sits on the desktop, in CSS pixels. */
export function cellOrigin(cell: GridCell): { x: number; y: number } {
  return {
    x: GRID_ORIGIN_X + cell.column * CELL_WIDTH,
    y: GRID_ORIGIN_Y + cell.row * CELL_HEIGHT,
  };
}

/** The cell a point falls in: what a drop snaps to before anything is asked about whether it is free. */
export function cellAt(x: number, y: number, grid: IconGrid): GridCell {
  const column = Math.round((x - GRID_ORIGIN_X) / CELL_WIDTH);
  const row = Math.round((y - GRID_ORIGIN_Y) / CELL_HEIGHT);
  return clampedCell({ column, row }, grid);
}

/** The cell a point lies IN: what the pointer is over, as against the nearest cell corner. */
export function cellContaining(x: number, y: number, grid: IconGrid): GridCell {
  const column = Math.floor((x - GRID_ORIGIN_X) / CELL_WIDTH);
  const row = Math.floor((y - GRID_ORIGIN_Y) / CELL_HEIGHT);
  return clampedCell({ column, row }, grid);
}

/** The same cell, brought inside a grid that may be smaller than the one it was saved against. */
export function clampedCell(cell: GridCell, grid: IconGrid): GridCell {
  return {
    column: Math.max(0, Math.min(cell.column, grid.columns - 1)),
    row: Math.max(0, Math.min(cell.row, grid.rows - 1)),
  };
}

function cellKey(cell: GridCell): string {
  return `${cell.column},${cell.row}`;
}

function isInsideGrid(cell: GridCell, grid: IconGrid): boolean {
  return cell.column >= 0 && cell.column < grid.columns && cell.row >= 0 && cell.row < grid.rows;
}

/**
 * Every cell of the grid, nearest to ``target`` first.
 *
 * Distance is measured in cells rather than pixels, and ties break by going down before across,
 * which matches the default flow -- so an icon displaced from its cell lands where the eye is
 * already looking for it.
 */
function cellsNearest(target: GridCell, grid: IconGrid): GridCell[] {
  const cells: GridCell[] = [];
  for (let column = 0; column < grid.columns; column += 1) {
    for (let row = 0; row < grid.rows; row += 1) cells.push({ column, row });
  }
  return cells.sort((left, right) => {
    const leftDistance = Math.hypot(left.column - target.column, left.row - target.row);
    const rightDistance = Math.hypot(right.column - target.column, right.row - target.row);
    if (leftDistance !== rightDistance) return leftDistance - rightDistance;
    if (left.column !== right.column) return left.column - right.column;
    return left.row - right.row;
  });
}

/** The free cell closest to ``target``, or ``target`` itself when the grid is completely full. */
export function nearestFreeCell(target: GridCell, taken: ReadonlySet<string>, grid: IconGrid): GridCell {
  const wanted = clampedCell(target, grid);
  if (!taken.has(cellKey(wanted))) return wanted;
  return cellsNearest(wanted, grid).find((cell) => !taken.has(cellKey(cell))) ?? wanted;
}

/** Where the ``index``-th never-moved icon goes: along the top row, then the row under it. */
export function flowCell(index: number, grid: IconGrid): GridCell {
  return { column: index % grid.columns, row: Math.floor(index / grid.columns) };
}

/** One thing on the desktop, whether or not it is an app. */
export interface DesktopEntry {
  /** The entry's stable name: an app's registry name, or the built-in entry's own. */
  name: string;
  label: string;
  /** The app this entry opens, or null for the built-in entry. */
  app: AppRecord | null;
}

/** The apps a user can open, in the order the desktop lays them out. */
export function launchableApps(apps: readonly AppRecord[]): AppRecord[] {
  return apps
    .filter((app) => !app.internal)
    .slice()
    .sort(
      (left, right) =>
        (left.launcher_rank ?? Number.MAX_SAFE_INTEGER) - (right.launcher_rank ?? Number.MAX_SAFE_INTEGER),
    );
}

/** Every desktop entry, in their default order: Make something, then every app by rank. */
export function desktopEntries(apps: readonly AppRecord[]): DesktopEntry[] {
  return [
    { name: MAKE_SOMETHING_ENTRY, label: MAKE_SOMETHING_LABEL, app: null },
    ...launchableApps(apps).map((app) => ({ name: app.name, label: app.display_name, app })),
  ];
}

/** One icon as the desktop draws it: what it is, which cell it is in, and where that cell sits. */
export interface PlacedIcon {
  entry: DesktopEntry;
  cell: GridCell;
  x: number;
  y: number;
}

/**
 * Every entry in a cell: the one the user put it in, else the nearest free one to it, else the
 * next cell of the default flow.
 *
 * Two icons never share a cell, whatever the file says: the first entry to claim one keeps it and
 * anything else goes to the nearest free cell. A cell saved for an entry that no longer exists
 * (an app that was removed) simply frees up, and a cell that no longer exists on a narrower
 * desktop is drawn at the nearest one that does -- without the saved cell being touched, so the
 * arrangement comes back whole when the desktop is wide again.
 */
export function placedIcons(
  entries: readonly DesktopEntry[],
  arrangement: IconArrangement,
  area: IconArea,
): PlacedIcon[] {
  const grid = iconGridFor(area);
  const taken = new Set<string>();
  const placed = new Map<string, GridCell>();

  // The icons the user placed take their cells first, so a never-moved icon can never push one of
  // them out of the cell it was put in.
  for (const entry of entries) {
    const saved = arrangement.cell_by_entry[entry.name];
    if (saved === undefined || !isInsideGrid(saved, grid)) continue;
    const cell = nearestFreeCell(saved, taken, grid);
    taken.add(cellKey(cell));
    placed.set(entry.name, cell);
  }
  // Then the ones whose saved cell is off this desktop, near where it would have been...
  for (const entry of entries) {
    if (placed.has(entry.name)) continue;
    const saved = arrangement.cell_by_entry[entry.name];
    if (saved === undefined) continue;
    const cell = nearestFreeCell(clampedCell(saved, grid), taken, grid);
    taken.add(cellKey(cell));
    placed.set(entry.name, cell);
  }
  // ...and last the ones that have never been moved, which flow in the default order.
  let flowed = 0;
  for (const entry of entries) {
    if (placed.has(entry.name)) continue;
    let candidate = flowCell(flowed, grid);
    flowed += 1;
    while (taken.has(cellKey(candidate)) && flowed < grid.columns * grid.rows) {
      candidate = flowCell(flowed, grid);
      flowed += 1;
    }
    const cell = nearestFreeCell(candidate, taken, grid);
    taken.add(cellKey(cell));
    placed.set(entry.name, cell);
  }

  return entries.map((entry) => {
    const cell = placed.get(entry.name) ?? { column: 0, row: 0 };
    return { entry, cell, ...cellOrigin(cell) };
  });
}

/**
 * The arrangement after one icon is dropped at a point: it takes the nearest free cell.
 *
 * Written over the SAVED arrangement rather than over what is on screen, so an icon that is
 * merely being drawn somewhere else -- because its own cell is off the edge of a narrowed desktop
 * -- keeps the cell the user actually gave it. Every placed icon's cell is written, so installing
 * an app never shuffles the ones already arranged.
 */
export function withIconMovedTo(
  arrangement: IconArrangement,
  placed: readonly PlacedIcon[],
  entryName: string,
  x: number,
  y: number,
  area: IconArea,
): IconArrangement {
  const grid = iconGridFor(area);
  const taken = new Set(placed.filter((icon) => icon.entry.name !== entryName).map((icon) => cellKey(icon.cell)));
  const dropped = nearestFreeCell(cellAt(x, y, grid), taken, grid);
  const cells: Record<string, GridCell> = { ...arrangement.cell_by_entry };
  for (const icon of placed) {
    // An icon drawn away from its saved cell keeps the saved one: it is only displaced for as
    // long as the desktop is too small for it.
    const saved = arrangement.cell_by_entry[icon.entry.name];
    cells[icon.entry.name] = saved !== undefined && !isInsideGrid(saved, grid) ? saved : icon.cell;
  }
  cells[entryName] = dropped;
  return { cell_by_entry: cells };
}

/** A cell's place in reading order: along the row, then the next row. */
function flowIndex(cell: GridCell, grid: IconGrid): number {
  return cell.row * grid.columns + cell.column;
}

/** The cell at a place in reading order. */
function cellAtFlowIndex(index: number, grid: IconGrid): GridCell {
  return { column: index % grid.columns, row: Math.floor(index / grid.columns) };
}

/**
 * The arrangement while ``entryName`` is held over ``target``, as an insertion in reading order
 * (along each row, then the next). The held icon takes the cell it is held over, and the icons
 * in the way make room one of two ways:
 *
 * - the default: the icons between where it was picked up and where it is held slide one place
 *   toward the cell it left (back when held further along, on when held earlier), and nothing
 *   else moves;
 * - when that would carry an icon onto another row, the icons from the held cell outward slide
 *   one place the OTHER way instead, along the run of occupied cells until the first free one --
 *   if THAT keeps every icon on its row and on the grid. Otherwise the default stands.
 *
 * A free cell in between is skipped over either way.
 */
export function withRoomMadeAt(
  arrangement: IconArrangement,
  placed: readonly PlacedIcon[],
  entryName: string,
  target: GridCell,
  area: IconArea,
): IconArrangement {
  const grid = iconGridFor(area);
  const wanted = clampedCell(target, grid);
  const cells: Record<string, GridCell> = { ...arrangement.cell_by_entry };
  for (const icon of placed) {
    const saved = arrangement.cell_by_entry[icon.entry.name];
    cells[icon.entry.name] = saved !== undefined && !isInsideGrid(saved, grid) ? saved : icon.cell;
  }
  const held = placed.find((icon) => icon.entry.name === entryName);
  if (held === undefined) return { cell_by_entry: cells };
  const from = flowIndex(held.cell, grid);
  const to = flowIndex(wanted, grid);
  if (from === to) return { cell_by_entry: cells };
  const others = placed.filter((icon) => icon.entry.name !== entryName);
  const moves = slideBetween(others, from, to, grid);
  const isRowFlip = (move: { icon: PlacedIcon; index: number }): boolean =>
    cellAtFlowIndex(move.index, grid).row !== move.icon.cell.row;
  let chosen = moves;
  if (moves.some(isRowFlip)) {
    const away = slideAway(others, from, to, grid);
    if (away !== null && !away.some(isRowFlip)) chosen = away;
  }
  for (const move of chosen) cells[move.icon.entry.name] = cellAtFlowIndex(move.index, grid);
  cells[entryName] = wanted;
  return { cell_by_entry: cells };
}

interface IconMove {
  icon: PlacedIcon;
  index: number;
}

/** The default: the icons between ``from`` and ``to`` slide one place toward ``from``. */
function slideBetween(others: readonly PlacedIcon[], from: number, to: number, grid: IconGrid): IconMove[] {
  const step = to > from ? -1 : 1;
  const low = Math.min(from, to);
  const high = Math.max(from, to);
  return others.flatMap((icon) => {
    const index = flowIndex(icon.cell, grid);
    const isBetween = to > from ? index > low && index <= high : index >= low && index < high;
    return isBetween ? [{ icon, index: index + step }] : [];
  });
}

/** The other way: the icon in the held cell and the run of occupied cells beyond it, away from
 *  ``from``, each slide one place further away. Null when the run would leave the grid. */
function slideAway(others: readonly PlacedIcon[], from: number, to: number, grid: IconGrid): IconMove[] | null {
  const step = to > from ? 1 : -1;
  const byIndex = new Map(others.map((icon) => [flowIndex(icon.cell, grid), icon]));
  const moves: IconMove[] = [];
  let index = to;
  while (byIndex.has(index)) {
    const next = index + step;
    if (next < 0 || next >= grid.columns * grid.rows) return null;
    moves.push({ icon: byIndex.get(index) as PlacedIcon, index: next });
    index = next;
  }
  return moves;
}
