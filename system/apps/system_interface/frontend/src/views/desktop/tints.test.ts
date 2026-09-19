import { describe, expect, it } from "vitest";

import { TINTS, hashName, tintForApp } from "./tints";

// The apps a workspace ships with, and the colours they have always worn. Named here, in a test,
// rather than in the shell: the shell knows no app by name, and this is what pins that the
// name-hash lands them on the design's own palette anyway.
const BUILT_IN_COLOURS: Record<string, string> = {
  chat: "#2f855a",
  files: "#d97706",
  browser: "#2563eb",
  terminal: "#3f3f46",
};

describe("tintForApp", () => {
  it("gives the built-in apps the colours the design gives them", () => {
    for (const [appName, foreground] of Object.entries(BUILT_IN_COLOURS)) {
      expect(tintForApp(appName).foreground, appName).toBe(foreground);
    }
  });

  it("gives those apps four different tints", () => {
    const tints = Object.keys(BUILT_IN_COLOURS).map((appName) => tintForApp(appName).background);
    expect(new Set(tints).size).toBe(tints.length);
  });

  it("gives one app one colour, whatever else is installed", () => {
    // The hash is of the name alone, so nothing about the rest of the machine can move it.
    expect(tintForApp("roadmap")).toEqual(tintForApp("roadmap"));
    expect(tintForApp("roadmap")).not.toEqual(tintForApp("roadmap-2"));
  });

  it("always answers with a tint from the palette", () => {
    for (const appName of ["a", "zzzz", "app-with-a-very-long-name", "6", ""]) {
      expect(TINTS).toContainEqual(tintForApp(appName));
    }
  });
});

describe("hashName", () => {
  it("is a stable 32-bit hash", () => {
    expect(hashName("chat")).toBe(hashName("chat"));
    expect(hashName("chat")).not.toBe(hashName("chats"));
    expect(hashName("terminal")).toBeGreaterThanOrEqual(0);
    expect(hashName("terminal")).toBeLessThan(2 ** 32);
  });
});
