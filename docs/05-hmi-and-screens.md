# HMI: tags, alarms and screens

The HMI side of Openness is uneven. This document says plainly what is automatable,
what is not, and how the toolchain splits the work so the manual part is as small as
possible.

---

## 1. What Openness can and cannot do for an HMI

> **Corrected.** An earlier version of this document said placing objects inside a screen
> "is not uniformly supported". That was too pessimistic. The Openness manual carries an
> explicit **object list** stating support per object type and panel family, and most
> screen objects *are* supported. Siemens' own Eigen agent adds screen items, writes
> dynamization JavaScript and instantiates faceplates - all through this API.

| Object | Automatable | How |
|---|---|---|
| HMI tag tables and folders | yes | `hmiTarget.TagFolder.TagTables.Create(...)` |
| HMI tags, bound to PLC tags | yes | `table.Tags.Create(name)` + connection / PLC tag |
| PLC ↔ HMI connection | yes | `hmiTarget.Connections` |
| Screens, templates, global screen | yes | `hmiTarget.ScreenFolder.Screens.Create(name)` |
| Pop-up and slide-in screens, permanent area | yes, **not on Basic panels** | |
| **Most screen objects** | **yes** | see the support table below |
| Dynamization - display, visibility, movements | yes | (operability: not on Basic) |
| Text lists, graphic lists | yes | API and XML import/export |
| VB scripts | yes, **not on Basic panels** | |
| Cycles, function lists, project graphics | yes | |
| Faceplate instances | yes, **not on Basic panels** | |
| **WinCC Runtime Professional** | **no** | not supported by Openness at all |
| **Device proxy files** | **no** | |

### Which screen objects are supported

For classic panels the manual is precise. Supported on Comfort Panels (and generally on
Mobile / RT Advanced): line, polyline, polygon, ellipse, circle, rectangle, text field,
graphic view, I/O field, date/time field, graphic I/O field, button, switch, symbolic I/O
field, bar, symbol library, slider, gauge, clock, function keys, **faceplate instances**,
user view, criteria analysis view, ProDiag overview, GRAPH overview, PLC code view.

**Not** supported, which matters for the screens this generator plans: **alarm view,
alarm window, alarm indicator, f(t) and f(x) trend views, table view, screen window,
recipe view, system diagnostic view/window, status/force**, HTML browser, media player,
check boxes, option buttons, combo/list boxes, connectors and pipes.

Basic Panels are a reduced subset: no polyline/polygon, no slider, gauge, clock or symbol
library, no faceplates, no VB scripts, no internal tags.

For **WinCC Unified** the picture is better than the classic table and moving fast. V21
alone added HMI graphic list management, screen **layout field export/import**, and new
controls - Alarm Line, Process Diagnostic Criteria Analysis, Process Diagnosis Graph
Overview, Process Diagnosis Overview, Process Diagnosis PLC Code Viewer - plus an Alarm
Indicator widget.

### What this means for the generated plan

The consequence is not "screens cannot be automated". It is:

- **Faceplate grids and status tiles are automatable** - buttons, I/O fields, bars,
  indicators and faceplate instances are all in the supported set.
- **The alarm and trend screens are not**, at least on classic panels: the alarm and
  trend *controls* are excluded from the API. Those two screens stay manual regardless of
  how much code you write.

So `screens.json` should be read as an executable plan for the device screens and an
advisory one for the alarm and trend screens. Turning the first part into API calls is a
worthwhile piece of work that this repo has not done yet.

---

## 2. How this toolchain splits it

**The driver creates the containers. The generator produces the content as an
explicit plan.**

```
create_hmi_connection   → the PLC/HMI connection
create_hmi_tags         → every HMI tag, typed and bound to its PLC address
create_hmi_screens      → the screens, empty
out/*/hmi/screens.json  → every object, with position, size and tag binding
out/*/hmi/alarms.json   → every alarm, with class, priority and trigger tag
out/*/hmi/hmi_tags.csv  → the same tags as a sheet
```

To be clear about why: this split reflects **what this repo has implemented**, not the
limit of the API. Screen items are largely creatable (§1). Emitting a plan was the
conservative first step because it is useful even when the API call for a given object
turns out to be unavailable on your panel family - and because a plan can be reviewed
before anything is written. Making the device screens executable is the obvious next
piece of work.

### The tags are the valuable half

Screen layout is an afternoon of dragging. Getting 150 tag bindings right, with
correct data types and no typos in
`DB_BottleLine.InfeedConveyor.Hmi.Sts_Running`, is where the errors actually live -
and that half is fully automated.

---

## 3. The tag interface: one struct, one faceplate

Every generated device FB carries the same static member:

```scl
Hmi : "UDT_DevIf";
```

so every device is reachable at a predictable symbolic address:

```
DB_<Machine>.<Equipment>.Hmi.Cmd_Start      HMI writes
DB_<Machine>.<Equipment>.Hmi.Sts_Running    HMI reads
DB_<Machine>.<Equipment>.Hmi.Sts_Fault
DB_<Machine>.<Equipment>.Hmi.Sts_FaultCode
DB_<Machine>.<Equipment>.Hmi.Set_Value
DB_<Machine>.<Equipment>.Hmi.Act_Value
```

One struct type for motors, valves, drives and analog channels means **one faceplate
type covers every device**. Build `FP_Motor` once, and adding the twentieth motor to
the machine costs one spec entry and one faceplate instance.

The full field list and the fault-code table are in
[`library/scl/10_UDT_DevIf.scl`](../library/scl/10_UDT_DevIf.scl).

### Momentary commands

`Cmd_*` members are momentary: the device FB evaluates them and clears them in the
same cycle. On the HMI, wire buttons to **set the bit on press** - do not use a
toggle, and do not reset the bit from the HMI.

Machine-level commands work the same way, on `DB_<Machine>.Cmd`:
`Start`, `Stop`, `Reset`, `ReqManual`, `ReqAuto`.

### Machine state

`DB_<Machine>.State` is an `Int`; bind a text list to it:

| Value | Meaning |
|---|---|
| 0 | Safety stop |
| 1 | Fault |
| 2 | Stopped - reset required |
| 3 | Manual |
| 4 | Auto ready |
| 5 | Auto running |

The generated `screens.json` already contains this text list on the overview screen.

---

## 4. Alarms

`out/*/hmi/alarms.json` is ready to configure:

```json
{
  "number": 8,
  "name": "TankLevel_HiHi",
  "text": "TankLevel: above 97.0 %",
  "class": "Critical",
  "priority": 12,
  "trigger_tag": "DB_BottleLine.Alarms.TankLevel_HiHi",
  "ack_required": true
}
```

Every trigger is a `Bool` member of `DB_<Machine>.Alarms`, refreshed every cycle by
the machine FB, so the PLC side needs nothing further. Classes are assigned by
severity: `_Lo`/`_Hi` become warnings, `_LoLo`/`_HiHi` and device faults become
alarms, and a safety-circuit open is critical.

Only trip-level conditions feed `AnyFault`, so a `_Lo` warning does not stop the
machine while a `_LoLo` does. If you disagree with that split for your process,
change it in the machine FB - it is one boolean expression, clearly marked.

---

## 5. Filling in the screens

Pick whichever suits your scale.

**By hand, from the plan.** For a small machine this is the fastest route. Open
`screens.json`, place the objects at the coordinates given, bind the tags named. The
plan is already laid out for the panel resolution in the spec, with a header strip and
a faceplate grid.

**By faceplate, once per device type.** Build `FP_Motor`, `FP_Valve`, `FP_Vfd`,
`FP_AnalogIn` against `UDT_DevIf` once. Then each screen is a grid of instances, and
`screens.json` tells you exactly which instance binds to which device.

**With SiVArc.** SiVArc generates screens from rules driven by the PLC program, and
V21 extends it to Unified Edge runtime with additional Openness APIs. If you are
building many similar machines, this is the route that scales - and the generated
tag/UDT structure is deliberately regular enough to write SiVArc rules against.

**By screen import.** Export one finished screen from your project, look at the format
your TIA version produces, and generate more of them from `screens.json`. Same rule as
for SimaticML: **use your own export as the template**, never a format documented
elsewhere. Unified screens export as JSON; classic panels as XML.

---

## 6. When the HMI stages fail

The driver attempts each HMI call individually and logs what it tried, because
attribute names in this corner move between versions. A typical outcome:

```
   !  binding 'InfeedConveyor_Sts_Running' to DB_BottleLine... failed:
      EngineeringTargetInvocationException: attribute 'PlcTag' not found
```

That tells you exactly what to fix. Two ways forward:

1. Find the current attribute name with `GetAttributeInfos()` on a tag you created by
   hand in the IDE, then adjust `HmiBuilder.cs`.
2. Skip the stage and import the tags instead:
   `--skip create_hmi_tags`, then import `hmi_tags.csv` through the panel's tag
   import, or through the Siemens **Excel Importer/Exporter** for WinCC Unified,
   which is built on Openness and handles this mapping already.

Neither loses any generated work: the tag list is data, and the PLC side is unaffected.

---

## 7. Unified vs Comfort

The spec's `hmi.runtime` selects the intent, and it changes two things:

- **Comfort (classic WinCC):** screens exchange as SimaticML XML. Scripting is
  VBScript. Faceplates are more limited.
- **Unified (default for V21):** screens exchange as JSON, with full properties, text
  and dynamization. Scripting is JavaScript. Unified Basic panels have no scripting -
  only Unified Comfort and Unified PC runtimes do, which matters if your screen plan
  relies on scripts.

The generated tag structure, alarm list and screen plan are runtime-agnostic; only
the final import format differs.

## Sources

- [Exporting all screens of an HMI device](https://docs.tia.siemens.cloud/r/en-us/v20/tia-portal-openness-api-for-automation-of-engineering-workflows/export/import/importing/exporting-data-of-an-hmi-device/screens/exporting-all-screens-of-an-hmi-device) (Siemens)
- [Automatically creating and exporting HMI screen objects - Excel Importer/Exporter](https://support.industry.siemens.com/cs/attachments/109792619/109792619_Excel_Importer_Exporter_V2_1_1.pdf) (Siemens, entry 109792619)
- [Configuring screen management (RT Unified)](https://docs.tia.siemens.cloud/r/en-us/v20/configuring-screens-rt-unified/basics-rt-unified/configuring-screen-management-rt-unified/configuring-screen-management-rt-unified) (Siemens)
- [Openness feature matrix V17-V21](https://t-ia-connect.com/en/compatibility-tia-portal-openness)
