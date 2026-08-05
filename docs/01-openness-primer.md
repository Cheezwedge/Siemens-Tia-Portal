# How TIA Portal Openness actually works

Everything here targets **TIA Portal V21** and an **S7-1200 G2** CPU, but the model is
the same from V15 onwards.

---

## 1. What Openness is

Openness is not a file format, a protocol, or a server. It is a **.NET API that
drives a running copy of TIA Portal**. Your program starts (or attaches to) a
`TiaPortal` process and then manipulates the project through an object model that
mirrors the project tree you see in the IDE.

Three consequences follow, and they explain most of what feels odd about it:

| Consequence | Why it matters |
|---|---|
| TIA Portal must be **installed** on the machine that runs your code | There is no redistributable engine. No TIA, no Openness. |
| Your code is **as slow as the IDE** | Creating a device takes seconds; compiling takes minutes. Batch work, do not poll. |
| Only **one thread** may touch the API | The object model is not thread-safe. Serialise every call. |

Openness is licensed as part of TIA Portal - there is no separate licence to buy -
but it is an **optional setup component**. If `HKLM\SOFTWARE\Siemens\Automation\Openness`
does not exist, someone unticked it during installation.

---

## 2. The four things you must get right before any code runs

These account for almost every "Openness doesn't work" report.

### 2.1 Windows group membership

The user running your process must be in the local group **`Siemens TIA Openness`**.
TIA Portal refuses the connection otherwise, and the error message is not obvious.

```bat
net localgroup "Siemens TIA Openness" "%USERDOMAIN%\%USERNAME%" /add
```

Then **sign out and back in** - group membership is baked into the logon token.

### 2.2 x64, .NET Framework 4.8

The Siemens assemblies are x64-only and Framework-only. An AnyCPU build works on
your machine and fails on a colleague's. Set `PlatformTarget=x64` explicitly.
V21 requires **.NET Framework 4.8**; earlier Openness versions accepted 4.6.2/4.7.

There is no .NET (Core) build of `Siemens.Engineering`. If you want to drive
Openness from Python, Node or a modern .NET service, you wrap a Framework process -
which is exactly what the driver in this repo is.

### 2.3 Runtime assembly resolution

You compile against `Siemens.Engineering.dll` from *your* installation, but the
machine that runs your program may have a different build. So you **never ship or
copy the DLL**. Instead you install an `AssemblyResolve` handler that looks the real
path up in the registry:

```
HKLM\SOFTWARE\Siemens\Automation\Openness\<version>\PublicAPI\<assembly version>
```

Each *value name* is an assembly name - sometimes the simple name
(`Siemens.Engineering`), sometimes the full strong name - and the *value data* is the
absolute path to the DLL. Because both conventions exist, walk the subkeys and match
on the part before the first comma. That is what
[`OpennessResolver.cs`](../openness/TiaGen.Openness/OpennessResolver.cs) does.

The handler must be installed **before the JIT has to resolve a Siemens type**. The
JIT resolves a method's types when the method is *entered*, not when a line runs. So
this is broken:

```csharp
static int Main(string[] args)
{
    OpennessResolver.Install();
    var portal = new TiaPortal(...);   // too late: Main already needed the type
}
```

and this is correct - the type only appears in a method that is entered afterwards:

```csharp
static int Main(string[] args)
{
    OpennessResolver.Install();
    return Runner.Apply(options);      // TiaPortal is referenced in here
}
```

Siemens also publish an official resolver on NuGet,
`Siemens.Collaboration.Net.TiaPortal.Openness.Resolver`. Version **2.x targets
.NET Framework 4.8 and only supports TIA V21 and above**; 1.x covers earlier
versions. Its entry point is:

```csharp
using Siemens.Collaboration.Net.TiaPortal.Openness;

Api.Global.Openness().Initialize();                 // discover and resolve
Api.Global.Openness().Initialize(tiaMajorVersion: 21);
bool inGroup = Api.Global.Openness().IsUserInGroup();
```

Either approach is fine. This repo hand-rolls it so the driver has no proprietary
package dependency and works on an air-gapped engineering PC.

### 2.4 Version binding

**V21 is a breaking change.** An application built against V20 or earlier does not
run against V21. There is no compatibility shim: you recompile against the V21
assemblies. Plan for one build of your tooling per TIA major version, and keep the
version out of your source (put it in a build property, as the `.csproj` here does).

---

## 3. The object model

The tree matches the IDE almost exactly. This is the spine you navigate:

```
TiaPortal                                  the process
└── Project                                the .ap21
    ├── Devices            (Device)         a station
    │   └── DeviceItems    (DeviceItem)     racks, CPUs, modules, interfaces  [recursive]
    │       ├── GetService<SoftwareContainer>()  → PlcSoftware / HmiTarget
    │       └── GetService<NetworkInterface>()   → Nodes, IoControllers, IoConnectors
    ├── Subnets            (Subnet)
    └── ...
```

and under `PlcSoftware`:

```
PlcSoftware
├── BlockGroup            → Blocks (FB, FC, OB, GlobalDB, InstanceDB) + nested Groups
├── TypeGroup             → Types (PLC data types / UDTs)          + nested Groups
├── TagTableGroup         → TagTables → Tags                       + nested Groups
└── ExternalSourceGroup   → ExternalSources (.scl, .db, .udt files)
```

Two patterns do all the work.

### 3.1 Compositions

Every collection is a *composition*: `Devices`, `DeviceItems`, `Blocks`, `Tags`.
Compositions are enumerable and expose `Create...` / `Find` methods. Objects are
created **through their parent composition**, never with `new`:

```csharp
Device device = project.Devices.CreateWithItem(typeIdentifier, itemName, deviceName);
PlcTagTable table = software.TagTableGroup.TagTables.Create("Inputs");
PlcTag tag = table.Tags.Create("Start_Pb", "Bool", "%I0.0");
```

### 3.2 Services

Capabilities are **not** on the type - you ask for them. `GetService<T>()` returns
`null` when the object does not have that capability, which is how you discover
what a `DeviceItem` really is:

```csharp
PlcSoftware plc  = item.GetService<SoftwareContainer>()?.Software as PlcSoftware;
NetworkInterface itf = item.GetService<NetworkInterface>();
ICompilable compiler = plcSoftware.GetService<ICompilable>();
```

Because a CPU's software container can sit at any nesting depth, **always recurse**
through `DeviceItems` instead of indexing `[0]`. Guessing indices is the second most
common source of code that works on one CPU family and breaks on the next.

### 3.3 Attributes: the escape hatch

Anything the typed API does not expose is reachable generically:

```csharp
object value = node.GetAttribute("Address");
node.SetAttribute("Address", "192.168.0.1");
foreach (var info in node.GetAttributeInfos())  // discover what exists
    Console.WriteLine($"{info.Name} {info.ReadOnly}");
```

`GetAttributeInfos()` is the single most useful debugging call in the whole API.
When an attribute name has moved between versions, it tells you the new one.

---

## 4. The canonical create-a-project sequence

This is the order the driver in this repo follows, and the order matters.

```csharp
// 1. connect
using (var portal = new TiaPortal(TiaPortalMode.WithUserInterface))
{
    // 2. project
    Project project = portal.Projects.Create(new DirectoryInfo(@"C:\TIA\Projects"), "BottleLine");

    // 3. CPU. The type identifier is the catalog string, matched exactly.
    Device plc = project.Devices.CreateWithItem(
        "OrderNumber:6ES7 214-1AH50-0XB0/V1.1", "PLC_1", "PLC_1");

    // 4. modules, into whichever DeviceItem accepts them
    DeviceItem cpu = plc.DeviceItems.First();
    if (cpu.CanPlugNew(moduleType, "AQ_1", 2))
        cpu.PlugNew(moduleType, "AQ_1", 2);

    // 5. addressing and the subnet
    NetworkInterface itf = FindInterface(plc);
    Node node = itf.Nodes.First();
    node.SetAttribute("Address", "192.168.0.1");
    Subnet subnet = node.CreateAndConnectToSubnet("PN_IE_1");

    // 6. PROFINET IO system, then attach each IO device
    IoSystem io = itf.IoControllers.First().CreateIoSystem("PROFINET IO-System");
    FindInterface(ioDevice).IoConnectors.First().ConnectToIoSystem(io);

    // 7. software
    PlcSoftware software = FindSoftware<PlcSoftware>(plc);

    // 8. tags - straight through the API, no file format involved
    PlcTagTable inputs = software.TagTableGroup.TagTables.Create("Inputs");
    inputs.Tags.Create("Start_Pb", "Bool", "%I0.0");

    // 9. code - as external SCL sources, generated in dependency order
    var source = software.ExternalSourceGroup.ExternalSources
                         .CreateFromFile("FB_Motor", @"C:\gen\FB_Motor.scl");
    source.GenerateBlocksFromSource();

    // 10. instance DB for the machine FB
    software.BlockGroup.Blocks.CreateInstanceDB("DB_BottleLine", true, 1, "FB_BottleLine");

    // 11. compile - the only automatic proof the generated code is valid
    CompilerResult result = software.GetService<ICompilable>().Compile();
    Console.WriteLine($"{result.State}: {result.ErrorCount} errors");

    // 12. save
    project.Save();
}
```

### The type identifier

`CreateWithItem` and `PlugNew` take a **catalog identifier**, most often:

```
OrderNumber:<MLFB>/<firmware>       e.g. OrderNumber:6ES7 214-1AH50-0XB0/V1.1
```

It is matched **character for character** against the hardware catalog. The two
things that trip people up:

- the catalog writes a **space** after the 4-character family prefix
  (`6ES7 214-...`), but everyone pastes it without. Try both.
- the **firmware suffix** must exist in the catalog. If only one firmware version is
  offered, some entries reject the qualifier entirely.

GSD-based devices use a different identifier (`GSD:...`), which is why the spec
schema has a `type_identifier` escape hatch. The driver here tries every plausible
spelling and, on failure, prints each one it attempted - that turns a dead end into
a copy-paste fix.

---

## 5. Getting code into the project: three routes

This is the decision that shapes any code generator, so it gets its own document -
see [03-simaticml-and-source-formats.md](03-simaticml-and-source-formats.md). The
short version:

| Route | API | Use it for |
|---|---|---|
| **External SCL source** | `ExternalSources.CreateFromFile` + `GenerateBlocksFromSource()` | **Generating logic.** Text in, compiler errors out. No schema to track. |
| **SimaticML XML import** | `Blocks.Import(FileInfo, ImportOptions)` | Round-tripping exports, LAD/FBD graphics, bulk edits of existing blocks. |
| **Source documents (V20+)** | `ExportAsDocuments` / `ImportFromDocuments` (`.s7dcl` + `.s7res`) | **Version control.** Text, diffable, git-friendly. |
| **Direct API** | `Tags.Create`, `Blocks.CreateInstanceDB`, `TagTables.Create` | Tags, tag tables, instance DBs, groups. No file at all. |

The pipeline in this repo uses **direct API for tags**, **external SCL for logic**,
and offers **document export** for committing the result to git. XML is generated
only as an offline fallback.

---

## 6. Compiling and reading the result

```csharp
CompilerResult result = software.GetService<ICompilable>().Compile();
```

`CompilerResult` is a **tree**: `result.Messages` holds `CompilerResultMessage`
objects, each with its own `Messages`. `ErrorCount` and `WarningCount` aggregate
downwards, so the root counts tell you pass/fail and the leaves tell you where.
Flatten it recursively - see [`Verifier.cs`](../openness/TiaGen.Openness/Verifier.cs).

Compile is the **only automatic evidence** that generated code is correct. Treat a
non-zero `ErrorCount` as a build failure, exactly like a failing unit test.

---

## 7. What Openness will not do for you

Being clear about the boundary saves a lot of wasted effort:

- **It does not write your sequence.** It creates blocks; the process logic is
  engineering.
- **It is not a substitute for commissioning.** Download, going online and
  forcing outputs are physical acts with a machine attached. See
  [07-safety-and-gates.md](07-safety-and-gates.md).
- **It never makes safety logic safe.** F-blocks require review and validation.
  Nothing generated is a safety function.
- **Screen content is only partly reachable.** Tag tables, connections and screens
  themselves are creatable; placing individual objects is not uniformly supported.
  See [05-hmi-and-screens.md](05-hmi-and-screens.md).

---

## 8. Practical notes that save hours

- **Run headless for batch work**: `TiaPortalMode.WithoutUserInterface` is faster
  and cannot pop a modal dialog that blocks your process forever. Use the UI mode
  while developing so you can watch what happens.
- **Firewall / trust prompt**: the first connection may raise a confirmation
  dialog. That is a deliberate human gate - do not try to automate past it.
- **Everything by name, idempotently.** Check `Find`/`FirstOrDefault` before every
  `Create`. Openness happily gives you `PLC_1_1`, and then nothing matches your
  plan any more.
- **Dispose the portal.** `TiaPortal` implements `IDisposable`; a leaked instance
  leaves `Siemens.Automation.Portal.exe` running and holding the project lock.
- **Attach instead of starting**, when a colleague already has the project open:
  `TiaPortal.GetProcesses()` lists running instances, and each has an `Attach()`.
- **Log every call you attempt.** The characteristic failure is "this attribute is
  named differently in your version". A log that records the attempted attribute
  name turns a mystery into a one-line fix.

---

## Sources

- [TIA Portal Openness V21 documentation](https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows) (Siemens)
- [Major changes for long-term stability in Openness V21](https://docs.tia.siemens.cloud/r/en-us/v21/readme-tia-portal-openness/major-changes-for-long-term-stability-in-tia-portal-openness-v21) (Siemens)
- [Siemens.Collaboration.Net.TiaPortal.Openness.Resolver](https://www.nuget.org/packages/Siemens.Collaboration.Net.TiaPortal.Openness.Resolver) (NuGet)
- [Openness feature matrix V17-V21](https://t-ia-connect.com/en/compatibility-tia-portal-openness)
