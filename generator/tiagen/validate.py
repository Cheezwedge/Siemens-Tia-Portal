"""Engineering rules applied on top of structural spec validation.

`model.load` already rejects anything structurally broken. This module catches the
things that parse fine but would produce a machine that misbehaves, or that a
reviewer must sign off on.
"""

from __future__ import annotations

import ipaddress
import re
from typing import List, Tuple

from . import devices as dev
from .model import Spec

# Siemens MLFB, e.g. 6ES7214-1AH50-0XB0, 6ES7 510-1DJ01-0AB0, 6AV2128-3MB06-0AX0:
# family (6 + 2 letters + digit), 3-digit type, then a 5- and a 4-character group.
MLFB_RE = re.compile(r"^6[A-Z]{2}\d[ -]?[0-9A-Z]{3}-[0-9A-Z]{5}-[0-9A-Z]{4}$", re.I)


def check(spec: Spec) -> Tuple[List[str], List[str]]:
    """Return (errors, warnings). A non-empty error list must block the build."""
    errors: List[str] = []
    warnings: List[str] = []

    _check_interlocks(spec, errors)
    _check_analog(spec, errors, warnings)
    _check_control_roles(spec, errors)
    _check_modules(spec, errors)
    _check_network(spec, errors, warnings)
    _check_safety(spec, warnings)
    _check_coverage(spec, warnings)
    _check_order_numbers(spec, warnings)
    _check_hmi(spec, warnings)

    return errors, warnings


def _check_interlocks(spec: Spec, errors: List[str]) -> None:
    names = {e.name for e in spec.equipment}
    controlled = {e.name for e in spec.controlled()}
    for eq in spec.equipment:
        for other in eq.options.get("interlock_from") or []:
            if other not in names:
                errors.append(
                    f"'{eq.name}'.interlock_from references unknown equipment '{other}'"
                )
            elif other not in controlled:
                errors.append(
                    f"'{eq.name}'.interlock_from references '{other}', which has no "
                    "running status to interlock against (tag-only equipment type)"
                )
            elif other == eq.name:
                errors.append(f"'{eq.name}' cannot interlock against itself")


def _check_analog(spec: Spec, errors: List[str], warnings: List[str]) -> None:
    for eq in spec.equipment:
        if eq.type not in ("analog_input", "analog_output", "vfd_analog"):
            continue
        sc = eq.scaling
        if float(sc["raw_max"]) == float(sc["raw_min"]):
            errors.append(f"'{eq.name}' scaling has raw_min == raw_max; scaling would divide by zero")
        if float(sc["eng_max"]) == float(sc["eng_min"]):
            errors.append(f"'{eq.name}' scaling has eng_min == eng_max; the value could never change")

        limits = [(k, sc.get(k)) for k in ("lo_lo", "lo", "hi", "hi_hi")]
        given = [(k, float(v)) for k, v in limits if v is not None]
        ordered = [v for _, v in given]
        if ordered != sorted(ordered):
            errors.append(
                f"'{eq.name}' alarm limits are out of order: "
                + ", ".join(f"{k}={v}" for k, v in given)
                + " (expected lo_lo <= lo <= hi <= hi_hi)"
            )
        for key, value in given:
            if not (float(sc["eng_min"]) <= value <= float(sc["eng_max"])):
                warnings.append(
                    f"'{eq.name}' limit {key}={value} lies outside the scaled range "
                    f"{sc['eng_min']}..{sc['eng_max']}, so it can never trigger"
                )


def _check_control_roles(spec: Spec, errors: List[str]) -> None:
    seen = {}
    for eq in spec.equipment:
        role = eq.options.get("control_role")
        if not role:
            continue
        if role not in ("start", "stop", "reset"):
            errors.append(
                f"'{eq.name}'.options.control_role '{role}' is not one of start, stop, reset"
            )
        elif role in seen:
            errors.append(
                f"control_role '{role}' is claimed by both '{seen[role]}' and '{eq.name}'"
            )
        else:
            seen[role] = eq.name
        if eq.type != "digital_input":
            errors.append(
                f"'{eq.name}' has control_role '{role}' but is not a digital_input"
            )


def _check_modules(spec: Spec, errors: List[str]) -> None:
    def dup_slots(modules, owner):
        slots = {}
        for m in modules:
            slot = m.get("slot")
            if slot in slots:
                errors.append(
                    f"{owner}: slot {slot} is claimed by both '{slots[slot]}' and '{m.get('name')}'"
                )
            else:
                slots[slot] = m.get("name")

    dup_slots(spec.plc.get("modules") or [], f"PLC '{spec.plc['name']}'")
    for device in spec.network.get("io_devices") or []:
        dup_slots(device.get("modules") or [], f"IO device '{device.get('name')}'")


def _check_network(spec: Spec, errors: List[str], warnings: List[str]) -> None:
    nodes = []
    if spec.plc.get("ip"):
        nodes.append((spec.plc["name"], spec.plc["ip"]))
    if spec.hmi and spec.hmi.get("ip"):
        nodes.append((spec.hmi["name"], spec.hmi["ip"]))
    for d in spec.network.get("io_devices") or []:
        if d.get("ip"):
            nodes.append((d["name"], d["ip"]))

    parsed = []
    for name, ip in nodes:
        try:
            parsed.append((name, ipaddress.IPv4Address(str(ip))))
        except ValueError:
            errors.append(f"{name} has an invalid IP address '{ip}'")

    seen = {}
    for name, ip in parsed:
        if ip in seen:
            errors.append(f"IP {ip} is assigned to both '{seen[ip]}' and '{name}'")
        seen[ip] = name

    mask = str(spec.plc.get("subnet_mask", "255.255.255.0"))
    if parsed and spec.plc.get("ip"):
        try:
            net = ipaddress.IPv4Network(f"{spec.plc['ip']}/{mask}", strict=False)
            for name, ip in parsed:
                if ip not in net:
                    warnings.append(
                        f"{name} ({ip}) is outside the PLC subnet {net} - it will not be "
                        "reachable without a router"
                    )
        except ValueError:
            errors.append(f"plc.subnet_mask '{mask}' is not a valid subnet mask")

    if not spec.plc.get("ip"):
        warnings.append(
            "plc.ip is not set - the generated project will keep the CPU's default "
            "address and no PROFINET subnet will be created"
        )
    if spec.hmi and not spec.hmi.get("ip"):
        warnings.append(f"hmi.ip is not set - '{spec.hmi['name']}' cannot reach the PLC")


def _check_safety(spec: Spec, warnings: List[str]) -> None:
    safety_inputs = spec.safety_inputs()
    if safety_inputs and not spec.safety.get("fail_safe_plc"):
        names = ", ".join(e.name for e in safety_inputs)
        verb = "is" if len(safety_inputs) == 1 else "are"
        warnings.append(
            f"SAFETY: {names} {verb} read by the standard program only. Standard logic is "
            "not a safety function - the stop must be achieved by a safety relay or an "
            "F-CPU. The generated code treats these inputs as STATUS only."
        )
    if spec.safety.get("fail_safe_plc"):
        warnings.append(
            "SAFETY: safety.fail_safe_plc is true. This generator never authors F-blocks. "
            "The F-runtime group, F-I/O and safety program remain a manual, reviewed step."
        )
    if not safety_inputs and spec.controlled():
        warnings.append(
            "No estop or safety_gate equipment is declared, so FB_ModeManager.SafetyOk "
            "will be wired to a constant TRUE. Add the safety status contact before "
            "commissioning."
        )


def _check_coverage(spec: Spec, warnings: List[str]) -> None:
    for eq in spec.controlled():
        if eq.type in ("motor_dol", "motor_reversing", "vfd_analog"):
            if not eq.has("fault_fb"):
                warnings.append(
                    f"'{eq.name}' has no fault feedback, so an overload trip will be "
                    "invisible to the PLC"
                )
            if not eq.has("running_fb"):
                warnings.append(
                    f"'{eq.name}' has no running feedback, so the code reports the "
                    "commanded state as the actual state"
                )
        if eq.type in ("valve_single", "valve_double"):
            if not eq.has("opened_fb") and not eq.has("closed_fb"):
                warnings.append(
                    f"'{eq.name}' has no position feedback, so travel supervision is "
                    "inactive"
                )

    if not spec.equipment:
        warnings.append("The spec declares no equipment; only the hardware skeleton is generated")

    for eq in spec.equipment:
        if eq.type == "digital_input" and not eq.description:
            warnings.append(f"'{eq.name}' has no description; the tag comment will just repeat the name")


def _check_hmi(spec: Spec, warnings: List[str]) -> None:
    if not spec.hmi:
        return

    # Every object in the screen plan carries an absolute x/y, so a wrong resolution
    # does not fail - it silently positions everything for a panel you do not own.
    if not spec.hmi.get("resolution"):
        warnings.append(
            "hmi.resolution is not set, so screens are laid out for 1920x1080. Set it to "
            "your panel's native resolution - an MTP700 is a 7-inch panel, not a monitor."
        )

    if spec.hmi.get("runtime") == "unified_basic":
        warnings.append(
            "hmi.runtime is unified_basic: Unified Basic panels have no scripting engine, "
            "so every generated screen must work by configuration alone."
        )
        # Openness cannot create faceplate instances on classic Basic Panels. Whether
        # that limit carries over to Unified Basic decides whether the manual screens
        # are generated or drawn by hand, so it is called out rather than assumed.
        if any(e.type in ("motor_dol", "motor_reversing", "vfd_analog",
                          "valve_single", "valve_double") for e in spec.controlled()):
            warnings.append(
                "the manual screens place faceplate instances. Confirm your Unified Basic "
                "panel accepts them before relying on generated manual screens - Openness "
                "cannot create faceplate instances on classic Basic Panels, and if the same "
                "limit applies here the tiles must be built from primitives instead."
            )


def _check_order_numbers(spec: Spec, warnings: List[str]) -> None:
    candidates = [("plc", spec.plc.get("order_number"))]
    if spec.hmi:
        candidates.append(("hmi", spec.hmi.get("order_number")))
    for m in spec.plc.get("modules") or []:
        candidates.append((f"module {m.get('name')}", m.get("order_number")))
    for d in spec.network.get("io_devices") or []:
        candidates.append((f"io device {d.get('name')}", d.get("order_number")))
        for m in d.get("modules") or []:
            candidates.append((f"module {m.get('name')}", m.get("order_number")))

    for label, mlfb in candidates:
        if mlfb and not MLFB_RE.match(str(mlfb).strip()):
            warnings.append(
                f"{label}: '{mlfb}' does not look like a Siemens MLFB. Openness matches "
                "the catalog string exactly - copy it from the hardware catalog."
            )


def format_report(errors: List[str], warnings: List[str]) -> str:
    lines = []
    for e in errors:
        lines.append(f"ERROR   {e}")
    for w in warnings:
        lines.append(f"WARNING {w}")
    if not lines:
        lines.append("OK      spec passed all checks")
    return "\n".join(lines)
