# handover/ - a snapshot, not a source

`first-compile/` holds generated SCL copied out of `out/step-sequence/`, committed
only so it can be downloaded from GitHub without running the generator.

**It is a snapshot.** The source of truth is `spec/examples/step-sequence.yaml`, and
`python -m tiagen build` regenerates all of it. Do not edit anything in here and do
not treat it as current - if the spec has moved since, this has not.

Delete this folder once the first compile test is done.

See `first-compile/README-IMPORT.txt` for the TIA Portal import steps.
