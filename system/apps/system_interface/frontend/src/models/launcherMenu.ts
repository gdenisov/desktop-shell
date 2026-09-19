/**
 * What the ``+`` offers when it opens: the menu above the field, before anything is typed.
 *
 * One flat list of APPS -- never of the things running in them, which is what the search and the
 * dock are for -- in the order the user reads it: the apps used most recently first (a few of
 * them, by when something in them was last active), then the rest in their registry order, and
 * last, for an app that browses its own instances (the chat), two ways in -- the app as it is,
 * and a new instance of it -- because "look at my chats" and "start a chat" are different
 * intents. Make something is not here: it has its desktop icon, and this menu is for opening
 * things, not making them.
 *
 * Typing narrows the list by name; what was said inside things is the search's job, and the
 * desktop shows both under the same field.
 */

import type { AppRecord } from "./Inventory";
import { matchesQuery } from "./search";
import { launchableApps } from "../views/desktop/icons";

/** How many apps are lifted to the top of the menu by recent use; the rest keep registry order. */
export const RECENT_COUNT = 5;

export type LauncherItemKind = "app" | "browse" | "new";

/** One row of the menu: what it opens, and how it reads. */
export interface LauncherItem {
  /** Stable, and unique across the menu: the vnode key and the ``data-item`` marker. */
  key: string;
  kind: LauncherItemKind;
  /** The row's text: the app's name, or what the app calls starting a new one ("New Chat"). */
  label: string;
  app: AppRecord;
}

/** When something in the app was last active, as epoch ms; 0 for an app never used. */
export function lastUsedMs(app: AppRecord): number {
  let latest = 0;
  for (const instance of app.instances) {
    if (instance.last_active === null) continue;
    const parsed = Date.parse(instance.last_active);
    if (Number.isFinite(parsed) && parsed > latest) latest = parsed;
  }
  return latest;
}

/** What the primary action of an app is called, as the app itself labels it ("New Chat"). */
function newLabelFor(app: AppRecord): string {
  const action = app.actions.find((candidate) => candidate.id === app.default_shortcut?.action) ?? app.actions[0];
  return action === undefined ? `New ${app.display_name}` : action.label;
}

/**
 * The apps in the order the menu lists them: the ``count`` used most recently first, newest
 * first, then every other in registry order. An app that browses its own instances is not among
 * them -- it gets its own pair of rows at the end.
 */
export function appsByRecency(apps: readonly AppRecord[], count: number = RECENT_COUNT): AppRecord[] {
  const plain = launchableApps(apps).filter((app) => !app.browses_instances);
  const recent = plain
    .filter((app) => lastUsedMs(app) > 0)
    .sort((left, right) => lastUsedMs(right) - lastUsedMs(left))
    .slice(0, count);
  return [...recent, ...plain.filter((app) => !recent.includes(app))];
}

/** The whole menu, before anything is typed. */
export function launcherMenuItems(apps: readonly AppRecord[]): LauncherItem[] {
  const items: LauncherItem[] = appsByRecency(apps).map((app) => ({
    key: `app:${app.name}`,
    kind: "app",
    label: app.display_name,
    app,
  }));
  // The browsing app's two rows go last: they are the pair the user reaches for most, and the
  // menu opens upward from the field, so last is nearest the hand.
  for (const app of launchableApps(apps).filter((candidate) => candidate.browses_instances)) {
    items.push({ key: `browse:${app.name}`, kind: "browse", label: app.display_name, app });
    items.push({ key: `new:${app.name}`, kind: "new", label: newLabelFor(app), app });
  }
  return items;
}

/** The rows a query keeps: those whose name it matches; all of them while nothing is typed. */
export function launcherItemsMatching(items: readonly LauncherItem[], query: string): LauncherItem[] {
  if (query.trim() === "") return [...items];
  return items.filter((item) => matchesQuery(query, item.label));
}
