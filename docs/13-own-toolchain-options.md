# Building your own tool instead of subscribing to Eigen

Eigen Engineering Agent works by driving TIA Portal through Openness from a desktop app,
with a cloud LLM deciding what to do ([docs/08](08-eigen-copilot-vs-claude.md) §2). Nothing
in that architecture is proprietary to Siemens except the model hosting and the polish.
This document re-evaluates what this repository already is, and lays out the ways to turn
it into a tool you own, that installs on a locked-down corporate Windows machine.

---

## 1. Where the project actually stands

Honest status as of this writing, because the options below build on it:

| Piece | State | Evidence |
|---|---|---|
| Generator: spec → SCL, UDTs, tag tables, HMI tags, alarms, screen plan | **working** | 90 tests; CI on every push |
| Step-sequence generator, step table from CSV | **working, compiled** | imported and compiled clean on V21 Upd1, S7-1200 G2 |
| Device library (9 blocks) | **compiled** | same compile, 0 errors 0 warnings |
| Spec validation and project lint (12 of 13 rules) | **working** | tests, and it found real bugs in its own output |
| Openness driver (`apply`, `export`, `doctor`) | **written, never compiled** | the remaining gate - see §6 |
| TIA Add-In | **written, never loaded** | |
| Hardware catalog queries | not built | confirmed possible (Eigen ships it) |
| Global-library instantiation | not built | namespaces confirmed in V21 |
| HMI screen content and faceplates | not built | confirmed possible on Unified (Eigen ships it) |
| Excel (`.xlsx`) input | not built | CSV only today |

The engine - turning a structured description into a compiling project - exists and is
proven. What is missing is mostly **packaging** and **front ends**, plus three Openness
capabilities that are known to work because Eigen uses them.

---

## 2. The architecture every option shares

One engine, several front ends. The engine is deterministic; any AI sits strictly in
front of it.

```
 design spec (PDF/Word)  ─┐
 IO list / device list    ├─►  [ front end ]  ─►  machine spec (YAML)  ─►  [ engine ]  ─►  TIA Portal
 step table (Excel)      ─┘     human, Excel       reviewable, in git       validate          via Openness
                                template, or AI                             build             compile
                                                                            apply             report
```

Why the line sits there:

- **Same spec, same project, every time.** Generated code comes from the reviewed library,
  not from a model's output on the day. That is the one thing Eigen structurally cannot
  offer ([docs/08](08-eigen-copilot-vs-claude.md) §5: "deterministic, repeatable output").
- **The AI becomes swappable.** If the model only has to produce a 60-line YAML file that a
  validator checks, a weaker or local model is viable, and changing provider changes
  nothing downstream.
- **The AI never touches Openness.** It cannot download, go online, or reach a gate in
  [docs/07](07-safety-and-gates.md), because the tool it calls does not have those verbs.

---

## 3. The options

Six distinct ways to deliver it. They are not exclusive - most of them wrap the same engine.

### Option A - Installable command-line engine (foundation)

A single install folder: the Openness driver, the generator, the house library, and Excel
templates. Run from a shortcut or a `.bat`.

- **Input:** Excel templates - IO list, device list, step table.
- **AI:** none. Fully local, nothing leaves the machine.
- **Corporate fit:** best. No Python install, no network, one signed folder.
- **Effort:** low - this is mostly packaging what exists.
- **Limits:** no conversation; a person fills in the templates.

Every other option needs this underneath, so it comes first regardless.

### Option B - Desktop app with a GUI

A small Windows app over Option A: pick the spreadsheets, see validation warnings and the
I/O report, preview what will be created, press "Build in TIA Portal".

- **Corporate fit:** excellent - it is an ordinary signed MSI.
- **Effort:** medium; the engine is done, this is UI.
- **Best for:** engineers who will never open a terminal.

### Option C - TIA Portal Add-In

The same engine reached from TIA's own right-click menus: *Build from spreadsheet*,
*Insert from global library*, *Export for review*. Closest to native ergonomics.

- **Corporate fit:** good, with one install step needing admin (the `.addin` goes into the
  TIA Add-Ins folder) and a per-user activation in TIA.
- **Effort:** medium; a scaffold exists ([docs/09](09-add-ins.md)) but is unverified.
- **Caution:** Add-In code runs **inside** TIA's process. Keep it a thin launcher for the
  engine rather than putting the engine in there.

### Option D - AI assistant over MCP (the Eigen replacement)

A local **MCP server** exposes the engine as tools an AI can call:

| Tool | Does |
|---|---|
| `read_document` / `read_spreadsheet` | pull a design spec or IO list into context |
| `write_spec`, `validate_spec` | draft the machine spec and check it |
| `build` | generate SCL, tags, HMI, plan |
| `apply_to_tia` | run the driver against the open project, after approval |
| `list_global_library`, `use_library_item` | browse and instantiate library content |
| `export_blocks`, `lint`, `compile_report` | read back and review |

The client is **Claude Desktop** or **Claude Code**, both of which run on Windows. The
server can be packaged as a single-file Claude Desktop extension that a user installs by
double-clicking, and approvals are per tool, the same gate model Eigen uses.

This is the option that matches what you are testing in Eigen: hand it a design spec or
spreadsheet, discuss, get a plan, approve, watch it build.

- **Corporate fit:** depends on data policy. Prompts and documents go to the model
  provider. If your company already has a cloud agreement, Claude is available through
  Amazon Bedrock, Google Cloud Vertex AI and Microsoft Foundry, which keeps it inside a
  contract IT already approved. A Claude Team or Enterprise plan is the other route.
- **Effort:** medium on top of Option A. The server is a thin layer that calls the CLI.
- **Advantage over Eigen:** deterministic output, works in CI, version-controlled specs,
  your library rather than Siemens' framework, no per-seat Siemens subscription.
- **What you give up vs Eigen:** licensed Siemens manual search, vendor support, and live
  project awareness (export-based here).

### Option E - Air-gapped AI

Option D with a model running locally instead of in the cloud. This is more realistic than
it sounds *because of §2*: the model only drafts a spec that the validator then checks, so
its mistakes are caught rather than compiled.

- **Corporate fit:** strongest data story - nothing leaves the site.
- **Cost:** a workstation GPU, and noticeably weaker results on messy documents.
- **Use when:** project data is not allowed off site at all.

### Option F - Buy the plumbing

Third-party Openness-to-MCP bridges exist - open-source (`renanlido/tia-openness`) and
commercial (T-IA Connect). Faster to start, less control over what the generated code
looks like. Vet the licence and what the bridge is allowed to do in TIA before installing
it on an engineering PC.

---

## 4. Comparison

| | A CLI | B Desktop | C Add-In | D AI/MCP | E Local AI | F Buy |
|---|---|---|---|---|---|---|
| Specs from Excel templates | ✅ | ✅ | ✅ | ✅ | ✅ | varies |
| Specs from free-form documents | ❌ | ❌ | ❌ | ✅ | ⚠️ weaker | varies |
| Conversation and planning | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| Deterministic output | ✅ | ✅ | ✅ | ✅ | ✅ | varies |
| Data stays on site | ✅ | ✅ | ✅ | ⚠️ policy | ✅ | varies |
| Runs unattended / in CI | ✅ | ❌ | ❌ | ⚠️ | ⚠️ | varies |
| Admin needed beyond one-time setup | ❌ | ❌ | install only | ❌ | ❌ | varies |
| Build effort | low | medium | medium | medium | medium + hardware | low |
| Recurring cost | none | none | none | AI plan | none | licence |

---

## 5. Recommendation

**A, then D, then B or C.**

1. **Option A first**, because every other option is a front end to it, and because it is
   useful on its own the day it ships.
2. **Option D next** - it is the direct Eigen replacement, and it is cheap once A exists
   because the MCP server only calls the CLI.
3. **B or C only if** people who will not use the AI need a button. Pick C if they live in
   TIA Portal, B if IT objects to Add-Ins.
4. **E** if data classification rules out a cloud model; **F** if you would rather not
   maintain any of this.

Keep the Eigen trial running until D does the jobs you are actually using Eigen for. The
comparison that matters is your three most common requests, run through both.

---

## 6. Making it installable on a corporate Windows machine

The friction you have already hit - Python not on PATH, no compiler, no export menu - is
exactly what an end user must never see. Concretely:

### No Python install

Ship the official **Windows embeddable Python** package inside the application folder.
It is a zip from python.org with signed binaries, needs no installer, touches no PATH and
no registry, and runs the generator unchanged with its tests intact.

Alternatives, and why they are second choices:

| Approach | Problem |
|---|---|
| PyInstaller / Nuitka single exe | frequently flagged by corporate antivirus as a packed binary |
| Port the generator to C# | one runtime, one exe - but a rewrite of tested code; worth it only if IT blocks the embedded interpreter |
| Require a Python install | the problem you already hit |

### No compiler on user machines

The driver is built once (now possible with the free .NET SDK, see
[openness/README.md](../openness/README.md)) and shipped compiled. Users get an `.exe`,
not a project file.

### One-time admin steps - the list to hand IT

| Step | Why | Frequency |
|---|---|---|
| Add users to the local group **Siemens TIA Openness** | TIA refuses Openness connections otherwise | once per machine, can be pushed by group policy |
| Allow the install folder in application control (AppLocker / WDAC) | unsigned or unknown executables are commonly blocked | once |
| **Code-sign** the driver and launcher with the company certificate | application control and SmartScreen trust signed binaries | every release |
| Approve the first Openness connection dialog | TIA asks once per new executable - a deliberate human gate | once per version |
| For Option C: copy the `.addin` into the TIA Add-Ins folder | needs elevation | once per version |
| For Option D: allow the model provider's endpoint through the proxy, or use the company's cloud tenant | the AI is a network call | once |

No Siemens licence beyond STEP 7 is needed: from V21, Openness is part of TIA Portal.

### Distribution

An MSI installing to `Program Files`, signed, with the house library and templates
alongside. Updating the standard is then a new MSI version - and the spec records which
version built each machine ([docs/10](10-standards-system.md) §1).

---

## 7. Capability roadmap against what you asked for

| You asked for | Today | Needed | Evidence it is possible |
|---|---|---|---|
| Specs and spreadsheets as input | YAML, CSV step tables | `.xlsx` reader; templates for IO list, device list, step table, HMI page list | ordinary file parsing |
| Hardware configuration | CPU, modules, IO devices, subnet | **catalog queries**, so module order numbers are picked, not typed | Eigen ships it |
| State-machine sequences | **done, compiled on V21** | LAD output, if wanted | V21 source documents |
| Function blocks | 9-block device library | more device types as the standard grows | - |
| Items from a global library | not built | open a `.al21`, instantiate master copies and library types into the project, record the library version | `Library.MasterCopies` / `Library.Types` namespaces present in V21 |
| HMI pages already mapped | tags, alarms, screen *plan* | create Unified screen items and **faceplate instances bound to the device interface** | Eigen ships it on Unified |

Two open questions decide how the HMI part goes, and both are ten-minute checks on your
machine rather than research:

1. Does an MTP700 **Unified Basic** accept faceplate instances? Eigen's capability is
   stated for Unified generally. If Basic panels do not, the generated tiles are built
   from primitives instead - more objects, same binding.
2. The exact method names for library instantiation and screen-item creation on your
   build. The **TIA Portal Openness Explorer** (Siemens entry 109760816) answers this
   against a live project faster than any document.

---

## 8. Order of work

| Phase | Delivers | Gate |
|---|---|---|
| **0** | Driver compiles; `doctor` and `apply` run on the example | **blocks everything below** |
| 1 | Option A: install folder with embedded Python, compiled driver, Excel templates, `.xlsx` input, text-list stage | IT allows the folder |
| 2 | Global library: publish the house library as a `.al21`; spec items can say `from_library:` | method names confirmed |
| 3 | Option D: MCP server and Claude Desktop extension | data policy decided |
| 4 | Mapped HMI screens on Unified, with faceplate instances | faceplate question answered |
| 5 | Option B or C, if a non-AI front end is wanted | demand |

Phase 0 is the same next step as before this document: build the driver with
`dotnet build`, and run `doctor`.
