# Testing: what is checked, where, and by whom

TIA Portal exists on one machine - the engineering PC - and the person developing
this toolchain cannot reach it. So testing is split by what each place can prove,
and the one thing only the engineering PC can do is made as cheap as possible:
**one command, one file back**.

---

## The layers

| Layer | Runs on | Proves | Who runs it |
|---|---|---|---|
| Unit tests (96) | Linux CI, every push | generator logic, validation, lint rules, packaging | automatic |
| Example builds + lint | Linux CI, every push | every example builds and its output passes the house rules | automatic |
| **Package smoke test** | **Windows CI**, every push | the zip extracts, `tiagen.cmd` starts, the bundled Python works with **nothing on PATH**, selftest/validate/build/lint all run | automatic |
| `tiagen selftest` | **the engineering PC** | the install is intact, the user has Openness rights, TIA is registered, the driver builds and its `doctor` passes | you, one command |
| Compile in TIA | the engineering PC | the generated SCL is valid to the real compiler | you, or the driver's `apply` |

The first three need nothing from anyone. The last two need TIA Portal, which is why
they are one command producing one file.

---

## The loop on the engineering PC

```
1. download TiaGen-<version>-win64.zip     (from the CI run's artifacts)
2. unblock, extract
3. tiagen selftest                          -> tiagen-selftest-<time>.zip
4. attach that zip to a message
```

The bundle holds every check's result, the doctor output when the driver is built, and
the generated example - enough to diagnose without a round trip asking "and what does it
say when you...".

**It names no person or machine.** User name, machine name, domain and home folder are
replaced with `<user>`, `<machine>`, `<domain>`, `<home>`, and the install path with
`<install>` - which matters because on Windows an install path is usually
`C:\Users\<name>\...` and appears in any error that quotes a file. There is a test for
this, because it was wrong in the first version.

### What selftest checks

| Check | Fails when | Warns when |
|---|---|---|
| `package` | a file is missing or altered against the SHA-256 manifest - antivirus quarantine or a partial extraction | running from a source checkout |
| `python` | PyYAML missing | |
| `generator` | the bundled example does not build, or its output does not lint clean | |
| `openness_group` | the group exists and the user is not in it | the group does not exist - TIA is not installed |
| `tia_install` | | no Openness installation registered |
| `dotnet_sdk` | | no SDK - only needed to build the driver |
| `driver` | `doctor` exits non-zero | the driver has not been built yet |

Warnings are normal on a build server and on a first run. Failures are the ones to fix.

---

## Two rules learned the hard way

**Run the artefact, never a retyping of it.** The first CI workflow was "verified
locally" by retyping its commands; the retyped version had a different path and passed,
while the workflow failed on every run for weeks. `tools/run_ci_locally.py` now runs the
steps straight out of the workflow file:

```bash
python tools/run_ci_locally.py .github/workflows/ci.yml
```

**Test the package, not the repo.** The package is a different set of files in a
different place. The packaging tests extract the zip and run from inside it, and the
Windows job strips `PATH` so the system Python cannot quietly stand in for the bundled
one.

---

## What is still not covered

- **The driver against real TIA.** Nothing off the engineering PC can compile it -
  the Siemens assemblies are not redistributable - so its first real test is
  `dotnet build` there, then `selftest`.
- **Openness API shape.** Method and attribute names are checked only when the driver
  runs against a live project. Every call logs what it attempted, so a rename shows up
  by name in the log.

A self-hosted runner on the engineering PC would close both gaps automatically, at the
cost of a CI agent on a corporate machine that pulls code from GitHub. That is IT's
decision, and not needed while the loop above is this cheap.
