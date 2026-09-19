/**
 * The colour each app's tile wears, on the desktop, in the dock and in a window's title bar.
 *
 * The shell knows no app by name, so a tile's colour cannot come from a table keyed by one. It is
 * a hash of the app's registry name into a fixed palette instead, which is stable in the way that
 * matters: installing, removing or re-ranking an app changes no other app's colour. (An index into
 * the palette by launcher rank would not be -- an app installed between two others would shift
 * every colour after it.)
 *
 * The palette's order is chosen so the apps a workspace ships with land on the colours they have
 * always worn; ``tints.test.ts`` pins that. Two apps can hash to one slot: they then share a tint,
 * which their different glyphs still tell apart, and that is accepted rather than worked around.
 */

/** A tile's two colours: the tint behind the glyph, and the glyph itself. */
export interface Tint {
  background: string;
  foreground: string;
}

/** The desktop's own entry for making something, which is not an app and has its own dark tile. */
export const MAKE_SOMETHING_TINT: Tint = { background: "#23262b", foreground: "#ffffff" };

/**
 * The tints, in the order the hash indexes them. Twelve rather than a handful so apps collide
 * rarely, and arranged so that a workspace's built-in apps take the four at the front of the
 * design: green, amber, blue and grey.
 */
export const TINTS: readonly Tint[] = [
  { background: "#fdf1dd", foreground: "#d97706" },
  { background: "#efeafd", foreground: "#6d47d9" },
  { background: "#fdeaf1", foreground: "#db2777" },
  { background: "#e7f4ec", foreground: "#2f855a" },
  { background: "#e8f6f8", foreground: "#0e7490" },
  { background: "#f1f2f5", foreground: "#3f3f46" },
  { background: "#f3f7e8", foreground: "#4d7c0f" },
  { background: "#e7f0fd", foreground: "#2563eb" },
  { background: "#fdeee8", foreground: "#c2410c" },
  { background: "#eef0f6", foreground: "#475569" },
  { background: "#fbf0e4", foreground: "#92400e" },
  { background: "#eaf7f1", foreground: "#0f766e" },
];

const FNV_OFFSET_BASIS = 0x811c9dc5;
const FNV_PRIME = 0x01000193;

/** FNV-1a over the name's bytes: a small, stable, well-spread string hash. */
export function hashName(name: string): number {
  let hash = FNV_OFFSET_BASIS;
  for (let index = 0; index < name.length; index += 1) {
    hash = Math.imul(hash ^ name.charCodeAt(index), FNV_PRIME) >>> 0;
  }
  return hash;
}

/** The tint an app wears, wherever it is drawn. */
export function tintForApp(appName: string): Tint {
  return TINTS[hashName(appName) % TINTS.length];
}
