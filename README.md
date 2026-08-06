# SDRduo Display

A browser-based SDR receiver for the SDRplay RSPduo. No dedicated display
hardware — the laptop does the DSP and serves a web UI you open in a normal
browser tab.

```
RSPduo --USB--> SDRplay API (this laptop) --DSP--> FastAPI/WebSocket --> browser
                                                     (spectrum, waterfall,
                                                      audio, controls)
```

## Requirements

- SDRplay API / drivers installed and the `SDRplay API Service` running
  (already the case on this machine).
- Python 3.10+ (this machine has 3.14).
- RSPduo connected by USB.

## Running

```bash
start_sdrduo.bat
```

First run creates a `venv`, installs `requirements.txt`, then starts the
server and opens `http://127.0.0.1:8080` in your default browser. Later
runs just reuse the venv.

To run manually instead:

```bash
venv\Scripts\activate
python run.py
```

## Current scope (v0.15)

- Single active VFO on Tuner A (the SMA port with the AM/HF option), fixed
  2 Msps / zero-IF acquisition -- gives a 2 MHz-wide panadapter around the
  tuned frequency.
- Modes: AM, FM (narrow), WFM (broadcast FM), USB, LSB, CW.
- Spectrum + scrolling waterfall, click or scroll-wheel to tune. Selectable
  span (2M down to 10k) and tuning step (10Hz-10k), both remembered across
  reloads. Floor/ceiling sliders control display contrast independently of
  RF gain.
- Band-select buttons for the VK amateur bands 160m-70cm (see
  `backend/bands.py` -- edges are approximate, not the authoritative ACMA
  band plan).
- On-screen keypad for frequency entry (unit-aware, MHz/kHz toggle, live
  preview) plus 8 memory preset slots (M1-M8), saved/recalled client-side.
- Manual gain reduction / LNA state sliders, or hardware AGC -- with
  automatic GR/LNA re-sync on HF/VHF/UHF band crossings when AGC is on.
- Squelch (enable + threshold), S-meter (ham S-unit convention) and
  ADC-overload badge.
- Exit button in the UI for a clean shutdown (releases the SDRplay API
  device properly instead of requiring a console kill).
- Standalone Windows installer (`Install/setup.exe`) for installing on
  another machine -- see `Install/README.txt`.

## Not yet implemented

- Tuner B / simultaneous dual-band receive (RSPduo can do both tuners at
  once; this version only drives Tuner A).
- Digital mode decoding (FT8 etc.) and recording.
- Persistence of frequency/mode between runs -- it always starts on 20m
  SSB (display settings like span/step/memories/floor/ceiling do persist,
  client-side via `localStorage`).

See `CHANGELOG.md` for version history, `docs/` for the end-user guide, and
`backend/` for the code layout.
