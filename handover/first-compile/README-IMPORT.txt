TransferStation - generated SCL for TIA Portal V21 / S7-1200 G2
==============================================================

Nothing here needs Python, git or a compiler. TIA Portal alone.

*** IMPORT THE TAGS FIRST. ***
An earlier version of this file said the tag table was optional. It is not.
FB_TransferStation references 21 global tags by name; without them, that one
block fails to generate with 21 "Tag not defined" errors. Everything else
imports cleanly either way.

ORDER
    1. Tag tables          tags\Inputs.xml, Outputs.xml, AnalogOutputs.xml
    2. The SCL, numerically 10_ ... 40_
    3. Instance DB          DB_TransferStation, of FB_TransferStation
    4. Delete Main [OB1]    the default one, so 60_Main can replace it
    5. 60_Main.scl
    6. Compile

STEP 1 - TAGS
    Project tree -> PLC_1 -> PLC tags -> right-click -> Import
    Import each of the three XML files in tags\.
    If the import is refused, open plc_tags.csv in Excel instead and paste the
    Name / DataType / Address columns straight into a PLC tag table grid.
    21 tags: 15 inputs, 5 outputs, 1 analog output.

STEP 2 - THE SCL
    PLC_1 -> External source files -> right-click -> Add new external file
    Add in numeric order, then right-click each -> Generate blocks from source:

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

    A source can only reference what already exists, which is why they are
    numbered. Out of order gives "unknown type" errors that look like broken
    code but are a sequencing bug.

STEP 3 - INSTANCE DB
    Program blocks -> Add new block -> Data block
    Type: instance DB of "FB_TransferStation"
    Name: DB_TransferStation

STEP 4/5 - THE OB
    A fresh project already has Main [OB1]. Delete it, then generate blocks
    from 60_Main.scl. If that import fails, the fallback is trivial: keep the
    original Main [OB1] and put one line in it:

         "DB_TransferStation"();

    That is the entire cyclic program.

STEP 6 - COMPILE
    Compile the PLC. Send back the error list if there is one.

VERIFIED SO FAR (V21, real compiler)
    14 of 15 sources imported with 0 errors and 0 warnings, including
    38_FB_TransferStationCycle.scl - the generated step sequencer.
