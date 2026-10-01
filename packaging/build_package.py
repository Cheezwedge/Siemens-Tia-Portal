"""Assemble the installable Windows package: one folder, zipped.

    python packaging/build_package.py --python-embed python-3.11.9-embed-amd64.zip

The package is the repository's own layout, minus everything a user does not run,
plus a private Python. Mirroring the repo is deliberate: the generator finds its
library and rule catalogue by walking up from its own source, so an identical
layout means no path logic changes between a checkout and an install - and the
Windows CI job that runs the package proves that rather than assuming it.

    TiaGen/
      tiagen.cmd            the only thing a user runs
      python/               Windows embeddable Python + PyYAML, private to this folder
      generator/tiagen/     the generator
      library/scl/          the house device library
      spec/examples/        example specs; selftest builds one
      templates/            machine-template.xlsx, and an example filled in
      openness/             driver sources, to build once on the TIA PC
      bin/                  the built driver goes here
      rules.yaml            lint rule catalogue
      VERSION.txt, README.txt, MANIFEST.sha256

Without --python-embed the package is built without python/, which is how this is
tested on a machine that cannot reach python.org. That variant is for testing only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))

PYTHON_VERSION = "3.11"          # must match the embeddable zip and the wheel tag
PYYAML_REQUIREMENT = "pyyaml==6.0.2"

# What goes in, relative to the repo root. Anything not listed stays out - an
# allowlist, because a denylist eventually ships somebody's scratch file.
INCLUDE_TREES = [
    ("generator/tiagen", "generator/tiagen"),
    ("library/scl", "library/scl"),
    ("spec/examples", "spec/examples"),
    ("spec/machine.schema.json", "spec/machine.schema.json"),
    ("openness/TiaGen.Openness", "openness/TiaGen.Openness"),
    ("rules.yaml", "rules.yaml"),
]
SKIP_DIRS = {"__pycache__", "bin", "obj", ".vs"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".user", ".suo"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=os.path.join(REPO, "dist"))
    parser.add_argument("--python-embed",
                        help="path to python-3.11.x-embed-amd64.zip from python.org")
    parser.add_argument("--wheels",
                        help="folder holding the PyYAML win_amd64 wheel; downloaded if omitted")
    parser.add_argument("--driver", help="a built TiaGen.Openness.exe to include in bin/")
    parser.add_argument("--version", help="version string (default: from git)")
    args = parser.parse_args(argv)

    version = args.version or _git_version()
    stage_root = os.path.join(args.out, "stage")
    root = os.path.join(stage_root, "TiaGen")
    shutil.rmtree(stage_root, ignore_errors=True)
    os.makedirs(root)

    for source, dest in INCLUDE_TREES:
        _copy(os.path.join(REPO, source), os.path.join(root, dest))

    for name in ("tiagen.cmd", "README.txt"):
        shutil.copy2(os.path.join(HERE, name), os.path.join(root, name))

    os.makedirs(os.path.join(root, "bin"), exist_ok=True)
    if args.driver:
        shutil.copy2(args.driver, os.path.join(root, "bin", "TiaGen.Openness.exe"))
    else:
        with open(os.path.join(root, "bin", "PUT-DRIVER-HERE.txt"), "w", encoding="utf-8") as fh:
            fh.write("Build TiaGen.Openness.exe on the TIA PC and copy it here.\n"
                     "See README.txt, 'Building the driver'.\n")

    if args.python_embed:
        _install_python(root, args.python_embed, args.wheels)
    else:
        print("WARNING: no --python-embed given; package has no bundled Python (test build only)")

    # After the Python install, so the templates can be made by the interpreter that ships.
    _write_templates(root)

    with open(os.path.join(root, "VERSION.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{version}\n")
        fh.write(f"built {_dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds')}\n")
        fh.write(f"python bundled: {'yes' if args.python_embed else 'no'}\n")

    # Belt and braces: whatever wrote a cache, none ships.
    for base, dirs, _files in os.walk(root):
        for d in [d for d in dirs if d == "__pycache__"]:
            shutil.rmtree(os.path.join(base, d))

    # Last, so it covers every file above and nothing written after it.
    _write_manifest(root)

    suffix = "" if args.python_embed else "-nopython"
    archive = os.path.join(args.out, f"TiaGen-{version}-win64{suffix}.zip")
    _zip(stage_root, archive)
    print(f"package: {archive}")
    return 0


def _write_templates(root: str) -> None:
    """Workbooks made by the packaged generator itself, so they match the code shipped.

    A template copied from somewhere else would drift: a new column in the code, an
    old file in the zip, and the first user to fill it in finds out.
    """
    out = os.path.join(root, "templates")
    os.makedirs(out, exist_ok=True)
    python = _template_python(root)
    # No bytecode caches: they would be written into the staged package, land in the
    # manifest, and be recompiled on the user's PC - which selftest would then report,
    # correctly but misleadingly, as files altered since packaging. -B as well as the
    # variable, because the embeddable Python ignores environment variables.
    env = dict(os.environ, PYTHONPATH=os.path.join(root, "generator"),
               PYTHONDONTWRITEBYTECODE="1")
    jobs = [
        (["excel", os.path.join(out, "machine-template.xlsx"), "--force"]),
        (["excel", os.path.join(out, "example-transfer-station.xlsx"), "--force",
          "--from", os.path.join(root, "spec", "examples", "step-sequence.yaml")]),
    ]
    for args in jobs:
        subprocess.check_call([python, "-B", "-m", "tiagen"] + args, env=env,
                              stdout=subprocess.DEVNULL)


def _template_python(root: str) -> str:
    """The bundled interpreter when it can run here, otherwise this one.

    The bundled one is preferred because it is what the user runs: if it cannot make
    a workbook, the package is broken and the build should stop now, not on site.
    It only runs on Windows, so a package assembled elsewhere falls back to the
    interpreter running this script - which then needs PyYAML, like any checkout.
    """
    bundled = os.path.join(root, "python", "python.exe")
    if os.name == "nt" and os.path.isfile(bundled):
        return bundled
    try:
        import yaml  # noqa: F401
    except ImportError:
        raise SystemExit(f"{sys.executable} has no PyYAML, which the templates need: "
                         f"pip install -r generator/requirements.txt")
    return sys.executable


def _copy(src: str, dst: str) -> None:
    if os.path.isfile(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = os.path.relpath(base, src)
        target = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target, exist_ok=True)
        for name in files:
            if os.path.splitext(name)[1].lower() in SKIP_SUFFIXES:
                continue
            shutil.copy2(os.path.join(base, name), os.path.join(target, name))


def _install_python(root: str, embed_zip: str, wheels: str) -> None:
    """Unpack the embeddable Python and give it PyYAML and a path to the generator.

    The embeddable distribution ignores PYTHONPATH and the registry by design - it
    reads its search path only from its ._pth file. That isolation is the point on a
    corporate machine: nothing installed elsewhere can change what runs here.
    """
    pydir = os.path.join(root, "python")
    with zipfile.ZipFile(embed_zip) as zf:
        zf.extractall(pydir)

    pth = [f for f in os.listdir(pydir) if f.endswith("._pth")]
    if len(pth) != 1:
        raise SystemExit(f"expected one ._pth file in the embeddable zip, found {pth}")
    stdlib_zip = [f for f in os.listdir(pydir) if f.startswith("python") and f.endswith(".zip")]
    # Paths in ._pth are relative to the folder holding it.
    with open(os.path.join(pydir, pth[0]), "w", encoding="utf-8") as fh:
        fh.write("\n".join(stdlib_zip + [".", "Lib\\site-packages", "..\\generator"]) + "\n")

    site = os.path.join(pydir, "Lib", "site-packages")
    os.makedirs(site, exist_ok=True)
    wheel = _find_or_fetch_wheel(wheels)
    # A wheel is a zip laid out for site-packages; unpacking it is installing it.
    with zipfile.ZipFile(wheel) as zf:
        zf.extractall(site)


def _find_or_fetch_wheel(wheels: str) -> str:
    folder = wheels or os.path.join(REPO, "dist", "wheels")
    if not wheels:
        os.makedirs(folder, exist_ok=True)
        subprocess.check_call([
            sys.executable, "-m", "pip", "download", PYYAML_REQUIREMENT,
            "--only-binary=:all:", "--platform", "win_amd64",
            "--python-version", PYTHON_VERSION, "--implementation", "cp",
            "--abi", "cp" + PYTHON_VERSION.replace(".", ""), "-d", folder, "-q",
        ])
    found = [f for f in os.listdir(folder) if f.lower().startswith("pyyaml") and f.endswith(".whl")]
    if not found:
        raise SystemExit(f"no PyYAML wheel in {folder}")
    return os.path.join(folder, sorted(found)[-1])


def _write_manifest(root: str) -> None:
    lines = []
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            lines.append(f"{_sha256(path)}  {rel}")
    with open(os.path.join(root, "MANIFEST.sha256"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(sorted(lines, key=lambda l: l.split("  ", 1)[1])) + "\n")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip(stage_root: str, archive: str) -> None:
    if os.path.exists(archive):
        os.remove(archive)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, _dirs, files in os.walk(stage_root):
            for name in files:
                full = os.path.join(base, name)
                zf.write(full, os.path.relpath(full, stage_root))


def _git_version() -> str:
    try:
        sha = subprocess.check_output(["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
                                      text=True).strip()
    except Exception:
        sha = "unknown"
    return f"0.1.0+{sha}"


if __name__ == "__main__":
    sys.exit(main())
