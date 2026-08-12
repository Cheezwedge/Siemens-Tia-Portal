# Generated step sequences

A `sequence:` section in the spec generates a step sequencer: one FB, one CASE over the
step number, one branch per step. The step table in the spec is the source of truth, and
the operator messages come from the same rows as the logic - so the panel and the code
cannot drift apart.

---

## The shape of it

```yaml
sequence:
  name: Cycle              # becomes FB_<Machine>Cycle
  cyclic: true             # last step returns to the first
  idle_step: 0             # held when auto is not running
  steps:
    - step: 1100
      name: RunInfeed
      message: Running infeed to load the part
      actions: [Infeed.start]
      wait_for: [Infeed.running]
      timeout: 5s
      on_timeout: fault
```

Leave gaps in the numbering. `1000, 1100, 1200` lets you insert `1150` later without
renumbering the table, the messages, or the operator's memory of what step 1200 means.

| Field | |
|---|---|
| `step` | the number. Positive, unique, and ideally ascending |
| `name` | identifier used in comments and the report |
| `message` | operator text. Becomes a text-list entry keyed by the step number |
| `actions` | device requests **held while the step is active** |
| `wait_for` | conditions to leave the step. A list means all; `{any: [...]}` means any |
| `timeout` | `500ms`, `10s`, `2m`, or a bare number of seconds |
| `on_timeout` | `fault` (latch, wait for reset), `hold` (wait, no fault), `continue` |
| `next` | step to advance to. Defaults to the next in the list |

### Actions

`Device.verb`, and `Device.speed = 40` where a value is needed. The verb has to be
something that type of device does:

| Type | Verbs |
|---|---|
| `motor_dol` | `start`, `run`, `stop` |
| `motor_reversing` | `forward`/`fwd`, `reverse`/`rev` |
| `vfd_analog` | `start`, `run`, `stop`, `speed = N`, `setpoint = N` |
| `valve_single`, `valve_double` | `open`, `close` |
| `digital_output` | `on`/`set`, `off`/`clear` |
| `analog_output` | `value = N`, `setpoint = N` |

A request is re-asserted every cycle its step is active and is not carried into the next
step, so a request can never outlive the step that made it. `close` on a single-acting
valve clears the open request - there is one coil, and "closed" is the de-energised state.

### Conditions

Three forms, and nothing else:

```yaml
wait_for: [PartPresent]              # a wired digital input, by its equipment name
wait_for: [Clamp.opened]             # a device status
wait_for: [not Lift.opened]          # negated
wait_for: [Level.value >= 75]        # a scaled analog value
```

Device statuses are aliases onto `UDT_DevIf`, so the same words work for every device
type: `running`/`on`/`open`/`opened`/`extended`, `stopped`/`off`/`closed`/`retracted`,
`fault`, `warning`, `interlocked`, `enabled`, `commanded`, `manual`.

Anything more complicated than this belongs in the `AUTO SEQUENCE` region, which still
exists and still runs after the sequencer. Comparing two tags is deliberately not
supported: it is the point where a step table stops being a table.

---

## What gets generated

```
scl/33_UDT_<M>Cond.scl     one Bool per distinct condition
scl/38_FB_<M><Name>.scl    the sequencer
scl/40_FB_<M>.scl          machine FB: fills Cond, calls the sequencer
hmi/textlists.json         step messages and blocked reasons
```

The sequencer holds **no device knowledge**. The machine FB evaluates each condition from
its device instances and passes a `Cond` struct in:

```scl
#Cond.PartPresent    := "PartPresent";
#Cond.Clamp_Closed   := #Clamp.Hmi.Sts_Stopped;
#Cond.Infeed_Running := #Infeed.Hmi.Sts_Running;

#Seq(AutoRun := #AutoRun, Reset := #vReset, Hold := #AnyFault,
     Cond := #Cond, Auto := #Auto);
```

That is the only place a status name in the spec becomes a DevIf member, which is what
makes the mapping reviewable in one screen instead of scattered through the sequence.

### What the sequencer publishes

| Output | For |
|---|---|
| `Step` | the active step number, and the index into the step-message text list |
| `BlockedById` | **which condition is holding the step**, as a text-list index. 0 = advancing |
| `StepTime` | time on the active step |
| `Timeout` | latched: a step exceeded its timeout |
| `Complete` | one cycle when the last step finishes; feeds the mode manager's `CycleComplete` |

`StepTimeMax` (a static array, one entry per step) keeps the longest time each step has
taken since the last reset. That is the cycle-time data, in the PLC, without any external
tool - read it in a watch table, or over OPC UA once that exists.

**`BlockedById` is the one worth understanding.** It answers *why is the machine not
moving* without anyone writing diagnostic code. For an `all` step it reports the first
unsatisfied condition; ids distinguish a condition from its negation, so "waiting for the
lift to raise" and "waiting for the lift to lower" are different messages even though
they read the same bit.

---

## Operator text without scripting

Unified **Basic** panels have no scripting engine, so a message cannot be assembled at
runtime. Both text lists are therefore keyed by an `Int` the PLC publishes, and a symbolic
I/O field resolves the text - configuration only:

```
Cycle_StepMessage   <- DB_<M>.Seq.Step          1100 -> "Running infeed to load the part"
Cycle_BlockedBy     <- DB_<M>.Seq.BlockedById      3 -> "Waiting for Infeed.running (Infeed conveyor)"
```

Two fields on the overview screen are the whole diagnostic story: what the machine is
doing, and what it is waiting for.

> The driver has **no stage that applies text lists yet**. They are generated into
> `hmi/textlists.json` and carried in `plan.json`, and imported by hand for now. Openness
> does support text lists (system manual §5.12.3 and §6.3.4), so this is a gap in the
> driver rather than in the API.

---

## From a spreadsheet

The step table usually exists before any code. Read it rather than retyping it:

```bash
python -m tiagen import-steps steps.csv -o sequence.yaml
```

Columns are matched loosely - `Step No`, `Step Name`, `Operator Message`, `Actions`,
`Condition`, `Max time`, `On timeout`, `Next`, and German equivalents. Only the step
number is required. Rows without a numeric step cell are skipped, so phase headings in
the middle of the table do no harm.

Two things to know:

- **Inside a cell, separate a list with the delimiter the file does not use.** A
  semicolon-separated export must use commas inside cells, and vice versa. Getting this
  wrong shifts every column right; the importer notices when a timeout cell is not a
  duration and says so, naming the row.
- A bare number in the timeout column is read as **seconds**.

Then `validate` checks every action and condition against the equipment list, so a typo in
the spreadsheet surfaces before TIA Portal ever opens.

---

## Validation

`tiagen validate` covers, as errors:

- a duplicate step number
- an action or condition naming equipment that does not exist
- a verb the device type does not have, or a status that is not a status
- `next` pointing at a step that is not in the sequence
- an empty message

and as warnings:

- a step that waits with no timeout - a failure then looks identical to a slow machine
- a step with neither a wait nor a timeout, which advances the cycle after entry
- step numbers out of ascending order, so the generated block does not read in step order
- an unreachable step
- `cyclic: false`, which needs a fresh start command per cycle

---

## What this is not

It is not S7-GRAPH. GRAPH gives you numbered steps, per-step names and times, and
per-transition ladder natively, and Openness imports and exports it - but **GRAPH needs an
S7-1500**. On an S7-1200 the sequencer has to be hand-rolled, which is what this
generates. If you move to a 1500, revisit the decision: GRAPH plus ProDiag answers "why is
this step blocked" as a platform feature rather than as generated code.

It is also still SCL, not ladder. The transition conditions are the part worth having in
LAD, and generating them means emitting `.s7dcl` - see
[docs/10](10-standards-system.md#02-ladder-and-scl-split-it-deliberately) for the spike
that decides whether that is worth doing.

---

## Verification

The generated sequencer has been **compiled in TIA Portal V21** (build
`V21.00.01.00_00.04.00.01`) on an S7-1200 G2, imported as an external source:
`0 errors, 0 warnings`, and the project compiles clean end to end including the
instance DB and OB1.

That settles the four things most likely to have been wrong, and they were all guesses
until it ran:

- `TON_TIME` is the right timer type on an S7-1200 (it matches the library blocks)
- `Array[0..N] of Time` is valid as a static in an optimized-access FB
- a `UDT` passed as `VAR_INPUT` compiles - it did not need to be `VAR_IN_OUT`
- the `FOR` loop, the `CASE` over step numbers and the `TIME` comparisons are all fine

One ordering lesson worth keeping, learned by getting it wrong: **the PLC tags must
exist before the machine FB is generated.** The machine FB is the only block that
references global tags, so importing it into a project with no tag table produces one
"Tag not defined" error per signal. Nothing is wrong with the code; the dependency
simply is not there. The `apply` pipeline gets this right - `create_tag_tables` runs
before `import_sources` - but doing it by hand invites the mistake.
