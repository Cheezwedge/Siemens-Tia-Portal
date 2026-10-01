"""The machine workbook: an Excel file that is a complete machine spec.

Engineers already describe machines in spreadsheets - an IO list, a device list, a
step table. This lets that spreadsheet *be* the spec, instead of a source somebody
retypes into YAML. `validate`, `build` and `explain` accept the .xlsx directly;
`from-excel` writes the equivalent YAML for review and version control; `excel` goes
the other way and turns any spec into a filled workbook.

    Machine      settings, one per row: project, PLC, HMI, network, safety
    Equipment    the IO list - one row per device
    IO devices   PROFINET IO devices (remote racks, valve terminals)
    Modules      modules, under the PLC or under an IO device
    Sequence     the step table
    Help         generated from the code - types, statuses, verbs

Every problem is reported by sheet and row, because "Equipment row 14" is how the
person holding the spreadsheet will look for it. Every problem is reported at once,
too: fixing one error per run is a bad way to spend an afternoon.

Column headings are matched loosely - case, spaces and punctuation do not matter -
so a workbook survives someone retyping a heading.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import xlsx
from .model import SpecError


class WorkbookError(SpecError):
    """One or more problems in a workbook, each located by sheet and row."""

    def __init__(self, problems: List[str]):
        self.problems = problems
        super().__init__(
            f"{len(problems)} problem(s) in the workbook:\n  " + "\n  ".join(problems))


# --------------------------------------------------------------------------
# Value conversion - cells arrive as text
# --------------------------------------------------------------------------
_TRUE = {"yes", "y", "true", "1", "x", "ja", "on"}
_FALSE = {"no", "n", "false", "0", "nein", "off", "-"}


def _as_text(value: str) -> Optional[str]:
    value = value.strip()
    return value or None


def _as_int(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        number = float(value.replace(",", "."))
    except ValueError:
        raise ValueError(f"must be a whole number, got '{value}'")
    if number != int(number):
        raise ValueError(f"must be a whole number, got '{value}'")
    return int(number)


def _as_float(value: str) -> Optional[float]:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value.replace(",", "."))
    except ValueError:
        raise ValueError(f"must be a number, got '{value}'")


def _as_bool(value: str) -> Optional[bool]:
    value = value.strip().lower()
    if not value:
        return None
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ValueError(f"must be yes or no, got '{value}'")


def _as_list(value: str) -> Optional[List[str]]:
    # Excel cells can hold a list on several lines (Alt+Enter) or separated by ; or ,
    items = [p.strip() for p in re.split(r"[;,\n]+", value) if p.strip()]
    return items or None


def _number_cell(value):
    """A number for the sheet: an int stays an int, so 3000 is not shown as 3000.0."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _norm(heading: str) -> str:
    return re.sub(r"[^a-z0-9]", "", heading.lower())


# --------------------------------------------------------------------------
# Field tables - one place that says what each cell means
# --------------------------------------------------------------------------
# (label, path in the spec, converter, note shown in the Machine sheet)
MACHINE_FIELDS: List[Tuple[str, str, Callable, str]] = [
    ("Project name", "project.name", _as_text,
     "Required. Letters, digits and _ only - it becomes part of block names."),
    ("Author", "project.author", _as_text, ""),
    ("Comment", "project.comment", _as_text, ""),
    ("Project folder", "project.directory", _as_text,
     "Folder on the TIA PC where the project is created."),
    ("TIA version", "project.tia_version", _as_text, "V21"),
    ("PLC name", "plc.name", _as_text, ""),
    ("PLC order number", "plc.order_number", _as_text,
     "Required. Copy it from the TIA hardware catalog, character for character."),
    ("PLC firmware", "plc.firmware", _as_text, "e.g. V1.1"),
    ("PLC IP address", "plc.ip", _as_text, ""),
    ("Subnet mask", "plc.subnet_mask", _as_text, ""),
    ("Gateway", "plc.gateway", _as_text, ""),
    ("PLC PROFINET name", "plc.profinet_name", _as_text, ""),
    ("Cycle time (ms)", "plc.cycle_ms", _as_int, ""),
    ("Subnet name", "network.subnet_name", _as_text, ""),
    ("HMI name", "hmi.name", _as_text, "Leave every HMI row blank for a machine without one."),
    ("HMI order number", "hmi.order_number", _as_text, ""),
    ("HMI firmware", "hmi.firmware", _as_text, ""),
    ("HMI runtime", "hmi.runtime", _as_text, "unified, unified_basic or comfort"),
    ("HMI resolution", "hmi.resolution", _as_text, "e.g. 800x480 for a 7-inch panel"),
    ("HMI IP address", "hmi.ip", _as_text, ""),
    ("First DI byte", "io.di_start_byte", _as_int, "Where automatic address allocation starts."),
    ("First DO byte", "io.do_start_byte", _as_int, ""),
    ("First AI byte", "io.ai_start_byte", _as_int, ""),
    ("First AO byte", "io.ao_start_byte", _as_int, ""),
    ("Modes", "modes", _as_list, "Comma separated: Manual, Auto, SemiAuto, Setup, Clean"),
    ("Safety category", "safety.category", _as_text, "e.g. PLd Cat.3 per ISO 13849-1"),
    ("Fail-safe PLC", "safety.fail_safe_plc", _as_bool,
     "Recorded only. This tool never writes safety logic."),
    ("Safety notes", "safety.notes", _as_text, ""),
    ("Sequence name", "sequence.name", _as_text, "Used when the Sequence sheet has steps."),
    ("Sequence cyclic", "sequence.cyclic", _as_bool, "yes = last step returns to the first"),
    ("Sequence idle step", "sequence.idle_step", _as_int, ""),
]

# (heading, key path within one equipment entry, converter, column width)
EQUIPMENT_COLUMNS: List[Tuple[str, str, Callable, float]] = [
    ("Name", "name", _as_text, 18),
    ("Type", "type", _as_text, 16),
    ("Description", "description", _as_text, 34),
    ("Area", "area", _as_text, 12),
    ("Address", "address", _as_text, 10),
    ("Signal addresses", "signals", None, 22),            # role=%addr; ...
    ("Control role", "options.control_role", _as_text, 12),
    ("Normally closed", "options.normally_closed", _as_bool, 10),
    ("Interlock from", "options.interlock_from", _as_list, 18),
    ("Running feedback", "options.has_running_feedback", _as_bool, 10),
    ("Fault feedback", "options.has_fault_feedback", _as_bool, 10),
    ("Opened feedback", "options.has_opened_feedback", _as_bool, 10),
    ("Closed feedback", "options.has_closed_feedback", _as_bool, 10),
    ("Feedback timeout (ms)", "options.feedback_timeout_ms", _as_int, 12),
    ("Travel timeout (ms)", "options.travel_timeout_ms", _as_int, 12),
    ("Raw min", "scaling.raw_min", _as_float, 9),
    ("Raw max", "scaling.raw_max", _as_float, 9),
    ("Eng min", "scaling.eng_min", _as_float, 9),
    ("Eng max", "scaling.eng_max", _as_float, 9),
    ("Unit", "scaling.unit", _as_text, 8),
    ("Lo-Lo", "scaling.lo_lo", _as_float, 8),
    ("Lo", "scaling.lo", _as_float, 8),
    ("Hi", "scaling.hi", _as_float, 8),
    ("Hi-Hi", "scaling.hi_hi", _as_float, 8),
]

IO_DEVICE_COLUMNS: List[Tuple[str, str, Callable, float]] = [
    ("Name", "name", _as_text, 18),
    ("Order number", "order_number", _as_text, 22),
    ("Firmware", "firmware", _as_text, 10),
    ("IP address", "ip", _as_text, 15),
    ("PROFINET name", "profinet_name", _as_text, 20),
    ("GSD type identifier", "type_identifier", _as_text, 30),
]

MODULE_COLUMNS: List[Tuple[str, str, Callable, float]] = [
    ("Parent", "_parent", _as_text, 16),
    ("Name", "name", _as_text, 18),
    ("Order number", "order_number", _as_text, 26),
    ("Slot", "slot", _as_int, 6),
    ("Firmware", "firmware", _as_text, 10),
]

SEQUENCE_COLUMNS: List[Tuple[str, float]] = [
    ("Step", 7), ("Name", 16), ("Message", 40), ("Actions", 30), ("Wait for", 30),
    ("Any of", 7), ("Timeout", 9), ("On timeout", 11), ("Next", 7),
]

SHEETS = ("Machine", "Equipment", "IO devices", "Modules", "Sequence")


def _set(target: Dict[str, Any], path: str, value) -> None:
    keys = path.split(".")
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = value


def _get(source: Dict[str, Any], path: str):
    for key in path.split("."):
        if not isinstance(source, dict) or key not in source:
            return None
        source = source[key]
    return source


# --------------------------------------------------------------------------
# Reading a workbook into a spec
# --------------------------------------------------------------------------
def read_workbook(path: str) -> Dict[str, Any]:
    """The workbook as a raw spec mapping, ready for model.from_dict."""
    try:
        sheets = xlsx.read(path)
    except xlsx.XlsxError as exc:
        raise WorkbookError([str(exc)])

    by_name = {name.strip().lower(): rows for name, rows in sheets.items()}
    problems: List[str] = []
    for required in ("Machine", "Equipment"):
        if required.lower() not in by_name:
            problems.append(
                f"no '{required}' sheet. Sheets found: {', '.join(sheets) or 'none'}. "
                "Start from `tiagen excel <file>.xlsx` for the expected layout.")
    if problems:
        raise WorkbookError(problems)

    spec: Dict[str, Any] = {}
    _read_machine(by_name["machine"], spec, problems)
    spec["equipment"] = _read_table(by_name["equipment"], "Equipment", EQUIPMENT_COLUMNS,
                                    problems, _equipment_row)
    _check_equipment(spec["equipment"], problems)

    devices = _read_table(by_name.get("io devices", []), "IO devices", IO_DEVICE_COLUMNS,
                          problems)
    if devices:
        spec.setdefault("network", {})["io_devices"] = devices
    _read_modules(by_name.get("modules", []), spec, devices, problems)
    _read_sequence(by_name.get("sequence", []), spec, problems)

    if problems:
        raise WorkbookError(_in_sheet_order(problems))
    return spec


def _in_sheet_order(problems: List[str]) -> List[str]:
    """Sort problems the way someone fixing them walks the workbook: sheet, then row.

    They are found in check order - headings, then values, then cross-row checks -
    which sends the reader back and forth through the same sheet.
    """
    order = {name.lower(): i for i, name in enumerate(SHEETS)}

    def key(problem: str):
        match = re.match(r"([A-Za-z ]+?)(?: row (\d+))?[ :(]", problem)
        if not match:
            return (len(order), 0, problem)
        return (order.get(match.group(1).strip().lower(), len(order)),
                int(match.group(2) or 0), problem)

    return sorted(problems, key=key)


def _read_machine(rows, spec: Dict[str, Any], problems: List[str]) -> None:
    fields = {_norm(label): (label, path, convert) for label, path, convert, _ in MACHINE_FIELDS}
    for number, cells in rows:
        label = cells[0] if cells else ""
        if _norm(label) in ("setting", ""):
            continue
        field = fields.get(_norm(label))
        if field is None:
            problems.append(f"Machine row {number}: '{label}' is not a setting this tool knows")
            continue
        raw = cells[1] if len(cells) > 1 else ""
        try:
            value = field[2](raw)
        except ValueError as exc:
            problems.append(f"Machine row {number} ({field[0]}): {exc}")
            continue
        if value is not None:
            _set(spec, field[1], value)

    for label, path in (("Project name", "project.name"), ("PLC order number", "plc.order_number")):
        if _get(spec, path) is None:
            problems.append(f"Machine: '{label}' is required")


def _header_map(header: List[str], columns, sheet: str, number: int,
                problems: List[str]) -> Dict[int, Tuple]:
    known = {_norm(c[0]): c for c in columns}
    mapping: Dict[int, Tuple] = {}
    for index, heading in enumerate(header):
        if not heading.strip():
            continue
        column = known.get(_norm(heading))
        if column is None:
            problems.append(f"{sheet} row {number}: column '{heading}' is not one this tool "
                            "reads, so it is ignored. Check the spelling against the Help sheet.")
            continue
        mapping[index] = column
    return mapping


def _read_table(rows, sheet: str, columns, problems: List[str],
                row_hook: Optional[Callable] = None) -> List[Dict[str, Any]]:
    if not rows:
        return []
    header_number, header = rows[0]
    mapping = _header_map(header, columns, sheet, header_number, problems)
    entries = []
    for number, cells in rows[1:]:
        entry: Dict[str, Any] = {}
        where = f"{sheet} row {number}"
        for index, column in mapping.items():
            raw = cells[index] if index < len(cells) else ""
            heading, path, convert = column[0], column[1], column[2]
            if convert is None:                     # handled by the row hook
                entry.setdefault("_raw", {})[path] = raw
                continue
            try:
                value = convert(raw)
            except ValueError as exc:
                problems.append(f"{where} ({heading}): {exc}")
                continue
            if value is not None:
                _set(entry, path, value)
        if row_hook:
            row_hook(entry, where, problems)
        entry.pop("_raw", None)
        entry["_row"] = where
        entries.append(entry)
    return entries


def _equipment_row(entry: Dict[str, Any], where: str, problems: List[str]) -> None:
    raw = (entry.get("_raw") or {}).get("signals", "")
    if raw.strip():
        signals = {}
        for part in re.split(r"[;\n]+", raw):
            if not part.strip():
                continue
            if "=" not in part:
                problems.append(f"{where} (Signal addresses): write role=address, for example "
                                f"speed_out=%QW80 - got '{part.strip()}'")
                continue
            role, _, address = (p.strip() for p in part.partition("="))
            signals[role] = address
        if signals:
            entry["signals"] = signals


def _check_equipment(equipment: List[Dict[str, Any]], problems: List[str]) -> None:
    from . import devices as dev

    seen: Dict[str, str] = {}
    for entry in equipment:
        where = entry.get("_row", "Equipment")
        name = entry.get("name")
        if not name:
            problems.append(f"{where}: Name is empty")
            continue
        where = f"{where} ({name})"
        if name in seen:
            problems.append(f"{where}: name already used on {seen[name]}")
        seen[name] = entry["_row"]
        etype = entry.get("type")
        if not etype:
            problems.append(f"{where}: Type is empty")
        elif etype not in dev.TYPES:
            close = difflib.get_close_matches(etype.strip().lower(), list(dev.TYPES), n=1)
            hint = f" Did you mean {close[0]}?" if close else ""
            problems.append(f"{where}: '{etype}' is not an equipment type.{hint} "
                            "The Help sheet lists them all.")
    for entry in equipment:
        entry.pop("_row", None)


def _read_modules(rows, spec: Dict[str, Any], devices: List[Dict[str, Any]],
                  problems: List[str]) -> None:
    modules = _read_table(rows, "Modules", MODULE_COLUMNS, problems)
    plc_names = {"plc", _norm(str(_get(spec, "plc.name") or "PLC_1"))}
    by_device = {_norm(d.get("name", "")): d for d in devices}
    for module in modules:
        where = module.pop("_row")
        parent = str(module.pop("_parent", "") or "")
        if not parent or _norm(parent) in plc_names:
            spec.setdefault("plc", {}).setdefault("modules", []).append(module)
        elif _norm(parent) in by_device:
            by_device[_norm(parent)].setdefault("modules", []).append(module)
        else:
            problems.append(f"{where}: parent '{parent}' is neither the PLC nor a row on the "
                            "IO devices sheet")
    for device in devices:
        device.pop("_row", None)


def _read_sequence(rows, spec: Dict[str, Any], problems: List[str]) -> None:
    from . import import_steps

    settings = spec.pop("sequence", None) or {}
    # The template always has a Sequence sheet. Headings with nothing under them
    # mean "no sequence", not a mistake.
    if len(rows) <= 1:
        return
    header_number, header = rows[0]
    try:
        # The step-table reader already knows the loose headings and list syntax;
        # a workbook row is the same thing as a CSV row.
        steps = import_steps.parse_rows(header, rows[1:], delimiter="\t")
    except import_steps.ImportError_ as exc:
        problems.append(f"Sequence: {exc}")
        return
    if steps:
        sequence = {"name": settings.get("name", "Cycle"), "steps": steps}
        for key in ("cyclic", "idle_step"):
            if key in settings:
                sequence[key] = settings[key]
        spec["sequence"] = sequence


# --------------------------------------------------------------------------
# Writing a spec out as a workbook
# --------------------------------------------------------------------------
def write_workbook(path: str, raw: Optional[Dict[str, Any]] = None) -> None:
    """A workbook for the given raw spec, or an empty template when raw is None."""
    from . import devices as dev

    raw = raw or {}
    sheets = [
        _machine_sheet(raw),
        _table_sheet("Equipment", EQUIPMENT_COLUMNS, [_equipment_cells(e) for e in
                                                      raw.get("equipment") or []],
                     lists={1: sorted(dev.TYPES),
                            6: ["start", "stop", "reset"],
                            **{i: ["yes", "no"] for i in (7, 9, 10, 11, 12)}}),
        _table_sheet("IO devices", IO_DEVICE_COLUMNS,
                     [[d.get(c[1]) for c in IO_DEVICE_COLUMNS]
                      for d in (raw.get("network") or {}).get("io_devices") or []]),
        _table_sheet("Modules", MODULE_COLUMNS, _module_rows(raw)),
        _sequence_sheet(raw.get("sequence")),
        _help_sheet(),
    ]
    xlsx.write(path, sheets)


def _machine_sheet(raw: Dict[str, Any]) -> xlsx.Sheet:
    rows: List[List[Any]] = [["Setting", "Value", "Notes"]]
    for label, path, convert, note in MACHINE_FIELDS:
        value = _get(raw, path)
        if convert is _as_list and isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        elif convert is _as_bool and isinstance(value, bool):
            value = "yes" if value else "no"
        elif isinstance(value, str):
            value = " ".join(value.split()) if "\n" in value else value
        elif isinstance(value, (int, float)):
            value = _number_cell(value)
        rows.append([label, value, note])
    return xlsx.Sheet("Machine", rows, widths=[22, 34, 70])


def _equipment_cells(entry: Dict[str, Any]) -> List[Any]:
    cells = []
    for heading, path, convert, _w in EQUIPMENT_COLUMNS:
        if path == "signals":
            signals = entry.get("signals") or {}
            cells.append("; ".join(f"{k}={v}" for k, v in signals.items()) or None)
            continue
        value = _get(entry, path)
        if isinstance(value, bool):
            value = "yes" if value else "no"
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value)
        elif isinstance(value, (int, float)):
            value = _number_cell(value)
        cells.append(value)
    return cells


def _module_rows(raw: Dict[str, Any]) -> List[List[Any]]:
    rows = []
    plc_name = (raw.get("plc") or {}).get("name", "PLC")
    for module in (raw.get("plc") or {}).get("modules") or []:
        rows.append([plc_name] + [module.get(c[1]) for c in MODULE_COLUMNS[1:]])
    for device in (raw.get("network") or {}).get("io_devices") or []:
        for module in device.get("modules") or []:
            rows.append([device.get("name")] + [module.get(c[1]) for c in MODULE_COLUMNS[1:]])
    return rows


def _table_sheet(name: str, columns, rows: List[List[Any]],
                 lists: Optional[Dict[int, Sequence[str]]] = None) -> xlsx.Sheet:
    return xlsx.Sheet(name, [[c[0] for c in columns]] + rows,
                      widths=[c[3] for c in columns], lists=lists or {})


def _sequence_sheet(sequence: Optional[Dict[str, Any]]) -> xlsx.Sheet:
    rows: List[List[Any]] = [[c[0] for c in SEQUENCE_COLUMNS]]
    for step in (sequence or {}).get("steps") or []:
        wait = step.get("wait_for")
        any_of = None
        if isinstance(wait, dict):
            any_of, wait = "x", wait.get("any") or []
        elif isinstance(wait, str):
            wait = [wait]
        timeout = step.get("timeout")
        rows.append([
            step.get("step"), step.get("name"), step.get("message"),
            "; ".join(step.get("actions") or []) or None,
            "; ".join(wait or []) or None,
            any_of,
            _number_cell(timeout) if isinstance(timeout, (int, float)) else timeout,
            step.get("on_timeout"), step.get("next"),
        ])
    return xlsx.Sheet("Sequence", rows, widths=[c[1] for c in SEQUENCE_COLUMNS],
                      lists={5: ["x"], 7: ["fault", "hold", "continue"]})


def _help_sheet() -> xlsx.Sheet:
    """Reference material generated from the code, so it cannot drift from it."""
    from . import devices as dev
    from . import sequence as seq

    rows: List[List[Any]] = [["Topic", "Value", "Meaning"]]
    for name in sorted(dev.TYPES):
        t = dev.TYPES[name]
        roles = ", ".join(f"{s.role} ({s.kind})" for s in t.signals)
        rows.append(["Equipment type", name, f"{t.summary}  Signals: {roles}"])
    statuses: Dict[str, List[str]] = {}
    for alias, member in seq.STATUS_ALIASES.items():
        statuses.setdefault(member, []).append(alias)
    for member, aliases in statuses.items():
        rows.append(["Wait-for status", " / ".join(aliases),
                     f"Device.{aliases[0]} - reads {member}"])
    rows.append(["Wait-for value", "Device.value >= 50",
                 "Compare a scaled analog value: >=, <=, >, <, =, <>"])
    rows.append(["Wait-for input", "InputName", "A wired digital input, by its equipment name"])
    rows.append(["Wait-for negation", "not Device.opened", "Prefix any condition with 'not'"])
    for etype in sorted(seq.ACTION_VERBS):
        verbs = ", ".join(sorted(seq.ACTION_VERBS[etype]))
        rows.append(["Action verbs", etype, verbs])
    rows += [
        ["Lists in a cell", "a; b  or  a, b  or one per line (Alt+Enter)",
         "Actions, Wait for, Interlock from and Modes all accept these"],
        ["Yes / no cells", "yes, no, x, true, false, 1, 0", "Blank means the default"],
        ["Timeout", "500ms, 10s, 2m or a plain number", "A plain number is seconds"],
        ["Signal addresses", "speed_out=%QW80; running_fb=%I2.0",
         "Only to pin an address; blank means allocated automatically"],
        ["Modules: Parent", "the PLC name, or an IO device name",
         "Which rack the module sits in"],
        ["Order numbers", "copy from the TIA hardware catalog",
         "Openness matches them character for character - never type them from memory"],
    ]
    return xlsx.Sheet("Help", rows, widths=[20, 34, 100], wrap=True)
