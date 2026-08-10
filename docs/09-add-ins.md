# TIA Add-Ins: putting Claude inside TIA Portal

An Add-In is user-written code that runs **inside the TIA Portal process** and appears
in TIA Portal's own context menus. It is the mechanism that closes the one real gap
between an external assistant and Siemens' in-editor agent: placement.

---

## 1. Add-In vs Openness application vs Modular Application Creator

Siemens publishes a selection guide for exactly this choice (entry 109816879). The
distinction that matters:

| | Runs | Started by | Use for |
|---|---|---|---|
| **Openness application** | its own process, driving TIA from outside | you, from a shell or CI | batch generation, CI, anything unattended. **The `apply` driver in this repo.** |
| **Add-In** | inside the TIA Portal process | the engineer, from a context menu | interactive helpers on whatever is selected. **The Claude bridge.** |
| **Modular Application Creator** | its own framework | the engineer, via a technological UI | parameterised generation from pretested Equipment Modules, with input validation |

The Modular Application Creator is worth knowing about because it is Siemens' own
answer to what this repository does: Equipment Modules bundle a parameter UI with
generation logic, and MAC assembles a project from them. If you find yourself wanting
a form instead of a YAML file, that is the product to look at.

An Add-In and an Openness application are not exclusive - the sensible split is an
Add-In that gathers context and shells out to your Openness application for the heavy
work.

---

## 1b. What V21 changed

Three things, and the first will bite you:

- **Add-Ins built against V17-V20 do not run on V21.** The old
  `Siemens.Engineering` libraries are not shipped with V21, so Add-Ins - like Openness
  applications - must be adapted and recompiled against the new modular libraries. See
  [01-openness-primer.md](01-openness-primer.md) §2.4.
- **Three kinds of Add-In now exist**: **Corporate**, **System** and **User** Add-Ins.
  They differ in where they are installed and who may enable them, which is how an
  organisation ships a mandatory Add-In to every engineer without relying on each of them
  activating it by hand.
- **Shared .NET class libraries** can now be written once and referenced from both an
  Openness application and an Add-In, because both consume the same uniformed Openness
  libraries. That is the sanctioned way to avoid duplicating the logic between
  `TiaGen.Openness` and `TiaGen.AddIn`.

## 2. Anatomy

A minimal Add-In is two classes.

**A view provider** declares *where* the menu appears. One class per area:

| Provider base class | Appears in |
|---|---|
| `ProjectTreeAddInProvider` | the project tree |
| `DevicesAndNetworksAddInProvider` | the hardware and network editor |
| `ProjectLibraryTreeAddInProvider` | the project library |
| `GlobalLibraryTreeAddInProvider` | global libraries |
| `VciEditorAddInProvider` | the Version Control Interface workspace |

**A context menu** class inherits `ContextMenuAddIn` and holds the actual code. It
declares its items, and each item is generic over the selected object type - so an
item registered for `PlcBlock` only lights up when blocks are selected, and TIA hands
you exactly those objects.

```csharp
public class ClaudeBridgeAddIn : ContextMenuAddIn
{
    private readonly TiaPortal _tiaPortal;

    public ClaudeBridgeAddIn(TiaPortal tiaPortal) : base("Claude")
    {
        _tiaPortal = tiaPortal;      // the LIVE portal - never construct your own
    }

    protected override void BuildContextMenuItems(ContextMenuAddInRoot root)
    {
        root.Items.AddActionItem<PlcBlock>("Export for Claude", OnExport, OnStatus);
    }

    private void OnExport(MenuSelectionProvider<PlcBlock> selection)
    {
        foreach (PlcBlock block in selection.GetSelection())
        {
            block.ExportToFile(new FileInfo(...));
        }
    }

    private MenuStatus OnStatus(MenuSelectionProvider<PlcBlock> selection)
        => MenuStatus.Enabled;
}
```

The single most important difference from an Openness application: **you are given the
`TiaPortal` object, you do not create one.** You are already inside the process, the
project is already open, there is no connection to make and no assembly resolver to
install.

---

## 3. Build and deploy

### The easy way

Siemens publish a NuGet package that sets the whole thing up:

```
Siemens.Collaboration.Net.TiaPortal.AddIn.Build
```

Create a C# **class library**, install the package, and pick the version matching your
TIA major version (`16.*` = V16, `17.*` = V17, … `21.*` for V21). Then build. It does all
of this for you:

- references `Siemens.Engineering.AddIn.dll` from your TIA installation, detected
  automatically (set the `TiaPortalLocation` property in the `.csproj` if detection
  fails)
- **generates template view-provider and context-menu classes if none exist** - which
  makes it the authoritative source for the exact API shape on your version, better
  than any documentation including this page
- enforces the right target framework: **.NET Framework 4.6.2 for V16, 4.8 for V17 and
  later**
- writes and maintains the publisher configuration, detecting dependent assemblies and
  certificates
- adds a post-build event that converts `*.dll` → `*.addin` and deploys it into the TIA
  Portal installation (needs elevation)
- deploys the **TIA Add-In Tester** and presets the Debug configuration, so you can
  debug without installing anything

There are also Visual Studio templates on the **TIA Portal setup DVD2**, under
`Support\TIA_Portal_Add-In_Tools\Development`, for VS2019, VS2022 and VS Code.

### What is happening underneath

1. Your class library compiles to a `.dll`.
2. `Siemens.Engineering.AddIn.Publisher.exe` (in the TIA installation directory)
   converts it to a single `.addin` package.
3. The `.addin` goes into the **`AddIns` folder of the TIA Portal installation
   directory**.
4. In TIA Portal, the **Add-Ins task card** lists it. **Add-Ins are deactivated by
   default** - the engineer activates each one explicitly. That activation is a
   deliberate trust gate; do not try to automate around it.

### Debugging

Use the **TIA Add-In Tester** (Siemens entry 109783096). It loads the add-in without
installing it, so the edit-build-test loop does not involve copying files into
Program Files or restarting TIA Portal.

---

## 4. The Claude bridge

`openness/TiaGen.AddIn/` implements the round trip. Two menu items, no embedded model,
no network calls from inside TIA Portal:

```
right-click a block / PLC / project
  └─ Claude ▸ Export for Claude        → writes a workspace folder and opens it
                                          · every selected block as .scl and .xml
                                          · context.md: devices, blocks, tag tables
                                          · inbox/ for Claude to write into
  └─ Claude ▸ Apply SCL from workspace → imports every .scl in inbox/ as an external
                                          source, generates blocks, compiles, reports
```

You then point Claude Code at the workspace folder. It reads `context.md` and the
exported sources, writes revised or new `.scl` files into `inbox/`, and you right-click
again.

### Why it works this way

**No LLM call from inside TIA Portal.** An Add-In runs in TIA's process: an unhandled
exception or a hung HTTP request takes the IDE down with it, and the engineer loses
unsaved work. Doing network I/O and JSON parsing in there to save one Alt-Tab is a bad
trade. The workspace folder is the interface, and it has the useful side effect of
being inspectable and diffable.

**It composes with the rest of the repo.** `context.md` and the export are the same
artefacts `TiaGen.Openness.exe export` produces, so anything you learn to do in the
repo-side flow works here too.

**Claude never crosses a gate.** The Add-In imports and compiles. It does not
download, go online, or force a value - same posture as the driver, for the same
reasons ([07-safety-and-gates.md](07-safety-and-gates.md)).

### If you want the chat inside TIA anyway

It is possible - a WPF window from the Add-In, calling the Claude API. Two warnings
before you spend the week: everything in the process-stability paragraph above applies
doubly, and you are now maintaining a chat UI. The workspace round trip gets you 90%
of the value for 10% of the code, and it keeps the model's output in a file you
reviewed before it touched the project.

---

## 5. Verifying the API shape on your machine

The Add-In API is small but its exact signatures are the kind of thing that shifts
between versions. Two local sources outrank any document:

**The generated templates.** Install `Siemens.Collaboration.Net.TiaPortal.AddIn.Build`
into an empty class library and build. The template classes it writes are correct for
your version by construction. Diff them against
`openness/TiaGen.AddIn/ClaudeBridgeAddIn.cs`.

**The TIA Portal Openness Explorer** (entry 109760816). A standalone tool that walks
your actual open project as an object tree and shows the attributes and methods of the
selected object. Point it at
`...\Portal V21\PublicAPI\V21\Siemens.Engineering.dll`, and it becomes a live API
browser for your exact installation. This is the fastest way to settle any
"is that attribute really called `Address`?" question - including the ones flagged as
unverified in this repo's driver.

Also local, and easy to miss:

```
C:\Program Files\Siemens\Automation\Portal V21\PublicAPI\V21\HW Parameter description
```

The **Hardware Parameter List** - which hardware parameters Openness can reach and what
they mean. If you are going to harden one thing in
`openness/TiaGen.Openness/HardwareBuilder.cs`, read this folder first.

---

## Sources

- [TIA Add-In Build Package](https://github.com/tia-portal-applications/tia-addin-build-package) (Siemens-affiliated, TIA Portal Applications org)
- [TIA Add-Ins Getting Started](https://support.industry.siemens.com/cs/attachments/109779415/109779415_TIA_Add-In_Getting_Started_DOC_V1_3_EN.pdf) (Siemens, entry 109779415)
- [Selection Guide: TIA Add-In / Openness Application / Modular Application Creator](https://support.industry.siemens.com/cs/attachments/109816879/109816879_SelectionGuide_Add-In_Openness-App_MAC.pdf) (Siemens, entry 109816879)
- [TIA Portal Openness Explorer](https://support.industry.siemens.com/cs/attachments/109760816/109760816_TiaOpennessExplorer_DOC_V2_0_en.pdf) (Siemens, entry 109760816 - V2.0 covers V21)
- [TIA Add-In Tester](https://support.industry.siemens.com/cs/ww/en/view/109783096) (Siemens, entry 109783096)
- [How to get started with TIA Portal Openness](https://github.com/orgs/tia-portal-applications/discussions/1) (TIA Portal Applications discussion)
- [NuGet: Siemens.Collaboration.Net.TiaPortal.AddIn.Build](https://www.nuget.org/packages/Siemens.Collaboration.Net.TiaPortal.AddIn.Build)
