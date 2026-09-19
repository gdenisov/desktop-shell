import { describe, expect, it } from "vitest";

import {
  GRABBABLE_WIDTH,
  MIN_WINDOW_HEIGHT,
  MIN_WINDOW_WIDTH,
  TITLE_BAR_HEIGHT,
  cascadeRect,
  draggedRect,
  fittedSize,
  maximizedRect,
  nudgedIntoView,
  resizedRect,
} from "./geometry";

const DESKTOP = { width: 1440, height: 900 };
const RECT = { x: 100, y: 80, width: 800, height: 600 };

describe("cascadeRect", () => {
  it("steps each window down and across from the last, inside the desktop", () => {
    const first = cascadeRect(0, DESKTOP);
    const second = cascadeRect(1, DESKTOP);
    expect(second.x).toBeGreaterThan(first.x);
    expect(second.y).toBeGreaterThan(first.y);
    expect(first.x + first.width).toBeLessThanOrEqual(DESKTOP.width);
    expect(first.y + first.height).toBeLessThanOrEqual(DESKTOP.height);
  });

  it("comes back to the start rather than walking a long run of windows off the screen", () => {
    expect(cascadeRect(7, DESKTOP)).toEqual(cascadeRect(0, DESKTOP));
  });

  it("fits a window onto a desktop smaller than the default size", () => {
    const tiny = cascadeRect(0, { width: 500, height: 400 });
    expect(tiny.width).toBeLessThanOrEqual(500);
    expect(tiny.height).toBeLessThanOrEqual(400);
    expect(tiny.width).toBeGreaterThanOrEqual(MIN_WINDOW_WIDTH);
    expect(tiny.height).toBeGreaterThanOrEqual(MIN_WINDOW_HEIGHT);
  });
});

describe("nudgedIntoView", () => {
  it("leaves a window that is already in reach exactly where it is", () => {
    expect(nudgedIntoView(RECT, DESKTOP)).toEqual(RECT);
  });

  it("brings a window back from off the edge without resizing it", () => {
    const offRight = nudgedIntoView({ ...RECT, x: 5000 }, DESKTOP);
    expect(offRight.width).toBe(RECT.width);
    expect(offRight.x).toBeLessThan(DESKTOP.width);

    const offBottom = nudgedIntoView({ ...RECT, y: 5000 }, DESKTOP);
    expect(offBottom.height).toBe(RECT.height);
    expect(offBottom.y).toBeLessThan(DESKTOP.height);
  });

  it("keeps a window wider than the screen grabbable rather than shrinking it", () => {
    // A desktop saved on a wide screen and reopened on a narrow one must not have its windows
    // resized: the resize would be saved, and the wide screen would never get them back.
    const wide = nudgedIntoView({ x: -900, y: 40, width: 1600, height: 500 }, { width: 800, height: 600 });
    expect(wide.width).toBe(1600);
    expect(wide.x + wide.width).toBeGreaterThan(0);
  });

  it("leaves a full grab handle on screen on a fractionally sized desktop", () => {
    // A real viewport is not a whole number of pixels wide. Rounding the limit up from 699.99
    // would leave a sliver under the 80px handle on screen -- too little to grab the window by,
    // which is the whole point of the nudge.
    const fractional = { width: 699.9911499023438, height: 467.9873046875 };
    const nudged = nudgedIntoView({ x: 740, y: 120, width: 420, height: 300 }, fractional);
    expect(nudged.x + GRABBABLE_WIDTH).toBeLessThanOrEqual(fractional.width);
    expect(nudged.y + TITLE_BAR_HEIGHT).toBeLessThanOrEqual(fractional.height);
    // Still not resized, and still moved in from where it was saved.
    expect(nudged.width).toBe(420);
    expect(nudged.x).toBeLessThan(740);
  });
});

describe("fittedSize", () => {
  it("never produces a window too small to grab", () => {
    expect(fittedSize({ x: 0, y: 0, width: 10, height: 10 }, DESKTOP)).toEqual({
      width: MIN_WINDOW_WIDTH,
      height: MIN_WINDOW_HEIGHT,
    });
  });
});

describe("draggedRect", () => {
  it("moves the window by the pointer", () => {
    expect(draggedRect(RECT, 30, -20, DESKTOP)).toMatchObject({ x: 130, y: 60, width: 800, height: 600 });
  });

  it("never lets a window be dragged out of reach", () => {
    const far = draggedRect(RECT, 99999, 99999, DESKTOP);
    expect(far.x).toBeLessThan(DESKTOP.width);
    expect(far.y).toBeLessThan(DESKTOP.height);
  });
});

describe("resizedRect", () => {
  it("pulls the dragged edge and leaves the others where they were", () => {
    expect(resizedRect(RECT, "e", 100, 0)).toEqual({ x: 100, y: 80, width: 900, height: 600 });
    expect(resizedRect(RECT, "s", 0, 50)).toEqual({ x: 100, y: 80, width: 800, height: 650 });
    // Pulling the left edge moves the window's origin as its width changes.
    expect(resizedRect(RECT, "w", 100, 0)).toEqual({ x: 200, y: 80, width: 700, height: 600 });
    expect(resizedRect(RECT, "n", 0, 100)).toEqual({ x: 100, y: 180, width: 800, height: 500 });
  });

  it("pulls both edges of a corner", () => {
    expect(resizedRect(RECT, "se", 60, 40)).toEqual({ x: 100, y: 80, width: 860, height: 640 });
    expect(resizedRect(RECT, "nw", 60, 40)).toEqual({ x: 160, y: 120, width: 740, height: 560 });
  });

  it("stops at the smallest window that can still be grabbed", () => {
    const squashed = resizedRect(RECT, "se", -5000, -5000);
    expect(squashed.width).toBe(MIN_WINDOW_WIDTH);
    expect(squashed.height).toBe(MIN_WINDOW_HEIGHT);
  });
});

describe("maximizedRect", () => {
  it("is the whole desktop", () => {
    expect(maximizedRect(DESKTOP)).toEqual({ x: 0, y: 0, width: 1440, height: 900 });
  });
});
