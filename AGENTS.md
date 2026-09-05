# AGENTS.md — Home Keeper — Battery Notes

This glue follows [Home Keeper's](https://github.com/prestomation/ha-home-keeper/blob/main/AGENTS.md)
workflow. The rules below are the part that governs how a change becomes a release.
Where the two repositories differ, this file wins.

## Workflow

- **Never push directly to `main`.** Always use a feature branch and open a PR.
- Wait for CI (tests, HACS validation, code review) and approval before merging.
- **Always squash merge PRs.**
- **Always run tests locally before pushing.** Never use CI as the test runner.
  - Pure-logic unit tests need only `pip install pytest`: `bash ci/test-unit.sh`.
  - The fuller tiers are `ci/test-integration.sh` (a Home Keeper fake),
    `ci/test-docker.sh` (a real Home Assistant container) and `ci/e2e-up.sh`
    (Playwright).
- **Every PR that changes what the user sees in Home Assistant MUST include current
  screenshots.** Capture them with the harness in `tests/e2e/`, commit the PNGs under
  `docs/images/`, and embed them in the PR body with an HTML
  `<img src="…" alt="…" width="820">` tag whose `src` is a
  `raw.githubusercontent.com` URL pinned to the commit that added them. A markdown
  `![](…)` image can be silently wrapped in backticks by the API that sets the body,
  so use the HTML tag and re-read the body afterwards to confirm the URLs survived.
- **Never comment on a GitHub issue.** Issues are where users talk to the maintainer.
  Findings, analysis and status belong in the PR that carries the work, or in the
  reply to whoever asked. A PR that fixes an issue links it with `Fixes #N`, and the
  release that ships it closes the issue — that is the only signal an issue needs.
  This does not restrict PR comments. It also does not cover repo automation:
  `release.yml`'s `notify-issues` job posts from a fixed template as the mechanical
  consequence of a release, which is not an agent answering on the maintainer's
  behalf.

## CHANGELOG

- **Update `CHANGELOG.md` for every user-facing change**, before tagging a release.
  Developer-only changes (CI config, this file) need no entry.
- **Keep every bullet to three sentences at most.** A bold lead naming the change,
  then what a user notices, then a caveat or `(Fixes #N)` if one is needed. That is
  the whole budget, and the bold lead counts as the first sentence. Cut the worked
  example, the before-and-after story and the inventory of every new option — that
  detail belongs in `README.md` or the PR body. One bullet per change, never a second
  paragraph.
- **`(Fixes #N)` must land in the bullet's first paragraph**, because
  `ci/release-issues.py` quotes the bullet it first appears in.
- **Credit an outside contributor** in the bullet for their change: end it with
  `(Thanks @user!)`, after `(Fixes #N)` if the bullet has one. The credit does not
  count against the three-sentence budget, and it stays in the CHANGELOG — it never
  reaches the issue comment.
- **A stable release's `## [X.Y.Z]` notes describe what changed since the last
  _stable_ release, not since its betas.** When you cut `X.Y.Z` from an `X.Y.ZbN`
  line, write the section for someone upgrading from the previous stable version. A
  feature introduced over the betas is **Added**, even if a later beta changed how it
  worked mid-stream; do not carry beta-to-beta framing into the stable section.
  Include a `### Fixed` section listing every issue the commits since the last stable
  fixed — check the git log for `(Fixes #N)` references and write each one in.

## Issues

- **A `(Fixes #N)` in a version's CHANGELOG section is what notifies and closes the
  issue.** `release.yml`'s `notify-issues` job reads the shipped version's section,
  comments on every issue it references, and closes it once the version is stable. An
  issue left out of the section is never told and never closes, so the section is the
  release's issue list, not decoration.
- **Only closing keywords count.** Write `(Fixes #N)`. `(Related to #N)` and a bare
  `(#N)` are ignored on purpose, because `(#N)` is also the PR number squash-merge
  appends and the two cannot be told apart. The job posts a CI warning naming any
  issue that a shipped commit referenced but the section forgot.
- **Keep linking issues from a PR with `Fixes #N`.** Turn closing-on-merge off for
  this repository so the keyword links the PR to the issue — filling in the issue's
  **Development** panel — without closing it. The issue then stays open until the fix
  reaches users, and `notify-issues` closes it on the release that carries it, so the
  reporter's "closed" notification names a version they can install.

## Versions and releases

The mechanics are in [RELEASE.md](RELEASE.md). The rules an agent has to apply:

- **Beta versioning — always use the next release number.** After a stable `X.Y.0`
  ships, bump `manifest.json` to `X.(Y+1).0b1` on `main` and rename the top CHANGELOG
  section to match. Beta iterations go `b1 → b2 → …` until the stable `X.(Y+1).0` is
  cut. **Never use `X.Y.0bN` after `X.Y.0` has shipped** — PEP 440 sorts those below
  the stable version, so HACS would offer the stable as an "upgrade" to anyone on the
  beta.
- **Always cut a beta release for a new feature.** A PR that adds a user-facing
  feature bumps `manifest.json` to the next `bN` in the same change, with a matching
  `## [X.Y.0bN]` CHANGELOG section, so the work reaches beta testers through HACS
  instead of waiting on the floor. If the top CHANGELOG section is an already-released
  beta, open the next `bN`; if it is an unreleased beta still being iterated, fold the
  feature into it. `test.yml`'s `changelog-release-gap` job fails a PR that gets this
  wrong. Bug-fix-only and developer-only PRs need no fresh beta.
- **Always add the `preview-release` label to a new-feature PR**, so
  `preview-release.yml` publishes an installable ephemeral pre-release
  (`X.Y.Z.dev<pr>`) from the PR head and testers can try the feature before merge.

## Home Keeper pin

This glue needs Home Keeper's `triggered` task type. The `home-keeper` test pin in
`requirements-test.txt` and `HK_REF` in `ci/fetch-upstreams.sh` point at a stable Home
Keeper tag. When a future Home Keeper stable changes an API this glue uses, repin both
in the same release PR.
