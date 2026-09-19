/**
 * The live pages, and which window is showing each one.
 *
 * There is ONE live page per instance, machine-wide. Removing an iframe from the document destroys
 * it, and re-parenting one reloads it, so the element holding a page must never leave the DOM --
 * not when a window is minimized, not when it is dragged, not when the desktop is rearranged. The
 * instance leaving its app's list is the one thing that takes it out.
 *
 * So a window's body is demoted to a **slot**: a transparent, pointer-transparent box the window
 * draws and moves, which a surface mirrors. A surface lives in the desktop's own coordinate space,
 * is positioned behind whichever slot currently shows it, and outlives every one of them -- which
 * is what makes minimizing a window free to undo, since the page never stopped running.
 *
 * A page sits just BELOW its own window and above every window under it (see ``surfaceZIndex``), so
 * the window keeps the parts of itself that overlap the page -- its resize edges, and the shield an
 * unfocused window puts up -- while the page shows through the slot.
 *
 * The desktop owns everything that knows what a page *is* -- which url it loads, what it is
 * called, what closing it means. This module owns only where the live elements are, how big,
 * whether anything is looking at them, and where they sit in the stack.
 */

import m from "mithril";

/** The identity a live page is filed under: the instance's address. */
export type LiveKey = string;

/** The box a window offers a page, and whether the window is showing it right now. */
export interface SurfaceSlot {
  /** The window body the page is drawn over. */
  element: HTMLElement;
  /** False while the window is minimized: the page stays loaded, and nothing is looking at it. */
  isShowing: () => boolean;
  /** Where the page sits in the stack, so a window above it draws over it. */
  stackIndex: () => number;
  /**
   * The radius the window's bottom corners are drawn with, as a CSS length.
   *
   * The page has to round its own bottom corners to that, because it is NOT inside the window:
   * it is a sibling layer painted over the window's body, so the window's own ``border-radius``
   * and ``overflow: hidden`` cannot clip it, and a square-cornered page overruns the window's
   * rounded outline. The window's top corners are the title bar's, which is opaque and inside
   * the window, so only the bottom two are the page's problem.
   *
   * Read afresh on every reconcile rather than once, since it follows the window's state -- a
   * maximized window has no radius at all.
   */
  bottomCornerRadius: () => string;
  /** The bottom-LEFT corner's radius when it differs: none at all where the window draws its own
   *  rail down the left, so the page starts mid-window with a square corner against it. Defaults
   *  to ``bottomCornerRadius``. */
  bottomLeftCornerRadius?: () => string;
}

/** One live page: the element that holds it, and whether any window is currently showing it. */
export interface LiveSurface {
  /** The address this page is filed under. Follows the instance when a window is rebound. */
  key: LiveKey;
  /** The element that holds the page. Created once and removed only when the instance is gone. */
  readonly element: HTMLElement;
  /** The page's id: minted when it was first opened and baked into its url. */
  readonly tabId: string;
  /** Whether a window is showing this page right now. */
  isVisible: boolean;
  /** The path the page itself last reported (``shell:location``), cleared by the frame's next
   *  load. A listed url equal to it is where the page already is, so no reload. */
  lastReportedPath: string | null;
  /** The window currently showing this page, if any. */
  boundSlotId: string | null;
  boundSlot: SurfaceSlot | null;
  unmount: () => void;
}

// While a window is being dragged or resized the surfaces stop taking pointer events, so the
// gesture is not swallowed by a framed page the pointer crosses.
const SURFACE_DRAG_CLASS = "si-live-surface--drag";

/**
 * Where the window stack starts, above the desktop's icons.
 *
 * A window and the page it shows take two levels each, and every one of them has to be above the
 * icon grid -- which is itself a layer, and would otherwise swallow the pointer over any window
 * that overlapped it.
 */
export const WINDOW_Z_BASE = 11;

/** The z-index a window at ``stackIndex`` is drawn at. */
export function windowZIndex(stackIndex: number): number {
  return WINDOW_Z_BASE + stackIndex * 2;
}

/**
 * The z-index the page shown by the window at ``stackIndex`` is drawn at: one BELOW its own
 * window, and above every window under it.
 *
 * A window has to be able to draw and act over its own page, because the parts of a window that
 * overlap the page are the parts the page cannot be allowed to swallow: the resize edges and
 * corners, which reach in over the page's outer few pixels exactly as they did when the page was
 * a child of the window, and the shield that makes a click on an unfocused window raise it
 * instead of pressing what is under the pointer. A page above its window would take every one of
 * those pointers -- and a cross-origin frame takes them silently, so nothing reaches the window
 * at all.
 *
 * It still interleaves correctly: page ``2i-1`` sits under window ``2i``, and the next window's
 * page at ``2i+1`` sits over both, so raising a window brings its page with it.
 */
export function surfaceZIndex(stackIndex: number): number {
  return windowZIndex(stackIndex) - 1;
}

const surfacesByKey = new Map<LiveKey, LiveSurface>();
let layerHost: HTMLElement | null = null;
let onVisibilityChanged: (() => void) | null = null;
let reconcileFrame: number | null = null;
let isGestureUnderWay = false;

// ---------- The registry ----------

/**
 * Point the registry at the layer its surfaces live in (the desktop area), and at what to call
 * when a page starts or stops being looked at.
 */
export function initializeLiveLayer(host: HTMLElement, visibilityListener: () => void): void {
  layerHost = host;
  onVisibilityChanged = visibilityListener;
}

/** The window currently showing the page filed under ``key``, or null when none is. */
export function liveSurfaceBoundSlotId(key: LiveKey): string | null {
  return surfacesByKey.get(key)?.boundSlotId ?? null;
}

/** The live page's DOM element for ``key``, or null when it has none. */
export function liveSurfaceElement(key: LiveKey): HTMLElement | null {
  return surfacesByKey.get(key)?.element ?? null;
}

/** Every address a live page is currently filed under. */
export function liveSurfaceKeys(): LiveKey[] {
  return Array.from(surfacesByKey.keys());
}

/**
 * The page for ``key``, creating it on first open.
 *
 * ``mountContent`` runs exactly once per instance, ever: the page outlives every window that shows
 * it, so an existing page is handed back untouched -- same document, same page id, same scroll
 * position -- no matter what the caller was about to render into it.
 */
export function ensureLiveSurface(
  key: LiveKey,
  tabId: string,
  mountContent: (surface: LiveSurface) => void,
): LiveSurface {
  const existing = surfacesByKey.get(key);
  if (existing !== undefined) return existing;
  if (layerHost === null) {
    throw new Error("desktop: a live page was opened before the live layer was initialized");
  }
  const element = document.createElement("div");
  element.className = `si-live-surface${isGestureUnderWay ? ` ${SURFACE_DRAG_CLASS}` : ""}`;
  element.style.display = "none";
  const surface: LiveSurface = {
    key,
    element,
    tabId,
    isVisible: false,
    lastReportedPath: null,
    boundSlotId: null,
    boundSlot: null,
    unmount: () => {
      m.mount(element, null);
    },
  };
  surfacesByKey.set(key, surface);
  layerHost.appendChild(element);
  mountContent(surface);
  return surface;
}

/** Remember the path a page reported for itself, so the record catching up with it is not a reload. */
export function recordReportedPath(key: LiveKey, path: string): void {
  const surface = surfacesByKey.get(key);
  if (surface !== undefined) surface.lastReportedPath = path;
}

/** The shell pointed the frame elsewhere: whatever the page last reported was about the document
 *  being left. A page's own navigation keeps its report, since the page posts it while loading,
 *  before the frame's load event, and the record catching up with it must not reload the page. */
export function clearReportedPath(key: LiveKey): void {
  const surface = surfacesByKey.get(key);
  if (surface !== undefined) surface.lastReportedPath = null;
}

/** Whether the page is already at ``listedUrl``: its path (query and fragment included) is
 *  exactly what the page last reported, so the record merely caught up with the page. An
 *  agent's replace-url names another path and still lands (contracts.md section 10). */
export function isPageAtListedUrl(listedUrl: string, lastReportedPath: string | null): boolean {
  if (lastReportedPath === null) return false;
  const parsed = new URL(listedUrl, "http://placeholder.invalid");
  return `${parsed.pathname}${parsed.search}${parsed.hash}` === lastReportedPath;
}

/** Re-file a page under a new address without touching the page: a window whose app re-pointed it
 *  at another instance keeps its iframe. An instance has one page, so a page already filed under
 *  the new address goes. */
export function rekeyLiveSurface(fromKey: LiveKey, toKey: LiveKey): void {
  if (fromKey === toKey) return;
  const surface = surfacesByKey.get(fromKey);
  if (surface === undefined) return;
  destroyLiveSurface(toKey);
  surfacesByKey.delete(fromKey);
  surface.key = toKey;
  surfacesByKey.set(toKey, surface);
}

/** Show ``surface`` in the window ``slotId`` names. A window shows one page: whatever page the
 *  window showed before lets go of the slot (and stays loaded, unshown, for when it is picked
 *  again -- a chat list's window switches between the chats it holds this way). */
export function bindSlot(surface: LiveSurface, slotId: string, slot: SurfaceSlot): void {
  for (const other of surfacesByKey.values()) {
    if (other !== surface && other.boundSlotId === slotId) {
      other.boundSlotId = null;
      other.boundSlot = null;
    }
  }
  surface.boundSlotId = slotId;
  surface.boundSlot = slot;
  scheduleReconcile();
}

/** Stop showing whatever page ``slotId``'s window was showing. */
export function unbindSlot(slotId: string): void {
  for (const surface of surfacesByKey.values()) {
    if (surface.boundSlotId !== slotId) continue;
    surface.boundSlotId = null;
    surface.boundSlot = null;
    scheduleReconcile();
    return;
  }
}

/** Tear a page down. The only path that takes an element out of the DOM, and it exists for
 *  exactly one reason: the instance behind the page is gone from its app's list. */
export function destroyLiveSurface(key: LiveKey): void {
  const surface = surfacesByKey.get(key);
  if (surface === undefined) return;
  surfacesByKey.delete(key);
  surface.boundSlotId = null;
  surface.boundSlot = null;
  surface.unmount();
  surface.element.remove();
}

/** Whether a window is being dragged or resized right now. */
export function isGestureInProgress(): boolean {
  return isGestureUnderWay;
}

/** Step every surface out of the way of a drag or a resize, or back into it. Without this the
 *  pointer would be swallowed by whichever framed page it crossed. */
export function setGestureInProgress(active: boolean): void {
  if (isGestureUnderWay === active) return;
  isGestureUnderWay = active;
  for (const surface of surfacesByKey.values()) {
    surface.element.classList.toggle(SURFACE_DRAG_CLASS, active);
  }
}

/** Ask for a reconcile on the next frame. Safe to call from anywhere that might have moved,
 *  resized, shown or hidden a window. */
export function scheduleReconcile(): void {
  if (reconcileFrame !== null) return;
  reconcileFrame = requestAnimationFrame(() => {
    reconcileFrame = null;
    reconcileLiveSurfaces();
  });
}

/**
 * Put every page where its window is, hide the ones nothing is showing, and stack each one
 * directly above the window it belongs to.
 *
 * Hiding is ``display: none``: the content stays in the DOM, so the document keeps running and
 * keeps its scroll position, and restoring a minimized window is instant.
 */
export function reconcileLiveSurfaces(): void {
  let visibilityChanged = false;
  for (const surface of surfacesByKey.values()) {
    const placement = slotPlacement(surface);
    const style = surface.element.style;
    if (placement === null) {
      style.display = "none";
    } else {
      style.left = `${placement.left}px`;
      style.top = `${placement.top}px`;
      style.width = `${placement.width}px`;
      style.height = `${placement.height}px`;
      // The window's own rounding cannot reach the page (see ``bottomCornerRadius``), so the
      // page carries it, and gives it up with the window when it is maximized.
      style.borderBottomLeftRadius = placement.bottomLeftCornerRadius;
      style.borderBottomRightRadius = placement.bottomCornerRadius;
      style.zIndex = String(surfaceZIndex(placement.stackIndex));
      style.display = "";
    }
    if (surface.isVisible !== (placement !== null)) {
      surface.isVisible = placement !== null;
      visibilityChanged = true;
    }
  }
  if (!visibilityChanged) return;
  // A page that just appeared or disappeared is told so: the frames redraw and send shown or hidden.
  m.redraw();
  onVisibilityChanged?.();
}

interface SurfacePlacement {
  left: number;
  top: number;
  width: number;
  height: number;
  stackIndex: number;
  bottomCornerRadius: string;
  bottomLeftCornerRadius: string;
}

/** Where a page's window is, in the desktop's coordinates, or null when nothing is showing it. */
function slotPlacement(surface: LiveSurface): SurfacePlacement | null {
  const slot = surface.boundSlot;
  if (slot === null || layerHost === null || !slot.isShowing()) return null;
  const box = slot.element.getBoundingClientRect();
  // A zero-sized box is a window that has not been laid out yet. Showing a page there would hand
  // a framed terminal a zero-column viewport to fit itself to.
  if (box.width <= 0 || box.height <= 0) return null;
  const host = layerHost.getBoundingClientRect();
  return {
    left: box.left - host.left,
    top: box.top - host.top,
    width: box.width,
    height: box.height,
    stackIndex: slot.stackIndex(),
    bottomCornerRadius: slot.bottomCornerRadius(),
    bottomLeftCornerRadius: (slot.bottomLeftCornerRadius ?? slot.bottomCornerRadius)(),
  };
}
