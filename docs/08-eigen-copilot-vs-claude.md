# Siemens' AI for TIA Portal, and what Claude can do instead

Short answer: **yes, you can do most of what the Siemens agent does** - and the reason
you can is that **Eigen is itself built on TIA Portal Openness**, the same API this
repository uses. Its own manual says so: installing it requires the Openness component
and membership of the `Siemens TIA Openness` Windows group.

That single fact sets the ceiling and the floor of this comparison. Nothing Eigen does
to a project is beyond Openness. What you are buying is the model, the orchestration,
the licensed knowledge, and the support contract - not privileged access.

Facts below are from the **Eigen Engineering Agent manual, version 01.03.02, 07/2026**
unless a source is given.

---

## 1. Naming, because it has changed three times

| When | Name | What it was |
|---|---|---|
| 2023-2024 | **Siemens Industrial Copilot for Engineering** | The original announcement. Chat plus SCL generation. |
| 2024-2025 | **TIA Portal Copilot** / **Engineering Copilot** | In-product feature for V19/V20. Generated SCL, explained code, guided WinCC Unified screens. |
| April 2026 (GA) | **Eigen Engineering Agent** ("EEA") | The current product. Agentic: plans, executes against the live project, validates its own output, iterates until ready for review. |

If you have read about "TIA Portal Copilot", that is the V19/V20 generation. **Eigen is
what you would buy today**, and it covers V19 Update 5+, V20 Update 4+, and V21.

---

## 2. How it actually works

This matters more than the feature list, and it is not what I assumed before reading
the manual.

**Eigen is a separate Windows desktop application**, not a panel inside the TIA Portal
editor. You install it from its own `Install-EigenEngineeringAgent.exe` (300 MB disk,
1 GB RAM), it runs alongside TIA Portal, and you attach it to a project by picking from
a dropdown of **currently open** TIA Portal projects.

```
┌─────────────────┐   Openness    ┌──────────────┐   HTTPS    ┌──────────────┐
│  Eigen desktop  │ ────────────► │  TIA Portal  │            │ Siemens cloud│
│      app        │               │ (open proj.) │            │ orchestrator │
│                 │ ──────────────────────────────────────────►│  + LLM       │
└─────────────────┘                                            └──────────────┘
```

Consequences worth knowing:

- **The project must be open in TIA Portal.** Same constraint as any Openness client.
- **The project locks to the conversation** after the first prompt. To work on another
  project you start a new conversation.
- **There is no live sync.** "Real-time synchronization with every change in TIA Portal
  does not occur." If you edit by hand, you press the sync button or the agent reasons
  about a stale project. This is the same staleness problem any external tool has.
- **Every action needs your permission.** The first time it wants to use a given tool
  in a conversation it asks, and you grant once or always. Granted permissions are
  listed in settings and revocable. It is a per-capability gate model, and a good one.
- **Two explicit modes**: planning mode (`/` to enter, `/exit` to leave) and execution.
  ECAD work has its own entry point, `/ecad`.
- **`@` autocomplete** pulls device, block, tag and screen names from the attached
  project - captured on first use of `@`, refreshed manually.
- **Login is browser-based (Auth0) and lasts at most 12 hours.**

### Data handling

Better than I expected, and worth stating precisely:

- Prompts and conversation history are **stored only locally** on your machine.
- Your code, project files, inputs and outputs are **not used to train models** and not
  shared with third parties.
- For EU customers, processing and storage are in the **EU region**.
- Uploaded documents go to `*.s3.amazonaws.com`; the orchestrator is
  `*.prod.ecp.siemens.io` (`*.eea.siemens.com.cn` in China); auth is
  `siemens-00001.eu.auth0.com`.
- No ports need permanent reservation - the client dropped its HTTP port requirement in
  1.2.0.

Your project content does still leave the building to reach the model. That is the
constraint, not model training.

---

## 3. What Eigen does, from its own manual

**Hardware configuration**
- add, rename, delete devices and modules
- configure SINAMICS drives (G220, G200 Basic, S200, S210 - FW V6.x)
- add motors and encoders to drives; set drive parameters
- read and write hardware, module and **channel** parameters and attributes (1.3.2)
- list compatible sub-modules for a parent module (1.3.2)
- list devices connected to a PROFINET IO system (1.3.1)
- add, delete, read, modify SINAMICS drive telegrams (1.3.1)
- connect a PLC technology object to a drive telegram, incl. multi-axis (1.3.2)

**PLC programming**
- generate and import **LAD and SCL**
- create and manage PLC tags, tag tables and **user constants**
- work with UDTs and data blocks
- access **software units and namespaces**
- clone blocks, tag tables, software units and PLC data types **from one PLC to
  another** (1.3.1)
- cross-reference a PLC object - all dependencies, optionally as **mermaid** (1.2.0)

**HMI**
- create WinCC Unified HMI devices and screens
- design screen layouts and **add screen items** (buttons, I/O fields, graphics)
- write WinCC Unified **JavaScript** for property and event dynamization
- **instantiate existing Unified faceplates in screens**, configure their interfaces and
  link them to variables (1.3.0)

**Testing** - generate executable test cases for SCL *and* LAD blocks.

**Project management** - create and manage projects; translate project texts (filterable
by scope: PLC, HMI, alarms, tags…); mass adaptations across many blocks.

**Standards** - enforce an uploaded style guide; generate against the **Siemens
Automation Framework V2.2** (Units / Equipment Modules / Control Modules), max 6
elements per request.

**ECAD → hardware configuration** - upload `.aml` / `.xml`, prompt with `/ecad`, and it
recognises topology, machine structure and engineering intent; detects inconsistencies
and resolves or flags them; adds devices; configures connections; generates PLC tags
grounded in the real topology. It produces a **plan you approve, modify or ignore**
before anything is written.

**Document grounding** - upload `.docx`, `.xlsx`, `.csv`, `.txt`, `.pdf`, `.html`,
`.md`, `.xml`, `.aml`, labelled `Style Guide` or `Parts List`, and assign them to
projects. 10 GB per user, 250 MB per file.

### Two corrections to what I wrote before reading this

1. **I said neither tool really automates HMI screen content. That was wrong for
   Eigen.** It adds screen items, writes dynamization JavaScript, and instantiates
   faceplates with their interfaces bound. The Openness manual's object list confirms
   most screen objects are creatable through the API - so this is a gap in *my*
   implementation, not in the API. [05-hmi-and-screens.md](05-hmi-and-screens.md) has
   been corrected, including the objects that genuinely are excluded (alarm and trend
   controls).
2. **I said Eigen lives inside TIA Portal's UI. It does not** - it is a side-by-side
   desktop app talking Openness. So the "placement" advantage is smaller than I
   claimed, and a TIA Add-In is actually *more* embedded than Eigen is.

### The limits it states about itself

- "We can **not** guarantee complete syntactical and semantical correctness of the
  AI-generated SCL and LAD code." Review everything.
- Long conversations degrade answer quality - start new ones.
- **Scripted or automated use is prohibited.** Sustained "automated, scripted, or
  bot-driven usage" may be throttled or suspended. So Eigen cannot be a CI step. This
  is a hard architectural boundary, not a soft limit.
- 3 users may share a seat, one active at a time.

---

## 4. Eigen's changelog is a capability map of Openness

This is the most useful thing in the manual for this repository. Every item Eigen ships
is something Openness demonstrably supports on V19-V21. That converts a lot of my
"probably possible" into "confirmed possible":

| Confirmed reachable via Openness | Used here? |
|---|---|
| Hardware / module / **channel** parameter read *and write* | partially - `HardwareBuilder` sets node attributes only |
| Listing compatible sub-modules for a parent module | no - would make module MLFB guessing unnecessary |
| PROFINET device number; devices on an IO system | partially |
| SINAMICS drive telegrams; motors; encoders; drive parameters | no |
| PLC technology objects, incl. connecting to a drive telegram | no |
| **User constants** | no |
| **Software units and namespaces** | no |
| Cloning blocks / tag tables / types **between PLCs** | no |
| **Cross-references** for a PLC object | no |
| Project text translation, filtered by scope | no |
| Unified **screen items** and faceplate instantiation with interface binding | no - emitted as a plan instead |

"Listing compatible sub-modules for a parent module" is the standout. If Openness can
enumerate what legitimately plugs into a given CPU, then the placeholder-MLFB problem
in this repo is solvable properly: query the catalog and let the user pick, instead of
asking them to transcribe an order number. That is now the highest-value thing I could
add to the driver.

---

## 5. Capability comparison

"Claude" means Claude Code plus this repository plus Openness.

| Capability | Eigen | Claude + Openness | Notes |
|---|---|---|---|
| Generate SCL | ✅ | ✅ | Claude's edge: structure comes from a reviewed library, not per-prompt output |
| Generate LAD/FBD | ✅ | ⚠️ feasible, not built | V21's SIMATIC SD text format covers LAD, Safety-LAD, FBD and mixed blocks, so this no longer means hand-writing `FlgNet` XML |
| Explain existing code | ✅ | ✅ | Export as documents/SCL, then ask. Claude reads the whole project at once. |
| Fix compile errors | ✅ | ✅ | `apply` returns the flattened compiler tree |
| Generate test cases | ✅ SCL + LAD | ✅ SCL | |
| Cross-references / dependency graphs | ✅ | ❌ not built | Confirmed possible |
| Devices, modules, network | ✅ | ✅ | |
| Drives, motors, telegrams, technology objects | ✅ | ❌ not built | Confirmed possible |
| Tags from real topology | ✅ from ECAD | ✅ from spec | Different inputs, same result |
| **ECAD ingestion (AML/XML)** | ✅ | ❌ **not built** | The gap worth closing - §6 |
| HMI tags and connections | ✅ | ✅ | |
| **HMI screen content, faceplates, JS** | ✅ | ❌ plan only | Genuine Eigen win |
| Translate project texts | ✅ | ❌ not built | Confirmed possible |
| Search Siemens manuals | ✅ licensed | ⚠️ web only | Genuine Eigen win |
| Style-guide enforcement from a document | ✅ | ✅ | Claude reads `CLAUDE.md`; same idea |
| Standards framework | ✅ Automation Framework V2.2 | ✅ this library | Different standard, same mechanism |
| Approve-before-apply | ✅ plan mode + per-tool permission | ✅ plan.json + dry-run | Both do this well |
| Live project awareness | ✅ with manual sync | ⚠️ export-based | Eigen is more convenient |
| **Deterministic, repeatable output** | ❌ generative | ✅ | The core engineering difference |
| **Usable in CI / unattended** | ❌ **prohibited** | ✅ | Hard boundary in their usage policy |
| Version control friendly | ⚠️ | ✅ | Spec + `.s7dcl` in git, diff per change |
| Works beyond TIA Portal | ❌ | ✅ | |
| Offline / air-gapped | ❌ cloud only | ⚠️ generator yes, model no | |
| Cost | subscription per seat, 1-month trial | existing Claude plan | |
| Vendor support | ✅ | ❌ | Weigh honestly |

---

## 6. Where each genuinely wins

**Eigen is better at:**

- **HMI screens.** It builds them - items, layout, dynamization JS, faceplate
  instances. This toolchain emits a plan and asks you to place it.
- **LAD.** Most of the installed base is ladder. Generating LAD via SimaticML is a
  maintenance liability I would not take on.
- **Drives and technology objects.** SINAMICS parameters, telegrams, motors, encoders,
  multi-axis. Nothing here touches any of it.
- **Licensed documentation.** Natural-language search over the real Siemens manual
  corpus. Nobody else can legally reproduce that.
- **Breadth per unit of effort.** It ships all of the above today. This repo covers
  eleven equipment types.
- **Convenience.** Attach a project, type a sentence, approve the tool call.

**Claude plus this toolchain is better at:**

- **Determinism.** Eigen is generative: ask twice, get two answers, and its own manual
  will not guarantee syntactic correctness. Here the *spec* is the artefact and
  generation is a pure function of it. Change one line, regenerate, diff the report.
  That is what makes output reviewable by someone who did not watch it being made, and
  auditable a year later.
- **Review at the right altitude.** Nobody meaningfully reviews 800 lines of generated
  SCL. Everybody can review 40 lines of YAML.
- **Standards that are enforced, not requested.** Eigen follows a style guide you
  upload. Here the standard *is* the library: every motor gets the same fault codes and
  feedback supervision because they all call the same reviewed FB. That property does
  not degrade under deadline or across prompts.
- **CI and unattended use.** Explicitly out of bounds for Eigen. This is the difference
  between an assistant and a build step.
- **Data control.** The generator never leaves your machine.
- **Not being locked to TIA Portal.** Same assistant, whole workflow.
- **Cost.**

---

## 7. Closing the real gaps

### Gap 1 - ECAD ingestion (worth doing)

Openness does CAx exchange in **AutomationML** (`*.aml`), and from V19 the results come
back through the API rather than only a log file; on import devices are reconnected to
the same subnet. Eigen's `/ecad` flow is the same underlying capability plus a model to
interpret intent.

The version that fits this repo: `tiagen import-cax <file>.aml` producing a **machine
spec** for you to review as YAML, not a project. Same input as Eigen, determinism
preserved, and the review step is a diff instead of a plan you skim once.

### Gap 2 - catalog queries (worth doing, and cheap)

Eigen 1.3.2 lists compatible sub-modules for a parent module. Add that to the driver as
`TiaGen.Openness.exe catalog --for PLC_1` and the `REPLACE-WITH-CATALOG-MLFB`
placeholder stops being necessary - the tool can offer the real options for the CPU in
the spec. This is the single best return on effort available right now.

### Gap 3 - HMI screen content (reconsider)

Eigen proves the Unified screen API is more writable than I assumed. Worth a spike:
create one screen, add one button, bind one tag through the API. If that works,
`screens.json` becomes executable rather than advisory.

### Gap 4 - placement

Build an **Add-In** and you are *more* embedded than Eigen: your items appear in TIA
Portal's own context menus on whatever is selected. `openness/TiaGen.AddIn/` is a
skeleton that does this - see [09-add-ins.md](09-add-ins.md).

### Gap 5 - LAD, drives, translation

**LAD is no longer the closed door I called it.** V21's SIMATIC SD text format covers
LAD, Safety-LAD, FBD and mixed-language blocks, so ladder generation means emitting
`.s7dcl` rather than hand-writing `FlgNet` SimaticML. Still a project, but a tractable
one - and worth it if ladder is your house standard.

Translation is easy and worth adding when you need it. Drives are a large, genuinely
specialised surface; if you run SINAMICS, that remains a real argument for Eigen.

---

## 8. The three ways to wire Claude to TIA Portal

**1. Chat-side.** Export (`export --formats documents`), point Claude at it, ask, paste
back. Zero setup, good for explaining and debugging, tedious when repeated.

**2. Repo-side - what this repo is.** Spec in git, Claude edits the spec, the generator
produces artefacts, the driver applies them. Deterministic, reviewable, CI-able. Best
for anything you will do more than once.

**3. In-TIA Add-In.** Claude reached from TIA Portal's own menus. Best ergonomics, most
work, and the code runs inside TIA's process - be conservative in there.

Third-party products also bridge AI assistants to TIA Portal over MCP - for example
`renanlido/tia-openness` with its MCP server and Claude Code plugin, and T-IA Connect's
commercial offering. If you would rather buy the plumbing than maintain it, they exist.

---

## 9. What I would actually do

Assuming your V21 site with an S7-1200 G2 and no hard data-sovereignty rule:

1. **Take the one-month trial.** It costs nothing, and the manual's own sample prompts
   ("Explain the entire project in Spanish", "Fix the compile error in code block
   Sequence and explain what caused it") are the fastest way to judge whether the
   conversational loop suits you. Point it at a real legacy project - comprehension is
   where it looks strongest.
2. **Keep the spec-driven flow for anything repeated**, and for anything that has to
   run unattended - Eigen is contractually not allowed to be that.
3. **Add catalog queries to the driver next.** Smallest change, removes the one
   placeholder in this repo, and Eigen proves the API supports it.
4. **Then CAx/AML import**, if your electrical design is already in AML.
5. **Build the Add-In when the round trip annoys you** - not before you know which two
   menu items you want.
6. **If project data cannot leave the site**, Eigen is out entirely and this flow is
   your option - with the caveat that Claude is also an API call, so decide
   deliberately what context you send.

They are not really competitors. Eigen is a better pair of hands for interactive work
and covers far more of TIA's surface; a spec-driven generator is a better factory and
the only one of the two that can be a build step. A shop doing both repeat machine
builds and one-off engineering would sensibly run both.

---

## Sources

- **Eigen Engineering Agent Manual, 01.03.02, 07/2026** (Siemens, via
  `docs.tia.siemens.cloud`) - supplied by the user; the authority for §2-§4
- [Eigen Engineering Agent product page](https://www.siemens.com/en-us/products/tia-portal/eigen-engineering-agent/) (Siemens)
- [Siemens launches the Eigen Engineering Agent](https://press.siemens.com/global/en/pressrelease/siemens-launches-eigen-engineering-agent-bringing-purpose-built-ai-industrial) (Siemens press, April 2026)
- [Siemens expands Eigen Engineering Agent capabilities](https://www.arcweb.com/blog/siemens-expands-eigen-engineering-agent-capabilities) (ARC Advisory)
- [Siemens adds ECAD and project generation to engineering AI](https://www.engineering.com/siemens-adds-ecad-and-project-generation-to-engineering-ai/) (Engineering.com)
- [Scaling roll-out of generative AI with Siemens Industrial Copilot](https://press.siemens.com/global/en/pressrelease/siemens-xcelerator-scaling-roll-out-generative-ai-siemens-industrial-copilot) (Siemens press - the original Copilot)
- [Import of CAx data](https://docs.tia.siemens.cloud/r/en-us/v20/tia-portal-openness-api-for-automation-of-engineering-workflows/export/import/importing/exporting-hardware-data/import-of-cax-data) (Siemens)
- [Siemens Automation Framework](https://support.industry.siemens.com/cs/ww/en/view/109817223) (Siemens, entry 109817223)
- [Purchase / Digital Exchange](https://dex.siemens.com/industrialsoftware/automation-software) · [seat management](https://industry.siemens.io) · [downloads](https://industry.siemens.io/downloads)
