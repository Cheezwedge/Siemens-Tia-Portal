# TiaGen.AddIn - Claude inside TIA Portal

A TIA Portal Add-In that adds two items to the project-tree context menu:

```
right-click a block / PLC / project
  └─ Claude ▸ Export for Claude         → workspace folder, then opens it
  └─ Claude ▸ Apply SCL from workspace  → imports inbox\*.scl, generates blocks, compiles
```

The workspace lives at `Documents\TiaGen\workspace\<project>\`:

```
export/      blocks as .scl (or .xml where a block has no textual form)
inbox/       where Claude Code writes the .scl files to import
context.md   project, PLCs, tag tables, exported blocks - what Claude reads first
bridge.log   what the last apply did
```

Point Claude Code at that folder, let it work, then use the second menu item.

## Why there is no model call in here

This code runs **inside the TIA Portal process**. An unhandled exception or a hung HTTP
request takes the IDE down with it and loses unsaved work. Doing network I/O and JSON
parsing in there to save one Alt-Tab is a bad trade. The workspace folder is the
interface, and it has the useful side effect of being inspectable and diffable - you see
exactly what will be imported before it is.

It also keeps the safety posture identical to the CLI driver: the Add-In imports and
compiles, and never downloads, goes online or forces a value. See
[../../docs/07-safety-and-gates.md](../../docs/07-safety-and-gates.md).

## Build

**Use the Siemens NuGet package** rather than the checked-in `.csproj` if you can:

1. Create a C# class library targeting .NET Framework 4.8.
2. Install `Siemens.Collaboration.Net.TiaPortal.AddIn.Build` (the `21.*` line for V21).
3. Copy `AddInProvider.cs` and `ClaudeBridgeAddIn.cs` into it, and build.

The package references the Add-In assembly from your installation, converts
`*.dll` → `*.addin`, deploys it into the TIA Portal `AddIns` folder (needs elevation), and
sets up the **TIA Add-In Tester** so you can debug without installing anything.

Otherwise build the project here, pointing at your install:

```bat
msbuild TiaGen.AddIn.csproj /p:Configuration=Release ^
  /p:TiaOpennessDir="C:\Program Files\Siemens\Automation\Portal V21\PublicAPI\V21\net48"
```

then convert and deploy by hand with `Siemens.Engineering.AddIn.Publisher.exe` from the
TIA installation directory, copy the `.addin` into the installation's `AddIns` folder, and
**activate it in the Add-Ins task card** - Add-Ins are deactivated by default.

## Status: unverified

This has **not been compiled or loaded into TIA Portal** - the environment it was written
in has no TIA installation. The Openness calls follow the V21 manual; the Add-In API shape
(`ContextMenuAddIn`, `ContextMenuAddInRoot`, `MenuSelectionProvider<T>`, `MenuStatus`,
`ProjectTreeAddInProvider`) is written from documentation.

**The fastest way to correct it:** install the NuGet package above into an empty class
library and build. It generates template provider and context-menu classes that are
correct for your version by construction. Diff them against these two files and fix any
signature that differs.

Also worth knowing: Add-Ins built against V17-V20 do **not** run on V21 - the old
libraries are not shipped with it, so anything you find online may need the same
treatment. Full detail in [../../docs/09-add-ins.md](../../docs/09-add-ins.md).
