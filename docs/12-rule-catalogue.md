# Rule catalogue, and what carried over from the Panasonic reviewer

The [LadderLogicReview](https://github.com/Cheezwedge/LadderLogicReview) repo is a static
analyser for Panasonic FPWin Pro 7 ladder, with 12 deterministic rules in `rules.yaml` and
about 22 plain-English rules sent to Claude under `--deep`. This document records what
happened when those rules were pointed at this toolchain.

The short version: **the hazards transfer, the notation does not.** A rule about a
duplicate output coil is about ladder; the bug it prevents - two places driving one output
- exists in any language. So each rule was sorted by what it means here, not by whether it
mentions a rung.

---

## The split that made it useful

The Panasonic tool already draws the line this repo argues for in
[docs/10 §5](10-standards-system.md): mechanical rules run offline and deterministically,
judgement rules are handed to a model. That is the right architecture and it survived the
move unchanged. What differs is *where* a rule can run:

| Layer | Runs on | Analogue in the Panasonic tool |
|---|---|---|
| **Spec validation** (`tiagen validate`) | the YAML, before any code exists | none - it has no spec, only a finished program |
| **Guaranteed by construction** | nothing to check; the generator cannot emit the defect | none |
| **Project lint** (`tiagen lint`, [docs/10 §4](10-standards-system.md)) | exported `.s7dcl` from a real project | this is where every one of its 12 local rules lives |
| **Deep review** | a model reading the program | its `claude_rules` section |

The interesting consequence: **a generator moves rules from "check" to "cannot happen"**.
Six of the twelve local rules are in that category here, which is the strongest form a
standard can take.

---

## Rules now enforced by `tiagen validate`

Eight new checks, all deterministic, all with tests.

| From | Rule here | Severity |
|---|---|---|
| R011 test/bypass bits | equipment names, step names and operator messages containing `bypass`, `debug`, `dummy`, `temp`, `tmp`, `force`, `override`, `todo`, `fixme` | warning |
| "non-descriptive variable names" | equipment named `Flag1`, `tmp`, `x`, `data` and similar - it becomes a tag name, an HMI tag and an alarm text | warning |
| R012 SET without RST | **a request no step ever cancels.** Requests persist, so a cyclic sequence that starts a conveyor and never stops it ends in a different state than it began | warning |
| "rung that can never be TRUE" | a step waiting on both `X` and `not X` | **error** |
| "timer done-bit as sole permissive" | a step with `on_timeout: continue` and no `wait_for` - advancing on time with nothing confirming the actions completed | warning |
| "timer/counter preset of zero" | `timeout: 0` | **error** |
| "duplicate copy-paste comments" | two steps sharing an operator message - a copied step whose message was never updated | warning |
| "excessively complex rungs" | a step waiting on more than six conditions | warning |
| R001/R005 missing comments | equipment with no `description`, so tag comments, HMI tags and alarm text repeat the name | warning |

### The one that found a live bug

`R012` was worth the whole exercise. Ported to a step table it reads: *a request that
nothing cancels*. Run against the example spec written an hour earlier, it said:

```
WARNING sequence: 'Infeed_Start' is requested at step 1100 and nothing in the cycle
        cancels it. Requests persist, so the cycle ends in a different state from the
        one it started in and the second cycle does not repeat the first.
```

That was correct, and chasing it exposed a worse defect underneath. Requests in the
generated sequencer **persist** after their step advances - deliberately, so a clamp closed
at step 1200 is still closed at step 1600 without restating it. But `stop` only asserted
`_Stop`, leaving `_Start` TRUE from the earlier step, so from the second step onwards the
device FB saw **both requests at once**.

Fixed by making opposing verbs cancel each other in the same step:

```scl
1100:
    #Auto.Infeed_Start := TRUE;    // Infeed.start
    #Auto.Infeed_Stop  := FALSE;   // Infeed.start
1200:
    #Auto.Infeed_Stop  := TRUE;    // Infeed.stop
    #Auto.Infeed_Start := FALSE;   // Infeed.stop
```

A rule set written for a different PLC family found a real bug in this one, on its first
run. That is the argument for borrowing rules rather than writing fresh ones.

---

## Rules the generator makes impossible

Not skipped - **structurally unreachable**. Worth listing explicitly, because "we do not
check that" and "that cannot happen" are very different claims to make to an auditor.

| Rule | Why it cannot happen |
|---|---|
| R002 duplicate output coil | every `%Q` is written in exactly one device FB, and the sequence writes requests rather than outputs |
| R003 direct physical address | every address is allocated and named by the generator; the SCL references symbols |
| R007 unintended self-seal | latching lives in the reviewed library blocks, each with a defined reset that refuses to clear a live fault |
| R008 empty rung | nothing generates an empty statement |
| R010 rung with no output | every generated branch either assigns or advances |
| R009 output read as contact undocumented | device status is read through `UDT_DevIf`, never off the output tag |
| "SET commanding motion with no reset on E-stop / re-init / fault" | `IF NOT #AutoRun THEN` clears every request, `Hold := #AnyFault` freezes the sequence, and every device FB drops its output when `Enable` is lost |
| "double word into single word" | datatypes are generated from the equipment type; no move is hand-written |
| "divide by zero" | already an error: `raw_min == raw_max` in analog scaling was caught before this exercise |

---

## Rules for the project lint layer

These need a real project rather than a spec, so they belong to `tiagen lint` over exported
`.s7dcl` - the Layer 2 described in [docs/10 §4](10-standards-system.md). Listed here so
the lint layer starts with a rule set rather than a blank file:

- R001 / R006 - every block and every network has a comment; a timer's setpoint is explained
- R002 / R010 - a coil driven from two places; logic that drives nothing (in *hand-written*
  blocks, where it is possible again)
- R003 - absolute addressing where a symbol exists
- R004 - writes to system memory
- R011 - test and bypass bits, same word list as above
- library-block hash mismatch - not from the Panasonic set, but the same family of check

`rules.yaml`'s structure is worth copying wholesale when that lands: rules as data, with an
id, a severity, an enable flag, and a description that reads as the explanation given to
whoever tripped it. The ids matter more than they look - they let a team say "we waived
R011 on this machine, here is why" instead of arguing about a paragraph.

---

## Panasonic-specific, not ported

Honest about the ones that do not transfer, and why:

| Rule | Why not |
|---|---|
| R004 system registers `DT9000+`, special relays `R900+` | Panasonic address map. The S7 equivalent (clock and system memory bytes) is a CPU property this spec does not yet model - a genuine gap, not a non-issue |
| STRING max-length / current-length header words | an FPWin Pro memory layout. S7 `String` has its own two length bytes, but nothing here writes into a string's storage |
| `F0_MV`, `F32_DIV`, `F31 %` instruction checks | instruction mnemonics with no SCL counterpart |
| K-constant usage | in this toolchain the spec *is* the named place a constant lives |
| Scan-cycle timing dependence | the one place it applies is the sequencer's own step timer, which is generated and documented, not user-written |
| Handshaking between stations; parallel stations stopped by the main sequence | genuine judgement rules. They belong in the deep-review pass, and the second one is a good argument for keeping parallel equipment out of the main step table entirely |

---

## What to do with the `claude_rules` section

Keep it, and keep it plain English - that part of the design is right. Two adjustments for
use here:

1. **Point it at the spec and the step table, not only the SCL.** A rule like "check that
   a parallel station cannot be stopped when the main sequence pauses" is answerable from
   `sequence:` far more reliably than from generated code, because intent is still visible.
2. **Move any rule that becomes mechanical.** Three rules moved out of the deep pass and
   into the validator during this exercise, because a step table made them decidable. That
   is the direction of travel: every rule that graduates from judgement to determinism is
   one that now runs on every build, for free, with the same answer every time.

The rule that must never move the other way is the checker itself. A model is for
explaining a violation and for the rules a regex cannot express - never for deciding
whether a mechanical rule passed.

---

## Running it

```bash
cd generator
python -m tiagen lint --list                    # the catalogue, and what is implemented
python -m tiagen lint ../out/mymachine/scl      # lint generated output (should be silent)
python -m tiagen lint C:\export --min-severity warning
python -m tiagen lint C:\export --format json   # for CI
```

Exit code is 1 when anything at `error` severity is found, so it can gate a pull request.

**The generator's own output must lint clean.** That is a test
(`test_generated_output_is_clean`), not an aspiration: if the generator emits something
the house rules reject, one of the two is wrong and the build says so.

### Formats

| Format | Support |
|---|---|
| `.scl` | **fully parsed.** TIA has exported SCL from any block for years, so lint works today with no driver build |
| `.xml` (SimaticML) | block identity, language and per-network comments, with namespaces read rather than assumed |
| `.s7dcl` (SIMATIC SD) | read as text; block name and language recovered. **Structure not parsed yet** - awaiting a real export |
| `.s7res` | read alongside its block |

A block read as text only is **reported as unchecked**, never as clean - the report says
how many, and rules needing structure skip them. A checker that silently passes what it
could not read is worse than no checker.

### Encoding

TIA writes UTF-8 with a BOM in some places and UTF-16 in others. Both are detected, plus
UTF-16 with no BOM, because the failure mode otherwise is a file that reads as empty and a
run that reports nothing wrong.
