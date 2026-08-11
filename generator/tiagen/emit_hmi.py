"""HMI artefacts: tag list, alarm list and a concrete screen plan.

The Openness driver creates the HMI tag tables, the PLC/HMI connection and the
(empty) screens through the API. Screen *content* is emitted here as an explicit
plan: every object with its position, size and tag binding. That plan is what a
screen generator, SiVArc, or an engineer with the screen editor open works from.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .model import Equipment, Spec

# Faceplate tile geometry, in pixels.
TILE_W, TILE_H = 420, 150
MARGIN, GAP = 24, 16
HEADER_H = 90

# Which HMI tags each equipment type contributes: (suffix, member path, datatype, access)
DEVICE_TAGS = {
    "common": [
        ("Sts_Running", "Hmi.Sts_Running", "Bool", "r"),
        ("Sts_Fault", "Hmi.Sts_Fault", "Bool", "r"),
        ("Sts_FaultCode", "Hmi.Sts_FaultCode", "Int", "r"),
        ("Sts_Interlocked", "Hmi.Sts_Interlocked", "Bool", "r"),
        ("Sts_Enabled", "Hmi.Sts_Enabled", "Bool", "r"),
        ("Sts_ManualMode", "Hmi.Sts_ManualMode", "Bool", "r"),
        ("Cmd_Reset", "Hmi.Cmd_Reset", "Bool", "w"),
    ],
    "startstop": [
        ("Cmd_Start", "Hmi.Cmd_Start", "Bool", "w"),
        ("Cmd_Stop", "Hmi.Cmd_Stop", "Bool", "w"),
    ],
    "openclose": [
        ("Cmd_Open", "Hmi.Cmd_Open", "Bool", "w"),
        ("Cmd_Close", "Hmi.Cmd_Close", "Bool", "w"),
        ("Sts_Opened", "Hmi.Sts_Running", "Bool", "r"),
        ("Sts_Closed", "Hmi.Sts_Stopped", "Bool", "r"),
    ],
    "setpoint": [
        ("Set_Value", "Hmi.Set_Value", "Real", "rw"),
        ("Act_Value", "Hmi.Act_Value", "Real", "r"),
    ],
    "measure": [
        ("Act_Value", "Hmi.Act_Value", "Real", "r"),
    ],
}

TYPE_TAG_GROUPS = {
    "motor_dol": ["common", "startstop"],
    "motor_reversing": ["common", "startstop", "openclose"],
    "vfd_analog": ["common", "startstop", "setpoint"],
    "valve_single": ["common", "openclose"],
    "valve_double": ["common", "openclose"],
    "analog_input": ["measure"],
    "analog_output": ["setpoint"],
    "digital_output": ["common", "startstop"],
}

# Faceplate kind used on the manual screens, per equipment type.
FACEPLATE = {
    "motor_dol": "FP_Motor",
    "motor_reversing": "FP_MotorRev",
    "vfd_analog": "FP_Vfd",
    "valve_single": "FP_Valve",
    "valve_double": "FP_Valve",
    "analog_input": "FP_AnalogIn",
    "analog_output": "FP_AnalogOut",
    "digital_output": "FP_DigitalOut",
}


def _screen_size(spec: Spec):
    raw = (spec.hmi or {}).get("resolution", "1920x1080")
    try:
        w, h = raw.lower().split("x")
        return int(w), int(h)
    except (ValueError, AttributeError):
        return 1920, 1080


def hmi_tags(spec: Spec) -> List[Dict[str, Any]]:
    """Every HMI tag, with its symbolic PLC address."""
    db = spec.db_machine
    tags: List[Dict[str, Any]] = [
        {"name": "Machine_State", "datatype": "Int", "plc_tag": f"{db}.State",
         "access": "r", "table": "Machine", "comment": "mode manager state 0..5"},
        {"name": "Machine_AutoRun", "datatype": "Bool", "plc_tag": f"{db}.AutoRun",
         "access": "r", "table": "Machine", "comment": "auto sequence running"},
        {"name": "Machine_AnyFault", "datatype": "Bool", "plc_tag": f"{db}.AnyFault",
         "access": "r", "table": "Machine", "comment": "at least one alarm active"},
    ]
    for member in ("Start", "Stop", "Reset", "ReqManual", "ReqAuto"):
        tags.append({
            "name": f"Machine_Cmd_{member}",
            "datatype": "Bool",
            "plc_tag": f"{db}.Cmd.{member}",
            "access": "w",
            "table": "Machine",
            "comment": f"momentary machine command: {member}",
        })

    for eq in spec.controlled():
        for group in TYPE_TAG_GROUPS.get(eq.type, ["common"]):
            for suffix, path, datatype, access in DEVICE_TAGS[group]:
                name = f"{eq.name}_{suffix}"
                if any(t["name"] == name for t in tags):
                    continue
                tags.append({
                    "name": name,
                    "datatype": datatype,
                    "plc_tag": f"{db}.{eq.name}.{path}",
                    "access": access,
                    "table": _table_for(eq),
                    "comment": f"{eq.description or eq.name}: {suffix}",
                })

    # Raw process signals that are useful on the HMI without going through an FB.
    for eq in spec.equipment:
        if eq.type in ("digital_input", "estop", "safety_gate"):
            sig = eq.signals[0] if eq.signals else None
            if sig:
                tags.append({
                    "name": eq.name,
                    "datatype": sig.datatype,
                    "plc_tag": sig.tag,
                    "access": "r",
                    "table": "Signals",
                    "comment": eq.description or eq.name,
                })
    return tags


def _table_for(eq: Equipment) -> str:
    return f"Area_{eq.area}" if eq.area else "Devices"


def alarm_list(spec: Spec) -> List[Dict[str, Any]]:
    """Discrete alarms, ready for HMI alarm configuration."""
    from .emit_scl import alarm_members

    db = spec.db_machine
    alarms = []
    for index, (member, _datatype, comment) in enumerate(alarm_members(spec), start=1):
        if member.endswith(("_Lo", "_Hi")):
            klass, priority = "Warning", 8
        elif member == "SafetyOpen":
            klass, priority = "Critical", 16
        elif member.endswith(("_LoLo", "_HiHi")):
            klass, priority = "Critical", 12
        else:
            klass, priority = "Alarm", 10
        alarms.append({
            "number": index,
            "name": member,
            "text": comment or member,
            "class": klass,
            "priority": priority,
            "trigger_tag": f"{db}.Alarms.{member}",
            "ack_required": klass != "Warning",
        })
    return alarms


def screen_plan(spec: Spec) -> Dict[str, Any]:
    """Screens, and the objects on them, with positions and bindings."""
    width, height = _screen_size(spec)
    explicit = (spec.hmi or {}).get("screens")

    if explicit:
        screens = [_build_explicit(spec, s, width, height) for s in explicit]
    else:
        screens = _derive_screens(spec, width, height)

    runtime = (spec.hmi or {}).get("runtime", "unified")
    return {
        "resolution": {"width": width, "height": height},
        "runtime": runtime,
        # Unified Basic panels have no scripting engine. Anything the plan carries that
        # would need one has to be reachable by configuration alone on that runtime, so
        # the flag travels with the plan rather than being re-derived downstream.
        "scripting": runtime not in ("unified_basic",),
        "start_screen": screens[0]["name"] if screens else None,
        "faceplates_required": sorted({
            FACEPLATE[e.type] for e in spec.controlled() if e.type in FACEPLATE
        }),
        "screens": screens,
    }


def _areas(spec: Spec) -> List[str]:
    seen: List[str] = []
    for eq in spec.controlled():
        area = eq.area or "Machine"
        if area not in seen:
            seen.append(area)
    return seen


def _derive_screens(spec: Spec, width: int, height: int) -> List[Dict[str, Any]]:
    screens: List[Dict[str, Any]] = []

    screens.append(_overview_screen(spec, width, height))

    for area in _areas(spec):
        members = [e for e in spec.controlled() if (e.area or "Machine") == area]
        screens.append({
            "name": f"Manual_{area}",
            "title": f"Manual - {area}",
            "kind": "manual",
            "objects": _header(spec, width) + _tile_grid(members, width, height),
        })

    screens.append({
        "name": "Alarms",
        "title": "Alarms",
        "kind": "alarms",
        "objects": _header(spec, width) + [{
            "type": "AlarmControl",
            "name": "AlarmView",
            "x": MARGIN, "y": HEADER_H + GAP,
            "width": width - 2 * MARGIN,
            "height": height - HEADER_H - 2 * GAP - MARGIN,
            "columns": ["Time", "State", "Class", "Text", "Area"],
        }],
    })

    analogs = [e for e in spec.controlled() if e.type == "analog_input"]
    if analogs:
        screens.append({
            "name": "Trends",
            "title": "Trends",
            "kind": "trends",
            "objects": _header(spec, width) + [{
                "type": "TrendControl",
                "name": "TrendView",
                "x": MARGIN, "y": HEADER_H + GAP,
                "width": width - 2 * MARGIN,
                "height": height - HEADER_H - 2 * GAP - MARGIN,
                "series": [
                    {"tag": f"{e.name}_Act_Value", "label": e.description or e.name,
                     "unit": e.scaling.get("unit", "")}
                    for e in analogs
                ],
            }],
        })

    screens.append({
        "name": "Diagnostics",
        "title": "Diagnostics",
        "kind": "diagnostics",
        "objects": _header(spec, width) + [{
            "type": "SystemDiagnosisControl",
            "name": "DiagView",
            "x": MARGIN, "y": HEADER_H + GAP,
            "width": width - 2 * MARGIN,
            "height": height - HEADER_H - 2 * GAP - MARGIN,
        }],
    })
    return screens


def _overview_screen(spec: Spec, width: int, height: int) -> Dict[str, Any]:
    objects = _header(spec, width)
    objects += [
        {"type": "Text", "name": "lblState", "text": "Machine state",
         "x": MARGIN, "y": HEADER_H + GAP, "width": 200, "height": 32},
        {"type": "TextList", "name": "valState", "tag": "Machine_State",
         "x": MARGIN + 210, "y": HEADER_H + GAP, "width": 260, "height": 32,
         "entries": {
             0: "SAFETY STOP", 1: "FAULT", 2: "STOPPED - RESET",
             3: "MANUAL", 4: "AUTO READY", 5: "AUTO RUNNING",
         }},
        {"type": "Button", "name": "btnStart", "text": "START",
         "tag": "Machine_Cmd_Start", "event": "set_on_press",
         "x": MARGIN, "y": HEADER_H + GAP + 48, "width": 150, "height": 56},
        {"type": "Button", "name": "btnStop", "text": "STOP",
         "tag": "Machine_Cmd_Stop", "event": "set_on_press",
         "x": MARGIN + 160, "y": HEADER_H + GAP + 48, "width": 150, "height": 56},
        {"type": "Button", "name": "btnReset", "text": "RESET",
         "tag": "Machine_Cmd_Reset", "event": "set_on_press",
         "x": MARGIN + 320, "y": HEADER_H + GAP + 48, "width": 150, "height": 56},
        {"type": "Button", "name": "btnManual", "text": "MANUAL",
         "tag": "Machine_Cmd_ReqManual", "event": "set_on_press",
         "x": MARGIN + 490, "y": HEADER_H + GAP + 48, "width": 150, "height": 56},
        {"type": "Button", "name": "btnAuto", "text": "AUTO",
         "tag": "Machine_Cmd_ReqAuto", "event": "set_on_press",
         "x": MARGIN + 650, "y": HEADER_H + GAP + 48, "width": 150, "height": 56},
    ]
    top = HEADER_H + GAP + 48 + 56 + GAP
    objects += _status_grid(spec.controlled(), width, height, top)
    return {"name": "Overview", "title": "Overview", "kind": "overview", "objects": objects}


def _header(spec: Spec, width: int) -> List[Dict[str, Any]]:
    """Navigation and machine status strip repeated on every screen."""
    return [
        {"type": "Rectangle", "name": "hdrBackground", "x": 0, "y": 0,
         "width": width, "height": HEADER_H, "role": "header"},
        {"type": "Text", "name": "hdrTitle", "text": spec.machine,
         "x": MARGIN, "y": 20, "width": 520, "height": 48, "font_size": 28},
        {"type": "Indicator", "name": "hdrFault", "tag": "Machine_AnyFault",
         "x": width - 260, "y": 24, "width": 40, "height": 40,
         "colour_on": "red", "colour_off": "grey", "tooltip": "machine fault"},
        {"type": "Indicator", "name": "hdrAuto", "tag": "Machine_AutoRun",
         "x": width - 200, "y": 24, "width": 40, "height": 40,
         "colour_on": "green", "colour_off": "grey", "tooltip": "auto running"},
        {"type": "NavigationBar", "name": "navBar", "x": 0, "y": HEADER_H - 4,
         "width": width, "height": 4,
         "targets": ["Overview", "Alarms", "Diagnostics"]},
    ]


def _tile_grid(equipment: List[Equipment], width: int, height: int) -> List[Dict[str, Any]]:
    """Manual-mode faceplate tiles."""
    objects = []
    columns = max(1, (width - 2 * MARGIN + GAP) // (TILE_W + GAP))
    for index, eq in enumerate(equipment):
        row, col = divmod(index, columns)
        objects.append({
            "type": "Faceplate",
            "faceplate": FACEPLATE.get(eq.type, "FP_Generic"),
            "name": f"fp_{eq.name}",
            "x": MARGIN + col * (TILE_W + GAP),
            "y": HEADER_H + GAP + row * (TILE_H + GAP),
            "width": TILE_W,
            "height": TILE_H,
            "label": eq.description or eq.name,
            "interface": {"tag_prefix": f"{eq.name}_"},
            "equipment": eq.name,
            "equipment_type": eq.type,
        })
    return objects


def _status_grid(equipment: List[Equipment], width: int, height: int, top: int):
    """Compact read-only status row per device, for the overview screen."""
    objects = []
    row_h = 36
    for index, eq in enumerate(equipment):
        y = top + index * (row_h + 4)
        if y + row_h > height - MARGIN:
            break
        objects += [
            {"type": "Text", "name": f"ovLbl_{eq.name}", "text": eq.description or eq.name,
             "x": MARGIN, "y": y, "width": 420, "height": row_h},
            {"type": "Indicator", "name": f"ovRun_{eq.name}",
             "tag": f"{eq.name}_Sts_Running", "x": MARGIN + 430, "y": y + 4,
             "width": 28, "height": 28, "colour_on": "green", "colour_off": "grey"},
            {"type": "Indicator", "name": f"ovFlt_{eq.name}",
             "tag": f"{eq.name}_Sts_Fault", "x": MARGIN + 470, "y": y + 4,
             "width": 28, "height": 28, "colour_on": "red", "colour_off": "grey"},
        ]
    return objects


def _build_explicit(spec: Spec, raw: Dict[str, Any], width: int, height: int) -> Dict[str, Any]:
    names = raw.get("equipment") or []
    members = [e for e in spec.controlled() if e.name in names]
    kind = raw.get("kind", "custom")
    objects = _header(spec, width)
    if kind == "alarms":
        objects.append({"type": "AlarmControl", "name": "AlarmView", "x": MARGIN,
                        "y": HEADER_H + GAP, "width": width - 2 * MARGIN,
                        "height": height - HEADER_H - 2 * GAP - MARGIN})
    elif members:
        objects += _tile_grid(members, width, height)
    return {
        "name": raw["name"],
        "title": raw.get("title", raw["name"]),
        "kind": kind,
        "objects": objects,
    }


def dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2) + "\n"
