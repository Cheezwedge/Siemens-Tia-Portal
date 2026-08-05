# TiaGen.Openness - the driver

Applies a generated `plan.json` to TIA Portal V21 through the Openness API.

It contains **no engineering knowledge**. Every decision - which CPU, which addresses,
which order to import the sources in - is in the plan, where it can be reviewed and
diffed. This program executes it and reports.

---

## Requirements

- Windows 10/11 x64
- **TIA Portal V21** with the **Openness** setup component installed
- **.NET Framework 4.8**
- The running user in the local group **`Siemens TIA Openness`**
- Newtonsoft.Json 13 (restored from NuGet, or dropped next to the exe)

`doctor` checks all of this except the last.

---

## Build

```bat
msbuild TiaGen.Openness\TiaGen.Openness.csproj /p:Configuration=Release /p:Platform=x64
```

If TIA is not in the default location, point the build at your PublicAPI folder:

```bat
msbuild TiaGen.Openness\TiaGen.Openness.csproj /p:Configuration=Release ^
  /p:TiaOpennessDir="D:\Siemens\Automation\Portal V21\PublicAPI\V21"
```

Notes on the project file:

- **x64 is mandatory.** The Siemens assemblies have no 32-bit build; AnyCPU produces a
  binary that fails at runtime with a `BadImageFormatException`.
- The Siemens references are **compile-time only** (`Private=false`). They are never
  copied next to the exe and never redistributed - `OpennessResolver` finds the real
  ones through the registry at runtime, which is what lets one binary run on a machine
  with a different TIA build.
- `Siemens.Engineering.Hmi.dll` is referenced only if present, and the HMI code is
  behind `HAS_HMI_ASSEMBLY` so the project builds either way.

---

## Commands

### doctor

Run this first on any new machine. Needs no project.

```bat
TiaGen.Openness.exe doctor
```

Checks bitness, group membership (with the exact `net localgroup` command to fix it),
and which Openness assemblies are registered where.

### apply

```bat
TiaGen.Openness.exe apply --plan out\conveyor\plan.json --log apply.log
```

Runs the pipeline in the plan's order:

| Stage | What it does |
|---|---|
| `create_project` | creates the project, or opens `--project` |
| `create_plc` | the CPU, from its catalog identifier |
| `add_plc_modules` | signal modules and boards, into whichever item accepts them |
| `create_hmi` | the panel |
| `create_io_devices` | PROFINET IO devices and their modules |
| `build_subnet` | IP addresses, subnet mask, router, PROFINET device names |
| `assign_io_devices` | the controller's IO system, and each device attached to it |
| `create_tag_tables` | every tag through the API - no file format involved |
| `delete_blocks` | removes the default `Main [OB1]` so the generated OB can replace it |
| `import_sources` | every SCL source, in dependency order |
| `create_instance_dbs` | the machine FB's instance DB |
| `import_ob` | the generated OB1 |
| `create_hmi_connection` / `create_hmi_tags` / `create_hmi_screens` | the HMI side |
| `compile` | the only automatic proof the generated code is valid |
| `save` | |

Useful flags:

```bat
--dry-run                     print the plan, touch nothing
--mode nogui                  headless: faster, and cannot raise a modal dialog
--stop-after build_subnet     run part of the pipeline
--skip create_hmi_tags,compile
--project C:\TIA\...\X.ap21   work on an existing project instead of creating one
--no-compile   --no-save
--verbose                     print the per-call detail lines
--log apply.log               record every attempted API call
```

Everything is **idempotent by name**: re-running reuses what exists rather than
creating `PLC_1_1`. So the normal edit loop is safe:

```bat
TiaGen.Openness.exe apply --plan out\conveyor\plan.json --project C:\TIA\Projects\BottleLine\BottleLine.ap21 ^
  --skip create_project,create_plc,add_plc_modules,create_hmi,create_io_devices,build_subnet,assign_io_devices,create_tag_tables
```

### verify

```bat
TiaGen.Openness.exe verify --project C:\TIA\Projects\BottleLine\BottleLine.ap21 ^
                           --plan out\conveyor\plan.json
```

Opens, compiles, and reports. With `--plan` it also checks that every block the plan
expects is present. Exit code 0 only when the compile is clean.

### export

```bat
TiaGen.Openness.exe export --project ...\BottleLine.ap21 --out .\vcs --formats documents
```

| Format | Files | For |
|---|---|---|
| `documents` | `.s7dcl` + `.s7res` | **version control.** Text, diffable. V20+ only. |
| `xml` | SimaticML `.xml` | the schema template that matches *your* TIA build |
| `scl` | `.scl` | code blocks in the format this toolchain imports |

`--formats all` is the default. Commit the `documents` output; use one `xml` file as
ground truth before hand-generating any SimaticML.

---

## Exit codes

| | |
|---|---|
| 0 | success, compile clean |
| 1 | problems reported - read the summary and the log |
| 2 | bad usage, or the environment is not ready (run `doctor`) |

---

## When a call fails

Every Openness call is logged with **what was attempted**, because the characteristic
failure is an attribute that is named differently in your TIA version:

```
   !  setting PLC_1 address to 192.168.0.1 failed:
      EngineeringTargetInvocationException: attribute 'Address' not found
```

Where possible a failure is **non-fatal**: it costs you that one item, not the run. To
find the current name, create the object by hand in the IDE and ask it:

```csharp
foreach (var info in node.GetAttributeInfos())
    Console.WriteLine($"{info.Name} readonly={info.ReadOnly}");
```

Then adjust the relevant builder. The layout maps to the pipeline:

| File | Stages |
|---|---|
| `HardwareBuilder.cs` | project, devices, modules, subnet, IO system |
| `SoftwareBuilder.cs` | tag tables, block deletion, sources, instance DBs, the OB |
| `HmiBuilder.cs` | connection, HMI tags, screens |
| `Verifier.cs` | compile, and flattening the result tree |
| `Exporter.cs` | XML / SCL / source documents |
| `OpennessResolver.cs` | runtime assembly resolution |
| `Runner.cs` | the pipeline itself |

`HmiBuilder.cs` is the most likely to need a tweak - see
[../docs/05-hmi-and-screens.md](../docs/05-hmi-and-screens.md).

---

## What it will not do

No download, no online connection, no forcing, nothing F-related. Openness supports
all of them; their absence here is deliberate. See
[../docs/07-safety-and-gates.md](../docs/07-safety-and-gates.md).
