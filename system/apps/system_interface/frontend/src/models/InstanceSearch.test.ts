import { describe, expect, it } from "vitest";

import { appRecord, instanceRecord } from "../testing/records";
import { highlightedSegments, snippetInView, titleMatchesFromInventory } from "./InstanceSearch";

describe("titleMatchesFromInventory", () => {
  const apps = [
    appRecord("chat", {
      display_name: "Chats",
      instances: [
        instanceRecord({ key: "agent-1", title: "make ipad ui", last_active: "2026-09-18T10:00:00Z" }),
        instanceRecord({
          key: "agent-2",
          title: "ipad follow-up",
          last_active: "2026-09-18T12:00:00Z",
          status: "stopped",
        }),
        instanceRecord({ key: "agent-3", title: "taxes", last_active: null }),
      ],
    }),
    appRecord("system_interface", { internal: true, instances: [instanceRecord({ key: "", title: "ipad shell" })] }),
  ];

  it("is every openable thing whose title holds the query, most recently active first, stopped included", () => {
    const results = titleMatchesFromInventory(apps, "IPAD");
    expect(results.map((result) => [result.key, result.status, result.matched_on])).toEqual([
      ["agent-2", "stopped", "title"],
      ["agent-1", "idle", "title"],
    ]);
    expect(results[0].app_display_name).toBe("Chats");
  });

  it("is nothing for a blank query", () => {
    expect(titleMatchesFromInventory(apps, "  ")).toEqual([]);
  });
});

describe("highlightedSegments", () => {
  it("marks every occurrence of the query, whatever its case, and nothing else", () => {
    expect(highlightedSegments("The Dock is what is running, dock and all", "dock")).toEqual([
      { text: "The ", isMatch: false },
      { text: "Dock", isMatch: true },
      { text: " is what is running, ", isMatch: false },
      { text: "dock", isMatch: true },
      { text: " and all", isMatch: false },
    ]);
  });

  it("is the text unmarked when the query is blank or matches nothing", () => {
    expect(highlightedSegments("Chats", "")).toEqual([{ text: "Chats", isMatch: false }]);
    expect(highlightedSegments("Chats", "dock")).toEqual([{ text: "Chats", isMatch: false }]);
    expect(highlightedSegments("", "dock")).toEqual([]);
  });
});

describe("snippetInView", () => {
  it("leaves a snippet alone when its match is within what the row shows", () => {
    expect(snippetInView("we talked about the dock here", "dock")).toBe("we talked about the dock here");
    expect(snippetInView("the part where we talked about the dock", "dock")).toBe(
      "the part where we talked about the dock",
    );
  });

  it("cuts the lead-in to a few whole words so the match is not hidden behind the ellipsis", () => {
    const snippet = "one two three four five six seven eight nine ten eleven twelve the dock at last";
    const shown = snippetInView(snippet, "dock", 20);
    expect(shown.startsWith("…")).toBe(true);
    expect(shown).toContain("dock at last");
    // Cut between words, and no more than the lead allows before the match.
    expect(shown).toMatch(/^…[a-z]/);
    expect(shown.toLowerCase().indexOf("dock")).toBeLessThanOrEqual(21);
  });
});
