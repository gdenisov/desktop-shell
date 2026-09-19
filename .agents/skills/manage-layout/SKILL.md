---
name: manage-layout
description: Use when you want to rearrange the workspace's desktop windows (open, split, move, focus, close, maximize, reload, rename, delete, stop or start an instance) or inspect what is on screen.
metadata:
  author: imbue
  crystallized: true
---

# Managing the workspace desktop

The user interacts with you (and the apps you build) through a **desktop**
defined in `system/apps/system_interface`: launcher icons, floating windows,
and a dock along the bottom of what is running. Your chat is one such window;
every other window is an instance of some app: a terminal, a browser, a files
page, another agent's chat, an app you built.

`system/scripts/layout.py` is the agent-facing helper. Use it whenever you
want to surface, inspect, or rearrange windows. Do not hand-edit the desktop's
saved layouts.

> **Where the script lives:** `layout.py` is at the **repo root**, at
> `system/scripts/layout.py` (i.e. `/home/user/workspace/system/scripts/layout.py`,
> the container WORKDIR). It is **NOT** inside this skill's folder. Every
> command below is written as `python3 system/scripts/layout.py ...`, a path
> relative to the repo root, which is the cwd for all commands in this repo.

## Addresses (read this first)

Every window is named by one **address**:

| Form | Meaning | Example |
|---|---|---|
| `app:<name>?instance=<key>` | One instance of an app. | `app:chat?instance=agent-3f2a...` (a chat, keyed by its chat id: `$MINDS_CHAT_ID`, the id of its first agent), `app:terminal?instance=terminal-2` (a terminal, keyed by its tmux session name), `app:browser?instance=riley` (a browser, keyed by its name) |
| `app:<name>` | A single-instance app's one window (an app built without `instances = true`); or, as an `open` / `split` target for an app with instances, "a fresh instance of this app". | `app:docs` (a single-instance app you built), `open app:terminal` |

A bare word is shorthand for `app:<word>` (`open files`). The literal `self`
resolves to your own chat's window; most useful as `--relative-to=self` on
`split` / `move`. Your own chat's address is `app:chat?instance=${MINDS_CHAT_ID:-$MNGR_AGENT_ID}`
(the chat app sets `MINDS_CHAT_ID` on every agent it creates; an agent created any
other way is its own chat).

`layout.py list` prints every address on the machine, with each instance's
title and status, so you never have to guess: find the row whose title the
user said, and use its address. An instance has exactly one window, so an
`open` of something already open raises that window rather than making a
second one.

A bare `https://` URL is also an `open` target: `open https://example.com`
starts a new browser on that page (the browser app's `new` action with the URL
as its `url` param) and prints the new browser's address.

## Clients and views

The workspace shows one *view* at a time: a **project** (a shared set of tabs
plus its own desktop) or **Everything** (every instance on the machine).
Every browser **client** (one per browser; its windows share it) has one
active view and its own desktop for every view -- the windows it holds, where
each one sits, where its icons sit -- kept in a file on the shell. That file is
the truth: the browser saves the user's own gestures into it, and the shell
edits it for your ops, so an op lands whether or not a browser is connected and
a connected window shows it within a redraw.

- **Every op targets exactly one client.** With no `--client`, that is the
  client that most recently messaged you, else the one connected client.
  When neither settles it (several clients, an agent nobody messaged), the op
  is refused with the connected clients listed; pass `--client <id>` (from
  `context`). Ops are never applied to every client at once.
- **An op with no `--view` edits the client's active view.** That is what
  you want nearly always; just run the op.
- **Pass `--view <name>` to edit a different view** (a project's name, or
  `Everything`). The op edits that view's arrangement and switches the client
  to it, so the user sees what you arranged.
- **`views` lists the views**: every project plus Everything, each with its
  tab set and which connected clients have it in front.
- **`context` tells you which client asked**: every known client with its
  device kind, active view, connection state, and last few messages. The
  client that most recently messaged you is almost always the requester.
- **`load <view>` switches a client onto a view** without changing any
  arrangement (`load "Research"`).

Every window you open in a project files its address into that project's tab
set, so the project keeps it on every device.

## The verbs you'll use 95% of the time

| Goal | Command |
|---|---|
| See which client/view asked for something | `python3 system/scripts/layout.py context` |
| List every app and instance (address, title, status, where docked) | `python3 system/scripts/layout.py list` |
| List the views and who is on each | `python3 system/scripts/layout.py views` |
| See what's currently open and how it's laid out | `python3 system/scripts/layout.py inspect [--view <name>]` |
| Locate one window and what sits around it | `python3 system/scripts/layout.py where <address> [--view <name>]` |
| Switch a client onto a view | `python3 system/scripts/layout.py load <view> [--client <id>]` |
| Surface an instance in a window beside your chat | `python3 system/scripts/layout.py open <address>` |
| Open a web page in a new browser | `python3 system/scripts/layout.py open https://example.com` |
| Create an instance with arguments | `python3 system/scripts/layout.py open terminal --param workdir=/data` |
| Tile against a window other than your chat | `python3 system/scripts/layout.py split terminal --relative-to=<address> --direction=right` |
| Put a window away (the instance keeps running) | `python3 system/scripts/layout.py close <address>` |

`open` is the opinionated default. It puts the new window **beside your own
chat's window**, each taking half the desktop, so what you opened for the user
sits next to the conversation that asked for it instead of covering it. Steer it
with the same flags a `split` takes -- `--relative-to` for a different anchor,
`--direction left|right|above|below`, `--ratio` for the share the new window
gets. With nothing to sit beside (your chat has no window on that desktop) the
window is cascaded at the default size instead; an `open` never fails for want
of an anchor.

Use `split` when you want to tile against a window that is *not* your chat and
want the op to fail loudly if that anchor is missing.

What `open` does with each target:

- `open app:docs` (a single-instance app, one you built without
  `instances = true`): opens its one window, or raises it if it is already open.
- `open app:terminal?instance=terminal-2` (an instance address): opens a window
  on that instance, or raises the one it already has.
- `open terminal` (a bare app that has instances): runs the app's action
  through the app and creates a **fresh** instance every time, exactly like
  the rail's "New Terminal". The new instance's address is printed to
  **stdout** so you can capture it for later ops. The same holds for `open
  chat` (a new chat), `open browser` (a new browser), and `open files` (a new
  file viewer: the files app has instances too, so `app:files` never names an
  open viewer). `--action <id>` picks another of the app's actions and
  `--param name=value` (repeatable) passes the create's params: `open
  terminal --param workdir=/data`, `open files --param path=/data/notes`. An
  app's refusal (a full browser fleet, no signed-in account) is the op's
  error, printed as the app spelled it.
- `open https://example.com` (a URL): a new browser on that page.

## Less common operations

All of these take the same `--client` as `open`; `split`, `focus`, `move`
take `--view` too. `maximize`, `restore`, and `refresh` change what is on the
target client's screen without changing the saved arrangement (a `refresh` of
a whole app reloads its iframes on every client):

| Goal | Command |
|---|---|
| Tile two things side by side | `python3 system/scripts/layout.py split <address> --relative-to=<address> --direction=<left\|right\|above\|below\|within> [--ratio=0.5]` |
| Bring a window to the front | `python3 system/scripts/layout.py focus <address>` |
| Snap an open window beside another | `python3 system/scripts/layout.py move <address> --relative-to=<address> --direction=<dir>` |
| Maximize / restore a window | `python3 system/scripts/layout.py maximize <address>` / `python3 system/scripts/layout.py restore` |
| Reload one window (or every iframe of an app) | `python3 system/scripts/layout.py refresh <address>` |

And five verbs that go through the app that owns the instance rather than
the dock (they take an instance address, never a bare app):

| Goal | Command |
|---|---|
| Retitle an instance (the title shows in every view) | `python3 system/scripts/layout.py rename <address> "<title>"` |
| Delete an instance (it leaves every view) | `python3 system/scripts/layout.py delete <address>` |
| Point an instance at a path under its app, or at a URL for an app that browses to one | `python3 system/scripts/layout.py replace-url <address> </path-or-url>` |
| Stop what backs an instance while keeping it (a chat's agent, a browser's Chromium, a terminal's session) | `python3 system/scripts/layout.py stop <address>` |
| Start a stopped instance again | `python3 system/scripts/layout.py start <address>` |

Not every app accepts every verb: a browser is not renameable (its title is
its name), an app that does not track locations (the terminal, the chat)
refuses `replace-url`, each app takes only the location form that fits it
(a path under the app for the file viewer, an absolute `http(s)` URL for the
browser), and only an instance the app lists as `stoppable` accepts `stop`
and `start` (a file viewer has nothing to stop). The app's refusal is
printed as the error.

### What `close`, `split` and `move` mean on a desktop

**`close` puts a window away.** It is the desktop's minimize: the window goes
out of sight, its page stays loaded, and what it shows **keeps running** and
stays in the dock, dimmed. That is what `close` has always meant for you -- it
changes no tab set and stops nothing -- and the desktop simply gives the state
a name. The verb for ending what a window shows is `stop`, and the window's own
X button is the user's gesture for it. So:

- "put that away" / "clear the screen" -> `close`
- "stop that" / "shut it down" -> `stop`

**`split` tiles.** It resizes the anchor to one half of the desktop and opens
the new window filling the other, so both can be seen at once -- which is what
you mean when you split. `--direction` picks the side the new window takes and
`--ratio` how much of the desktop it gets (half by default):

```bash
python3 system/scripts/layout.py split terminal --relative-to=self --direction=right
```

**`move` snaps an already-open window** to the named side of its anchor, with
the same tiling arithmetic, and never reloads it: a chat or a terminal keeps
its state while you tidy the screen.

`--direction within` is the one direction with no side: it gives the placed
window the anchor's own rectangle and puts it directly above the anchor -- these
two occupy one slot, and this one is on top.

`--new-group` is accepted and ignored: a desktop has no tab groups.

## Inspecting state

`inspect` prints the desktop: every window, bottom of the stack first, with
where it sits and what state it is in.

```
desktop 1440x900
  app:chat?instance=agent-3f2a  "make ipad ui"  720x900 at (0,0)
  app:terminal?instance=terminal-1  "Terminal 1"  720x900 at (720,0)  (on top)
```

A window that has been put away is marked `minimized`, and one filling the
screen `maximized`. Pass `--verbose` for the full YAML or `--json` for the
structured object. `where <address>` zeros in on one window: its title, where
it sits, and what is around it.

`list` prints every app with its instances: each instance's address, title,
status (`idle`, `working`, `attention`, `stopped`, `error`), and the ids of
the clients whose desktops hold a window on it. `list` and `views` output YAML by default;
pass `--json` for programmatic consumption.

Run `python3 system/scripts/layout.py --help` (or `<subcommand> --help`) for
the full surface.

## Ops answer at once

The shell edits the client's desktop itself and answers with the result, so
every arrangement op returns as soon as the file is written. `open`, `split`,
`move`, `focus`, and `close` print a one-line description on **stderr**;
opening an address that already has a window raises it. `maximize`,
`restore`, and `refresh` print `(sent <op> to client <id>)`.

**stdout** is reserved for machine-readable output: the address of an
instance `open` / `split` created, and the structured output of the read
commands. Descriptions always go to stderr.

## Exit codes

- `0` ok (including no-op successes)
- `1` error (the specific reason is in stderr, including "could not tell
  which client this op is for", for which you pass `--client <id>` from
  `context`, and an address that is not open or that no app lists)
- `3` the app cannot do it right now (a full browser fleet, no signed-in
  account for a new chat, an app still starting up; its 409 or 503): retry
  after a short backoff, or tell the user

## When NOT to use this skill

- **Building a brand-new app.** Use `build-app` to scaffold it first; it
  ends with a `layout.py open` call to surface the new tab.
- **Projects themselves** (what a project shows, its rail shortcuts, adding
  a tab to a project without opening it): see `manage-projects`.
- **Persisting layout state.** The desktop auto-saves on every change -- the
  windows, where they sit, the icon arrangement and the dock's own setting --
  so it comes back as the user left it.
