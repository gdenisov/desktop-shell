---
title: "Desktop Shell"
description: "A desktop workspace UI: a rearrangeable icon grid, a dock that opens into search, windows with put-away tiles, and chats as pages with an instance rail."
thumbnail: "template.svg"
version: v1
format: v2
---

# Desktop Shell

This file is the manifest for the **Desktop Shell** template (slug:
`desktop-shell`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a mind that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

A desktop workspace UI: a rearrangeable icon grid, a dock that opens into search, windows with put-away tiles, and chats as pages with an instance rail.

This template replaces the workspace's own interface with a desktop. Opening
the workspace shows a plain surface with an icon for each app along the top:
icons can be picked up and dropped anywhere, and they land in a cell of a grid
that spans the whole surface, so the arrangement is the user's and it survives
a resize. Clicking one opens a floating window -- draggable, resizable,
maximizable, renameable by its title -- that shows one thing the app owns (one
chat, one terminal session, one file). A dock along the bottom shows what is
RUNNING rather than what happens to be open, with a smaller tile for anything
running with no window on screen. The dock's `+` is the single way
into everything: closed it is a round button, a click widens it into a search
field with a short menu of apps ordered by recent use, and typing turns the
menu into a live search over everything the user has ever started -- running or
stopped, by title and, for apps that opt in, by what is inside them (the chat
app searches its own transcripts). A window of an app that browses its own
instances -- the chat -- carries a rail of that app's instances down its left
side, so the chat list belongs to the desktop and the chat app stays a plain
page per conversation. Nothing here is specific to the mind that built it: the
desktop learns every app from the app registry, so an app added later shows up
with no change to this code.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/system_interface`
- `system/apps/chat`
- `system/libs/workspace_ui`
- `system/libs/app_instances`
- `system/libs/app_manifest`
- `system/scripts/layout.py`
- `system/scripts/layout_test.py`
- `system/scripts/migrate_workspace_layouts_test.py`
- `system/scripts/forward_port.py`
- `system/package-lock.json`
- `.gitignore`
- `system/supervisord.conf.d/system_interface.conf`
- `system/supervisord.conf.d/chat.conf`
- `.agents/skills/update-system-interface`
- `.agents/skills/manage-layout`
- `.agents/skills/update-app`
- `.agents/skills/build-app`
- `.agents/skills/assist`
- `.agents/skills/migrate-workspace`
- `.agents/shared/worker/references/type-system-interface.md`
- `docs/system/blueprint/workspace-app-model/contracts.md`

Two of these paths are apps (a supervised program with a manifest, a registry
row and its own browser origin), three are shared Python libraries, four are
scripts, six are agent skills, and two are contract documents. What each one
is:

**The apps**

- `system/apps/system_interface` -- the shell itself, and the bulk of this
  template. Its Python side (`imbue/system_interface/shell/`) keeps the
  inventory over the app registry, relays every instance verb to the app that
  owns it, and stores projects, per-client desktop layouts, client records and
  the client-activity log under `data/.state/system_interface/`. Its frontend
  (`frontend/`, Mithril + Tailwind over the shared token layer) is the desktop:
  the icon grid (`views/desktop/icons.ts`), the floating windows
  (`views/desktop/DesktopShell.ts`, `windowState.ts`), the dock
  (`views/desktop/dock.ts`), the search-and-apps `+`
  (`views/desktop/Launcher.ts`), and the instance rail
  (`views/desktop/instanceRail.ts`).
- `system/apps/chat` -- the agent-harness UI: one page per conversation, framed
  by the shell at the chat app's own origin. It is included because moving the
  chat list onto the desktop changed both sides -- the app now serves a plain
  page per chat and publishes its chats through the instances API, and the
  shell draws the list. It also owns the provider accounts (sign-in, the model
  picker, moving a chat between accounts or harnesses) and the per-chat
  transcript search the desktop's search calls into.

**The shared libraries**

- `system/libs/workspace_ui` -- the frontend library every page in the
  workspace builds against: the browser-side app contract
  (`src/app_contract.ts`, served by the shell at `/_static/app_contract.js`),
  the semantic token layer and base styles (`src/base.css` -- the palette,
  radii and fills this desktop is drawn in), and the shared components.
- `system/libs/app_instances` -- the instance model: the records an app
  publishes on its instances API, which are what the dock, the search results
  and a window's instance rail are made of.
- `system/libs/app_manifest` -- the `app.toml` manifest and the app registry
  (`data/.state/apps.toml`) the shell's inventory reads. The flags the desktop
  keys off (`instances`, `browses_instances`, `instance_search`,
  `launcher_rank`, `default_shortcut`, `critical`) are defined here.

**The scripts**

- `system/scripts/layout.py` (with `layout_test.py` and
  `migrate_workspace_layouts_test.py`) -- the agent-facing layout CLI:
  `list / inspect / where / context / open / focus / split / close / move /
  rename / delete / stop / start / maximize / restore / refresh / ...`, all
  speaking addresses (`app:<name>?instance=<key>`). This is how an agent
  arranges the user's desktop from a script.
- `system/scripts/forward_port.py` -- how an app registers its manifest and
  port into the registry at startup, which is the only way the shell learns an
  app exists.
- `system/package-lock.json` -- the npm workspace lockfile. It ships with the
  changed `frontend/package.json`, or `npm ci` refuses the mismatch.

**The skills and contracts** -- `update-system-interface` (the canonical flow
for changing this shell: worker, isolated test, preview as a tab, then the
update apply), `manage-layout` (driving `layout.py`), `update-app` and
`build-app` (the app lifecycle every new app goes through, both written against
the desktop's vocabulary), `assist` and `migrate-workspace`. Each was revised
alongside the shell, so an adopting mind wants these versions rather than its
own. `docs/system/blueprint/workspace-app-model/contracts.md` is the authority
for every route, message and file format named above, and
`.agents/shared/worker/references/type-system-interface.md` is the worker
reference the update flow hands to whoever edits this code. `.gitignore` is
included for the rule that keeps stray per-package `data/` directories out of
the tree.

**How it runs.** Two supervisord programs, each in its own drop-in.
`system/supervisord.conf.d/system_interface.conf` runs `system-interface` on
`127.0.0.1:8000` after registering `system/apps/system_interface/app.toml`
through `forward_port.py`; `system/supervisord.conf.d/chat.conf` runs
`chat-app`, which registers its own manifest and port 8010 from inside its
entry point and starts `mngr observe` for the workspace's agents. Both are
wrapped in `system/services/oom_priority/bin/oom_tag_service.py` so the
memory-pressure daemon sheds them last. The shell polls each app's instances
API (and refetches on an app's `POST /api/apps/<name>/changed` nudge), pushes
the diff to every browser over its WebSocket as `apps_updated`, and relays
rename / stop / start / delete back to the owning app. Both frontends are
members of the npm workspace rooted at `system/package.json` and build into
their package's `static/` directory (`cd system && npm run build`), which is
gitignored build output -- so an adopting mind must build them once before the
desktop serves anything but its recovery placeholder.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting mind must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.

### Activation

- requires_llm: keyless -- the chat app runs whole agent CLIs (Claude Code,
  Codex, and the other harnesses mngr has plugins for) as PTY sessions, signed
  in against a provider account the user creates from the chat page's own
  provider chooser (`harnesses/auth_flows.py`, accounts under
  `~/.minds/accounts`). No model call is made by this code and no key is baked
  into it, so the adopting agent does not set anything up ahead of time: it
  opens a chat and lets the user sign in. The same sign-in screen also accepts
  a pasted `ANTHROPIC_API_KEY` (optionally with an `ANTHROPIC_BASE_URL` for a
  litellm proxy) for an adopter who is on the keyed path instead -- the app
  supports both routes with no code change, so nothing needs switching per
  use-ai-integration.

Nothing else needs activating. The included code reaches no third-party
service, so there are no latchkey permissions to request and no secrets to
supply. (The chat app does call the latchkey *gateway* for one thing -- the
display name and description of a permission scope, to label a permission
request card -- but that is the gateway's own catalog endpoint, authorized by
the credentials every agent container already has, and it degrades to an
unlabeled card when the gateway is absent.)

### Adaptation

- **Both frontends must be built once after adoption.** The served bundles
  (`system/apps/*/imbue/*/static/`) are gitignored build output, and nothing
  rebuilds them at service start. Until they exist the shell serves its
  recovery placeholder instead of the desktop. A working replacement is one run
  of `cd system && npm ci && npm run build` followed by
  `supervisorctl restart system_interface chat` -- or, in a mind that is
  already running, letting the `update-self` apply flow do it, which builds,
  pre-flight-boots both processes and reloads every open view.
- **This overwrites the adopting mind's own workspace UI and chat app.** Both
  are stock apps of every mind, so an adopter that has already customized
  either one loses those edits at the overlay and must re-apply them on top.
  The same is true of the six included skills and the two contract documents:
  they are this shell's versions, and a mind on a newer workspace template
  should diff them against its own copies rather than assume they are newer.
- **The desktop arrives empty of arrangement.** None of the publisher's
  workspace data was included -- no chats, no saved icon positions, no window
  layouts, no projects. The first boot shows the default icon row and no
  windows, and the arrangement becomes the adopter's as soon as they move
  anything. Nothing needs restoring; this bullet exists so the empty desktop is
  not mistaken for a broken one.
- **The icon grid shows the adopting mind's apps, not the publisher's.** Icons
  come from the app registry in `launcher_rank` order, so whichever apps that
  mind has are what appears. An adopter who wants a particular app first, or
  wants one kept off the desktop entirely, sets `launcher_rank` (or `internal`)
  in that app's own `app.toml` -- there is no list of apps in this code to
  edit.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting mind
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that mind's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment.

Both included apps are apps every mind already has, so everything they reach
for is already there: Python and `uv` for the two backends, Node and npm for
the two frontends (they are members of the npm workspace the template already
roots at `system/package.json`, and every dependency they add is pinned in the
included `system/package-lock.json`), `supervisorctl` for the app Stop and
Start verbs, and `mngr` plus the harness CLIs the chat app drives as
subprocesses. No apt package, no global npm/uv/cargo tool, and no `env.d` unit
is added by this template.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new mind. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the minds app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-09-18) -- the workspace UI rebuilt as a desktop: a rearrangeable icon grid, floating windows with a put-away dock, a `+` that opens into an apps-and-search field, and an instance rail that moves the chat list out of the chat app and onto the desktop.

## Adaptation history

Each mind that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
