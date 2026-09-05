# Release Process

Mirrors [Home Keeper's](https://github.com/prestomation/ha-home-keeper/blob/main/RELEASE.md):
releases are produced by merging a single "release" PR to `main`. The PR bumps the
version and adds a changelog entry; after merge, CI tags the commit and publishes the
GitHub release automatically. No manual `git tag` step.

## Steps

1. **Open a release PR** that contains exactly these changes:
   - `custom_components/home_keeper_battery_notes/manifest.json` — bump `version` to `X.Y.Z`
   - `CHANGELOG.md` — add a `## [X.Y.Z] - YYYY-MM-DD` section

2. **Merge the PR.** On the merge commit to `main`, `release.yml` will:
   1. Read the version from `manifest.json`.
   2. Verify a matching `## [X.Y.Z]` entry exists in `CHANGELOG.md` (fails loudly if not).
   3. Skip silently if tag `vX.Y.Z` already exists. This is why a PR must bump the
      version for *every* release, a beta iteration included: folding a new entry into
      an already-tagged `## [X.Y.Z]` section without bumping the version merges clean
      and then ships nothing, because this step sees nothing new to tag. `test.yml`'s
      `changelog-release-gap` job (`ci/check-changelog-release-gap.py`) catches that
      case at PR time.
   4. Build `home_keeper_battery_notes.zip` (the HACS asset).
   5. Push tag `vX.Y.Z` and create the GitHub Release with the changelog section as the
      body and the zip attached.
   6. Comment on every issue the changelog section says this version fixes, and — on a
      stable release — close it. See "Issue notifications" below.

3. **HACS picks it up** via `hacs.json` (`zip_release: true`, `filename:
   home_keeper_battery_notes.zip`).

On a release PR (before merge) the workflow runs as a **dry run** — it validates the
version/changelog and builds the zip but does not tag or publish.

## Issue notifications

An issue closes when its fix **ships**, not when its PR merges. A merged PR is not in
anyone's Home Assistant yet — it may sit on `main` for days and go out in a beta before
it reaches everyone.

Turn closing-on-merge off for this repository, so a PR's `Fixes #N` links the issue
(filling in its **Development** panel) without closing it and the issue stays open on
its own. Keep writing `Fixes #N` — the link is worth having.

The `notify-issues` job in `release.yml` closes the loop. It reads the shipped
version's `## [X.Y.Z]` CHANGELOG section, pulls out every `(Fixes #N)` reference, and
for each one:

- **On a beta** — comments that the fix is available for testing, with the "Show beta
  versions" instructions, and leaves the issue **open**.
- **On a stable** — comments that it shipped, quotes the changelog bullet, and closes
  the issue as `completed`.

Notes on how it behaves:

- **`(Fixes #N)` in the changelog is the only thing that notifies an issue.** Forget it
  and the issue is never told and never closes. The job posts a CI warning naming any
  issue that a commit in the release referenced but the section left out — check the
  job summary after a release. A **developer-only** issue (a CI or tooling fix, which
  correctly gets no changelog entry) shows up here too; that one is expected, and
  closing it is a manual call.
- **The cross-check range depends on the kind of release.** A stable is compared
  against the previous stable, because its section rolls up every beta in between. A
  beta is compared against the previous tag of any kind, because its section covers
  only its own increment.
- **Bare `(#N)` is ignored**, because it is also the PR number squash-merge appends to
  commit subjects, and the two cannot be distinguished. So is `(Related to #N)`.
- **Re-running is safe.** Each comment carries a `<!-- battery-notes-release vX.Y.Z -->`
  marker and the job skips any issue that already has one.
- **It cannot fail a release.** The release is already tagged and published by the time
  it runs; a bad issue number becomes a warning and a row in the job summary.

### Rehearsing it

Run the workflow manually (Actions → Release → Run workflow) with **notify_dry_run**
checked and **notify_version** set to a past release such as `0.3.0b1`. The job
resolves the same issue list and writes the full plan to the run summary without
posting or closing anything.

The parsing itself lives in `ci/release-issues.py`, which also cuts the release notes.
Run it locally against any version:

```bash
python3 ci/release-issues.py --version 0.3.0 --json    # issues it would notify
python3 ci/release-issues.py --version 0.3.0 --notes   # the release body
```

## Beta / pre-release releases

Betas go through the *exact same flow* — the only difference is the version string. Use
a PEP 440 pre-release suffix: `bN` (beta), `aN` (alpha), or `rcN` (e.g. `0.1.0b1`).
`release.yml` recognizes the suffix and publishes the GitHub release as a
**pre-release**, so HACS offers it only to users who enabled "Show beta versions". Cut
the final `0.1.0` (with its own `## [0.1.0]` changelog section) when ready.

> This integration requires Home Keeper's `triggered` task type (ha-home-keeper#21),
> which shipped in Home Keeper's first stable release, **0.3.0**. The `home-keeper`
> test pin (`requirements-test.txt`) and `ci/fetch-upstreams.sh` `HK_REF` therefore
> pin the stable **`v0.3.0`** tag. When a future Home Keeper stable bumps an API this
> glue uses, repin both to the new tag in the same release PR. (Betas before `0.1.0`
> tracked Home Keeper `main` because no stable yet contained `triggered`.)

## Preview releases (test a PR build without merging)

Sometimes you want to **install and try a PR's build via HACS** before merging it —
without bumping the version or cutting a real release. Add the **`preview-release`**
label to the PR and `preview-release.yml` builds `home_keeper_battery_notes.zip` from
the PR head, stamps a synthetic version (`X.Y.Z.dev<pr>`) into the zip's manifest, and
publishes an **ephemeral GitHub pre-release** with the zip attached. Install it from
HACS: open *Home Keeper Battery Notes* → ⋮ → **Redownload**, enable **Show beta
versions**, and pick `X.Y.Z.dev<pr>` (or download `home_keeper_battery_notes.zip` from
the release and unzip into `config/custom_components/home_keeper_battery_notes/`).

- **Opt-in only** — nothing happens without the label (and only users with write
  access can label).
- **Same-repo PRs only** — fork PRs get no token and are not built this way.
- **Owner approval** — the publish job runs in the `preview-release` GitHub
  Environment; add **Required reviewers** to it (Settings → Environments) to make each
  build wait for an explicit approval.
- **Ephemeral & low-noise** — it's a **pre-release** (`prerelease: true`), so it's
  offered only to users who enabled *Show beta versions*; the `.dev<pr>` version sorts
  *below* the real `X.Y.Z` release so it never nags anyone as an update; it's
  re-published on each push and **deleted automatically when the PR closes**.

## Constraints

- **Never push directly to `main`.** All changes go through PRs.
- **Never create GitHub releases manually** — `release.yml` handles tag, zip, release.
- **Never comment on an issue by hand** to say a fix shipped — `notify-issues` does it
  from a template. See [AGENTS.md](AGENTS.md).
- **`hacs.json` must have `zip_release: true`** with `filename: home_keeper_battery_notes.zip`.
- How to write the CHANGELOG section a release reads — the three-sentence budget, the
  `(Fixes #N)` placement, and how a stable's section rolls up its betas — is in
  [AGENTS.md](AGENTS.md).

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| "CHANGELOG.md has no '## [X.Y.Z]' section (or it is empty)" | Missing changelog entry | Add it in a follow-up PR |
| "'## [X.Y.Z]' section changed, but vX.Y.Z is already a published release" | An entry folded into a shipped section | Bump `manifest.json` to the next beta and open a new section |
| "Tag vX.Y.Z already exists" | Version wasn't bumped | Bump the version in a new PR |
| "version '…' is malformed" | Not `X.Y.Z` or `X.Y.Z{a\|b\|rc}N` | Fix the manifest version |
| HACS install fails / "No valid version found" | Missing zip asset | Check `hacs.json` `zip_release: true` |
