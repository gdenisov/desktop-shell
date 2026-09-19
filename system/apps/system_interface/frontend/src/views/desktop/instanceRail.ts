/**
 * The list of an app's instances down the left of its window: the chat list.
 *
 * An app that lists its own instances (``browses_instances``) gets this rail inside every window
 * of it, beside the page: every instance the app lists, with its status, the one the window
 * shows marked, and starting a new one at the head. Picking a row shows that instance in the
 * SAME window -- the page it was showing stays loaded, unshown, so coming back is instant --
 * rather than opening a second window. It is the desktop's, not the app's: the app stays a
 * plain page per instance, and the rail reads the inventory the desktop already keeps.
 *
 * The list is ordered by recency: the instance most recently active leads, one never active
 * trails -- except one started from this rail, which leads until its first activity. An instance
 * another agent started (a background worker, labelled ``agent_created`` and ``lead_agent`` by
 * the app) is indented a step and sits right under its starter, and the pair moves together.
 *
 * The rail COLLAPSES to a strip of message-bubble monograms and status marks on a toggle (never
 * under the pointer), and open it is RESIZABLE by the strip down its right edge; both are kept
 * per browser. A row is renameable in place (double-click, or the pencil under the pointer), and
 * a right-click opens a menu: rename, stop or restart, delete (which asks first). A stopped
 * instance stays in the list faded, wearing a pause mark; one being deleted is crossed out in
 * red with a shredder at work until the list drops it.
 */

import m from "mithril";
import { setHoverTooltip } from "@imbue/workspace-ui/src/components/hoverTooltip";
import { menuCardClass, menuDividerClass, menuRowClass } from "@imbue/workspace-ui/src/components/menu";
import { addressFor } from "@imbue/workspace-ui/src/addresses";
import type { AppRecord, InstanceRecord } from "../../models/Inventory";
import { deleteInstance, renameInstance, setInstanceLifecycle } from "../../models/Relay";
import { isUnread, markRead } from "./chatUnread";
import { clearPendingTitle, pendingTitle, setPendingTitle } from "./pendingTitles";

const COLLAPSED_STORAGE_KEY = "chat-sidebar-collapsed";
const WIDTH_STORAGE_KEY = "chat-sidebar-width";
const DEFAULT_WIDTH_PX = 240;
const MIN_WIDTH_PX = 168;
const MAX_WIDTH_PX = 480;

/** One row of the rail: the part of an instance record the rail reads, plus where it lives. */
interface RailRow {
  address: string;
  key: string;
  title: string;
  status: string;
  labels: Record<string, string>;
  lastActiveMs: number | null;
}

export interface InstanceRailAttrs {
  app: AppRecord;
  /** The window the rail is in, for the state that is per window. */
  tabId: string;
  /** The address the window shows: the selected row. */
  shownAddress: string;
  /** Whether the window is on screen, so what it shows counts as seen. */
  isOnScreen: boolean;
  /** Show another instance of the app in this window. */
  onPick: (address: string) => void;
  /** Start a new instance and show it in this window. */
  onNew: () => void;
}

function rowOf(app: AppRecord, instance: InstanceRecord): RailRow {
  const parsed = instance.last_active === null ? Number.NaN : Date.parse(instance.last_active);
  return {
    address: addressFor(app.name, instance.key),
    key: instance.key,
    title: instance.title,
    status: instance.status,
    labels: instance.labels ?? {},
    lastActiveMs: Number.isNaN(parsed) ? null : parsed,
  };
}

/** The status each dot stands for, and nothing else: an unknown status reads as idle. Working is
 *  a blue dot that breathes (and the title shimmers with it); done -- finished a turn the user
 *  has not looked at, see ``chatUnread`` -- is a green check, with the title bold and green too;
 *  attention -- the agent is waiting on the user -- is orange; idle is a hollow grey ring; error
 *  is red. A stopped instance wears a pause mark instead of a dot (see ``statusDot``). */
const DOT_CLASS_BY_STATUS: Record<string, string> = {
  working: "chat-sidebar-dot--pulse bg-info",
  attention: "bg-warning",
  error: "bg-danger",
  idle: "border border-faint bg-transparent",
};

function dotClassFor(status: string): string {
  return DOT_CLASS_BY_STATUS[status] ?? DOT_CLASS_BY_STATUS.idle;
}

function displayStatus(row: RailRow): string {
  return row.status === "idle" && isUnread(row.address) ? "done" : row.status;
}

function isAgentStarted(row: RailRow): boolean {
  return row.labels.agent_created === "true";
}

function monogramOf(row: RailRow): string {
  const title = titleOf(row).trim();
  return title === "" ? "?" : title.slice(0, 1).toUpperCase();
}

// ---------- ordering ----------

/** The instances started from this rail: new by definition, on top until their first activity. */
const startedHereKeys = new Set<string>();

export function noteStartedHere(address: string): void {
  startedHereKeys.add(address);
}

function recencyKey(row: RailRow): number {
  if (row.lastActiveMs !== null) return row.lastActiveMs;
  return startedHereKeys.has(row.address) ? Number.POSITIVE_INFINITY : 0;
}

function byRecency(rows: RailRow[]): RailRow[] {
  return [...rows].sort((first, second) => recencyKey(second) - recencyKey(first));
}

/** The row that started ``row``, when listed: the one whose key -- or whose agent, for a chat that
 *  moved to a new agent and kept its old ones (``agent_ids``) -- the ``lead_agent`` label names. A
 *  helper of a helper is filed under the root of that chain, so the list is one level deep. */
function ownsAgent(row: RailRow, agentId: string): boolean {
  if (row.key === agentId) return true;
  const agentIds = row.labels.agent_ids;
  return agentIds !== undefined && agentIds.split(",").includes(agentId);
}

function parentOf(row: RailRow, rows: RailRow[]): RailRow | null {
  let current = row;
  let parent: RailRow | null = null;
  for (let depth = 0; depth < rows.length; depth += 1) {
    const leadAgent = current.labels.lead_agent;
    if (!isAgentStarted(current) || leadAgent === undefined) break;
    const next = rows.find((candidate) => candidate.key !== current.key && ownsAgent(candidate, leadAgent));
    if (next === undefined) break;
    parent = next;
    current = next;
  }
  return parent;
}

function grouped(rows: RailRow[]): RailRow[] {
  const helpersByParent = new Map<string, RailRow[]>();
  const roots: RailRow[] = [];
  for (const row of rows) {
    const parent = parentOf(row, rows);
    if (parent === null) roots.push(row);
    else helpersByParent.set(parent.key, [...(helpersByParent.get(parent.key) ?? []), row]);
  }
  const groups = roots.map((root) => ({ root, helpers: byRecency(helpersByParent.get(root.key) ?? []) }));
  const latestOf = (group: { root: RailRow; helpers: RailRow[] }): number =>
    Math.max(recencyKey(group.root), ...group.helpers.map(recencyKey));
  groups.sort((first, second) => latestOf(second) - latestOf(first));
  return groups.flatMap((group) => [group.root, ...group.helpers]);
}

// ---------- per-browser rail state ----------

function isCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function setCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(COLLAPSED_STORAGE_KEY, collapsed ? "true" : "false");
  } catch {
    /* Nothing to do: the rail works, it just will not be remembered. */
  }
}

function openWidth(): number {
  try {
    const stored = Number(window.localStorage.getItem(WIDTH_STORAGE_KEY));
    if (Number.isFinite(stored) && stored >= MIN_WIDTH_PX && stored <= MAX_WIDTH_PX) return stored;
  } catch {
    /* Fall through to the default. */
  }
  return DEFAULT_WIDTH_PX;
}

function setOpenWidth(width: number): void {
  try {
    window.localStorage.setItem(WIDTH_STORAGE_KEY, String(width));
  } catch {
    /* Nothing to do. */
  }
}

let dragState: { pointerId: number; startX: number; startWidth: number } | null = null;

function beginResize(event: PointerEvent): void {
  if (event.button !== 0) return;
  event.preventDefault();
  dragState = { pointerId: event.pointerId, startX: event.clientX, startWidth: openWidth() };
  (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
}

function continueResize(event: PointerEvent): void {
  if (dragState === null || event.pointerId !== dragState.pointerId) return;
  const width = Math.min(
    MAX_WIDTH_PX,
    Math.max(MIN_WIDTH_PX, dragState.startWidth + event.clientX - dragState.startX),
  );
  setOpenWidth(width);
  m.redraw();
}

function endResize(event: PointerEvent): void {
  if (dragState === null || event.pointerId !== dragState.pointerId) return;
  dragState = null;
}

// ---------- titles, renames, deletes, menus ----------

function titleOf(row: RailRow): string {
  return pendingTitle(row.address) ?? row.title;
}

interface RenameState {
  address: string;
  draft: string;
  error: string | null;
}

let rename: RenameState | null = null;

function beginRename(row: RailRow): void {
  rename = { address: row.address, draft: titleOf(row), error: null };
}

function cancelRename(): void {
  rename = null;
}

/** Keep the typed name: the field closes at once and the row (and the window's title bar) show
 *  the name from now; the app takes seconds, and a refusal brings the field back with the reason. */
function commitRename(app: AppRecord, row: RailRow): void {
  if (rename === null || rename.address !== row.address) return;
  const title = rename.draft.trim();
  rename = null;
  m.redraw();
  if (title === "" || title === row.title) return;
  setPendingTitle(row.address, title);
  renameInstance(app.name, row.key, title).catch((error: unknown) => {
    clearPendingTitle(row.address);
    if (rename !== null) {
      m.redraw();
      return;
    }
    rename = { address: row.address, draft: title, error: error instanceof Error ? error.message : String(error) };
    m.redraw();
  });
}

const deletingAddresses = new Set<string>();

function isDeleting(row: RailRow): boolean {
  return deletingAddresses.has(row.address);
}

function pruneDeleting(rows: RailRow[]): void {
  for (const address of deletingAddresses) {
    if (!rows.some((row) => row.address === address)) deletingAddresses.delete(address);
  }
}

interface MenuState {
  address: string;
  x: number;
  y: number;
}

let menu: MenuState | null = null;

function openMenu(row: RailRow, event: MouseEvent): void {
  event.preventDefault();
  menu = { address: row.address, x: event.clientX, y: event.clientY };
}

function closeMenu(): void {
  menu = null;
}

function setRunningFromMenu(app: AppRecord, row: RailRow, isRunning: boolean): void {
  closeMenu();
  m.redraw();
  setInstanceLifecycle(app.name, row.key, isRunning ? "start" : "stop").catch((error: unknown) => {
    const verb = isRunning ? "restart" : "stop";
    alert(`Could not ${verb} "${titleOf(row)}".\n\n${error instanceof Error ? error.message : String(error)}`);
  });
}

/** Delete from the menu, after asking. When it is the instance this window shows, the window
 *  moves to the next one in the list first, so it is not left on a page whose instance is gone. */
function deleteFromMenu(attrs: InstanceRailAttrs, row: RailRow, rows: RailRow[]): void {
  closeMenu();
  m.redraw();
  const isConfirmed = window.confirm(
    `Delete "${titleOf(row)}"?\n\nThis ends its agent and removes its conversation. It cannot be undone.`,
  );
  if (!isConfirmed) return;
  const next = rows.find((candidate) => candidate.address !== row.address && !isDeleting(candidate));
  deletingAddresses.add(row.address);
  m.redraw();
  if (row.address === attrs.shownAddress && next !== undefined) attrs.onPick(next.address);
  deleteInstance(attrs.app.name, row.key).catch((error: unknown) => {
    deletingAddresses.delete(row.address);
    m.redraw();
    alert(`Could not delete "${titleOf(row)}".\n\n${error instanceof Error ? error.message : String(error)}`);
  });
}

function keepMenuOnScreen(dom: Element): void {
  const element = dom as HTMLElement;
  const rect = element.getBoundingClientRect();
  const overflowX = rect.right - window.innerWidth;
  const overflowY = rect.bottom - window.innerHeight;
  if (overflowX > 0) element.style.left = `${Math.max(0, rect.left - overflowX)}px`;
  if (overflowY > 0) element.style.top = `${Math.max(0, rect.top - overflowY)}px`;
}

function rowMenu(attrs: InstanceRailAttrs, row: RailRow, state: MenuState, rows: RailRow[]): m.Vnode {
  const isStopped = row.status === "stopped";
  const rowClass = menuRowClass({ extra: "text-(length:--font-size-row) text-primary" });
  return m(
    "div",
    {
      class: `chat-sidebar-menu ${menuCardClass("fixed min-w-36")}`,
      style: `left: ${state.x}px; top: ${state.y}px`,
      role: "menu",
      oncreate: ({ dom }: m.VnodeDOM) => keepMenuOnScreen(dom),
      onclick: (event: MouseEvent) => event.stopPropagation(),
      oncontextmenu: (event: MouseEvent) => event.preventDefault(),
    },
    [
      m(
        "button",
        {
          class: rowClass,
          role: "menuitem",
          "data-menu-item": "rename",
          onclick: () => {
            closeMenu();
            beginRename(row);
          },
        },
        "Rename",
      ),
      m(
        "button",
        {
          class: rowClass,
          role: "menuitem",
          "data-menu-item": isStopped ? "start" : "stop",
          onclick: () => setRunningFromMenu(attrs.app, row, isStopped),
        },
        isStopped ? "Restart chat" : "Stop chat",
      ),
      m("div", { class: menuDividerClass() }),
      m(
        "button",
        {
          class: menuRowClass({ extra: "text-(length:--font-size-row) text-danger" }),
          role: "menuitem",
          "data-menu-item": "delete",
          onclick: () => deleteFromMenu(attrs, row, rows),
        },
        "Delete chat",
      ),
    ],
  );
}

function closeMenuOnClickAway(): void {
  if (menu === null) return;
  closeMenu();
  m.redraw();
}

function closeMenuOnEscape(event: KeyboardEvent): void {
  if (event.key !== "Escape" || menu === null) return;
  closeMenu();
  m.redraw();
}

// ---------- glyphs ----------

function panelGlyph(which: "open" | "close"): m.Vnode {
  const arrow = which === "open" ? "m10 15-3-3 3-3" : "m8 9 3 3-3 3";
  return m(
    "svg",
    {
      class: "chat-sidebar-panel-glyph",
      width: 16,
      height: 16,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "data-panel": which,
    },
    [m("rect", { width: 18, height: 18, x: 3, y: 3, rx: 2 }), m("path", { d: "M15 3v18" }), m("path", { d: arrow })],
  );
}

function plusGlyph(): m.Vnode {
  return m(
    "svg",
    {
      width: 16,
      height: 16,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    },
    [m("path", { d: "M12 5v14" }), m("path", { d: "M5 12h14" })],
  );
}

/** Lucide's message-circle (ISC), behind a collapsed row's letter. */
function messageBubbleGlyph(): m.Vnode {
  return m(
    "svg",
    {
      class: "chat-sidebar-bubble absolute inset-0 size-full",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 1.25,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    },
    m("path", { d: "M7.9 20A9 9 0 1 0 4 16.1L2 22Z" }),
  );
}

/** Lucide's corner-down-right (ISC), in front of a helper. */
function nestArrowGlyph(): m.Vnode {
  return m(
    "svg",
    {
      class: "chat-sidebar-nest-arrow -mr-1 ml-1 flex-none text-faint",
      width: 12,
      height: 12,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
    },
    [m("polyline", { points: "15 10 20 15 15 20" }), m("path", { d: "M4 4v7a4 4 0 0 0 4 4h12" })],
  );
}

/** Lucide's pencil (ISC), on the rename button a row shows under the pointer. */
function pencilGlyph(): m.Vnode {
  return m(
    "svg",
    {
      width: 14,
      height: 14,
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
    },
    [
      m("path", {
        d: "M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z",
      }),
      m("path", { d: "m15 5 4 4" }),
    ],
  );
}

/** Lucide's shredder (ISC), at work while an instance is being deleted; the motion is in style.css. */
function shredderGlyph(row: RailRow): m.Vnode {
  const clipId = `chat-sidebar-shred-clip-${row.key}`;
  return m(
    "svg",
    {
      class: "chat-sidebar-dot chat-sidebar-shredder -mx-1 size-4 flex-none",
      "data-status": "deleting",
      "data-glyph": "shredder",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": 2,
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
    },
    [
      m("defs", m("clipPath", { id: clipId }, m("rect", { x: 0, y: 0, width: 24, height: 13 }))),
      m("g", { "clip-path": `url(#${clipId})` }, [
        m("g", { class: "chat-sidebar-shredder-sheet" }, [
          m("path", { d: "M20 13V7l-5-5H6a2 2 0 0 0-2 2v9" }),
          m("path", { d: "M14 2v4a2 2 0 0 0 2 2h4" }),
        ]),
      ]),
      m("path", { d: "M2 13h20" }),
      m("path", { class: "chat-sidebar-shredder-strip", d: "M6 17v3", pathLength: 1 }),
      m("path", { class: "chat-sidebar-shredder-strip", d: "M10 17v5", pathLength: 1 }),
      m("path", { class: "chat-sidebar-shredder-strip", d: "M14 17v2", pathLength: 1 }),
      m("path", { class: "chat-sidebar-shredder-strip", d: "M18 17v3", pathLength: 1 }),
    ],
  );
}

/** The status mark beside a row: a dot in the status's colour, a green check for done, or a
 *  pause mark for a stopped instance -- paused is what a stop is, since a message resumes it. */
function statusDot(row: RailRow, extraClass: string): m.Vnode {
  const status = displayStatus(row);
  if (status === "done") {
    return m(
      "svg",
      {
        class: `chat-sidebar-dot -mx-0.5 size-3 text-success ${extraClass}`,
        "data-status": status,
        "data-glyph": "check",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        "stroke-width": 3,
        "stroke-linecap": "round",
        "stroke-linejoin": "round",
        "aria-hidden": "true",
      },
      m("path", { d: "M20 6 9 17l-5-5" }),
    );
  }
  if (status === "stopped") {
    return m(
      "svg",
      {
        class: `chat-sidebar-dot size-2 rounded-full text-faint ${extraClass}`,
        "data-status": status,
        "data-glyph": "pause",
        viewBox: "0 0 24 24",
        fill: "currentColor",
        "aria-hidden": "true",
      },
      [
        m("rect", { x: 4, y: 3, width: 5, height: 18, rx: 1 }),
        m("rect", { x: 15, y: 3, width: 5, height: 18, rx: 1 }),
      ],
    );
  }
  return m("span", {
    class: `chat-sidebar-dot size-2 rounded-full ${dotClassFor(status)} ${extraClass}`,
    "data-status": status,
  });
}

/** Hand the title's visible width to its style, for the glint's fixed-speed sweep (see style.css). */
function measureTitle(dom: Element): void {
  const element = dom as HTMLElement;
  const range = document.createRange();
  range.selectNodeContents(element);
  const textWidth = Math.ceil(range.getBoundingClientRect().width);
  const width = Math.min(textWidth, element.clientWidth);
  element.style.setProperty("--chat-title-width", String(width > 0 ? width : textWidth));
}

function tooltipBeside(element: Element, text: string | null): void {
  setHoverTooltip(element, text, "right");
}

// ---------- the component ----------

export const InstanceRail: m.Component<InstanceRailAttrs> = {
  oncreate() {
    document.addEventListener("click", closeMenuOnClickAway);
    document.addEventListener("keydown", closeMenuOnEscape);
  },
  onremove() {
    document.removeEventListener("click", closeMenuOnClickAway);
    document.removeEventListener("keydown", closeMenuOnEscape);
    menu = null;
  },
  view({ attrs }) {
    const collapsed = isCollapsed();
    const rows = grouped(attrs.app.instances.map((instance) => rowOf(attrs.app, instance)));
    pruneDeleting(rows);
    if (attrs.isOnScreen) markRead(attrs.shownAddress);
    return m(
      "nav",
      {
        class: [
          "chat-sidebar relative flex h-full flex-none flex-col border-r border-default bg-surface",
          collapsed ? "chat-sidebar--collapsed w-13" : "",
        ].join(" "),
        style: collapsed ? undefined : `width: ${openWidth()}px`,
        "aria-label": "Chats",
      },
      [
        m(
          "div",
          {
            class: [
              "chat-sidebar-head flex flex-none gap-1 p-2",
              collapsed ? "flex-col items-stretch" : "items-center justify-between",
            ].join(" "),
          },
          // Collapsed, the toggle leads: the way back out of the strip sits where the eye lands first,
          // and the plus under it.
          ((): m.Children => {
            const newButton = m(
              "button",
              {
                class: [
                  "chat-sidebar-new flex flex-none items-center gap-2 rounded-md",
                  "px-2 py-1.5 text-(length:--font-size-row) text-secondary hover:bg-fill-hover hover:text-primary",
                  collapsed ? "justify-center" : "",
                ].join(" "),
                "aria-label": "New chat",
                oncreate: ({ dom }: m.VnodeDOM) => tooltipBeside(dom, collapsed ? "New chat" : null),
                onupdate: ({ dom }: m.VnodeDOM) => tooltipBeside(dom, collapsed ? "New chat" : null),
                onclick: () => attrs.onNew(),
              },
              [plusGlyph(), collapsed ? null : m("span", "New chat")],
            );
            const toggle = m(
              "button",
              {
                class:
                  "chat-sidebar-toggle flex flex-none items-center justify-center rounded-md p-1.5 text-faint hover:bg-fill-hover",
                "aria-label": collapsed ? "Show chat titles" : "Hide chat titles",
                oncreate: ({ dom }: m.VnodeDOM) =>
                  tooltipBeside(dom, collapsed ? "Show chat titles" : "Hide chat titles"),
                onupdate: ({ dom }: m.VnodeDOM) =>
                  tooltipBeside(dom, collapsed ? "Show chat titles" : "Hide chat titles"),
                onclick: () => {
                  setCollapsed(!collapsed);
                  m.redraw();
                },
              },
              panelGlyph(collapsed ? "close" : "open"),
            );
            return collapsed ? [toggle, newButton] : [newButton, toggle];
          })(),
        ),
        m(
          "div",
          { class: "chat-sidebar-list min-h-0 flex-1 overflow-y-auto px-2 pb-2" },
          rows.map((row) => railRow(attrs, row, rows, collapsed)),
        ),
        menu === null ? null : rowMenuFor(attrs, rows, menu),
        collapsed
          ? null
          : m("div", {
              class:
                "chat-sidebar-resize absolute top-0 bottom-0 -right-1 w-2 cursor-col-resize touch-none " +
                "hover:bg-accent/30",
              "aria-hidden": "true",
              onpointerdown: beginResize,
              onpointermove: continueResize,
              onpointerup: endResize,
              onpointercancel: endResize,
            }),
      ],
    );
  },
};

function rowMenuFor(attrs: InstanceRailAttrs, rows: RailRow[], state: MenuState): m.Vnode | null {
  const row = rows.find((candidate) => candidate.address === state.address);
  return row === undefined ? null : rowMenu(attrs, row, state, rows);
}

function railRow(attrs: InstanceRailAttrs, row: RailRow, rows: RailRow[], collapsed: boolean): m.Vnode {
  const isSelected = row.address === attrs.shownAddress;
  const isRenaming = !collapsed && rename !== null && rename.address === row.address;
  if (isRenaming) return renameRow(attrs, row, isSelected);
  return m(
    "button",
    {
      key: row.address,
      class: [
        "chat-sidebar-row group flex w-full items-center gap-2 rounded-md py-1.5 text-left",
        collapsed ? "justify-center px-1" : isAgentStarted(row) ? "chat-sidebar-row--nested pr-2 pl-2.5" : "px-2",
        isDeleting(row)
          ? "chat-sidebar-row--deleting text-danger line-through opacity-50"
          : displayStatus(row) === "done"
            ? `chat-sidebar-row--done font-semibold text-success ${isSelected ? "chat-sidebar-row--selected bg-fill-active" : "hover:bg-fill-hover"}`
            : isSelected
              ? "chat-sidebar-row--selected bg-fill-active text-primary"
              : "text-primary hover:bg-fill-hover",
        isSelected && isDeleting(row) ? "chat-sidebar-row--selected bg-fill-active" : "",
        row.status === "stopped" ? "chat-sidebar-row--stopped opacity-50" : "",
      ].join(" "),
      "data-chat-id": row.key,
      "data-status": displayStatus(row),
      "aria-current": isSelected ? "true" : undefined,
      "aria-disabled": isDeleting(row) ? "true" : undefined,
      oncreate: ({ dom }: m.VnodeDOM) => tooltipBeside(dom, collapsed ? titleOf(row) : null),
      onupdate: ({ dom }: m.VnodeDOM) => tooltipBeside(dom, collapsed ? titleOf(row) : null),
      onclick: () => {
        if (isSelected) return;
        attrs.onPick(row.address);
      },
      ondblclick: collapsed
        ? undefined
        : () => {
            beginRename(row);
          },
      oncontextmenu: (event: MouseEvent) => {
        if (isDeleting(row)) {
          event.preventDefault();
          return;
        }
        openMenu(row, event);
      },
    },
    collapsed
      ? [
          m("span", { class: "chat-sidebar-monogram relative flex size-7 items-center justify-center" }, [
            messageBubbleGlyph(),
            m("span", { class: "relative pt-0.5 text-[10px] leading-none font-bold" }, monogramOf(row)),
            statusDot(row, "absolute -right-0.5 -bottom-0.5 ring-2 ring-surface"),
          ]),
        ]
      : [
          isAgentStarted(row) ? nestArrowGlyph() : null,
          isDeleting(row) ? shredderGlyph(row) : statusDot(row, "flex-none"),
          m(
            "span",
            {
              class: [
                "chat-sidebar-title min-w-0 flex-1 truncate text-(length:--font-size-row)",
                row.status === "working" ? "chat-sidebar-title--working" : "",
              ].join(" "),
              oncreate: ({ dom }: m.VnodeDOM) => measureTitle(dom),
              onupdate: ({ dom }: m.VnodeDOM) => measureTitle(dom),
            },
            titleOf(row),
          ),
          m(
            "span",
            {
              class:
                "chat-sidebar-rename flex-none rounded p-0.5 text-faint opacity-0 hover:bg-fill-hover hover:text-primary " +
                "group-hover:opacity-100 focus-visible:opacity-100",
              role: "button",
              tabindex: 0,
              "aria-label": "Rename chat",
              onclick: (event: MouseEvent) => {
                event.stopPropagation();
                beginRename(row);
              },
              onkeydown: (event: KeyboardEvent) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                event.stopPropagation();
                beginRename(row);
              },
            },
            pencilGlyph(),
          ),
        ],
  );
}

function renameRow(attrs: InstanceRailAttrs, row: RailRow, isSelected: boolean): m.Vnode {
  const state = rename;
  if (state === null) throw new Error("renameRow rendered with no rename in progress");
  return m(
    "div",
    {
      key: row.address,
      class: [
        "chat-sidebar-row chat-sidebar-row--renaming flex w-full items-center gap-2 rounded-md py-1.5",
        isAgentStarted(row) ? "chat-sidebar-row--nested pr-2 pl-2.5" : "px-2",
        isSelected ? "chat-sidebar-row--selected bg-fill-active text-primary" : "text-primary",
      ].join(" "),
      "data-chat-id": row.key,
      "aria-current": isSelected ? "true" : undefined,
    },
    [
      isAgentStarted(row) ? nestArrowGlyph() : null,
      statusDot(row, "flex-none"),
      m("input", {
        class: [
          "chat-sidebar-rename-input min-w-0 flex-1 rounded bg-surface px-1 text-(length:--font-size-row) text-primary",
          "outline-none ring-1",
          state.error === null ? "ring-accent" : "ring-danger",
        ].join(" "),
        type: "text",
        value: state.draft,
        "aria-label": "Chat name",
        "aria-invalid": state.error === null ? undefined : "true",
        title: state.error ?? undefined,
        maxlength: 256,
        oncreate: ({ dom }: m.VnodeDOM) => {
          const input = dom as HTMLInputElement;
          input.focus();
          input.select();
        },
        oninput: (event: InputEvent) => {
          if (rename === null) return;
          rename = { ...rename, draft: (event.target as HTMLInputElement).value, error: null };
        },
        onkeydown: (event: KeyboardEvent) => {
          event.stopPropagation();
          if (event.key === "Enter") {
            event.preventDefault();
            commitRename(attrs.app, row);
          } else if (event.key === "Escape") {
            event.preventDefault();
            cancelRename();
          }
        },
        onblur: () => {
          commitRename(attrs.app, row);
        },
      }),
    ],
  );
}
