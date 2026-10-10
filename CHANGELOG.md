# Changelog

All notable changes to SDRduo Display are documented here.

## v0.20 — checks GitHub for a newer release

When the page opens, the browser makes one small request to GitHub's
"latest release" record for this project (after about 4 seconds) and shows
an amber **UPDATE vX.Y AVAILABLE** badge in the top bar if a newer release
exists. Nothing is downloaded or installed; it stays silent when offline.
The **check for updates** tick box in the top bar turns it off (remembered
in the browser). No receiver changes.

## v0.19 — version number shown in the header

The top bar and the browser tab now show the version (`v0.19`), so it is
clear which build is running. The number is written in `static/index.html`
(two places); bump it there, in `README.md` and in `Install/build.ps1` with
each release. No receiver changes.

## v0.18 — gain readout showed what we asked for, not what the tuner is doing

Found while investigating whether the hardware AGC was hurting SNR (it
isn't — measured 0.5dB between AGC and best manual gain, stable across
2s/12s/32s settles, so no hunting). The real defect was next to it: the
Gain reduction readout reports `receiver.grdb`, the last value the app
*commanded*, which goes stale the moment the AGC moves. Caught it
showing 30 while the tuner was actually running 47.

The value is available — the API writes the AGC's current gain back into
the tuner params struct — but nothing read it: `_on_event` handles only
`PowerOverloadChange` and `DeviceRemoved`.

- `backend/receiver.py`: new `current_grdb` property reading the live
  struct, falling back to the commanded value when the device isn't open.
- `backend/server.py`: state now carries `grdbActual` alongside `grdb`.
- `static/app.js`: the readout and slider follow `grdbActual` always, with
  a "(AGC)" marker saying who is driving it.

Note the marker says *who is driving*, not *which number to show*. A
first attempt displayed the commanded value whenever AGC was off, which
reintroduced the same bug in a smaller form: switching AGC off leaves
the gain wherever the AGC last put it rather than snapping back, so
"AGC off" showed 30 next to a slider at 47. Caught in a browser, not in
the unit-level check — the server-side numbers looked right either way.

Verified in a real browser end to end: AGC on `47 (AGC)`/47, AGC off
`47`/47, manual drag `35`/35, AGC back on `45 (AGC)`/45 — label and
slider agree at every step, and the number tracks the hardware rather
than our memory of it.

## v0.17 — AF AGC, so audio level stops tracking signal strength

Reported after the v0.16 bench test: audio too quiet with the PC volume
at full and the app's Volume slider at maximum. Measured, and the
complaint was well founded — nothing in this chain set the audio level
at all. AM and SSB come out directly proportional to RF amplitude, and
the browser's Volume control only reaches 2x:

| mode | RF amp | audio before |
| --- | --- | --- |
| WFM | any | -14.0 dBFS (constant — it's phase-derived) |
| AM | 0.1 | -23.0 dBFS |
| AM | 0.02 | -37.0 dBFS |
| USB | 0.005 | -43.0 dBFS |

- `backend/dsp.py`: new `_AudioAgc`, applied to every mode's audio as the
  last step of `process()`. Everything now settles at about -9.2 dBFS
  regardless of signal strength: WFM +4.7dB, AM at 0.02 +27.8dB, USB at
  0.005 +33.8dB. Gain is reset in `set_mode()`, since a gain settled on a
  weak SSB signal would otherwise arrive at WFM as an instant blast.

Design notes, because the naive version of this is worse than none:

- **It cannot clip.** Each block's peak is known before its gain is
  applied, so the gain is capped at `ceiling / peak`. Measured 0.000%
  clipped everywhere tested, including an AM signal stepping 0.02 -> 0.5
  (28dB) mid-stream, where the worst peak reached 0.777.
- **Gain reductions are flat across their block, not ramped into.**
  Ramping into a reduction leaves the start of the block multiplied by
  the old, too-large gain -- and the peak that triggered the limiting is
  often right there. Flat is what makes "cannot clip" true rather than
  nearly true.
- **Silence is held, not chased.** Below `floor_rms` the gain is frozen.
  Otherwise every gap between words, and every squelched moment, would
  wind the noise floor up to full scale and roar the instant a signal
  returned. Dead-air output on AM/SSB measures 0.00000 rms.
- **Release is slow (1.5s), attack immediate.** A weak signal appearing
  after dead air reaches full level over ~2-3s rather than pumping
  between syllables.

Block stats are computed for a whole chunk at once and only the gain
recursion is sequential, so the cost is 15.0% -> 15.8% of one core.
Verified across all six modes on ragged chunks from 200 to 65536 samples:
no exceptions, no clipping, no sample-rate drift, and the v0.16
decimation fix still measures identically chunked vs single-call.

One consequence worth knowing: on FM/WFM, *unsquelched* dead-air hiss is
now normalised up to the same target as programme audio, so it is louder
than it used to be. That is what an AGC does with noise -- use the
squelch.

## v0.16 — decimate on a continuous grid across chunk boundaries

Every decimation in the demod chain was a plain `x[::factor]`, which
restarts its sampling grid at index 0 on every call. That is only
correct while every chunk length is an exact multiple of the factor,
and `server.py` reads *up to* `max_n` samples from the ring, so in
general it isn't: WFM's final /5 stage got 2048-sample chunks and
emitted 410 audio samples where 409.6 was right, advancing the time
base ~32us at every chunk boundary, ~30 times a second.

Found in HackRF SDR TRX (whose demod chain was ported from this one,
so the bug went the other way for once) and ported back. Measured here
the same way — the same IQ demodulated in 65536-sample chunks, as the
server actually does it, versus a single call:

| mode | tone | chunked, before | chunked, after |
| --- | --- | --- | --- |
| WFM | 4kHz | -39.1 dB | **-18.4 dB** |
| AM | 3.5kHz | -33.7 dB | **-19.7 dB** |
| FM | 1kHz | -5.5 dB | -5.4 dB |

Both fixed figures now match the single-call result exactly, so WFM was
losing 20.7dB of treble and AM 14dB. FM is unaffected: its audio is only
3kHz wide, so it had almost nothing above the smear to lose -- which is
also why this survived so long unnoticed, along with the fact that a
steady single tone has no phase reference to violate and so passes a
response test that real programme material fails.

- `backend/dsp.py`: new `_decimate(x, factor, phase)` returns the phase
  the next chunk must start at, and `Demodulator._dec()` threads that
  state per stage through `self._dec_phase` -- the same shape as
  `_stream()` already threading filter delay lines. Applied to all six
  decimation points (stage1, stage2 main/FM, and WFM's IF/A/B).

Verified: chunked output now matches single-call output exactly; audio
sample-rate drift over a run drops from ~0.1% to ~0.006%; and no mode
raises on randomly-sized chunks between 1000 and 65536 samples.

Installer rebuilt (`Install/build.ps1`). While syncing `Install/payload\`
for that, its `static/index.html` turned out to have drifted from the
project's own -- the Volume control sat in a different position, an
8-line reorder. Resolved in favour of `static/index.html`, since the
payload is documented as a manual mirror of the project source.

## v0.15 — startup race: browser opening before the RSPduo was actually claimed

- `backend/receiver.py`: `open()` now retries `sdrplay_api_GetDevices()`
  for up to 8s (0.5s poll interval) instead of raising "No RSPduo found"
  on the first empty result. Device enumeration can legitimately lag a
  few seconds behind the SDRplay API Service itself starting, or behind
  the RSPduo being plugged in -- reported for real on a fresh install:
  launching via the Desktop shortcut failed immediately with "won't
  connect" in the browser, but a manual relaunch moments later (nothing
  else changed) worked fine. Every other `sdrplay_api_*` failure still
  raises immediately via `api.check()`, unchanged -- only the "device not
  enumerated yet" case retries.
- `run.py`: `webbrowser.open()` used to fire before `uvicorn.run()` had
  even started, racing the FastAPI `lifespan` handler's `receiver.open()`
  call -- which, per the above, can legitimately take several seconds.
  uvicorn doesn't bind its listening socket until `lifespan` startup
  completes (confirmed directly: a startup failure never produces a
  "Uvicorn running on ..." line and the port never opens), so a plain
  connect-retry loop is a reliable readiness check with no fixed guess of
  how long to wait. The browser tab now opens only once port 8080 is
  actually accepting connections (30s cap, then opens anyway so the
  console error is at least visible behind it, same failure mode as
  before this fix for a genuinely absent device).
- Verified against the live setup on this machine: with the RSPduo not
  connected, `receiver.open()` now visibly retries enumeration for the
  full 8s window (12-13 attempts) before raising a clear, attempt-counted
  RuntimeError, instead of failing instantly.
- `Install/payload/` re-synced, `Install/setup.exe` rebuilt.

## v0.14 — Exit button for a clean shutdown

- `backend/server.py`: new `POST /api/shutdown` -- explicitly calls
  `receiver.close()` (the SDRplay API release: `Uninit`/`ReleaseDevice`/
  `Close`) on its own thread, then hard-exits the process
  (`os._exit(0)`), rather than trying to simulate Ctrl+C via a Windows
  console-signal trick (`CTRL_C_EVENT` has real caveats depending on how/
  whether a console is actually attached to the process -- calling the
  same cleanup code directly sidesteps that entirely and was more
  reliable to reason about). This is the same release step Ctrl+C
  already triggered via the FastAPI lifespan handler -- what changed is
  giving the browser UI a way to trigger it too, without the user needing
  to find the right console window. Confirmed the difference directly:
  after a `Stop-Process -Force` kill (skips this), the next launch failed
  with "No RSPduo found" for several seconds; after this endpoint's
  clean shutdown, an immediate relaunch (no extra wait at all) found the
  device right away.
- `static/index.html`, `static/app.css`, `static/app.js`: new **Exit**
  button (top-right, red-tinted to read as a stop/significant action, not
  routine). Confirms before doing anything (`confirm()`) -- this ends the
  session, no undo. On confirm: stops any playing audio immediately,
  marks the shutdown as intentional so the WebSocket's auto-reconnect
  loop doesn't spend the rest of the tab's life quietly retrying a server
  that was stopped on purpose, POSTs to `/api/shutdown`, and shows a
  full-screen "Server stopped" overlay so it's unambiguous the action
  completed. `#shutdown-overlay.hidden` needed its own ID-specific rule
  to actually win over `#shutdown-overlay`'s own `display: flex` --
  same ID-vs-class specificity trap `#keypad-overlay` hit earlier this
  project, applied proactively here instead of getting bitten by it again.

## v0.13 — automatic GR/LNA re-sync on band crossings

- `backend/receiver.py`: `set_frequency()` now detects a genuine band
  crossing (HF/VHF/UHF, via a new `_gain_zone()` classifier -- 30MHz and
  300MHz partition points, chosen conservatively to bracket wherever the
  RSPduo's own internal front-end switches actually are, not a precise
  claim about that threshold) and, when AGC is on, resets GR/LNA to a
  safe starting point (`DEFAULT_GRDB`/LNA state 0 -- always a valid table
  index, unlike whatever state happened to be active before) for AGC to
  re-adjust from. This is what used to require manually hunting for a
  working GR/LNA combination by hand every time you crossed from HF into
  VHF broadcast or back (bands.py's band buttons only ever set
  frequency+mode, never gain) -- and was a real contributor to earlier
  "weak signal"/overload confusion this session, since a GR/LNA combo
  that's sensible on quiet HF can be badly wrong for a strong local VHF
  station. Left alone in manual gain mode (AGC off) -- overriding a
  deliberately-chosen GR/LNA there would fight the user, not help them.
  `_lock` changed `Lock` -> `RLock` since `set_frequency()` now calls
  `set_gain()` internally, reusing its existing rollback-safe device
  update rather than duplicating it. Verified against the live receiver
  both ways: HF (14.22MHz, GR40/LNA4) -> VHF (100.1MHz FM broadcast)
  correctly reset to GR40/LNA0; a manually-set LNA state 2 then survived
  an in-zone-only retune (FM broadcast -> 2m) completely untouched.

## v0.12 — standalone installer

- `Install/`: new self-contained `setup.exe` for installing on another
  computer -- a single file, nothing else needs to ship alongside it (the
  app source is embedded in it as a base64-encoded zip). Built with
  `ps2exe` (a genuine standalone .exe, not a script-with-a-wrapper) after
  `iexpress` -- the built-in Windows self-extractor packager, the first
  approach tried -- turned out to fail silently in this non-interactive
  build environment, apparently needing a real interactive desktop
  session it didn't have here. On run: checks for Python 3 (offers a
  `winget` install if missing), copies the app to
  `%LOCALAPPDATA%\SDRduoDisplay` by default (no admin rights needed),
  creates a venv and installs dependencies, and adds Desktop/Start Menu
  shortcuts. Does NOT bundle or install the SDRplay API driver -- that's
  the vendor's own USB driver for the RSPduo, not something this
  installer can legitimately redistribute; the final message points to
  https://www.sdrplay.com/software/ instead. Hit two real bugs while
  building it, both fixed: (1) ps2exe's compiled `Read-Host` throws
  instead of returning an empty string when stdin isn't a real
  interactive console, which crashed automated test runs -- wrapped every
  prompt in a `Read-HostSafe` helper that falls back to the stated
  default; (2) `$ErrorActionPreference = "Stop"` treated ANY stderr
  output from external tools as a fatal error, including venv's harmless
  "moved due to redirects or junctions" notice (common under
  `%LOCALAPPDATA%`), aborting a fully-working install -- switched to
  `"Continue"` with explicit `$LASTEXITCODE`/output-file checks after
  each external command instead. Verified with a full end-to-end run on
  a clean target directory: Python found, dependencies installed,
  shortcuts created, exit code 0; rebuilt via `build.ps1` a second time
  to confirm the generate-from-template pipeline reproduces byte-for-byte
  (93,184 bytes both times).

## v0.11 — zero-IF DC-offset causing AM "Donald Duck" distortion exactly on-frequency

- `backend/receiver.py`: root cause of AM audio garbling to "Donald Duck"
  distortion, always exactly on-frequency, clearing up when manually
  detuned a few kHz off-carrier -- this is a zero-IF receiver tuned by
  moving the hardware LO to exactly the requested frequency, so the wanted
  signal and the hardware's own DC-offset/LO-leakage artifact (which sits
  at 0 Hz regardless of tuning) always land on the same spot. An AM
  carrier is itself indistinguishable from "DC" in complex baseband, so no
  downstream filter could separate the two once they'd already collided.
  Fixed by never tuning the hardware LO to the frequency of interest: it's
  now offset +150kHz from every request, and every batch of samples is
  digitally shifted back down by the same amount before it reaches the
  ring buffer -- implemented as a phase-continuous NCO (150kHz/2Msps
  reduces to an exact 3/40 sample-count period, so the correction has zero
  floating-point phase drift over any run length; verified numerically
  with a two-chunk phase-continuity test). `receiver.frequency_hz` (what
  every other module reads) stays the *requested* value throughout, so
  this is fully transparent to `dsp.py`/`server.py`/the frontend -- none
  needed to change. Verified against the live receiver: 774kHz AM
  (previously distorted exactly on-frequency) now clean.

## v0.10 — unit-aware frequency entry, span/step persistence

- `static/index.html`, `static/app.css`, `static/app.js`: root cause of a
  recurring, session-derailing mistuning bug -- the keypad took raw Hz
  digits with no unit indication, so typing a station's dial frequency
  (e.g. "108898" meaning 108.898 MHz) without the full 9-digit Hz count
  silently tuned 1000x low into another band entirely, with no error and
  no visual cue (confirmed happening twice: 108898 -> 108.898kHz, and
  separately 109932/120011 during a click-to-tune investigation). Keypad
  now defaults to MHz entry with an explicit MHz/kHz toggle and a
  decimal-point key, plus a live "= X.XXXXXX MHz" preview line so the
  actual result is obvious before committing (verified: typing "108898"
  now visibly shows "= 0.108898 MHz" instead of tuning there silently).
- `static/app.js`: span and step selection used to silently reset to
  their 2M/1k defaults on every page reload (only memory presets were
  persisted) -- with no visual cue that a prior span selection hadn't
  survived a refresh, a click near a peak could jump far more than
  expected (reported: "10kHz click jumps 10MHz", traced to span having
  silently reverted to 2M). Both now persist to `localStorage`, same
  mechanism as memory presets.

## v0.9 — audio delivery robustness (broadcaster blocking, stale scheduled audio on retune)

- `backend/server.py`: `frame_broadcaster()`/`state_broadcaster()` sent to
  each connected client sequentially with an un-timeout-bounded
  `await ws.send_bytes()`/`send_text()` -- a single stale/half-dead
  connection (accumulated from repeated testing) could stall the shared
  frame queue indefinitely, starving every other client, including the
  real listening session. Now sends to all clients concurrently via
  `asyncio.gather`, each with a 1s timeout; a client that doesn't respond
  in time is dropped instead of blocking the rest.
- `static/app.js`: retuning didn't flush already-scheduled Web Audio
  buffers the way the mute button already did -- audio already queued
  from the *old* frequency (up to 300ms ahead) kept playing out on its own
  timeline while the new frequency's audio got scheduled on top of it, so
  both were briefly audible together. `send()` now flushes scheduled
  sources (`stopAllScheduled()`) and resets the playback clock whenever a
  `set_freq` command goes out, for every tuning path (click, scroll,
  keypad, memory recall) since they all funnel through it.
- `backend/receiver.py`: `set_gain()`'s existing rollback-on-rejection
  only restored the *software's* record of the gain state, not the
  device's actual internal state -- a rejected `Update(Gr)` call could
  leave hardware and software silently out of sync, making every
  subsequent gain command fail too, for a reason no longer visible in
  `self.grdb`/`self.lna_state` (confirmed in logs: LNA states 7/8/9/8/7
  rejected back-to-back). Rollback now re-pushes the restored values to
  the device too, best-effort.

## v0.8 — WFM (broadcast FM) mode, streaming filter state

- `backend/dsp.py`: new **WFM** mode alongside FM. Ham NFM (+-5kHz
  deviation, ~16kHz channel) and broadcast FM (+-75kHz deviation, ~180kHz
  channel) are different signals needing different sample-rate chains --
  FM's own first-stage filter only preserves +-45kHz, and its
  post-discriminator audio filter assumes a +-5kHz reference, so running a
  broadcast signal through it clipped/distorted badly. WFM gets its own
  parallel chain: gentler first-stage decimation (x4 not x20) to preserve
  the full Carson bandwidth, a +-75kHz deviation reference, and a steep
  Kaiser-window audio filter (~60dB down by 19kHz, up from a 63-tap filter
  that only managed ~12dB) to reject the 19kHz stereo pilot and 38kHz L-R
  subcarrier before they could alias into the audible 9.5-23kHz range as a
  garbled "second signal" under the real program audio. Verified against
  synthetic 100%/20% modulation-depth test tones (99.6%/20.0% of full
  scale, exactly proportional) and against the live receiver.
- `backend/dsp.py`: FM's own channel selectivity and audio-bandwidth fixes
  -- a dedicated +-8kHz pre-discriminator filter (previously the
  discriminator ran on the full +-45kHz shared anti-alias filter with no
  channel selectivity at all, so adjacent-channel noise/carriers got
  demodulated right along with the wanted signal) and a dedicated ~3kHz
  post-discriminator audio filter (previously shared the generic 5.5kHz
  AM/SSB filter, letting the loudest part of an FM discriminator's noise
  triangle straight through). Plus a DC-blocker on the discriminator
  output for residual tuning-offset bias, matching the existing AM design.
- `backend/dsp.py`: every FIR filter's delay-line state (and the FM/WFM
  discriminator's cross-chunk sample) is now threaded across `process()`
  calls instead of reset to zero each call. The reset produced a real
  transient at the start of every ~40ms chunk that the discriminator
  turned into an audible click, on a steady chunk-rate rhythm -- a
  "machine gun" sound, worse than the "faint clicks... acceptable for v1"
  the original decimation design had anticipated, and it got substantially
  louder once WFM's extra cascaded filter stages were added. Verified via
  an automated multi-chunk test across all six modes plus continuous live
  testing (8+ seconds, WFM against a real broadcast signal, no artifacts).

## v0.7 — display/UI polish

- `static/app.css`: keypad-not-closing recurred with a *different* root
  cause than v0.6's stale-cache fix -- `#keypad-overlay { display: flex }`
  (an ID selector) always beat `.hidden { display: none }` (a class
  selector) regardless of source order, so `closeKeypad()`'s
  `classList.add("hidden")` was applying correctly but never actually
  hiding anything. Fixed with a `#keypad-overlay.hidden` rule specific
  enough to win.
- `static/index.html`, `static/app.css`: band/span/step/mem collapsed from
  four stacked rows to two (band+step, span+mem, each split left/right via
  a new `.dual-row` wrapper) to give the spectrum/waterfall more vertical
  space.
- `static/app.js`: waterfall rows render at native FFT-bin width then get
  stretched to the full canvas width -- default canvas image smoothing was
  bilinear-blurring that stretch, burying narrow/weak signals into the
  noise floor. `imageSmoothingEnabled = false` (reapplied after every
  resize, since resizing a canvas's width/height attribute silently resets
  its 2D context state). Spectrum trace line width was scaling with the
  canvas's *physical* pixel width (`w/800`), rendering 1.5-2.5px+ thick and
  anti-aliased on any display wider than 800px physical pixels, worse on
  the 150%-scaled laptop display -- fixed to a flat 1 device pixel.
- `static/app.js`: S-meter now shows ham-convention S-units (S0-S9,
  S9+NNdB, clamped at S9+60dB) instead of raw dBFS, with the raw value
  kept as a hover tooltip. Uncalibrated (S9 anchored at a placeholder
  -73dBFS, mirroring the classic S9=-73dBm convention) since this SDR's
  dBFS has no fixed relationship to true RF level -- it shifts with gain
  reduction/LNA state.
- `static/app.js`: volume slider max raised 100 -> 200 (unity gain still
  at 100, so existing settings are unaffected) -- the Web Audio gain node
  already supported gain > 1.0, the UI just didn't expose it.
- `static/app.css`: skeuomorphic panel-chrome pass across buttons, panels,
  sliders, and the frequency/keypad displays -- raised glossy gradients
  with a top-highlight/bottom-shade/drop-shadow on buttons (pressed-in on
  click, glowing "lit" gradient when active/selected), sunken-groove
  treatment on recessed elements (frequency readout, S-meter track,
  keypad display), and a recessed bezel around the spectrum/waterfall via
  the `main` container's own padding (deliberately not touching the
  canvases themselves, to leave the crispness fix above untouched).

## v0.6 — fix keypad not closing, memory/preset slots

- `backend/server.py`: root cause of "keypad doesn't close on Enter or on
  clicking elsewhere" -- the browser had been silently reusing a stale
  cached copy of `app.css` (no request logged at all, not even a
  conditional check), predating the keypad CSS entirely. Without that
  CSS, the overlay never became a real fullscreen modal, so outside
  clicks landed on ordinary page elements instead of the overlay's own
  backdrop. Added a `no-store` `Cache-Control` middleware on `/` and
  `/static/*` so this can't recur -- confirmed via `document.styleSheets`
  rule counts before/after (stuck at 36 rules/missing the keypad block
  before the fix, 55 rules/present after).
- `static/app.js`: also hardened the Enter-key path independently of the
  cache bug -- a clicked keypad `<button>` keeps keyboard focus
  afterward, and a native browser behavior re-activates (re-clicks) a
  focused button on Enter. Now every keypad button is explicitly
  `.blur()`-ed right after its click handler runs, so Enter always goes
  through our own commit handler only. Verified: `document.activeElement`
  is confirmed no longer the clicked button afterward.
- `static/index.html`, `static/app.css`, `static/app.js`: new memory/
  preset row (M1-M8) below STEP. Click "Save..." then a slot to store the
  current frequency + mode into it; right-click a slot to overwrite it
  directly without the arm step. Click a filled slot (showing its
  frequency in MHz) to recall -- replays `set_freq`/`set_mode`, so it
  goes through the exact same path as band buttons. Pure client-side
  (`localStorage`, like span/step/floor/ceiling), since it's just
  replaying existing commands -- no server-side storage needed. Verified
  save, recall, right-click overwrite, and persistence across a reload
  against the live receiver.

## v0.5 — on-screen keypad for frequency entry

- `static/index.html`, `static/app.css`, `static/app.js`: clicking the
  frequency display now opens an on-screen numeric keypad (modal overlay)
  instead of editing the field directly -- `#freq-input` is now
  `readonly`. Keypad: digits 0-9, Clear, backspace, Cancel, Enter,
  formatted with the same thousands-separator display as the main
  readout as you type. Backdrop click, Escape, and Cancel all discard
  without changing the frequency; Enter (button or key) commits via the
  same `set_freq` command the click-to-tune/step controls already use.
  Physical-keyboard digit/Backspace/Enter/Escape also work while the
  keypad is open, for anyone who'd rather type than click. Verified
  against the live receiver: every path (mouse and keyboard, commit and
  cancel) round-trips correctly.

## v0.4.1 — fix audio not actually stopping on mute

- `static/app.js`: `playAudio()`'s scheduling had no cap on how far ahead
  of real time it could queue -- `nextStartTime` only self-corrected when
  it fell *behind*, never when it drifted *ahead*. Under bursty delivery
  (network jitter, tab backgrounding) this could build an unbounded
  backlog of already-scheduled `AudioBufferSourceNode`s; `audioCtx
  .suspend()` alone doesn't retroactively cancel those, so a large enough
  backlog kept audibly playing out after clicking "disable" (reported:
  noticeable on 70cm). Fixed two ways: (1) `playAudio()` now drops
  incoming audio rather than scheduling it if the backlog already reaches
  300ms ahead of real time, so the backlog stays small; (2) every
  scheduled source is tracked and explicitly `.stop()`-ed on mute, so
  disabling is now a hard, immediate stop instead of relying on
  `suspend()` to silence whatever had already queued up. Verified against
  live traffic: confirmed 7 sources pending right at the new 300ms cap,
  and all 7 explicitly stopped on the next click.

## v0.4 — audio mute toggle fix, squelch

- `static/app.js`: the "Enable Audio" button had no path back to off at
  all -- fixed to a proper toggle. First attempt at the fix read
  `audioCtx.state` synchronously right after an unawaited `resume()`/
  `suspend()` call to decide direction; since that state transition is
  asynchronous, a second click shortly after the first could read stale
  state and get the toggle stuck back on (reproduced directly: two clicks
  in quick succession both landed on "Mute Audio"). Fixed by tracking our
  own `audioEnabled` intent flag instead of relying on the browser's async
  `audioCtx.state`, both for the button and for `playAudio()`'s gate.
- `backend/server.py`, `static/index.html`, `static/app.js`: new squelch
  control -- enable checkbox + threshold slider (-120..-20 dBFS). When
  enabled and the current signal (the same center-frequency reading the
  S-meter uses) is below threshold, `demod_worker` zeroes the PCM before
  it goes out (silence, not a dropped frame, so the browser's audio
  scheduler stays glitch-free) rather than filtering client-side. A
  "SQUELCHED" badge lights up next to the OVERLOAD badge when it's
  actively gating. Especially useful on FM, where there's no SSB/CW
  passband filter to reject inter-station hiss.

## v0.3.1 — floor/ceiling contrast controls, step-row alignment fix

- `static/app.css`: `#step-row` was missing from the `#band-row, #span-row`
  padding rule, so its "STEP" label sat flush against the left edge
  instead of aligning with "SPAN"/"BAND" above it. Added.
- `static/index.html`, `static/app.js`: new **Floor**/**Ceiling** sliders
  in the footer (-140..-40 dB / -80..0 dB, 10 dB minimum gap enforced
  both ways). They control the shared dB range used for *both* the
  spectrum trace's vertical scale and the waterfall's color mapping --
  narrowing the range increases display contrast/sensitivity so weaker
  signals stand out against the noise floor, without touching RF gain.
  Pure client-side display state (unlike frequency/mode/gain, these don't
  touch the server at all, so there's no cross-tab shared-state concern
  for them).

## v0.3 — selectable tuning step

- `static/index.html`, `static/app.css`: new step-select row
  (10Hz/100Hz/500Hz/1k/5k/10k) below the span row.
- `static/app.js`: mouse-wheel tuning on the spectrum/waterfall now uses
  the selected step, and always snaps the *current* frequency to the
  nearest multiple of that step before moving one step further -- so an
  off-grid frequency (e.g. from click-tuning) lands back on a clean
  boundary the next time you scroll. Click-to-tune is unchanged (still
  exact, unrounded) since it's for jumping near a signal, not fine
  stepping. Applies to both the spectrum and waterfall canvases.

## v0.2.1 — spectrum trace averaging

- `backend/dsp.py`: `SpectrumEngine` now keeps an exponential moving
  average of per-bin *power* (linear domain, alpha=0.25) across frames
  instead of converting each single FFT snapshot straight to dB. A single
  FFT snapshot is a very noisy power estimate regardless of resolution
  (chi-squared distributed, ~5-6 dB of pure statistical noise per bin) --
  that's what was making zoomed-in spans look useless and the waterfall
  look like undifferentiated blur, not a sample-rate/resolution
  limitation. This is the same "video/trace averaging" a real spectrum
  analyzer applies. Verified via a raw WebSocket client: per-bin std dev
  across consecutive frames dropped to ~1.2 dB.

## v0.2 — selectable spectrum span + frequency axis

- `backend/dsp.py`: `FFT_SIZE` raised 2048 -> 16384 (~122 Hz/bin instead of
  ~976 Hz/bin) so a narrow zoomed span still has enough resolution to
  distinguish individual signals. The backend always computes/sends the
  full-bandwidth spectrum; span is a pure display-side crop, no wire
  protocol change.
- `static/index.html`, `static/app.css`: new span-select row (2M/1M/500k/
  200k/100k/50k/20k/10k) below the band row.
- `static/app.js`:
  - `drawSpectrum()` now crops the full-bandwidth spectrum to the
    selected span, centered on the tuned frequency, before plotting --
    both the spectrum trace and the waterfall (which now renders the same
    cropped view, so the two stay aligned).
  - New frequency axis strip along the bottom of the spectrum panel:
    "nice" 1-2-5 tick spacing (`niceTickSpacing()`) chosen from the
    current span so grid lines land on round numbers, labeled in MHz with
    a decimal count that scales with tick spacing.
  - Click-to-tune and scroll-to-tune now map canvas position through the
    *selected span*, not the fixed acquisition bandwidth, so they stay
    accurate when zoomed in.
  - Fixed a robustness gap found while testing this: initial canvas
    sizing was deferred to `requestAnimationFrame`, which can be
    throttled indefinitely in a backgrounded/non-composited tab, leaving
    the canvas stuck at the browser's default 300px buffer width. Now
    called directly at boot.

## v0.1.1 — fix invisible spectrum/waterfall

- `static/app.css`: `#spectrum` had no CSS `height`, so its layout size
  was driven by the canvas `height` attribute -- which the resize handler
  was also writing to (`clientHeight * devicePixelRatio`). On a scaled
  display (devicePixelRatio != 1) each resize compounded this into a
  runaway feedback loop; the canvas grew to 900px+ tall and pushed
  everything off-screen. Fixed by giving both canvases explicit
  CSS-driven layout height, decoupled from the buffer-resolution
  attribute, plus `overflow:hidden` on `main` as a backstop.
- `static/app.js`: binary WS frames are `[1-byte tag][payload]`, but
  `new Float32Array(buf, 1)` / `new Int16Array(buf, 1)` both require a
  byte offset that's a multiple of the element size -- 1 isn't a multiple
  of 4 or 2, so both threw `RangeError` on every message (spectrum frames
  silently failed to draw at all; audio was equally broken). Fixed with
  `buf.slice(1)` to copy into a fresh zero-offset buffer before
  constructing the typed array view.

## v0.1 — initial browser SDR receiver (single tuner)

- `backend/sdrplay_api.py`: ctypes binding to the real SDRplay API v3.15
  DLL (`sdrplay_api.dll`), struct layouts transcribed from the vendor
  headers in `C:\Program Files\SDRplay\API\inc`.
- `backend/receiver.py`: opens the RSPduo in single-tuner mode (Tuner A,
  the SMA port with the AM/HF option), zero-IF, fixed 2 Msps ADC rate,
  streams IQ into a ring buffer. Exposes frequency/gain/LNA-state/AGC
  setters.
- `backend/ringbuffer.py`: thread-safe circular IQ buffer bridging the
  SDRplay driver's real-time callback thread to the spectrum and demod
  worker threads.
- `backend/dsp.py`: windowed-FFT spectrum engine, cascaded decimation
  (2 Msps -> 100 kHz -> 12.5 kHz), and AM/FM/USB/LSB/CW demodulators to
  12.5 kHz PCM audio.
- `backend/bands.py`: approximate VK amateur band plan (160m-70cm) for
  the band-select buttons.
- `backend/server.py`: FastAPI + WebSocket server. Binary frames carry
  spectrum (float32 dB) and audio (int16 PCM); JSON carries state and
  control commands. No ESP32/display hardware involved — the browser is
  the whole UI.
- `static/`: single-page browser frontend — canvas spectrum + scrolling
  waterfall (click/scroll to tune), band/mode buttons, frequency entry,
  gain/LNA/AGC/volume controls, S-meter, overload badge, Web Audio
  playback.
- Scope for this version: single active VFO (Tuner A only); Tuner B /
  simultaneous dual-band receive deferred to a later version.
