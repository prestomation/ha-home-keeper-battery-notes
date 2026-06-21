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
   3. Skip silently if tag `vX.Y.Z` already exists.
   4. Build `home_keeper_battery_notes.zip` (the HACS asset).
   5. Push tag `vX.Y.Z` and create the GitHub Release with the changelog section as the
      body and the zip attached.

3. **HACS picks it up** via `hacs.json` (`zip_release: true`, `filename:
   home_keeper_battery_notes.zip`).

On a release PR (before merge) the workflow runs as a **dry run** — it validates the
version/changelog and builds the zip but does not tag or publish.

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

## Constraints

- **Never push directly to `main`.** All changes go through PRs.
- **Never create GitHub releases manually** — `release.yml` handles tag, zip, release.
- **`hacs.json` must have `zip_release: true`** with `filename: home_keeper_battery_notes.zip`.

## Troubleshooting

| Problem | Cause | Fix |
|---------|-------|-----|
| "manifest.json is at X.Y.Z but CHANGELOG.md has no '## [X.Y.Z]' section" | Missing changelog entry | Add it in a follow-up PR |
| "Tag vX.Y.Z already exists" | Version wasn't bumped | Bump the version in a new PR |
| "version '…' is malformed" | Not `X.Y.Z` or `X.Y.Z{a\|b\|rc}N` | Fix the manifest version |
| HACS install fails / "No valid version found" | Missing zip asset | Check `hacs.json` `zip_release: true` |
