# Working in this repository

This repo turns a YAML machine spec into a TIA Portal V21 project for an S7-1200 G2.
Read [README.md](README.md) for the shape of it and
[docs/01-openness-primer.md](docs/01-openness-primer.md) for how Openness works.

## The one rule

**Edit the spec, not the output.** `spec/*.yaml` is the source of truth. Everything in
`out/` is generated and is overwritten on the next build. If the generated code is
wrong, fix the generator or the spec - never patch the artefact.

The single sanctioned exception is the region between `>>> BEGIN AUTO SEQUENCE >>>`
and `<<< END AUTO SEQUENCE <<<` in the generated machine FB, which is where
machine-specific sequence logic goes.

A spec with a `sequence:` section generates the sequencer instead, and that region
shrinks to whatever a step table cannot express. Sequence work belongs in the spec -
see [docs/11-step-sequences.md](docs/11-step-sequences.md). Never hand-edit
`FB_<Machine><SeqName>`; it is regenerated from the step table every build.

## Reference manuals go in a file, never in the chat

Siemens manuals are enormous. The Openness system manual is ~2.7 MB of text - roughly
660k tokens, several times a context window. Pasting one into the conversation ends the
session; it cannot be summarised out afterwards because it was never usable to begin with.

So: drop the manual in `reference/` (gitignored - it is Siemens' copyright, not ours),
then work it the way you would work a codebase.

```bash
grep -n "OpenWithUpgrade" reference/*.txt     # find the section
# then Read with offset/limit - a few hundred lines, not the file
```

Build a section index once per manual (`reference/manual-index.md`, line numbers against
the text) and grep that instead. Note the edition on the cover: the 11/2023 manual stops
at V19 and knows nothing about the modular `net48` assemblies, so it does not override
what [docs/04](docs/04-s7-1200-g2-and-v21.md) says about V21.

Anything a manual actually settles belongs in `docs/`, in a sentence, with the reasoning.
That is the memory that survives the session - the conversation is not.

## Commands

```bash
cd generator
python -m tiagen types                                  # equipment types and their signals
python -m tiagen validate ../spec/examples/minimal.yaml  # engineering rules
python -m tiagen explain  ../spec/examples/minimal.yaml  # the I/O list
python -m tiagen build    ../spec/examples/minimal.yaml -o ../out/minimal
python -m tiagen import-steps steps.csv -o seq.yaml       # step spreadsheet -> sequence section

cd .. && python3 -m unittest discover -s generator/tests  # 80 tests, keep them green
```

The Openness driver only runs on Windows with TIA Portal V21 installed; it cannot be
built or tested here. Treat its code as carefully reviewed but unexecuted.

## When asked to build a machine

1. Write or edit `spec/<name>.yaml`. Ask about anything that changes the I/O budget:
   CPU order number, which feedbacks are wired, IP addresses, analog alarm limits.
2. Run `validate`, then `build`. Read every warning out loud - they are written to be
   read, not suppressed.
3. Point at `out/<name>/report.md` for the I/O list, and hand over the
   `TiaGen.Openness.exe apply` command.

Never run `--force` to get past a validation error without saying what the error was
and why proceeding is acceptable.

## Never invent hardware identifiers

Openness matches catalog strings character for character. A plausible-but-wrong MLFB
costs an afternoon of debugging.

If an order number or firmware version is not known, put a placeholder that fails
loudly - `REPLACE-WITH-CATALOG-MLFB` - and say the user must copy it from the TIA
hardware catalog. The validator already warns about anything that is not MLFB-shaped.
This applies to module MLFBs for the S7-1200 G2 in particular: they are a new family
and are not shipped with this repo.

## Safety

- `estop` and `safety_gate` are **status inputs for the standard program**. They are
  not a safety function and no amount of code makes them one.
- Never author F-blocks, an F-runtime group or F-I/O configuration.
- Never download, go online, or force a value. Those are human gates - see
  [docs/07-safety-and-gates.md](docs/07-safety-and-gates.md).
- Do not remove or soften the safety warnings the validator emits.

## Extending the generator

Adding an equipment type touches exactly three places:

1. `library/scl/<n>_FB_<Name>.scl` - the device FB. Follow the existing pattern:
   fail-safe on lost `Enable`, latching fault with a code from the table in
   `10_UDT_DevIf.scl`, an `Hmi : "UDT_DevIf"` static, momentary commands cleared at
   the end.
2. `generator/tiagen/devices.py` - a `Signal` list, the FB name, a parameter builder,
   and the entry in `TYPES`. Add the file to `LIBRARY_FILES` in dependency order.
3. `generator/tests/test_tiagen.py` - a test that the signals allocate and the call is
   emitted with the right tags.

Nothing else in the generator is type-aware, so tags, HMI tags, alarms and screens
pick the new type up automatically.

## Style

- SCL: 4-space indent, `#local` and `"Global"` sigils always explicit, section comment
  banners, one blank line between logical sections.
- Python: standard library plus PyYAML only. No new dependencies.
- C#: log what was attempted before it fails - a renamed attribute must be
  identifiable from the log alone.
- Comments explain *why*, not what. The generated code is read by people who will be
  debugging a machine at 2am.
