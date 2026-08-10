# Building a standards system your team can actually use

A standard written as a document is a standard nobody follows by the third machine. This
document is a plan for making the standard **executable**: a generator that can only emit
conforming code, a checker that finds it when someone hand-writes something else, and an
AI layer that authors intent rather than artefacts.

It is written against what this repo already does, and it is explicit about the three
places where the goal as stated collides with what the API can do.

---

## 0. What already exists

Worth knowing before planning, because most of the foundation is built:

| Requirement | Status today |
|---|---|
| Device library with uniform interface | **Done.** 9 blocks, one `UDT_DevIf` per device, one faceplate covers every type |
| DUTs / PLC data types | **Done.** Generated as SCL `TYPE`, imported as external sources |
| Tag tables with allocated addresses | **Done.** Direct API, 4 tables, sorted like a wiring list |
| Network topology from a device list | **Partly.** `network.io_devices` creates devices, modules, IPs, subnet and IO assignment. The *device list* is hand-written YAML |
| Automated checks | **Partly.** `tiagen validate` has 7 rule families over the spec. Nothing checks a project |
| HMI screens populated with tags | **Partly.** 93 HMI tags and 11 alarms generated and bound; screen *content* is a plan, not applied |
| Ladder | **No.** SCL only |
| Online monitoring | **No.** And deliberately - see §7 |

---

## 1. The standard is four artefacts, not a PDF

Split the standard by how each part is enforced. Anything that cannot be enforced by one
of these is a guideline, and should be labelled as one so nobody mistakes it for a rule.

| Artefact | What it fixes | Enforced by |
|---|---|---|
| **The library** (`library/scl/`) | device behaviour: fail-safe defaults, feedback supervision, fault latching, ack semantics | it is the only implementation. You cannot write a non-conforming motor because you do not write motors |
| **The spec schema** (`spec/machine.schema.json`) | what a machine may declare at all | schema validation, before anything is generated |
| **The rule set** (`tiagen validate` + a new `tiagen lint`) | naming, addressing, coverage, interlock sanity, house conventions | exit code, in CI |
| **The catalog** (new) | which hardware is approved, with its real MLFB | a lookup that fails on anything not in it |

The fourth is new and it is the one that pays for itself first. Today an unknown order
number becomes `REPLACE-WITH-CATALOG-MLFB` and a warning. A house catalog file turns that
into: approved hardware only, with the MLFB filled in correctly and automatically.

### Version-stamp everything

Put the standard's version in the generated `VERSION` attribute of every block and in
`report.md`. When a machine misbehaves in year three, the first question is which standard
it was built to, and the answer should be in the project rather than in someone's memory.

```scl
FUNCTION_BLOCK "FB_Motor"
{ S7_Optimized_Access := 'TRUE' }
VERSION : 2.1          // house standard 2.1 - not a per-block edit count
```

---

## 2. Ladder and SCL: pick the split deliberately

You asked for a mix, and V21 makes that possible where it previously was not. But the two
languages are not interchangeable for generation, and the reason is in
[docs/03](03-simaticml-and-source-formats.md):

- **SCL** generates as plain text with no schema to track. Stable across TIA versions.
- **LAD** has no textual form in plain SCL export. Before V21 you had to hand-write
  `FlgNet` SimaticML with version-suffixed namespaces - a generator that hard-codes
  `FlgNet/v4` breaks on the next upgrade.
- **V21 extended SIMATIC SD (`.s7dcl`/`.s7res`) to cover LAD, FBD, mixed-language blocks,
  data blocks and PLC data types.** That makes generated ladder tractable for the first
  time, and it is text, so it diffs.

### The recommended split

Not "some LAD, some SCL" by taste - split by **who reads the block at 2am**:

| Layer | Language | Why |
|---|---|---|
| Device FBs (`FB_Motor`, `FB_Valve`, …) | **SCL** | written once, reviewed once, changed almost never. Nobody debugs inside them; they debug the `UDT_DevIf` that comes out |
| Machine FB: interlocks, permissives, the auto sequence | **LAD** | this is what a maintenance electrician traces with a meter in one hand. Ladder shows you which rung is false |
| Scaling, maths, string and array work | **SCL** | ladder for arithmetic is a punishment |

That split gives the people who maintain the machine ladder exactly where they look, and
keeps the generator's stable path for everything else.

### Spike this before committing to it

`.s7dcl` LAD generation is genuinely new and this repo has not done it. The honest
sequence, and it is a day, not a sprint:

1. Hand-write one representative LAD block in TIA - three rungs, an interlock, a timer.
2. `TiaGen.Openness.exe export --project ... --formats documents`
3. Read the `.s7dcl`. Confirm the rung structure is what you would have to emit.
4. Change one rung by editing the text, re-import, compile.

If step 4 round-trips cleanly, generate LAD. If it does not, generate SCL for the machine
layer too and revisit at V22 - do not hand-write `FlgNet` XML as a fallback. That decision
should rest on your own export, not on this document.

---

## 3. Topology from a device list

`network.io_devices` already creates devices, plugs modules, sets IPs and PROFINET names,
and attaches everything to the controller's IO system. Three additions make it fed by a
list rather than typed by hand:

1. **Catalog queries** (README gap #1, and the highest value item in this whole document).
   V21 can enumerate the sub-modules compatible with a given parent. That retires
   `REPLACE-WITH-CATALOG-MLFB` and means a device list can say `ET200SP, 8DI` and get the
   right MLFB rather than a placeholder.
2. **A device-list importer.** CSV or Excel from the electrical designer →
   `network.io_devices`. Deterministic, testable, no AI needed.
3. **CAx / AutomationML import** (README gap #2). If your designer exports `.aml`, read it
   and emit the spec directly. This is the version of "generate from a device list" that
   requires no retyping at all, and Openness supports it.

Port-level topology - which port on which switch - is a further step and only worth doing
if you commission with topology-based device naming.

---

## 4. Conformance: two layers, and an honest autofix policy

You asked for automatic checking *and* automatic correction. Those need separating,
because the safe answer differs by layer.

### Layer 1 - the spec, before generation

Extend `tiagen validate`. Autofix here is safe and should be automatic (`--fix`): the spec
is the source of truth, the output is regenerated anyway, and a fix is a text edit to a
40-line file a human can read in full.

```bash
python -m tiagen validate spec/mymachine.yaml --fix
```

### Layer 2 - an existing project, after the fact

This is the new capability, and it is what covers hand-written code, legacy machines, and
the work of whoever ignored the generator. It exists because of source documents:

```bash
TiaGen.Openness.exe export --project X.ap21 --out vcs --formats documents
python -m tiagen lint vcs/            # rules over .s7dcl text
```

Rules worth having on day one, all of them mechanical:

- every block has a `VERSION` and a title
- device FBs match the library byte for byte (hash them - a "slightly modified" library
  block is the single most expensive thing that happens to a standard)
- no output coil written in more than one place
- every `%Q` written only inside a device FB
- naming conventions: prefix, area segment, no spaces, no German-English hybrids
- every declared device appears in exactly one call in the machine FB
- no absolute addressing where a symbol exists
- every alarm has a class, a priority and a trigger tag
- no empty rung, no permanently-true interlock, no `TRUE` fed into `SafetyOk`

**Autofix at layer 2 must never be silent.** Emit a patch, show the diff, require a human
to apply it. The rule: the generator owns generated code and may rewrite it freely; a human
owns hand-written code and a tool may only propose. Violating that is how a linter ends up
blamed for a machine crash.

### CI

Layers 1 and 2 differ in what they need:

| Check | Runs on | Where |
|---|---|---|
| `validate`, unit tests, `lint` of committed `vcs/` | Linux | GitHub Actions, free, every push |
| `apply` + **compile** | Windows + TIA V21 | self-hosted runner, one licensed machine |

Compile is the only automatic proof the code is valid, so the self-hosted Windows runner is
worth the setup. Put the licensed engineering PC behind a runner and every pull request
gets a real compile.

---

## 5. Where AI belongs, and where it must not

The failure mode to design against: an LLM as the *checker*. If a rule can be checked by a
regex, a schema, or a hash, it must be - a check that gives a different answer on Tuesday
is not a standard. Reproducibility is the whole point.

| Task | Who | Why |
|---|---|---|
| Prose description → machine spec | **AI** | genuine language work, and the spec is small, reviewable and validated immediately after |
| Spec → SCL / LAD / tags / HMI | **Generator** | must be identical every time. Same spec in, same bytes out |
| Rule checking | **Generator** | reproducible, diffable, exit codes |
| The auto sequence, inside the sanctioned region | **AI, human-reviewed** | real engineering judgement; also the one region where the repo permits hand-edits |
| Explaining a violation, proposing a fix for a judgement-call rule | **AI** | "why is this wrong and what would be right" is exactly what it is good at |
| Deciding whether a violation is acceptable | **Human** | accountability does not delegate |

The mechanism for sharing this with the team already exists in this repo:
`.claude/skills/tia-machine/SKILL.md` plus `CLAUDE.md`. A skill is a file in git - reviewed
in a pull request like any other part of the standard. That is what makes AI use consistent
across your team rather than dependent on who is better at prompting.

Add a second skill for review (`tia-review`) that runs `lint`, reads the report, and
explains the findings in the language of the standard.

---

## 6. HMI: the tag interface *is* the standard

Screens auto-populated with all tags is the right goal, and it is already most of the way
there because of one decision: every device exposes the same `UDT_DevIf`. One struct, one
faceplate, every device type. Screen generation becomes instantiation rather than drawing.

Be aware of the ceiling, from [docs/05](05-hmi-and-screens.md):

- **Tags, alarms and the connection** automate well. This is the valuable half and it is
  done - 93 tags bound symbolically to instance-DB members.
- **Screen content** is partly creatable. Unified is better than classic and moving fast;
  Unified screens exchange as JSON, classic panels as SimaticML XML.
- **Alarm and trend controls are genuinely excluded from the API.** Those two screens stay
  manual. Plan for it rather than discovering it.

So: generate the overview, per-area manual screens and diagnostics from the faceplate; hand
the alarm and trend screens to a human once, in the template project, and reuse them.

Decide Unified vs Comfort early - it changes the exchange format, the scripting language,
and whether you have scripting at all (Unified **Basic** panels have none).

---

## 7. Online monitoring: the boundary that matters most

This is the part of your request that needs correcting before anyone builds it.

**TIA Openness is an engineering API, not a runtime API.** In the manual, `OnlineProvider`
carries `GoOnline`, `GoOffline`, `Connection`, `PrimaryState`, `BackupState` - connection
*state*. There is no call that reads a tag's value. Nothing named `ReadValue` exists in the
API at all. `DownloadProvider` downloads. That is the extent of it.

So live monitoring, cycle-time measurement and fault tracing cannot be built on Openness,
and any plan that assumes otherwise fails late. The architecture that does work has three
parts, and only the first is Openness:

### 7a. Engineering time - generate the debug apparatus (Openness)

- **Watch tables and force tables are engineering objects** and Openness can create,
  find, delete and export/import them. So generate a watch table per device and per
  sequence step, pre-populated from the spec. Debug apparatus becomes a build output
  instead of something typed under pressure during commissioning.
- **The OPC UA server and its server interfaces are configurable** through Openness
  (security policy, namespace, SIMATIC and custom interfaces). So the generator can expose
  exactly the diagnostic tags - and nothing else - as a named interface.

### 7b. Instrument the code, because you cannot measure what it does not publish

The highest-value item for cycle time and robustness is not a monitoring tool. It is making
the standard require that the information exists:

- **Step timing.** Every sequence step publishes actual duration, plus min/max/last since
  reset. Cycle-time tuning is then reading a number, not timing rungs with a stopwatch.
- **A blocked-reason word per step.** "Step 40 waiting on: Clamp2 not closed." This single
  feature answers the 2am question - *why is the machine not moving* - and it is generated,
  free, and impossible to forget once it is in the standard.
- **Fault codes already exist** in `UDT_DevIf`. Extend the table so every code has a
  human-readable string generated into the HMI text list, so the panel says the cause
  rather than `Fault 7`.
- **First-fault latching per machine cycle.** The first thing that failed, not the twelfth
  consequence of it.

Do this before building any monitoring client. It is cheap, it is inside code you already
generate, and it improves troubleshooting even for someone with no tool but the HMI.

### 7c. Runtime - a read-only client, outside this toolchain

For live values, use the runtime protocol, not the engineering API:

- **OPC UA client** against the CPU's server - `asyncua` in Python. Subscribe to the
  diagnostic interface from 7a and log it.
- Then: cycle-time histograms, step-duration distributions across hundreds of cycles,
  fault-frequency ranking, and alerting when a step's duration drifts.

Two things to verify before relying on this: whether **the OPC UA server on your S7-1200 G2
needs a runtime licence** (it has historically been licensed separately on the 1200 family
- confirm for G2, it is a real cost), and that its throughput suits your sample rate.

### 7d. The gates do not move

[docs/07](07-safety-and-gates.md) exists for good reasons and this adds a lane rather than
opening a door:

| Action | Verdict |
|---|---|
| Read-only OPC UA subscription to a named diagnostic interface | **Allowed**, as a new explicitly read-only lane |
| Writing any value online, forcing, modifying | **Human gate.** Unchanged. A client that can write is a client that can move an axis |
| Download, go online, network scan | **Human gate.** Unchanged |
| Anything F-related | **Never** |

Build the monitoring client with no write path in it at all. Not a disabled write path - no
credentials that permit one. That is what makes it safe to leave running during
commissioning, which is exactly when you want it.

---

## 8. Getting it to the team

| What | How | Why this way |
|---|---|---|
| The generator | tagged releases of a versioned package, plus the built `TiaGen.Openness.exe` | engineers should not `pip install` from a branch. A machine records which release built it |
| The library | `.s7dcl` in git **and** a TIA Global Library | git is the source of truth; the Global Library is how someone hand-building a project gets the approved blocks |
| Project history | `export --formats documents` into `vcs/`, committed | text, diffable, reviewable in a pull request. This is what makes PLC code review real |
| The standard's rules | `spec/machine.schema.json` + the validator, in the same repo | a rule change is a pull request with a test |
| AI behaviour | `CLAUDE.md` + `.claude/skills/` | reviewed like code, consistent across everyone |
| Onboarding | one `spec/examples/` machine per pattern you build | a new engineer copies a working spec, not a blank file |

TIA's **Version Control Interface** understands source documents, and V21 added VCI support
for LAD/FBD/SCL and mixed blocks - so an engineer can commit from inside TIA rather than
learning a git client.

---

## 9. Order of work

Sequenced by value per unit of effort, not by architecture.

| # | Work | Effort | Why here |
|---|---|---|---|
| 1 | Catalog queries → retire `REPLACE-WITH-CATALOG-MLFB` | S | smallest change, biggest win. Unblocks the device list and stops the one error that costs an afternoon |
| 2 | Blocked-reason word + step timing in the machine FB | S | best troubleshooting improvement available, and it is generated |
| 3 | `tiagen lint` over exported `.s7dcl`, plus library-hash check | M | turns the standard into something enforceable on work the generator did not produce |
| 4 | Windows self-hosted CI runner doing `apply` + compile | M | compile is the only real proof; without this, review is opinion |
| 5 | Device-list importer (CSV, then AML) | M | removes the retyping that makes people skip the tool |
| 6 | Watch/force table generation + OPC UA interface config | M | the debug apparatus, as a build output |
| 7 | `.s7dcl` LAD spike, then the machine layer in LAD | M-L | do it after 3 and 4, so a generated ladder block is checked and compiled automatically from day one |
| 8 | Read-only OPC UA monitoring client | M | genuinely useful, but worth far more once 2 is in place |
| 9 | Applied screen content (Unified JSON) | L | highest uncertainty, and the tag half is already the valuable half |

Items 1-4 are the ones that change how the team works. 5-9 are leverage on top.

---

## 10. Decisions needed before implementation

Four forks, each of which changes the build rather than the plan:

1. **Is generated ladder a hard requirement, or does it just need to be readable?** If the
   real requirement is "maintenance can follow it", well-structured SCL with a good
   faceplate sometimes wins - and it is the path with no schema risk.
2. **Unified or Comfort?** Changes screen exchange format, scripting language, and whether
   scripting exists.
3. **Is there a licensed TIA machine that can host a CI runner?** Without one, compile stays
   manual and conformance checking stops at the text layer.
4. **Is the OPC UA server licensed on your G2 CPUs?** Gates §7c entirely.

---

## Sources

- [docs/03](03-simaticml-and-source-formats.md) - why SCL for generation, and what V21's
  source documents changed for LAD
- [docs/05](05-hmi-and-screens.md) - which screen objects the API can and cannot create
- [docs/07](07-safety-and-gates.md) - the gates, unchanged by anything here
- Openness system manual §5.11 (PLC data access), §5.17 (OPC UA server configuration), and
  the watch/force table sections - `OnlineProvider` members confirmed against it
