# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Fork of [koreader/koreader](https://github.com/koreader/koreader) (a Lua document viewer for e-ink devices) that adds an **iOS / iPadOS target**, sideloaded via Xcode. The `base/` submodule is repointed to `hezi/koreader-base-ios` (fork of koreader-base with the iOS cross-compile + monolibtic support). Everything not iOS-specific tracks upstream; keep generic changes upstream-compatible.

KOReader is two layers:

- **Frontend (this repo)** — the entire application, written in Lua (LuaJIT): `reader.lua` entry point, `frontend/`, `plugins/`.
- **base/ (submodule)** — koreader-base: C/C++ engines (mupdf, crengine, k2pdfopt, djvulibre), SDL, LuaJIT, and the `ffi/` wrappers the frontend calls. Build system for all targets lives here (`base/Makefile*`), included by the top-level `Makefile` with per-target extras in `make/<target>.mk`.

## Persistent project memory

`.claude/memory/` holds durable, verified findings about this codebase — committed to git so they travel across machines. `INDEX.md` is the index (one line per note).

- **Read**: at the start of a non-trivial task, skim `.claude/memory/INDEX.md` and read the notes touching your area before re-deriving how a subsystem works.
- **Write**: when you verify a non-obvious finding during work (root cause, invariant, cross-file mechanism, gotcha, decision + its why), add or update a note — one topic per file, kebab-case filename, first line `Verified: <date> @ <short-hash>` — and add its one-line entry to `INDEX.md`.
- Don't duplicate what this file or `doc/` already covers; notes hold what those don't. Update or delete stale notes rather than appending contradictions. Prefer this directory over account-local memory for anything project-specific, so the knowledge is shared across machines.

## Toolchain requirements

All dev tooling is bash (≥ 4.0) + GNU make (≥ 4.1) based and runs on **Linux or macOS only** (Windows: use WSL or a VM — see `doc/Building.md`). The **iOS and macOS targets build only on macOS** with full Xcode (not just Command-Line Tools). On macOS, Homebrew GNU tools must precede the BSD ones on PATH — `make: *** missing separator` means you're on BSD make:

```sh
export PATH="$(brew --prefix)/opt/findutils/libexec/gnubin:$(brew --prefix)/opt/gnu-getopt/bin:$(brew --prefix)/opt/make/libexec/gnubin:$(brew --prefix)/opt/util-linux/bin:${PATH}"
```

`./platform/ios/check-prereqs.sh` preflights all of this (also run automatically by the iOS make targets) and prints a single `brew install` for anything missing.

## Common commands

Everything goes through `./kodev` (it auto-fetches submodules when missing):

```sh
./kodev build                  # build the emulator (debug by default)
./kodev run                    # build + run the emulator
./kodev run -s kobo-aura-one   # simulate a device's screen size/DPI
./kodev test                   # all tests (busted specs via meson runner)
./kodev test front             # frontend tests only (spec/unit/)
./kodev test front readerbookmark_spec.lua   # single spec file
./kodev test -l                # list available tests
./kodev check                  # ALL linters: luacheck + shellcheck + custom CI checks
./kodev cov                    # frontend test coverage
./kodev prompt                 # LuaJIT REPL inside KOReader's environment
./kodev wbuilder               # minimal UI sandbox for developing widgets (tools/wbuilder.lua)
./kodev build <target>         # cross-compile: kobo, kindle, android-arm64, ios, …
```

`make static-check` runs luacheck alone. `make po` fetches translations (needed by `./kodev release`, skip with `-i`).

`python tools/event_xref.py` regenerates `doc/events_xref.md` — the committed event-dispatch cross-reference (event name ↔ emit sites ↔ `on*` handler definitions, including dispatcher actions and `key_events`/`ges_events` tables). Consult it instead of grepping for string-composed event dispatch; regenerate after adding or renaming events/handlers (`--check` verifies it is current).

### iOS build (macOS host)

```sh
make TARGET=macos base         # optional: host LuaJIT enables .lua→bytecode precompile (~30% faster cold launch)
make TARGET=ios xcodeproj      # builds base + staging tree, generates KOReader.xcodeproj at repo root
open KOReader.xcodeproj        # set Team under Signing & Capabilities, pick device, ⌘R
```

Headless smoke test (no signing, won't install on device):

```sh
xcodebuild -project KOReader.xcodeproj -scheme KOReader -configuration Debug \
    -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO build
```

If the app builds but crashes at launch with a Lua error, suspect stale artifacts: `rm -rf base/build/arm64-apple-ios14.0 && make TARGET=ios xcodeproj`. Full docs + troubleshooting: `doc/Building_iOS.md`.

## Architecture

- **Entry / boot**: platform launcher execs `reader.lua` (via `setupkoenv.lua` for package paths), which sets up `Device`, `G_reader_settings`/`G_defaults`, then opens `ReaderUI` (a document) or `FileManager`.
- **Event-driven UI**: `frontend/ui/uimanager.lua` is a singleton owning the window stack, repaint scheduling (`setDirty`), and timers (`scheduleIn`). Widgets extend `EventListener`/`WidgetContainer` (`frontend/ui/widget/`); events (`ui/event.lua`) propagate to children first, and a handler returns `true` to consume. `ReaderUI` (`frontend/apps/reader/readerui.lua`) is a container whose feature modules (`frontend/apps/reader/modules/reader*.lua`) communicate by events (see `doc/Events.md`).
- **Device abstraction**: `frontend/device.lua` probes the runtime and loads one of `frontend/device/<platform>/` (kobo, kindle, sdl, android, …), each providing screen/input/power implementations. **iOS is not a separate device class** — it runs the SDL device (`frontend/device/sdl/`) like the desktop emulator, with iOS-specific behavior gated on `os.getenv("KO_IOS") == "1"` (set by the iOS launcher).
- **Documents**: `frontend/document/documentregistry.lua` maps formats to engines — `credocument.lua` (crengine: EPUB/FB2/reflowable) vs `pdfdocument.lua`/`djvudocument.lua` (mupdf/djvulibre, paged) with `koptinterface.lua` for k2pdfopt reflow. Rendering is cached (`cache.lua`, `tilecacheitem.lua`).
- **Plugins**: `plugins/<name>.koplugin/` with `_meta.lua` (metadata) + `main.lua` returning a `WidgetContainer` subclass; discovered by `frontend/pluginloader.lua`. A plugin opts out at load time by returning `{ disabled = true }` (see `iosfilepicker.koplugin` for the pattern).
- **Settings**: `G_reader_settings` (`LuaSettings` over `settings.reader.lua`), `G_defaults` (`defaults.lua`), per-book sidecars via `docsettings.lua` (`.sdr` dirs).
- **i18n**: wrap every user-visible string: `local _ = require("gettext")`, `_("text")`, with `T()` templates for placeholders (`%1`, `%2` — never reorder/alter them in code). Translations live in the `l10n/` submodule.

### iOS port delta (what this fork adds)

- `platform/ios/` — the whole port lives here plus a handful of `KO_IOS`-gated frontend patches:
  - `ios_loader.m`: SDL3 `main` wrapper; chdirs into the bundle's `app/` dir, sets `KO_IOS=1`, `KO_HOME` (sandbox Documents), `SDL_FULLSCREEN=1`, `SDL_TOUCH_MOUSE_EVENTS=0` (avoids double taps), then runs `reader.lua`.
  - `ios_filepicker.m`: `UIDocumentPickerViewController` + security-scoped bookmarks, exposed as C symbols the `iosfilepicker.koplugin` calls over LuaJIT FFI (poll-based, since the picker runs on the UIKit main thread).
  - `project.yml`: xcodegen spec → `KOReader.xcodeproj`. Pre-build phase runs `make TARGET=ios all` (base libs + staging tree); post-build `embed-bundle-payload.sh` rsyncs the staging tree into `KOReader.app/app/`, precompiles `.lua` to bytecode (`precompile-lua.sh`), and re-signs every embedded `.dylib`/`.so` (iOS refuses unsigned Mach-Os at dlopen).
  - The Lua tree is bundled as `app/`, **not** `koreader/` — APFS is case-insensitive and it would collide with the `KOReader` executable.
- `make/ios.mk`: `xcodeproj` + `update` targets, prereq preflight, plugins excluded from the iOS bundle (SSH, autofrontlight, hello, timesync).
- `KO_IOS`-gated frontend patches (search `KO_IOS` to find them all): boot splash in `reader.lua`; full repaint on `SDL_EVENT_DID_ENTER_FOREGROUND` in `frontend/device/sdl/device.lua` (resume invalidates SDL textures); `ReaderRolling:_rerenderInBackground` returns false; coverbrowser's `bookinfomanager.lua` time-slices metadata extraction one file per UIManager tick instead of forking.
- **iOS constraints to respect in any code that can run there**: no `fork()` (`runInSubProcess` runs inline on the main thread), no JIT (LuaJIT is interpreter-only — heavy Lua paths must yield to the UI loop), no `system()` (k2pdfopt reflow is compiled out; plain mupdf PDF rendering works), filesystem confined to the app sandbox (`KO_HOME`).

## Code style & CI checks

`./kodev check` (mirrors CI) fails on, beyond luacheck (`.luacheckrc`) and shellcheck:

- **Tabs in Lua** — 4-space indent everywhere (`.editorconfig`).
- **Unscaled dimensions** — never hardcode pixel values for `padding`/`margin`/`width`/`height`/`radius`/etc.; use `ui/size` presets or `Screen:scaleBySize()`. Escape hatch comment: `-- unscaled_size_check: ignore`.
- **Untagged TODOs** — must be `--- @todo`, `--- @fixme`, or `--- @warning`.

Logging: `local logger = require("logger")`; `logger.dbg(...)` (goes to `crash.log` on devices). Arguments are always evaluated — guard expensive ones behind `dbg.is_on`.
