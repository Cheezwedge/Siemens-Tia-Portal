"""`tiagen selftest` - one command, one file back.

The engineering PC is the only place TIA Portal exists, and the person sitting at it
should not have to be a debugger. This runs every check that matters, in order, and
zips the results into a single file to attach to a message. Whoever reads it sees
what failed and why without a round trip asking "what does it say when you...".

Checks degrade instead of failing where the environment legitimately lacks
something: on a build server there is no TIA Portal, and that is a warning there,
not a defect in the package.

Privacy: the bundle records whether the user is in the Openness group, not who the
user is. No user name, machine name or domain is written - it goes into a chat.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

from .build import REPO_ROOT

OPENNESS_GROUP = "Siemens TIA Openness"
OPENNESS_REGISTRY = r"SOFTWARE\Siemens\Automation\Openness"
DRIVER_RELATIVE = os.path.join("bin", "TiaGen.Openness.exe")
MANIFEST = "MANIFEST.sha256"
EXAMPLE_SPEC = os.path.join("spec", "examples", "step-sequence.yaml")


@dataclass
class Check:
    name: str
    status: str = "pass"            # pass | warn | fail | skip
    summary: str = ""
    detail: List[str] = field(default_factory=list)


@dataclass
class Report:
    started: str
    tiagen_version: str
    python: str
    platform: str
    root: str
    checks: List[Check] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(c.status == "fail" for c in self.checks)


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------
def check_package(root: str) -> Check:
    """The install folder is complete and nothing in it has been altered.

    Corporate antivirus quarantining one file out of an extracted zip is a real
    failure mode, and it presents as a baffling error three steps later. The
    manifest catches it here, by name.
    """
    check = Check("package")
    required = [
        os.path.join("generator", "tiagen", "cli.py"),
        os.path.join("library", "scl", "10_UDT_DevIf.scl"),
        "rules.yaml",
        EXAMPLE_SPEC,
    ]
    missing = [p for p in required if not os.path.exists(os.path.join(root, p))]
    if missing:
        check.status = "fail"
        check.summary = f"{len(missing)} required files missing"
        check.detail = missing
        return check

    manifest_path = os.path.join(root, MANIFEST)
    if not os.path.exists(manifest_path):
        check.status = "skip"
        check.summary = "no manifest - running from a source checkout, not an installed package"
        return check

    bad: List[str] = []
    count = 0
    with open(manifest_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            expected, relative = line.split("  ", 1)
            count += 1
            path = os.path.join(root, relative.replace("/", os.sep))
            if not os.path.exists(path):
                bad.append(f"missing: {relative}")
            elif _sha256(path) != expected:
                bad.append(f"changed: {relative}")
    if bad:
        check.status = "fail"
        check.summary = (f"{len(bad)} of {count} files missing or altered - antivirus "
                         "quarantine, or a partial extraction")
        check.detail = bad[:50]
    else:
        check.summary = f"all {count} files present and unaltered"
    return check


def check_python(root: str) -> Check:
    check = Check("python")
    embedded = os.path.abspath(sys.executable).startswith(
        os.path.abspath(os.path.join(root, "python")))
    check.summary = f"Python {platform.python_version()} ({'bundled' if embedded else 'system'})"
    try:
        import yaml  # noqa: F401
        check.detail.append(f"PyYAML {getattr(yaml, '__version__', '?')}")
    except ImportError:
        check.status = "fail"
        check.summary += " - PyYAML is missing"
    return check


def check_generator(root: str, workdir: str) -> Check:
    """Build the bundled example end to end, then lint what came out."""
    from . import build as build_mod
    from . import lint as lint_mod
    from . import model

    check = Check("generator")
    out = os.path.join(workdir, "generated")
    try:
        spec = model.load(os.path.join(root, EXAMPLE_SPEC))
        result = build_mod.build(spec, out_dir=out)
    except Exception as exc:  # report, do not crash the selftest
        check.status = "fail"
        check.summary = f"build raised {type(exc).__name__}: {exc}"
        return check
    if not result.ok:
        check.status = "fail"
        check.summary = "example spec failed validation"
        check.detail = list(result.errors)
        return check

    rules = lint_mod.load_rules()
    project = lint_mod.load_project(os.path.join(out, "scl"),
                                    library_dir=build_mod.DEFAULT_LIBRARY)
    findings = lint_mod.run(project, rules)
    check.summary = (f"built {len(result.files)} files from the example; "
                     f"lint: {len(findings)} findings")
    if findings:
        check.status = "fail"
        check.detail = [f"{f.rule_id} {f.location()}: {f.message}" for f in findings[:20]]
    return check


def check_windows() -> Check:
    check = Check("windows")
    if os.name != "nt":
        check.status = "skip"
        check.summary = f"not Windows ({platform.system()}) - TIA checks skipped"
        return check
    check.summary = platform.platform()
    return check


def check_openness_group() -> Check:
    check = Check("openness_group")
    if os.name != "nt":
        check.status = "skip"
        check.summary = "Windows only"
        return check
    try:
        output = subprocess.run(["whoami", "/groups"], capture_output=True, text=True,
                                timeout=30).stdout
    except Exception as exc:
        check.status = "warn"
        check.summary = f"could not query group membership: {exc}"
        return check
    if OPENNESS_GROUP.lower() in output.lower():
        check.summary = f"user is in '{OPENNESS_GROUP}'"
    elif not _local_group_exists(OPENNESS_GROUP):
        # TIA Portal's installer creates the group. No group means no TIA here,
        # which tia_install reports in its own words; failing twice for one cause
        # would bury it.
        check.status = "warn"
        check.summary = (f"the '{OPENNESS_GROUP}' group does not exist on this machine - "
                         "TIA Portal is not installed here")
    else:
        check.status = "fail"
        check.summary = (f"user is NOT in '{OPENNESS_GROUP}' - TIA Portal will refuse the "
                         "connection. IT can add the user, or push it by group policy. "
                         "Sign out and back in afterwards.")
    return check


def check_tia_install() -> Check:
    """What Openness installations the registry knows about, and their assemblies."""
    check = Check("tia_install")
    if os.name != "nt":
        check.status = "skip"
        check.summary = "Windows only"
        return check
    try:
        import winreg
    except ImportError:
        check.status = "skip"
        return check

    found: List[str] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, OPENNESS_REGISTRY, 0,
                              winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except OSError:
        check.status = "warn"
        check.summary = (f"no Openness installation under HKLM\\{OPENNESS_REGISTRY}. "
                         "Expected on a build server; on the engineering PC it means TIA "
                         "Portal is not installed.")
        return check

    for portal in _subkeys(winreg, root):
        found.append(f"TIA Portal {portal}")
        for path in _assembly_paths(winreg, root, portal):
            base = os.path.join(path, "Siemens.Engineering.Base.dll")
            state = "present" if os.path.exists(base) else "MISSING"
            found.append(f"    {path}  (Siemens.Engineering.Base.dll {state})")
    check.detail = found
    portals = [f for f in found if f.startswith("TIA Portal")]
    check.summary = f"{len(portals)} Openness installation(s) registered"
    if not portals:
        check.status = "warn"
    return check


def check_dotnet() -> Check:
    """Only needed to build the driver; reported so the next step is obvious."""
    check = Check("dotnet_sdk")
    exe = shutil.which("dotnet")
    if not exe:
        check.status = "warn"
        check.summary = ("no .NET SDK on PATH - only needed to build the driver, which is "
                         "a one-time step")
        return check
    try:
        sdks = subprocess.run([exe, "--list-sdks"], capture_output=True, text=True,
                              timeout=60).stdout.strip().splitlines()
    except Exception as exc:
        check.status = "warn"
        check.summary = f"dotnet found but did not answer: {exc}"
        return check
    check.summary = f"{len(sdks)} .NET SDK(s)"
    check.detail = sdks
    if not sdks:
        check.status = "warn"
        check.summary = "dotnet runtime found, but no SDK - install the SDK to build the driver"
    return check


def check_driver(root: str, workdir: str) -> Check:
    """Run the driver's own doctor, if the driver has been built into bin/."""
    check = Check("driver")
    exe = os.path.join(root, DRIVER_RELATIVE)
    if not os.path.exists(exe):
        check.status = "warn"
        check.summary = (f"{DRIVER_RELATIVE} not built yet. Build it once on this PC: "
                         "see README.txt, 'Building the driver'.")
        return check
    if os.name != "nt":
        check.status = "skip"
        check.summary = "driver present, but it only runs on Windows"
        return check
    try:
        proc = subprocess.run([exe, "doctor"], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        check.status = "fail"
        check.summary = "doctor did not finish in 5 minutes"
        return check
    log = proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else "")
    with open(os.path.join(workdir, "doctor.txt"), "w", encoding="utf-8") as fh:
        fh.write(log)
    check.summary = f"doctor exited {proc.returncode} (full output in doctor.txt)"
    check.detail = log.strip().splitlines()[-25:]
    if proc.returncode != 0:
        check.status = "fail"
    return check


# --------------------------------------------------------------------------
# Running and reporting
# --------------------------------------------------------------------------
def run(out_dir: Optional[str] = None, make_zip: bool = True) -> tuple:
    """Run every check. Returns (report, path to the zip or the folder)."""
    root = REPO_ROOT
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    workdir = tempfile.mkdtemp(prefix="tiagen-selftest-")

    report = Report(
        started=stamp,
        tiagen_version=_version(root),
        python=platform.python_version(),
        platform=platform.platform(),
        root="<install folder>",
    )

    steps: List[Callable[[], Check]] = [
        lambda: check_package(root),
        lambda: check_python(root),
        lambda: check_generator(root, workdir),
        check_windows,
        check_openness_group,
        check_tia_install,
        check_dotnet,
        lambda: check_driver(root, workdir),
    ]
    for step in steps:
        try:
            report.checks.append(step())
        except Exception as exc:
            # A check that crashes must still show up, as a failure with the reason.
            name = getattr(step, "__name__", "check")
            report.checks.append(Check(name, "fail", f"crashed: {type(exc).__name__}: {exc}"))

    for check in report.checks:
        check.summary = redact(check.summary, root)
        check.detail = [redact(d, root) for d in check.detail]
    doctor_log = os.path.join(workdir, "doctor.txt")
    if os.path.exists(doctor_log):
        with open(doctor_log, encoding="utf-8") as fh:
            scrubbed = redact(fh.read(), root)
        with open(doctor_log, "w", encoding="utf-8") as fh:
            fh.write(scrubbed)

    text = format_report(report)
    with open(os.path.join(workdir, "selftest.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    with open(os.path.join(workdir, "selftest.json"), "w", encoding="utf-8") as fh:
        json.dump(asdict(report), fh, indent=2)

    target_dir = out_dir or os.getcwd()
    os.makedirs(target_dir, exist_ok=True)
    if not make_zip:
        final = os.path.join(target_dir, f"tiagen-selftest-{stamp}")
        shutil.copytree(workdir, final)
        return report, final

    final = os.path.join(target_dir, f"tiagen-selftest-{stamp}.zip")
    with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(workdir):
            for name in files:
                full = os.path.join(base, name)
                zf.write(full, os.path.relpath(full, workdir))
    shutil.rmtree(workdir, ignore_errors=True)
    return report, final


def format_report(report: Report) -> str:
    marks = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    lines = [
        f"tiagen selftest  {report.started}",
        f"version {report.tiagen_version}  |  Python {report.python}  |  {report.platform}",
        "",
    ]
    for check in report.checks:
        lines.append(f"[{marks.get(check.status, check.status)}] {check.name:<15} {check.summary}")
        for d in check.detail:
            lines.append(f"       {d}")
    lines.append("")
    lines.append("RESULT: " + ("FAILED - see the FAIL lines above" if report.failed
                               else "no failures"))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _redactions(root: str) -> List[tuple]:
    """Strings that identify a person or machine, and what to show instead.

    The install path is the subtle one: on Windows it is usually under
    C:\\Users\\<name>, so any error message quoting a file path names the user.
    Longest first, so the install root is replaced before the home folder inside it.
    """
    pairs = [(os.path.abspath(root), "<install>"),
             (os.path.expanduser("~"), "<home>")]
    for var, label in (("USERNAME", "<user>"), ("USER", "<user>"),
                       ("COMPUTERNAME", "<machine>"), ("USERDOMAIN", "<domain>")):
        value = os.environ.get(var, "")
        if len(value) >= 3:          # never blank out short common substrings
            pairs.append((value, label))
    return sorted({p for p in pairs if p[0]}, key=lambda p: -len(p[0]))


def redact(text: str, root: str) -> str:
    for secret, label in _redactions(root):
        text = text.replace(secret, label)
        if os.sep == "\\":
            text = text.replace(secret.replace("\\", "/"), label)
    return text


def _local_group_exists(name: str) -> bool:
    try:
        return subprocess.run(["net", "localgroup", name], capture_output=True,
                              timeout=30).returncode == 0
    except Exception:
        return True          # unknown: assume it exists, so a real problem is not hidden


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(root: str) -> str:
    path = os.path.join(root, "VERSION.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.readline().strip()
    return "source checkout"


def _subkeys(winreg, key) -> List[str]:
    names = []
    index = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, index))
        except OSError:
            return names
        index += 1


def _assembly_paths(winreg, root, portal: str) -> List[str]:
    """Every folder the registry names for this portal version's public API.

    Mirrors the walk OpennessResolver does in the driver, so the selftest and the
    driver agree on what is installed.
    """
    paths: List[str] = []
    try:
        public_api = winreg.OpenKey(root, portal + r"\PublicAPI")
    except OSError:
        return paths
    stack = [public_api]
    while stack:
        key = stack.pop()
        index = 0
        while True:
            try:
                name, value, _type = winreg.EnumValue(key, index)
            except OSError:
                break
            if isinstance(value, str) and value.lower().endswith(".dll"):
                paths.append(os.path.dirname(value))
            index += 1
        for sub in _subkeys(winreg, key):
            try:
                stack.append(winreg.OpenKey(key, sub))
            except OSError:
                pass
    return sorted(set(paths))
