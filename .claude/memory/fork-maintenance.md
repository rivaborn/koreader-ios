# Fork maintenance — relationship to upstream

Verified: 2026-07-09 @ a7cf2a98a

- Last upstream (koreader/koreader) commit in this history: `240f591d3`
  ("MoveToArchive main: load settings when needed only (#15302)"). Everything
  after it is the iOS fork delta — 9 commits, `deea229d1` (ios: wire target
  into kodev) through `a7cf2a98a`, as of the verify date.
- Full fork diff, excluding the submodule pointer:

  ```sh
  git diff 240f591d3..HEAD -- . ':(exclude)base'
  ```

  (~1.7k added lines across 25 files: `platform/ios/*`, `make/ios.mk`,
  `plugins/iosfilepicker.koplugin/`, the KO_IOS-gated patches in `reader.lua`,
  `frontend/device/sdl/device.lua`, `readerrolling.lua`, coverbrowser's
  `bookinfomanager.lua`, plus docs.)
- Remotes: `origin` = rivaborn/koreader-ios (the only configured remote).
  Canonical fork per README = hezi/koreader-ios; true upstream =
  koreader/koreader (add as a remote manually when rebasing onto upstream).
- `base/` submodule → hezi/koreader-base-ios: upstream koreader-base with the
  iOS commits rebased on top (see commit `aae863e9d`). Frontend and base iOS
  changes come in matching pairs — when rebasing either repo, check whether the
  counterpart needs the sibling change.
- **This fork's clones carry no tags** — `git tag` is empty and
  `git describe HEAD` fatals ("No names found"). The top-level `Makefile`
  computes `VERSION := $(shell git describe HEAD)`, so on a fresh clone VERSION
  comes out empty — mainly affecting git-rev stamping and release packaging
  names, not compilation. Fix on any new machine:
  `git fetch --tags https://github.com/koreader/koreader.git`.
