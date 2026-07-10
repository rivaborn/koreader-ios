# iOS background work — no fork(), time-slice instead

Verified: 2026-07-09 @ a7cf2a98a (from code/diff reading; not exercised on a device)

iOS sandboxing rejects `fork()`. In the iOS base, `runInSubProcess` runs the
task **inline on the main thread** (with a `mkstemp`-backed file replacing the
pipe — see doc/Building_iOS.md). Two frontend sites are patched around this,
both gated on `os.getenv("KO_IOS") == "1"`:

- `ReaderRolling:_rerenderInBackground`
  (frontend/apps/reader/modules/readerrolling.lua) returns `false` on iOS —
  the supported "fork failed" fallback; the rerender happens at the next
  document reload instead. Why: the inline "subprocess" and the parent can't
  run concurrently, so the parent never flips `shared_state` over mmap;
  crengine wedges and later renders crash deep in LuaJIT FFI
  (`lj_cconv_ct_ct`).
- Cover-browser metadata extraction
  (plugins/coverbrowser.koplugin/bookinfomanager.lua): `extractInBackground`
  on iOS doesn't fork — it stores `self._ios_chunk_state = { idx, files }` and
  drives `_iosProcessNextChunk()` via `UIManager:scheduleIn(0, …)`, **one file
  per UIManager tick**, keeping the UI responsive.
  - Lifecycle pairing: `UIManager:preventStandby()` + `Device:enableCPUCores(2)`
    at start; `allowStandby()` + `enableCPUCores(1)` + deferred `cleanUp()`
    when the queue drains.
  - Cancellation: `terminateBackgroundJobs()` simply sets
    `_ios_chunk_state = nil`; the next scheduled tick sees nil and bails.
    `isExtractingInBackground()` checks `_ios_chunk_state` as well as real
    subprocess pids.

Pattern for new work: anything that would fork on other platforms must, on
iOS, either take the documented "fork failed" fallback or time-slice across
UIManager ticks. Check `KO_IOS` at the fork callsite, and keep
preventStandby/allowStandby and enableCPUCores calls paired on every exit
path — including cancellation.
