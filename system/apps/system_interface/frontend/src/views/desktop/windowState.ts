/**
 * The windows on screen, in stacking order, and the pure moves over them.
 *
 * Almost every window shows an instance and is saved with the desktop. The one exception is the
 * Make something window, which shows no instance and is the desktop's own: it is held here
 * alongside the others so there is a single stacking order over everything on screen, and it is
 * simply left out of what is saved.
 */

import type { DesktopDocument, DesktopSize, WindowRect } from "../../models/Desktop";
import { emptyDesktop } from "../../models/Desktop";
import { maximizedRect, nudgedIntoView } from "./geometry";

/** What a window shows: an instance of an app, or the desktop's own Make something panel. */
export type WindowKind = "instance" | "make";

/** One window on screen. */
export interface WindowState {
  kind: WindowKind;
  /** The page's id. A Make something window has one too, so every window is keyed the same way. */
  tabId: string;
  /** The instance the window shows; empty for the Make something window. */
  address: string;
  rect: WindowRect;
  isMinimized: boolean;
  isMaximized: boolean;
  restoreRect: WindowRect | null;
  lastFocusedMs: number;
  /** What this window was called when its instance was last listed, for when it is not. */
  lastKnownTitle: string | null;
}

/** The window showing ``address``, or undefined when none does. */
export function windowShowing(windows: readonly WindowState[], address: string): WindowState | undefined {
  return windows.find((window) => window.kind === "instance" && window.address === address);
}

/** The Make something window, or undefined when it is not open. */
export function makeSomethingWindow(windows: readonly WindowState[]): WindowState | undefined {
  return windows.find((window) => window.kind === "make");
}

export function windowWithTabId(windows: readonly WindowState[], tabId: string): WindowState | undefined {
  return windows.find((window) => window.tabId === tabId);
}

/** The windows with one replaced; unchanged when none carries that page. */
export function withUpdatedWindow(
  windows: readonly WindowState[],
  tabId: string,
  change: (window: WindowState) => WindowState,
): WindowState[] {
  return windows.map((window) => (window.tabId === tabId ? change(window) : window));
}

/** The windows with one raised to the top of the stack, out of the dock, and stamped as just used. */
export function withRaised(windows: readonly WindowState[], tabId: string, nowMs: number): WindowState[] {
  const raised = windowWithTabId(windows, tabId);
  if (raised === undefined) return [...windows];
  const rest = windows.filter((window) => window.tabId !== tabId);
  return [...rest, { ...raised, isMinimized: false, lastFocusedMs: nowMs }];
}

/** The windows with one put away: out of sight, its page still loaded, its instance untouched. */
export function withMinimized(windows: readonly WindowState[], tabId: string): WindowState[] {
  return withUpdatedWindow(windows, tabId, (window) => ({ ...window, isMinimized: true }));
}

/** The windows with one filling the desktop, or back at the rect it was filling the desktop from. */
export function withMaximizeToggled(
  windows: readonly WindowState[],
  tabId: string,
  desktop: DesktopSize,
): WindowState[] {
  return withUpdatedWindow(windows, tabId, (window) => {
    if (window.isMaximized) {
      // Back at exactly the rect it was filling the desktop from, even if this screen cannot hold
      // it all: drawing keeps it reachable, and the rect stays the one the user set.
      return { ...window, rect: window.restoreRect ?? window.rect, isMaximized: false, restoreRect: null };
    }
    return { ...window, rect: maximizedRect(desktop), isMaximized: true, restoreRect: window.rect };
  });
}

/** The windows with one moved or resized. A window being dragged is no longer maximized. */
export function withGeometry(windows: readonly WindowState[], tabId: string, rect: WindowRect): WindowState[] {
  return withUpdatedWindow(windows, tabId, (window) => ({
    ...window,
    rect,
    isMaximized: false,
    restoreRect: null,
  }));
}

/**
 * Where a window is drawn: its own rect, kept within reach of the screen it is on now.
 *
 * A window parked off the right of a bigger screen is drawn back where it can be grabbed, and a
 * maximized one fills whatever it is looking at -- but neither is a change to the window. The
 * rect the user put it at is left alone, because the fitting is about this screen and the rect
 * is about their arrangement, and the next save writes the rect.
 */
export function shownRect(window: WindowState, desktop: DesktopSize): WindowRect {
  if (window.isMaximized) return maximizedRect(desktop);
  return nudgedIntoView(window.rect, desktop);
}

/** The windows a saved desktop opens as. A read: what the file says is what they are. */
export function windowsFromDocument(document: DesktopDocument): WindowState[] {
  return document.windows.map((window) => ({
    kind: "instance" as const,
    tabId: window.tab_id,
    address: window.address,
    rect: window.rect,
    isMinimized: window.is_minimized,
    isMaximized: window.is_maximized,
    restoreRect: window.restore_rect,
    lastFocusedMs: window.last_focused_ms,
    lastKnownTitle: window.last_known_title,
  }));
}

/** The desktop to save: every window that shows an instance, in stacking order, with the rest of
 *  what the user arranged. The Make something window shows no instance and is not saved. */
export function documentFromWindows(
  windows: readonly WindowState[],
  document: DesktopDocument,
  desktop: DesktopSize,
): DesktopDocument {
  return {
    ...emptyDesktop(),
    icons: document.icons,
    dock: document.dock,
    desktop_size: desktop,
    windows: windows
      .filter((window) => window.kind === "instance")
      .map((window) => ({
        tab_id: window.tabId,
        address: window.address,
        rect: window.rect,
        is_minimized: window.isMinimized,
        is_maximized: window.isMaximized,
        restore_rect: window.restoreRect,
        last_focused_ms: window.lastFocusedMs,
        last_known_title: window.lastKnownTitle,
      })),
  };
}
