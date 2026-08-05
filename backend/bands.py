"""Approximate VK (Australian) amateur band plan for the band-select buttons.

Edges and default calling frequency/mode are deliberately simplified --
close enough to land the VFO in the right segment, not a substitute for the
real ACMA/WIA band plan. All within the RSPduo's 1kHz-2GHz range.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Band:
    name: str
    low_hz: float
    high_hz: float
    default_hz: float
    default_mode: str


BANDS: list[Band] = [
    Band("160m", 1_800_000, 2_000_000, 1_843_000, "LSB"),
    Band("80m", 3_500_000, 3_700_000, 3_600_000, "LSB"),
    Band("40m", 7_000_000, 7_300_000, 7_100_000, "LSB"),
    Band("30m", 10_100_000, 10_150_000, 10_116_000, "CW"),
    Band("20m", 14_000_000, 14_350_000, 14_200_000, "USB"),
    Band("17m", 18_068_000, 18_168_000, 18_130_000, "USB"),
    Band("15m", 21_000_000, 21_450_000, 21_200_000, "USB"),
    Band("12m", 24_890_000, 24_990_000, 24_950_000, "USB"),
    Band("10m", 28_000_000, 29_700_000, 28_400_000, "USB"),
    Band("6m", 50_000_000, 54_000_000, 50_150_000, "USB"),
    Band("2m", 144_000_000, 148_000_000, 146_500_000, "FM"),
    Band("70cm", 430_000_000, 450_000_000, 438_500_000, "FM"),
]


def find_band(freq_hz: float) -> Band | None:
    for b in BANDS:
        if b.low_hz <= freq_hz <= b.high_hz:
            return b
    return None


def to_json() -> list[dict]:
    return [
        {
            "name": b.name,
            "lowHz": b.low_hz,
            "highHz": b.high_hz,
            "defaultHz": b.default_hz,
            "defaultMode": b.default_mode,
        }
        for b in BANDS
    ]
