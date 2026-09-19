<p align="center">
  <img alt="Desktop Shell" src="template.svg" width="480">
</p>

# Desktop Shell

<p align="center">
  <a href="https://boweiliu.github.io/open-in-minds/?git_url=https://github.com/gdenisov/desktop-shell"><img alt="Open in Mind" height="64" src="https://img.shields.io/badge/Open%20in%20Mind-D8D1C0?style=for-the-badge"></a>
</p>

Didn't work? Create a Mind workspace and paste this to your agent:
` /use-template https://github.com/gdenisov/desktop-shell`

## Why you care

This gives the workspace the window experience of a desktop operating system.
Apps are icons on a surface you arrange yourself. Opening one gives you a real
window to drag, resize, maximize and put away. A dock along the bottom shows
what is running, and a single `+` is both the app menu and a search over
everything you have ever started -- including the things you stopped.

## How to use it

Adopt it and open your workspace. There is nothing to configure: the desktop
reads your apps from the app registry.

- **Icons.** Drag one anywhere. They snap to a grid, never overlap, and your
  arrangement is saved and survives a resize.
- **Windows.** Clicking an icon opens one thing -- a chat, a terminal session,
  a file. Drag the bar to move, any edge to resize, double-click the bar to
  maximize, the title to rename.
- **Three verbs.** Minimize puts a window away with its page still running.
  Close stops what the window is showing. Maximize remembers where to go back
  to. None of them deletes anything.
- **The dock.** One tile per running instance, drawn smaller for anything
  running with no window on screen. It scrolls sideways when crowded.
- **The `+`.** Click it and it widens into a search field over a menu of your
  apps, most recently used first. Type, and it searches everything you have
  ever started -- running or stopped, by title and, for apps that opt in, by
  what is inside them. Opening a stopped result starts it again.
- **The instance rail.** An app that browses its own instances gets a list of
  them down the left of its window. For the chat app that is your chat list:
  rename in place, collapse it to monograms, stop or restart from a
  right-click.

An agent can drive all of it through `system/scripts/layout.py`, whether or not
a browser is connected:

```bash
python3 system/scripts/layout.py open terminal
python3 system/scripts/layout.py split app:chat?instance=chat-1
python3 system/scripts/layout.py rename app:terminal?instance=terminal-3 "Build log"
```

The `manage-layout` skill is the orientation for that, and
`update-system-interface` is the flow for changing the shell itself.

## Ideas for making it yours

- **Repaint it.** The desktop draws from the tokens in
  `system/libs/workspace_ui/src/base.css`, so the shell, the chat app and every
  app you build later move together. A dark desktop is a few token edits.
- **Give the dock your own rules.** Group tiles by app, pin a favourite, or
  badge a chat that has something new to say.
- **Point a rail at your own app.** Any app that sets `browses_instances` in
  its `app.toml` gets one -- and you choose what the rows are sorted by.
- **Teach the search a new source.** An app that declares `instance_search`
  joins the same `+` field with no change to the shell.
- **Bring projects to the surface.** Named, shared tab sets exist underneath
  with no UI on them; adding a view switcher is a front-end job on ground that
  is already there.

## What this is

This repository is a published **minds template**: a clean, bootable
snapshot of what a mind built, ready to adapt into your own. It is NOT the
generic workspace template -- it is this specific project.

[`template.md`](template.md) is the full manifest -- what it is, how it
works, what it needs to run, and what to adapt -- with the
machine-readable half (recipe, requirements, and the environment it needs
installed) in [`template.toml`](template.toml).
