"""Engineering rules applied on top of structural spec validation.

`model.load` already rejects anything structurally broken. This module catches the
things that parse fine but would produce a machine that misbehaves, or that a
reviewer must sign off on.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Dict, List, Optional, Set, Tuple

from . import devices as dev
from . import sequence
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
    _check_sequence(spec, errors, warnings)
    _check_hygiene(spec, warnings)

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


def _check_sequence(spec: Spec, errors: List[str], warnings: List[str]) -> None:
    seq = spec.sequence
    if seq is None:
        return

    numbers = [s.number for s in seq.steps]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    for number in duplicates:
        errors.append(f"sequence: step {number} is declared more than once")

    if numbers != sorted(numbers):
        warnings.append(
            "sequence: step numbers are not in ascending order. The generated CASE "
            "follows the spec's order, so the block will not read in step order."
        )

    # Every reference has to resolve, or the generated SCL will not compile. Report
    # them all rather than stopping at the first - one pass should list every typo.
    for step in seq.steps:
        for text in step.actions:
            try:
                sequence.resolve_action(spec, text)
            except sequence.SequenceError as exc:
                errors.append(f"sequence step {step.number}: {exc}")
        for text in step.wait_for:
            try:
                sequence.resolve_condition(spec, text)
            except sequence.SequenceError as exc:
                errors.append(f"sequence step {step.number}: {exc}")

    # A step that can be left has an exit; one that cannot will sit there forever.
    for step in seq.steps:
        if not step.wait_for and step.timeout_ms is None:
            warnings.append(
                f"sequence step {step.number} ({step.name}) has no wait_for and no "
                "timeout, so it advances the cycle after it is entered. That is only "
                "right for a step whose actions need no confirmation."
            )
        if step.wait_for and step.timeout_ms is None:
            warnings.append(
                f"sequence step {step.number} ({step.name}) waits with no timeout, so a "
                "failure to reach the condition looks identical to a slow machine. Give "
                "it a timeout unless the wait is genuinely unbounded."
            )
        if step.next_number is not None and seq.by_number(step.next_number) is None:
            errors.append(
                f"sequence step {step.number}: next is {step.next_number}, which is not "
                "a step in this sequence"
            )

    # Reachability, following explicit next links and list order.
    reachable = {seq.steps[0].number}
    frontier = [seq.steps[0]]
    while frontier:
        step = frontier.pop()
        successor = seq.successor(step)
        if successor is not None and successor not in reachable:
            reachable.add(successor)
            target = seq.by_number(successor)
            if target is not None:
                frontier.append(target)
    for step in seq.steps:
        if step.number not in reachable:
            warnings.append(
                f"sequence step {step.number} ({step.name}) is unreachable: nothing "
                "advances to it. A step reached only from the hand-written region is fine; "
                "otherwise it is a gap in the table."
            )

    if not seq.cyclic:
        warnings.append(
            "sequence: cyclic is false, so the last step returns to idle and the machine "
            "needs a fresh start command for every cycle"
        )

    for step in seq.steps:
        if not step.message.strip():
            errors.append(f"sequence step {step.number} has an empty message")

    _check_step_transitions(spec, seq, errors, warnings)
    _check_cycle_returns_to_start(spec, seq, warnings)


def _check_step_transitions(spec: Spec, seq, errors: List[str], warnings: List[str]) -> None:
    """Transition rules carried over from the ladder rule set.

    A ladder reviewer looks for a rung that can never be true, and for a timer
    done-bit used as the only permissive. Both hazards exist unchanged in a step
    table; only the notation is different.
    """
    for step in seq.steps:
        resolved = []
        for text in step.wait_for:
            try:
                resolved.append(sequence.resolve_condition(spec, text))
            except sequence.SequenceError:
                continue        # already reported against this step

        # X and NOT X in the same "all" transition can never both hold - the
        # equivalent of an NO and an NC contact on one address in series.
        if step.wait_mode == "all":
            positive = {c.key for c in resolved if not c.negated}
            negative = {c.key for c in resolved if c.negated}
            for key in sorted(positive & negative):
                sources = [c.source for c in resolved if c.key == key]
                errors.append(
                    f"sequence step {step.number} waits for {' and '.join(sources)}, which "
                    "cannot both be true. The step would never advance."
                )

        # Time as the only permissive: nothing confirms the step's actions happened.
        if step.on_timeout == "continue" and not step.wait_for:
            warnings.append(
                f"sequence step {step.number} ({step.name}) advances on its timeout alone, "
                "with nothing confirming its actions completed. A timeout is a backstop, "
                "not a permissive - add the condition that proves the step finished."
            )

        if step.timeout_ms is not None and step.timeout_ms <= 0:
            errors.append(
                f"sequence step {step.number} has a timeout of {step.timeout_ms} ms, which "
                "expires immediately"
            )

        if len(step.wait_for) > 6:
            warnings.append(
                f"sequence step {step.number} ({step.name}) waits on {len(step.wait_for)} "
                "conditions. Consider splitting it - a step this wide is hard to diagnose, "
                "and only the first unsatisfied condition is reported to the operator."
            )


def _check_cycle_returns_to_start(spec: Spec, seq, warnings: List[str]) -> None:
    """A cyclic sequence should leave the machine as it found it.

    The generated sequencer re-asserts requests per step, so nothing latches on
    the way a SET coil does. What can still happen is a cycle that opens something
    and never closes it - the machine ends the cycle in a different state from the
    one it started in, and the second cycle behaves differently from the first.
    """
    if not seq.cyclic:
        return

    asserted: Dict[str, int] = {}
    released: Set[str] = set()
    verb_for: Dict[str, str] = {}
    for step in seq.steps:
        for text in step.actions:
            try:
                resolved = sequence.resolve_action(spec, text)
            except sequence.SequenceError:
                continue
            for action in resolved:
                if action.value == "FALSE":
                    released.add(action.member)
                elif action.value == "TRUE":
                    asserted.setdefault(action.member, step.number)
                    verb_for.setdefault(action.member, action.source)

    for member, step_number in sorted(asserted.items(), key=lambda kv: kv[1]):
        if member in released:
            continue
        warnings.append(
            f"sequence: '{member}' is requested at step {step_number} and nothing in the "
            f"cycle cancels it (requested by '{verb_for[member]}'). Requests persist, so the "
            "cycle ends in a different state from the one it started in and the second cycle "
            "does not repeat the first."
        )


# Names that mean somebody was debugging. Adapted from the LadderLogicReview rule
# set (R011): the hazard is identical on any platform - a bypass that was meant to
# come out before the machine shipped.
_BYPASS_WORDS = (
    "bypass", "debug", "dummy", "temp", "tmp", "testbit", "forcebit",
    "force", "override", "fixme", "todo", "xxx", "scrap", "delete",
)
# Names that carry no meaning. A tag called Flag1 is a tag nobody can search for.
_VAGUE_NAMES = {
    "tmp", "temp", "var", "bit", "flag", "thing", "stuff", "data", "value",
    "test", "aux", "misc", "new", "old", "x", "y", "z", "a", "b", "c",
}
_VAGUE_RE = re.compile(r"^(flag|bit|var|tag|item|obj|val|out|in)\d*$", re.I)


def _looks_like_debug(text: str) -> Optional[str]:
    lowered = text.lower()
    for word in _BYPASS_WORDS:
        if word in lowered:
            return word
    return None


def _check_hygiene(spec: Spec, warnings: List[str]) -> None:
    """Naming and documentation rules that survive the move from ladder to SCL."""
    for eq in spec.equipment:
        found = _looks_like_debug(eq.name)
        if found:
            warnings.append(
                f"'{eq.name}' contains '{found}', which reads like a test or bypass left "
                "over from commissioning. Rename it or remove it before this machine ships."
            )
        if eq.name.lower() in _VAGUE_NAMES or _VAGUE_RE.match(eq.name):
            warnings.append(
                f"'{eq.name}' is not a descriptive name. It becomes a tag name, an HMI tag "
                "and an alarm text - name it after what it is on the machine."
            )
        # Every generated tag takes its comment from the description, so a missing
        # description means a wiring list whose comment column repeats the name.
        if eq.typedef.fb and not eq.description:
            warnings.append(
                f"'{eq.name}' has no description, so its tag comments, HMI tags and alarm "
                "text will just repeat the name"
            )

    seq = spec.sequence
    if seq is None:
        return

    for step in seq.steps:
        for label, text in (("name", step.name), ("message", step.message)):
            found = _looks_like_debug(text)
            if found:
                warnings.append(
                    f"sequence step {step.number}: {label} contains '{found}'. An operator "
                    "message is machine documentation - it should not mention debugging."
                )

    # A copied step whose message was never updated tells the operator the wrong thing
    # for the whole of that step. Adapted from the duplicate-comment rule.
    by_message: Dict[str, List[int]] = {}
    for step in seq.steps:
        by_message.setdefault(step.message.strip().lower(), []).append(step.number)
    for message, steps in by_message.items():
        if len(steps) > 1 and message:
            listed = ", ".join(str(s) for s in steps)
            warnings.append(
                f"sequence steps {listed} share the message '{message}'. If a step was "
                "copied, its message was not updated - the operator cannot tell them apart."
            )


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
