/**
 * The desktop document: what one client's arrangement of one view holds (contracts.md section 6).
 *
 * The windows in stacking order (last is topmost), where the launcher icons sit, whether the dock
 * hides itself, and the desktop area the geometry was laid out against. The shell writes the same
 * shape for an agent's op, so this file and ``shell/desktop_document.py`` are two readers of one
 * contract; the wire spelling is the shell's snake_case throughout.
 */

/** Where a window sits on the desktop, in CSS pixels from the desktop area's top left. */
export interface WindowRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/* Two shapes a saved desktop may still carry instead of cells, both read forward so that nobody's
   arrangement is lost to a change of mind about how icons are placed. The numbers are the grids
   those shapes were written against. */
/** The six-column block icons were first pinned to, as a flat index. */
const LEGACY_FLAT_COLUMNS = 6;
/** The cell the desktop's grid uses, which the free pixel positions are read against. */
const LEGACY_CELL_WIDTH = 90;
const LEGACY_CELL_HEIGHT = 106;
const LEGACY_ORIGIN_X = 36;
const LEGACY_ORIGIN_Y = 36;

/** The cell a flat index named in the six-column block. */
function cellOfFlatIndex(index: number): GridCell {
  return { column: index % LEGACY_FLAT_COLUMNS, row: Math.floor(index / LEGACY_FLAT_COLUMNS) };
}

/** The cell a free pixel position falls nearest to. */
function cellOfPosition(x: number, y: number): GridCell {
  return {
    column: Math.max(0, Math.round((x - LEGACY_ORIGIN_X) / LEGACY_CELL_WIDTH)),
    row: Math.max(0, Math.round((y - LEGACY_ORIGIN_Y) / LEGACY_CELL_HEIGHT)),
  };
}

/** One floating window: the instance it shows, where it sits, and how it is showing it. */
export interface DesktopWindowRecord {
  /** The page's id, minted when the page was first opened and baked into its url. */
  tab_id: string;
  /** The instance the window shows. */
  address: string;
  rect: WindowRect;
  /** Out of sight with its page still loaded: the same state, to the user, as never having opened one. */
  is_minimized: boolean;
  is_maximized: boolean;
  /** Where a maximized window goes back to; null when it is not maximized. */
  restore_rect: WindowRect | null;
  /** Epoch milliseconds the window was last raised, 0 for never. */
  last_focused_ms: number;
  /**
   * What the window was called the last time its instance was listed, or null when it has never
   * been saved with one.
   *
   * A window outlives its instance leaving its app's list, and a restored window whose instance
   * is gone has no title to read -- so this is what it is called instead. The live title always
   * wins when there is one: this is a memory, not a source of truth.
   */
  last_known_title: string | null;
}

export interface DesktopSize {
  width: number;
  height: number;
}

/** One cell of the desktop's icon grid, counted from its top left. */
export interface GridCell {
  column: number;
  row: number;
}

/** Where the desktop's launcher icons sit: the cell the user put each one in. */
export interface IconArrangement {
  cell_by_entry: Record<string, GridCell>;
}

export interface DockPreferences {
  is_hiding: boolean;
}

export interface DesktopDocument {
  version: number;
  windows: DesktopWindowRecord[];
  icons: IconArrangement;
  dock: DockPreferences;
  desktop_size: DesktopSize | null;
}

export const DESKTOP_DOCUMENT_VERSION = 1;

/** The desktop a view that has never been arranged starts from. */
export function emptyDesktop(): DesktopDocument {
  return {
    version: DESKTOP_DOCUMENT_VERSION,
    windows: [],
    icons: { cell_by_entry: {} },
    dock: { is_hiding: false },
    desktop_size: null,
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

function parseRect(value: unknown): WindowRect | null {
  const record = asRecord(value);
  if (record === null) return null;
  const { x, y, width, height } = record;
  if (typeof x !== "number" || typeof y !== "number") return null;
  if (typeof width !== "number" || typeof height !== "number") return null;
  return { x, y, width, height };
}

function parseWindow(value: unknown): DesktopWindowRecord | null {
  const record = asRecord(value);
  if (record === null) return null;
  const rect = parseRect(record.rect);
  if (typeof record.tab_id !== "string" || typeof record.address !== "string" || rect === null) return null;
  return {
    tab_id: record.tab_id,
    address: record.address,
    rect,
    is_minimized: record.is_minimized === true,
    is_maximized: record.is_maximized === true,
    restore_rect: parseRect(record.restore_rect),
    last_focused_ms: typeof record.last_focused_ms === "number" ? record.last_focused_ms : 0,
    last_known_title: typeof record.last_known_title === "string" ? record.last_known_title : null,
  };
}

/**
 * Where each icon sits, read from a saved desktop.
 *
 * Three shapes are accepted, because the answer to "how are icons placed" has changed twice and
 * an arrangement the user made is not worth losing to that: cells (what is written now), free
 * pixel positions (read as the cell they fall nearest to), and the flat index into the original
 * six-column block. The next save writes cells whichever it read.
 */
function parseIcons(value: unknown): IconArrangement {
  const icons = asRecord(value);
  const cells: Record<string, GridCell> = {};

  const saved = asRecord(icons?.cell_by_entry);
  if (saved !== null) {
    for (const [entry, cell] of Object.entries(saved)) {
      const point = asRecord(cell);
      if (point === null) {
        // The flat index of the original block, which this key also used to hold.
        if (typeof cell === "number" && Number.isInteger(cell) && cell >= 0) cells[entry] = cellOfFlatIndex(cell);
        continue;
      }
      const { column, row } = point;
      if (typeof column === "number" && typeof row === "number" && column >= 0 && row >= 0) {
        cells[entry] = { column: Math.round(column), row: Math.round(row) };
      }
    }
    if (Object.keys(cells).length > 0) return { cell_by_entry: cells };
  }

  const positions = asRecord(icons?.position_by_entry);
  if (positions === null) return { cell_by_entry: {} };
  for (const [entry, position] of Object.entries(positions)) {
    const point = asRecord(position);
    if (point === null) continue;
    const { x, y } = point;
    if (typeof x === "number" && typeof y === "number" && Number.isFinite(x) && Number.isFinite(y)) {
      cells[entry] = cellOfPosition(x, y);
    }
  }
  return { cell_by_entry: cells };
}

function parseDesktopSize(value: unknown): DesktopSize | null {
  const record = asRecord(value);
  if (record === null) return null;
  const { width, height } = record;
  if (typeof width !== "number" || typeof height !== "number" || width <= 0 || height <= 0) return null;
  return { width, height };
}

/**
 * ``value`` as a desktop document, or null when it is not one.
 *
 * Read loosely on purpose: the file outlives this build, and a window whose shape this build does
 * not understand costs that window rather than the whole desktop.
 */
export function parseDesktopDocument(value: unknown): DesktopDocument | null {
  const record = asRecord(value);
  if (record === null) return null;
  const rawWindows = Array.isArray(record.windows) ? record.windows : [];
  const windows: DesktopWindowRecord[] = [];
  const seenAddresses = new Set<string>();
  for (const rawWindow of rawWindows) {
    const parsed = parseWindow(rawWindow);
    // An instance has one live page, so one window: a document naming it twice keeps the first.
    if (parsed === null || seenAddresses.has(parsed.address)) continue;
    seenAddresses.add(parsed.address);
    windows.push(parsed);
  }
  return {
    version: typeof record.version === "number" ? record.version : DESKTOP_DOCUMENT_VERSION,
    windows,
    icons: parseIcons(record.icons),
    dock: { is_hiding: asRecord(record.dock)?.is_hiding === true },
    desktop_size: parseDesktopSize(record.desktop_size),
  };
}

/** The window showing ``address``, or undefined when none does. */
export function windowForAddress(document: DesktopDocument, address: string): DesktopWindowRecord | undefined {
  return document.windows.find((window) => window.address === address);
}

/** The window carrying ``tabId``, or undefined when none does. */
export function windowForTabId(document: DesktopDocument, tabId: string): DesktopWindowRecord | undefined {
  return document.windows.find((window) => window.tab_id === tabId);
}

/** The document with one window replaced by ``replacement``; unchanged when it holds no such window. */
export function withWindow(document: DesktopDocument, replacement: DesktopWindowRecord): DesktopDocument {
  return {
    ...document,
    windows: document.windows.map((window) => (window.tab_id === replacement.tab_id ? replacement : window)),
  };
}

/** The document with one window raised to the top of the stack, the rest in their order. */
export function withRaisedWindow(document: DesktopDocument, tabId: string): DesktopDocument {
  const raised = windowForTabId(document, tabId);
  if (raised === undefined) return document;
  return {
    ...document,
    windows: [...document.windows.filter((window) => window.tab_id !== tabId), raised],
  };
}

/** The document without the window carrying ``tabId``. */
export function withoutWindow(document: DesktopDocument, tabId: string): DesktopDocument {
  return { ...document, windows: document.windows.filter((window) => window.tab_id !== tabId) };
}

/** The document with a window added on top. An address already open is not added twice. */
export function withAddedWindow(document: DesktopDocument, added: DesktopWindowRecord): DesktopDocument {
  if (windowForAddress(document, added.address) !== undefined) return document;
  return { ...document, windows: [...document.windows, added] };
}
