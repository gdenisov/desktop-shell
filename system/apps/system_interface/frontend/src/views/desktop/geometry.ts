/**
 * Where a window goes: the arithmetic behind opening, dragging, resizing and maximizing one.
 *
 * All of it is pure and in CSS pixels from the desktop area's top left, the same frame the stored
 * document uses, so what a gesture computes is exactly what is saved and restored.
 */

import type { DesktopSize, WindowRect } from "../../models/Desktop";

/** Nothing may be resized below this: a window too small to grab cannot be opened again. */
export const MIN_WINDOW_WIDTH = 340;
export const MIN_WINDOW_HEIGHT = 200;

/** What a freshly opened window is sized at, and how far each one steps from the last. */
export const DEFAULT_WINDOW_WIDTH = 900;
export const DEFAULT_WINDOW_HEIGHT = 620;
const CASCADE_ORIGIN_X = 120;
const CASCADE_ORIGIN_Y = 72;
const CASCADE_STEP = 30;
const CASCADE_LENGTH = 7;

/** The margins a new window keeps from the desktop's edges, so it never opens flush to them. */
const OPEN_MARGIN_X = 24;
const OPEN_MARGIN_Y = 16;

/** How much of a window must stay on the desktop: enough of the title bar to grab and drag back. */
/** How much of a window must stay on the desktop for the user to have something to drag it by. */
export const GRABBABLE_WIDTH = 80;
/** A window's title bar: the least that must sit below the desktop's top edge to be grabbable. */
export const TITLE_BAR_HEIGHT = 42;

/** Which edges a resize is pulling; a corner names both. */
export type ResizeEdge = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

export function clamp(value: number, lowest: number, highest: number): number {
  return Math.min(Math.max(value, lowest), highest);
}

/** A window sized to fit the desktop, never below what can be grabbed. */
export function fittedSize(rect: WindowRect, desktop: DesktopSize): { width: number; height: number } {
  return {
    width: Math.round(clamp(rect.width, MIN_WINDOW_WIDTH, Math.max(MIN_WINDOW_WIDTH, desktop.width))),
    height: Math.round(clamp(rect.height, MIN_WINDOW_HEIGHT, Math.max(MIN_WINDOW_HEIGHT, desktop.height))),
  };
}

/**
 * A restored window nudged back into reach, without resizing it.
 *
 * A desktop laid out on a wide screen and reopened on a narrow one would otherwise come back with
 * windows off the edge and no way to drag them in. Only the position moves: shrinking a window to
 * the smaller screen would make the resize permanent on the next save.
 *
 * The limits are floored, not rounded, because they are *guarantees*: at least ``GRABBABLE_WIDTH``
 * of the window has to stay on the desktop and at least a title bar's height below its top edge,
 * or there is nothing left to drag it back by. A real desktop is a fractional number of pixels
 * wide (a 699.99px viewport, say), and rounding the limit up from that puts the window a hair
 * past the edge -- which is exactly the sliver of the grab handle the user cannot reach.
 */
export function nudgedIntoView(rect: WindowRect, desktop: DesktopSize): WindowRect {
  return {
    ...rect,
    x: Math.floor(clamp(rect.x, GRABBABLE_WIDTH - rect.width, Math.max(0, desktop.width - GRABBABLE_WIDTH))),
    y: Math.floor(clamp(rect.y, 0, Math.max(0, desktop.height - TITLE_BAR_HEIGHT))),
  };
}

/** Where the next window opens: a default-sized window, stepped along the cascade, inside the desktop. */
export function cascadeRect(openWindowCount: number, desktop: DesktopSize): WindowRect {
  const size = fittedSize(
    {
      x: 0,
      y: 0,
      width: Math.min(DEFAULT_WINDOW_WIDTH, Math.max(MIN_WINDOW_WIDTH, desktop.width - 2 * OPEN_MARGIN_X)),
      height: Math.min(DEFAULT_WINDOW_HEIGHT, Math.max(MIN_WINDOW_HEIGHT, desktop.height - 2 * OPEN_MARGIN_Y)),
    },
    desktop,
  );
  const offset = (openWindowCount % CASCADE_LENGTH) * CASCADE_STEP;
  return {
    x: Math.round(
      Math.min(CASCADE_ORIGIN_X + offset, Math.max(OPEN_MARGIN_X, desktop.width - size.width - OPEN_MARGIN_X)),
    ),
    y: Math.round(
      Math.min(CASCADE_ORIGIN_Y + offset, Math.max(OPEN_MARGIN_Y, desktop.height - size.height - OPEN_MARGIN_Y)),
    ),
    ...size,
  };
}

/** The whole desktop, which is what a maximized window fills. */
export function maximizedRect(desktop: DesktopSize): WindowRect {
  return { x: 0, y: 0, width: desktop.width, height: desktop.height };
}

/** Where a drag leaves a window: moved by the pointer, held so it can always be dragged back. */
export function draggedRect(start: WindowRect, deltaX: number, deltaY: number, desktop: DesktopSize): WindowRect {
  return nudgedIntoView({ ...start, x: Math.round(start.x + deltaX), y: Math.round(start.y + deltaY) }, desktop);
}

/** Where a resize leaves a window: the dragged edges follow the pointer, the others stay put. */
export function resizedRect(start: WindowRect, edge: ResizeEdge, deltaX: number, deltaY: number): WindowRect {
  const isPullingRight = edge.includes("e");
  const isPullingLeft = edge.includes("w");
  const isPullingDown = edge.includes("s");
  const isPullingUp = edge.includes("n");
  const width = isPullingRight
    ? Math.max(MIN_WINDOW_WIDTH, start.width + deltaX)
    : isPullingLeft
      ? Math.max(MIN_WINDOW_WIDTH, start.width - deltaX)
      : start.width;
  const height = isPullingDown
    ? Math.max(MIN_WINDOW_HEIGHT, start.height + deltaY)
    : isPullingUp
      ? Math.max(MIN_WINDOW_HEIGHT, start.height - deltaY)
      : start.height;
  return {
    x: Math.round(isPullingLeft ? start.x + (start.width - width) : start.x),
    y: Math.round(isPullingUp ? start.y + (start.height - height) : start.y),
    width: Math.round(width),
    height: Math.round(height),
  };
}

/** Whether a pointer at these client coordinates is over ``element``. */
export function isPointerOver(element: Element | null, clientX: number, clientY: number): boolean {
  if (element === null) return false;
  const box = element.getBoundingClientRect();
  return clientX >= box.left && clientX <= box.right && clientY >= box.top && clientY <= box.bottom;
}
