# Home Keeper — Battery Notes — Claude Code memory

@AGENTS.md

The workflow, changelog rules and release gates live in `AGENTS.md` (imported above).
The release mechanics themselves are in `RELEASE.md`. Read both before pushing.

Two gates are easy to miss:

- **A `(Fixes #N)` in the shipped version's CHANGELOG section is the only thing that
  notifies and closes an issue.** Leave one out and the reporter is never told. Never
  comment on an issue by hand — `release.yml` does it from a template.
- **A PR that adds a user-facing feature must cut the next beta in the same change**
  (`manifest.json` + a matching `## [X.Y.0bN]` CHANGELOG section) and carry the
  `preview-release` label. `test.yml`'s `changelog-release-gap` job fails a PR that
  adds prose to a section whose version already shipped.
