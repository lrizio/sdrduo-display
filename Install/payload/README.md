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

## Current scope (v0.1)

- Single active VFO on Tuner A (the SMA port with the AM/HF option), fixed
  2 Msps / zero-IF acquisition -- gives a 2 MHz-wide panadapter around the
  tuned frequency.
- Modes: AM, FM (narrow), USB, LSB, CW.
- Spectrum + scrolling waterfall, click or scroll-wheel to tune.
- Band-select buttons for the VK amateur bands 160m-70cm (see
  `backend/bands.py` -- edges are approximate, not the authoritative ACMA
  band plan).
- Manual gain reduction / LNA state sliders, or hardware AGC.
- S-meter (relative dBFS) and ADC-overload badge.

## Not yet implemented

- Tuner B / simultaneous dual-band receive (RSPduo can do both tuners at
  once; this version only drives Tuner A).
- Digital mode decoding (FT8 etc.), recording, memory channels, squelch.
- Any persistence of settings between runs -- it always starts on 20m SSB.

## Known limitation

Audio decimation (2 Msps -> 100 kHz -> 12.5 kHz) is done per-chunk without
carrying filter state across chunk boundaries, so there can be faint
clicks roughly every 40 ms. Fine for casual listening; a streaming
decimator would remove it if it turns out to matter.

See `CHANGELOG.md` for version history and `backend/` for the code layout.
