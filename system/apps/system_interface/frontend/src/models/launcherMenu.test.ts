import { describe, expect, it } from "vitest";

import { appRecord, instanceRecord } from "../testing/records";
import { appsByRecency, launcherItemsMatching, launcherMenuItems } from "./launcherMenu";

const APPS = [
  appRecord("chat", {
    display_name: "Chats",
    launcher_rank: 10,
    browses_instances: true,
    actions: [{ id: "new", label: "New Chat", params: [] }],
    default_shortcut: { action: "new", mode: "new" },
    instances: [instanceRecord({ key: "agent-1", title: "make ipad ui", last_active: "2026-09-18T12:00:00Z" })],
  }),
  appRecord("browser", { display_name: "Browser", launcher_rank: 15 }),
  appRecord("files", {
    display_name: "File Viewer",
    launcher_rank: 20,
    instances: [
      instanceRecord({ key: "files-1", title: "Notes", last_active: "2026-09-18T09:00:00Z" }),
      instanceRecord({ key: "files-2", title: "Old", last_active: "2026-09-18T11:00:00Z", status: "stopped" }),
    ],
  }),
  appRecord("terminal", {
    display_name: "Terminal",
    launcher_rank: 40,
    instances: [instanceRecord({ key: "terminal-1", title: "Build log", last_active: "2026-09-18T10:00:00Z" })],
  }),
  appRecord("roadmap", { display_name: "Roadmap", launcher_rank: 50 }),
  appRecord("system_interface", { internal: true, instances: [instanceRecord({ key: "", title: "Shell" })] }),
];

describe("appsByRecency", () => {
  it("lifts the apps used most recently to the front, by their latest instance, then keeps registry order", () => {
    expect(appsByRecency(APPS).map((app) => app.name)).toEqual(["files", "terminal", "browser", "roadmap"]);
  });

  it("lifts only so many, and leaves out the app that browses its own instances and any the user cannot open", () => {
    expect(appsByRecency(APPS, 1).map((app) => app.name)).toEqual(["files", "browser", "terminal", "roadmap"]);
    expect(appsByRecency(APPS).some((app) => app.name === "chat" || app.internal)).toBe(false);
  });
});

describe("launcherMenuItems", () => {
  it("is apps, never the things running in them, with the browsing app's pair of rows last", () => {
    const items = launcherMenuItems(APPS);
    expect(items.map((item) => [item.kind, item.label])).toEqual([
      ["app", "File Viewer"],
      ["app", "Terminal"],
      ["app", "Browser"],
      ["app", "Roadmap"],
      ["browse", "Chats"],
      ["new", "New Chat"],
    ]);
    expect(items.map((item) => item.label)).not.toContain("Notes");
    expect(new Set(items.map((item) => item.key)).size).toBe(items.length);
  });
});

describe("launcherItemsMatching", () => {
  const items = launcherMenuItems(APPS);

  it("is the whole menu while nothing is typed", () => {
    expect(launcherItemsMatching(items, "  ")).toEqual(items);
  });

  it("keeps the rows a query names", () => {
    expect(launcherItemsMatching(items, "term").map((item) => item.label)).toEqual(["Terminal"]);
    expect(launcherItemsMatching(items, "chat").map((item) => item.label)).toEqual(["Chats", "New Chat"]);
    expect(launcherItemsMatching(items, "notes")).toEqual([]);
  });
});
