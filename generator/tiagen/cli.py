"""tiagen command line interface.

    python -m tiagen validate  spec/examples/conveyor-line.yaml
    python -m tiagen build     spec/examples/conveyor-line.yaml -o out
    python -m tiagen types
    python -m tiagen explain   spec/examples/conveyor-line.yaml
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List

from . import build as build_mod
from . import devices as dev
from . import import_steps, model, validate


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tiagen",
        description="Generate TIA Portal V21 artefacts from a machine spec.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="check a spec without writing anything")
    p_val.add_argument("spec")

    p_build = sub.add_parser("build", help="generate SCL, tag tables, HMI plan and plan.json")
    p_build.add_argument("spec")
    p_build.add_argument("-o", "--out", default="out", help="output directory (default: out)")
    p_build.add_argument("--library", default=build_mod.DEFAULT_LIBRARY,
                         help="directory holding the SCL library blocks")
    p_build.add_argument("--engineering-version", default="V21",
                         help="value written to <Engineering version=...> in tag table XML")
    p_build.add_argument("--force", action="store_true",
                         help="generate even when validation reports errors")

    sub.add_parser("types", help="list the supported equipment types")

    p_exp = sub.add_parser("explain", help="print the I/O and block summary for a spec")
    p_exp.add_argument("spec")

    p_lint = sub.add_parser(
        "lint",
        help="check an exported TIA project against the house rules in rules.yaml",
    )
    p_lint.add_argument("target", nargs="?", default=".",
                        help="export directory, or a single exported file")
    p_lint.add_argument("--rules", help="rule catalogue (default: rules.yaml at the repo root)")
    p_lint.add_argument("--library", default=build_mod.DEFAULT_LIBRARY,
                        help="library directory to compare device FBs against")
    p_lint.add_argument("--min-severity", default="info",
                        choices=["error", "warning", "info"],
                        help="hide findings below this severity (default: info)")
    p_lint.add_argument("--format", default="text", choices=["text", "json"],
                        dest="output_format")
    p_lint.add_argument("--list", action="store_true", dest="list_rules",
                        help="print the rule catalogue and exit")

    p_xl = sub.add_parser(
        "excel",
        help="write a machine workbook - empty, or filled in from an existing spec",
    )
    p_xl.add_argument("workbook", help="the .xlsx to write")
    p_xl.add_argument("--from", dest="source", help="a spec (.yaml) to fill it from")
    p_xl.add_argument("--force", action="store_true", help="overwrite an existing file")

    p_fx = sub.add_parser(
        "from-excel",
        help="convert a machine workbook to a YAML spec, for review and version control",
    )
    p_fx.add_argument("workbook")
    p_fx.add_argument("-o", "--out", help="YAML file to write (default: next to the workbook)")

    p_self = sub.add_parser(
        "selftest",
        help="check this installation end to end and zip the results into one file",
    )
    p_self.add_argument("--out", help="folder for the result zip (default: current folder)")
    p_self.add_argument("--no-zip", action="store_true",
                        help="leave the results as a folder instead of a zip")

    p_imp = sub.add_parser(
        "import-steps",
        help="turn a step spreadsheet (CSV) into the spec's sequence section",
    )
    p_imp.add_argument("csv")
    p_imp.add_argument("-o", "--out", help="write here instead of stdout")
    p_imp.add_argument("--name", default="Cycle", help="sequence name (default: Cycle)")

    args = parser.parse_args(argv)

    try:
        if args.command == "types":
            return _cmd_types()
        if args.command == "validate":
            return _cmd_validate(args.spec)
        if args.command == "explain":
            return _cmd_explain(args.spec)
        if args.command == "build":
            return _cmd_build(args)
        if args.command == "import-steps":
            return _cmd_import_steps(args)
        if args.command == "lint":
            return _cmd_lint(args)
        if args.command == "selftest":
            return _cmd_selftest(args)
        if args.command == "excel":
            return _cmd_excel(args)
        if args.command == "from-excel":
            return _cmd_from_excel(args)
    except model.SpecError as exc:
        print(f"ERROR   {exc}", file=sys.stderr)
        return 2
    except import_steps.ImportError_ as exc:
        print(f"ERROR   {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"ERROR   {exc}", file=sys.stderr)
        return 2
    return 1


def _cmd_excel(args) -> int:
    from . import workbook

    if os.path.exists(args.workbook) and not args.force:
        print(f"ERROR   {args.workbook} already exists. Pass --force to overwrite it.",
              file=sys.stderr)
        return 2
    raw = None
    if args.source:
        import yaml

        with open(args.source, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        # Load it properly too, so a spec that would not build is not turned into a
        # workbook that looks fine.
        model.load(args.source)
    workbook.write_workbook(args.workbook, raw)
    what = f"filled in from {args.source}" if args.source else "empty template"
    print(f"Wrote {args.workbook} ({what}).")
    print("Fill in the Machine and Equipment sheets, then:")
    print(f"  tiagen validate {args.workbook}")
    print(f"  tiagen build    {args.workbook} -o out")
    return 0


def _cmd_from_excel(args) -> int:
    import yaml

    from . import workbook

    raw = workbook.read_workbook(args.workbook)
    model.from_dict(raw)                      # same checks as validate's first stage
    out = args.out or os.path.splitext(args.workbook)[0] + ".yaml"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# Generated from {os.path.basename(args.workbook)} by tiagen from-excel.\n")
        fh.write("# The workbook is the source; regenerate this rather than editing it.\n\n")
        yaml.safe_dump(raw, fh, sort_keys=False, allow_unicode=True, width=100)
    print(f"Wrote {out}")
    return 0


def _cmd_selftest(args) -> int:
    from . import selftest

    report, path = selftest.run(out_dir=args.out, make_zip=not args.no_zip)
    print(selftest.format_report(report), end="")
    print()
    print(f"Results: {path}")
    print("Attach that file to a message - it holds everything needed to diagnose a problem,")
    print("and no user or machine names.")
    return 1 if report.failed else 0


def _cmd_lint(args) -> int:
    import json as _json

    from . import lint as lint_mod

    rules = lint_mod.load_rules(args.rules)

    if args.list_rules:
        for rule in rules:
            state = "enabled " if rule.enabled else "disabled"
            impl = "" if rule.implemented else "   [NOT IMPLEMENTED]"
            print(f"{rule.id}  {rule.severity.value:<7} {state} {rule.name}{impl}")
            print(f"        {rule.description}")
        missing = [r.id for r in rules if r.enabled and not r.implemented]
        print()
        print(f"{len(rules)} rules, {sum(1 for r in rules if r.enabled)} enabled, "
              f"{len(missing)} without an implementation"
              + (f": {', '.join(missing)}" if missing else ""))
        return 0

    project = lint_mod.load_project(args.target, library_dir=args.library)
    findings = lint_mod.filter_by_severity(
        lint_mod.run(project, rules), args.min_severity
    )

    if args.output_format == "json":
        print(_json.dumps(lint_mod.as_json(findings, project, rules), indent=2))
    else:
        print(lint_mod.format_report(findings, project, rules), end="")

    # Non-zero on an error so this can gate a pull request.
    return 1 if any(f.severity is lint_mod.Severity.ERROR for f in findings) else 0


def _cmd_import_steps(args) -> int:
    with open(args.csv, "r", encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    steps = import_steps.parse_csv(text)
    body = import_steps.to_yaml(steps, args.name)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(body)
        print(f"{len(steps)} steps -> {args.out}")
        print("Paste it into the spec, or append it if the spec has no sequence yet.")
        print("Then: python -m tiagen validate <spec>   - every action and condition is")
        print("checked against the equipment list, so typos surface now rather than in TIA.")
    else:
        print(body, end="")
    return 0


def _cmd_types() -> int:
    for name in sorted(dev.TYPES):
        t = dev.TYPES[name]
        print(f"{name}")
        print(f"    {t.summary}")
        print(f"    FB: {t.fb or '(tag only)'}")
        for sig in t.signals:
            optional = f"  [optional via options.{sig.option}]" if sig.option else ""
            print(f"    {sig.kind:<3} {sig.role:<12} tag suffix '{sig.suffix}'{optional}")
        print()
    return 0


def _cmd_validate(path: str) -> int:
    spec = model.load(path)
    errors, warnings = validate.check(spec)
    print(validate.format_report(errors, warnings))
    return 1 if errors else 0


def _cmd_explain(path: str) -> int:
    spec = model.load(path)
    from . import emit_tags

    tables = emit_tags.build_tables(spec)
    print(f"{spec.machine}: {emit_tags.summarise(tables) or 'no I/O'}")
    print()
    for table, tags in tables.items():
        print(f"[{table}]")
        for tag in tags:
            print(f"  {tag.address:<10} {tag.name:<28} {tag.datatype:<6} {tag.comment}")
        print()
    print("Blocks to be created:")
    for block in _expected_blocks(spec):
        print(f"  {block}")
    return 0


def _expected_blocks(spec) -> List[str]:
    from . import emit_plan

    return emit_plan._expected_blocks(spec)


def _cmd_build(args) -> int:
    spec = model.load(args.spec)
    result = build_mod.build(
        spec,
        out_dir=args.out,
        library_dir=args.library,
        force=args.force,
        engineering_version=args.engineering_version,
    )

    for warning in result.warnings:
        print(f"WARNING {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"ERROR   {error}", file=sys.stderr)

    if not result.ok and not args.force:
        print("\nNothing was generated. Fix the errors above, or pass --force.", file=sys.stderr)
        return 1

    print(f"Wrote {len(result.files)} files to {os.path.abspath(result.out_dir)}")
    for relative in result.files:
        # Paths are built two ways inside build(); show them in the platform's own
        # form, so a Windows user does not see scl\a.scl next to scl/b.scl.
        print(f"  {os.path.normpath(relative)}")
    print(f"\nReview {os.path.join(result.out_dir, 'report.md')}, then run the Openness driver:")
    print(f"  TiaGen.Openness.exe apply --plan {os.path.join(result.out_dir, 'plan.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
