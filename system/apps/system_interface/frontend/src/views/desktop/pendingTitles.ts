/**
 * Titles ahead of the inventory: the name an instance is about to have, by address, while a
 * rename is in flight -- from a window's title bar or from the chat list. Shown wherever the
 * instance is titled until the inventory reports that name, the instance goes, or a minute
 * passes, so a rename that takes the app seconds reads as instant.
 */

import m from "mithril";

const pendingTitleByAddress = new Map<string, string>();
const PENDING_TITLE_TTL_MS = 60_000;

export function setPendingTitle(address: string, title: string): void {
  const trimmed = title.trim();
  if (trimmed === "") return;
  pendingTitleByAddress.set(address, trimmed);
  setTimeout(() => {
    if (pendingTitleByAddress.get(address) !== trimmed) return;
    pendingTitleByAddress.delete(address);
    m.redraw();
  }, PENDING_TITLE_TTL_MS);
  m.redraw();
}

export function clearPendingTitle(address: string): void {
  pendingTitleByAddress.delete(address);
}

export function pendingTitle(address: string): string | undefined {
  return pendingTitleByAddress.get(address);
}

/** The inventory moved: a pending title it now agrees with is no longer pending, and one whose
 *  instance is gone has nothing left to name. ``liveTitle`` answers null for an unlisted address. */
export function settlePendingTitles(liveTitle: (address: string) => string | null): void {
  for (const [address, title] of pendingTitleByAddress) {
    const live = liveTitle(address);
    if (live === null || live === title) pendingTitleByAddress.delete(address);
  }
}
