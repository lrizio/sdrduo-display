"""Spectrum (FFT) and demodulation DSP, working on IQ pulled from the ring
buffer. Runs on plain CPU/numpy/scipy -- no GPU, no special hardware.

Sample-rate chain for demod:
    RAW_FS (2,000,000, from the RSPduo)
      -> stage 1 decimate x20 -> INTERMEDIATE_FS (100,000)
           [FM discriminator runs here -- needs the wider bandwidth
            for +-5kHz NFM deviation]
      -> stage 2 decimate x8  -> AUDIO_FS (12,500)
           [AM envelope / SSB+CW complex bandpass run here]

WFM (broadcast FM) is NOT the same signal as FM above -- ham NFM is
+-5 kHz deviation in a ~16 kHz channel (Carson's rule); broadcast FM is
+-75 kHz deviation in a ~180 kHz channel. The NFM chain's own first stage
only preserves +-45 kHz, well short of what WFM needs, so WFM gets its
own parallel, less-aggressive chain instead of trying to share NFM's:
    RAW_FS (2,000,000)
      -> wfm stage 1 decimate x4 -> WFM_IF_FS (500,000)
           [discriminator runs here, +-75kHz deviation reference,
            preserves the full broadcast channel]
      -> wfm audio decimate x8 -> 62,500 -> decimate x5 -> AUDIO_FS (12,500)
           [two audio-domain stages, same total /40 as the two together,
            because a single /40 stage's transition band would alias]

Every FIR filter's delay-line state, and the FM/WFM discriminator's
cross-chunk sample, are threaded across `process()` calls (see `_stream`
and the `_prev_if` handling) rather than reset to zero each call -- an
earlier version reset everything per chunk, which produces a real
transient (the filter "ringing" from a false zero-signal history) at the
start of every chunk. For FM/WFM that transient runs straight through the
discriminator and comes out as an audible click, once per chunk -- a
steady, fast repeating "clacking" since chunks arrive tens of times a
second, regardless of what's tuned in.
"""
from __future__ import annotations

import numpy as np
from scipy import signal

RAW_FS = 2_000_000.0
INTERMEDIATE_FS = 100_000.0
AUDIO_FS = 12_500.0
_STAGE1_FACTOR = 20
_STAGE2_FACTOR = 8

WFM_IF_FS = 500_000.0
_WFM_IF_FACTOR = 4
_WFM_AUDIO_A_FS = 62_500.0
_WFM_AUDIO_A_FACTOR = 8
_WFM_AUDIO_B_FACTOR = 5  # 62,500 / 5 = 12,500 = AUDIO_FS

FFT_SIZE = 16384  # ~122 Hz/bin at RAW_FS -- fine enough to zoom into a single signal

MODES = ("AM", "FM", "WFM", "USB", "LSB", "CW")


class SpectrumEngine:
    """Windowed FFT -> dB power spectrum, centered (DC in the middle).

    A single FFT snapshot is a very noisy power estimate (high per-bin
    variance regardless of resolution -- this is a statistics problem, not
    a resolution problem). Exponential trace averaging in the linear power
    domain smooths that out, the same "video averaging" a real spectrum
    analyzer does, without adding perceptible tuning lag.
    """

    def __init__(self, fft_size: int = FFT_SIZE, avg_alpha: float = 0.25):
        self.fft_size = fft_size
        self._window = np.hanning(fft_size).astype(np.float32)
        self._window_sum = float(np.sum(self._window))
        self._avg_alpha = avg_alpha
        self._avg_power: np.ndarray | None = None

    def compute(self, iq: np.ndarray) -> np.ndarray:
        n = self.fft_size
        if len(iq) < n:
            iq = np.pad(iq, (0, n - len(iq)))
        x = iq[-n:] * self._window
        spec = np.fft.fftshift(np.fft.fft(x))
        mag = np.abs(spec) / (self._window_sum / 2.0)
        power = np.maximum(mag * mag, 1e-24)

        if self._avg_power is None or self._avg_power.shape != power.shape:
            self._avg_power = power
        else:
            a = self._avg_alpha
            self._avg_power = a * power + (1.0 - a) * self._avg_power

        return (10.0 * np.log10(self._avg_power)).astype(np.float32)


def center_power_dbfs(spectrum_db: np.ndarray, half_width_bins: int = 5) -> float:
    n = len(spectrum_db)
    c = n // 2
    lo, hi = max(0, c - half_width_bins), min(n, c + half_width_bins)
    return float(np.mean(spectrum_db[lo:hi]))


def _zeros_zi(taps: np.ndarray, complex_: bool = False) -> np.ndarray:
    """A zero-initial-state delay line sized correctly for `taps`, without
    hardcoding `len(taps) - 1` at every call site."""
    zi = signal.lfilter_zi(taps, [1.0]) * 0.0
    return zi.astype(np.complex64) if complex_ else zi.astype(np.float64)


class _AudioAgc:
    """Audio-frequency AGC with a look-ahead limiter: holds demodulated
    audio near a target level regardless of signal strength, without ever
    clipping.

    Needed because nothing else in this chain sets the audio level. AM and
    SSB come out directly proportional to RF amplitude, so a weak station
    lands tens of dB down -- measured -29dBFS on an AM carrier at 0.05 of
    full scale, and -43dBFS at 0.01 -- while the browser's volume control
    only reaches 2x, nowhere near enough to make that up. WFM is a
    different problem: being phase-derived its level doesn't depend on
    signal strength at all, but it sat at a constant ~-14dBFS peaking at
    0.37, leaving most of the available headroom unused. So the target
    here is deliberately louder than the level WFM already had -- matching
    it would have been a no-op for exactly the mode that prompted this.

    Gain is recomputed per block from that block's RMS, then ramped
    linearly across the block's samples: no Python loop over samples, and
    no zipper noise from stepping the gain at block edges. Block stats are
    computed for the whole chunk at once; only the gain recursion itself
    is sequential, and that's scalar arithmetic over ~8 blocks per chunk.

    The limiter is exact rather than approximate, because each block's
    peak is known *before* its gain is applied: the gain is never allowed
    to exceed `ceiling / peak`, so a block physically cannot clip. Gain
    reductions take effect immediately and flat across the block (a step
    down is far less objectionable than clipping through a ramp that
    arrives too late); increases are eased in with a slow release so the
    level doesn't pump between syllables.

    Below `floor_rms` the gain is *held* rather than wound up -- otherwise
    every gap between words, and every squelched moment, would slowly
    amplify the noise floor to full scale and roar the instant a signal
    returned.
    """

    BLOCK = 64  # samples, ~5ms at AUDIO_FS -- also the limiter's look-ahead

    def __init__(self, fs: float = AUDIO_FS, target_rms: float = 0.35,
                 ceiling: float = 0.95, max_gain: float = 80.0,
                 min_gain: float = 0.02, release_s: float = 1.5,
                 floor_rms: float = 2e-4):
        self._target = target_rms
        self._ceiling = ceiling
        self._max_gain = max_gain
        self._min_gain = min_gain
        self._floor = floor_rms
        # One-pole release coefficient, evaluated once per block rather
        # than per sample, hence block_dt (not 1/fs) against the constant.
        self._a_release = 1.0 - float(np.exp(-(self.BLOCK / fs) / release_s))
        self.gain = 1.0

    def reset(self) -> None:
        self.gain = 1.0

    def process(self, audio: np.ndarray) -> np.ndarray:
        n = len(audio)
        if n == 0:
            return audio
        nblocks = -(-n // self.BLOCK)  # ceil
        padded = np.zeros(nblocks * self.BLOCK, dtype=np.float32)
        padded[:n] = audio
        blocks = padded.reshape(nblocks, self.BLOCK)
        rms = np.sqrt(np.mean(blocks.astype(np.float64) ** 2, axis=1))
        peak = np.max(np.abs(blocks), axis=1)

        # Start/end gain per block, not one running chain: a limiting
        # block has to be flat at its reduced gain, because ramping *into*
        # a reduction means the early part of the block is still being
        # multiplied by the old, too-large gain -- and the peak that
        # triggered the limiting may well be right there. Flat is what
        # makes "cannot clip" actually true rather than nearly true.
        g_start = np.empty(nblocks, dtype=np.float64)
        g_end = np.empty(nblocks, dtype=np.float64)
        g = self.gain
        for i in range(nblocks):
            if rms[i] < self._floor:
                desired = g                     # hold: don't chase silence up
            else:
                desired = min(max(self._target / rms[i], self._min_gain), self._max_gain)
            if peak[i] > 0.0:                   # never let this block clip
                desired = min(desired, self._ceiling / peak[i])
            if desired < g:
                g = desired                     # limiter: down now, flat
                g_start[i] = g_end[i] = g
            else:
                g_start[i] = g
                g += (desired - g) * self._a_release
                g_end[i] = g
        self.gain = g

        # Piecewise-linear gain, continuous across chunk boundaries except
        # where the limiter deliberately steps.
        ramp = np.linspace(g_start, g_end, self.BLOCK, endpoint=False,
                           axis=1, dtype=np.float64).astype(np.float32)
        return (blocks * ramp).reshape(-1)[:n]


class Demodulator:
    """Converts raw IQ at RAW_FS into int16 PCM audio at AUDIO_FS."""

    def __init__(self):
        self.mode = "USB"

        self._stage1_taps = signal.firwin(63, 45_000, fs=RAW_FS).astype(np.float32)
        self._stage2_taps = signal.firwin(63, 5_500, fs=INTERMEDIATE_FS).astype(np.float32)

        # stage1 is used by AM/SSB/CW/LSB. stage2 is used TWICE with the
        # same coefficients but on two unrelated signals (stage1's complex
        # IQ for AM/SSB/CW/LSB, and FM's real-valued discriminator output)
        # -- same taps, but each use needs its own independent delay-line
        # memory, hence two separate zi's below.
        self._stage1_zi = _zeros_zi(self._stage1_taps, complex_=True)
        self._stage2_zi_main = _zeros_zi(self._stage2_taps, complex_=True)
        self._stage2_zi_fm = _zeros_zi(self._stage2_taps, complex_=False)

        # FM-only pre-discriminator channel filter. stage1 is a generic
        # 45 kHz-wide anti-alias filter shared by every mode -- fine for
        # AM/SSB/CW, which narrow further with stage2/SSB filtering before
        # detection, but the FM discriminator ran directly on that wide
        # signal with no channel selectivity at all. An FM discriminator's
        # noise output scales with its input bandwidth, and any adjacent
        # carrier inside that 45 kHz window gets demodulated right along
        # with the wanted station -- both the excess background noise and
        # the "carrier on top of the audio" trace back to this. NFM channel
        # bandwidth is ~16 kHz by Carson's rule (+-5 kHz deviation, 3 kHz
        # audio), so narrow to a +-8 kHz complex lowpass before the
        # discriminator.
        self._fm_pre_taps = signal.firwin(63, 8_000, fs=INTERMEDIATE_FS).astype(np.float32)
        self._fm_pre_zi = _zeros_zi(self._fm_pre_taps, complex_=True)

        # FM-only post-discriminator audio filter. An FM discriminator's
        # noise rises with frequency (the classic "noise triangle" --
        # output is proportional to instantaneous frequency, so wideband
        # phase noise gets amplified more at higher audio frequencies).
        # The only shaping applied to the demodulated audio was stage2_taps
        # (5.5 kHz), which exists as the anti-alias filter for the /8
        # decimation and is shared with AM/SSB -- too wide for NFM voice
        # (~3 kHz), so it passed the loudest part of that noise triangle
        # straight through. This narrows FM specifically, after decimation,
        # to normal ham NFM voice bandwidth.
        self._fm_audio_taps = signal.firwin(63, 3_000, fs=AUDIO_FS).astype(np.float32)
        self._fm_audio_zi = _zeros_zi(self._fm_audio_taps, complex_=False)

        # FM discriminator's cross-chunk continuity: the last sample of
        # each chunk's pre-discriminator IF, carried into the next chunk so
        # the very first phase-difference sample of a new chunk is computed
        # against real history instead of being dropped (source of a small
        # per-chunk gap, on top of the much louder filter-reset click above).
        self._fm_prev_if: np.ndarray | None = None

        # WFM's own first stage: a much gentler decimation (x4, not x20) so
        # the full +-90kHz-ish broadcast channel (Carson's rule for +-75kHz
        # deviation + 15kHz audio) survives to the discriminator. Cutoff
        # 100kHz leaves comfortable margin below the post-decimation
        # Nyquist (250kHz).
        self._wfm_if_taps = signal.firwin(63, 100_000, fs=RAW_FS).astype(np.float32)
        self._wfm_if_zi = _zeros_zi(self._wfm_if_taps, complex_=True)
        self._wfm_prev_if: np.ndarray | None = None

        # WFM audio downsampling, 500,000 -> 12,500 (AUDIO_FS), split into
        # two stages same as the RF side is -- a single /40 jump would need
        # a filter transition band tighter than a modest tap count can
        # deliver at these rates, aliasing noise back into the audio.
        #
        # This first stage is the critical one: broadcast FM carries more
        # than mono program audio -- a 19kHz stereo pilot tone and a 38kHz
        # L-R DSB-SC subcarrier (itself real audio content, just riding a
        # suppressed carrier) sit right above the ~15kHz program audio
        # band. A loose 63-tap filter here only attenuated the pilot by
        # ~12dB and the subcarrier's lower edge by ~22dB -- not nearly
        # enough, so that leftover multiplex content survived into the
        # decimate-by-8 step, ALIASED down into the 9.5-23kHz range (well
        # inside the audible band), and came out sounding like a second,
        # garbled, unsynchronized "other" signal layered under the real
        # program audio. A steep Kaiser-window filter (~60dB down by
        # 19kHz) kills the whole multiplex before it ever reaches that
        # decimation step. Still under 1.5ms per chunk against a 40ms
        # budget, so the extra taps cost nothing that matters.
        _wfm_audio_a_numtaps, _wfm_audio_a_beta = signal.kaiserord(
            ripple=60, width=(19_000 - 15_000) / (WFM_IF_FS / 2)
        )
        if _wfm_audio_a_numtaps % 2 == 0:
            _wfm_audio_a_numtaps += 1
        self._wfm_audio_a_taps = signal.firwin(
            _wfm_audio_a_numtaps, 15_000, window=("kaiser", _wfm_audio_a_beta), fs=WFM_IF_FS
        ).astype(np.float32)
        self._wfm_audio_b_taps = signal.firwin(63, 5_500, fs=_WFM_AUDIO_A_FS).astype(np.float32)
        self._wfm_audio_a_zi = _zeros_zi(self._wfm_audio_a_taps, complex_=False)
        self._wfm_audio_b_zi = _zeros_zi(self._wfm_audio_b_taps, complex_=False)

        # (taps, zi) per (lo, hi) SSB/CW passband -- taps are cached
        # already (identical filter design each time that key recurs); zi
        # rides along in the same cache entry so USB/LSB/CW each keep their
        # own continuous filter memory independent of the others.
        self._ssb_cache: dict[tuple[float, float], tuple[np.ndarray, np.ndarray]] = {}

        # Decimation-grid phase per stage, threaded across chunks by
        # _decimate() (see there). Keyed by stage rather than kept as six
        # separate attributes; a mode that isn't running just holds a
        # stale phase, which is harmless -- any phase is a valid grid
        # start, and switching mode already clicks.
        self._dec_phase: dict[str, int] = {}

        # AF AGC, applied to every mode's audio as the last step of
        # process() -- see _AudioAgc for why this chain needs one.
        self._agc = _AudioAgc()

        # AM envelope DC-blocker (1-pole high-pass, ~15 Hz corner at AUDIO_FS),
        # state persisted across calls so there's no click at chunk edges.
        alpha = 0.995
        self._am_dc_b = np.array([1.0 - alpha])
        self._am_dc_a = np.array([1.0, -alpha])
        self._am_zi = signal.lfilter_zi(self._am_dc_b, self._am_dc_a) * 0.0

        # FM discriminator DC-blocker: a residual tuning offset (not exactly
        # on the station's center frequency) shows up as a DC bias on the
        # discriminator output. Same 1-pole high-pass shape and same
        # ~15 Hz-at-AUDIO_FS corner as the AM blocker -- applied after
        # stage2 decimation, at AUDIO_FS, same as the AM blocker is.
        self._fm_dc_b = np.array([1.0 - alpha])
        self._fm_dc_a = np.array([1.0, -alpha])
        self._fm_zi = signal.lfilter_zi(self._fm_dc_b, self._fm_dc_a) * 0.0

        # WFM discriminator DC-blocker, same design, own persisted state
        # (independent from FM's -- they run at different times/rates).
        self._wfm_dc_b = np.array([1.0 - alpha])
        self._wfm_dc_a = np.array([1.0, -alpha])
        self._wfm_zi = signal.lfilter_zi(self._wfm_dc_b, self._wfm_dc_a) * 0.0

    def set_mode(self, mode: str) -> None:
        mode = mode.upper()
        if mode not in MODES:
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = mode
        # Modes sit at very different natural levels, so a gain settled on
        # a weak SSB signal would arrive at WFM as an instant blast.
        self._agc.reset()

    @staticmethod
    def _stream(taps: np.ndarray, x: np.ndarray, zi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """lfilter with the delay-line state threaded in and back out, so
        consecutive chunks filter as if they were one continuous signal."""
        return signal.lfilter(taps, [1.0], x, zi=zi)

    @staticmethod
    def _decimate(x: np.ndarray, factor: int, phase: int) -> tuple[np.ndarray, int]:
        """Keep every `factor`-th sample starting at `phase`, and return the
        phase the NEXT chunk has to start at for the decimation grid to stay
        continuous across the join.

        A plain `x[::factor]` restarts that grid at index 0 for every chunk,
        which is only correct while every chunk length is an exact multiple
        of `factor`. `server.py` reads "up to" `max_n` samples from the ring,
        so in general it isn't: WFM's final /5 stage gets 2048-sample chunks
        and emits 410 audio samples where 409.6 is right, advancing the time
        base ~32us at every chunk boundary. Steady tones survive that (they
        have no phase reference to violate), which is why single-tone tests
        looked clean, but real programme material hears it as a click ~30
        times a second and anything above a couple of kHz smears: a 4kHz
        tone measures 21dB down through the chunked path versus a single
        call. Same class of bug as an unthreaded filter delay line, and it
        wants the same fix -- carry the state across.

        Ported from HackRF SDR TRX, where this chain came from and where the
        bug was found (its CHANGELOG.md v0.2.6 has the full measurements).
        """
        return x[phase::factor], (phase - len(x)) % factor

    def _dec(self, key: str, x: np.ndarray, factor: int) -> np.ndarray:
        """`_decimate()` with this stage's phase threaded in and back out,
        so consecutive chunks decimate on one continuous grid -- the same
        shape as `_stream()` doing it for filter state."""
        y, self._dec_phase[key] = self._decimate(x, factor, self._dec_phase.get(key, 0))
        return y

    def _ssb_taps(self, f_lo: float, f_hi: float) -> tuple[np.ndarray, np.ndarray]:
        key = (f_lo, f_hi)
        cached = self._ssb_cache.get(key)
        if cached is not None:
            return cached
        fc = (f_lo + f_hi) / 2.0
        bw = f_hi - f_lo
        numtaps = 127
        lp = signal.firwin(numtaps, bw / 2.0, fs=AUDIO_FS).astype(np.float64)
        n = np.arange(numtaps)
        shift = np.exp(1j * 2 * np.pi * fc * n / AUDIO_FS)
        taps = (lp * shift).astype(np.complex64)
        zi = _zeros_zi(taps, complex_=True)
        entry = (taps, zi)
        self._ssb_cache[key] = entry
        return entry

    def _discriminate(
        self, if_signal: np.ndarray, prev_attr: str, if_fs: float, deviation_ref_hz: float
    ) -> np.ndarray:
        """Phase discriminator with continuity across chunk boundaries: the
        previous chunk's last IF sample is carried in as `prev`, so this
        chunk's first phase-difference sample is computed against real
        history instead of being dropped (and length stays == len(if_signal),
        not len(if_signal) - 1)."""
        prev = getattr(self, prev_attr)
        combined = if_signal if prev is None else np.concatenate((prev, if_signal))
        setattr(self, prev_attr, if_signal[-1:])
        prod = combined[1:] * np.conj(combined[:-1])
        phase = np.angle(prod)
        return np.clip(phase * (if_fs / (2 * np.pi * deviation_ref_hz)), -1.0, 1.0).astype(np.float32)

    def process(self, raw_iq: np.ndarray) -> np.ndarray:
        if len(raw_iq) == 0:
            return np.empty(0, dtype=np.int16)

        if self.mode == "WFM":
            # Own decimation from raw_iq, NOT the shared `stage1` below --
            # that one only preserves +-45kHz, too narrow for a broadcast
            # channel, and computing it here would be wasted work anyway.
            wfm_if, self._wfm_if_zi = self._stream(self._wfm_if_taps, raw_iq, self._wfm_if_zi)
            wfm_if = self._dec("wfm_if", wfm_if, _WFM_IF_FACTOR)
            wide_audio = self._discriminate(wfm_if, "_wfm_prev_if", WFM_IF_FS, 75_000.0)
            audio_a, self._wfm_audio_a_zi = self._stream(self._wfm_audio_a_taps, wide_audio, self._wfm_audio_a_zi)
            audio_a = self._dec("wfm_a", audio_a, _WFM_AUDIO_A_FACTOR)
            audio, self._wfm_audio_b_zi = self._stream(self._wfm_audio_b_taps, audio_a, self._wfm_audio_b_zi)
            audio = self._dec("wfm_b", audio, _WFM_AUDIO_B_FACTOR).astype(np.float64)
            dc_est, self._wfm_zi = signal.lfilter(
                self._wfm_dc_b, self._wfm_dc_a, audio, zi=self._wfm_zi
            )
            audio = (audio - dc_est).astype(np.float32)
            audio = self._agc.process(audio)
            np.clip(audio, -1.0, 1.0, out=audio)
            return (audio * 32767.0).astype(np.int16)

        stage1, self._stage1_zi = self._stream(self._stage1_taps, raw_iq, self._stage1_zi)
        stage1 = self._dec("stage1", stage1, _STAGE1_FACTOR)

        if self.mode == "FM":
            fm_if, self._fm_pre_zi = self._stream(self._fm_pre_taps, stage1, self._fm_pre_zi)
            wide_audio = self._discriminate(fm_if, "_fm_prev_if", INTERMEDIATE_FS, 5000.0)
            audio, self._stage2_zi_fm = self._stream(self._stage2_taps, wide_audio, self._stage2_zi_fm)
            audio = self._dec("stage2_fm", audio, _STAGE2_FACTOR).astype(np.float64)
            dc_est, self._fm_zi = signal.lfilter(
                self._fm_dc_b, self._fm_dc_a, audio, zi=self._fm_zi
            )
            audio = (audio - dc_est).astype(np.float32)
            audio, self._fm_audio_zi = self._stream(self._fm_audio_taps, audio, self._fm_audio_zi)
            audio = audio.astype(np.float32)
        else:
            stage2, self._stage2_zi_main = self._stream(self._stage2_taps, stage1, self._stage2_zi_main)
            stage2 = self._dec("stage2", stage2, _STAGE2_FACTOR)
            if self.mode == "AM":
                env = np.abs(stage2).astype(np.float64)
                dc_est, self._am_zi = signal.lfilter(
                    self._am_dc_b, self._am_dc_a, env, zi=self._am_zi
                )
                audio = ((env - dc_est) * 2.0).astype(np.float32)
            elif self.mode in ("USB", "CW"):
                lo, hi = (300.0, 2700.0) if self.mode == "USB" else (550.0, 850.0)
                taps, zi = self._ssb_taps(lo, hi)
                filtered, zi = self._stream(taps, stage2, zi)
                self._ssb_cache[(lo, hi)] = (taps, zi)
                audio = (filtered.real * 2.0).astype(np.float32)
            elif self.mode == "LSB":
                lo, hi = -2700.0, -300.0
                taps, zi = self._ssb_taps(lo, hi)
                filtered, zi = self._stream(taps, stage2, zi)
                self._ssb_cache[(lo, hi)] = (taps, zi)
                audio = (filtered.real * 2.0).astype(np.float32)
            else:
                audio = np.zeros(len(stage2), dtype=np.float32)

        audio = self._agc.process(audio)
        np.clip(audio, -1.0, 1.0, out=audio)
        return (audio * 32767.0).astype(np.int16)
