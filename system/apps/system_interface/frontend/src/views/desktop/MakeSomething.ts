/**
 * The Make something window: the ways into the product, and the templates other people published.
 *
 * One window, however many times it is asked for. Everything in it starts a new chat seeded with a
 * prompt -- which goes to whichever app declares an action that takes a first message, so this
 * names no app either.
 */

import m from "mithril";
import type { CatalogTemplate, TemplateCatalogState } from "../../models/TemplateCatalog";
import { resolveShelves } from "../../models/TemplateCatalog";
import { TemplateArt } from "../TemplateArt";
import { START_OPTIONS, startGlyph } from "../startSomething";
import type { StartOption } from "../startSomething";

export interface MakeSomethingAttrs {
  catalog: TemplateCatalogState;
  /** Start a new chat on this prompt. */
  onStart: (prompt: string) => void;
}

const START_HEADING = "Start something";
const INSPIRATION_HEADING = "Inspiration";
const INSPIRATION_ANCHOR = "make-inspiration";
const TILE_GLYPH_SIZE = 26;
const ART_GLYPH_SIZE = 22;

/** What adopting a template asks for, as the first message of the chat that does it. */
export function adoptTemplatePrompt(template: CatalogTemplate): string {
  return (
    `I want to use the "${template.title}" template (${template.repository_url}). ` +
    "Adopt it into my workspace and walk me through anything it needs from me."
  );
}

function startTile(option: StartOption, onStart: (prompt: string) => void): m.Vnode {
  return m(
    "button.start-tile",
    {
      key: option.key,
      "data-start": option.key,
      onclick: () => {
        // The one tile whose answer is further down this same window scrolls there instead.
        if (option.prompt === null) {
          document.getElementById(INSPIRATION_ANCHOR)?.scrollIntoView({ behavior: "smooth" });
          return;
        }
        onStart(option.prompt);
      },
    },
    [
      m.trust(startGlyph(option, TILE_GLYPH_SIZE, true)),
      m("span.start-tile-title", option.title),
      m("span.start-tile-desc", option.description),
    ],
  );
}

function templateCard(template: CatalogTemplate, onStart: (prompt: string) => void): m.Vnode {
  return m(
    "button.template-card",
    {
      key: template.slug,
      "data-template": template.slug,
      title: template.description !== "" ? template.description : template.title,
      onclick: () => onStart(adoptTemplatePrompt(template)),
    },
    [
      m(TemplateArt, { template, frameClass: "template-thumb", glyphSize: ART_GLYPH_SIZE }),
      m("span.template-text", [m("span.template-title", template.title), m("span.template-byline", template.author)]),
    ],
  );
}

function shelves(catalog: TemplateCatalogState, onStart: (prompt: string) => void): m.Children {
  switch (catalog.kind) {
    case "loading":
      return m("p.shelf-status", "Loading templates...");
    case "failed":
      return m("p.shelf-status", "Failed to load templates.");
    case "disabled":
      return m("p.shelf-status", "No template catalog is configured on this machine.");
    case "loaded":
      return resolveShelves(catalog.catalog).map((shelf) =>
        m("div.shelf-block", { key: shelf.key }, [
          m("p.shelf-title", shelf.title),
          m(
            "div.shelf",
            shelf.templates.map((template) => templateCard(template, onStart)),
          ),
        ]),
      );
  }
}

export const MakeSomething: m.Component<MakeSomethingAttrs> = {
  view(vnode) {
    const { catalog, onStart } = vnode.attrs;
    return m("div.make-body", [
      m("p.make-heading", START_HEADING),
      m(
        "div.start-grid",
        START_OPTIONS.map((option) => startTile(option, onStart)),
      ),
      m("p.make-heading.is-later", { id: INSPIRATION_ANCHOR }, INSPIRATION_HEADING),
      m("div.shelves", shelves(catalog, onStart)),
    ]);
  },
};
