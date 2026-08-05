"""Spec loading, defaulting and I/O address allocation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import devices as dev

IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
BIT_ADDR_RE = re.compile(r"^%(?P<pfx>[IQ])(?P<byte>\d+)\.(?P<bit>[0-7])$")
WORD_ADDR_RE = re.compile(r"^%(?P<pfx>[IQ])W(?P<byte>\d+)$")

DEFAULT_SCALING = {
    "raw_min": 0.0,
    "raw_max": 27648.0,
    "eng_min": 0.0,
    "eng_max": 100.0,
    "unit": "%",
}

# SCL keywords that must not be used as an identifier.
SCL_RESERVED = {
    "and", "array", "begin", "bool", "by", "byte", "case", "char", "const", "continue",
    "data_block", "date", "dint", "div", "do", "dword", "else", "elsif", "end_case",
    "end_const", "end_data_block", "end_for", "end_function", "end_function_block",
    "end_if", "end_label", "end_organization_block", "end_repeat", "end_struct",
    "end_type", "end_var", "end_while", "exit", "false", "for", "function",
    "function_block", "goto", "if", "int", "label", "lint", "lreal", "lword", "mod",
    "not", "of", "or", "organization_block", "real", "repeat", "return", "sint",
    "string", "struct", "then", "time", "to", "true", "type", "udint", "uint",
    "ulint", "until", "usint", "var", "var_in_out", "var_input", "var_output",
    "var_temp", "void", "while", "word", "xor",
}


class SpecError(Exception):
    """Raised for a spec that cannot be turned into a build."""


@dataclass
class ResolvedSignal:
    role: str
    kind: str
    tag: str
    address: str
    datatype: str
    comment: str
    table: str
    explicit: bool = False


@dataclass
class Equipment:
    name: str
    type: str
    description: str = ""
    area: str = ""
    options: Dict[str, Any] = field(default_factory=dict)
    scaling: Dict[str, Any] = field(default_factory=dict)
    signals: List[ResolvedSignal] = field(default_factory=list)

    @property
    def typedef(self) -> dev.EquipmentType:
        return dev.TYPES[self.type]

    @property
    def instance(self) -> str:
        """Static instance name inside the machine FB."""
        return self.name

    def signal(self, role: str) -> Optional[ResolvedSignal]:
        for s in self.signals:
            if s.role == role:
                return s
        return None

    def has(self, role: str) -> bool:
        return self.signal(role) is not None


@dataclass
class Spec:
    project: Dict[str, Any]
    plc: Dict[str, Any]
    hmi: Optional[Dict[str, Any]]
    network: Dict[str, Any]
    io: Dict[str, Any]
    equipment: List[Equipment]
    modes: List[str]
    safety: Dict[str, Any]
    options: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)

    # ---- derived names ---------------------------------------------------
    @property
    def machine(self) -> str:
        return self.project["name"]

    @property
    def prefix(self) -> str:
        return self.options.get("block_prefix", "") or ""

    @property
    def fb_machine(self) -> str:
        return f"{self.prefix}FB_{self.machine}"

    @property
    def db_machine(self) -> str:
        return f"{self.prefix}DB_{self.machine}"

    @property
    def udt_alarms(self) -> str:
        return f"{self.prefix}UDT_{self.machine}Alarms"

    @property
    def udt_auto(self) -> str:
        return f"{self.prefix}UDT_{self.machine}Auto"

    @property
    def udt_cmd(self) -> str:
        return f"{self.prefix}UDT_{self.machine}Cmd"

    @property
    def ob_main(self) -> str:
        return "Main"

    def controlled(self) -> List[Equipment]:
        """Equipment that gets an FB instance in the machine FB."""
        return [e for e in self.equipment if e.typedef.fb]

    def safety_inputs(self) -> List[Equipment]:
        return [e for e in self.equipment if e.type in dev.SAFETY_TYPES]

    def by_control_role(self, role: str) -> Optional[Equipment]:
        for e in self.equipment:
            if e.options.get("control_role") == role:
                return e
        return None


# --------------------------------------------------------------------------
# Address allocation
# --------------------------------------------------------------------------
class AddressPool:
    """Allocates bit and word addresses, never handing out a reserved one."""

    def __init__(self, io_cfg: Dict[str, Any]):
        self._next_bit = {
            dev.DI: (int(io_cfg.get("di_start_byte", 0)), 0),
            dev.DO: (int(io_cfg.get("do_start_byte", 0)), 0),
        }
        self._next_word = {
            dev.AI: int(io_cfg.get("ai_start_byte", 64)),
            dev.AO: int(io_cfg.get("ao_start_byte", 64)),
        }
        self._used_bits = {dev.DI: set(), dev.DO: set()}
        self._used_words = {dev.AI: set(), dev.AO: set()}

    # ---- reservation of explicit addresses ------------------------------
    def reserve(self, kind: str, address: str) -> None:
        if kind in dev.BIT_KINDS:
            byte, bit = parse_bit_address(kind, address)
            self._used_bits[kind].add((byte, bit))
        else:
            byte = parse_word_address(kind, address)
            # A word occupies two bytes; both must stay clear.
            self._used_words[kind].add(byte)
            self._used_words[kind].add(byte + 1)

    # ---- allocation ------------------------------------------------------
    def take(self, kind: str) -> str:
        if kind in dev.BIT_KINDS:
            byte, bit = self._next_bit[kind]
            while (byte, bit) in self._used_bits[kind]:
                bit += 1
                if bit > 7:
                    bit, byte = 0, byte + 1
            self._used_bits[kind].add((byte, bit))
            nbit, nbyte = bit + 1, byte
            if nbit > 7:
                nbit, nbyte = 0, byte + 1
            self._next_bit[kind] = (nbyte, nbit)
            return f"{dev.KIND_PREFIX[kind]}{byte}.{bit}"

        byte = self._next_word[kind]
        while byte in self._used_words[kind] or (byte + 1) in self._used_words[kind]:
            byte += 2
        self._used_words[kind].add(byte)
        self._used_words[kind].add(byte + 1)
        self._next_word[kind] = byte + 2
        return f"{dev.KIND_PREFIX[kind]}{byte}"


def parse_bit_address(kind: str, address: str):
    m = BIT_ADDR_RE.match(address.strip())
    if not m:
        raise SpecError(f"'{address}' is not a valid bit address (expected %I0.0 / %Q1.2)")
    want = "I" if kind == dev.DI else "Q"
    if m.group("pfx") != want:
        raise SpecError(f"'{address}' has the wrong area for a {kind} signal (expected %{want})")
    return int(m.group("byte")), int(m.group("bit"))


def parse_word_address(kind: str, address: str) -> int:
    m = WORD_ADDR_RE.match(address.strip())
    if not m:
        raise SpecError(f"'{address}' is not a valid word address (expected %IW64 / %QW64)")
    want = "I" if kind == dev.AI else "Q"
    if m.group("pfx") != want:
        raise SpecError(f"'{address}' has the wrong area for a {kind} signal (expected %{want}W)")
    byte = int(m.group("byte"))
    if byte % 2:
        raise SpecError(f"'{address}' must start on an even byte")
    return byte


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def load(path: str) -> Spec:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise SpecError(
                "PyYAML is required to read YAML specs. Install it with "
                "'pip install -r generator/requirements.txt', or use a .json spec."
            ) from exc
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    if not isinstance(raw, dict):
        raise SpecError(f"{os.path.basename(path)} must contain a mapping at the top level")
    return from_dict(raw)


def from_dict(raw: Dict[str, Any]) -> Spec:
    if "project" not in raw:
        raise SpecError("spec is missing the required 'project' section")
    if "plc" not in raw:
        raise SpecError("spec is missing the required 'plc' section")

    project = dict(raw["project"])
    project.setdefault("tia_version", "V21")
    project.setdefault("author", "tiagen")
    project.setdefault("comment", "")
    name = project.get("name", "")
    if not IDENT_RE.match(str(name)):
        raise SpecError(
            f"project.name '{name}' must start with a letter and contain only letters, "
            "digits and underscores (it becomes part of block names)"
        )

    plc = dict(raw["plc"])
    plc.setdefault("name", "PLC_1")
    plc.setdefault("firmware", "V1.1")
    plc.setdefault("subnet_mask", "255.255.255.0")
    plc.setdefault("cycle_ms", 100)
    plc.setdefault("modules", [])
    if not plc.get("order_number"):
        raise SpecError("plc.order_number is required (the CPU's MLFB, e.g. 6ES7214-1AH50-0XB0)")

    hmi = dict(raw["hmi"]) if raw.get("hmi") else None
    if hmi is not None:
        hmi.setdefault("name", "HMI_1")
        hmi.setdefault("runtime", "unified")
        hmi.setdefault("resolution", "1920x1080")

    network = dict(raw.get("network") or {})
    network.setdefault("subnet_name", "PN_IE_1")
    network.setdefault("io_devices", [])

    io_cfg = dict(raw.get("io") or {})
    options = dict(raw.get("options") or {})
    options.setdefault("generate_hmi_tags", True)
    options.setdefault("generate_alarms", True)
    options.setdefault("optimized_access", True)
    options.setdefault("block_prefix", "")

    modes = list(raw.get("modes") or ["Manual", "Auto"])
    safety = dict(raw.get("safety") or {})
    safety.setdefault("fail_safe_plc", False)

    equipment = [_equipment_from_dict(e, i) for i, e in enumerate(raw.get("equipment") or [])]

    spec = Spec(
        project=project,
        plc=plc,
        hmi=hmi,
        network=network,
        io=io_cfg,
        equipment=equipment,
        modes=modes,
        safety=safety,
        options=options,
    )
    _allocate_addresses(spec)
    return spec


def _equipment_from_dict(raw: Dict[str, Any], index: int) -> Equipment:
    if not isinstance(raw, dict):
        raise SpecError(f"equipment[{index}] must be a mapping")
    name = raw.get("name")
    etype = raw.get("type")
    if not name:
        raise SpecError(f"equipment[{index}] is missing 'name'")
    if not IDENT_RE.match(str(name)):
        raise SpecError(
            f"equipment name '{name}' must start with a letter and contain only "
            "letters, digits and underscores"
        )
    if str(name).lower() in SCL_RESERVED:
        raise SpecError(f"equipment name '{name}' is an SCL keyword; pick another name")
    if etype not in dev.TYPES:
        known = ", ".join(sorted(dev.TYPES))
        raise SpecError(f"equipment '{name}' has unknown type '{etype}'. Known types: {known}")

    scaling = dict(DEFAULT_SCALING)
    scaling.update(raw.get("scaling") or {})

    eq = Equipment(
        name=str(name),
        type=str(etype),
        description=str(raw.get("description", "") or ""),
        area=str(raw.get("area", "") or ""),
        options=dict(raw.get("options") or {}),
        scaling=scaling,
    )
    eq.options.setdefault("_addr_override", raw.get("address"))
    eq.options.setdefault("_signal_overrides", dict(raw.get("signals") or {}))
    return eq


def _allocate_addresses(spec: Spec) -> None:
    pool = AddressPool(spec.io)

    # Pass 1: reserve every explicitly given address.
    for eq in spec.equipment:
        for sig in _wanted_signals(eq):
            addr = _explicit_address(eq, sig)
            if addr:
                pool.reserve(sig.kind, addr)

    # Pass 2: build the resolved signal list, auto-allocating what is left.
    seen_tags: Dict[str, str] = {}
    seen_addr: Dict[str, str] = {}
    for eq in spec.equipment:
        for sig in _wanted_signals(eq):
            explicit = _explicit_address(eq, sig)
            address = explicit or pool.take(sig.kind)
            tag = f"{eq.name}{sig.suffix}"

            if tag in seen_tags:
                raise SpecError(
                    f"tag name '{tag}' is produced by both '{seen_tags[tag]}' and "
                    f"'{eq.name}' - rename one of them"
                )
            seen_tags[tag] = eq.name

            key = f"{sig.kind}:{address}"
            if key in seen_addr:
                raise SpecError(
                    f"address {address} is used by both '{seen_addr[key]}' and '{tag}'"
                )
            seen_addr[key] = tag

            eq.signals.append(
                ResolvedSignal(
                    role=sig.role,
                    kind=sig.kind,
                    tag=tag,
                    address=address,
                    datatype=dev.KIND_DATATYPE[sig.kind],
                    comment=_signal_comment(eq, sig),
                    table=dev.KIND_TABLE[sig.kind],
                    explicit=bool(explicit),
                )
            )


def _wanted_signals(eq: Equipment):
    """The signals a piece of equipment actually has, after applying its options."""
    for sig in eq.typedef.signals:
        if sig.option is None or bool(eq.options.get(sig.option, sig.default)):
            yield sig


def _explicit_address(eq: Equipment, sig: dev.Signal) -> Optional[str]:
    overrides = eq.options.get("_signal_overrides") or {}
    if sig.role in overrides:
        return str(overrides[sig.role])
    # A bare 'address' on a single-signal type addresses that one signal.
    single = len(eq.typedef.signals) == 1
    if single and eq.options.get("_addr_override"):
        return str(eq.options["_addr_override"])
    return None


GENERIC_COMMENTS = {"digital input", "digital output", "analog input", "analog output"}


def _signal_comment(eq: Equipment, sig: dev.Signal) -> str:
    base = eq.description or eq.name
    # For single-signal equipment the description already says what the point is;
    # appending "- digital input" only pads the tag comment.
    if not sig.comment or sig.comment in GENERIC_COMMENTS:
        return base
    return f"{base} - {sig.comment}"
