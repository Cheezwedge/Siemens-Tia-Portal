# Openness cookbook

Copy-paste recipes for the calls you actually need. All V21, C#, .NET Framework 4.8,
x64. The primer explains the model these snippets assume:
[01-openness-primer.md](01-openness-primer.md).

**Assembly references (V21).** Openness is modular from V21: reference
`Siemens.Engineering.Base.dll` plus one assembly per product area, all from the
target-framework subfolder `...\PublicAPI\V21\net48\`. Set **Copy Local: False** on every
one - the manual states that copying them is not supported.

| Assembly | Provides |
|---|---|
| `Siemens.Engineering.Base.dll` | `Siemens.Engineering`, `.Compiler`, `.HW`, `.HW.Features` |
| `Siemens.Engineering.Step7.dll` | `Siemens.Engineering.SW` and everything below it |
| `Siemens.Engineering.Hmi.dll` | `Siemens.Engineering.Hmi.*` |

Namespaces used throughout:

```csharp
// Siemens.Engineering.Base.dll
using Siemens.Engineering;              // TiaPortal, Project, ExportOptions
using Siemens.Engineering.Compiler;     // ICompilable, CompilerResult
using Siemens.Engineering.HW;           // Device, DeviceItem, Subnet, Node
using Siemens.Engineering.HW.Features;  // SoftwareContainer, NetworkInterface, IoSystem

// Siemens.Engineering.Step7.dll
using Siemens.Engineering.SW;           // PlcSoftware
using Siemens.Engineering.SW.Blocks;    // PlcBlock, PlcBlockGroup
using Siemens.Engineering.SW.Tags;      // PlcTagTable, PlcTag
using Siemens.Engineering.SW.Types;     // PlcType, PlcTypeGroup
using Siemens.Engineering.SW.ExternalSources;

// Siemens.Engineering.Hmi.dll
using Siemens.Engineering.Hmi;          // HmiTarget
```

Others you will reach for eventually: `Siemens.Engineering.Cax` (AutomationML),
`.Download`, `.Upload`, `.Compare`, `.Library{,.MasterCopies,.Types}`,
`.HW.{Extensions,Utilities}`, `.SW.TechnologicalObjects{,.Motion}`,
`.Hmi.{Communication,Cycle,Globalization,RuntimeScripting,Screen,Tag,TextGraphicList}`.

---

## Connect

```csharp
// Start a new instance.
using (var portal = new TiaPortal(TiaPortalMode.WithUserInterface))     // or WithoutUserInterface
{
    ...
}

// Attach to one that is already running - useful when a colleague has the project open.
foreach (TiaPortalProcess process in TiaPortal.GetProcesses())
{
    Console.WriteLine($"{process.Id} {process.ProjectPath} {process.Mode}");
    using (TiaPortal portal = process.Attach())
    {
        Project project = portal.Projects.FirstOrDefault();
    }
}
```

`WithoutUserInterface` is faster and cannot raise a modal dialog that blocks your
process. Use the UI while developing so you can watch what happens.

## Projects

```csharp
Project project = portal.Projects.Create(new DirectoryInfo(@"C:\TIA\Projects"), "BottleLine");
Project project = portal.Projects.Open(new FileInfo(@"C:\TIA\Projects\BottleLine\BottleLine.ap21"));

project.Save();
project.Close();

Console.WriteLine(project.Path.FullName);
project.SetAttribute("Author", "cheezwedge");
```

## Find things

Recursion, not indices - a CPU's software can sit at any depth.

```csharp
static IEnumerable<DeviceItem> Traverse(DeviceItem item)
{
    yield return item;
    foreach (var child in item.DeviceItems)
        foreach (var nested in Traverse(child)) yield return nested;
}

static IEnumerable<DeviceItem> Traverse(Device device) =>
    device.DeviceItems.SelectMany(Traverse);

// PLC software
PlcSoftware software = Traverse(plcDevice)
    .Select(di => di.GetService<SoftwareContainer>()?.Software)
    .OfType<PlcSoftware>()
    .FirstOrDefault();

// HMI software
HmiTarget hmi = Traverse(hmiDevice)
    .Select(di => di.GetService<SoftwareContainer>()?.Software)
    .OfType<HmiTarget>()
    .FirstOrDefault();

// PROFINET interface
NetworkInterface itf = Traverse(plcDevice)
    .Select(di => di.GetService<NetworkInterface>())
    .FirstOrDefault(x => x != null);
```

## Discover attributes when a name has moved

The most useful debugging call in the API.

```csharp
foreach (EngineeringAttributeInfo info in node.GetAttributeInfos())
    Console.WriteLine($"{info.Name,-32} readonly={info.ReadOnly}");

object address = node.GetAttribute("Address");
node.SetAttribute("Address", "192.168.0.1");
```

## Devices and modules

```csharp
Device plc = project.Devices.CreateWithItem(
    "OrderNumber:6ES7 214-1AH50-0XB0/V1.1",   // catalog identifier, matched exactly
    "PLC_1",                                   // DeviceItem (CPU) name
    "PLC_1");                                  // Device (station) name

// Plug a module. Ask before plugging: which item accepts it differs by CPU family.
DeviceItem target = plc.DeviceItems.First();
if (target.CanPlugNew(moduleTypeIdentifier, "AQ_1", 2))
    target.PlugNew(moduleTypeIdentifier, "AQ_1", 2);

// What is already plugged
foreach (var item in Traverse(plc))
    Console.WriteLine($"{item.PositionNumber,3} {item.Name}");
```

If `CreateWithItem` throws, the identifier is wrong. Print every spelling you tried:
`6ES7214-...` vs `6ES7 214-...`, with and without `/V1.1`.

## Addresses, subnet, PROFINET IO

```csharp
Node node = itf.Nodes.First();
node.SetAttribute("Address", "192.168.0.1");
node.SetAttribute("SubnetMask", "255.255.255.0");
node.SetAttribute("UseRouter", true);
node.SetAttribute("RouterAddress", "192.168.0.254");

// Create the subnet through the node - no need to name the subnet type.
Subnet subnet = node.CreateAndConnectToSubnet("PN_IE_1");
// or join an existing one
otherNode.ConnectToSubnet(subnet);

// PROFINET device name on the wire
itf.SetAttribute("PnDeviceNameAutoGeneration", false);
itf.SetAttribute("PnDeviceName", "plc-1");

// IO system, then attach each IO device
IoSystem ioSystem = itf.IoControllers.First().CreateIoSystem("PROFINET IO-System");
NetworkInterface deviceItf = /* the IO device's interface */;
deviceItf.IoConnectors.First().ConnectToIoSystem(ioSystem);
```

## Tag tables and tags

No file format involved - prefer this over XML import always.

```csharp
PlcTagTable table = software.TagTableGroup.TagTables.Create("Inputs");

PlcTag tag = table.Tags.Create("EStop_Ok", "Bool", "%I0.0");
foreach (var item in tag.Comment.Items) item.Text = "E-stop status contact";

// Read back
foreach (PlcTag t in table.Tags)
    Console.WriteLine($"{t.LogicalAddress,-8} {t.Name,-28} {t.DataTypeName}");

// Sub-folders
PlcTagTableGroup group = software.TagTableGroup.Groups.Create("Field");
```

## Blocks: groups, find, delete, move

```csharp
PlcBlockGroup devices = software.BlockGroup.Groups.Create("Devices");

PlcBlock block = software.BlockGroup.Blocks.Find("FB_Motor");
block?.Delete();

// Recurse - Find only looks in one group
static IEnumerable<PlcBlock> AllBlocks(PlcBlockGroup g) =>
    g.Blocks.Concat(g.Groups.SelectMany(AllBlocks));

// Block attributes
Console.WriteLine($"{block.Name} #{block.Number} {block.ProgrammingLanguage} " +
                  $"consistent={block.IsConsistent}");
```

## Import code as an external SCL source

The route this toolchain uses for all logic.

```csharp
var sources = software.ExternalSourceGroup.ExternalSources;

// Remove a stale source of the same name first, or you regenerate old code.
sources.Find("FB_Motor")?.Delete();

PlcExternalSource source = sources.CreateFromFile("FB_Motor", @"C:\gen\FB_Motor.scl");
source.GenerateBlocksFromSource();
```

Import in dependency order: types, then blocks that declare them, then the OB.

## Instance DBs

```csharp
software.BlockGroup.Blocks.CreateInstanceDB(
    "DB_BottleLine",   // name
    true,              // auto-number
    1,                 // number (ignored when auto-numbered)
    "FB_BottleLine");  // the FB it instantiates
```

## Export: XML, SCL, source documents

```csharp
// SimaticML - use one of these as the schema template for your own TIA build
block.Export(new FileInfo(@"C:\gen\FB_Motor.xml"), ExportOptions.WithDefaults);
plcType.Export(new FileInfo(@"C:\gen\UDT_DevIf.xml"), ExportOptions.WithDefaults);
tagTable.Export(new FileInfo(@"C:\gen\Inputs.xml"), ExportOptions.WithDefaults);

// Plain SCL, code blocks only
block.ExportToFile(new FileInfo(@"C:\gen\FB_Motor.scl"));

// SIMATIC Source Documents, V20+ - the git-friendly format
block.ExportAsDocuments(new DirectoryInfo(@"C:\gen\vcs"), block.Name);   // .s7dcl + .s7res
```

Call the document API through reflection if your binary must also run against a
pre-V20 assembly - see [`Exporter.cs`](../openness/TiaGen.Openness/Exporter.cs).

## Import SimaticML

```csharp
software.BlockGroup.Blocks.Import(new FileInfo(path), ImportOptions.Override);
software.TypeGroup.Types.Import(new FileInfo(path), ImportOptions.Override);
software.TagTableGroup.TagTables.Import(new FileInfo(path), ImportOptions.Override);
```

`ImportOptions.Override` replaces an existing object of the same name;
`ImportOptions.None` fails instead. Prefer failing while developing.

## Compile and read the result

```csharp
CompilerResult result = software.GetService<ICompilable>().Compile();
Console.WriteLine($"{result.State}: {result.ErrorCount} errors, {result.WarningCount} warnings");

static void Flatten(CompilerResultMessageComposition messages, int depth)
{
    foreach (CompilerResultMessage m in messages)
    {
        Console.WriteLine($"{new string(' ', depth * 2)}{m.State}: {m.Description} " +
                          $"[{m.ErrorCount}E/{m.WarningCount}W]");
        Flatten(m.Messages, depth + 1);
    }
}
Flatten(result.Messages, 0);
```

Also compilable: a single `PlcBlock`, an `HmiTarget`, or the whole `Device`.

## HMI tags and screens

```csharp
Siemens.Engineering.Hmi.Tag.TagTable table = hmi.TagFolder.TagTables.Create("Machine");
var tag = table.Tags.Create("Machine_State");
// Prefer SetAttribute here: whether Connection is typed as a string or as an object
// reference has varied between versions, and SetAttribute takes object either way.
tag.SetAttribute("Connection", "HMI_Connection_1");
tag.SetAttribute("PlcTag", "DB_BottleLine.State");
tag.SetAttribute("DataType", "Int");

hmi.ScreenFolder.Screens.Create("Overview");
```

Attribute names in this area move between versions. Set them individually and log
failures rather than assuming - see [05-hmi-and-screens.md](05-hmi-and-screens.md).

## What this toolchain deliberately does not call

`DownloadProvider`, online connections, and anything F-related. Openness can do all
three. See [07-safety-and-gates.md](07-safety-and-gates.md) for why they stay human.

## Error handling that saves time

```csharp
try
{
    node.SetAttribute("Address", ip);
}
catch (EngineeringTargetInvocationException ex)   // the object rejected the call
{
    // Log WHAT you attempted, not just the message - that is what identifies a
    // renamed attribute in a new TIA version.
    Console.Error.WriteLine($"SetAttribute(Address, {ip}) failed: {ex.Message}");
}
catch (EngineeringNotSupportedException ex)       // not available in this version
{
    ...
}
```

Common exception types: `EngineeringTargetInvocationException`,
`EngineeringNotSupportedException`, `EngineeringObjectDisposedException`,
`EngineeringSecurityException` (usually the missing Windows group).
