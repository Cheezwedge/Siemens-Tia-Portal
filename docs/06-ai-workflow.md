# Driving this with Claude Code

The point of the machine spec is that it is the smallest description a person can
give and a model can turn into a complete project without inventing anything.

---

## 1. The loop

```
you describe the machine in plain language
        ↓
Claude writes / edits  spec/<machine>.yaml          ← the only artefact it authors
        ↓
python -m tiagen validate                            ← engineering rules, errors block
        ↓
python -m tiagen build -o out/<machine>              ← SCL, tags, HMI plan, plan.json
        ↓
you review out/<machine>/report.md                   ← the I/O list, in one page
        ↓
TiaGen.Openness.exe apply --plan out/<machine>/plan.json    ← on the Windows PC
        ↓
compile green → write the AUTO SEQUENCE → re-import that one source
        ↓
you download. (Claude does not.)
```

The important property: **Claude edits the spec, not the generated code.** The spec is
40 lines a person can read and argue with. The generated SCL is 800 lines nobody
should have to review line by line. Everything downstream is deterministic, so a
review of the spec is a review of the whole project.

---

## 2. What to say

You do not need to know the schema. Describe the machine the way you would to a
colleague:

> I have an S7-1200 G2, CPU 1214C DC/DC/DC, and a 12-inch Unified panel. The machine
> is a bottle filler: an infeed conveyor, an outfeed conveyor that must not run
> unless the infeed is running, a fill pump on a VFD with an analog speed reference,
> a spring-return fill valve with an open limit switch only, and a double-acting
> divert valve. Tank level on an analog input, 0-100%, warn below 15 and above 90,
> trip below 5 and above 97. E-stop and a guard door on a safety relay. Start, stop
> and reset pushbuttons. Stack light and a horn.

That is enough for a complete spec. What Claude should ask about if you leave it out:

| Missing | Why it matters | Reasonable default |
|---|---|---|
| CPU order number | must match the catalog exactly | ask - it decides the I/O budget |
| IP addresses | no subnet is built without them | ask, or `192.168.0.1` / `.10` |
| Which feedbacks are wired | changes both I/O count and fault detection | assume both, and say so |
| Fixed addresses | existing panel wiring | auto-allocate from byte 0 |
| Alarm limits on analog | no limits means no monitoring | ask |
| Analog output hardware | the CPU 1214C has none | flag it, do not invent an MLFB |

The last row is the pattern for anything unverifiable: **flag it, do not invent it.**
An MLFB that looks plausible and is wrong costs an afternoon. `REPLACE-WITH-CATALOG-MLFB`
costs twenty seconds and the validator points at it.

---

## 3. What Claude should and should not do

**Should:**

- write and edit `spec/*.yaml`
- run `tiagen validate` and `tiagen build`, and read the warnings out loud
- explain what the generated code does, and why an interlock or a fault code is there
- write the `AUTO SEQUENCE` region when asked, following the idiom in the comment
- add a new equipment type: an SCL FB in `library/scl/` plus one entry in
  `generator/tiagen/devices.py`, plus a test

**Should not:**

- hand-edit generated SCL, tag XML or `plan.json` - change the spec and regenerate
- invent an order number, a firmware version, or an address on hardware that is not
  in the spec
- author F-blocks or safety logic
- run a download, go online, or force a value

The last two are gates, not preferences. See
[07-safety-and-gates.md](07-safety-and-gates.md).

---

## 4. The skill

`.claude/skills/tia-machine/SKILL.md` carries the above as an instruction set, so in
a fresh session you can say

> /tia-machine a two-conveyor transfer with a lift and a gripper, S7-1200 G2 1214C

and get a validated spec plus a build without explaining the toolchain first.

`CLAUDE.md` in the repo root carries the same rules as always-on context, so they
apply even when the skill is not invoked.

---

## 5. Useful prompts

**Size the hardware.**

> From this spec, how many DI/DO/AI/AO do I need, and does it fit the CPU 1214C G2
> on-board I/O?

The answer is already in `report.md` under *Highest address used per area* - Claude
should read it rather than recount.

**Change a decision and see the consequences.**

> Move the fill valve to a double-acting valve with both limit switches, and give the
> outfeed conveyor a fault contact.

Two spec edits, one rebuild, and the diff of `report.md` shows exactly which
addresses moved. This is the workflow that makes the spec worth having.

**Write the sequence.**

> Write the auto sequence: run the infeed until a bottle is present, stop it, open the
> fill valve for 4 seconds while the pump runs at 35 Hz, close it, run the outfeed for
> 2 seconds.

Claude edits only the region between `>>> BEGIN AUTO SEQUENCE >>>` and
`<<< END AUTO SEQUENCE <<<`, using `#Auto.*` members and gating on `#AutoRun`. Then
re-import that one source:

```bat
TiaGen.Openness.exe apply --plan out\conveyor\plan.json ^
  --skip create_project,create_plc,add_plc_modules,create_hmi,create_io_devices,^
build_subnet,assign_io_devices,create_tag_tables --project C:\TIA\...\BottleLine.ap21
```

**Explain a fault.**

> The pump faults with code 2 as soon as it starts. Why?

Fault code 2 is a running-feedback timeout: commanded, but `FillPump_Running` never
came back within `FbTimeout`. Either the feedback is not wired to `%I1.0`, or the
drive needs longer than 5 s, or `has_running_feedback` should be false. The fault
codes are listed in `library/scl/10_UDT_DevIf.scl`.

**Add an equipment type.**

> Add a `motor_two_speed` type: two contactors, slow and fast, with a change-over
> delay.

That is an SCL FB, a `devices.py` entry with its signals and parameter builder, and a
test. Roughly 80 lines, and the generator picks it up everywhere at once - tags, HMI
tags, alarms, screens.

---

## 6. Running it where TIA is not

The generator is pure Python and runs anywhere - a Mac, a container, a web session.
Only the driver needs Windows and TIA Portal. So the useful split is:

- **anywhere:** describe, validate, build, review `report.md`, iterate on the spec
- **the engineering PC:** `TiaGen.Openness.exe apply`, compile, download

`out/` is self-contained: copy the folder to the Windows machine and the driver needs
nothing else. That is also why the plan carries relative paths.

---

## 7. Keeping the spec as the source of truth

The generated project drifts the moment someone edits a block in TIA. Two habits keep
that honest:

1. **Export before you regenerate.** `TiaGen.Openness.exe export --formats documents`
   writes `.s7dcl`/`.s7res` text; commit it. Now a regeneration produces a reviewable
   diff instead of a surprise.
2. **Put hand-written logic in its own blocks.** The `AUTO SEQUENCE` region is the
   sanctioned exception and it is small on purpose. Anything larger belongs in an FB
   of its own, which regeneration does not touch.
