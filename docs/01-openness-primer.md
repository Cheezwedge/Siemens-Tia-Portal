# How TIA Portal Openness actually works

Everything here targets **TIA Portal V21** (Openness `21.00.00.00`) and an **S7-1200
G2** CPU. The object model is the same from V15 onwards, but **V21 redesigned the
assembly layout and the setup story**, so anything you read about V17-V20 is wrong on
those two points.

Primary source: *TIA Portal Openness: API for automation of engineering workflows*,
manual `21.00.00.00`, 03/2026.

---

## 1. What Openness is

Openness is not a file format, a protocol, or a server. It is a **.NET API that drives a
running copy of TIA Portal**. Your program starts (or attaches to) a `TiaPortal` process
and manipulates the project through an object model that mirrors the project tree you
see in the IDE.

Communication is **out of process**, over .NET Remoting: the channels are registered as
`IpcChannel` with `ensureSecurity` set to `false`, priority `1`, `typeFilterLevel`
`Full`. Three consequences follow, and they explain most of what feels odd:

| Consequence | Why it matters |
|---|---|
| TIA Portal must be **installed** on the machine that runs your code | There is no redistributable engine. No TIA, no Openness. |
| Your code is **as slow as the IDE** | Creating a device takes seconds; compiling takes minutes. Batch work; see §9. |
| Only **one thread** may touch the API | The object model is not thread-safe. Serialise every call. |

Because the API is out of process, **your client does not have to match TIA Portal's
bitness**. The manual lists 32-bit, 64-bit *and* AnyCPU clients as supported against
64-bit TIA Portal on a 64-bit OS. (An earlier version of this document claimed x64 was
mandatory. It is not.)

Avoid registering another `IpcChannel` with `ensureSecurity` other than `false` at
priority ≥ 1 - it will fight with Openness for the channel.

---

## 2. The five things you must get right before any code runs

### 2.1 Openness is no longer an optional install

**From V21, "TIA Portal Openness is no longer a setup option but an inherent feature of
TIA Portal"** and is installed automatically. On V20 and earlier it was a checkbox under
*Options* that people missed.

So if the registry key is absent on a V21 machine, TIA Portal itself is not installed -
not Openness. (Eigen's manual still tells users to tick the Openness box, which is
V19/V20 advice.)

### 2.2 Windows group membership

The user running your process must be in the local group **`Siemens TIA Openness`**,
directly or through another group. TIA Portal verifies this on every connection and
throws `EngineeringSecurityException` if it fails.

```bat
net localgroup "Siemens TIA Openness" "%USERDOMAIN%\%USERNAME%" /add
```

Then **sign out and back in** - membership is baked into the logon token. The group is
created automatically by TIA Portal setup. Note what the grant means: it "permits you to
execute *any* TIA Portal Openness application", so it is not a per-application
permission.

### 2.3 The Openness firewall (AllowList)

Separate from the group, and easy to miss. TIA Portal only accepts connections from
executables on an **AllowList** in the registry. The first connection from a new
executable raises a **confirmation dialog**; accepting it adds the entry.

From V21 the AllowList is **version independent**:

```
HKLM\SOFTWARE\Siemens\Automation\Openness\AllowList
```

That dialog is a deliberate human gate - it is the confirmation that a program may drive
your engineering system. Do not automate past it. For a Windows Service (§2.6) you must
create the AllowList entry at install time instead, because there is nobody to click it.

### 2.4 Modular assemblies, and where they actually are

**This is the V21 change most likely to break you.** There is no longer one
`Siemens.Engineering.dll`. The libraries are modular, the base assembly is
**`Siemens.Engineering.Base.dll`**, and they live in a **target-framework subfolder**:

```
C:\Program Files\Siemens\Automation\Portal V21\PublicAPI\V21\net48\
    Siemens.Engineering.Base.dll     Siemens.Engineering, .Compiler, .HW, .HW.Features
    Siemens.Engineering.Step7.dll    Siemens.Engineering.SW and everything below it
    Siemens.Engineering.Hmi.dll      Siemens.Engineering.Hmi.*
    ...                              one per installed TIA product/option
```

Which assemblies exist depends on which TIA products are installed. Note the path: it is
`PublicAPI\V21\net48`, **not** `PublicAPI\V21`.

Two related rules from the manual:

- **`Copy Local: True` is not supported.** Set `Private=false` (or "Copy Local: False")
  on every Siemens reference and clean your output directory. The assemblies must be
  loaded from the installation.
- **V21 is a breaking change.** The V17-V20 libraries are not shipped with V21, so
  applications *and Add-Ins* built against them must be adapted and recompiled. There is
  no shim.

### 2.5 Runtime assembly resolution

You compile against your own installation but must run against whatever is on the target
machine, so you install an `AssemblyResolve` handler and read the path from the registry.

The registry layout in V21:

```
HKLM\SOFTWARE\Siemens\Automation\Openness
├── AllowList                                      (version independent, V21+)
└── <xx.xx>                     PortalVersion      technical TIA version, e.g. 21.0
    └── PublicAPI
        └── <xx.xx.xx.xx>       EngineeringVersion  e.g. 21.0.0.0
            └── <netxxx>        AssemblyVersion     Microsoft Target Framework Moniker
                                PublicKeyToken
                                Siemens.Engineering.Base = <full path to the DLL>
```

The critical detail, quoting the manual: *"Regardless which TIA Portal products are
installed, the Windows registry only provides the installation path to the base assembly
`Siemens.Engineering.Base`."* So you resolve **Base** from the registry, take its
directory, and load every other `Siemens.Engineering.*` from alongside it.

Pre-V21 installs have no target-framework subkey and publish one value per assembly, so
handle both shapes if you support older versions.

**Register the handler in the entry-point class's static constructor.** The JIT resolves
a method's types when the method is *entered*, so "first line of `Main`" is too late if
`Main`'s body mentions an Openness type:

```csharp
internal static class Program
{
    private const string OpennessFolder =
        @"C:\Program Files\Siemens\Automation\Portal V21\PublicAPI\V21\net48";

    static Program()
    {
        // Earliest available hook.
        AppDomain.CurrentDomain.AssemblyResolve += OnAssemblyResolve;
    }

    private static Assembly OnAssemblyResolve(object sender, ResolveEventArgs args)
    {
        var requested = new AssemblyName(args.Name);
        var path = Path.Combine(OpennessFolder, requested.Name + ".dll");

        // Answer only for Openness, and only for files that exist - returning null
        // lets the CLR carry on with normal probing.
        if (!requested.Name.StartsWith("Siemens.Engineering.") || !File.Exists(path))
            return null;

        var loaded = Assembly.LoadFrom(path);

        // A version mismatch must be fatal: loading a different build fails later,
        // far from the cause.
        if (requested.FullName != loaded.GetName().FullName)
            throw new FileNotFoundException("TIA Portal Openness version does not match", path);

        return loaded;
    }
}
```

[`OpennessResolver.cs`](../openness/TiaGen.Openness/OpennessResolver.cs) does this,
driven by the registry rather than a hard-coded path.

Siemens also publish a resolver on NuGet,
`Siemens.Collaboration.Net.TiaPortal.Openness.Resolver`; version **2.x targets .NET
Framework 4.8 and V21+**, 1.x covers earlier versions:

```csharp
Api.Global.Openness().Initialize();
Api.Global.Openness().Initialize(tiaMajorVersion: 21);
bool inGroup = Api.Global.Openness().IsUserInGroup();
```

### 2.6 Toolchain and other requirements

- **Visual Studio 2017 or later**, .NET Framework SDK **4.8**, with the Windows Classic
  Desktop package.
- A TIA Portal product installed (STEP 7 Professional, WinCC Professional, …).
- **Windows Service**: supported, with caveats. Create a *Windows Service (.NET
  Framework)* project; add the service executable to the AllowList at install time; run
  it as a **dedicated user account** in the Openness group. You can only create a **new
  TIA Portal instance without UI** - you cannot attach to a running process or start one
  with a GUI. The **local system account is not supported** and always throws
  `EngineeringSecurityException`.

---

## 3. The object model

```
TiaPortal                                  the process
├── Projects              (Project)        the .ap21
│   ├── Devices           (Device)         a station
│   │   └── DeviceItems   (DeviceItem)     racks, CPUs, modules, interfaces  [recursive]
│   │       ├── GetService<SoftwareContainer>()  → PlcSoftware / HmiTarget
│   │       └── GetService<NetworkInterface>()   → Nodes, IoControllers, IoConnectors
│   ├── Subnets           (Subnet)
│   └── ProjectLibrary
└── GlobalLibraries
```

and under `PlcSoftware`:

```
PlcSoftware
├── BlockGroup            → Blocks (FB, FC, OB, GlobalDB, InstanceDB) + nested Groups
├── TypeGroup             → Types (PLC data types / UDTs)             + nested Groups
├── TagTableGroup         → TagTables → Tags                          + nested Groups
├── ExternalSourceGroup   → ExternalSources (.scl, .db, .udt files)
├── TechnologicalObjects  → axes, cams, PID, SIMATIC Ident
└── Units                 → software units and namespaces
```

Three patterns do all the work, plus two gotchas.

### 3.1 Compositions

Every collection is a *composition*. Objects are created **through their parent
composition**, never with `new`:

```csharp
Device device = project.Devices.CreateWithItem(typeIdentifier, itemName, deviceName);
PlcTagTable table = software.TagTableGroup.TagTables.Create("Inputs");
PlcTag tag = table.Tags.Create("Start_Pb", "Bool", "%I0.0");
```

Addressing within a composition works three ways: by **index** (counting starts at
**0**), by **`Find(name)`**, or symbolically. **`Find` is not recursive** - it only
looks in the composition you call it on, so walking nested groups is your job.

In the object model, every class that is not directly instantiated is declared
`abstract`.

### 3.2 Services

Capabilities are not on the type - you ask for them. `GetService<T>()` returns `null`
when the object lacks that capability, which is how you discover what a `DeviceItem`
really is:

```csharp
PlcSoftware plc = item.GetService<SoftwareContainer>()?.Software as PlcSoftware;
NetworkInterface itf = item.GetService<NetworkInterface>();
ICompilable compiler = plcSoftware.GetService<ICompilable>();
```

Because a CPU's software container can sit at any nesting depth, **always recurse**
instead of indexing `[0]`.

### 3.3 `.Items` and `.DeviceItems` are different hierarchies

A real trap. `HardwareObject` exposes `Items`; `Device` also exposes `UnpluggedItems`;
`DeviceItem` exposes `Container` (the reverse of `Items`). Iterating `.Items` and
iterating `.DeviceItems` give **different trees for the same device**, and the manual
documents both.

Rules worth remembering:

- Every *plugged* `DeviceItem` has a `Container`. An **unplugged** one has none and is
  reachable only via `Device.UnpluggedItems`.
- `PositionNumber` is unique **within a container**, not globally.
- Parent-child between device items is purely logical: a child cannot exist without its
  parent. A submodule modelled as part of a module cannot be removed on its own; a
  module or submodule that can be added and removed is a child of the **device**.

### 3.4 Attributes: the escape hatch

```csharp
object value = node.GetAttribute("Address");
node.SetAttribute("Address", "192.168.0.1");

foreach (var info in node.GetAttributeInfos())   // discover what exists
    Console.WriteLine($"{info.Name} readonly={info.ReadOnly}");
```

`GetAttributeInfos()` is the most useful debugging call in the API - when an attribute
has been renamed between versions, it tells you the new name. V21 extends this
**self-description support to navigators, actions and services**, not just attributes.

Two local resources beat any documentation for this:

- **Hardware Parameter List** -
  `...\Portal V21\PublicAPI\V21\HW Parameter description` - which hardware parameters
  Openness can reach and what they mean.
- **TIA Portal Openness Explorer** (Siemens entry 109760816) - a standalone tool that
  walks your open project as an object tree and shows each object's attributes and
  methods. Point it at your `Siemens.Engineering.Base.dll`. It is a live API browser for
  your exact install.

---

## 4. The canonical create-a-project sequence

```csharp
using (var portal = new TiaPortal(TiaPortalMode.WithUserInterface))
{
    Project project = portal.Projects.Create(new DirectoryInfo(@"C:\TIA\Projects"), "BottleLine");

    // CPU. The type identifier is the catalog string, matched exactly.
    Device plc = project.Devices.CreateWithItem(
        "OrderNumber:6ES7 214-1AH50-0XB0/V1.1", "PLC_1", "PLC_1");

    // Modules, into whichever DeviceItem accepts them.
    DeviceItem cpu = plc.DeviceItems.First();
    if (cpu.CanPlugNew(moduleType, "AQ_1", 2))
        cpu.PlugNew(moduleType, "AQ_1", 2);

    // Addressing and the subnet.
    NetworkInterface itf = FindInterface(plc);
    Node node = itf.Nodes.First();
    node.SetAttribute("Address", "192.168.0.1");
    Subnet subnet = node.CreateAndConnectToSubnet("PN_IE_1");

    // PROFINET IO system, then attach each IO device.
    IoSystem io = itf.IoControllers.First().CreateIoSystem("PROFINET IO-System");
    FindInterface(ioDevice).IoConnectors.First().ConnectToIoSystem(io);

    // Software.
    PlcSoftware software = FindSoftware<PlcSoftware>(plc);

    // Tags - straight through the API, no file format involved.
    PlcTagTable inputs = software.TagTableGroup.TagTables.Create("Inputs");
    inputs.Tags.Create("Start_Pb", "Bool", "%I0.0");

    // Code - as external SCL sources, generated in dependency order.
    var source = software.ExternalSourceGroup.ExternalSources
                         .CreateFromFile("FB_Motor", @"C:\gen\FB_Motor.scl");
    source.GenerateBlocksFromSource();

    // Instance DB for the machine FB.
    software.BlockGroup.Blocks.CreateInstanceDB("DB_BottleLine", true, 1, "FB_BottleLine");

    // Compile - the only automatic proof the generated code is valid.
    CompilerResult result = software.GetService<ICompilable>().Compile();

    project.Save();
}
```

### The type identifier

`CreateWithItem` and `PlugNew` take a catalog identifier, usually:

```
OrderNumber:<MLFB>/<firmware>       e.g. OrderNumber:6ES7 214-1AH50-0XB0/V1.1
```

Matched **character for character**. Two things trip people up: the catalog writes a
**space** after the 4-character family prefix (`6ES7 214-...`) but everyone pastes it
without, and the **firmware suffix** must exist in the catalog. GSD devices use a
different form. There is also a **normalized type identifier** concept for CAx work.

Better than guessing: **`Accessing the TIA Portal hardware catalog`** is a documented
function, and V21 can list the sub-modules compatible with a given parent module. Query
the catalog instead of transcribing part numbers.

---

## 5. Getting code into the project

Full treatment in [03-simaticml-and-source-formats.md](03-simaticml-and-source-formats.md).
The short version:

| Route | API | Use it for |
|---|---|---|
| **External SCL source** | `ExternalSources.CreateFromFile` + `GenerateBlocksFromSource()` | **Generating logic.** Text in, compiler errors out. |
| **SIMATIC SD documents** | `ExportAsDocuments` / `ImportFromDocuments` (`.s7dcl` + `.s7res`) | **Version control**, and in V21 also **LAD, Safety-LAD, FBD, Safety-FBD, SCL, mixed-language blocks, DBs and PLC data types**. |
| **SimaticML XML** | `Blocks.Import(FileInfo, ImportOptions)` | Round-tripping exports, bulk edits. |
| **Direct API** | `Tags.Create`, `Blocks.CreateInstanceDB`, `TagTables.Create` | Tags, tag tables, instance DBs, groups. No file at all. |

**SimaticML version compatibility in V21**, straight from the manual: the V21 libraries
**write** engineering version V21 files, and **read** V18, V19, V20 and V21. A V17 or
older export will not import - there is a "Version Specific Simatic ML Import" topic for
the details.

---

## 6. Compiling and reading the result

```csharp
CompilerResult result = software.GetService<ICompilable>().Compile();
```

`CompilerResult` is a **tree**: `result.Messages` holds `CompilerResultMessage` objects,
each with its own `Messages`. `ErrorCount` and `WarningCount` aggregate downwards.
Flatten it recursively - see [`Verifier.cs`](../openness/TiaGen.Openness/Verifier.cs).

Compile is the **only automatic evidence** that generated code is correct. Treat a
non-zero `ErrorCount` as a build failure.

---

## 7. What the API covers beyond the basics

Worth knowing exists, because it is easy to assume Openness is only blocks and tags. All
of these are documented function areas in V21:

| Area | Examples |
|---|---|
| **Libraries** | global and project libraries, types and type versions, master copies, instances, out-of-date instances, library update/cleanup, release/edit/discard a type version, harmonize project from library |
| **CAx / AutomationML** | export and import hardware as `.aml`, topology, subnets, IO systems, PLC tags, GSD custom attributes, fail-safe PLC and IO, pruned AML |
| **Multiuser** | project server connections, local sessions, commit, lock state, markings |
| **Version Control Interface** | workspaces, mapped objects, sync status, per-object export |
| **Teamcenter Gateway** | check in/out, revisions, custom attributes |
| **Safety** | F-signatures, `SafetyBaseIdProvider`, F-activation enable/disable, Safety Administration; **Safety Validation Assistant** - activation tests, safety functions, conditions, trace config, reports |
| **Technology objects** | motion axes, superimposing axes, SIMATIC Ident, DB member/interpreter mapping |
| **Online** | download, upload, station upload, go online by IP, on a protected PLC, certificate checks |
| **User management** | users, roles, function rights, UMAC/UMC |
| **OPC UA** | server interfaces, security policies, reference namespaces, access control |
| **Drives** | SINAMICS Integrated, drive parameters upload, telegrams, hardware modules |
| **Connections** | create and manage FDL, HMI, ISO, ISO-on-TCP, PtP, S7, TCP, UDP (new in V21) |
| **Web server** | default web pages and web applications |

Namespaces to `using` when you go looking:
`Siemens.Engineering{,.Cax,.Compare,.Compiler,.Download,.Upload,.Library{,.MasterCopies,.Types}}`,
`Siemens.Engineering.HW{,.Extensions,.Features,.Utilities}`,
`Siemens.Engineering.SW{,.Blocks,.ExternalSources,.Tags,.Types,.TechnologicalObjects{,.Motion}}`,
`Siemens.Engineering.Hmi{,.Communication,.Cycle,.Globalization,.RuntimeScripting,.Screen,.Tag,.TextGraphicList}`.

---

## 8. Performance and transactions

The manual has a dedicated performance section, and these are the levers:

- **`ExclusiveAccess`** - take exclusive access for a batch of work instead of letting
  the IDE refresh between calls.
- **Transaction handling** - group related modifications so the UI and undo stack are
  updated once.
- **Batch, do not poll.** Every call crosses a process boundary.
- **Bulk operations exist** - "Bulk changing hardware parameters", and in V21 bulk
  support for safety module parameters on ET 200SP/AL/pro/eco Safety.
- **Run headless** for unattended work: `TiaPortalMode.WithoutUserInterface` is faster
  and cannot pop a modal dialog that blocks your process forever.

Event handlers exist for **program-controlled acknowledgement of dialogs with system
events** - the sanctioned way to handle prompts that appear mid-operation. Note the
difference from the AllowList dialog, which is a gate you must not automate.

---

## 9. Practical notes that save hours

- **Everything by name, idempotently.** Check `Find`/`FirstOrDefault` before every
  `Create`. Openness will happily give you `PLC_1_1`, and then nothing matches your plan.
- **Dispose the portal.** `TiaPortal` is `IDisposable`; a leaked instance leaves the
  process running and holding the project lock.
- **Attach instead of starting** when a project is already open: `TiaPortal.GetProcesses()`
  lists instances and each has `Attach()`. V21 improved this - "possibility to get and
  attach to other TIA Portal processes".
- **Use absolute paths.** Relative paths are only allowed inside the XML import/export
  files.
- **Log every call you attempt.** The characteristic failure is "this attribute is named
  differently in your version"; a log recording the attempted name turns a mystery into
  a one-line fix.
- **Exceptions to expect**: `EngineeringSecurityException` (group or AllowList),
  `EngineeringTargetInvocationException` (the object rejected the call),
  `EngineeringNotSupportedException`, `EngineeringObjectDisposedException`.

---

## 10. What Openness will not do for you

- **It does not write your sequence.** It creates blocks; process logic is engineering.
- **It is not a substitute for commissioning.** Download, going online and forcing
  outputs are physical acts with a machine attached. See
  [07-safety-and-gates.md](07-safety-and-gates.md).
- **It never makes safety logic safe.** F-blocks require review and validation. The API
  exposes F-signatures and the Safety Validation Assistant precisely so a human can
  discharge that obligation - not so a generator can skip it.

---

## Sources

- **TIA Portal Openness: API for automation of engineering workflows**, manual
  `21.00.00.00`, 03/2026 (Siemens) - the authority for §1-§8
- [TIA Portal Openness V21 documentation](https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows) (Siemens)
- [Major changes for long-term stability in Openness V21](https://docs.tia.siemens.cloud/r/en-us/v21/readme-tia-portal-openness/major-changes-for-long-term-stability-in-tia-portal-openness-v21) (Siemens)
- [TIA Portal Openness Explorer](https://support.industry.siemens.com/cs/attachments/109760816/109760816_TiaOpennessExplorer_DOC_V2_0_en.pdf) (Siemens, entry 109760816)
- [Siemens.Collaboration.Net.TiaPortal.Openness.Resolver](https://www.nuget.org/packages/Siemens.Collaboration.Net.TiaPortal.Openness.Resolver) (NuGet)
- [How to get started with TIA Portal Openness](https://github.com/orgs/tia-portal-applications/discussions/1) (TIA Portal Applications)
