# The machine workbook

The whole machine spec as an Excel file. Engineers already describe machines in
spreadsheets - an IO list, a device list, a step table - so the spreadsheet *is* the
spec, rather than a source somebody retypes into YAML.

```bat
tiagen excel mymachine.xlsx                               rem an empty template
tiagen excel mymachine.xlsx --from spec\conveyor.yaml     rem an existing spec, as a workbook
tiagen validate mymachine.xlsx
tiagen build    mymachine.xlsx -o out\mymachine
tiagen from-excel mymachine.xlsx                          rem YAML, for version control
```

The installed package ships `templates\machine-template.xlsx` and a filled-in example.

## Sheets

| Sheet | Holds |
|---|---|
| **Machine** | one setting per row: project, PLC, HMI, network, address ranges, modes, safety notes, sequence name |
| **Equipment** | the IO list, one device per row: type, description, area, feedback switches, timeouts, scaling and limits |
| **IO devices** | PROFINET IO devices - remote racks, valve terminals |
| **Modules** | modules, each under the PLC or under an IO device (the *Parent* column) |
| **Sequence** | the step table - see [docs/11](11-step-sequences.md) |
| **Help** | every equipment type, wait-for status and action verb - **generated from the code**, so it cannot drift from it |

Drop-down lists are on the columns with fixed answers: equipment type, control role,
the yes/no feedback switches, on-timeout behaviour.

## What it forgives

Real spreadsheets are messy, and a tool that rejects them gets bypassed.

- **Headings** are matched ignoring case, spaces and punctuation: `feedback TIMEOUT (ms)`
  works. A heading it does not know is reported, not silently ignored.
- **Lists** in a cell can be separated by `;`, `,` or put one per line with Alt+Enter.
- **Yes/no** accepts yes, no, x, true, false, 1, 0. Blank means the default.
- **A plain number** in a timeout is seconds.
- **Blank rows** and **padding spaces** are ignored. An empty Sequence sheet means no
  sequence.
- **Storage formats**: shared or inline strings, rich text, booleans, sparse rows, and
  both the standard and *Strict* OOXML namespaces.

## What it reports

Every problem at once, in the order you would walk the workbook to fix them:

```
5 problem(s) in the workbook:
  Equipment row 1: column 'Colour' is not one this tool reads, so it is ignored.
  Equipment row 2 (Conv): 'motr_dol' is not an equipment type. Did you mean motor_dol?
  Equipment row 3 (Conv): name already used on Equipment row 2
  Equipment row 4 (Feedback timeout (ms)): must be a whole number, got 'fast'
  Modules row 2: parent 'Nowhere' is neither the PLC nor a row on the IO devices sheet
```

An old `.xls` is recognised and the message says to save it as `.xlsx`.

## How it is verified

The reader and writer are both this repository's code, and two halves agreeing proves
nothing about the file format. So:

- **Round trip.** Every example spec is written as a workbook and built from it; the
  output is compared **byte for byte** with building from the YAML. Any lost setting
  shows up as a difference in generated code.
- **An independent reader.** CI opens every generated workbook with `openpyxl`, with its
  warnings treated as failures (`tools/check_workbook_independently.py`). It is a CI
  tool only - never a dependency of tiagen.
- **Files from other software.** Two fixtures in `generator/tests/fixtures/` were written
  by a different implementation: one with inline strings, one converted to the
  shared-string storage Excel itself uses, confirmed identical by the independent
  reader.
- **On Windows.** The packaged tool validates and builds the example workbook with the
  bundled Python, in CI.

What none of that replaces: opening a generated template in **real Excel** and seeing no
"we found a problem with some content" prompt. That needs Excel, so it is a check for
the engineering PC.

## Not in the workbook yet

Three things stay YAML-only: an explicit HMI screen list (`hmi.screens` - without one,
screens are derived from the equipment), equipment options that have no column
(`runtime_hours`), and the top-level generator `options` section. Everything the
example specs use is covered - the byte-for-byte round trip is the proof.
