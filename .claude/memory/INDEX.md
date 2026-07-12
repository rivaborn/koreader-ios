# Project memory — index

Durable, verified findings about this codebase, committed so they travel across
machines. One note per topic; each starts with a `Verified: <date> @ <commit>`
line so staleness is detectable. Conventions live in CLAUDE.md § "Persistent
project memory".

- [Fork maintenance](fork-maintenance.md) — upstream merge-base `240f591d3`, the iOS commit series, the fork-diff command, missing-tags/VERSION gotcha
- [iOS background work](ios-background-work.md) — no-fork constraint: the KO_IOS gates and the UIManager time-slicing pattern to reuse
- [Android build](android-build.md) — WSL2 recipe for the fork's APK: package list, make>=4.4 REQUIRED (4.3 jobserver vs koenv.sh fd 3), tags/VERSION fix, kodev commands; first APK 2026-07-12
- [Architecture-analysis toolkit](architecture-analysis-toolkit.md) — full runbook: extraction + frontend pipeline on any machine (.env template, commands, GPU-idle-gate/RAM/orphan/refs gotchas); full run completed 2026-07-11 (19h23m, 0 failures, 348 docs/pass)
