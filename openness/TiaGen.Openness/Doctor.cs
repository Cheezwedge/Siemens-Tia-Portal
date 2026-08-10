using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Principal;

namespace TiaGen.Openness
{
    /// <summary>
    /// Environment checks. Run this first on a new engineering PC: it catches the things
    /// that account for nearly every "Openness does not work" report - group membership,
    /// the Openness firewall, and (since V21) looking for the assemblies in the wrong
    /// place.
    /// </summary>
    internal static class Doctor
    {
        public const string OpennessGroup = "Siemens TIA Openness";

        public static int Run(string assemblyDirectory)
        {
            var failures = 0;

            Log.Stage("Process");
            Log.Info($"64-bit process:      {Environment.Is64BitProcess}");
            Log.Info($"64-bit OS:           {Environment.Is64BitOperatingSystem}");
            Log.Info($"CLR version:         {Environment.Version}");
            Log.Info($"User:                {WindowsIdentity.GetCurrent().Name}");
            if (!Environment.Is64BitOperatingSystem)
            {
                Log.Error("TIA Portal requires a 64-bit Windows installation.");
                failures++;
            }
            // Bitness of the CLIENT does not have to match TIA Portal: Openness talks to
            // it out of process over .NET Remoting, and the manual lists 32-bit, 64-bit
            // and AnyCPU clients as supported.
            Log.Skip("client bitness is not required to match TIA Portal (out-of-process API)");

            Log.Stage("Openness user group");
            var membership = CheckGroupMembership();
            if (membership == true)
            {
                Log.Ok($"the current user is a member of \"{OpennessGroup}\"");
            }
            else if (membership == false)
            {
                Log.Error($"the current user is NOT a member of \"{OpennessGroup}\". TIA Portal " +
                          "throws EngineeringSecurityException on connect. Add the user (as an " +
                          "administrator):\n" +
                          $"      net localgroup \"{OpennessGroup}\" \"%USERDOMAIN%\\%USERNAME%\" /add\n" +
                          "      then sign out and back in - group membership is baked into the " +
                          "logon token.");
                failures++;
            }
            else
            {
                Log.Warn("could not determine group membership; check it by hand with " +
                         $"'net localgroup \"{OpennessGroup}\"'");
            }

            Log.Stage("Openness firewall (AllowList)");
            var allowed = OpennessResolver.IsCurrentProcessAllowListed();
            if (allowed == true)
            {
                Log.Ok("this executable appears on the Openness AllowList");
            }
            else if (allowed == false)
            {
                Log.Info("this executable is not on the AllowList yet. That is expected before " +
                         "the first run: TIA Portal raises a confirmation dialog on the first " +
                         "connection from a new executable, and accepting it adds the entry.");
                Log.Info("A human has to accept that dialog. Do not try to automate past it.");
            }
            else
            {
                Log.Skip("could not read the AllowList");
            }

            Log.Stage("Openness assemblies");
            if (!string.IsNullOrWhiteSpace(assemblyDirectory))
            {
                Log.Info("using explicit directory: " + assemblyDirectory);
                failures += ReportDirectory(assemblyDirectory);
            }
            else
            {
                List<OpennessResolver.Installation> installations = null;
                try
                {
                    installations = OpennessResolver.DiscoverAll(21);
                }
                catch (Exception ex)
                {
                    Log.Error("reading the Openness registry keys failed: " + ex.Message);
                    failures++;
                }

                if (installations == null || installations.Count == 0)
                {
                    Log.Error(
                        @"no Openness installation registered under HKLM\SOFTWARE\Siemens\Automation\Openness. " +
                        "From V21, Openness is an inherent feature of TIA Portal rather than an " +
                        "optional setup component, so this normally means TIA Portal is not " +
                        "installed here. On V20 and earlier it could also mean the 'TIA Portal " +
                        "Openness' option was not selected during setup. You can also pass " +
                        "--assembly-dir explicitly.");
                    failures++;
                }
                else
                {
                    foreach (var installation in installations)
                    {
                        Log.Info($"{installation}  ->  {installation.Directory}");
                    }
                    var best = installations[0];
                    Log.Ok($"selected {best}");
                    failures += ReportDirectory(best.Directory);
                }
            }

            Log.Stage("Result");
            if (failures == 0) Log.Ok("environment looks ready for Openness");
            else Log.Error($"{failures} problem(s) must be fixed before Openness will work");
            return failures == 0 ? 0 : 1;
        }

        /// <summary>
        /// Lists the modular assemblies present. Which ones exist depends on the TIA
        /// products installed - Step7 for PLC work, Hmi for panels, and so on.
        /// </summary>
        private static int ReportDirectory(string directory)
        {
            if (!Directory.Exists(directory))
            {
                Log.Error("directory does not exist: " + directory);
                return 1;
            }

            var assemblies = Directory.GetFiles(directory, "Siemens.Engineering*.dll")
                                      .Select(Path.GetFileNameWithoutExtension)
                                      .OrderBy(name => name)
                                      .ToList();
            if (assemblies.Count == 0)
            {
                Log.Error("no Siemens.Engineering* assemblies in " + directory +
                          ". On V21 they live in a target-framework subfolder such as " +
                          @"...\PublicAPI\V21\net48 - check you are not pointing at the parent.");
                return 1;
            }

            foreach (var name in assemblies) Log.Ok(name);

            var failures = 0;
            var hasBase = assemblies.Contains("Siemens.Engineering.Base");
            var hasLegacy = assemblies.Contains("Siemens.Engineering");
            if (!hasBase && !hasLegacy)
            {
                Log.Error("neither Siemens.Engineering.Base (V21+) nor Siemens.Engineering " +
                          "(V20 and earlier) is present; nothing can be resolved.");
                failures++;
            }
            if (hasBase && !assemblies.Contains("Siemens.Engineering.Step7"))
            {
                Log.Warn("Siemens.Engineering.Step7 is missing - PLC software access needs it. " +
                         "Install STEP 7 Professional (or Basic) on this machine.");
            }
            if (hasBase && !assemblies.Contains("Siemens.Engineering.Hmi"))
            {
                Log.Skip("Siemens.Engineering.Hmi is absent - the HMI stages will be unavailable");
            }
            return failures;
        }

        /// <summary>
        /// True / false / null when the answer cannot be determined (for example on a
        /// domain-joined machine where SID translation fails).
        /// </summary>
        public static bool? CheckGroupMembership()
        {
            try
            {
                var identity = WindowsIdentity.GetCurrent();
                if (identity?.Groups == null) return null;

                var translated = 0;
                foreach (var reference in identity.Groups)
                {
                    string name;
                    try
                    {
                        name = reference.Translate(typeof(NTAccount)).Value;
                    }
                    catch (Exception)
                    {
                        continue;   // orphaned or unresolvable SID
                    }
                    translated++;

                    // The name arrives as MACHINE\Siemens TIA Openness.
                    var leaf = name.Contains("\\") ? name.Substring(name.IndexOf('\\') + 1) : name;
                    if (leaf.Equals(OpennessGroup, StringComparison.OrdinalIgnoreCase)) return true;
                }
                return translated > 0 ? (bool?)false : null;
            }
            catch (Exception)
            {
                return null;
            }
        }
    }
}
