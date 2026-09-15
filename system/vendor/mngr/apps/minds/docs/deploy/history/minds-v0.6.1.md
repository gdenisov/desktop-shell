# minds-v0.6.1 (2026-09-14/15): the second gen-2 release, cut and built; not deployed

Tag pair: mngr `0c9d81e7f6` (reachable on `main` as the second parent of merge
`f0229ba36d`, PR #1032), default-workspace-template `a87c68e19` (DWT PR #595's
merge into `main`), both `minds-v0.6.1`, annotated; vendor-match verified
against DWT `origin/main` after the merge (`system/vendor/mngr` equals
`git archive` of the mngr SHA modulo `.minds/`). ToDesktop build
`260915wjcyd06bp` (pre-merge launch-to-msg run 34912494056 on the frozen pair
`0c9d81e7f6` / DWT `b9bed4e23`, a real 40 minute run with `build`,
`macos_launch`, `launch_to_msg` and `notify_slack` all green; the tag run is
recorded below).

Cut from `main` one day after `minds-v0.6.0`, carrying the ~600 commits that
landed in between: the workspace stop kinds and the migrate's latchkey-state
replay (both drilled on staging before the cut; see
[minds-v0.6.0.md](./minds-v0.6.0.md)), the chat-agent refactor, proactive
compaction, the new-tab chat tile, the latchkey 3.13.0 bump, the remote
workspace fixes and the connector's web-pin read from the release feed. **The
build and the tags are the whole of this entry**: nothing was deployed to any
tier, no pool was baked at the tag, and no channel moved. Josh is doing the
staging rehearsal, the tier deploys, the bakes and the promotions by hand.

## What rode along in the bump commit

- Version 0.6.1, `FALLBACK_BRANCH = minds-v0.6.1`, and the 0.4.0 wire-compat
  snapshot's window extended to 2026-10-14: the only `wire_types.py` change
  since `minds-v0.6.0` is the optional `stop_kind` field on the workspace entry
  and the `WorkspaceStopKind` enum it carries, so no new snapshot was needed.
- **A real bug on `main`, found while establishing state:** every ToDesktop
  build since the latchkey 3.13.0 bump (`71612b28ad`, 2026-09-14) failed at
  "Configuring App" with `pnpm install --prod=false` exiting 1 on both the Mac
  and Linux targets (run 34901210332 on `main` `f017239585`). latchkey pins
  playwright to an exact version, 3.13.0 pinned one published inside
  `pnpm-workspace.yaml`'s 14-day `minimumReleaseAge` cooldown, and ToDesktop's
  agents install with `--no-frozen-lockfile`, which re-resolves from the
  registry and refuses it; CI's frozen installs never noticed. The fix
  (`playwright` and `playwright-core` added to `minimumReleaseAgeExclude`,
  first written on the unmerged `mngr/linux-package-testing` branch, PR #968)
  was cherry-picked into the bump commit and proven locally: deleting the
  lockfile and running `pnpm install --lockfile-only` fails without the
  exemption and passes with it. Without this, no 0.6.x build was possible
  until the cooldown expired around 2026-09-19.
- Debian trixie guest-image pins (DWT `[providers.lima]` and
  `mirror_artifacts.py`, both `20260722-2547`) and the apt snapshot timestamps
  (`20260725T000000Z` on both sides) were checked and needed no bump, so
  step 0 did not apply. No tier configures `lima_image_base_url`, so 8b did
  not apply either.

## Verification

- mngr PR #1032 CI (run 34912440779): every job green, including
  `test-offload`, `test-offload-acceptance`, `test-docker` and
  `test-minds-snapshot`.
- The minds release dispatch (`run_minds_release_tests=true`, run 34912507972,
  `template_ref` = DWT `b9bed4e23`, one hour end to end): `test-minds-release`
  green with 3 `minds_deployment` tests passed and 5 plain release tests
  passed / 1 skipped (the AWS opt-in); `test-minds-snapshot` green with 24
  `minds_services` passed / 3 skipped (the relay tests skip by design on a
  per-run env). `build-minds-ci-env`, `warm-minds-pool-cache` and
  `destroy-minds-ci-env` all green.
- DWT PR #595 CI: green on the second run (34912828885) after a changelog
  entry was added. The first run failed twice: the `check-changelog` gate
  (the vendor refresh counts as the synthetic `dev` project and needs
  `system/changelog/<branch>.md`, as PR #584 had), and
  `system/scripts/agy_shim/agy_shim_test.py::test_the_reminder_returns_on_the_next_turn`,
  a dwt-native test whose own comment says the kernel may hand a recreated
  marker the same inode; it fired the `[Step tracking reminder]` instead of
  the `Open task reminder` it asserts on. Not caused by the vendor refresh
  (the strings are dwt scripts, and `main` was green on the same base); it
  passed on the rerun. Worth marking flaky or hardening in DWT.
- Tag run: launch-to-msg run 34916838276 on `commit_sha=minds-v0.6.1` /
  `template_ref=minds-v0.6.1`, green in 16 minutes: `build` reused
  `260915wjcyd06bp` for the identical mngr SHA (by design), and
  `launch_to_msg` was a real 15 minute round-trip through the binary's baked
  `FALLBACK_BRANCH`, not a marker-cache skip. Green here concludes the
  artifacts.

## Deferred, deliberately (Josh)

- The staging rehearsal at `minds-v0.6.1` (bake, the app checklist, retire).
- The production gen-2 bring-up still listed in [../next_deploy.md](../next_deploy.md),
  the production services deploy, the production bake, and every channel
  promotion (desktop and web). Beta and stable remain at 0.5.2 build
  `260909wnd3gb4z1`; alpha too, since 0.6.0 was never promoted.
- The `agy_shim` inode flake above, in the DWT repo.
