"""Equipment type library.

One entry per equipment type in the machine spec. Each entry declares the I/O
signals the type needs, the library FB that drives it, and how that FB is called.
Adding a new equipment type means adding an SCL FB under ``library/scl/`` and one
entry here - nothing else in the generator is type-aware.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# I/O kinds. DI/DO are bit addressed, AI/AO are word addressed.
DI, DO, AI, AO = "DI", "DO", "AI", "AO"

BIT_KINDS = (DI, DO)
WORD_KINDS = (AI, AO)

# Which PLC tag table a signal lands in, and the address prefix it uses.
KIND_TABLE = {DI: "Inputs", DO: "Outputs", AI: "AnalogInputs", AO: "AnalogOutputs"}
KIND_PREFIX = {DI: "%I", DO: "%Q", AI: "%IW", AO: "%QW"}
KIND_DATATYPE = {DI: "Bool", DO: "Bool", AI: "Int", AO: "Int"}


@dataclass(frozen=True)
class Signal:
    """One physical I/O point belonging to a piece of equipment."""

    role: str  # stable key, also used for per-signal address overrides
    kind: str  # DI / DO / AI / AO
    suffix: str  # appended to the equipment name to form the tag name
    comment: str
    option: Optional[str] = None  # spec option gating this signal
    default: bool = True  # value of that option when unset


@dataclass(frozen=True)
class EquipmentType:
    name: str
    signals: List[Signal]
    fb: Optional[str]  # library FB, or None for tag-only types
    summary: str
    # Builds the SCL parameter list for the FB call. Signature: (equip, ctx) -> list[str]
    call: Optional[Callable] = None
    alarm_roles: List[str] = field(default_factory=list)


def _sig(role, kind, suffix, comment, option=None, default=True):
    return Signal(role, kind, suffix, comment, option, default)


def _fmt_time(ms: int) -> str:
    """Render milliseconds as an SCL TIME literal."""
    return f"T#{int(ms)}MS"


# --------------------------------------------------------------------------
# Parameter builders. `ctx` carries the resolved names the machine FB uses:
#   ctx["enable"], ctx["manual"], ctx["reset"]  - locals from the mode manager
#   ctx["tag"](role)                            - quoted PLC tag for a signal
#   ctx["has"](role)                            - whether that signal exists
#   ctx["auto"]                                 - "#Auto." prefix for requests
#   ctx["interlock"]                            - SCL boolean expression
# --------------------------------------------------------------------------
def _call_motor(eq, ctx):
    o = eq.options
    p = [
        f"Enable := {ctx['enable']}",
        f"Interlock := {ctx['interlock']}",
        f"AutoStart := {ctx['auto']}{eq.name}_Start",
        f"AutoStop := {ctx['auto']}{eq.name}_Stop",
        f"ManualMode := {ctx['manual']}",
        f"GroupReset := {ctx['reset']}",
    ]
    if ctx["has"]("running_fb"):
        p.append(f"RunningFb := {ctx['tag']('running_fb')}")
    if ctx["has"]("fault_fb"):
        p.append(f"FaultFb := {ctx['tag']('fault_fb')}")
    p.append(f"UseRunningFb := {_scl_bool(ctx['has']('running_fb'))}")
    p.append(f"UseFaultFb := {_scl_bool(ctx['has']('fault_fb'))}")
    p.append(f"FbTimeout := {_fmt_time(o.get('feedback_timeout_ms', 3000))}")
    p.append(f"RunOut => {ctx['tag']('run_out')}")
    return p


def _call_motor_rev(eq, ctx):
    o = eq.options
    p = [
        f"Enable := {ctx['enable']}",
        f"InterlockFwd := {ctx['interlock']}",
        f"InterlockRev := {ctx['interlock']}",
        f"AutoFwd := {ctx['auto']}{eq.name}_Fwd",
        f"AutoRev := {ctx['auto']}{eq.name}_Rev",
        f"ManualMode := {ctx['manual']}",
        f"GroupReset := {ctx['reset']}",
    ]
    if ctx["has"]("running_fb"):
        p.append(f"RunningFb := {ctx['tag']('running_fb')}")
    if ctx["has"]("fault_fb"):
        p.append(f"FaultFb := {ctx['tag']('fault_fb')}")
    p.append(f"UseRunningFb := {_scl_bool(ctx['has']('running_fb'))}")
    p.append(f"UseFaultFb := {_scl_bool(ctx['has']('fault_fb'))}")
    p.append(f"FbTimeout := {_fmt_time(o.get('feedback_timeout_ms', 3000))}")
    p.append(f"FwdOut => {ctx['tag']('fwd_out')}")
    p.append(f"RevOut => {ctx['tag']('rev_out')}")
    return p


def _call_vfd(eq, ctx):
    o = eq.options
    sc = eq.scaling
    p = [
        f"Enable := {ctx['enable']}",
        f"Interlock := {ctx['interlock']}",
        f"AutoStart := {ctx['auto']}{eq.name}_Start",
        f"AutoStop := {ctx['auto']}{eq.name}_Stop",
        f"AutoSpeed := {ctx['auto']}{eq.name}_Speed",
        f"ManualMode := {ctx['manual']}",
        f"GroupReset := {ctx['reset']}",
    ]
    if ctx["has"]("running_fb"):
        p.append(f"RunningFb := {ctx['tag']('running_fb')}")
    if ctx["has"]("fault_fb"):
        p.append(f"FaultFb := {ctx['tag']('fault_fb')}")
    p.append(f"UseRunningFb := {_scl_bool(ctx['has']('running_fb'))}")
    p.append(f"UseFaultFb := {_scl_bool(ctx['has']('fault_fb'))}")
    p.append(f"FbTimeout := {_fmt_time(o.get('feedback_timeout_ms', 5000))}")
    p.append(f"SpeedMin := {_r(sc['eng_min'])}")
    p.append(f"SpeedMax := {_r(sc['eng_max'])}")
    p.append(f"RawMin := {_r(sc['raw_min'])}")
    p.append(f"RawMax := {_r(sc['raw_max'])}")
    p.append(f"SpeedRaw => {ctx['tag']('speed_out')}")
    p.append(f"RunOut => {ctx['tag']('run_out')}")
    return p


def _call_valve(eq, ctx):
    o = eq.options
    double = eq.type == "valve_double"
    p = [
        f"Enable := {ctx['enable']}",
        f"Interlock := {ctx['interlock']}",
        f"AutoOpen := {ctx['auto']}{eq.name}_Open",
        f"ManualMode := {ctx['manual']}",
        f"GroupReset := {ctx['reset']}",
    ]
    if ctx["has"]("opened_fb"):
        p.append(f"OpenedFb := {ctx['tag']('opened_fb')}")
    if ctx["has"]("closed_fb"):
        p.append(f"ClosedFb := {ctx['tag']('closed_fb')}")
    p.append(f"UseOpenedFb := {_scl_bool(ctx['has']('opened_fb'))}")
    p.append(f"UseClosedFb := {_scl_bool(ctx['has']('closed_fb'))}")
    p.append(f"DoubleActing := {_scl_bool(double)}")
    p.append(f"TravelTime := {_fmt_time(o.get('travel_timeout_ms', 5000))}")
    p.append(f"OpenOut => {ctx['tag']('open_out')}")
    if double:
        p.append(f"CloseOut => {ctx['tag']('close_out')}")
    return p


def _call_analog_in(eq, ctx):
    sc = eq.scaling
    use_limits = any(sc.get(k) is not None for k in ("lo_lo", "lo", "hi", "hi_hi"))
    p = [
        f"Raw := {ctx['tag']('in')}",
        f"RawMin := {_r(sc['raw_min'])}",
        f"RawMax := {_r(sc['raw_max'])}",
        f"EngMin := {_r(sc['eng_min'])}",
        f"EngMax := {_r(sc['eng_max'])}",
        f"UseLimits := {_scl_bool(use_limits)}",
        f"GroupReset := {ctx['reset']}",
    ]
    if use_limits:
        p.insert(5, f"LimitLoLo := {_r(sc.get('lo_lo', sc['eng_min']))}")
        p.insert(6, f"LimitLo := {_r(sc.get('lo', sc['eng_min']))}")
        p.insert(7, f"LimitHi := {_r(sc.get('hi', sc['eng_max']))}")
        p.insert(8, f"LimitHiHi := {_r(sc.get('hi_hi', sc['eng_max']))}")
    return p


def _call_analog_out(eq, ctx):
    sc = eq.scaling
    return [
        f"Enable := {ctx['enable']}",
        f"AutoValue := {ctx['auto']}{eq.name}_Value",
        f"ManualMode := {ctx['manual']}",
        f"EngMin := {_r(sc['eng_min'])}",
        f"EngMax := {_r(sc['eng_max'])}",
        f"RawMin := {_r(sc['raw_min'])}",
        f"RawMax := {_r(sc['raw_max'])}",
        f"Raw => {ctx['tag']('out')}",
    ]


def _call_digital_out(eq, ctx):
    o = eq.options
    return [
        f"Enable := {ctx['enable']}",
        f"Interlock := {ctx['interlock']}",
        f"AutoCmd := {ctx['auto']}{eq.name}_Cmd",
        f"ManualMode := {ctx['manual']}",
        f"Invert := {_scl_bool(o.get('normally_closed', False))}",
        f"Out => {ctx['tag']('out')}",
    ]


def _scl_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _r(value) -> str:
    """Render a number as an SCL REAL literal (always with a decimal point)."""
    text = repr(float(value))
    return text if ("." in text or "e" in text or "E" in text) else text + ".0"


# --------------------------------------------------------------------------
# The type table
# --------------------------------------------------------------------------
TYPES: Dict[str, EquipmentType] = {
    "digital_input": EquipmentType(
        name="digital_input",
        signals=[_sig("in", DI, "", "digital input")],
        fb=None,
        summary="Single digital input (sensor, pushbutton, switch).",
    ),
    "digital_output": EquipmentType(
        name="digital_output",
        signals=[_sig("out", DO, "", "digital output")],
        fb="FB_DigitalOut",
        summary="Single digital output (lamp, horn, simple solenoid).",
        call=_call_digital_out,
    ),
    "motor_dol": EquipmentType(
        name="motor_dol",
        signals=[
            _sig("run_out", DO, "_Run", "contactor coil"),
            _sig("running_fb", DI, "_Running", "aux contact", "has_running_feedback"),
            _sig("fault_fb", DI, "_Fault", "overload contact", "has_fault_feedback"),
        ],
        fb="FB_Motor",
        summary="Direct-on-line motor: one run output, optional running and fault feedback.",
        call=_call_motor,
        alarm_roles=["fault"],
    ),
    "motor_reversing": EquipmentType(
        name="motor_reversing",
        signals=[
            _sig("fwd_out", DO, "_Fwd", "forward contactor"),
            _sig("rev_out", DO, "_Rev", "reverse contactor"),
            _sig("running_fb", DI, "_Running", "aux contact", "has_running_feedback"),
            _sig("fault_fb", DI, "_Fault", "overload contact", "has_fault_feedback"),
        ],
        fb="FB_MotorRev",
        summary="Reversing motor with interlocked contactors and change-over dead time.",
        call=_call_motor_rev,
        alarm_roles=["fault"],
    ),
    "vfd_analog": EquipmentType(
        name="vfd_analog",
        signals=[
            _sig("run_out", DO, "_Run", "drive run/enable"),
            _sig("speed_out", AO, "_Speed", "speed reference"),
            _sig("running_fb", DI, "_Running", "drive running", "has_running_feedback"),
            _sig("fault_fb", DI, "_Fault", "drive fault", "has_fault_feedback"),
        ],
        fb="FB_Vfd",
        summary="VFD driven by a hardwired run output and an analog speed reference.",
        call=_call_vfd,
        alarm_roles=["fault"],
    ),
    "valve_single": EquipmentType(
        name="valve_single",
        signals=[
            _sig("open_out", DO, "_Open", "solenoid coil"),
            _sig("opened_fb", DI, "_Opened", "open limit switch", "has_opened_feedback"),
            _sig("closed_fb", DI, "_Closed", "closed limit switch", "has_closed_feedback"),
        ],
        fb="FB_Valve",
        summary="Single-acting (spring return) valve, one coil.",
        call=_call_valve,
        alarm_roles=["fault"],
    ),
    "valve_double": EquipmentType(
        name="valve_double",
        signals=[
            _sig("open_out", DO, "_Open", "open coil"),
            _sig("close_out", DO, "_Close", "close coil"),
            _sig("opened_fb", DI, "_Opened", "open limit switch", "has_opened_feedback"),
            _sig("closed_fb", DI, "_Closed", "closed limit switch", "has_closed_feedback"),
        ],
        fb="FB_Valve",
        summary="Double-acting valve, separate open and close coils.",
        call=_call_valve,
        alarm_roles=["fault"],
    ),
    "analog_input": EquipmentType(
        name="analog_input",
        signals=[_sig("in", AI, "", "analog input")],
        fb="FB_AnalogIn",
        summary="Scaled analog measurement with range check and limit monitoring.",
        call=_call_analog_in,
        alarm_roles=["fault", "limits"],
    ),
    "analog_output": EquipmentType(
        name="analog_output",
        signals=[_sig("out", AO, "", "analog output")],
        fb="FB_AnalogOut",
        summary="Scaled analog output.",
        call=_call_analog_out,
    ),
    "estop": EquipmentType(
        name="estop",
        signals=[_sig("ok_fb", DI, "_Ok", "E-stop status contact (NC, TRUE = released)")],
        fb=None,
        summary="E-stop STATUS input for the standard program. Not a safety function.",
    ),
    "safety_gate": EquipmentType(
        name="safety_gate",
        signals=[_sig("ok_fb", DI, "_Ok", "guard status contact (NC, TRUE = closed)")],
        fb=None,
        summary="Guard door STATUS input for the standard program. Not a safety function.",
    ),
}

# Library SCL files, imported in this order (types before the blocks that use them).
LIBRARY_FILES = [
    "10_UDT_DevIf.scl",
    "20_FB_Motor.scl",
    "21_FB_MotorRev.scl",
    "22_FB_Valve.scl",
    "23_FB_Vfd.scl",
    "24_FB_AnalogIn.scl",
    "25_FB_AnalogOut.scl",
    "26_FB_DigitalOut.scl",
    "27_FB_ModeManager.scl",
]

SAFETY_TYPES = ("estop", "safety_gate")
