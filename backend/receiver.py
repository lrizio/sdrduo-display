"""RSPduo device wrapper: owns the SDRplay API session, streams IQ into an
IQRingBuffer, and exposes simple setters for frequency/gain/AGC.

Single-tuner mode only (Tuner A, the SMA port with the AM/HF option),
zero-IF, fixed 2 Msps ADC rate -- see CHANGELOG.md / README.md for why.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

from . import sdrplay_api as api
from .ringbuffer import IQRingBuffer

log = logging.getLogger("sdrduo.receiver")

RAW_FS = 2_000_000.0
DEFAULT_FREQ_HZ = 14_200_000.0  # 20m SSB
DEFAULT_GRDB = 40
DEFAULT_LNA_STATE = 4

# This is a zero-IF receiver tuned by moving the hardware LO to exactly the
# requested frequency -- which means the desired signal always lands
# exactly on top of 0 Hz in the raw acquisition. Zero-IF/direct-conversion
# hardware (this one included) has a DC-offset/LO-leakage artifact that
# ALSO sits at 0 Hz, tied to the LO itself, regardless of what's tuned
# there. When a signal that itself sits near 0 Hz (AM's carrier, envelope-
# detected as amplitude, is mathematically indistinguishable from a DC
# term) collides with that artifact, the two interfere and corrupt simple
# envelope/discriminator detection -- this is what produced "Donald Duck"
# distortion on AM exactly on-frequency, clearing up when manually
# detuned a couple kHz off-carrier (the classic DXer "ECSS" workaround).
#
# Fix: never actually tune the hardware LO to the frequency of interest.
# Offset it by a fixed amount, and shift the acquired IQ back down by the
# same amount in software (see _on_stream_a) before it reaches the ring
# buffer -- so every downstream consumer (spectrum, demod) still sees the
# wanted signal sitting at 0 Hz exactly as before, but the fixed hardware
# artifact, which stays at the LO's own reference, now lands at
# +LO_OFFSET_HZ instead -- clear of every mode's passband (WFM needs the
# most headroom, +-100kHz for its Carson bandwidth). 150kHz/2Msps is
# exactly 3/40, a clean 40-sample repeat period for the software shift,
# so the correction has zero floating-point phase drift over any run
# length.
LO_OFFSET_HZ = 150_000.0
_NCO_PERIOD_SAMPLES = 40  # LO_OFFSET_HZ / RAW_FS == 3/40 exactly


def _gain_zone(hz: float) -> str:
    """Coarse RF zone, used only to decide when a band-crossing gain
    re-sync is warranted -- not a claim about where the RSPduo's own
    internal front-end switches actually happen (not precisely documented
    here). 30MHz/300MHz are conservative, standard HF/VHF/UHF partition
    points chosen to safely bracket wherever those real transitions are,
    so every actual crossing gets caught even without knowing the exact
    number.
    """
    if hz < 30_000_000:
        return "HF"
    if hz < 300_000_000:
        return "VHF"
    return "UHF"


class RspduoReceiver:
    def __init__(self, ring: IQRingBuffer):
        self.ring = ring
        self._device = api.sdrplay_api_DeviceT()
        self._device_params: "api.sdrplay_api_DeviceParamsT" = None  # type: ignore[assignment]
        self._rx_a = None
        self._dev_params = None
        # Reentrant: set_frequency() calls set_gain() internally (for the
        # per-band gain re-sync below), and both take this lock.
        self._lock = threading.RLock()

        self.frequency_hz = DEFAULT_FREQ_HZ
        self.grdb = DEFAULT_GRDB
        self.lna_state = DEFAULT_LNA_STATE
        self.agc_enabled = True
        self.overload = False

        # Tracks which coarse RF zone we're in, so set_frequency() can
        # tell a genuine band crossing (HF -> VHF broadcast, say) apart
        # from ordinary in-band retuning. GR/LNA settings that were fine
        # in the old zone can be invalid or just badly wrong for the new
        # one (LNA state validity ranges differ by band; a GR that's
        # sensible on quiet HF can overload a strong local VHF broadcast
        # station) -- this is what used to require hunting for a working
        # combination by hand after every band change.
        self._gain_zone = _gain_zone(DEFAULT_FREQ_HZ)

        # Running sample count for the software re-centering NCO (see
        # LO_OFFSET_HZ above), wrapped mod its exact repeat period rather
        # than left to grow unbounded -- keeps the phase calculation
        # working with small numbers indefinitely, not just for a while.
        # Never reset on retune: the offset itself never changes, only the
        # hardware LO's absolute position does, so the correction can just
        # keep running continuously across retunes with no discontinuity.
        self._nco_sample_count = 0

        # Keep references so the GC never collects these while the DLL holds
        # function pointers to them.
        self._stream_a_cb = api.StreamCallback_t(self._on_stream_a)
        self._stream_b_cb = api.StreamCallback_t(self._on_stream_b)
        self._event_cb = api.EventCallback_t(self._on_event)
        self._callbacks = api.sdrplay_api_CallbackFnsT(
            StreamACbFn=self._stream_a_cb,
            StreamBCbFn=self._stream_b_cb,
            EventCbFn=self._event_cb,
        )

    # -- lifecycle ----------------------------------------------------
    def open(self) -> None:
        api.check("Open", api.dll.sdrplay_api_Open())

        ver = api.C.c_float()
        api.check("ApiVersion", api.dll.sdrplay_api_ApiVersion(api.C.byref(ver)))
        log.info("SDRplay API version %.2f", ver.value)

        # sdrplay_api_GetDevices() can legitimately come back empty for a
        # few seconds right after the SDRplay API Service has just
        # started, or right after the RSPduo itself was just plugged in --
        # device enumeration lags service startup. Failing instantly on
        # one empty result produced a spurious "No RSPduo found" on a
        # fresh install: hit for real when launching via the Desktop
        # shortcut immediately failed, but a manual relaunch moments later
        # (nothing else changed) succeeded. Retry for a few seconds before
        # actually giving up. Every other sdrplay_api_* failure still
        # raises immediately via api.check(), unchanged.
        ENUM_TIMEOUT_S = 8.0
        ENUM_POLL_S = 0.5
        deadline = time.monotonic() + ENUM_TIMEOUT_S
        chosen = None
        num = api.C.c_uint(0)
        attempt = 0
        while chosen is None:
            attempt += 1
            devices = (api.sdrplay_api_DeviceT * api.SDRPLAY_MAX_DEVICES)()
            api.check(
                "GetDevices",
                api.dll.sdrplay_api_GetDevices(
                    devices, api.C.byref(num), api.SDRPLAY_MAX_DEVICES
                ),
            )
            for i in range(num.value):
                d = devices[i]
                if d.hwVer == api.SDRPLAY_RSPduo_ID and d.valid:
                    chosen = d
                    break
            if chosen is None:
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "No RSPduo found by sdrplay_api_GetDevices "
                        f"({num.value} device(s) enumerated, {attempt} "
                        f"attempt(s) over {ENUM_TIMEOUT_S:.0f}s)."
                    )
                log.info(
                    "RSPduo not enumerated yet (attempt %d) -- retrying...",
                    attempt,
                )
                time.sleep(ENUM_POLL_S)

        chosen.tuner = api.sdrplay_api_Tuner_A
        chosen.rspDuoMode = api.sdrplay_api_RspDuoMode_Single_Tuner
        chosen.rspDuoSampleFreq = RAW_FS
        self._device = chosen

        api.check("SelectDevice", api.dll.sdrplay_api_SelectDevice(api.C.byref(self._device)))
        log.info("RSPduo selected, serial %s", self._device.SerNo.decode(errors="replace").rstrip("\x00"))

        dp_ptr = api.C.POINTER(api.sdrplay_api_DeviceParamsT)()
        api.check(
            "GetDeviceParams",
            api.dll.sdrplay_api_GetDeviceParams(self._device.dev, api.C.byref(dp_ptr)),
        )
        self._device_params = dp_ptr.contents
        self._dev_params = self._device_params.devParams.contents
        self._rx_a = self._device_params.rxChannelA.contents

        self._dev_params.fsFreq.fsHz = RAW_FS

        tp = self._rx_a.tunerParams
        tp.ifType = api.sdrplay_api_IF_Zero
        tp.bwType = api.sdrplay_api_BW_1_536
        tp.loMode = api.sdrplay_api_LO_Auto
        tp.rfFreq.rfHz = self.frequency_hz + LO_OFFSET_HZ
        tp.gain.gRdB = self.grdb
        tp.gain.LNAstate = self.lna_state
        tp.gain.minGr = api.sdrplay_api_NORMAL_MIN_GR

        cp = self._rx_a.ctrlParams
        cp.dcOffset.DCenable = 1
        cp.dcOffset.IQenable = 1
        cp.decimation.enable = 0
        cp.decimation.decimationFactor = 1
        cp.agc.enable = api.sdrplay_api_AGC_50HZ if self.agc_enabled else api.sdrplay_api_AGC_DISABLE
        cp.agc.setPoint_dBfs = -30

        self._rx_a.rspDuoTunerParams.tuner1AmPortSel = api.sdrplay_api_RspDuo_AMPORT_2

        api.check(
            "Init",
            api.dll.sdrplay_api_Init(
                self._device.dev, api.C.byref(self._callbacks), None
            ),
        )
        log.info("Streaming started at %.0f Hz center %.0f Hz", RAW_FS, self.frequency_hz)

    def close(self) -> None:
        try:
            if self._device.dev:
                api.dll.sdrplay_api_Uninit(self._device.dev)
                api.dll.sdrplay_api_ReleaseDevice(api.C.byref(self._device))
        finally:
            api.dll.sdrplay_api_Close()

    # -- controls -------------------------------------------------------
    def set_frequency(self, hz: float) -> None:
        with self._lock:
            self.frequency_hz = hz
            self._rx_a.tunerParams.rfFreq.rfHz = hz + LO_OFFSET_HZ
            api.check(
                "Update(Frf)",
                api.dll.sdrplay_api_Update(
                    self._device.dev,
                    api.sdrplay_api_Tuner_A,
                    api.sdrplay_api_Update_Tuner_Frf,
                    api.sdrplay_api_Update_Ext1_None,
                ),
            )
            new_zone = _gain_zone(hz)
            zone_changed = new_zone != self._gain_zone
            self._gain_zone = new_zone

        # Only when AGC is on: that's an explicit signal the user wants
        # gain auto-managed, so giving it a clean, known-valid starting
        # point (LNA 0 -- always a valid table index, unlike whatever
        # state happened to be active before) on a real band crossing is
        # squarely in scope. Leave manual gain mode alone -- forcibly
        # overriding a deliberately-chosen GR/LNA there would fight the
        # user rather than help them.
        if zone_changed and self.agc_enabled:
            log.info(
                "Gain zone changed to %s at %.0f Hz -- resetting GR/LNA for AGC to re-adjust from",
                new_zone, hz,
            )
            try:
                self.set_gain(grdb=DEFAULT_GRDB, lna_state=0)
            except api.SdrplayError:
                # Don't let a failed gain re-sync make the frequency
                # change itself look like it failed -- it already
                # succeeded above.
                log.exception("Gain re-sync on band change failed")

    @property
    def current_grdb(self) -> int:
        """The gain reduction the tuner is *actually* running, which is not
        `self.grdb` whenever the hardware AGC is enabled.

        `self.grdb` is only ever the last value we commanded. With AGC on,
        the API drives gRdB itself and writes the result back into the
        tuner params struct, so the commanded value goes stale the moment
        the AGC moves -- measured 40 commanded against 43 actual on a live
        signal, with the UI cheerfully reporting 40. Nothing was reading
        the struct back: `_on_event` handles only PowerOverloadChange and
        DeviceRemoved, never the API's gain-change event.

        Falls back to the commanded value when the device isn't open,
        since the struct doesn't exist to read then.
        """
        if self._rx_a is None:
            return self.grdb
        try:
            return int(self._rx_a.tunerParams.gain.gRdB)
        except Exception:  # pragma: no cover -- struct gone mid-teardown
            return self.grdb

    def set_gain(self, grdb: int | None = None, lna_state: int | None = None) -> None:
        # Valid (gRdB, LNAstate) combinations depend on the current LNA
        # table, which varies with frequency/AM-port -- not every value the
        # slider can produce is valid. On rejection, roll the struct field
        # back to the last-known-good value so the device doesn't get stuck
        # rejecting every subsequent update because of a lingering bad field.
        with self._lock:
            prev_grdb, prev_lna = self.grdb, self.lna_state
            if grdb is not None:
                self.grdb = grdb
                self._rx_a.tunerParams.gain.gRdB = grdb
            if lna_state is not None:
                self.lna_state = lna_state
                self._rx_a.tunerParams.gain.LNAstate = lna_state
            try:
                api.check(
                    "Update(Gr)",
                    api.dll.sdrplay_api_Update(
                        self._device.dev,
                        api.sdrplay_api_Tuner_A,
                        api.sdrplay_api_Update_Tuner_Gr,
                        api.sdrplay_api_Update_Ext1_None,
                    ),
                )
            except api.SdrplayError:
                self.grdb, self.lna_state = prev_grdb, prev_lna
                self._rx_a.tunerParams.gain.gRdB = prev_grdb
                self._rx_a.tunerParams.gain.LNAstate = prev_lna
                # Restoring the struct fields only fixes our own in-memory
                # belief about the gain state -- the rejected Update() call
                # may have left the DEVICE's actual internal gain state out
                # of sync with that belief. Re-push the rollback to the
                # device too (best-effort: if even this fails, we're in a
                # genuinely wedged state and there's nothing more to try
                # here), so hardware and software stay in agreement instead
                # of silently diverging and making every later gain command
                # fail for a reason that's no longer visible in `self.grdb`/
                # `self.lna_state`.
                try:
                    api.dll.sdrplay_api_Update(
                        self._device.dev,
                        api.sdrplay_api_Tuner_A,
                        api.sdrplay_api_Update_Tuner_Gr,
                        api.sdrplay_api_Update_Ext1_None,
                    )
                except Exception:
                    pass
                raise

    def set_agc(self, enabled: bool) -> None:
        with self._lock:
            self.agc_enabled = enabled
            self._rx_a.ctrlParams.agc.enable = (
                api.sdrplay_api_AGC_50HZ if enabled else api.sdrplay_api_AGC_DISABLE
            )
            api.check(
                "Update(Agc)",
                api.dll.sdrplay_api_Update(
                    self._device.dev,
                    api.sdrplay_api_Tuner_A,
                    api.sdrplay_api_Update_Ctrl_Agc,
                    api.sdrplay_api_Update_Ext1_None,
                ),
            )

    # -- callbacks (called on the SDRplay driver's streaming thread) ----
    def _on_stream_a(self, xi, xq, params, num_samples, reset, cb_context) -> None:
        n = int(num_samples)
        if n == 0:
            return
        i = np.ctypeslib.as_array(xi, shape=(n,))
        q = np.ctypeslib.as_array(xq, shape=(n,))
        iq = (i.astype(np.float32) + 1j * q.astype(np.float32)) * (1.0 / 32768.0)

        # Software re-centering (see LO_OFFSET_HZ above): the hardware LO
        # is deliberately tuned LO_OFFSET_HZ above the requested frequency,
        # so the wanted signal currently sits at -LO_OFFSET_HZ in this raw
        # acquisition. Shift it back up to 0 Hz -- the phase-continuous
        # part is the running sample index mod its exact 40-sample repeat
        # period, so this has no drift no matter how long the receiver's
        # been streaming.
        idx = (self._nco_sample_count + np.arange(n)) % _NCO_PERIOD_SAMPLES
        phase = (2.0 * np.pi * LO_OFFSET_HZ / RAW_FS) * idx
        iq = iq * np.exp(1j * phase).astype(np.complex64)
        self._nco_sample_count = (self._nco_sample_count + n) % _NCO_PERIOD_SAMPLES

        self.ring.write(iq.astype(np.complex64))

    def _on_stream_b(self, xi, xq, params, num_samples, reset, cb_context) -> None:
        pass  # Tuner B unused in single-tuner mode.

    def _on_event(self, event_id, tuner, params, cb_context) -> None:
        if event_id == api.sdrplay_api_PowerOverloadChange:
            self.overload = (
                params.contents.powerOverloadParams.powerOverloadChangeType
                == api.sdrplay_api_Overload_Detected
            )
            # Per SDRplay API docs, overload messages must be acknowledged.
            api.dll.sdrplay_api_Update(
                self._device.dev,
                tuner,
                api.sdrplay_api_Update_Ctrl_OverloadMsgAck,
                api.sdrplay_api_Update_Ext1_None,
            )
        elif event_id == api.sdrplay_api_DeviceRemoved:
            log.warning("RSPduo removed")
