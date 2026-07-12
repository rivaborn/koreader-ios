# Building this fork for Android (WSL2)

Verified: 2026-07-12 @ 24ee448b2 (first APK built end-to-end on ALIENWARESAGAPT)

The fork inherits upstream KOReader's Android target unchanged — `make/android.mk`,
`kodev android-*` targets, stock `platform/android/luajit-launcher` submodule,
`frontend/device/android/`. The fork delta is inert on Android: every frontend patch
is `KO_IOS`-gated, `iosfilepicker.koplugin` self-disables (`{ disabled = true }`),
and `make/ios.mk` is only included for `TARGET=ios`. The one shared surface is
`base/` (`hezi/koreader-base-ios` = upstream koreader-base + additive iOS commits) —
it built Android cleanly; if it ever misbehaves, diff against upstream koreader-base.

## Working recipe (WSL2 Ubuntu 24.04 on Windows)

1. Packages (as root: `wsl -d Ubuntu -u root`):
   `apt install --no-install-recommends autoconf automake build-essential
   ca-certificates cmake curl gcc-multilib gettext git libtool libtool-bin meson
   nasm ninja-build patch pkg-config unzip wget openjdk-17-jdk-headless p7zip-full`
2. **GNU make >= 4.4 is REQUIRED — Ubuntu 24.04's make 4.3 breaks the build** (see
   gotcha below). Build 4.4.1 from source into `/usr/local/bin` (shadows 4.3):
   `wget https://ftp.gnu.org/gnu/make/make-4.4.1.tar.gz && tar xzf ... &&
   ./configure --prefix=/usr/local && make && make install`.
3. Clone INSIDE WSL ext4, never build on `/mnt/c`. Cloning from the Windows checkout
   skips the big download: `git clone /mnt/c/Coding/rivaborn/koreader-ios
   ~/koreader-ios`, then re-point origin to GitHub and **fetch upstream tags**
   (`git fetch https://github.com/koreader/koreader --tags`) — without them VERSION
   is empty (fork-maintenance gotcha) and the APK filename/version string break.
4. Build with a sanitized PATH (Windows PATH entries leak into WSL and can break
   configure scripts): `export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`
   then `./kodev build android-arm64` — auto-downloads NDK r23c + SDK (1.9 GB under
   `base/toolchain/`) and compiles the native engines.
5. `make po` (translations; or pass `-i` to skip), then `./kodev release
   android-arm64` → APK at the repo root, e.g.
   `koreader-android-arm64-v2026.03-76-g24ee448b2_2026-07-11.apk` (29 MB; gradle
   flavor `arm64RocksRelease`, ~2.5 min). Copy out via `\\wsl$\Ubuntu\home\...` and
   sideload (APKs are gitignored).

Wall clock for the whole thing (deps → APK, 12 threads, incl. NDK download): ~1 h.

## Gotcha: make 4.3 jobserver vs koenv.sh (the reason for step 2)

With make 4.3, parallel-jobs tokens pass over inherited **raw fds 3/4**
(`--jobserver-auth=3,4`). koreader-base's external-project wrapper
(`base/thirdparty/cmake_modules/koenv.sh`) runs `exec 3<&1` for its own logging,
clobbering fd 3 — LuaJIT's sub-make then dies with
`make[3]: *** read jobs pipe: Bad file descriptor` at ~55% (first make-driven
thirdparty project under ninja). make 4.4 switched the jobserver to a named fifo,
immune to fd redirection — `base/Makefile.defs` is explicitly 4.4-aware
(`USING_MAKE_4_4_OR_BETTER`). Symptom in the log: `FAILED: .../luajit/stamp/build`
with the jobs-pipe error a screen above it.

## Follow-ups not done yet

- No Android job in `.github/workflows/build.yml` (CI is macOS/iOS only) — add
  `./kodev release android-arm64` on ubuntu-latest if Android becomes a real target.
- APK is debug-keystore-signed by the stock gradle setup — fine for sideloading;
  a Play/F-Droid release would need a real signing config.
