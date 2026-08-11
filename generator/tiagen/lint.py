"""Conformance checking for an existing TIA project, exported to text.

`validate` checks a spec before any code exists, so the generator cannot emit a
defect. `lint` checks the other direction: a project that already exists, including
the parts nobody generated - hand-written blocks, legacy machines, and library
blocks somebody improved locally.

Rules live in `rules.yaml` as data, with an id, a severity and an enable flag. The
engine dispatches each to `check_<name>` in `lint_rules`. That layout is borrowed
from LadderLogicReview, and the reason to borrow it is the ids: a waiver recorded
as "L004 waived on this machine, because ..." is reviewable, where an argument
about a paragraph is not.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

DEFAULT_RULES_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "rules.yaml",
)

# Files worth reading out of an export directory, by what they are.
SOURCE_GLOBS = ("*.s7dcl", "*.scl", "*.xml", "*.udt", "*.db")
RESOURCE_GLOBS = ("*.s7res",)


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"error": 3, "warning": 2, "info": 1}[self.value]


@dataclass
class Finding:
    rule_id: str
    rule_name: str
    severity: Severity
    message: str
    path: str = ""
    line: Optional[int] = None
    block: str = ""

    def location(self) -> str:
        parts = []
        if self.path:
            parts.append(os.path.basename(self.path))
        if self.block:
            parts.append(self.block)
        if self.line is not None:
            parts.append(f"line {self.line}")
        return " / ".join(parts) if parts else "-"


@dataclass
class Rule:
    id: str
    name: str
    severity: Severity
    enabled: bool
    description: str
    check: Optional[Callable] = field(default=None, repr=False)

    @property
    def implemented(self) -> bool:
        return self.check is not None


@dataclass
class SourceFile:
    """One exported file, with the text and whatever structure we could recover."""
    path: str
    text: str
    kind: str = "unknown"          # s7dcl | scl | xml | s7res | unknown
    blocks: List["Block"] = field(default_factory=list)

    @property
    def lines(self) -> List[str]:
        return self.text.splitlines()


@dataclass
class Block:
    """A block recovered from an exported file.

    Only the fields the parser can fill honestly are here. The structural fields -
    networks, instructions - arrive with the .s7dcl parser; rules that need them
    check `structured` first rather than silently passing on an empty list.
    """
    name: str
    kind: str = ""                 # FB | FC | OB | DB | UDT
    language: str = ""             # LAD | FBD | SCL | STL | GRAPH
    title: str = ""
    comment: str = ""
    start_line: int = 1
    text: str = ""
    networks: List["Network"] = field(default_factory=list)
    structured: bool = False       # False = text recovered, structure not parsed


@dataclass
class Network:
    """One network / rung inside a block."""
    number: int
    title: str = ""
    comment: str = ""
    start_line: int = 1
    text: str = ""

    @property
    def has_comment(self) -> bool:
        return bool(self.title.strip() or self.comment.strip())


@dataclass
class Project:
    """Everything lint was given, plus the library it is being compared against."""
    files: List[SourceFile] = field(default_factory=list)
    library_dir: str = ""
    root: str = ""

    @property
    def blocks(self) -> List[Block]:
        return [b for f in self.files for b in f.blocks]

    def file_of(self, block: Block) -> Optional[SourceFile]:
        for f in self.files:
            if block in f.blocks:
                return f
        return None

    @property
    def structured_blocks(self) -> List[Block]:
        return [b for b in self.blocks if b.structured]


# --------------------------------------------------------------------------
# Rule loading
# --------------------------------------------------------------------------
def load_rules(path: Optional[str] = None) -> List[Rule]:
    import yaml

    from . import lint_rules

    rules_path = path or DEFAULT_RULES_FILE
    if not os.path.exists(rules_path):
        raise FileNotFoundError(
            f"rule catalogue not found: {rules_path}. Pass --rules <file>, or keep "
            "rules.yaml at the repository root where the team can review it."
        )
    with open(rules_path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    rules: List[Rule] = []
    for entry in config.get("rules") or []:
        try:
            severity = Severity(str(entry.get("severity", "warning")).lower())
        except ValueError:
            severity = Severity.WARNING
        name = entry["name"]
        rules.append(Rule(
            id=entry["id"],
            name=name,
            severity=severity,
            enabled=bool(entry.get("enabled", True)),
            description=" ".join((entry.get("description") or "").split()),
            check=getattr(lint_rules, f"check_{name}", None),
        ))
    return rules


def deep_rules(path: Optional[str] = None) -> List[str]:
    """The plain-English rules, for a reviewing agent rather than the engine."""
    import yaml

    rules_path = path or DEFAULT_RULES_FILE
    with open(rules_path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    return [" ".join(r.split()) for r in (config.get("deep_rules") or [])]


# --------------------------------------------------------------------------
# Loading an export
# --------------------------------------------------------------------------
def load_project(target: str, library_dir: str = "") -> Project:
    """Read an export directory, or a single exported file."""
    from . import lint_parse

    paths: List[str] = []
    if os.path.isdir(target):
        for base, _dirs, names in os.walk(target):
            for name in sorted(names):
                if any(fnmatch.fnmatch(name, g) for g in SOURCE_GLOBS + RESOURCE_GLOBS):
                    paths.append(os.path.join(base, name))
    elif os.path.isfile(target):
        paths.append(target)
    else:
        raise FileNotFoundError(f"nothing to lint at {target}")

    if not paths:
        raise FileNotFoundError(
            f"{target} holds no exported sources. Expected any of "
            + ", ".join(SOURCE_GLOBS + RESOURCE_GLOBS)
            + ". Produce them with: TiaGen.Openness.exe export --formats documents"
        )

    files = [lint_parse.read_file(p) for p in paths]
    return Project(files=files, library_dir=library_dir, root=target)


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------
def run(project: Project, rules: List[Rule]) -> List[Finding]:
    findings: List[Finding] = []
    for rule in rules:
        if not rule.enabled or rule.check is None:
            continue
        try:
            findings.extend(rule.check(project, rule) or [])
        except Exception as exc:
            # A rule that throws must not take the run down with it, and must not
            # pass silently either - a check that errored has not been performed.
            findings.append(Finding(
                rule_id=rule.id,
                rule_name=rule.name,
                severity=Severity.WARNING,
                message=f"this check could not run: {type(exc).__name__}: {exc}",
            ))
    return sorted(findings, key=lambda f: (-f.severity.rank, f.rule_id, f.path, f.line or 0))


def filter_by_severity(findings: List[Finding], minimum: str) -> List[Finding]:
    floor = Severity(minimum).rank
    return [f for f in findings if f.severity.rank >= floor]


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def format_report(findings: List[Finding], project: Project, rules: List[Rule]) -> str:
    lines: List[str] = []
    counts = {s: 0 for s in Severity}
    for f in findings:
        counts[f.severity] += 1

    for f in findings:
        lines.append(f"{f.severity.value.upper():<7} {f.rule_id}  {f.location()}")
        lines.append(f"        {f.message}")
    if findings:
        lines.append("")

    blocks = project.blocks
    unstructured = len(blocks) - len(project.structured_blocks)
    lines.append(
        f"{len(project.files)} files, {len(blocks)} blocks: "
        f"{counts[Severity.ERROR]} errors, {counts[Severity.WARNING]} warnings, "
        f"{counts[Severity.INFO]} info"
    )

    skipped = [r for r in rules if r.enabled and not r.implemented]
    if skipped:
        lines.append(
            f"{len(skipped)} enabled rules have no implementation yet and did not run: "
            + ", ".join(r.id for r in skipped)
        )
    if unstructured:
        lines.append(
            f"{unstructured} blocks were read as text only, so rules needing network "
            "structure did not apply to them."
        )
    return "\n".join(lines) + "\n"


def as_json(findings: List[Finding], project: Project, rules: List[Rule]) -> Dict:
    return {
        "root": project.root,
        "files": len(project.files),
        "blocks": len(project.blocks),
        "structured_blocks": len(project.structured_blocks),
        "rules_run": [r.id for r in rules if r.enabled and r.implemented],
        "rules_not_implemented": [r.id for r in rules if r.enabled and not r.implemented],
        "findings": [
            {
                "rule_id": f.rule_id,
                "rule": f.rule_name,
                "severity": f.severity.value,
                "message": f.message,
                "path": f.path,
                "line": f.line,
                "block": f.block,
            }
            for f in findings
        ],
    }
