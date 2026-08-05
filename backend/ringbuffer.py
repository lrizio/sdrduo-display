"""Thread-safe circular buffer for complex IQ samples.

Written from the SDRplay driver's real-time streaming callback thread;
read from the spectrum and demodulator worker threads. The write side is a
single writer, so writes only need to protect the copy itself; readers take
the same lock to get a consistent snapshot of `write_pos` before copying.
"""
from __future__ import annotations

import threading

import numpy as np


class IQRingBuffer:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self._buf = np.zeros(capacity, dtype=np.complex64)
        self._write_pos = 0  # monotonically increasing total sample count
        self._lock = threading.Lock()

    def write(self, chunk: np.ndarray) -> None:
        n = len(chunk)
        if n == 0:
            return
        if n >= self.capacity:
            chunk = chunk[-self.capacity :]
            n = len(chunk)
        with self._lock:
            start = self._write_pos % self.capacity
            end = start + n
            if end <= self.capacity:
                self._buf[start:end] = chunk
            else:
                first = self.capacity - start
                self._buf[start:] = chunk[:first]
                self._buf[: end - self.capacity] = chunk[first:]
            self._write_pos += n

    @property
    def write_pos(self) -> int:
        with self._lock:
            return self._write_pos

    def read_latest(self, n: int) -> np.ndarray:
        """Most recent `n` samples, for a spectrum snapshot."""
        n = min(n, self.capacity)
        with self._lock:
            wp = self._write_pos
            start = (wp - n) % self.capacity
            if start + n <= self.capacity:
                return self._buf[start : start + n].copy()
            first = self.capacity - start
            out = np.empty(n, dtype=np.complex64)
            out[:first] = self._buf[start:]
            out[first:] = self._buf[: n - first]
            return out

    def read_since(self, read_pos: int, max_n: int) -> tuple[np.ndarray, int]:
        """Samples written since `read_pos` (a previously returned position),
        capped at `max_n`, and the new read position to pass next time."""
        with self._lock:
            wp = self._write_pos
            avail = wp - read_pos
            if avail <= 0:
                return np.empty(0, dtype=np.complex64), read_pos
            if avail > self.capacity:
                # Reader fell behind further than the buffer holds; drop
                # the data we can no longer recover and resync.
                read_pos = wp - self.capacity
                avail = self.capacity
            n = min(avail, max_n)
            start = read_pos % self.capacity
            if start + n <= self.capacity:
                out = self._buf[start : start + n].copy()
            else:
                first = self.capacity - start
                out = np.empty(n, dtype=np.complex64)
                out[:first] = self._buf[start:]
                out[first:] = self._buf[: n - first]
            return out, read_pos + n
