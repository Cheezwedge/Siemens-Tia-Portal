TransferStation - generated SCL for TIA Portal V21 / S7-1200 G2
==============================================================

Nothing here needs Python, git or a compiler. TIA Portal alone.

WHAT THIS IS FOR
    The first compile test. Nine library blocks, four machine blocks and a
    generated step sequencer have never been through a TIA compiler. This
    finds out whether they are valid.

IMPORT ORDER MATTERS
    A source can only reference what already exists, which is why the files
    are numbered. Import them in numeric order. Out of order gives you
    "unknown type" errors that look like broken code but are a sequencing bug.

STEPS
    1. New project. Add an S7-1200 CPU: 6ES7214-1AH50-0XB0
    2. Project tree -> PLC_1 -> External source files
       -> right-click -> Add new external file
    3. Add these, in this order:

         10_UDT_DevIf.scl
         20_FB_Motor.scl
         21_FB_MotorRev.scl
         22_FB_Valve.scl
         23_FB_Vfd.scl
         24_FB_AnalogIn.scl
         25_FB_AnalogOut.scl
         26_FB_DigitalOut.scl
         27_FB_ModeManager.scl
         30_UDT_TransferStationAuto.scl
         31_UDT_TransferStationCmd.scl
         32_UDT_TransferStationAlarms.scl
         33_UDT_TransferStationCond.scl
         38_FB_TransferStationCycle.scl
         40_FB_TransferStation.scl

    4. Right-click each added source -> Generate blocks from source
    5. Compile the PLC.

    SKIP 60_Main.scl on this pass. It calls an instance DB that does not
    exist yet, so it will fail for a reason that tells us nothing.

WHAT TO SEND BACK
    The compile error list, if there is one. Copy the text out of the
    Inspector window. I will fix the generator, not these files - they are
    regenerated from the spec every build.

TAG TABLE
    plc_tags.csv is the I/O list, for reference. The blocks compile without
    it: they reference tags by name, and the compiler will report the names
    it cannot resolve. Importing it is optional on this pass.

THE INTERESTING FILE
    38_FB_TransferStationCycle.scl is the generated step sequencer - eight
    steps, 1000 to 2000, with per-step timeouts and a blocked-reason word.
    Most likely place for a compile error, and the most useful thing to know
    about.
