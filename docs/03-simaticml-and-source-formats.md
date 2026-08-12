# Getting code into TIA: SCL, SimaticML and source documents

Four routes exist. Choosing wrongly is the single biggest cause of brittle code
generators, so this document explains what each one is for and why this toolchain
picks the one it does.

---

## The decision, up front

| | External SCL source | SimaticML XML | SIMATIC SD documents (V20+) | Direct API |
|---|---|---|---|---|
| Format | plain `.scl` text | `.xml` | `.s7dcl` + `.s7res` | none |
| Schema to track | **none** | yes, per TIA version | yes, but text | none |
| Errors surface as | **compiler errors** | import failure | import failure | exception |
| LAD/FBD graphics | no | yes | **yes, as text (V21)** | no |
| Good for | **generating logic** | round-tripping, bulk edits | **version control, LAD** | tags, DBs, groups |
| Risk when generating | low | high | medium | low |

> **V21 changed the SD row.** The text-based exchange format was extended to cover
> **LAD, Safety-LAD, FBD, Safety-FBD, SCL, blocks with mixed languages, data blocks and
> PLC data types**. Text-based ladder generation is therefore a real option now, where
> before it meant hand-writing `FlgNet` XML. See §3.

**This repo generates SCL and creates tags through the API.** Everything else is
provided for the jobs it is genuinely better at.

---

## 1. External SCL sources - the generation route

An external source is a text file registered in the project under *External source
files*. TIA compiles it and produces real blocks.

```csharp
var sources = software.ExternalSourceGroup.ExternalSources;
PlcExternalSource source = sources.CreateFromFile("FB_Motor", @"C:\gen\FB_Motor.scl");
source.GenerateBlocksFromSource();
```

### Why this is the right default for generated code

- **No schema versioning.** SCL text is stable across TIA versions in a way the XML
  schema namespaces are not. A generator written for V18 still emits valid V21 SCL.
- **The compiler is your validator.** A typo produces a compiler error naming the
  line, not a silent import that yields a subtly wrong block.
- **It is reviewable.** A colleague can read a diff of `.scl`. Nobody reviews a diff
  of SimaticML.
- **One file can hold several units**, and a folder of files imports in whatever
  order you choose.

### Dependency order is everything

A source can only reference what already exists. That is why the generated files are
numbered, and why `plan.json` carries them as an ordered list:

```
10_UDT_DevIf.scl        types first
20_FB_Motor.scl         library blocks that declare those types
...
30_UDT_<M>Auto.scl      machine interface types
40_FB_<M>.scl           the machine FB, which declares the library FBs
                        → instance DB created through the API here
60_Main.scl             the OB, which calls the instance DB
```

Import out of order and you get "unknown type" errors that look like broken code but
are just a sequencing bug.

### What SCL sources can declare

```scl
TYPE "UDT_Name"           ... END_TYPE                  -- PLC data type
FUNCTION_BLOCK "FB_Name"  ... END_FUNCTION_BLOCK        -- FB
FUNCTION "FC_Name" : Void ... END_FUNCTION              -- FC
ORGANIZATION_BLOCK "Main" ... END_ORGANIZATION_BLOCK    -- OB
DATA_BLOCK "DB_Name"      ... END_DATA_BLOCK            -- global DB
```

Optimized access is an attribute on the declaration:

```scl
FUNCTION_BLOCK "FB_Motor"
{ S7_Optimized_Access := 'TRUE' }
VERSION : 0.1
```

### The one rough edge: organization blocks

An `ORGANIZATION_BLOCK` in an external source does not carry its event class or OB
number explicitly, and a fresh S7-1200 project already has `Main [OB1]`. The plan
therefore **deletes `Main` before importing** the generated OB, which makes the
outcome deterministic rather than dependent on how a name clash is resolved.

If that import fails on your installation, the fallback is trivial and the driver
prints it: keep the original `Main [OB1]` and add one line.

```scl
"DB_BottleLine"();
```

That is the entire cyclic program. Everything else is generated. It is worth knowing
this is the *only* step in the pipeline with a manual fallback.

### Instance DBs

External sources are not the reliable way to create an instance DB. Use the API:

```csharp
software.BlockGroup.Blocks.CreateInstanceDB("DB_BottleLine", true, 1, "FB_BottleLine");
```

(name, auto-number, number, name of the FB it instantiates)

---

## 2. SimaticML XML - the round-trip route

SimaticML is Siemens' XML for project data. Every block, type and tag table can be
exported to it and imported from it.

```csharp
block.Export(new FileInfo(path), ExportOptions.WithDefaults);
group.Blocks.Import(new FileInfo(path), ImportOptions.Override);
```

### Structure

```xml
<?xml version="1.0" encoding="utf-8"?>
<Document>
  <Engineering version="V21" />
  <DocumentInfo>
    <Created>2026-08-05T20:21:30Z</Created>
    <ExportSetting>WithDefaults</ExportSetting>
  </DocumentInfo>
  <SW.Tags.PlcTagTable ID="0">
    <AttributeList>
      <Name>Inputs</Name>
    </AttributeList>
    <ObjectList>
      <SW.Tags.PlcTag ID="1" CompositionName="Tags">
        <AttributeList>
          <DataTypeName>Bool</DataTypeName>
          <ExternalAccessible>true</ExternalAccessible>
          <ExternalVisible>true</ExternalVisible>
          <ExternalWritable>true</ExternalWritable>
          <LogicalAddress>%I0.0</LogicalAddress>
          <Name>EStop_Ok</Name>
        </AttributeList>
        <ObjectList>
          <MultilingualText ID="2" CompositionName="Comment">
            <ObjectList>
              <MultilingualTextItem ID="3" CompositionName="Items">
                <AttributeList>
                  <Culture>en-US</Culture>
                  <Text>E-stop status contact</Text>
                </AttributeList>
              </MultilingualTextItem>
            </ObjectList>
          </MultilingualText>
        </ObjectList>
      </SW.Tags.PlcTag>
    </ObjectList>
  </SW.Tags.PlcTagTable>
</Document>
```

The recurring shape:

- exactly **one `<Document>`** per file;
- one top-level `SW.*` element naming the object type
  (`SW.Blocks.FB`, `SW.Blocks.FC`, `SW.Blocks.OB`, `SW.Blocks.GlobalDB`,
  `SW.Blocks.InstanceDB`, `SW.Types.PlcStruct`, `SW.Tags.PlcTagTable`);
- **`AttributeList`** holds scalar properties, **`ObjectList`** holds children;
- **`ID`** is a document-unique integer (0 … 2147483647) that you may assign freely;
- child elements carry **`CompositionName`** naming the parent composition they
  belong to (`Tags`, `Members`, `Comment`, `Items`);
- an FB's interface lives in `AttributeList/Interface/Sections`, in its own
  namespace; code lives in `SW.Blocks.CompileUnit` children with a
  `ProgrammingLanguage` attribute.

### Why generating this is a trap

The interface and network-source elements are **namespaced with a schema version**
that tracks the TIA release:

```xml
<Sections xmlns="http://www.siemens.com/automation/Openness/SW/Interface/v5">
<StructuredText xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/StructuredText/v3">
<FlgNet xmlns="http://www.siemens.com/automation/Openness/SW/NetworkSource/FlgNet/v4">
```

Those `vN` suffixes change between versions. A generator that hard-codes them
silently stops working after an upgrade.

**Version compatibility in V21**, from the manual: the V21 libraries **write** engineering
version V21 files and **read** V18, V19, V20 and V21. A V17-or-older export will not
import. V21 also "improved formatting of SimaticML", so expect exports to differ
cosmetically from earlier versions even for unchanged blocks - relevant if you diff them.

Which brings us to the rule:

### What the GUI actually offers (V21 Upd1, verified)

Checked on a real install, because it changes who needs the driver. Right-clicking a
block in the project tree offers **no "Export" item at all**. The only export-shaped
entry is **"Generate source from blocks"**, with a submenu. There is no SimaticML XML
export in the GUI.

That is worth knowing before planning any workflow around exports: **SimaticML is an
Openness-only format in practice.** You cannot hand an engineer a menu path that produces
it, so anything built on reading XML needs the driver, or an Add-In, running against the
project.

The consequence for the rule in this section stands and gets sharper: to obtain your own
export as a schema template, you must go through the API. There is no manual fallback.

### Adopting your own export as the template

**Never hand-write SimaticML from documentation. Export one real object from your own
TIA build and use it as the template.**

```bat
TiaGen.Openness.exe export --project C:\TIA\Projects\BottleLine\BottleLine.ap21 ^
                           --out C:\gen\templates --formats xml
```

Open one of the resulting files and copy the `<Engineering version="...">` line and
every `xmlns` you find. Those are ground truth for your installation; nothing in any
document, including this one, outranks them.

The tag-table XML this repo generates (`out/*/tags/*.xml`) is provided for offline
import only, with `--engineering-version` to match your build. The primary path uses
`Tags.Create` and needs no schema at all - which is precisely why it is the primary
path.

---

## 3. SIMATIC SD documents - version control, and LAD

**New in V20, extended in V21, and the headline Openness feature of the release.**
SIMATIC Source Documents are a text representation of blocks:

- **`.s7dcl`** - declarations and code
- **`.s7res`** - comments, titles and translations

V21 extended the format to cover **LAD, Safety-LAD, FBD, Safety-FBD, SCL, blocks with
mixed languages, data blocks and PLC data types**. Two things follow:

1. **A graphical block survives the round trip**, unlike plain SCL export.
2. **Generating ladder is now tractable.** If your house standard is LAD, emitting
   `.s7dcl` is a far better bet than hand-writing `FlgNet` SimaticML with its versioned
   namespaces. This toolchain still generates SCL - but "LAD is impractical to generate"
   stopped being true in V21, and if ladder is a hard requirement for you, this is the
   route to investigate.

Being text, they diff and merge, which is what finally makes a TIA project a reasonable
thing to keep in git. The Version Control Interface understands the format too: V21 adds
VCI support for F-compliant PLC data types, FBD/LAD/SCL and mixed blocks, and data blocks
in SIMATIC SD format.

```csharp
block.ExportAsDocuments(new DirectoryInfo(dir), block.Name);
group.ImportFromDocuments(...);
// plus the bulk variants ExportBlocksAsDocuments / ImportBlocksFromDocuments
```

Both need **V20 or newer**. The exporter here calls them through reflection, so the
same binary still runs against an older assembly and simply reports the format as
unavailable.

```bat
TiaGen.Openness.exe export --project ...\BottleLine.ap21 --out .\vcs --formats documents
```

Commit `vcs/`. Review pull requests against it. That is the intended workflow, and it
is the reason V21 is worth the upgrade for anyone doing CI on PLC code.

---

## 4. Direct API - for everything that is not code

No file, no schema, no round trip:

```csharp
PlcTagTable table = software.TagTableGroup.TagTables.Create("Inputs");
PlcTag tag = table.Tags.Create("EStop_Ok", "Bool", "%I0.0");
foreach (var item in tag.Comment.Items) item.Text = "E-stop status contact";

software.BlockGroup.Groups.Create("Devices");            // block folders
software.BlockGroup.Blocks.CreateInstanceDB(...);        // instance DBs
```

Use this for tags, tag tables, groups and instance DBs - always. It is the least
version-sensitive part of the whole API.

---

## Summary

- Generating logic → **SCL external sources**, in dependency order.
- Tags, tables, instance DBs → **direct API**.
- Version control → **source documents** (`.s7dcl`/`.s7res`), V20+.
- Round-tripping, graphics, bulk edits → **SimaticML**, using *your own export* as
  the template.
- The compile result is the test suite. A generator that does not end in a green
  compile has not proved anything.

## Sources

- [Export/import of SCL blocks](https://docs.tia.siemens.cloud/r/en-us/v21/tia-portal-openness-api-for-automation-of-engineering-workflows/export/import/importing/exporting-data-of-a-plc-device/blocks/export/import-of-scl-blocks) (Siemens)
- [How is the XML file structured for blocks?](https://www.industry-mobile-support.siemens-info.com/en/article/detail/109480446) (Siemens FAQ 109480446)
- [How to read an Openness XML file](https://www.dmcinfo.com/latest-thinking/blog/id/9908/how-to-read-an-openness-xml-file) (DMC)
- [TIA Portal V21 SIMATIC Source Documents](https://industrialmonitordirect.com/blogs/knowledgebase/tia-portal-v21-simatic-source-documents-git-export-format)
- [Openness feature matrix V17-V21](https://t-ia-connect.com/en/compatibility-tia-portal-openness)
