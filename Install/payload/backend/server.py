"""FastAPI + WebSocket server: the browser is the only UI. Serves the
static frontend and a /ws endpoint that pushes binary spectrum/audio frames
plus JSON state, and accepts JSON control commands.

Binary WS frame format: first byte is a type tag, rest is raw payload.
  0x01  spectrum: float32[[FFT_SIZE]] magnitude in dB, DC-centered
  0x02  audio:    int16[N] mono PCM at dsp.AUDIO_FS
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import bands, dsp
from .receiver import RspduoReceiver
from .ringbuffer import IQRingBuffer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sdrduo.server")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

RING_CAPACITY = int(dsp.RAW_FS * 2)  # 2 seconds of headroom
SPECTRUM_PERIOD_S = 0.05  # 20 Hz
AUDIO_CHUNK_PERIOD_S = 0.04  # 40 ms
STATE_PERIOD_S = 0.25

ring = IQRingBuffer(RING_CAPACITY)
receiver = RspduoReceiver(ring)
spectrum_engine = dsp.SpectrumEngine()
demodulator = dsp.Demodulator()

out_queue: "queue.Queue[bytes]" = queue.Queue(maxsize=200)
clients: set[WebSocket] = set()
stop_event = threading.Event()

_last_signal_dbfs = -120.0
_squelch_enabled = False
_squelch_threshold_dbfs = -90.0


def spectrum_worker() -> None:
    global _last_signal_dbfs
    while not stop_event.is_set():
        time.sleep(SPECTRUM_PERIOD_S)
        try:
            iq = ring.read_latest(dsp.FFT_SIZE)
            spec_db = spectrum_engine.compute(iq)
            _last_signal_dbfs = dsp.center_power_dbfs(spec_db)
            frame = b"\x01" + spec_db.tobytes()
            out_queue.put_nowait(frame)
        except queue.Full:
            pass
        except Exception:
            log.exception("spectrum worker error")


def demod_worker() -> None:
    read_pos = ring.write_pos
    max_n = int(dsp.RAW_FS * AUDIO_CHUNK_PERIOD_S * 3)  # generous headroom
    while not stop_event.is_set():
        time.sleep(AUDIO_CHUNK_PERIOD_S)
        try:
            data, read_pos = ring.read_since(read_pos, max_n)
            if len(data) == 0:
                continue
            pcm = demodulator.process(data)
            if len(pcm) == 0:
                continue
            if _squelch_enabled and _last_signal_dbfs < _squelch_threshold_dbfs:
                # Gate the audio, not the demod -- keeps the PCM stream
                # continuous (silence, not a gap) so the browser's Web
                # Audio scheduler doesn't have to deal with dropped chunks.
                pcm = np.zeros_like(pcm)
            frame = b"\x02" + pcm.tobytes()
            out_queue.put_nowait(frame)
        except queue.Full:
            pass
        except Exception:
            log.exception("demod worker error")


def current_state() -> dict:
    return {
        "type": "state",
        "freqHz": receiver.frequency_hz,
        "mode": demodulator.mode,
        "grdb": receiver.grdb,              # what we last commanded
        "grdbActual": receiver.current_grdb,  # what the tuner is really running
        "lnaState": receiver.lna_state,
        "agc": receiver.agc_enabled,
        "overload": receiver.overload,
        "signalDbfs": round(_last_signal_dbfs, 1),
        "sampleRateHz": dsp.RAW_FS,
        "squelchEnabled": _squelch_enabled,
        "squelchThresholdDbfs": _squelch_threshold_dbfs,
        "squelched": bool(_squelch_enabled and _last_signal_dbfs < _squelch_threshold_dbfs),
    }


def handle_command(cmd: dict) -> None:
    global _squelch_enabled, _squelch_threshold_dbfs
    action = cmd.get("cmd")
    try:
        if action == "set_freq":
            receiver.set_frequency(float(cmd["hz"]))
        elif action == "set_mode":
            demodulator.set_mode(cmd["mode"])
        elif action == "set_band":
            band = next((b for b in bands.BANDS if b.name == cmd["band"]), None)
            if band is not None:
                receiver.set_frequency(band.default_hz)
                demodulator.set_mode(band.default_mode)
        elif action == "set_gain":
            receiver.set_gain(
                grdb=cmd.get("grdb"),
                lna_state=cmd.get("lnaState"),
            )
        elif action == "set_agc":
            receiver.set_agc(bool(cmd["enabled"]))
        elif action == "set_squelch":
            if "enabled" in cmd:
                _squelch_enabled = bool(cmd["enabled"])
            if "thresholdDbfs" in cmd:
                _squelch_threshold_dbfs = float(cmd["thresholdDbfs"])
        else:
            log.warning("unknown command: %s", cmd)
    except Exception:
        log.exception("command failed: %s", cmd)


SEND_TIMEOUT_S = 1.0  # a single slow/half-dead client must not stall everyone else


async def _send_or_mark_dead(ws: WebSocket, send_coro, dead: list[WebSocket]) -> None:
    try:
        await asyncio.wait_for(send_coro, timeout=SEND_TIMEOUT_S)
    except Exception:
        dead.append(ws)


async def frame_broadcaster() -> None:
    # Sends to every client CONCURRENTLY, each with its own timeout, rather
    # than one `await` per client in sequence -- a client that stopped
    # reading (e.g. a stale tab whose disconnect the server hasn't detected
    # yet) would otherwise block this loop indefinitely, since `send_bytes`
    # has no timeout of its own. That stalls `out_queue` draining for EVERY
    # client, not just the stuck one, which looks exactly like "audio
    # stopped updating" even though the demod worker is producing fine --
    # the frames just never get broadcast out.
    loop = asyncio.get_event_loop()
    while True:
        frame = await loop.run_in_executor(None, out_queue.get)
        dead: list[WebSocket] = []
        await asyncio.gather(
            *(_send_or_mark_dead(ws, ws.send_bytes(frame), dead) for ws in list(clients))
        )
        for ws in dead:
            clients.discard(ws)


async def state_broadcaster() -> None:
    while True:
        await asyncio.sleep(STATE_PERIOD_S)
        text = json.dumps(current_state())
        dead: list[WebSocket] = []
        await asyncio.gather(
            *(_send_or_mark_dead(ws, ws.send_text(text), dead) for ws in list(clients))
        )
        for ws in dead:
            clients.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    receiver.open()
    stop_event.clear()
    threads = [
        threading.Thread(target=spectrum_worker, daemon=True, name="spectrum"),
        threading.Thread(target=demod_worker, daemon=True, name="demod"),
    ]
    for t in threads:
        t.start()
    bg_tasks = [
        asyncio.create_task(frame_broadcaster()),
        asyncio.create_task(state_broadcaster()),
    ]
    try:
        yield
    finally:
        for t in bg_tasks:
            t.cancel()
        stop_event.set()
        for t in threads:
            t.join(timeout=2)
        receiver.close()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def no_cache_static(request, call_next):
    # This app is actively developed and reloaded constantly; a browser
    # silently reusing a stale cached copy of app.css/app.js (no request
    # logged at all, not even a conditional 304) previously caused UI
    # changes to appear broken/missing even though the served file was
    # correct. Static assets are tiny and served locally, so there's no
    # real cost to always revalidating.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


def _shutdown_worker() -> None:
    # Runs on its own thread, NOT the asyncio event loop, so it can't
    # block the response below from actually reaching the browser first.
    time.sleep(0.3)
    log.info("Shutting down (requested via UI)")
    try:
        # The one step that actually matters: release the RSPduo through
        # the SDRplay API. Skipping this (e.g. a Task Manager "End Task"
        # or closing the console window instead of Ctrl+C) is exactly
        # what leaves the device "still claimed" until the USB stack
        # times out on its own -- confirmed the hard way earlier this
        # project. os._exit() below is a hard stop with no further
        # Python-level cleanup, so this has to happen before it, not
        # rely on it.
        receiver.close()
    except Exception:
        log.exception("Error releasing the RSPduo during UI-triggered shutdown")
    os._exit(0)


@app.post("/api/shutdown")
async def shutdown_endpoint() -> dict:
    threading.Thread(target=_shutdown_worker, daemon=True).start()
    return {"status": "shutting down"}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    clients.add(websocket)
    init = current_state()
    init["type"] = "init"
    init["bands"] = bands.to_json()
    init["modes"] = list(dsp.MODES)
    init["fftSize"] = dsp.FFT_SIZE
    init["audioRateHz"] = dsp.AUDIO_FS
    await websocket.send_text(json.dumps(init))
    try:
        while True:
            msg = await websocket.receive_text()
            handle_command(json.loads(msg))
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(websocket)
