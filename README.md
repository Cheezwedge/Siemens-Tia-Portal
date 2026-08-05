# Siemens TIA Portal V21 machine generator

Describe a machine in ~40 lines of YAML. Get a complete TIA Portal V21 project for an
**S7-1200 G2**: SCL blocks, PLC data types, tag tables with allocated addresses, HMI
tags, an alarm list, a screen plan, and a driver that builds it all through the
**Openness API** and compiles it.

Two halves, deliberately:

- **`generator/`** - pure Python, runs anywhere. Spec in, artefacts out. No TIA needed.
- **`openness/`** - a .NET Framework 4.8 driver that runs on the engineering PC and
  applies the generated plan to TIA Portal.

---

## Quick start

### 1. Describe the machine

```yaml
# spec/mymachine.yaml
project:
  name: TestRig
  directory: C:\TIA\Projects

plc:
  name: PLC_1
  order_number: 6ES7214-1AH50-0XB0     # S7-1200 G2, CPU 1214C DC/DC/DC
  firmware: V1.1
  ip: 192.168.0.1

equipment:
  - name: EStop
    type: estop
    description: E-stop status contact
  - name: StartPb
    type: digital_input
    options: { control_role: start }
  - name: Conveyor
    type: motor_dol
    description: Infeed conveyor
```

### 2. Generate

```bash
pip install -r generator/requirements.txt      # PyYAML only
cd generator
python -m tiagen validate ../spec/mymachine.yaml
python -m tiagen build    ../spec/mymachine.yaml -o ../out/mymachine
```

You get:

```
out/mymachine/
├── plan.json          what the driver executes, in order
├── report.md          the I/O list and block list, one page - read this
├── scl/               every block, as importable SCL
├── tags/              tag tables as CSV and SimaticML XML
└── hmi/               HMI tags, alarms, and a screen plan with positions
```

### 3. Apply, on the Windows PC with TIA Portal V21

```bat
TiaGen.Openness.exe doctor
TiaGen.Openness.exe apply --plan out\mymachine\plan.json --log apply.log
```

That creates the project, the CPU, the modules, the subnet, the tag tables, imports
the SCL in dependency order, creates the instance DB, imports OB1, and compiles.

### 4. Write the sequence

The generated machine FB has one region for you:

```scl
// >>> BEGIN AUTO SEQUENCE >>>
IF #AutoRun THEN
    #Auto.Conveyor_Start := TRUE;
END_IF;
// <<< END AUTO SEQUENCE <<<
```

Then re-import that single source. Everything else is already correct.

---

## What it generates

From `spec/examples/conveyor-line.yaml` (15 pieces of equipment):

- **14 DI, 9 DO, 1 AI, 1 AO** allocated, named and commented
- **9 library blocks** - `FB_Motor`, `FB_MotorRev`, `FB_Valve`, `FB_Vfd`,
  `FB_AnalogIn`, `FB_AnalogOut`, `FB_DigitalOut`, `FB_ModeManager`, `UDT_DevIf`
- **4 machine blocks** - the machine FB with one instance and one call per device,
  plus `UDT_*Auto` / `UDT_*Cmd` / `UDT_*Alarms`
- **4 tag tables**, addresses sorted so they read like a wiring list
- **93 HMI tags** bound symbolically to instance-DB members
- **11 alarms** with classes, priorities and trigger tags
- **8 screens** - overview, one manual screen per area, alarms, trends, diagnostics -
  laid out for the panel resolution, with every object positioned

### Equipment types

`motor_dol`, `motor_reversing`, `vfd_analog`, `valve_single`, `valve_double`,
`analog_input`, `analog_output`, `digital_input`, `digital_output`, `estop`,
`safety_gate`.

`python -m tiagen types` prints each one with its signals. Adding a type is an SCL FB
plus one entry in `generator/tiagen/devices.py`.

### What the library blocks do for you

Not stubs. Every device FB has fail-safe defaults, feedback supervision with a
timeout, latching faults with codes, an acknowledge that refuses to clear a live
fault, manual/auto arbitration, and a uniform HMI interface so **one faceplate covers
every device type**.

- **Motors** fault on overload, on missing running feedback, and on feedback present
  without a command (welded contactor).
- **Reversing motors** hard-interlock the contactors and enforce a change-over dead
  time.
- **Valves** supervise travel in both directions and detect contradictory feedback.
- **VFDs** scale engineering units to counts, clamp the setpoint, and zero the
  reference when not commanded.
- **Analog inputs** range-check for wire break before scaling, filter, and monitor
  four limits.

---

## Documentation

| | |
|---|---|
| [01 Openness primer](docs/01-openness-primer.md) | How the API actually works: assembly resolution, the object model, the create sequence, the traps |
| [02 Cookbook](docs/02-api-cookbook.md) | Copy-paste C# for every call you need |
| [03 SCL, SimaticML, source documents](docs/03-simaticml-and-source-formats.md) | The four routes for getting code in, and which to use |
| [04 S7-1200 G2 and V21](docs/04-s7-1200-g2-and-v21.md) | Order numbers, on-board I/O, V21 breaking changes |
| [05 HMI and screens](docs/05-hmi-and-screens.md) | What is automatable, what is not, and how the work splits |
| [06 AI workflow](docs/06-ai-workflow.md) | Driving this with Claude Code from a plain-language description |
| [07 Safety and gates](docs/07-safety-and-gates.md) | **Read before the first download** |
| [openness/README.md](openness/README.md) | Building and running the driver |

---

## Design decisions worth knowing

**Logic is generated as SCL, not XML.** SimaticML carries schema-version namespaces
that change between TIA releases; a generator that hard-codes them breaks on upgrade.
SCL is stable text, and the TIA compiler validates it. Tag tables go in through the
API, so no schema is involved there either. XML is generated only as an offline
fallback. [Full reasoning](docs/03-simaticml-and-source-formats.md).

**The plan is data, the driver is dumb.** All engineering decisions - which CPU, which
addresses, which order to import - live in `plan.json`, where they can be reviewed and
diffed. The C# driver contains no engineering knowledge.

**Everything is idempotent by name.** Re-running a plan reuses what exists instead of
creating `PLC_1_1`. Change the spec, rebuild, re-apply.

**Compile is the test suite.** The driver exits non-zero on a compile error. Nothing
claims to work until it compiles.

**No downloads.** Openness can download; this does not, deliberately.
[Why](docs/07-safety-and-gates.md).

---

## Verification status

Honest about what has been tested, because that matters more than looking finished:

| | |
|---|---|
| **Generator** | Tested. 54 unit tests over addressing, naming, validation, SCL emission, tag XML well-formedness, and both example specs end to end. `python -m unittest discover -s generator/tests` |
| **SCL library** | Written against S7-1200 SCL and reviewed, **not yet compiled in TIA Portal**. The first `apply` run is the real test; the compiler names any problem and the file it is in. |
| **Openness driver** | Written against the documented V21 API; **not compiled or run against a real installation** - there is no TIA Portal in the environment this was built in. Every call is individually logged and non-fatal where it can be, so a version difference costs you one item and a clear message rather than the run. Expect to adjust one or two attribute names on first use; `GetAttributeInfos()` and the log tell you which. |
| **Order numbers** | The CPU MLFB `6ES7214-1AH50-0XB0` matches Siemens' G2 documentation and distributor listings. **G2 module MLFBs are not shipped** - the example carries a deliberate placeholder the validator flags. Copy yours from the hardware catalog. |
| **HMI stages** | The least certain area of the API. Tags, connection and screens are attempted with per-item logging; screen *content* is emitted as a plan rather than pretended to be automatic. [Detail](docs/05-hmi-and-screens.md). |

The one step with a known manual fallback is importing OB1, and the driver prints the
one line to paste if it fails.

---

## Repository layout

```
spec/
  machine.schema.json          the spec schema
  examples/minimal.yaml        smallest working spec
  examples/conveyor-line.yaml  a full machine, with a flagged placeholder MLFB
library/scl/                   the reusable device FBs - reviewable SCL, not templates
generator/tiagen/              spec model, validation, emitters, CLI
generator/tests/               54 tests
openness/TiaGen.Openness/      the .NET driver: doctor, apply, verify, export
docs/                          how Openness works, and how this uses it
.claude/skills/tia-machine/    the Claude Code skill
CLAUDE.md                      always-on rules for AI sessions in this repo
```

---

## Licence and trademarks

*TIA Portal*, *SIMATIC*, *STEP 7*, *WinCC* and *Siemens* are trademarks of Siemens AG.
This is an independent toolchain **for** TIA Portal, not affiliated with or endorsed
by Siemens. No Siemens assemblies are redistributed here; the driver loads them from
your own installation at runtime.
