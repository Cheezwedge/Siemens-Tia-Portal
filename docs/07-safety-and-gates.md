# Safety, and the gates this toolchain will not cross

Read this before the first download. It is short on purpose.

---

## 1. Nothing generated here is a safety function

The `estop` and `safety_gate` equipment types produce **status inputs for the standard
program**. They tell the PLC what the safety system is doing so the HMI can show it and
the sequence can stop cleanly. They do not stop the machine.

The stop is achieved by the safety system: a safety relay whose contacts break the
actuator supply, or an F-CPU running a validated safety program. Standard logic in a
standard CPU is not a means of achieving a required safety function, no matter how the
code is written.

The generated code is explicit about this. `FB_ModeManager` names the input `SafetyOk`
and its own comment block says what it is:

> `SafetyOk` is a STATUS input, not a safety function. It is fed from the safety
> relay's auxiliary contact or from an F-CPU's safety program.

`tiagen validate` says the same thing every time, and there is no flag to silence it:

```
WARNING SAFETY: EStop, GuardDoor are read by the standard program only. Standard
        logic is not a safety function - the stop must be achieved by a safety relay
        or an F-CPU.
```

If your spec declares no safety input at all, `SafetyOk` is wired to a constant
`TRUE` and you get a different warning saying so. That is a scaffold for bench
testing, not something to commission.

---

## 2. This toolchain never authors F-blocks

Setting `safety.fail_safe_plc: true` in the spec does not make it generate safety
logic. It makes it say:

```
WARNING SAFETY: safety.fail_safe_plc is true. This generator never authors F-blocks.
        The F-runtime group, F-I/O and safety program remain a manual, reviewed step.
```

Functional safety carries a review and validation obligation - the F-collective
signature exists precisely so that a human is accountable for that code. Generating
it, or generating something a reviewer might mistake for it, would undermine the one
control that makes the system trustworthy. So the F-runtime group, F-I/O
configuration and safety program stay manual.

What the toolchain *does* help with: the standard program is structured so the safety
system's status enters at exactly one place (`#vSafetyOk`, section 1 of the machine
FB) and gates every actuator through one signal (`Enable`). That makes the interface
between the safety system and the standard program a single reviewable line.

---

## 3. Gates: things a human does

The driver has verbs for creating and compiling. It has none for these, deliberately:

| Gate | Why it stays human |
|---|---|
| **First-connect trust dialog** | It is the confirmation that a program may drive your engineering system. |
| **Download to the CPU** | Stops the CPU. Physical machine, physical consequences. |
| **Go online / network scan** | Touches live devices on a production network. |
| **Forcing or modifying values online** | Bypasses the program while the machine can move. |
| **Master secret / UMAC configuration** | Project-wide access control. |
| **Anything F-related** | Requires review and validation. |

Openness *can* download - `DownloadProvider` exists. This toolchain does not call it,
and that is not an oversight. A generator that compiles clean has proved its syntax,
not its behaviour. Between the two sits a human with the machine in sight.

---

## 4. Guards: things the toolchain does automatically

The counterpart to the gates - the checks that run every time, without being asked:

- **Validate before generating.** Errors block the build; `--force` exists but says
  so loudly.
- **Compile before you are told it worked.** A non-zero error count is a build
  failure and a non-zero exit code.
- **Idempotency by name.** Re-running a plan reuses what exists rather than creating
  `PLC_1_1`.
- **Fail-safe defaults in the library blocks.** Losing `Enable` or an interlock drops
  the command; a valve de-energises; a VFD reference goes to `RawMin` when not
  commanded.
- **Feedback supervision by default.** A commanded motor with no running feedback
  faults after `FbTimeout`; feedback *without* a command also faults, which catches a
  welded contactor.
- **Faults latch and require an acknowledge**, and an acknowledge does not clear a
  fault whose cause is still present.
- **No output written outside its device FB.** The auto sequence writes requests, not
  `%Q` tags, so there is exactly one place that drives each output.
- **The spec is untrusted input.** A spec cannot cross a gate. There is no field that
  triggers a download.

---

## 5. Before you download, for real

1. `out/*/report.md` - does the I/O list match the wiring? Highest address within
   the modules you actually have?
2. Compile is green, with the warnings read rather than skimmed.
3. The safety system is wired, tested and its status contact lands on the input the
   spec says it does.
4. Actuator power is isolated, or the machine is in a state where unexpected motion
   is harmless.
5. Every device tested from the HMI faceplates in manual mode, one at a time, before
   auto is selected at all.
6. `AUTO SEQUENCE` region reviewed by someone who did not write it.

Steps 3 to 6 are the job. The rest of this repository exists to get you to step 1
faster, with fewer typos.
