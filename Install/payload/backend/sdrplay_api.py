"""ctypes binding for SDRplay API v3.15 (sdrplay_api.dll), RSPduo-focused.

Struct layouts are transcribed field-for-field from the vendor headers in
"C:\\Program Files\\SDRplay\\API\\inc\\*.h" (v3.15). ctypes.Structure uses the
platform's natural alignment by default, which matches the MSVC-compiled DLL,
so as long as field order/types match the header exactly, no manual padding
is needed. Only the pieces needed to drive an RSPduo in single-tuner mode are
exposed; other RSP models' structs are still included because they are
unconditionally embedded (by value) in the shared parent structs and affect
struct size/offsets.
"""
from __future__ import annotations

import ctypes as C
import os

# ---------------------------------------------------------------------------
# Constants (sdrplay_api.h)
# ---------------------------------------------------------------------------
SDRPLAY_MAX_DEVICES = 16
SDRPLAY_MAX_SER_NO_LEN = 64

SDRPLAY_RSP1_ID = 1
SDRPLAY_RSP1A_ID = 255
SDRPLAY_RSP2_ID = 2
SDRPLAY_RSPduo_ID = 3
SDRPLAY_RSPdx_ID = 4
SDRPLAY_RSP1B_ID = 6
SDRPLAY_RSPdxR2_ID = 7

# sdrplay_api_ErrT
sdrplay_api_Success = 0
sdrplay_api_Fail = 1

# sdrplay_api_ReasonForUpdateT (bitmask)
sdrplay_api_Update_None = 0x00000000
sdrplay_api_Update_Dev_Fs = 0x00000001
sdrplay_api_Update_Tuner_Gr = 0x00008000
sdrplay_api_Update_Tuner_GrLimits = 0x00010000
sdrplay_api_Update_Tuner_Frf = 0x00020000
sdrplay_api_Update_Tuner_BwType = 0x00040000
sdrplay_api_Update_Tuner_IfType = 0x00080000
sdrplay_api_Update_Ctrl_Decimation = 0x00800000
sdrplay_api_Update_Ctrl_Agc = 0x01000000
sdrplay_api_Update_Ctrl_OverloadMsgAck = 0x04000000

sdrplay_api_Update_Ext1_None = 0x00000000

# sdrplay_api_TunerSelectT
sdrplay_api_Tuner_Neither = 0
sdrplay_api_Tuner_A = 1
sdrplay_api_Tuner_B = 2
sdrplay_api_Tuner_Both = 3

# sdrplay_api_RspDuoModeT
sdrplay_api_RspDuoMode_Unknown = 0
sdrplay_api_RspDuoMode_Single_Tuner = 1
sdrplay_api_RspDuoMode_Dual_Tuner = 2
sdrplay_api_RspDuoMode_Master = 4
sdrplay_api_RspDuoMode_Slave = 8

# sdrplay_api_RspDuo_AmPortSelectT
sdrplay_api_RspDuo_AMPORT_1 = 1
sdrplay_api_RspDuo_AMPORT_2 = 0

# sdrplay_api_Bw_MHzT
sdrplay_api_BW_Undefined = 0
sdrplay_api_BW_0_200 = 200
sdrplay_api_BW_0_300 = 300
sdrplay_api_BW_0_600 = 600
sdrplay_api_BW_1_536 = 1536
sdrplay_api_BW_5_000 = 5000
sdrplay_api_BW_6_000 = 6000
sdrplay_api_BW_7_000 = 7000
sdrplay_api_BW_8_000 = 8000

# sdrplay_api_If_kHzT
sdrplay_api_IF_Undefined = -1
sdrplay_api_IF_Zero = 0
sdrplay_api_IF_0_450 = 450
sdrplay_api_IF_1_620 = 1620
sdrplay_api_IF_2_048 = 2048

# sdrplay_api_LoModeT
sdrplay_api_LO_Auto = 1

# sdrplay_api_MinGainReductionT
sdrplay_api_NORMAL_MIN_GR = 20

# sdrplay_api_AgcControlT
sdrplay_api_AGC_DISABLE = 0
sdrplay_api_AGC_50HZ = 2

# sdrplay_api_TransferModeT
sdrplay_api_ISOCH = 0
sdrplay_api_BULK = 1

# sdrplay_api_EventT
sdrplay_api_GainChange = 0
sdrplay_api_PowerOverloadChange = 1
sdrplay_api_DeviceRemoved = 2
sdrplay_api_RspDuoModeChange = 3
sdrplay_api_DeviceFailure = 4

sdrplay_api_Overload_Detected = 0
sdrplay_api_Overload_Corrected = 1


# ---------------------------------------------------------------------------
# tuner.h
# ---------------------------------------------------------------------------
class sdrplay_api_GainValuesT(C.Structure):
    _fields_ = [("curr", C.c_float), ("max", C.c_float), ("min", C.c_float)]


class sdrplay_api_GainT(C.Structure):
    _fields_ = [
        ("gRdB", C.c_int),
        ("LNAstate", C.c_ubyte),
        ("syncUpdate", C.c_ubyte),
        ("minGr", C.c_int),
        ("gainVals", sdrplay_api_GainValuesT),
    ]


class sdrplay_api_RfFreqT(C.Structure):
    _fields_ = [("rfHz", C.c_double), ("syncUpdate", C.c_ubyte)]


class sdrplay_api_DcOffsetTunerT(C.Structure):
    _fields_ = [
        ("dcCal", C.c_ubyte),
        ("speedUp", C.c_ubyte),
        ("trackTime", C.c_int),
        ("refreshRateTime", C.c_int),
    ]


class sdrplay_api_TunerParamsT(C.Structure):
    _fields_ = [
        ("bwType", C.c_int),
        ("ifType", C.c_int),
        ("loMode", C.c_int),
        ("gain", sdrplay_api_GainT),
        ("rfFreq", sdrplay_api_RfFreqT),
        ("dcOffsetTuner", sdrplay_api_DcOffsetTunerT),
    ]


# ---------------------------------------------------------------------------
# control.h
# ---------------------------------------------------------------------------
class sdrplay_api_DcOffsetT(C.Structure):
    _fields_ = [("DCenable", C.c_ubyte), ("IQenable", C.c_ubyte)]


class sdrplay_api_DecimationT(C.Structure):
    _fields_ = [
        ("enable", C.c_ubyte),
        ("decimationFactor", C.c_ubyte),
        ("wideBandSignal", C.c_ubyte),
    ]


class sdrplay_api_AgcT(C.Structure):
    _fields_ = [
        ("enable", C.c_int),
        ("setPoint_dBfs", C.c_int),
        ("attack_ms", C.c_ushort),
        ("decay_ms", C.c_ushort),
        ("decay_delay_ms", C.c_ushort),
        ("decay_threshold_dB", C.c_ushort),
        ("syncUpdate", C.c_int),
    ]


class sdrplay_api_ControlParamsT(C.Structure):
    _fields_ = [
        ("dcOffset", sdrplay_api_DcOffsetT),
        ("decimation", sdrplay_api_DecimationT),
        ("agc", sdrplay_api_AgcT),
        ("adsbMode", C.c_int),
    ]


# ---------------------------------------------------------------------------
# rsp1a.h / rsp2.h / rspDx.h (embedded by value in shared structs)
# ---------------------------------------------------------------------------
class sdrplay_api_Rsp1aParamsT(C.Structure):
    _fields_ = [("rfNotchEnable", C.c_ubyte), ("rfDabNotchEnable", C.c_ubyte)]


class sdrplay_api_Rsp1aTunerParamsT(C.Structure):
    _fields_ = [("biasTEnable", C.c_ubyte)]


class sdrplay_api_Rsp2ParamsT(C.Structure):
    _fields_ = [("extRefOutputEn", C.c_ubyte)]


class sdrplay_api_Rsp2TunerParamsT(C.Structure):
    _fields_ = [
        ("biasTEnable", C.c_ubyte),
        ("amPortSel", C.c_int),
        ("antennaSel", C.c_int),
        ("rfNotchEnable", C.c_ubyte),
    ]


class sdrplay_api_RspDxParamsT(C.Structure):
    _fields_ = [
        ("hdrEnable", C.c_ubyte),
        ("biasTEnable", C.c_ubyte),
        ("antennaSel", C.c_int),
        ("rfNotchEnable", C.c_ubyte),
        ("rfDabNotchEnable", C.c_ubyte),
    ]


class sdrplay_api_RspDxTunerParamsT(C.Structure):
    _fields_ = [("hdrBw", C.c_int)]


# ---------------------------------------------------------------------------
# rspDuo.h
# ---------------------------------------------------------------------------
class sdrplay_api_RspDuoParamsT(C.Structure):
    _fields_ = [("extRefOutputEn", C.c_int)]


class sdrplay_api_RspDuo_ResetSlaveFlagsT(C.Structure):
    _fields_ = [("resetGainUpdate", C.c_ubyte), ("resetRfUpdate", C.c_ubyte)]


class sdrplay_api_RspDuoTunerParamsT(C.Structure):
    _fields_ = [
        ("biasTEnable", C.c_ubyte),
        ("tuner1AmPortSel", C.c_int),
        ("tuner1AmNotchEnable", C.c_ubyte),
        ("rfNotchEnable", C.c_ubyte),
        ("rfDabNotchEnable", C.c_ubyte),
        ("resetSlaveFlags", sdrplay_api_RspDuo_ResetSlaveFlagsT),
    ]


# ---------------------------------------------------------------------------
# rx_channel.h
# ---------------------------------------------------------------------------
class sdrplay_api_RxChannelParamsT(C.Structure):
    _fields_ = [
        ("tunerParams", sdrplay_api_TunerParamsT),
        ("ctrlParams", sdrplay_api_ControlParamsT),
        ("rsp1aTunerParams", sdrplay_api_Rsp1aTunerParamsT),
        ("rsp2TunerParams", sdrplay_api_Rsp2TunerParamsT),
        ("rspDuoTunerParams", sdrplay_api_RspDuoTunerParamsT),
        ("rspDxTunerParams", sdrplay_api_RspDxTunerParamsT),
    ]


# ---------------------------------------------------------------------------
# dev.h
# ---------------------------------------------------------------------------
class sdrplay_api_FsFreqT(C.Structure):
    _fields_ = [("fsHz", C.c_double), ("syncUpdate", C.c_ubyte), ("reCal", C.c_ubyte)]


class sdrplay_api_SyncUpdateT(C.Structure):
    _fields_ = [("sampleNum", C.c_uint), ("period", C.c_uint)]


class sdrplay_api_ResetFlagsT(C.Structure):
    _fields_ = [
        ("resetGainUpdate", C.c_ubyte),
        ("resetRfUpdate", C.c_ubyte),
        ("resetFsUpdate", C.c_ubyte),
    ]


class sdrplay_api_DevParamsT(C.Structure):
    _fields_ = [
        ("ppm", C.c_double),
        ("fsFreq", sdrplay_api_FsFreqT),
        ("syncUpdate", sdrplay_api_SyncUpdateT),
        ("resetFlags", sdrplay_api_ResetFlagsT),
        ("mode", C.c_int),
        ("samplesPerPkt", C.c_uint),
        ("rsp1aParams", sdrplay_api_Rsp1aParamsT),
        ("rsp2Params", sdrplay_api_Rsp2ParamsT),
        ("rspDuoParams", sdrplay_api_RspDuoParamsT),
        ("rspDxParams", sdrplay_api_RspDxParamsT),
    ]


# ---------------------------------------------------------------------------
# sdrplay_api.h top-level structs
# ---------------------------------------------------------------------------
class sdrplay_api_DeviceT(C.Structure):
    _fields_ = [
        ("SerNo", C.c_char * SDRPLAY_MAX_SER_NO_LEN),
        ("hwVer", C.c_ubyte),
        ("tuner", C.c_int),
        ("rspDuoMode", C.c_int),
        ("valid", C.c_ubyte),
        ("rspDuoSampleFreq", C.c_double),
        ("dev", C.c_void_p),
    ]


class sdrplay_api_DeviceParamsT(C.Structure):
    _fields_ = [
        ("devParams", C.POINTER(sdrplay_api_DevParamsT)),
        ("rxChannelA", C.POINTER(sdrplay_api_RxChannelParamsT)),
        ("rxChannelB", C.POINTER(sdrplay_api_RxChannelParamsT)),
    ]


class sdrplay_api_ErrorInfoT(C.Structure):
    _fields_ = [
        ("file", C.c_char * 256),
        ("function", C.c_char * 256),
        ("line", C.c_int),
        ("message", C.c_char * 1024),
    ]


# ---------------------------------------------------------------------------
# callback.h
# ---------------------------------------------------------------------------
class sdrplay_api_GainCbParamT(C.Structure):
    _fields_ = [("gRdB", C.c_uint), ("lnaGRdB", C.c_uint), ("currGain", C.c_double)]


class sdrplay_api_PowerOverloadCbParamT(C.Structure):
    _fields_ = [("powerOverloadChangeType", C.c_int)]


class sdrplay_api_RspDuoModeCbParamT(C.Structure):
    _fields_ = [("modeChangeType", C.c_int)]


class sdrplay_api_EventParamsT(C.Union):
    _fields_ = [
        ("gainParams", sdrplay_api_GainCbParamT),
        ("powerOverloadParams", sdrplay_api_PowerOverloadCbParamT),
        ("rspDuoModeParams", sdrplay_api_RspDuoModeCbParamT),
    ]


class sdrplay_api_StreamCbParamsT(C.Structure):
    _fields_ = [
        ("firstSampleNum", C.c_uint),
        ("grChanged", C.c_int),
        ("rfChanged", C.c_int),
        ("fsChanged", C.c_int),
        ("numSamples", C.c_uint),
    ]


StreamCallback_t = C.CFUNCTYPE(
    None,
    C.POINTER(C.c_short),
    C.POINTER(C.c_short),
    C.POINTER(sdrplay_api_StreamCbParamsT),
    C.c_uint,
    C.c_uint,
    C.c_void_p,
)
EventCallback_t = C.CFUNCTYPE(
    None, C.c_int, C.c_int, C.POINTER(sdrplay_api_EventParamsT), C.c_void_p
)


class sdrplay_api_CallbackFnsT(C.Structure):
    _fields_ = [
        ("StreamACbFn", StreamCallback_t),
        ("StreamBCbFn", StreamCallback_t),
        ("EventCbFn", EventCallback_t),
    ]


# ---------------------------------------------------------------------------
# DLL loading + function prototypes
# ---------------------------------------------------------------------------
_DLL_CANDIDATES = [
    r"C:\Program Files\SDRplay\API\x64\sdrplay_api.dll",
    r"C:\Program Files (x86)\SDRplay\API\x64\sdrplay_api.dll",
]


def _load_dll() -> C.CDLL:
    for path in _DLL_CANDIDATES:
        if os.path.exists(path):
            return C.CDLL(path)
    raise FileNotFoundError(
        "sdrplay_api.dll not found. Is the SDRplay API installed? "
        f"Checked: {_DLL_CANDIDATES}"
    )


dll = _load_dll()

dll.sdrplay_api_Open.restype = C.c_int
dll.sdrplay_api_Open.argtypes = []

dll.sdrplay_api_Close.restype = C.c_int
dll.sdrplay_api_Close.argtypes = []

dll.sdrplay_api_ApiVersion.restype = C.c_int
dll.sdrplay_api_ApiVersion.argtypes = [C.POINTER(C.c_float)]

dll.sdrplay_api_LockDeviceApi.restype = C.c_int
dll.sdrplay_api_LockDeviceApi.argtypes = []

dll.sdrplay_api_UnlockDeviceApi.restype = C.c_int
dll.sdrplay_api_UnlockDeviceApi.argtypes = []

dll.sdrplay_api_GetDevices.restype = C.c_int
dll.sdrplay_api_GetDevices.argtypes = [
    C.POINTER(sdrplay_api_DeviceT),
    C.POINTER(C.c_uint),
    C.c_uint,
]

dll.sdrplay_api_SelectDevice.restype = C.c_int
dll.sdrplay_api_SelectDevice.argtypes = [C.POINTER(sdrplay_api_DeviceT)]

dll.sdrplay_api_ReleaseDevice.restype = C.c_int
dll.sdrplay_api_ReleaseDevice.argtypes = [C.POINTER(sdrplay_api_DeviceT)]

dll.sdrplay_api_GetErrorString.restype = C.c_char_p
dll.sdrplay_api_GetErrorString.argtypes = [C.c_int]

dll.sdrplay_api_GetLastError.restype = C.POINTER(sdrplay_api_ErrorInfoT)
dll.sdrplay_api_GetLastError.argtypes = [C.POINTER(sdrplay_api_DeviceT)]

dll.sdrplay_api_GetDeviceParams.restype = C.c_int
dll.sdrplay_api_GetDeviceParams.argtypes = [
    C.c_void_p,
    C.POINTER(C.POINTER(sdrplay_api_DeviceParamsT)),
]

dll.sdrplay_api_Init.restype = C.c_int
dll.sdrplay_api_Init.argtypes = [
    C.c_void_p,
    C.POINTER(sdrplay_api_CallbackFnsT),
    C.c_void_p,
]

dll.sdrplay_api_Uninit.restype = C.c_int
dll.sdrplay_api_Uninit.argtypes = [C.c_void_p]

dll.sdrplay_api_Update.restype = C.c_int
dll.sdrplay_api_Update.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int]


def err_str(err: int) -> str:
    s = dll.sdrplay_api_GetErrorString(err)
    return s.decode("ascii", "replace") if s else f"<err {err}>"


class SdrplayError(RuntimeError):
    def __init__(self, call: str, err: int):
        super().__init__(f"{call} failed: {err_str(err)} ({err})")
        self.err = err


def check(call: str, err: int) -> None:
    if err != sdrplay_api_Success:
        raise SdrplayError(call, err)
