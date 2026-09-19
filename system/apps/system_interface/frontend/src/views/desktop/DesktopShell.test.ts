// @vitest-environment jsdom
import "../../testing/dom";

import m from "mithril";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { applyApps, resetInventoryForTesting } from "../../models/Inventory";
import { appRecord, instanceRecord } from "../../testing/records";
import { DesktopShell, resetDesktopForTesting } from "./DesktopShell";

const MAKE_SOMETHING = "make-something";

const APPS = [
  appRecord("chat", {
    display_name: "Chat",
    launcher_rank: 10,
    default_shortcut: { action: "new", mode: "new" },
    instances: [instanceRecord({ key: "agent-1", title: "make ipad ui" })],
  }),
  appRecord("files", {
    display_name: "File Viewer",
    launcher_rank: 20,
    instances: [instanceRecord({ key: "files-1", title: "Notes" })],
  }),
  appRecord("terminal", {
    display_name: "Terminal",
    launcher_rank: 40,
    instances: [
      instanceRecord({ key: "terminal-1", title: "Build log" }),
      instanceRecord({ key: "terminal-2", title: "Put away", status: "stopped" }),
    ],
  }),
  appRecord("system_interface", { internal: true, instances: [instanceRecord({ key: "", title: "Shell" })] }),
];

let root: HTMLElement;

function textsOf(selector: string): string[] {
  return Array.from(root.querySelectorAll(selector)).map((element) => element.textContent ?? "");
}

beforeEach(() => {
  resetInventoryForTesting();
  resetDesktopForTesting();
  // Nothing in these tests reaches the shell: the desktop is rendered over an inventory that is
  // handed to it directly.
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({ catalog: null }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
    ),
  );
  root = document.createElement("div");
  document.body.replaceChildren(root);
});

afterEach(() => {
  m.mount(root, null);
  vi.unstubAllGlobals();
});

describe("the desktop as it is drawn", () => {
  it("draws an icon for every app the user can open, and one for making something", () => {
    m.mount(root, DesktopShell);
    applyApps(APPS);
    m.redraw.sync();

    expect(textsOf("#icons .icon-label")).toEqual(["Make something", "Chat", "File Viewer", "Terminal"]);
    // A tile wears the app's own colour, and every app's is its own.
    const tints = Array.from(root.querySelectorAll("#icons .icon-tile")).map((tile) => tile.getAttribute("style"));
    expect(new Set(tints).size).toBe(tints.length);
  });

  it("draws the dock from what is running, not from what has a window", () => {
    m.mount(root, DesktopShell);
    applyApps(APPS);
    m.redraw.sync();

    const addresses = Array.from(root.querySelectorAll("#dock .dock-item")).map((tile) =>
      tile.getAttribute("data-address"),
    );
    expect(addresses).toEqual([
      "app:chat?instance=agent-1",
      "app:files?instance=files-1",
      "app:terminal?instance=terminal-1",
    ]);
    // Nothing has a window yet, so every tile is dimmed: running, and nothing showing it.
    expect(root.querySelectorAll("#dock .dock-item.is-offscreen")).toHaveLength(3);
  });

  it("opens the + into its field, with the menu of what can be opened over it", () => {
    m.mount(root, DesktopShell);
    applyApps(APPS);
    m.redraw.sync();

    (root.querySelector("#dock-new") as HTMLElement).click();
    m.redraw.sync();

    expect(root.querySelector(".dock-launch")?.className).toContain("is-open");
    expect(root.querySelector("#launcher")?.className).toContain("is-open");
    // One row per app the user can open, in rank order since nothing was used recently, and never
    // a row for a thing running in one; Make something is the desktop's, not the menu's.
    expect(textsOf("#launcher .result-title")).toEqual(["Chat", "File Viewer", "Terminal"]);
    expect(root.querySelector("#launcher input")).toBeNull();

    (root.querySelector("#dock-new") as HTMLElement).click();
    m.redraw.sync();
    expect(root.querySelector("#launcher")?.className).not.toContain("is-open");
  });

  it("lists the apps used most recently first, and pairs the chat with a new one, last", () => {
    m.mount(root, DesktopShell);
    applyApps([
      appRecord("chat", {
        display_name: "Chats",
        launcher_rank: 10,
        browses_instances: true,
        actions: [{ id: "new", label: "New Chat", params: [] }],
        instances: [instanceRecord({ key: "agent-1", title: "make ipad ui", last_active: "2026-09-18T12:00:00Z" })],
      }),
      appRecord("files", {
        display_name: "File Viewer",
        launcher_rank: 20,
        instances: [instanceRecord({ key: "files-1", title: "Notes", last_active: "2026-09-18T10:00:00Z" })],
      }),
      appRecord("terminal", {
        display_name: "Terminal",
        launcher_rank: 40,
        instances: [instanceRecord({ key: "terminal-1", title: "Build log", last_active: "2026-09-18T11:00:00Z" })],
      }),
    ]);
    m.redraw.sync();

    (root.querySelector("#dock-new") as HTMLElement).click();
    m.redraw.sync();

    expect(textsOf("#launcher .result-title")).toEqual(["Terminal", "File Viewer", "Chats", "New Chat"]);
  });

  it("opens a window on the Make something panel, which shows no instance", () => {
    m.mount(root, DesktopShell);
    applyApps(APPS);
    m.redraw.sync();

    // Opening an icon takes a double click; a single one only picks it out.
    (root.querySelector("#icons .icon[data-entry='make-something']") as HTMLElement).dispatchEvent(
      new MouseEvent("dblclick", { bubbles: true, button: 0 }),
    );
    m.redraw.sync();

    const windows = root.querySelectorAll(".window");
    expect(windows).toHaveLength(1);
    expect(windows[0].querySelector(".titlebar-title")?.textContent).toBe("Make something");
    expect(windows[0].querySelector(".make-body")).not.toBeNull();
    // Its three verbs are on the title bar, in the order the design settles on.
    expect(
      Array.from(windows[0].querySelectorAll(".titlebar-actions .win-btn")).map((button) =>
        button.getAttribute("data-action"),
      ),
    ).toEqual(["maximize", "minimize", "close"]);
  });

  it("survives an app list that arrives while the desktop is already drawn", () => {
    // The desktop renders before the socket answers, and again on every push: a redraw that
    // throws would leave the user looking at an empty desktop until they reloaded.
    m.mount(root, DesktopShell);
    m.redraw.sync();
    expect(textsOf("#icons .icon-label")).toEqual(["Make something"]);

    applyApps(APPS);
    m.redraw.sync();
    applyApps([...APPS, appRecord("roadmap", { display_name: "Roadmap" })]);
    m.redraw.sync();

    expect(textsOf("#icons .icon-label")).toEqual(["Make something", "Chat", "File Viewer", "Terminal", "Roadmap"]);
  });
});

describe("opening an icon", () => {
  function icon(entry: string): HTMLElement {
    return root.querySelector(`#icons .icon[data-entry="${entry}"]`) as HTMLElement;
  }

  function press(element: HTMLElement, type: string): void {
    element.dispatchEvent(new MouseEvent(type, { bubbles: true, button: 0 }));
    m.redraw.sync();
  }

  beforeEach(() => {
    m.mount(root, DesktopShell);
    applyApps(APPS);
    m.redraw.sync();
  });

  it("takes a double click, and opens exactly one thing", () => {
    press(icon(MAKE_SOMETHING), "click");
    expect(root.querySelectorAll(".window")).toHaveLength(0);

    // The second click of a double click, then the double-click itself: the classic bug is
    // handling both and opening twice.
    press(icon(MAKE_SOMETHING), "click");
    press(icon(MAKE_SOMETHING), "dblclick");

    expect(root.querySelectorAll(".window")).toHaveLength(1);
  });

  it("picks the icon out on a single click rather than doing nothing at all", () => {
    press(icon("chat"), "click");

    expect(icon("chat").className).toContain("is-selected");
    expect(root.querySelectorAll("#icons .icon.is-selected")).toHaveLength(1);

    // One at a time.
    press(icon("terminal"), "click");
    expect(root.querySelectorAll("#icons .icon.is-selected")).toHaveLength(1);
    expect(icon("terminal").className).toContain("is-selected");
  });

  it("puts it down again when the press lands anywhere else", () => {
    press(icon("chat"), "click");
    expect(root.querySelectorAll("#icons .icon.is-selected")).toHaveLength(1);

    (root.querySelector("#desktop") as HTMLElement).dispatchEvent(
      new PointerEvent("pointerdown", { bubbles: true, button: 0 }),
    );
    m.redraw.sync();

    expect(root.querySelectorAll("#icons .icon.is-selected")).toHaveLength(0);
  });

  it("stops picking it out once it has been opened", () => {
    press(icon(MAKE_SOMETHING), "click");
    expect(root.querySelectorAll("#icons .icon.is-selected")).toHaveLength(1);

    press(icon(MAKE_SOMETHING), "dblclick");

    expect(root.querySelectorAll("#icons .icon.is-selected")).toHaveLength(0);
  });

  it("puts a picked-out icon down when another one is dragged", () => {
    press(icon("chat"), "click");
    expect(root.querySelectorAll("#icons .icon.is-selected")).toHaveLength(1);

    // The desktop's own drag machinery, as a press that moves: moving is not choosing.
    const dragged = icon("terminal");
    dragged.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0, clientX: 0, clientY: 0 }));
    dragged.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 60, clientY: 40 }));
    m.redraw.sync();

    expect(root.querySelectorAll("#icons .icon.is-selected")).toHaveLength(0);
    dragged.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, clientX: 60, clientY: 40 }));
    m.redraw.sync();
    // And the drag opened nothing.
    expect(root.querySelectorAll(".window")).toHaveLength(0);
  });

  it("opens from the keyboard, which has no double click", () => {
    icon(MAKE_SOMETHING).dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    m.redraw.sync();

    expect(root.querySelectorAll(".window")).toHaveLength(1);
  });
});

describe("a window whose instance is gone", () => {
  /** The files window, opened from the dock tile of the instance it shows. */
  function openFilesWindow(): HTMLElement {
    m.mount(root, DesktopShell);
    applyApps(APPS);
    m.redraw.sync();
    (root.querySelector('#dock .dock-item[data-address="app:files?instance=files-1"]') as HTMLElement).click();
    m.redraw.sync();
    const opened = root.querySelector('.window[data-address="app:files?instance=files-1"]');
    expect(opened).not.toBeNull();
    return opened as HTMLElement;
  }

  /** The same machine, with the files app no longer listing anything. */
  function withFilesInstanceGone(): void {
    applyApps(APPS.map((app) => (app.name === "files" ? { ...app, instances: [] } : app)));
    m.redraw.sync();
  }

  it("keeps calling it what it was called, and never shows its address", () => {
    const window_ = openFilesWindow();
    expect(window_.querySelector(".titlebar-title")?.textContent).toBe("Notes");

    withFilesInstanceGone();

    // The window stays -- its app may list this again -- and it is still "Notes" to the user. An
    // address is developer text; the user should never be shown one.
    const title = root.querySelector(".window .titlebar-title")?.textContent ?? "";
    expect(title).toBe("Notes");
    expect(root.innerHTML).not.toContain("app:files?instance=files-1</");
    expect(root.querySelector(".window")?.className).toContain("is-unavailable");
  });

  it("falls back to the app's own name when it was never seen under a title", () => {
    m.mount(root, DesktopShell);
    // An app that lists an instance with no title of its own, which then goes away.
    applyApps([
      appRecord("files", {
        display_name: "File Viewer",
        instances: [instanceRecord({ key: "files-9", title: "" })],
      }),
    ]);
    m.redraw.sync();
    (root.querySelector('#dock .dock-item[data-address="app:files?instance=files-9"]') as HTMLElement).click();
    m.redraw.sync();
    applyApps([appRecord("files", { display_name: "File Viewer", instances: [] })]);
    m.redraw.sync();

    expect(root.querySelector(".window .titlebar-title")?.textContent).toBe("File Viewer");
  });

  it("has no dock tile, because the dock is what is running and this is not", () => {
    openFilesWindow();
    expect(root.querySelectorAll('#dock .dock-item[data-address="app:files?instance=files-1"]')).toHaveLength(1);

    withFilesInstanceGone();

    // A window on screen with nothing in the dock is the deliberate answer: the dock's one rule is
    // that it shows what is RUNNING, and an instance no app lists is not.
    expect(root.querySelectorAll('#dock .dock-item[data-address="app:files?instance=files-1"]')).toHaveLength(0);
    expect(root.querySelectorAll(".window")).toHaveLength(1);
  });

  it("says so in its body rather than looking like a window that has broken", () => {
    openFilesWindow();
    withFilesInstanceGone();

    const note = root.querySelector(".window-note")?.textContent ?? "";
    expect(note).toContain("isn't here any more");
    expect(note).toContain("close the window");
  });

  it("is cleared away by its own close button, which has nothing left to stop", () => {
    const window_ = openFilesWindow();
    withFilesInstanceGone();

    (window_.querySelector('.win-btn[data-action="close"]') as HTMLElement).click();
    m.redraw.sync();

    // Close means stop, and there is nothing to stop: what is left of the verb is taking the
    // window away, which is the only way the user can clear a stale one.
    expect(root.querySelectorAll(".window")).toHaveLength(0);
  });
});
