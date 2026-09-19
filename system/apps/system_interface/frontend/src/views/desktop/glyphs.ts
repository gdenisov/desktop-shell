/**
 * The desktop's own glyphs: the marks on its window buttons, its dock and its launcher.
 *
 * Drawn on the same 24x24 grid as the shared icon set, and kept here rather than added to that set
 * because they are this surface's furniture (a window's maximize chevron, the dock's hide control)
 * rather than vocabulary any other page would reach for.
 */

const XMLNS = "http://www.w3.org/2000/svg";

const PATHS = {
  /** "Make something": the desktop's one entry that is not an app. */
  sparkle:
    '<path d="M12 3l1.9 4.9L19 9.8l-5.1 1.9L12 16.6l-1.9-4.9L5 9.8l5.1-1.9z"/><path d="M18.5 14.5v4"/><path d="M16.5 16.5h4"/>',
  /** An app with no icon of its own, and a search result whose app is not registered. */
  app: '<rect x="3" y="3" width="7" height="7" rx="1.6"/><rect x="14" y="3" width="7" height="7" rx="1.6"/><rect x="3" y="14" width="7" height="7" rx="1.6"/><rect x="14" y="14" width="7" height="7" rx="1.6"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  /** The dock's search field, from lucide's ``search``. */
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  /* The size button's two faces, lucide's ``maximize-2`` and ``minimize-2``: the pair that ships
     for exactly this toggle, so the one button that changes a window's size is the only place
     either arrow appears. Putting the dock button on one of them would have left two "shrink"
     glyphs side by side in a maximized window's title bar. */
  /** Fill the desktop: arrows pushing out to the corners. */
  maximize: '<path d="M15 3h6v6"/><path d="m21 3-7 7"/><path d="m3 21 7-7"/><path d="M9 21H3v-6"/>',
  /** Go back to the rect it was filling the desktop from: the same arrows, drawn inward. */
  restore: '<path d="m14 10 7-7"/><path d="M20 10h-6V4"/><path d="m3 21 7-7"/><path d="M4 14h6v6"/>',
  /** Putting a window away into the dock, which changes no size and wears no arrow. */
  minimize: '<line x1="6" y1="12" x2="18" y2="12"/>',
  close: '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
  /** The dock's hide control while it is hiding: hover to bring the dock back. */
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
  /** The same control while the dock is pinned, from the lucide ``eye-dashed`` drawing: the eye
   *  broken into strokes, which reads as "this can go away" beside the solid one above. */
  eyeDashed:
    '<path d="M13.054 18.946a11 11 0 0 1-2.11 0"/><path d="M13.054 5.054a11 11 0 0 0-2.11-.001"/>' +
    '<path d="M17.072 6.274a11 11 0 0 1 1.753 1.173"/><path d="M18.825 16.552a11 11 0 0 1-1.753 1.174"/>' +
    '<path d="M2.514 13.303a11 11 0 0 1-.452-.954 1 1 0 0 1 0-.697 11 11 0 0 1 .45-.955"/>' +
    '<path d="M21.485 10.697a11 11 0 0 1 .453.955 1 1 0 0 1 0 .697 11 11 0 0 1-.453.954"/>' +
    '<path d="M5.173 7.448a11 11 0 0 1 1.753-1.174"/><path d="M6.926 17.726a11 11 0 0 1-1.753-1.174"/>' +
    '<circle cx="12" cy="12" r="3"/>',
} as const;

export type DesktopGlyphName = keyof typeof PATHS;

/** One glyph as inline SVG markup, in the caller's current text colour. */
export function glyph(name: DesktopGlyphName, strokeWidth = 1.9): string {
  return (
    `<svg xmlns="${XMLNS}" viewBox="0 0 24 24" fill="none" stroke="currentColor" ` +
    `stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">` +
    `${PATHS[name]}</svg>`
  );
}
