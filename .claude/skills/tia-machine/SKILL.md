---
name: tia-machine
description: Turn a plain-language machine description into a TIA Portal V21 project for an S7-1200 G2 - writes the YAML machine spec, validates it, generates SCL blocks, tag tables, HMI tags, alarms and a screen plan, and hands over the Openness apply command. Use when the user describes an industrial machine, asks for PLC code, tag tables, DUTs/UDTs, HMI screens or a TIA project, or mentions TIA Portal, S7-1200, S7-1500, Openness or SCL.
---

# Build a TIA Portal machine from a description

## What you are doing

The user describes a machine. You write a spec. The toolchain does the rest
deterministically. **You author the spec and, when asked, the auto sequence. You do
not author generated artefacts.**

Read [../../../docs/06-ai-workflow.md](../../../docs/06-ai-workflow.md) for the full
loop and [../../../CLAUDE.md](../../../CLAUDE.md) for the repository rules.

## Step 1 - understand the machine

Map what the user said onto equipment types. Run `python -m tiagen types` if you need
the signal list for any of them.

| They say | Type |
|---|---|
| conveyor, pump, fan, motor, starter | `motor_dol` |
| reversing, forward/reverse, indexing drive | `motor_reversing` |
| VFD, inverter, variable speed, drive with a speed reference | `vfd_analog` |
| solenoid valve, cylinder, spring return, single acting | `valve_single` |
| double acting, two coils, air-air cylinder | `valve_double` |
| level, pressure, temperature, flow, 4-20 mA, 0-10 V | `analog_input` |
| proportional valve, analog setpoint out | `analog_output` |
| sensor, prox, photo eye, limit switch, pushbutton | `digital_input` |
| lamp, stack light, horn, beacon | `digital_output` |
| E-stop | `estop` |
| guard door, light curtain, gate | `safety_gate` |

Ask about these only - they change the I/O budget or the fault behaviour, so a wrong
assumption is expensive:

1. **CPU order number and firmware.** Without it there is no project. If the user says
   "S7-1200 G2 1214C DC/DC/DC", `6ES7214-1AH50-0XB0` / `V1.1` is right; anything else
   they must read from the hardware catalog.
2. **Which feedbacks are wired.** Running feedback and fault contact default to
   present. If the user does not know, assume both and say that you did.
3. **IP addresses.** No subnet is built without `plc.ip`. Offer `192.168.0.1` for the
   PLC and `.10` for the panel.
4. **Analog alarm limits.** No limits means no monitoring and no alarms for that
   signal.

Do not ask about anything with a sensible default: address allocation, timeouts,
scaling, screen layout, tag naming.

## Step 2 - write the spec

Write `spec/<machine>.yaml`. Use `spec/examples/conveyor-line.yaml` as the reference
for structure and `spec/machine.schema.json` for the exact fields.

Things that are easy to get wrong:

- `project.name` becomes part of block names, so it must be a valid identifier -
  letters, digits, underscore, starting with a letter.
- Equipment `name` becomes the tag prefix. `InfeedConveyor` gives
  `InfeedConveyor_Run`, `InfeedConveyor_Running`, `InfeedConveyor_Fault`. Choose names
  a maintenance electrician would recognise.
- `interlock_from` takes equipment names and means "only runs while those are
  running".
- `control_role: start | stop | reset` on a `digital_input` wires a hardwired
  pushbutton into the mode manager. The stop circuit is **NC**: TRUE means not
  pressed.
- Count the I/O against what the CPU has. The 1214C G2 has 14 DI, 10 DQ, 2 AI and
  **no analog output** - any `vfd_analog` or `analog_output` needs a module in
  `plc.modules`.

**Never invent an order number.** If you do not know a module's MLFB, write
`REPLACE-WITH-CATALOG-MLFB`, add a comment saying which module it is, and tell the
user to copy the real one from the catalog. The validator flags it.

## Step 3 - validate and build

```bash
cd generator
python -m tiagen validate ../spec/<machine>.yaml
python -m tiagen build    ../spec/<machine>.yaml -o ../out/<machine>
```

Fix every error. **Report every warning to the user in your own words** - especially
the safety warning and any missing-feedback warning. Do not use `--force` to get past
an error without explaining what it was.

## Step 4 - report

Show the user:

- the I/O count and whether it fits their CPU (read it from
  `out/<machine>/report.md`, do not recount)
- the blocks that will be created
- what the HMI gets: tag count, alarm count, screen list
- any warning that needs a decision from them

Then hand over the command:

```bat
TiaGen.Openness.exe doctor
TiaGen.Openness.exe apply --plan out\<machine>\plan.json --log apply.log
```

## Step 5 - the auto sequence, if asked

Edit only the region between `>>> BEGIN AUTO SEQUENCE >>>` and
`<<< END AUTO SEQUENCE <<<` in `out/<machine>/scl/40_FB_<Machine>.scl`.

The idiom:

- gate everything on `#AutoRun`
- write only `#Auto.*` members - **never** a `%Q` tag, the device FBs own the outputs
- read status through `#<Device>.Hmi.Sts_Running` and process signals through their
  global tags
- for a step sequence, use an `Int` step counter in a static and a `CASE` statement;
  GRAPH is not available on the S7-1200

Then tell the user to re-import that one source rather than rebuilding everything:

```bat
TiaGen.Openness.exe apply --plan out\<machine>\plan.json --project <path to .ap21> ^
  --skip create_project,create_plc,add_plc_modules,create_hmi,create_io_devices,build_subnet,assign_io_devices,create_tag_tables
```

## Never

- Author F-blocks, an F-runtime group or F-I/O configuration.
- Download, go online, or force a value.
- Describe `estop` or `safety_gate` as providing a safety function - they are status
  inputs to the standard program.
- Hand-edit generated tag XML, `plan.json`, or any SCL outside the sequence region.
