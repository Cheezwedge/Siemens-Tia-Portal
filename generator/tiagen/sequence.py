"""The step sequence: parsing, and resolving its references against the spec.

A sequence is a list of numbered steps. Each step names the device requests that
hold while it is active, the conditions that must all be true to leave it, and a
timeout. Everything a person needs at 2am - which step, what it is waiting for,
how long it has been waiting - is published, because the generator knows all three
and a human writing this by hand would forget one of them on step 34.

This module is deliberately split in two halves:

* `parse` works on the raw mapping alone and raises `SpecError` for anything
  structurally wrong. `model` calls it, so a broken sequence fails at load time
  like every other spec error.
* `resolve_*` needs the equipment list and therefore takes a `Spec`. Emitters and
  the validator call those. Keeping the halves apart is what lets `model` import
  this module without a cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# A step's timeout, written the way an engineer says it: 500ms, 10s, 2m.
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ms|s|m)\s*$", re.I)
_DURATION_UNITS = {"ms": 1, "s": 1000, "m": 60_000}

# Boolean device statuses, by the name a person would use for them. The value is
# the UDT_DevIf member the alias reads.
STATUS_ALIASES = {
    "running": "Sts_Running",
    "on": "Sts_Running",
    "open": "Sts_Running",
    "opened": "Sts_Running",
    "extended": "Sts_Running",
    "stopped": "Sts_Stopped",
    "off": "Sts_Stopped",
    "closed": "Sts_Stopped",
    "retracted": "Sts_Stopped",
    "fault": "Sts_Fault",
    "faulted": "Sts_Fault",
    "warning": "Sts_Warning",
    "interlocked": "Sts_Interlocked",
    "enabled": "Sts_Enabled",
    "commanded": "Sts_Cmd",
    "manual": "Sts_ManualMode",
}

# Analog status, compared against a number rather than tested as a bit.
VALUE_ALIASES = {"value": "Act_Value", "actual": "Act_Value"}

# Action verbs, by equipment type. Each verb maps to every assignment it makes, so
# a verb is self-consistent: nothing else has to be said in the step to make it true.
#
# This matters because a request PERSISTS until something changes it - the sequencer
# does not clear requests when a step advances, which is what lets a clamp stay
# clamped for the six steps after the one that closed it. The consequence is that
# opposing verbs must cancel each other explicitly. If "stop" only asserted _Stop,
# then _Start would still be TRUE from the earlier step and the device FB would see
# both requests at once.
ACTION_VERBS: Dict[str, Dict[str, Tuple[Tuple[str, Any], ...]]] = {
    "motor_dol": {
        "start": (("_Start", True), ("_Stop", False)),
        "run": (("_Start", True), ("_Stop", False)),
        "stop": (("_Stop", True), ("_Start", False)),
    },
    "motor_reversing": {
        "forward": (("_Fwd", True), ("_Rev", False)),
        "fwd": (("_Fwd", True), ("_Rev", False)),
        "reverse": (("_Rev", True), ("_Fwd", False)),
        "rev": (("_Rev", True), ("_Fwd", False)),
        # Neither direction requested is how a reversing motor is told to stop.
        "stop": (("_Fwd", False), ("_Rev", False)),
    },
    "vfd_analog": {
        "start": (("_Start", True), ("_Stop", False)),
        "run": (("_Start", True), ("_Stop", False)),
        "stop": (("_Stop", True), ("_Start", False)),
        "speed": (("_Speed", "number"),),
        "setpoint": (("_Speed", "number"),),
    },
    "valve_single": {
        # One coil: "closed" is the de-energised state, so close clears the request.
        "open": (("_Open", True),),
        "close": (("_Open", False),),
        "closed": (("_Open", False),),
    },
    "valve_double": {
        "open": (("_Open", True),),
        "close": (("_Open", False),),
        "closed": (("_Open", False),),
    },
    "digital_output": {
        "on": (("_Cmd", True),), "set": (("_Cmd", True),),
        "off": (("_Cmd", False),), "clear": (("_Cmd", False),),
    },
    "analog_output": {
        "value": (("_Value", "number"),),
        "setpoint": (("_Value", "number"),),
    },
}

_COMPARE_OPS = {">=", "<=", ">", "<", "=", "<>"}
# SCL spells inequality "<>" and equality "="; accept the C-ish forms people type.
_OP_ALIASES = {"==": "=", "!=": "<>"}


class SequenceError(Exception):
    """Raised for a structurally invalid sequence. `model` converts it to SpecError."""


@dataclass
class Step:
    number: int
    name: str
    message: str
    actions: List[str] = field(default_factory=list)
    wait_for: List[str] = field(default_factory=list)
    wait_mode: str = "all"           # all | any
    timeout_ms: Optional[int] = None
    on_timeout: str = "fault"        # fault | hold | continue
    next_number: Optional[int] = None    # None = the next step in the list

    @property
    def ident(self) -> str:
        """Identifier fragment used in generated symbol names."""
        return f"{self.number}"


@dataclass
class Sequence:
    name: str
    steps: List[Step]
    cyclic: bool = True
    idle_number: int = 0

    def by_number(self, number: int) -> Optional[Step]:
        for s in self.steps:
            if s.number == number:
                return s
        return None

    def successor(self, step: Step) -> Optional[int]:
        """The step this one advances to: explicit, else the next in the list."""
        if step.next_number is not None:
            return step.next_number
        index = self.steps.index(step)
        if index + 1 < len(self.steps):
            return self.steps[index + 1].number
        return self.steps[0].number if self.cyclic else None


# ----------------------------------------------------------------------------
# Parsing - raw mapping only, no Spec
# ----------------------------------------------------------------------------
def parse(raw: Optional[Dict[str, Any]]) -> Optional[Sequence]:
    if not raw:
        return None

    name = str(raw.get("name") or "Cycle")
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
        raise SequenceError(
            f"sequence.name '{name}' must start with a letter and contain only "
            "letters, digits and underscores (it becomes part of a block name)"
        )

    raw_steps = raw.get("steps") or []
    if not raw_steps:
        raise SequenceError("sequence.steps is empty; remove the sequence section or add steps")

    steps: List[Step] = []
    for index, rs in enumerate(raw_steps):
        if not isinstance(rs, dict):
            raise SequenceError(f"sequence.steps[{index}] must be a mapping, not {type(rs).__name__}")
        steps.append(_step_from_dict(rs, index))

    idle = raw.get("idle_step", 0)
    if not isinstance(idle, int):
        raise SequenceError("sequence.idle_step must be an integer")
    if any(s.number == idle for s in steps):
        raise SequenceError(
            f"sequence.idle_step {idle} collides with a real step number; the idle step "
            "is the state the sequencer holds in when auto is not running"
        )

    return Sequence(
        name=name,
        steps=steps,
        cyclic=bool(raw.get("cyclic", True)),
        idle_number=idle,
    )


def _step_from_dict(rs: Dict[str, Any], index: int) -> Step:
    if "step" not in rs:
        raise SequenceError(f"sequence.steps[{index}] has no 'step' number")
    number = rs["step"]
    if not isinstance(number, int) or number <= 0:
        raise SequenceError(f"sequence.steps[{index}]: step must be a positive integer, got {number!r}")

    wait_raw = rs.get("wait_for")
    wait_mode = "all"
    conditions: List[str] = []
    if isinstance(wait_raw, dict):
        if len(wait_raw) != 1 or next(iter(wait_raw)) not in ("all", "any"):
            raise SequenceError(
                f"step {number}: wait_for must be a list, or a mapping with exactly one "
                "key, 'all' or 'any'"
            )
        wait_mode = next(iter(wait_raw))
        conditions = list(wait_raw[wait_mode] or [])
    elif isinstance(wait_raw, list):
        conditions = list(wait_raw)
    elif isinstance(wait_raw, str):
        conditions = [wait_raw]
    elif wait_raw is not None:
        raise SequenceError(f"step {number}: wait_for must be a list, a string or a mapping")

    actions = rs.get("actions") or []
    if isinstance(actions, str):
        actions = [actions]
    if not isinstance(actions, list):
        raise SequenceError(f"step {number}: actions must be a list")

    on_timeout = str(rs.get("on_timeout", "fault")).lower()
    if on_timeout not in ("fault", "hold", "continue"):
        raise SequenceError(
            f"step {number}: on_timeout must be fault, hold or continue, got '{on_timeout}'"
        )

    return Step(
        number=number,
        name=str(rs.get("name") or f"Step{number}"),
        message=str(rs.get("message") or rs.get("name") or f"Step {number}"),
        actions=[str(a) for a in actions],
        wait_for=[str(c) for c in conditions],
        wait_mode=wait_mode,
        timeout_ms=_duration_ms(rs.get("timeout"), number),
        on_timeout=on_timeout,
        next_number=rs.get("next"),
    )


def _duration_ms(value: Any, step_number: int) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SequenceError(f"step {step_number}: timeout must be a duration, not a boolean")
    if isinstance(value, (int, float)):
        return int(value * 1000)     # a bare number is seconds
    match = _DURATION_RE.match(str(value))
    if not match:
        raise SequenceError(
            f"step {step_number}: timeout '{value}' is not a duration. Write it as "
            "500ms, 10s or 2m."
        )
    magnitude, unit = float(match.group(1)), match.group(2).lower()
    return int(magnitude * _DURATION_UNITS[unit])


def scl_time(ms: int) -> str:
    """A millisecond count as an SCL TIME literal."""
    return f"T#{ms}MS"


# ----------------------------------------------------------------------------
# Resolution - needs the equipment list
# ----------------------------------------------------------------------------
@dataclass
class Condition:
    """One resolved wait_for term."""
    source: str          # what the spec said, for messages and comments
    key: str             # stable identifier, used as the Cond struct member
    expression: str      # SCL that reads it, in machine-FB scope
    negated: bool = False


def resolve_condition(spec, text: str) -> Condition:
    """Resolve one wait_for term to SCL evaluated in the machine FB's scope."""
    raw = text.strip()
    negated = False
    if raw.lower().startswith("not "):
        negated, raw = True, raw[4:].strip()

    parts = raw.split()
    if len(parts) == 3:
        return _resolve_comparison(spec, text, parts, negated)
    if len(parts) != 1:
        raise SequenceError(
            f"condition '{text}' is not understood. Write 'Device.status', a digital "
            "input name, or 'Device.value >= 50'."
        )

    token = parts[0]
    if "." in token:
        device_name, _, alias = token.partition(".")
        eq = _equipment(spec, device_name, text)
        member = STATUS_ALIASES.get(alias.lower())
        if member is None:
            if alias.lower() in VALUE_ALIASES:
                raise SequenceError(
                    f"condition '{text}' reads an analog value as a bit. Compare it "
                    f"instead, for example '{device_name}.value >= 50'."
                )
            raise SequenceError(
                f"condition '{text}': '{alias}' is not a device status. Known statuses: "
                + ", ".join(sorted(set(STATUS_ALIASES)))
            )
        if not eq.typedef.fb:
            raise SequenceError(
                f"condition '{text}': '{device_name}' has no function block, so it has no "
                "status. Reference the input by its own name instead."
            )
        return Condition(text, f"{device_name}_{alias.capitalize()}",
                         f"#{device_name}.Hmi.{member}", negated)

    # A bare name: a wired input, read from its global tag.
    eq = _equipment(spec, token, text)
    sig = eq.signals[0] if eq.signals else None
    if sig is None or sig.datatype != "Bool":
        raise SequenceError(
            f"condition '{text}': '{token}' has no boolean input signal to test. "
            "Use 'Device.status' for a device, or compare an analog value."
        )
    return Condition(text, token, f'"{sig.tag}"', negated)


def _resolve_comparison(spec, text: str, parts: List[str], negated: bool) -> Condition:
    left, op, right = parts
    op = _OP_ALIASES.get(op, op)
    if op not in _COMPARE_OPS:
        raise SequenceError(
            f"condition '{text}': '{parts[1]}' is not a comparison. Use one of "
            + ", ".join(sorted(_COMPARE_OPS))
        )
    try:
        number = float(right)
    except ValueError:
        raise SequenceError(
            f"condition '{text}': the right side must be a number, got '{right}'. "
            "Comparing two tags is not supported - put that in the auto sequence region."
        )

    device_name, _, alias = left.partition(".")
    if not alias or alias.lower() not in VALUE_ALIASES:
        raise SequenceError(
            f"condition '{text}': compare a device value, for example "
            f"'{device_name}.value >= {right}'."
        )
    eq = _equipment(spec, device_name, text)
    if not eq.typedef.fb:
        raise SequenceError(
            f"condition '{text}': '{device_name}' has no function block, so it has no "
            "scaled value."
        )
    member = VALUE_ALIASES[alias.lower()]
    literal = _real_literal(number)
    key = f"{device_name}_{_OP_KEYS[op]}_{str(number).replace('.', 'p').replace('-', 'neg')}"
    return Condition(text, key, f"#{device_name}.Hmi.{member} {op} {literal}", negated)


_OP_KEYS = {">=": "Ge", "<=": "Le", ">": "Gt", "<": "Lt", "=": "Eq", "<>": "Ne"}


def _real_literal(value: float) -> str:
    """SCL Real literal. An integer-valued Real still needs a decimal point."""
    text = repr(float(value))
    return text if "." in text or "e" in text.lower() else text + ".0"


def _equipment(spec, name: str, context: str):
    for eq in spec.equipment:
        if eq.name == name:
            return eq
    raise SequenceError(f"'{context}' refers to '{name}', which is not in the equipment list")


@dataclass
class Action:
    """One resolved action: an assignment into the UDT_*Auto struct."""
    source: str
    member: str          # Auto member name, without the struct prefix
    value: str           # SCL literal


def resolve_action(spec, text: str) -> List[Action]:
    """Resolve one action to the assignments it makes into the Auto struct.

    Returns a list because an opposing pair has to be cancelled in the same breath -
    see the note on ACTION_VERBS.
    """
    raw = text.strip()
    value_text: Optional[str] = None
    if "=" in raw:
        raw, _, value_text = (p.strip() for p in raw.partition("="))

    device_name, _, verb = raw.partition(".")
    if not verb:
        raise SequenceError(
            f"action '{text}' is not understood. Write 'Device.verb', or "
            "'Device.speed = 50' for a setpoint."
        )
    eq = _equipment(spec, device_name, text)
    verbs = ACTION_VERBS.get(eq.type)
    if not verbs:
        raise SequenceError(
            f"action '{text}': '{device_name}' is a {eq.type}, which the sequence cannot "
            "command. Only actuators take actions."
        )
    entry = verbs.get(verb.lower())
    if entry is None:
        raise SequenceError(
            f"action '{text}': '{verb}' is not something a {eq.type} does. Known verbs: "
            + ", ".join(sorted(verbs))
        )

    takes_value = any(kind == "number" for _, kind in entry)
    if takes_value and value_text is None:
        raise SequenceError(f"action '{text}' needs a value, for example '{raw} = 50'")
    if not takes_value and value_text is not None:
        raise SequenceError(
            f"action '{text}': '{verb}' does not take a value - write '{raw}' on its own"
        )

    actions: List[Action] = []
    for suffix, kind in entry:
        member = f"{device_name}{suffix}"
        if kind == "number":
            try:
                literal = _real_literal(float(value_text))
            except ValueError:
                raise SequenceError(f"action '{text}': '{value_text}' is not a number")
            actions.append(Action(text, member, literal))
        else:
            actions.append(Action(text, member, "TRUE" if kind else "FALSE"))
    return actions


def conditions_of(spec, seq: Sequence) -> List[Condition]:
    """Every distinct condition in the sequence, in first-use order.

    Keyed on the condition alone, ignoring negation: the Cond struct carries the
    bit once and a step negates it at the point of use.
    """
    seen: Dict[str, Condition] = {}
    for step in seq.steps:
        for text in step.wait_for:
            cond = resolve_condition(spec, text)
            seen.setdefault(cond.key, cond)
    return list(seen.values())


def reasons_of(spec, seq: Sequence) -> List[Condition]:
    """Every distinct blocked-reason, in first-use order.

    Unlike `conditions_of` this keys on negation too, because "waiting for the lift
    to raise" and "waiting for the lift to lower" read the same bit and are
    opposite messages. Sharing one id between them tells the operator the wrong
    thing at the worst moment.
    """
    seen: Dict[Tuple[str, bool], Condition] = {}
    for step in seq.steps:
        for text in step.wait_for:
            cond = resolve_condition(spec, text)
            seen.setdefault((cond.key, cond.negated), cond)
    return list(seen.values())
