/**
 * The dock: what is RUNNING.
 *
 * Derived from the live instance list of every app rather than from the windows this page happens
 * to have opened, so something running with no window on screen still appears -- which is how two
 * browsers once ended up alive and invisible. A stopped instance does not appear at all: it is
 * still the user's and still listed by its app, but it belongs in search rather than here.
 *
 * Two states, and only two: on screen, or minimized. Minimizing a window and never having opened
 * one are the same thing to the user -- the process is running and nothing is showing it -- so
 * they look the same.
 *
 * **The one exception is an app that browses its own instances** (``browses_instances``). The
 * point of the rule above is that nothing the user started is invisible; an app that lists its
 * own instances in its own window already satisfies that, and one with a dozen chats would
 * otherwise fill the dock and crowd everything else out. So such an app is represented by the
 * WINDOWS open on it, one tile each, and its instances are reached from inside it -- and from
 * search, which finds everything either way.
 */

import { addressFor } from "../../models/Inventory";
import type { AppRecord, InstanceRecord } from "../../models/Inventory";
import { launchableApps } from "./icons";

/** The size a dock tile is drawn at when its window is on screen (``--dock-tile`` in the
 *  stylesheet), whatever the row holds. A crowded row scrolls rather than shrinking, and a tile
 *  whose window is put away is drawn smaller (``--dock-tile-offscreen``). This is deliberately
 *  larger than the ``+`` beside it, which is not a running thing and sizes itself. */
export const DOCK_TILE_PX = 36;

/** One thing in the dock. */
export interface DockEntry {
  /** Unique per entry, and the vnode key: the address it stands for. */
  address: string;
  app: AppRecord;
  instance: InstanceRecord;
  title: string;
  /** Whether a window is showing it right now; false covers both minimized and never opened. */
  isOnScreen: boolean;
}

/**
 * What the dock shows: every running instance of every app the user can open, in registry order --
 * except for an app that browses its own instances, which is shown by the windows open on it.
 *
 * ``windowedAddresses`` is every address this client has a window for, minimized ones included.
 * ``closingAddresses`` are the ones whose stop is in flight: they are held out for as long as that
 * takes, so a window the user closed does not flick back into the dock while the app catches up.
 */
export function dockEntries(
  apps: readonly AppRecord[],
  // Whether a window is showing this address right now: false covers both minimized and never opened.
  isOnScreen: (address: string) => boolean,
  windowedAddresses: ReadonlySet<string>,
  closingAddresses: ReadonlySet<string>,
): DockEntry[] {
  const entries: DockEntry[] = [];
  for (const app of launchableApps(apps)) {
    for (const instance of app.instances) {
      const address = addressFor(app.name, instance.key);
      if (closingAddresses.has(address) || instance.status === "stopped") continue;
      // An app that lists its own instances is in the dock only where the user has a window on
      // one: the rest are its own business, and its window is where they are chosen from.
      if (app.browses_instances && !windowedAddresses.has(address)) continue;
      entries.push({
        address,
        app,
        instance,
        title: instance.title !== "" ? instance.title : app.display_name,
        isOnScreen: isOnScreen(address),
      });
    }
  }
  return entries;
}

/** The tooltip a dock tile carries: what it is, and whether it is out of sight. */
export function dockTooltip(entry: DockEntry): string {
  return entry.isOnScreen ? entry.title : `${entry.title} (minimized)`;
}
