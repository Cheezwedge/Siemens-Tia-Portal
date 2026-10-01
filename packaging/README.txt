TiaGen - generate TIA Portal V21 projects from a machine spec
==============================================================

Everything runs from this folder. Nothing is installed, nothing is added to PATH,
and the Python inside python\ is private to this folder.


FIRST: UNBLOCK THE ZIP
    Before extracting, right-click the downloaded zip -> Properties -> tick
    "Unblock" -> OK. Files extracted from a blocked zip carry a "downloaded from
    the internet" mark, which makes Windows warn on every run and some corporate
    policies refuse to run them at all.

    Extract somewhere you can write, e.g. C:\Tools\TiaGen or Documents\TiaGen.


OPENING A COMMAND PROMPT HERE
    In Explorer, open the TiaGen folder, click the address bar, type  cmd  and
    press Enter. A Command Prompt opens already in the right folder.


STEP 1 - CHECK THE INSTALLATION
        tiagen selftest

    It checks the package, builds an example machine, lints it, looks for TIA
    Portal and the Openness permissions, and writes one zip file. Attach that zip
    to a message when asking for help - it has everything needed, and no user or
    machine names.

    WARN lines are normal on a first run. FAIL lines are the ones to fix.


STEP 2 - GENERATE A MACHINE
        tiagen validate spec\examples\step-sequence.yaml
        tiagen build    spec\examples\step-sequence.yaml -o out\step-sequence

    Read out\step-sequence\report.md - the I/O list and block list on one page.


BUILDING THE DRIVER (once per PC, needs TIA Portal V21 installed)
    The driver is what talks to TIA Portal. It has to be compiled on a PC with
    TIA Portal, because the Siemens libraries it uses are not redistributable.

    1. Install the .NET SDK from Microsoft (free; no Visual Studio needed).
    2. In a Command Prompt in this folder:

        dotnet build openness\TiaGen.Openness\TiaGen.Openness.csproj -c Release -o bin

    3. Run  tiagen selftest  again. It will now run the driver's own checks and
       include them in the zip.

    If the build fails, send the whole output - it is usually one renamed
    attribute between TIA versions, and the error names it.


ONE-TIME PERMISSIONS (ask IT if you cannot do these yourself)
    - Your Windows user must be in the local group "Siemens TIA Openness".
      Sign out and back in after being added. selftest checks this.
    - The first time the driver connects, TIA Portal asks whether to allow it.
      That prompt is deliberate - accept it once.


WHAT THIS NEVER DOES
    It never downloads to a PLC, goes online, or forces values. Those stay
    manual, by design.
