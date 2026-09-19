import { describe, expect, it } from "vitest";

import { appRecord, instanceRecord } from "../../testing/records";
import { dockEntries, dockTooltip } from "./dock";

const APPS = [
  appRecord("terminal", {
    launcher_rank: 40,
    instances: [
      instanceRecord({ key: "terminal-1", title: "Build log" }),
      instanceRecord({ key: "terminal-2", title: "Old one", status: "stopped" }),
    ],
  }),
  appRecord("chat", {
    launcher_rank: 10,
    instances: [instanceRecord({ key: "agent-1", title: "make ipad ui" })],
  }),
  appRecord("system_interface", { internal: true, instances: [instanceRecord({ key: "", title: "Shell" })] }),
];

const NOTHING_ON_SCREEN = (): boolean => false;
const NOTHING_CLOSING = new Set<string>();
const NO_WINDOWS = new Set<string>();

describe("dockEntries", () => {
  it("is what is running, whether or not a window is showing it", () => {
    const entries = dockEntries(APPS, NOTHING_ON_SCREEN, NO_WINDOWS, NOTHING_CLOSING);
    expect(entries.map((entry) => entry.address)).toEqual([
      "app:chat?instance=agent-1",
      "app:terminal?instance=terminal-1",
    ]);
    expect(entries.every((entry) => !entry.isOnScreen)).toBe(true);
  });

  it("leaves out what has been stopped: that is found through search, not here", () => {
    const entries = dockEntries(APPS, NOTHING_ON_SCREEN, NO_WINDOWS, NOTHING_CLOSING);
    expect(entries.some((entry) => entry.address.includes("terminal-2"))).toBe(false);
  });

  it("leaves out an app the user cannot open", () => {
    const entries = dockEntries(APPS, NOTHING_ON_SCREEN, NO_WINDOWS, NOTHING_CLOSING);
    expect(entries.some((entry) => entry.app.internal)).toBe(false);
  });

  it("holds out what is being closed, so a closed window does not flicker back", () => {
    const entries = dockEntries(APPS, NOTHING_ON_SCREEN, NO_WINDOWS, new Set(["app:chat?instance=agent-1"]));
    expect(entries.map((entry) => entry.address)).toEqual(["app:terminal?instance=terminal-1"]);
  });

  it("marks what a window is showing right now", () => {
    const entries = dockEntries(
      APPS,
      (address) => address === "app:chat?instance=agent-1",
      NO_WINDOWS,
      NOTHING_CLOSING,
    );
    expect(entries.find((entry) => entry.address.includes("agent-1"))?.isOnScreen).toBe(true);
  });

  it("falls back to the app's name for an instance with no title of its own", () => {
    const untitled = [
      appRecord("files", { display_name: "File Viewer", instances: [instanceRecord({ key: "f1", title: "" })] }),
    ];
    expect(dockEntries(untitled, NOTHING_ON_SCREEN, NO_WINDOWS, NOTHING_CLOSING)[0].title).toBe("File Viewer");
  });
});

describe("an app that browses its own instances", () => {
  /** A chat app with three chats running, which lists them itself. */
  const BROWSING = [
    appRecord("chat", {
      launcher_rank: 10,
      browses_instances: true,
      instances: [
        instanceRecord({ key: "agent-1", title: "make ipad ui" }),
        instanceRecord({ key: "agent-2", title: "groceries" }),
        instanceRecord({ key: "agent-3", title: "taxes" }),
      ],
    }),
    appRecord("terminal", {
      launcher_rank: 40,
      instances: [instanceRecord({ key: "terminal-1", title: "Build log" })],
    }),
  ];

  it("is in the dock once per WINDOW open on it, not once per instance it is running", () => {
    // The complaint this answers: thirteen chats running made the dock almost entirely chats,
    // and crowded out everything else the user had going.
    const entries = dockEntries(BROWSING, NOTHING_ON_SCREEN, new Set(["app:chat?instance=agent-2"]), NOTHING_CLOSING);

    expect(entries.map((entry) => entry.address)).toEqual([
      "app:chat?instance=agent-2",
      "app:terminal?instance=terminal-1",
    ]);
    // The tile carries the chat that window is showing, so several windows stay tellable apart.
    expect(entries[0].title).toBe("groceries");
  });

  it("leaves nothing of itself in the dock when no window is open on it", () => {
    const entries = dockEntries(BROWSING, NOTHING_ON_SCREEN, NO_WINDOWS, NOTHING_CLOSING);
    expect(entries.map((entry) => entry.address)).toEqual(["app:terminal?instance=terminal-1"]);
  });

  it("changes nothing for an app that does not browse its own instances", () => {
    // The terminal has no window open on it and is still in the dock: the rule is the capability's,
    // not the window's.
    const entries = dockEntries(BROWSING, NOTHING_ON_SCREEN, NO_WINDOWS, NOTHING_CLOSING);
    expect(entries.map((entry) => entry.address)).toEqual(["app:terminal?instance=terminal-1"]);
  });
});

describe("dockTooltip", () => {
  it("says what it is, and says so when it is out of sight", () => {
    const [chat] = dockEntries(
      APPS,
      (address) => address === "app:chat?instance=agent-1",
      NO_WINDOWS,
      NOTHING_CLOSING,
    );
    expect(dockTooltip(chat)).toBe("make ipad ui");
    expect(dockTooltip({ ...chat, isOnScreen: false })).toBe("make ipad ui (minimized)");
  });
});
