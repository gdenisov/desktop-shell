/**
 * The desktop: a white surface carrying launcher icons, floating windows, and a dock of what is
 * running.
 *
 * Every window shows one instance of one app, named by its address, in an iframe the desktop sizes
 * to the window's body. The shell knows nothing about what any app is: what a window is called,
 * what icon it wears and what it is doing all come from the inventory, which the shell's WebSocket
 * keeps current.
 *
 * Three verbs, and the difference between them is the whole model:
 *
 * - **minimize** puts the window out of sight and leaves it loaded, so bringing it back is instant.
 *   Minimized and never-opened-a-window are the same state to the user: the thing is running and
 *   nothing is showing it, which is exactly what the dock says about both.
 * - **close** stops the instance -- the same verb as Stop elsewhere in the workspace -- and never
 *   deletes it. A stopped chat keeps its transcript and its name and is found again through search.
 *   There is no delete gesture on this desktop at all.
 * - **maximize** fills the desktop and remembers where to go back to.
 *
 * The arrangement is the client's layout of the view (``models/Layouts``), so it survives a reload
 * and an agent's ops land in the same file.
 */

import m from "mithril";
import { CLOSE_ACTIVE_TAB } from "@minds/embed-contract";
import {
  SHELL_CLOSE_REQUEST,
  SHELL_FOCUSED,
  SHELL_LOCATION,
  SHELL_OPEN,
  SHELL_TITLE,
} from "@imbue/workspace-ui/src/app_contract";
import { setEmbedderMessageHandler } from "@imbue/workspace-ui/src/embed";
import { getActiveProjectId, getClientId, setActiveProjectId } from "@imbue/workspace-ui/src/models/ClientIdentity";
import { requestFrameFocus } from "@imbue/workspace-ui/src/terminalFocus";
import { setHoverTooltip } from "@imbue/workspace-ui/src/components/hoverTooltip";
import {
  IFRAME_PANEL_ADDRESS_ATTR,
  IFRAME_PANEL_APP_ATTR,
  IframePanel,
  StoppedAppPlaceholder,
  reloadIframeForAddress,
  reloadIframesForApp,
} from "../IframePanel";
import {
  bindSlot,
  clearReportedPath,
  destroyLiveSurface,
  ensureLiveSurface,
  initializeLiveLayer,
  isPageAtListedUrl,
  liveSurfaceBoundSlotId,
  liveSurfaceElement,
  liveSurfaceKeys,
  recordReportedPath,
  reconcileLiveSurfaces,
  rekeyLiveSurface,
  scheduleReconcile,
  setGestureInProgress,
  unbindSlot,
  windowZIndex,
} from "../liveSurfaces";
import type { LiveSurface } from "../liveSurfaces";
import { sendToChildFrame, setChildFrameMessageHandler } from "../../relay";
import { reloadInterface } from "../../reload";
import {
  addActiveViewChangedListener,
  addAppsUpdatedListener,
  addLayoutOpListener,
  addLayoutUpdatedListener,
  addressFor,
  appNameFromAddress,
  appStoppedDetail,
  addTabReboundListener,
  findInstance,
  getApp,
  getOpenableApps,
  instancePageUrl,
  isAddressUnlisted,
  isAppStoppable,
  parseAddress,
  primaryActionForApp,
  removeAppsUpdatedListener,
  reportClientState,
  whenAppsLoaded,
} from "../../models/Inventory";
import type { AppRecord, InstanceRecord, LayoutOpEvent, ProjectInfo, ResolvedInstance } from "../../models/Inventory";
import { chooseInitialViewId, fetchProjectsList, isEverythingView, projectForViewId } from "../../models/Projects";
import { fetchOwnActiveView } from "../../models/Clients";
import { isDeepLinkEmpty, parseDeepLink, stripDeepLinkParams } from "../../models/deepLinks";
import type { DeepLink } from "../../models/deepLinks";
import {
  createInstance,
  renameInstance,
  reportInstanceLocation,
  setAppLifecycle,
  setInstanceLifecycle,
} from "../../models/Relay";
import { StaleLayoutSaveError, fetchLayout, isOwnSaveId, mintTabId, saveLayout } from "../../models/Layouts";
import type { LayoutRecord } from "../../models/Layouts";
import { emptyDesktop } from "../../models/Desktop";
import { initChatUnread, noteStatuses } from "./chatUnread";
import { InstanceRail, noteStartedHere } from "./instanceRail";
import { clearPendingTitle, pendingTitle, setPendingTitle, settlePendingTitles } from "./pendingTitles";
import type { DesktopDocument, DesktopSize, IconArrangement, WindowRect } from "../../models/Desktop";
import { ensureTemplateCatalogRequested, getTemplateCatalogState } from "../../models/TemplateCatalog";
import { searchEverything, titleMatchesFromInventory } from "../../models/InstanceSearch";
import type { SearchResult } from "../../models/InstanceSearch";
import { appIconMarkup } from "../components/appIcon";
import { glyph } from "./glyphs";
import { cascadeRect, draggedRect, isPointerOver, resizedRect } from "./geometry";
import type { ResizeEdge } from "./geometry";
import { DOCK_TILE_PX, dockEntries, dockTooltip } from "./dock";
import type { DockEntry } from "./dock";
import {
  MAKE_SOMETHING_ENTRY,
  MAKE_SOMETHING_LABEL,
  cellContaining,
  cellOrigin,
  desktopEntries,
  iconGridFor,
  placedIcons,
  withIconMovedTo,
  withRoomMadeAt,
} from "./icons";
import type { DesktopEntry, PlacedIcon } from "./icons";
import { Launcher, SEARCH_PLACEHOLDER } from "./Launcher";
import { launcherItemsMatching, launcherMenuItems } from "../../models/launcherMenu";
import type { LauncherItem } from "../../models/launcherMenu";
import { MakeSomething } from "./MakeSomething";
import { MAKE_SOMETHING_TINT, tintForApp } from "./tints";
import {
  documentFromWindows,
  makeSomethingWindow,
  windowShowing,
  windowWithTabId,
  shownRect,
  windowsFromDocument,
  withGeometry,
  withMaximizeToggled,
  withMinimized,
  withRaised,
  withUpdatedWindow,
} from "./windowState";
import type { WindowState } from "./windowState";

// A resize or a drag is saved once it comes to rest; opening, closing or raising a window is
// saved almost at once, because the shell edits the saved arrangement for agent ops and an op
// that follows a click has to see the click's window in the file.
const AUTOSAVE_DEBOUNCE_MS = 1500;
const STRUCTURAL_SAVE_DELAY_MS = 100;

// How long an open waits for an address the inventory does not list yet, and how long a deep
// link waits (it is applied right after the page loads, before every app's list has arrived).
const AWAIT_ADDRESS_TIMEOUT_MS = 4000;
const DEEP_LINK_ADDRESS_TIMEOUT_MS = 15000;

// Closing a window stops what it shows, and a page that is still attached when the stop lands can
// recreate what was just killed -- a terminal's session is launched with ``tmux new-session -A``,
// which attaches or creates. So the frame is dropped, the socket is given a moment to close, the
// stop is asked for, and then it is checked; a bounded few times, because something that refuses
// to stop is reported by the dock rather than fought with forever.
const STOP_ATTEMPTS = 4;
const STOP_SETTLE_MS = 700;

// How long after the last keystroke the shell is asked. Short, because the name matches are
// already on screen by then and the chat's search answers in milliseconds from what it read on
// the keystroke before.
const SEARCH_DEBOUNCE_MS = 80;

// The desktop the geometry falls back to before the real one has been measured.
const FALLBACK_DESKTOP_SIZE: DesktopSize = { width: 1200, height: 800 };

// What a window is called when nothing knows its name: its instance is not listed, it was never
// saved with a title, and its app is not registered either.
const UNTITLED_WINDOW_TITLE = "Untitled";

const WINDOW_GLYPH_SIZE = 17;
const ICON_GLYPH_SIZE = 32;
const DOCK_GLYPH_RATIO = 0.52;

// ---------- state ----------

/** The windows on screen, bottom of the stack first. */
let windows: WindowState[] = [];
/** What the desktop holds beside its windows: the icon arrangement and the dock's setting. */
let desktopDocument: DesktopDocument = emptyDesktop();
let desktopSize: DesktopSize = FALLBACK_DESKTOP_SIZE;
let desktopElement: HTMLElement | null = null;
let dockElement: HTMLElement | null = null;
let searchFieldElement: HTMLInputElement | null = null;

let mountedViewId: string | null = null;
let baseUpdatedAt: string | null = null;
let isSaveSuspended = false;
let lastSavedJson: string | null = null;
let saveTimer: ReturnType<typeof setTimeout> | null = null;
let viewMountGeneration = 0;

/** Instances whose stop is in flight: held out of the dock so a closed window does not flicker back. */
const closingAddresses = new Set<string>();
/** Actions already running, as ``<app>:<action>``, so a second click does not start a second thing. */
const actionsInFlight = new Set<string>();

let isLauncherOpen = false;
let searchQuery = "";
let searchResults: SearchResult[] = [];
let isSearchPending = false;
let searchToken = 0;
let searchTimer: ReturnType<typeof setTimeout> | null = null;

/** The window whose title is being edited, if any. */
let renamingTabId: string | null = null;
/** The icon being dragged across the desktop, if any, and where the gesture started. */
let iconDrag: {
  entryName: string;
  element: HTMLElement;
  startX: number;
  startY: number;
  /** Where the icon's own top left was when it was picked up, in desktop coordinates. */
  originX: number;
  originY: number;
  isMoving: boolean;
} | null = null;
/** Set when a drag ends on the icon it started on, so the click that follows does not select it. */
let isIconClickSuppressed = false;
/** The arrangement as it stands while an icon is in the hand: the icon under it has stepped aside.
 *  Drawn instead of the saved one until the drop commits it or the drag is called off. */
let roomMadeArrangement: IconArrangement | null = null;
/** The cell the held icon is over, so the room is made once per cell, not once per pointer move. */
let roomMadeCellKey: string | null = null;
/** The icon just dropped, for the frames in which it settles without sliding (``.is-dropping``). */
let droppingEntryName: string | null = null;
/** Whether the press that is ending moved far enough to have been a drag. A double-click made of
 *  two drags that happened to land in the same spot must not open anything. */
let didLastIconPressMove = false;
/** The icon a single click has picked out, if any: opening is the double click's job, so a single
 *  one has to leave something behind or it reads as the desktop ignoring the user. */
let selectedIconEntry: string | null = null;

// ---------- the desktop's own reads ----------

function currentApps(): AppRecord[] {
  return getOpenableApps();
}

function entries(): DesktopEntry[] {
  return desktopEntries(currentApps());
}

function measureDesktop(): DesktopSize {
  if (desktopElement === null) return desktopSize;
  const box = desktopElement.getBoundingClientRect();
  if (box.width <= 0 || box.height <= 0) return desktopSize;
  return { width: Math.round(box.width), height: Math.round(box.height) };
}

// ---------- saving ----------

/**
 * The windows with each one's remembered title brought up to date.
 *
 * A window is saved with what it is called, because it outlives its instance leaving its app's
 * list and a window restored without one has nothing to show but its address. Taken from the live
 * title at every save, so a rename is remembered too -- and left alone when there is no live title
 * to take, which is exactly the case the memory is for.
 */
function withRememberedTitles(open: readonly WindowState[]): WindowState[] {
  return open.map((window) => {
    if (window.kind !== "instance") return window;
    const live = findInstance(window.address)?.instance.title;
    if (live === undefined || live === "" || live === window.lastKnownTitle) return window;
    return { ...window, lastKnownTitle: live };
  });
}

function currentDocument(): DesktopDocument {
  return documentFromWindows(withRememberedTitles(windows), desktopDocument, desktopSize);
}

async function persistDesktop(): Promise<void> {
  const viewId = mountedViewId;
  if (viewId === null || isSaveSuspended) return;
  const document = currentDocument();
  const serialized = JSON.stringify(document);
  if (serialized === lastSavedJson) return;
  try {
    const outcome = await saveLayout(viewId, getClientId(), document, baseUpdatedAt);
    lastSavedJson = serialized;
    if (outcome.updatedAt !== null && viewId === mountedViewId) baseUpdatedAt = outcome.updatedAt;
  } catch (e) {
    if (e instanceof StaleLayoutSaveError) {
      // The shell holds a newer arrangement (an agent op, or another window's save): it wins.
      console.warn(`[si] the desktop of ${viewId} moved under this window; taking the shell's`, e);
      if (viewId === mountedViewId) void refreshDesktopFromServer();
      return;
    }
    console.warn(`[si] could not save the desktop of ${viewId}`, e);
  }
}

function scheduleSave(delayMs: number = AUTOSAVE_DEBOUNCE_MS): void {
  if (saveTimer !== null) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    void persistDesktop();
  }, delayMs);
}

/** A window was opened, closed, raised or put away: save without waiting out the resize debounce. */
function saveSoon(): void {
  scheduleSave(STRUCTURAL_SAVE_DELAY_MS);
}

async function flushPendingSave(): Promise<void> {
  if (saveTimer === null) return;
  clearTimeout(saveTimer);
  saveTimer = null;
  await persistDesktop();
}

// ---------- applying an arrangement ----------

/** Take a layout from the shell: the windows it holds, the icons, and the dock's setting. */
async function applyLayout(layout: LayoutRecord | null, generation: number): Promise<void> {
  // Every page url is derived from its app's origin label, which only resolves once the app list
  // has loaded; bounded, so a workspace that reports no apps still proceeds.
  await whenAppsLoaded();
  if (generation !== viewMountGeneration) return;
  const document = layout?.desktop ?? emptyDesktop();
  desktopSize = measureDesktop();
  desktopDocument = document;
  const openMake = makeSomethingWindow(windows);
  const restored = windowsFromDocument(document);
  // A page this window is already showing keeps its live surface: the arrangement moved, the
  // document behind it did not.
  windows = openMake === undefined ? restored : [...restored, openMake];
  releaseUnshownPages();
  lastSavedJson = JSON.stringify(currentDocument());
  scheduleReconcile();
  m.redraw();
}

/**
 * The pages a window of a listing app holds beside the one it shows: the instances picked in its
 * rail earlier this session, kept loaded (unshown) so picking them again is instant, as a
 * minimized window's page is. Let go when the window closes. Not saved: a reloaded desktop
 * starts each such window with only the page it shows.
 */
const heldAddressesByTab = new Map<string, Set<string>>();

function isHeld(address: string): boolean {
  for (const held of heldAddressesByTab.values()) if (held.has(address)) return true;
  return false;
}

/** Every address some window shows or holds. */
function shownOrHeldAddresses(): Set<string> {
  const addresses = windowedAddresses();
  for (const held of heldAddressesByTab.values()) for (const address of held) addresses.add(address);
  return addresses;
}

/**
 * Show ``address`` in the window ``tabId`` names, in place: the page it showed stays loaded and
 * held for the window, and the window's title, dock tile and saved arrangement follow the new
 * address. What a pick in a listing window's rail does.
 */
function showInstanceInWindow(tabId: string, address: string): void {
  const window_ = windowWithTabId(windows, tabId);
  if (window_ === undefined || window_.kind !== "instance" || window_.address === address) return;
  const held = heldAddressesByTab.get(tabId) ?? new Set<string>();
  held.add(window_.address);
  held.delete(address);
  heldAddressesByTab.set(tabId, held);
  windows = withUpdatedWindow(windows, tabId, (state) => ({ ...state, address, lastKnownTitle: null }));
  saveSoon();
  m.redraw();
}

/** Start an instance of ``app`` through its primary action and show it in the window ``tabId`` names. */
async function startInstanceInWindow(tabId: string, app: AppRecord): Promise<void> {
  const listed = await createListed(app, {});
  if (listed === null) return;
  const address = addressFor(listed.app.name, listed.instance.key);
  noteStartedHere(address);
  showInstanceInWindow(tabId, address);
}

/** Let go of the page of anything no window shows or holds any more, so a closed window stops running. */
function releaseUnshownPages(): void {
  const kept = shownOrHeldAddresses();
  for (const key of liveSurfaceKeys()) {
    if (!kept.has(key)) destroyLiveSurface(key);
  }
}

async function fetchLayoutOrSuspendSaves(viewId: string): Promise<LayoutRecord | null> {
  try {
    const layout = await fetchLayout(viewId, getClientId());
    isSaveSuspended = false;
    baseUpdatedAt = layout.updated_at;
    return layout;
  } catch (e) {
    console.warn(`[si] could not fetch the desktop of ${viewId}; autosave is off until it loads`, e);
    isSaveSuspended = true;
    baseUpdatedAt = null;
    return null;
  }
}

/** Take the shell's arrangement when it differs from the one this window holds. */
async function refreshDesktopFromServer(): Promise<void> {
  const viewId = mountedViewId;
  if (viewId === null) return;
  const generation = viewMountGeneration;
  let layout: LayoutRecord;
  try {
    layout = await fetchLayout(viewId, getClientId());
  } catch (e) {
    console.warn(`[si] could not fetch the pushed desktop of ${viewId}`, e);
    return;
  }
  if (generation !== viewMountGeneration || viewId !== mountedViewId) return;
  if (layout.updated_at === baseUpdatedAt) return;
  baseUpdatedAt = layout.updated_at;
  isSaveSuspended = false;
  await applyLayout(layout, generation);
}

async function switchToView(viewId: string): Promise<void> {
  if (mountedViewId === viewId) return;
  await flushPendingSave();
  const generation = ++viewMountGeneration;
  setActiveProjectId(viewId);
  mountedViewId = viewId;
  reportClientState();
  const layout = await fetchLayoutOrSuspendSaves(viewId);
  if (generation !== viewMountGeneration) return;
  await applyLayout(layout, generation);
}

// ---------- windows ----------

function raiseWindow(tabId: string): void {
  windows = withRaised(windows, tabId, Date.now());
  const raised = windowWithTabId(windows, tabId);
  // A page never takes focus on its own, so raising a window is what hands a framed terminal
  // the keyboard.
  if (raised !== undefined && raised.kind === "instance") requestFrameFocus(liveSurfaceElement(raised.address));
  scheduleReconcile();
  saveSoon();
  m.redraw();
}

function minimizeWindow(tabId: string): void {
  windows = withMinimized(windows, tabId);
  scheduleReconcile();
  saveSoon();
  m.redraw();
}

function toggleMaximize(tabId: string): void {
  desktopSize = measureDesktop();
  windows = withMaximizeToggled(windows, tabId, desktopSize);
  scheduleReconcile();
  saveSoon();
  m.redraw();
}

/**
 * Close a window: the page goes, and what it was showing is stopped.
 *
 * The frame is dropped before the stop is asked for, so the page's own connection is closed by
 * the time it lands -- otherwise a still-attached client recreates the session the stop killed.
 * An instance its app cannot stop (nothing is running behind a file viewer) simply loses its
 * window; the shell's own cleanup takes care of the record if nothing references it.
 */
function closeWindow(tabId: string): void {
  const closed = windowWithTabId(windows, tabId);
  if (closed === undefined) return;
  windows = windows.filter((window) => window.tabId !== tabId);
  if (closed.kind !== "instance") {
    m.redraw();
    return;
  }
  destroyLiveSurface(closed.address);
  // The pages the window held for its rail go with it, unless another window shows one of them.
  for (const address of heldAddressesByTab.get(tabId) ?? []) {
    if (windowShowing(windows, address) === undefined) destroyLiveSurface(address);
  }
  heldAddressesByTab.delete(tabId);
  const resolved = findInstance(closed.address);
  saveSoon();
  m.redraw();
  if (resolved === null) return;
  // An app that lists its own instances (the chat app and its chat list) keeps them running:
  // its window is just a window, and stopping is the explicit verb in its own list. Closing a
  // chat mid-work must not stop the work.
  if (resolved.app.browses_instances) return;
  // A single-program app (one window, no instances of its own) is its process: closing the
  // window stops the program, so the memory goes with the window rather than the app living on
  // in the dock. The desktop starts it again from its icon.
  if (parseAddress(closed.address)?.key === "" && isAppStoppable(resolved.app)) {
    closingAddresses.add(closed.address);
    void setAppLifecycle(resolved.app.name, "stop")
      .catch((e: Error) => console.warn(`[si] could not stop ${resolved.app.name}`, e))
      .finally(() => {
        closingAddresses.delete(closed.address);
        m.redraw();
      });
    return;
  }
  if (!resolved.instance.stoppable) return;
  closingAddresses.add(closed.address);
  void stopUntilItSticks(resolved.app.name, resolved.instance.key, closed.address);
}

/** Ask for the stop, confirm it held, and ask again if something revived it. */
async function stopUntilItSticks(appName: string, key: string, address: string): Promise<void> {
  for (let attempt = 0; attempt < STOP_ATTEMPTS; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, STOP_SETTLE_MS));
    try {
      await setInstanceLifecycle(appName, key, "stop");
    } catch (e) {
      console.warn(`[si] could not stop ${address}`, e);
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, STOP_SETTLE_MS));
    const resolved = findInstance(address);
    if (resolved === null || resolved.instance.status === "stopped") break;
  }
  closingAddresses.delete(address);
  m.redraw();
}

/** Open a window on an instance, or raise the one it already has. */
async function openInstance(app: AppRecord, instance: InstanceRecord): Promise<void> {
  const address = addressFor(app.name, instance.key);
  const open = windowShowing(windows, address);
  if (open !== undefined) {
    raiseWindow(open.tabId);
    return;
  }
  // Stopped means nothing is running behind it; bring it back before framing it.
  if (instance.status === "stopped") {
    try {
      await setInstanceLifecycle(app.name, instance.key, "start");
    } catch (e) {
      console.warn(`[si] could not start ${address}`, e);
    }
  }
  desktopSize = measureDesktop();
  windows = [
    ...windows,
    {
      kind: "instance",
      tabId: mintTabId(),
      address,
      // Remembered from the start, so a window is never without a name -- not even if its
      // instance disappears before the first save.
      lastKnownTitle: instance.title !== "" ? instance.title : null,
      rect: cascadeRect(windows.length, desktopSize),
      isMinimized: false,
      isMaximized: false,
      restoreRect: null,
      lastFocusedMs: Date.now(),
    },
  ];
  scheduleReconcile();
  saveSoon();
  m.redraw();
}

/** Resolve to whether the inventory lists ``address``, or false once the wait is over. */
function whenAddressListed(address: string, timeoutMs: number = AWAIT_ADDRESS_TIMEOUT_MS): Promise<boolean> {
  if (findInstance(address) !== null) return Promise.resolve(true);
  return new Promise<boolean>((resolve) => {
    const settle = (isListed: boolean): void => {
      removeAppsUpdatedListener(listener);
      clearTimeout(timer);
      resolve(isListed);
    };
    const listener = (): void => {
      if (findInstance(address) !== null) settle(true);
    };
    const timer = setTimeout(() => settle(false), timeoutMs);
    addAppsUpdatedListener(listener);
  });
}

/**
 * Open what an app's icon means.
 *
 * Which one that is, is the app's own declaration: an app whose default shortcut is in ``new``
 * mode always makes a new thing (a chat is a window and nothing more -- no list, no sidebar, and
 * as many at once as the user opens), and one in ``focus`` mode goes to what is already there and
 * only makes something when there is nothing.
 */
async function openApp(app: AppRecord, params: Record<string, string> = {}): Promise<void> {
  if (!app.has_instances) {
    const synthesized = app.instances[0];
    if (synthesized !== undefined) await openInstance(app, synthesized);
    return;
  }
  // An app that lists its own instances opens a window ONTO one rather than starting another:
  // its own list is where a new one is begun from, and you would otherwise start a chat every
  // time you only wanted to read one. Repeated clicks give you another window each time, on the
  // next thing that has none, because a window is the one instance it shows.
  if (app.browses_instances && Object.keys(params).length === 0 && (await openNextUnwindowedOf(app))) return;
  const action = primaryActionForApp(app);
  if (action === null) return;
  const isFocusMode = app.default_shortcut?.mode === "focus" && Object.keys(params).length === 0;
  if (isFocusMode && (await focusMostRecentOf(app))) return;
  await createAndOpen(app, params);
}

/** Start a new instance of the app through its primary action and open a window on it. */
async function createAndOpen(app: AppRecord, params: Record<string, string>): Promise<void> {
  const listed = await createListed(app, params);
  if (listed !== null) await openInstance(listed.app, listed.instance);
}

/**
 * Start a new instance of the app through its primary action, and wait for the app to list it:
 * what is opened is the app's own record of it. Null when the app has no such action, when the
 * same start is already in flight (a second click does not start a second thing), or when it
 * failed -- which the user is told about.
 */
async function createListed(app: AppRecord, params: Record<string, string>): Promise<ResolvedInstance | null> {
  const action = primaryActionForApp(app);
  if (action === null) return null;
  const key = `${app.name}:${action.id}`;
  if (actionsInFlight.has(key)) return null;
  actionsInFlight.add(key);
  m.redraw();
  try {
    const record = await createInstance(app.name, action.id, params);
    const address = addressFor(app.name, record.key);
    if (!(await whenAddressListed(address))) {
      throw new Error(`${app.display_name} created ${record.key} but has not listed it`);
    }
    return findInstance(address);
  } catch (e) {
    alert(`Failed to open ${app.display_name}: ${(e as Error).message}`);
    return null;
  } finally {
    actionsInFlight.delete(key);
    m.redraw();
  }
}

/**
 * Open a window on the app's most recently active instance that does not have one, or raise its
 * most recent window when every one of them does; false when the app is running nothing at all.
 *
 * What the desktop icon of an app that browses its own instances does. A window IS the instance it
 * shows, so "another window" means another instance -- and the most recent one without a window is
 * the one the user is most likely reaching for.
 */
async function openNextUnwindowedOf(app: AppRecord): Promise<boolean> {
  const running = app.instances.filter((instance) => instance.status !== "stopped");
  if (running.length === 0) return false;
  const unwindowed = running.filter(
    (instance) => windowShowing(windows, addressFor(app.name, instance.key)) === undefined,
  );
  const candidate = unwindowed[unwindowed.length - 1];
  if (candidate !== undefined) {
    await openInstance(app, candidate);
    return true;
  }
  return focusMostRecentOf(app);
}

/** Go to the app's most recent window, or its most recently active instance; false when it has none. */
async function focusMostRecentOf(app: AppRecord): Promise<boolean> {
  const open = windows
    .filter((window) => window.kind === "instance" && appNameFromAddress(window.address) === app.name)
    .sort((left, right) => left.lastFocusedMs - right.lastFocusedMs);
  const mostRecentlyUsed = open[open.length - 1];
  if (mostRecentlyUsed !== undefined) {
    raiseWindow(mostRecentlyUsed.tabId);
    return true;
  }
  const running = app.instances.filter((instance) => instance.status !== "stopped");
  const candidate = running[running.length - 1];
  if (candidate === undefined) return false;
  await openInstance(app, candidate);
  return true;
}

/** Open (or raise) the Make something window: one, however many times it is asked for. */
function openMakeSomething(): void {
  ensureTemplateCatalogRequested();
  const open = makeSomethingWindow(windows);
  if (open !== undefined) {
    windows = withRaised(windows, open.tabId, Date.now());
    m.redraw();
    return;
  }
  desktopSize = measureDesktop();
  windows = [
    ...windows,
    {
      kind: "make",
      tabId: mintTabId(),
      address: "",
      lastKnownTitle: null,
      rect: cascadeRect(windows.length, desktopSize),
      isMinimized: false,
      isMaximized: false,
      restoreRect: null,
      lastFocusedMs: Date.now(),
    },
  ];
  m.redraw();
}

/** Start a new chat seeded with a prompt, through whichever app takes a first message. */
async function startSeededChat(prompt: string): Promise<void> {
  const target = currentApps().find((app) => app.actions.some((action) => action.params.includes(MESSAGE_PARAM)));
  if (target === undefined) {
    alert("No app on this machine can start a chat.");
    return;
  }
  const action = target.actions.find((candidate) => candidate.params.includes(MESSAGE_PARAM));
  if (action === undefined) return;
  await openApp(target, { [MESSAGE_PARAM]: prompt });
}

// The create param a seeded prompt rides: an action declaring it takes a first message
// (contracts.md section 2).
const MESSAGE_PARAM = "message";

function openEntry(entry: DesktopEntry): void {
  // Whatever was picked out has been acted on, so nothing is picked out any more.
  selectedIconEntry = null;
  if (entry.app === null) {
    openMakeSomething();
    return;
  }
  void openApp(entry.app);
}

// ---------- dragging and resizing ----------

interface Gesture {
  tabId: string;
  startRect: WindowRect;
  startX: number;
  startY: number;
  element: HTMLElement;
  edge: ResizeEdge | null;
}

let gesture: Gesture | null = null;

function beginGesture(tabId: string, element: HTMLElement, event: PointerEvent, edge: ResizeEdge | null): void {
  const window_ = windowWithTabId(windows, tabId);
  if (window_ === undefined || (edge === null && window_.isMaximized)) return;
  desktopSize = measureDesktop();
  gesture = {
    tabId,
    startRect: shownRect(window_, desktopSize),
    startX: event.clientX,
    startY: event.clientY,
    element,
    edge,
  };
  element.classList.add(edge === null ? "is-dragging" : "is-resizing");
  setGestureInProgress(true);
  window.addEventListener("pointermove", onGesturePointerMove);
  window.addEventListener("pointerup", onGesturePointerUp);
}

/** Follow the pointer by writing the geometry straight onto the element: a redraw per frame would
 *  re-render every window and every page for a gesture that moves one box. */
function onGesturePointerMove(event: PointerEvent): void {
  if (gesture === null) return;
  const deltaX = event.clientX - gesture.startX;
  const deltaY = event.clientY - gesture.startY;
  const rect =
    gesture.edge === null
      ? draggedRect(gesture.startRect, deltaX, deltaY, desktopSize)
      : resizedRect(gesture.startRect, gesture.edge, deltaX, deltaY);
  applyRectToElement(gesture.element, rect);
  reconcileLiveSurfaces();
}

function onGesturePointerUp(event: PointerEvent): void {
  if (gesture === null) return;
  const deltaX = event.clientX - gesture.startX;
  const deltaY = event.clientY - gesture.startY;
  const rect =
    gesture.edge === null
      ? draggedRect(gesture.startRect, deltaX, deltaY, desktopSize)
      : resizedRect(gesture.startRect, gesture.edge, deltaX, deltaY);
  gesture.element.classList.remove("is-dragging", "is-resizing");
  windows = withGeometry(windows, gesture.tabId, rect);
  gesture = null;
  setGestureInProgress(false);
  window.removeEventListener("pointermove", onGesturePointerMove);
  window.removeEventListener("pointerup", onGesturePointerUp);
  scheduleReconcile();
  scheduleSave();
  m.redraw();
}

function applyRectToElement(element: HTMLElement, rect: WindowRect): void {
  element.style.left = `${rect.x}px`;
  element.style.top = `${rect.y}px`;
  element.style.width = `${rect.width}px`;
  element.style.height = `${rect.height}px`;
}

// ---------- renaming ----------

function commitRename(tabId: string, title: string): void {
  const window_ = windowWithTabId(windows, tabId);
  renamingTabId = null;
  if (window_ === undefined || window_.kind !== "instance") return;
  const parsed = parseAddress(window_.address);
  const trimmed = title.trim();
  if (parsed === null || trimmed === "") return;
  const resolved = findInstance(window_.address);
  if (resolved !== null && resolved.instance.title === trimmed) return;
  // Shown at once: the app takes seconds to rename, and the bar would say the old name meanwhile.
  setPendingTitle(window_.address, trimmed);
  void renameInstance(parsed.app, parsed.key, trimmed).catch((e: Error) => {
    clearPendingTitle(window_.address);
    alert(`Could not rename to "${trimmed}".\n\n${e.message}`);
    m.redraw();
  });
}

/** ``shell:title`` from a page: the name an instance of the posting frame's app is about to have. */
function adoptTitleForChildFrame(frame: HTMLIFrameElement, payload: Record<string, unknown>): void {
  const postingApp = frame.getAttribute(IFRAME_PANEL_APP_ATTR);
  const { key, title } = payload;
  if (postingApp === null || typeof key !== "string" || key === "" || typeof title !== "string") return;
  setPendingTitle(addressFor(postingApp, key), title);
}

// ---------- the live pages ----------

function mountLiveContent(surface: LiveSurface): void {
  m.mount(surface.element, { view: () => renderLiveContent(surface) });
}

function stoppedPlaceholderForApp(
  app: AppRecord,
): { label: string; detail: string; onStart: (() => void) | null } | null {
  if (app.is_running) return null;
  return {
    label: app.display_name,
    detail: appStoppedDetail(app),
    onStart: isAppStoppable(app)
      ? () => {
          void setAppLifecycle(app.name, "start").catch((e: Error) => {
            alert(`Failed to start ${app.display_name}: ${e.message}`);
          });
        }
      : null,
  };
}

function renderLiveContent(surface: LiveSurface): m.Children {
  const address = surface.key;
  const resolved = findInstance(address);
  if (resolved === null) {
    const appName = appNameFromAddress(address);
    const app = appName === null ? undefined : getApp(appName);
    const stopped = app === undefined ? null : stoppedPlaceholderForApp(app);
    if (app !== undefined && stopped !== null) return m(StoppedAppPlaceholder, { ...stopped, appName: app.name });
    if (isAddressUnlisted(address)) {
      // The window is kept: its app may list this again, and the window reconnects when it does.
      // Until then it says so plainly, and its X clears it away (there is nothing left to stop).
      return m("div.window-note", [
        m("p.window-note-lead", "This isn't here any more."),
        m("p", "If it comes back, this window will show it again. Otherwise, close the window to clear it away."),
      ]);
    }
    return m("div.window-note", "Waiting for this window's app to list it.");
  }
  const { app, instance } = resolved;
  const url = instancePageUrl(app, instance, surface.tabId);
  return m(IframePanel, {
    url,
    isPageAtUrl: isPageAtListedUrl(url, surface.lastReportedPath),
    onNavigate: () => clearReportedPath(surface.key),
    title: instance.title,
    appName: app.name,
    address,
    stopped: stoppedPlaceholderForApp(app),
    contract: {
      address,
      tabId: surface.tabId,
      viewId: getActiveProjectId(),
      isVisible: surface.isVisible,
    },
  });
}

// ---------- the app contract's shell side ----------

function windowForChildFrame(frame: HTMLIFrameElement): WindowState | undefined {
  const address = frame.getAttribute(IFRAME_PANEL_ADDRESS_ATTR);
  if (address === null) return undefined;
  const slotId = liveSurfaceBoundSlotId(address);
  return slotId === null ? undefined : windowWithTabId(windows, slotId);
}

/** ``shell:focused`` from a page: the window showing it comes forward. A click inside a framed
 *  page never reaches the desktop, so this is the only way one can raise its own window. */
function raiseWindowForChildFrame(frame: HTMLIFrameElement): void {
  const window_ = windowForChildFrame(frame);
  if (window_ !== undefined && windows[windows.length - 1]?.tabId !== window_.tabId) raiseWindow(window_.tabId);
}

/** ``shell:open`` from a page: open a window on an instance of the posting frame's own app. */
function openInstanceForChildFrame(frame: HTMLIFrameElement, payload: Record<string, unknown>): void {
  const address = payload.address;
  if (typeof address !== "string") return;
  const postingApp = frame.getAttribute(IFRAME_PANEL_APP_ATTR);
  if (postingApp === null || appNameFromAddress(address) !== postingApp) {
    console.warn(`[si] shell:open ignored: ${address} does not name the posting frame's app`);
    return;
  }
  void openAddressWhenListed(address);
}

async function openAddressWhenListed(address: string, timeoutMs: number = AWAIT_ADDRESS_TIMEOUT_MS): Promise<void> {
  if (!(await whenAddressListed(address, timeoutMs))) {
    console.warn(`[si] cannot open ${address}: its app has not listed it`);
    return;
  }
  const resolved = findInstance(address);
  if (resolved !== null) await openInstance(resolved.app, resolved.instance);
}

/** ``shell:location`` from a page: relayed to the app that owns the instance. */
function relayLocationForChildFrame(frame: HTMLIFrameElement, payload: Record<string, unknown>): void {
  const address = frame.getAttribute(IFRAME_PANEL_ADDRESS_ATTR);
  const path = payload.path;
  if (address === null || typeof path !== "string" || path === "") return;
  const parsed = parseAddress(address);
  if (parsed === null || parsed.key === "") return;
  recordReportedPath(address, path);
  void reportInstanceLocation(parsed.app, parsed.key, path);
}

function closeActiveWindowFromEmbedder(): void {
  const topmost = windows[windows.length - 1];
  if (topmost === undefined) return;
  const frame =
    topmost.kind === "instance" ? (liveSurfaceElement(topmost.address)?.querySelector("iframe") ?? null) : null;
  if (frame !== null) sendToChildFrame(frame, SHELL_CLOSE_REQUEST);
  closeWindow(topmost.tabId);
}

// ---------- agent-driven transient ops ----------

function handleLayoutOp(event: LayoutOpEvent): void {
  switch (event.op) {
    case "maximize":
      handleMaximize(event.args, event.requester);
      return;
    case "restore":
      handleRestore();
      return;
    case "refresh":
      handleRefresh(event.args, event.requester);
      return;
    case "reload_system_interface":
      reloadInterface();
      return;
  }
}

function addressOfOpArgument(value: unknown, requester: string): string | null {
  if (typeof value !== "string" || value === "") return null;
  return value === "self" ? (requester === "" ? null : requester) : value;
}

function handleMaximize(args: Record<string, unknown>, requester: string): void {
  const address = addressOfOpArgument(args.address, requester);
  if (address === null) return;
  const window_ = windowShowing(windows, address);
  if (window_ === undefined || window_.isMaximized) return;
  toggleMaximize(window_.tabId);
}

function handleRestore(): void {
  const maximized = windows.find((window) => window.isMaximized);
  if (maximized !== undefined) toggleMaximize(maximized.tabId);
}

function handleRefresh(args: Record<string, unknown>, requester: string): void {
  const address = addressOfOpArgument(args.address, requester);
  if (address === null) return;
  const parsed = parseAddress(address);
  if (parsed === null) return;
  if (parsed.key === "" && getApp(parsed.app)?.has_instances === true) {
    reloadIframesForApp(parsed.app);
    return;
  }
  reloadIframeForAddress(address);
}

// ---------- the + : the menu and the search, under one field ----------

function setSearchQuery(query: string): void {
  searchQuery = query;
  if (searchTimer !== null) clearTimeout(searchTimer);
  if (query.trim() === "") {
    searchResults = [];
    isSearchPending = false;
    m.redraw();
    return;
  }
  // What the page can answer itself, it answers now: the things whose NAME matches are drawn on
  // this very keystroke, from the instance list already in hand. The shell's answer -- the same
  // rows, plus what was found inside things -- replaces them when it lands.
  searchResults = titleMatchesFromInventory(currentApps(), query);
  isSearchPending = true;
  searchTimer = setTimeout(() => {
    searchTimer = null;
    void runSearch(query);
  }, SEARCH_DEBOUNCE_MS);
  m.redraw();
}

async function runSearch(query: string): Promise<void> {
  // Only the newest query may paint: an earlier, slower one landing after it would show results
  // for text the user has already moved past.
  const token = ++searchToken;
  const results = await searchEverything(query);
  if (token !== searchToken) return;
  searchResults = results;
  isSearchPending = false;
  m.redraw();
}

/**
 * Open or close the ``+``: open, the button widens into the field with the cursor in it and the
 * menu comes up over it; closed, the field empties, the menu goes, and the keyboard goes back to
 * the page.
 */
function setLauncherOpen(isOpen: boolean): void {
  isLauncherOpen = isOpen;
  if (isOpen) {
    // The width animates open; the cursor goes in as soon as the field can take it.
    requestAnimationFrame(() => searchFieldElement?.focus());
  } else {
    if (searchTimer !== null) clearTimeout(searchTimer);
    searchTimer = null;
    searchQuery = "";
    searchResults = [];
    isSearchPending = false;
    searchFieldElement?.blur();
  }
  m.redraw();
}

/** What a row of the menu does: open an app, the browsing app as it is, or start a new one. */
async function pickLauncherItem(item: LauncherItem): Promise<void> {
  setLauncherOpen(false);
  if (item.kind === "browse") {
    // The app as it is: its window if it has one, else a window on its latest instance.
    if (!(await focusMostRecentOf(item.app))) await openApp(item.app);
  } else if (item.kind === "new") {
    await startNewInstanceOf(item.app);
  } else {
    await openApp(item.app);
  }
}

/**
 * Start a new instance and show it in the app's window if it has one -- re-pointing that window
 * rather than opening a second -- else in a new window. What "New Chat" means when a chat window
 * is already up: the same window, on a fresh chat.
 */
async function startNewInstanceOf(app: AppRecord): Promise<void> {
  const open = windows
    .filter((window) => window.kind === "instance" && appNameFromAddress(window.address) === app.name)
    .sort((left, right) => left.lastFocusedMs - right.lastFocusedMs);
  const target = open[open.length - 1];
  if (target === undefined) {
    await createAndOpen(app, {});
    return;
  }
  const listed = await createListed(app, {});
  if (listed === null) return;
  const previous = target.address;
  windows = withUpdatedWindow(windows, target.tabId, (state) => ({
    ...state,
    address: listed.address,
    lastKnownTitle: listed.instance.title !== "" ? listed.instance.title : null,
  }));
  // The window's page was the old instance's; it is dropped so the slot frames the new one.
  destroyLiveSurface(previous);
  raiseWindow(target.tabId);
  scheduleReconcile();
  saveSoon();
  m.redraw();
}

async function openSearchResult(result: SearchResult): Promise<void> {
  setLauncherOpen(false);
  const app = getApp(result.app);
  if (app === undefined) return;
  const listed = app.instances.find((instance) => instance.key === result.key);
  // A stopped instance is started by opening it, which is what makes search the way back to one.
  await openInstance(
    app,
    listed ?? {
      key: result.key,
      url: result.url,
      title: result.title,
      status: result.status === "stopped" ? "stopped" : "idle",
      lifetime: "explicit",
      last_active: null,
      renameable: false,
      stoppable: false,
    },
  );
}

// ---------- the dock ----------

function dockRow(): DockEntry[] {
  return dockEntries(
    currentApps(),
    (address) => {
      const open = windowShowing(windows, address);
      return open !== undefined && !open.isMinimized;
    },
    windowedAddresses(),
    closingAddresses,
  );
}

/** Every address this client has a window for, minimized ones included: what an app that browses
 *  its own instances is represented by in the dock. */
function windowedAddresses(): Set<string> {
  return new Set(windows.filter((window) => window.kind === "instance").map((window) => window.address));
}

function setDockHiding(isHiding: boolean): void {
  desktopDocument = { ...desktopDocument, dock: { is_hiding: isHiding } };
  saveSoon();
  m.redraw();
}

// ---------- icons ----------

function moveIconTo(entryName: string, x: number, y: number): void {
  desktopDocument = {
    ...desktopDocument,
    icons: withIconMovedTo(desktopDocument.icons, currentIcons(), entryName, x, y, desktopSize),
  };
  saveSoon();
  m.redraw();
}

/** How far the pointer travels before a press on an icon becomes a drag rather than a click. */
const ICON_DRAG_THRESHOLD = 4;

/**
 * Pick an icon up.
 *
 * Pointer events rather than HTML5 drag-and-drop, which is what the windows already use: the
 * native API insists on its own cursor (the "copy" plus) and its own translucent drag image, and
 * neither is what picking something up off a desktop looks like. Here the icon ITSELF moves --
 * lifted, at full opacity, label and all -- because the element being dragged is the element on
 * the grid, translated rather than copied.
 */
function beginIconDrag(entryName: string, event: PointerEvent): void {
  const element = event.currentTarget as HTMLElement;
  const origin = currentIcons().find((icon) => icon.entry.name === entryName);
  iconDrag = {
    entryName,
    element,
    startX: event.clientX,
    startY: event.clientY,
    originX: origin?.x ?? 0,
    originY: origin?.y ?? 0,
    isMoving: false,
  };
  didLastIconPressMove = false;
  // Capture keeps the moves coming to this element once the pointer leaves it, which is most of
  // a drag. Not every environment has it, and a desktop that cannot capture should still drag.
  try {
    element.setPointerCapture(event.pointerId);
  } catch {
    /* No capture available: the listeners below still follow the pointer while it is over us. */
  }
  element.addEventListener("pointermove", onIconDragMove);
  element.addEventListener("pointerup", onIconDragEnd);
  element.addEventListener("pointercancel", onIconDragEnd);
}

/** Hold the closed hand for the whole gesture. The icon in the hand rises above the windows on
 *  its own (``.is-lifted``); the rest of the grid stays where it is, under them. */
function setIconLayerLifted(isLifted: boolean): void {
  document.getElementById("root")?.classList.toggle("is-arranging-icons", isLifted);
}

/** Whether a point (in desktop coordinates) is over a window on screen. A window is solid to an
 *  icon drag: nothing under it makes room, and a drop on it puts the icon back where it came from. */
function isPointOverWindow(x: number, y: number): boolean {
  return windows.some((window_) => {
    if (window_.isMinimized) return false;
    const rect = shownRect(window_, desktopSize);
    return x >= rect.x && x < rect.x + rect.width && y >= rect.y && y < rect.y + rect.height;
  });
}

/** The pointer's place on the desktop: client coordinates less the desktop's own offset. */
function desktopPoint(event: PointerEvent): { x: number; y: number } {
  const rect = desktopElement?.getBoundingClientRect();
  return { x: event.clientX - (rect?.left ?? 0), y: event.clientY - (rect?.top ?? 0) };
}

/** Make room under the hand: the icons between the held icon's cell and the cell the POINTER is
 *  in slide. The pointer, not the icon's corner: the icon is drawn big and low in the hand, and
 *  its corner is not where the user feels it to be. Over a window nothing moves; the grid below
 *  is not what the drag is about there. */
function makeRoomForDrag(x: number, y: number, isOverWindow: boolean): void {
  if (iconDrag === null) return;
  if (isOverWindow) {
    if (roomMadeArrangement === null) return;
    roomMadeArrangement = null;
    roomMadeCellKey = null;
    m.redraw();
    return;
  }
  const cell = cellContaining(x, y, iconGridFor(desktopSize));
  const key = `${cell.column},${cell.row}`;
  if (key === roomMadeCellKey) return;
  roomMadeCellKey = key;
  // From the SAVED arrangement each time, not from the room already made: only the icon under
  // the hand right now steps aside, and one that stepped aside for an earlier spot goes back.
  const saved = placedIcons(entries(), desktopDocument.icons, desktopSize);
  roomMadeArrangement = withRoomMadeAt(desktopDocument.icons, saved, iconDrag.entryName, cell, desktopSize);
  m.redraw();
}

function onIconDragMove(event: PointerEvent): void {
  if (iconDrag === null) return;
  const deltaX = event.clientX - iconDrag.startX;
  const deltaY = event.clientY - iconDrag.startY;
  if (!iconDrag.isMoving && Math.abs(deltaX) + Math.abs(deltaY) < ICON_DRAG_THRESHOLD) return;
  if (!iconDrag.isMoving) {
    iconDrag.isMoving = true;
    didLastIconPressMove = true;
    // Moving an icon is not choosing one: whatever was picked out is put down.
    selectedIconEntry = null;
    iconDrag.element.classList.add("is-lifted");
    setIconLayerLifted(true);
  }
  // Written straight onto the element: this moves one icon, and a redraw per frame would
  // re-render the whole desktop.
  iconDrag.element.style.transform = `translate(${deltaX}px, ${deltaY}px) scale(1.12)`;
  const pointer = desktopPoint(event);
  makeRoomForDrag(pointer.x, pointer.y, isPointOverWindow(pointer.x, pointer.y));
}

function onIconDragEnd(event: PointerEvent): void {
  if (iconDrag === null) return;
  const { entryName, element, isMoving, originX, originY } = iconDrag;
  element.removeEventListener("pointermove", onIconDragMove);
  element.removeEventListener("pointerup", onIconDragEnd);
  element.removeEventListener("pointercancel", onIconDragEnd);
  element.classList.remove("is-lifted");
  element.style.transform = "";
  iconDrag = null;
  const roomMade = roomMadeArrangement;
  roomMadeArrangement = null;
  roomMadeCellKey = null;
  if (!isMoving) return;
  setIconLayerLifted(false);
  // The press that started the drag still becomes a click, and an icon put back where it was
  // must not also open.
  isIconClickSuppressed = true;
  desktopSize = measureDesktop();
  // Dropped on a window: the window is solid, and the icon goes back where it came from.
  const pointer = desktopPoint(event);
  if (isPointOverWindow(pointer.x, pointer.y)) {
    m.redraw();
    return;
  }
  // The room made on the way is kept, then the icon takes the cell the pointer is in -- the
  // one the room was made under.
  if (roomMade !== null) desktopDocument = { ...desktopDocument, icons: roomMade };
  const landing = cellOrigin(cellContaining(pointer.x, pointer.y, iconGridFor(desktopSize)));
  moveIconTo(entryName, landing.x, landing.y);
  // It is already in the hand next to its new cell: it settles there, rather than sliding over
  // from the old cell the way an icon making room does. Held at the new cell by its transform
  // until the redraw has moved its corner there, then let go, with the slide switched off.
  const landed = currentIcons().find((icon) => icon.entry.name === entryName);
  if (landed === undefined) return;
  droppingEntryName = entryName;
  element.classList.add("is-dropping");
  element.style.transform = `translate(${landed.x - originX}px, ${landed.y - originY}px)`;
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      element.style.transform = "";
      requestAnimationFrame(() => {
        droppingEntryName = null;
        element.classList.remove("is-dropping");
      });
    });
  });
}

// ---------- boot ----------

let isInitialized = false;

async function initializeDesktop(): Promise<void> {
  initChatUnread();
  const generation = ++viewMountGeneration;
  const deepLink = takeDeepLinkFromLocation();
  const [listed, recordedViewId] = await Promise.all([fetchProjectsList(), fetchOwnActiveView(getClientId())]);
  if (generation !== viewMountGeneration) return;
  const projects = listed ?? [];
  const chosenId = chooseInitialViewId(projects, knownDeepLinkViewId(deepLink, projects) ?? recordedViewId);
  setActiveProjectId(chosenId);
  mountedViewId = chosenId;
  reportClientState();
  const layout = await fetchLayoutOrSuspendSaves(chosenId);
  if (generation !== viewMountGeneration) return;
  await applyLayout(layout, generation);
  if (generation === viewMountGeneration) void applyDeepLinkTargets(deepLink);
}

function knownDeepLinkViewId(link: DeepLink, projects: readonly ProjectInfo[]): string | null {
  if (link.viewId === null) return null;
  return isEverythingView(link.viewId) || projectForViewId(projects, link.viewId) !== null ? link.viewId : null;
}

function takeDeepLinkFromLocation(): DeepLink {
  const link = parseDeepLink(window.location.search);
  if (isDeepLinkEmpty(link)) return link;
  const stripped = `${window.location.pathname}${stripDeepLinkParams(window.location.search)}${window.location.hash}`;
  window.history.replaceState(window.history.state, "", stripped);
  return link;
}

async function applyDeepLinkTargets(link: DeepLink): Promise<void> {
  if (link.openAddress !== null) await openAddressWhenListed(link.openAddress, DEEP_LINK_ADDRESS_TIMEOUT_MS);
  if (link.action !== null) {
    await whenAppsLoaded();
    const app = getApp(link.action.app);
    if (app !== undefined) await openApp(app);
  }
  m.redraw();
}

/** The inventory moved: let go of the page of anything no app lists any more. */
function reconcileWithInventory(): void {
  settlePendingTitles((address) => findInstance(address)?.instance.title ?? null);
  for (const key of liveSurfaceKeys()) {
    if (isAddressUnlisted(key) && windowShowing(windows, key) === undefined && !isHeld(key)) destroyLiveSurface(key);
  }
  m.redraw();
}

function wireListeners(): void {
  setEmbedderMessageHandler(CLOSE_ACTIVE_TAB, closeActiveWindowFromEmbedder);
  setChildFrameMessageHandler(SHELL_FOCUSED, raiseWindowForChildFrame);
  setChildFrameMessageHandler(SHELL_OPEN, openInstanceForChildFrame);
  setChildFrameMessageHandler(SHELL_LOCATION, relayLocationForChildFrame);
  setChildFrameMessageHandler(SHELL_TITLE, adoptTitleForChildFrame);

  addAppsUpdatedListener((apps) => {
    reconcileWithInventory();
    // The chat list's "done" state: an instance that stops working while no window here shows it.
    const statusByAddress = new Map<string, string>();
    for (const app of apps) {
      for (const instance of app.instances) statusByAddress.set(addressFor(app.name, instance.key), instance.status);
    }
    noteStatuses(statusByAddress, (address) => {
      const shown = windowShowing(windows, address);
      return shown !== undefined && !shown.isMinimized;
    });
  });
  addLayoutOpListener(handleLayoutOp);
  addLayoutUpdatedListener((event) => {
    if (event.clientId !== getClientId() || event.viewId !== mountedViewId) return;
    if (isOwnSaveId(event.saveId)) return;
    void refreshDesktopFromServer();
  });
  addActiveViewChangedListener((event) => {
    if (event.clientId !== getClientId() || event.viewId === mountedViewId) return;
    void switchToView(event.viewId);
  });
  addTabReboundListener((event) => {
    if (event.clientId !== getClientId() || event.viewId !== mountedViewId) return;
    const window_ = windowWithTabId(windows, event.tabId);
    if (window_ === undefined || window_.kind !== "instance" || window_.address === event.address) return;
    rekeyLiveSurface(window_.address, event.address);
    windows = withUpdatedWindow(windows, event.tabId, (state) => ({ ...state, address: event.address }));
    m.redraw();
  });

  window.addEventListener("resize", () => {
    desktopSize = measureDesktop();
    scheduleReconcile();
    m.redraw();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    // Escape puts away whatever is up, wherever the keyboard happens to be. The field handles its
    // own Escape; this is the one pressed from anywhere else on the desktop.
    if (isLauncherOpen) setLauncherOpen(false);
  });
}

// ---------- rendering ----------

/** The tint an entry's tile wears: the desktop's own dark one, or the app's. */
function tintForEntry(entry: DesktopEntry): { background: string; foreground: string } {
  return entry.name === MAKE_SOMETHING_ENTRY ? MAKE_SOMETHING_TINT : tintForApp(entry.name);
}

function iconTile(entry: DesktopEntry): m.Children {
  const tint = tintForEntry(entry);
  const markup =
    entry.app === null
      ? glyph("sparkle", 1.8)
      : appIconMarkup(entry.app.icon, ICON_GLYPH_SIZE, glyph("app", 1.8), entry.app.name);
  return m("span.icon-tile", { style: `background:${tint.background};color:${tint.foreground}` }, m.trust(markup));
}

function desktopIcons(): m.Children {
  return currentIcons().map((icon) =>
    m(
      "button.icon",
      {
        key: icon.entry.name,
        "data-entry": icon.entry.name,
        "data-x": Math.round(icon.x),
        "data-y": Math.round(icon.y),
        "data-column": icon.cell.column,
        "data-row": icon.cell.row,
        // In a cell of the grid that spans the whole desktop, so an icon can be at the right edge
        // or along the bottom -- but always in a cell, and never sharing one. The icon in the
        // hand is the exception: its corner stays where it was picked up, whatever cell the room
        // being made puts it in, because the drag moves it from there.
        style:
          iconDrag !== null && iconDrag.entryName === icon.entry.name
            ? `left:${iconDrag.originX}px;top:${iconDrag.originY}px;${iconDrag.element.style.transform === "" ? "" : `transform:${iconDrag.element.style.transform}`}`
            : `left:${icon.x}px;top:${icon.y}px`,
        // The lifted and dropping states are drawn here too, so a redraw mid-gesture (the room
        // being made) does not take them off the element.
        class: [
          selectedIconEntry === icon.entry.name ? "is-selected" : "",
          iconDrag !== null && iconDrag.isMoving && iconDrag.entryName === icon.entry.name ? "is-lifted" : "",
          droppingEntryName === icon.entry.name ? "is-dropping" : "",
        ]
          .filter((name) => name !== "")
          .join(" "),
        onpointerdown: (event: PointerEvent) => {
          if (event.button !== 0) return;
          beginIconDrag(icon.entry.name, event);
        },
        // A single click picks the icon out; the double click opens it. A press that turned into
        // a drag does neither: the icon was being moved, not chosen.
        onclick: () => {
          if (isIconClickSuppressed) {
            isIconClickSuppressed = false;
            return;
          }
          selectedIconEntry = icon.entry.name;
          m.redraw();
        },
        ondblclick: () => {
          // Two drags that ended in the same place are still two drags, whatever the browser
          // counts them as.
          if (didLastIconPressMove) return;
          openEntry(icon.entry);
        },
        onkeydown: (event: KeyboardEvent) => {
          // Keyboard has no double click, so Enter and Space open the icon that has the focus --
          // without this there would be no way to open one at all from the keyboard.
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          openEntry(icon.entry);
        },
      },
      [iconTile(icon.entry), m("span.icon-label", icon.entry.label)],
    ),
  );
}

/** Every icon and where it sits right now, against the desktop as it is at this moment -- with
 *  the room made for an icon in the hand, while there is one. */
function currentIcons(): PlacedIcon[] {
  return placedIcons(entries(), roomMadeArrangement ?? desktopDocument.icons, desktopSize);
}

function windowChip(window_: WindowState): m.Children {
  if (window_.kind === "make") {
    return m(
      "span.titlebar-chip",
      { style: `background:${MAKE_SOMETHING_TINT.background};color:${MAKE_SOMETHING_TINT.foreground}` },
      m.trust(glyph("sparkle", 1.8)),
    );
  }
  const resolved = findInstance(window_.address);
  const appName = appNameFromAddress(window_.address) ?? "";
  const tint = tintForApp(appName);
  return m(
    "span.titlebar-chip",
    { style: `background:${tint.background};color:${tint.foreground}` },
    m.trust(appIconMarkup(resolved?.app.icon, WINDOW_GLYPH_SIZE, glyph("app", 1.8), appName)),
  );
}

/**
 * What a window is called.
 *
 * The live title while its instance is listed; then what the window was last called, which is
 * what a restored window whose instance has gone still knows; then the app's own name. Never the
 * address: ``app:files?instance=files-1`` is developer text and the user should not meet it. An
 * instance disappearing from under a saved desktop is ordinary -- something stopped and then
 * deleted, an app that forgot a session, a workspace restored from a backup -- so this is a state
 * to name well rather than an edge case.
 */
function windowTitle(window_: WindowState): string {
  if (window_.kind === "make") return MAKE_SOMETHING_LABEL;
  const pending = pendingTitle(window_.address);
  if (pending !== undefined) return pending;
  const resolved = findInstance(window_.address);
  if (resolved !== null && resolved.instance.title !== "") return resolved.instance.title;
  if (window_.lastKnownTitle !== null) return window_.lastKnownTitle;
  const appName = appNameFromAddress(window_.address);
  return (appName === null ? undefined : getApp(appName)?.display_name) ?? UNTITLED_WINDOW_TITLE;
}

/** Whether what this window shows is not listed by any app right now. The window stays -- the app
 *  may list it again, and it reconnects when it does -- but it is drawn as the empty frame it is. */
function isWindowUnavailable(window_: WindowState): boolean {
  return window_.kind === "instance" && findInstance(window_.address) === null;
}

function isRenameable(window_: WindowState): boolean {
  return window_.kind === "instance" && findInstance(window_.address) !== null;
}

function titleBarButton(
  label: string,
  glyphName: "maximize" | "restore" | "minimize" | "close",
  onclick: () => void,
): m.Vnode {
  return m(
    "button.win-btn",
    {
      class: glyphName === "close" ? "win-close" : "",
      "aria-label": label,
      "data-action": glyphName,
      oncreate: (created: m.VnodeDOM) => setHoverTooltip(created.dom as HTMLElement, label),
      onclick: (event: MouseEvent) => {
        event.stopPropagation();
        onclick();
      },
    },
    m.trust(glyph(glyphName)),
  );
}

const RESIZE_EDGES: readonly ResizeEdge[] = ["n", "s", "e", "w", "ne", "nw", "se", "sw"];

/** The window in front: the topmost one that is actually on screen, which a minimized window
 *  above it in the stack is not. */
function frontWindowTabId(): string | null {
  for (let index = windows.length - 1; index >= 0; index -= 1) {
    const candidate = windows[index];
    if (candidate !== undefined && !candidate.isMinimized) return candidate.tabId;
  }
  return null;
}

/**
 * The sheet that makes a click on a window that is not in front bring it forward instead of
 * pressing what is under the pointer.
 *
 * It covers the whole window, its title bar and its buttons included: stopping a chat you only
 * meant to look at is the accident worth preventing, and half a shield would be a worse rule to
 * learn than none. A press that lands on the title bar still starts a drag in the same gesture,
 * so bringing a window forward and moving it is one motion rather than two -- the resize edges
 * sit above this and keep working for the same reason.
 */
function windowShield(window_: WindowState): m.Vnode {
  return m("div.window-shield", {
    onpointerdown: (event: PointerEvent) => {
      raiseWindow(window_.tabId);
      const frame = (event.currentTarget as HTMLElement).closest(".window");
      if (!(frame instanceof HTMLElement)) return;
      if (isPointerOver(frame.querySelector(".titlebar"), event.clientX, event.clientY)) {
        beginGesture(window_.tabId, frame, event, null);
      }
    },
  });
}

function windowFrame(window_: WindowState, stackIndex: number): m.Vnode {
  const isRenaming = renamingTabId === window_.tabId;
  const isInFront = frontWindowTabId() === window_.tabId;
  const drawn = shownRect(window_, desktopSize);
  return m(
    "div.window",
    {
      key: window_.tabId,
      "data-tab-id": window_.tabId,
      "data-address": window_.address,
      class: [
        window_.isMaximized ? "is-maximized" : "",
        window_.isMinimized ? "is-minimized" : "",
        isInFront ? "is-front" : "",
        isWindowUnavailable(window_) ? "is-unavailable" : "",
      ]
        .filter((name) => name !== "")
        .join(" "),
      style: `left:${drawn.x}px;top:${drawn.y}px;width:${drawn.width}px;height:${drawn.height}px;z-index:${windowZIndex(stackIndex)}`,
      onpointerdown: () => {
        if (windows[windows.length - 1]?.tabId !== window_.tabId) raiseWindow(window_.tabId);
      },
    },
    [
      m(
        "div.titlebar",
        {
          onpointerdown: (event: PointerEvent) => {
            if ((event.target as HTMLElement).closest(".win-btn") !== null) return;
            if (isRenaming) return;
            const frame = (event.currentTarget as HTMLElement).closest(".window");
            if (frame instanceof HTMLElement) beginGesture(window_.tabId, frame, event, null);
          },
          ondblclick: (event: MouseEvent) => {
            if ((event.target as HTMLElement).closest(".win-btn") !== null) return;
            // The title itself renames; the rest of the bar maximizes. Decided by where the
            // pointer is rather than by the event's target: when a double-click's two clicks land
            // on different nodes the browser reports their common ancestor -- the bar -- and the
            // title would never match.
            const titleNode = (event.currentTarget as HTMLElement).querySelector(".titlebar-title");
            if (isPointerOver(titleNode, event.clientX, event.clientY) && isRenameable(window_)) {
              event.preventDefault();
              renamingTabId = window_.tabId;
              m.redraw();
              return;
            }
            toggleMaximize(window_.tabId);
          },
        },
        [
          windowChip(window_),
          isRenaming
            ? m("input.title-rename", {
                value: windowTitle(window_),
                oncreate: (created: m.VnodeDOM) => {
                  const field = created.dom as HTMLInputElement;
                  field.focus();
                  field.select();
                },
                onkeydown: (event: KeyboardEvent) => {
                  event.stopPropagation();
                  if (event.key === "Enter") commitRename(window_.tabId, (event.target as HTMLInputElement).value);
                  if (event.key === "Escape") {
                    renamingTabId = null;
                    m.redraw();
                  }
                },
                onblur: (event: FocusEvent) => commitRename(window_.tabId, (event.target as HTMLInputElement).value),
                onpointerdown: (event: PointerEvent) => event.stopPropagation(),
                ondblclick: (event: MouseEvent) => event.stopPropagation(),
              })
            : m("span.titlebar-title", windowTitle(window_)),
          m("span.titlebar-actions", [
            titleBarButton(
              window_.isMaximized ? "Restore" : "Maximize",
              window_.isMaximized ? "restore" : "maximize",
              () => toggleMaximize(window_.tabId),
            ),
            titleBarButton("Minimize", "minimize", () => minimizeWindow(window_.tabId)),
            titleBarButton("Close", "close", () => closeWindow(window_.tabId)),
          ]),
        ],
      ),
      m(
        "div.window-body",
        // A body showing a page is see-through, and lets pointers through to the page under it;
        // one holding the window's own content keeps both.
        { class: window_.kind === "make" ? "" : "holds-page" },
        window_.kind === "make"
          ? m(MakeSomething, {
              catalog: getTemplateCatalogState(),
              onStart: (prompt: string) => void startSeededChat(prompt),
            })
          : [
              // An app that lists its own instances gets its list down the left of the window,
              // beside the page: the chat list. The desktop's, so the app stays a page per chat.
              railFor(window_),
              m("div.window-slot", {
                // The slot is a see-through box: the page itself lives in the desktop's own layer,
                // just under this window, so minimizing or rearranging a window never reloads it.
                oncreate: (created: m.VnodeDOM) => bindWindowSlot(window_, created.dom as HTMLElement),
                onupdate: (updated: m.VnodeDOM) => bindWindowSlot(window_, updated.dom as HTMLElement),
                onremove: () => unbindSlot(window_.tabId),
              }),
            ],
      ),
      window_.isMaximized
        ? null
        : RESIZE_EDGES.map((edge) =>
            m(`div.resize-handle.resize-${edge}`, {
              "data-edge": edge,
              onpointerdown: (event: PointerEvent) => {
                event.stopPropagation();
                const frame = (event.currentTarget as HTMLElement).closest(".window");
                if (!(frame instanceof HTMLElement)) return;
                // Grabbing an edge is direct enough to be worth a raise of its own: the press is
                // swallowed here, so the window's own raise never sees it.
                raiseWindow(window_.tabId);
                beginGesture(window_.tabId, frame, event, edge);
              },
            }),
          ),
      isInFront ? null : windowShield(window_),
    ],
  );
}

/**
 * The radius the window holding this slot draws its bottom corners with.
 *
 * Read off the window rather than repeated here, so the rounding stays the stylesheet's -- and so
 * the 0 that ``.window.is-maximized`` drops it to follows for free.
 */
function bottomCornerRadiusOfWindow(slot: HTMLElement): string {
  const frame = slot.closest(".window");
  if (!(frame instanceof HTMLElement)) return "0px";
  return getComputedStyle(frame).borderBottomLeftRadius;
}

/** Give a window's page the box it is drawn over, and tell it where it sits in the stack. */
/** The rail down the left of a listing app's window, or nothing for any other window. */
function railFor(window_: WindowState): m.Children {
  if (window_.kind !== "instance") return null;
  const appName = appNameFromAddress(window_.address);
  const app = appName === null ? undefined : getApp(appName);
  if (app === undefined || !app.browses_instances) return null;
  return m(InstanceRail, {
    app,
    tabId: window_.tabId,
    shownAddress: window_.address,
    isOnScreen: !window_.isMinimized && frontWindowTabId() === window_.tabId,
    onPick: (address: string) => showInstanceInWindow(window_.tabId, address),
    onNew: () => void startInstanceInWindow(window_.tabId, app),
  });
}

function bindWindowSlot(window_: WindowState, element: HTMLElement): void {
  if (window_.kind !== "instance") return;
  const surface = ensureLiveSurface(window_.address, window_.tabId, mountLiveContent);
  bindSlot(surface, window_.tabId, {
    element,
    isShowing: () => windowWithTabId(windows, window_.tabId)?.isMinimized === false,
    stackIndex: () =>
      Math.max(
        0,
        windows.findIndex((candidate) => candidate.tabId === window_.tabId),
      ),
    bottomCornerRadius: () => bottomCornerRadiusOfWindow(element),
    // Beside a rail the page starts mid-window: its bottom-left corner is square against the rail.
    bottomLeftCornerRadius: () => (railFor(window_) === null ? bottomCornerRadiusOfWindow(element) : "0px"),
  });
}

function dockTile(entry: DockEntry): m.Vnode {
  const tint = tintForApp(entry.app.name);
  return m(
    "button.dock-item",
    {
      key: entry.address,
      "data-address": entry.address,
      class: entry.isOnScreen ? "is-open" : "is-offscreen",
      style: `background:${tint.background};color:${tint.foreground}`,
      oncreate: (created: m.VnodeDOM) => setHoverTooltip(created.dom as HTMLElement, dockTooltip(entry)),
      onupdate: (updated: m.VnodeDOM) => setHoverTooltip(updated.dom as HTMLElement, dockTooltip(entry)),
      // Always brings it forward: putting a window away stays on the window's own control, so a
      // click here never hides what the user was reaching for. The menu, if it is up, goes: its
      // scrim sits under the dock so the field stays reachable, which leaves this to close it.
      onclick: () => {
        if (isLauncherOpen) setLauncherOpen(false);
        void openInstance(entry.app, entry.instance);
      },
    },
    m.trust(
      appIconMarkup(entry.app.icon, Math.round(DOCK_TILE_PX * DOCK_GLYPH_RATIO), glyph("app", 1.8), entry.app.name),
    ),
  );
}

/**
 * The ``+``: one control that is the way into everything. Closed, it is a round outlined button.
 * Open, it widens into a field -- the ``+`` stays as its glyph -- with the menu over it, and
 * typing into the field searches. The menu rendering is ``Launcher``; this is the control in the
 * dock that opens it.
 */
function launchControl(): m.Vnode {
  return m(
    "div.dock-launch",
    {
      class: isLauncherOpen ? "is-open" : "",
      // A press on the open control's padding puts the cursor back in the field.
      onclick: () => {
        if (isLauncherOpen) searchFieldElement?.focus();
      },
    },
    [
      m(
        "button#dock-new.dock-new",
        {
          "aria-label": isLauncherOpen ? "Close" : "Open something",
          "aria-expanded": isLauncherOpen ? "true" : "false",
          oncreate: (created: m.VnodeDOM) => setHoverTooltip(created.dom as HTMLElement, "Open something"),
          onclick: (event: MouseEvent) => {
            event.stopPropagation();
            setLauncherOpen(!isLauncherOpen);
          },
        },
        m.trust(glyph("plus", 2.2)),
      ),
      m("input#dock-search-field.dock-search-field", {
        type: "text",
        placeholder: SEARCH_PLACEHOLDER,
        "aria-label": SEARCH_PLACEHOLDER,
        value: searchQuery,
        tabindex: isLauncherOpen ? undefined : -1,
        oncreate: (created: m.VnodeDOM) => {
          searchFieldElement = created.dom as HTMLInputElement;
        },
        oninput: (event: InputEvent) => setSearchQuery((event.target as HTMLInputElement).value),
        onkeydown: (event: KeyboardEvent) => {
          if (event.key !== "Escape") return;
          // Handled here rather than left to the document, so Escape in the field is about the
          // field and never reaches whatever else is listening for it. The first Escape clears
          // what was typed; a second closes the control.
          event.stopPropagation();
          if (searchQuery !== "") setSearchQuery("");
          else setLauncherOpen(false);
        },
      }),
    ],
  );
}

function dock(): m.Vnode {
  const row = dockRow();
  const isHiding = desktopDocument.dock.is_hiding;
  return m(
    "div#dock",
    {
      class: isHiding ? "is-hidden" : "",
      oncreate: (created: m.VnodeDOM) => {
        dockElement = created.dom as HTMLElement;
      },
      onpointerleave: () => {
        if (isHiding) (dockElement as HTMLElement | null)?.classList.add("is-hidden");
      },
    },
    [
      launchControl(),
      m("div.dock-divider"),
      m(
        "div.dock-items",
        row.map((entry) => dockTile(entry)),
      ),
      m(
        "div.dock-hint",
        m(
          "button#dock-autohide.dock-toggle",
          {
            "aria-label": isHiding ? "Turn hiding off" : "Turn hiding on",
            oncreate: (created: m.VnodeDOM) =>
              setHoverTooltip(created.dom as HTMLElement, isHiding ? "Turn hiding off" : "Turn hiding on"),
            onupdate: (updated: m.VnodeDOM) =>
              setHoverTooltip(updated.dom as HTMLElement, isHiding ? "Turn hiding off" : "Turn hiding on"),
            onclick: () => setDockHiding(!isHiding),
          },
          // The dashed eye is a much finer drawing than the solid one, so it is given a heavier
          // stroke: at 17px the pair reads as one weight rather than as two.
          m.trust(isHiding ? glyph("eye", 1.8) : glyph("eyeDashed", 2.1)),
        ),
      ),
    ],
  );
}

/** Forget every window and everything about the desktop's own state. Test-only. */
export function resetDesktopForTesting(): void {
  for (const key of liveSurfaceKeys()) destroyLiveSurface(key);
  windows = [];
  desktopDocument = emptyDesktop();
  desktopSize = FALLBACK_DESKTOP_SIZE;
  desktopElement = null;
  dockElement = null;
  mountedViewId = null;
  baseUpdatedAt = null;
  isSaveSuspended = false;
  lastSavedJson = null;
  isInitialized = false;
  isLauncherOpen = false;
  searchFieldElement = null;
  searchQuery = "";
  searchResults = [];
  isSearchPending = false;
  renamingTabId = null;
  closingAddresses.clear();
  actionsInFlight.clear();
}

export const DesktopShell: m.Component = {
  oncreate(vnode: m.VnodeDOM) {
    if (isInitialized) return;
    isInitialized = true;
    const desktop = (vnode.dom as HTMLElement).querySelector("#desktop");
    const surfaces = (vnode.dom as HTMLElement).querySelector("#surfaces");
    if (desktop instanceof HTMLElement) {
      desktopElement = desktop;
      desktopSize = measureDesktop();
    }
    // The pages live in a layer of their own rather than among the windows, so mithril's diffing
    // never moves or drops an iframe -- either of which would reload the document inside it.
    if (surfaces instanceof HTMLElement) initializeLiveLayer(surfaces, () => m.redraw());
    wireListeners();
    void initializeDesktop();
  },

  onupdate() {
    scheduleReconcile();
  },

  view() {
    const isHiding = desktopDocument.dock.is_hiding;
    return m(
      "div#root",
      {
        class: isHiding ? "dock-auto" : "",
        "data-view-id": mountedViewId ?? "",
        // A press anywhere that is not an icon puts the picked-out icon down again: the empty
        // desktop, a window, the dock. A press inside a framed page never reaches here, so an
        // icon stays picked out while the user works in a window, which is what a desktop does.
        onpointerdown: (event: PointerEvent) => {
          if (selectedIconEntry === null) return;
          if ((event.target as HTMLElement).closest(".icon") !== null) return;
          selectedIconEntry = null;
          m.redraw();
        },
      },
      [
        m("div#desktop", [
          m("div#icons", desktopIcons()),
          // The windows are keyed -- a window has to keep its DOM node when the stack is reordered
          // -- and mithril only allows keys where every child of the array has one, so they get a
          // layer of their own rather than sitting among the desktop's furniture.
          m(
            "div#windows",
            windows.map((window_, index) => windowFrame(window_, index)),
          ),
          m("div#surfaces"),
        ]),
        // Invisible, but it swallows the click that dismisses the menu so the same click cannot
        // also press whatever sits underneath. It sits UNDER the dock rather than over it: a press
        // anywhere on the desktop puts the menu away, and the field the user is typing in stays
        // reachable the whole time.
        m("div#launcher-scrim", {
          class: isLauncherOpen ? "is-open" : "",
          onpointerdown: (event: PointerEvent) => {
            event.stopPropagation();
            event.preventDefault();
            setLauncherOpen(false);
          },
        }),
        m(Launcher, {
          isOpen: isLauncherOpen,
          query: searchQuery,
          items: launcherItemsMatching(launcherMenuItems(currentApps()), searchQuery),
          results: searchResults,
          isSearching: isSearchPending,
          onPickItem: (item: LauncherItem) => void pickLauncherItem(item),
          onOpenResult: (result: SearchResult) => void openSearchResult(result),
        }),
        m("div#dock-hover-strip", {
          onpointerenter: () => {
            if (isHiding) dockElement?.classList.remove("is-hidden");
          },
        }),
        dock(),
      ],
    );
  },
};
