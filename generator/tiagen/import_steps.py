"""Turn a step spreadsheet into the spec's sequence section.

The step table is usually the one document that already exists before any code: a
process engineer has written out the cycle, step by step, with the operator message
for each. Retyping it into YAML is where the numbers stop matching the spreadsheet,
so this reads it instead.

CSV only, deliberately - `xlsx` would mean a dependency, and every spreadsheet
exports CSV. Column names are matched loosely, because nobody's spreadsheet has a
header called `on_timeout`:

    Step | Name | Message / Description | Actions | Wait for / Condition | Timeout | On timeout | Next

Only Step is required. Actions and conditions are split on ';' or newline, so one
cell can hold several - a comma would be ambiguous with 'Vfd.speed = 1,5' in
locales that write decimals that way.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, List, Optional

# Header aliases, lowercased and stripped of anything that is not a letter.
_COLUMNS = {
    "step": ("step", "stepno", "stepnumber", "no", "nr", "schritt"),
    "name": ("name", "stepname", "title", "label"),
    "message": ("message", "description", "text", "operatormessage", "stepmessage",
                "meldung", "beschreibung"),
    "actions": ("actions", "action", "outputs", "requests", "do", "aktionen"),
    "wait_for": ("waitfor", "wait", "condition", "conditions", "transition",
                 "transitions", "weiter", "bedingung"),
    "timeout": ("timeout", "maxtime", "monitoringtime", "watchdog", "zeit"),
    "on_timeout": ("ontimeout", "timeoutaction", "onfault"),
    "next": ("next", "nextstep", "goto"),
    "any": ("any", "anyof", "or"),
}

_STEP_RE = re.compile(r"^\d+$")


class ImportError_(Exception):
    """Raised when the spreadsheet cannot be read as a step table."""


def _delimiter(text: str) -> str:
    """Pick the delimiter by counting candidates in the header row.

    csv.Sniffer gives up on a header with no commas and falls back to one, which
    turns the whole file into a single column and produces a baffling error. A
    step table's header is one line and always contains its own delimiter, so
    counting is both simpler and right more often.
    """
    header = text.splitlines()[0] if text.strip() else ""
    counts = {candidate: header.count(candidate) for candidate in (";", "\t", ",")}
    best = max(counts, key=lambda c: counts[c])
    return best if counts[best] else ","


def _norm(header: str) -> str:
    return re.sub(r"[^a-z]", "", (header or "").lower())


def _map_headers(fieldnames: List[str]) -> Dict[str, str]:
    """Map our field names to the spreadsheet's actual column names."""
    found: Dict[str, str] = {}
    for raw in fieldnames or []:
        key = _norm(raw)
        for field, aliases in _COLUMNS.items():
            if key in aliases and field not in found:
                found[field] = raw
    if "step" not in found:
        raise ImportError_(
            "no step-number column found. One column must be named Step (or No, Nr, "
            "Step number). Columns seen: " + ", ".join(fieldnames or ["<none>"])
        )
    return found


def _cell(row: Dict[str, str], columns: Dict[str, str], field: str) -> str:
    name = columns.get(field)
    if name is None:
        return ""
    return (row.get(name) or "").strip()


def _split(value: str, delimiter: str) -> List[str]:
    """Split a cell holding several actions or conditions.

    The separator cannot be the file's own delimiter - a semicolon-separated export
    cannot use semicolons inside a cell - so the alternatives are used instead. This
    matters: getting it wrong shifts every column right and produces a step table
    that looks plausible and is wrong.
    """
    others = [sep for sep in (";", ",") if sep != delimiter]
    pattern = "[" + "".join(re.escape(s) for s in others) + r"\n]+"
    return [part.strip() for part in re.split(pattern, value) if part.strip()]


def parse_csv(text: str) -> List[Dict[str, Any]]:
    """Read a step table into the mappings the sequence section expects."""
    delimiter = _delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    columns = _map_headers(reader.fieldnames or [])

    steps: List[Dict[str, Any]] = []
    for line, row in enumerate(reader, start=2):
        raw_step = _cell(row, columns, "step")
        if not raw_step:
            continue                      # blank row, or a spacer between phases
        number = _step_number(raw_step)
        if number is None:
            continue                      # a heading row like "Phase 2 - transfer"

        step: Dict[str, Any] = {"step": number}
        name = _cell(row, columns, "name")
        message = _cell(row, columns, "message")
        if name:
            step["name"] = _identifier(name)
        if message or name:
            step["message"] = message or name

        actions = _split(_cell(row, columns, "actions"), delimiter)
        if actions:
            step["actions"] = actions

        conditions = _split(_cell(row, columns, "wait_for"), delimiter)
        if conditions:
            any_of = _cell(row, columns, "any").lower() in ("1", "x", "yes", "true", "any")
            step["wait_for"] = {"any": conditions} if any_of else conditions

        timeout = _cell(row, columns, "timeout")
        if timeout:
            step["timeout"] = _timeout(timeout, number, line)

        on_timeout = _cell(row, columns, "on_timeout").lower()
        if on_timeout in ("fault", "hold", "continue"):
            step["on_timeout"] = on_timeout

        nxt = _cell(row, columns, "next")
        if nxt:
            following = _step_number(nxt)
            if following is not None:
                step["next"] = following

        steps.append(step)

    if not steps:
        raise ImportError_("no rows with a step number were found")
    return steps


def _step_number(text: str) -> Optional[int]:
    """A step number, or None for a row that is not a step.

    The cell must be nothing but digits. Searching for the first number anywhere in
    the cell turns a phase heading - "Phase 2 - transfer" - into step 2, which then
    generates a real branch nobody asked for.
    """
    return int(text) if _STEP_RE.match(text.strip()) else None


def _identifier(text: str) -> str:
    """A step name usable in generated symbols: CamelCase, no punctuation."""
    words = re.split(r"[^A-Za-z0-9]+", text)
    ident = "".join(w[:1].upper() + w[1:] for w in words if w)
    if not ident or not ident[0].isalpha():
        ident = "Step" + ident
    return ident


def _timeout(text: str, step: int, line: int) -> str:
    """Normalise a timeout cell to the duration form the spec accepts."""
    cleaned = text.strip().lower().replace(",", ".")
    if re.match(r"^\d+(\.\d+)?\s*(ms|s|m)$", cleaned):
        return re.sub(r"\s+", "", cleaned)
    match = re.match(r"^(\d+(?:\.\d+)?)$", cleaned)
    if match:
        return f"{match.group(1)}s"          # a bare number is seconds
    # A non-duration here usually means the columns are shifted - the classic cause
    # is a cell that used the file's own delimiter to separate a list. Say so, rather
    # than emitting a spec that fails later with the symptom instead of the cause.
    raise ImportError_(
        f"line {line}, step {step}: timeout '{text.strip()}' is not a duration. Write it "
        "as 500ms, 10s, 2m or a bare number of seconds. If that value looks like it "
        "belongs in another column, the row has more separators than the header - "
        "inside a cell, separate lists with the delimiter the file does not use."
    )


def to_yaml(steps: List[Dict[str, Any]], name: str = "Cycle") -> str:
    """Render the sequence section. Hand-written rather than via PyYAML so the
    result reads like the examples: quoted only where necessary, one step per
    block, comments preserved in message order."""
    lines = [f"sequence:", f"  name: {name}", "  cyclic: true", "  steps:"]
    for step in steps:
        lines.append(f"    - step: {step['step']}")
        if "name" in step:
            lines.append(f"      name: {step['name']}")
        if "message" in step:
            lines.append(f"      message: {_scalar(step['message'])}")
        if "actions" in step:
            lines.append(f"      actions: [{', '.join(_scalar(a) for a in step['actions'])}]")
        if "wait_for" in step:
            wait = step["wait_for"]
            if isinstance(wait, dict):
                items = ", ".join(_scalar(c) for c in wait["any"])
                lines.append(f"      wait_for: {{ any: [{items}] }}")
            else:
                lines.append(f"      wait_for: [{', '.join(_scalar(c) for c in wait)}]")
        if "timeout" in step:
            lines.append(f"      timeout: {step['timeout']}")
        if "on_timeout" in step:
            lines.append(f"      on_timeout: {step['on_timeout']}")
        if "next" in step:
            lines.append(f"      next: {step['next']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _scalar(value: str) -> str:
    """Quote a YAML scalar only when it needs it."""
    text = str(value)
    if not text:
        return "''"
    if re.search(r"[:#\[\]{},&*?|<>=!%@`\"']", text) or text != text.strip():
        return "'" + text.replace("'", "''") + "'"
    if text.lower() in ("yes", "no", "true", "false", "null", "~", "on", "off"):
        return f"'{text}'"
    return text
