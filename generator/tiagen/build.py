"""Turns a spec into the complete artefact set under an output directory."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List

from . import devices as dev
from . import emit_hmi, emit_plan, emit_scl, emit_seq, emit_tags, validate
from .model import Spec, SpecError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_LIBRARY = os.path.join(REPO_ROOT, "library", "scl")


@dataclass
class BuildResult:
    out_dir: str
    files: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    report: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


def build(spec: Spec, out_dir: str, library_dir: str = DEFAULT_LIBRARY,
          force: bool = False, engineering_version: str = "V21") -> BuildResult:
    errors, warnings = validate.check(spec)
    result = BuildResult(out_dir=out_dir, errors=errors, warnings=warnings)
    if errors and not force:
        result.report = validate.format_report(errors, warnings)
        return result

    scl_dir = os.path.join(out_dir, "scl")
    tags_dir = os.path.join(out_dir, "tags")
    hmi_dir = os.path.join(out_dir, "hmi")
    for path in (scl_dir, tags_dir, hmi_dir):
        os.makedirs(path, exist_ok=True)

    written: List[str] = []
    source_files: List[str] = []

    def write(relative: str, content: str) -> None:
        full = os.path.join(out_dir, relative)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        written.append(relative)

    # ---- 1. library SCL, copied so the output directory is self-contained ----
    for name in dev.LIBRARY_FILES:
        src = os.path.join(library_dir, name)
        if not os.path.exists(src):
            raise SpecError(f"library block '{name}' is missing from {library_dir}")
        with open(src, "r", encoding="utf-8") as fh:
            body = fh.read()
        relative = os.path.join("scl", name)
        write(relative, body)
        source_files.append(relative.replace(os.sep, "/"))

    # ---- 2. machine specific SCL ----
    machine_sources = [
        (f"30_{spec.udt_auto}.scl", emit_scl.emit_udt_auto(spec)),
        (f"31_{spec.udt_cmd}.scl", emit_scl.emit_udt_cmd(spec)),
        (f"32_{spec.udt_alarms}.scl", emit_scl.emit_udt_alarms(spec)),
    ]
    if spec.sequence:
        # The condition struct and the sequencer are declared by the machine FB, so
        # both have to be imported before it.
        machine_sources += [
            (f"33_{spec.udt_cond}.scl", emit_seq.emit_udt_cond(spec, spec.sequence)),
            (f"38_{spec.fb_sequence}.scl", emit_seq.emit_fb_sequence(spec, spec.sequence)),
        ]
    machine_sources.append(
        (f"40_{spec.fb_machine}.scl", emit_scl.emit_fb_machine(spec))
    )
    for name, body in machine_sources:
        relative = f"scl/{name}"
        write(relative, body)
        source_files.append(relative)

    # The OB is imported last, after the instance DB it calls has been created.
    ob_relative = f"scl/60_{spec.ob_main}.scl"
    write(ob_relative, emit_scl.emit_ob_main(spec))

    # ---- 3. PLC tag tables ----
    tables = emit_tags.build_tables(spec)
    write("tags/plc_tags.csv", emit_tags.emit_csv(tables))
    for name, tags in tables.items():
        write(f"tags/{name}.xml", emit_tags.emit_xml(name, tags, engineering_version))

    # ---- 4. HMI artefacts ----
    if spec.hmi:
        write("hmi/hmi_tags.json", emit_hmi.dumps(emit_hmi.hmi_tags(spec)))
        write("hmi/alarms.json", emit_hmi.dumps(emit_hmi.alarm_list(spec)))
        write("hmi/screens.json", emit_hmi.dumps(emit_hmi.screen_plan(spec)))
        write("hmi/hmi_tags.csv", _hmi_tags_csv(spec))
        if spec.sequence:
            write("hmi/textlists.json",
                  emit_hmi.dumps(emit_seq.text_lists(spec, spec.sequence)))

    # ---- 5. the build plan for the Openness driver ----
    plan = emit_plan.build(spec, source_files + [ob_relative], tables)
    write("plan.json", json.dumps(plan, indent=2) + "\n")

    # ---- 6. human-readable report ----
    report = _report(spec, tables, plan, warnings)
    write("report.md", report)

    result.files = written
    result.report = report
    return result


def _hmi_tags_csv(spec: Spec) -> str:
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["Table", "Name", "DataType", "PlcTag", "Access", "Comment"])
    for tag in emit_hmi.hmi_tags(spec):
        writer.writerow([tag["table"], tag["name"], tag["datatype"],
                         tag["plc_tag"], tag["access"], tag["comment"]])
    return out.getvalue()


def _sequence_report(spec: Spec) -> List[str]:
    """The step table, so it can be diffed against the spreadsheet it came from."""
    from . import sequence as seq_mod

    seq = spec.sequence
    lines = [
        f"## Sequence `{seq.name}`",
        "",
        f"{len(seq.steps)} steps, "
        + ("cyclic" if seq.cyclic else "single-shot")
        + f", idle at {seq.idle_number}. Block: `{spec.fb_sequence}`.",
        "",
        "| Step | Name | Message | Requests | Waits for | Timeout |",
        "|---|---|---|---|---|---|",
    ]
    for step in seq.steps:
        requests = ", ".join(f"`{a}`" for a in step.actions) or "-"
        waits = ", ".join(f"`{c}`" for c in step.wait_for) or "-"
        if step.wait_for and step.wait_mode == "any":
            waits = "any of: " + waits
        timeout = f"{step.timeout_ms} ms ({step.on_timeout})" if step.timeout_ms else "-"
        lines.append(
            f"| {step.number} | {step.name} | {step.message} | {requests} | {waits} | {timeout} |"
        )
    lines.append("")

    reasons = seq_mod.reasons_of(spec, seq)
    lines += [
        f"Operator text comes from two text lists in `hmi/textlists.json`: the step "
        f"message keyed by `Seq.Step`, and {len(reasons)} blocked-reasons keyed by "
        "`Seq.BlockedById`. Both are plain value lists, so a panel without scripting "
        "can resolve them.",
        "",
    ]
    return lines


def _report(spec: Spec, tables, plan: Dict, warnings: List[str]) -> str:
    lines = [
        f"# {spec.machine} - generated build",
        "",
        f"- CPU: `{spec.plc['order_number']}` firmware {spec.plc.get('firmware')} "
        f"as `{spec.plc['name']}`",
    ]
    if spec.plc.get("ip"):
        lines.append(f"- PLC address: {spec.plc['ip']} / {spec.plc.get('subnet_mask')}")
    if spec.hmi:
        lines.append(
            f"- HMI: `{spec.hmi['order_number']}` as `{spec.hmi['name']}` "
            f"({spec.hmi.get('runtime')}, {spec.hmi.get('resolution')})"
        )
    modules = spec.plc.get("modules") or []
    if modules:
        lines.append("- PLC modules: " + ", ".join(
            f"slot {m['slot']} `{m['order_number']}` ({m['name']})" for m in modules
        ))
    io_devices = spec.network.get("io_devices") or []
    if io_devices:
        lines.append("- PROFINET IO devices: " + ", ".join(d["name"] for d in io_devices))

    lines += ["", "## I/O", "", f"{emit_tags.summarise(tables) or 'no I/O'}", ""]
    highest = emit_tags.highest_addresses(tables)
    if highest:
        lines.append("Highest address used per area:")
        lines.append("")
        for table, address in highest.items():
            lines.append(f"- {table}: `{address}`")
        lines.append("")

    lines += ["## Equipment", "", "| Name | Type | Area | Signals |", "|---|---|---|---|"]
    for eq in spec.equipment:
        signals = ", ".join(f"`{s.tag}`={s.address}" for s in eq.signals) or "-"
        lines.append(f"| {eq.name} | {eq.type} | {eq.area or '-'} | {signals} |")
    lines.append("")

    lines += ["## Blocks", ""]
    for block in plan["verification"]["expect_blocks"]:
        lines.append(f"- `{block}`")
    lines.append("")

    if spec.sequence:
        lines += _sequence_report(spec)

    if spec.hmi:
        hmi_plan = plan["hmi_software"]
        lines += ["## HMI", ""]
        lines.append(f"- {sum(len(t['tags']) for t in hmi_plan['tag_tables'])} tags in "
                     f"{len(hmi_plan['tag_tables'])} tables")
        lines.append(f"- {len(hmi_plan['alarms'])} discrete alarms")
        lines.append("- screens: " + ", ".join(
            f"{s['name']} ({s['object_count']} objects)" for s in hmi_plan["screens"]
        ))
        lines.append("")
        faceplates = emit_hmi.screen_plan(spec)["faceplates_required"]
        if faceplates:
            lines.append("Faceplates the screen plan expects: " +
                         ", ".join(f"`{f}`" for f in faceplates))
            lines.append("")

    lines += ["## Next steps", "",
              "1. Review `plan.json` and `scl/`.",
              "2. Run the Openness driver on the engineering PC:",
              "   `TiaGen.Openness.exe apply --plan out/plan.json`",
              "3. Write the auto sequence in the marked region of "
              f"`scl/40_{spec.fb_machine}.scl`, then re-import that one source.",
              ""]

    if warnings:
        lines += ["## Warnings", ""]
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)


def copy_library(destination: str, library_dir: str = DEFAULT_LIBRARY) -> List[str]:
    """Copy the SCL library on its own - useful for importing into an existing project."""
    os.makedirs(destination, exist_ok=True)
    copied = []
    for name in dev.LIBRARY_FILES:
        shutil.copy2(os.path.join(library_dir, name), os.path.join(destination, name))
        copied.append(name)
    return copied
