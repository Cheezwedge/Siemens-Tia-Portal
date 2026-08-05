using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Principal;

namespace TiaGen.Openness
{
    /// <summary>
    /// Environment checks. Run this first on a new engineering PC: it catches the
    /// three things that account for nearly every "Openness does not work" report -
    /// wrong bitness, missing group membership, and unregistered assemblies.
    /// </summary>
    internal static class Doctor
    {
        public const string OpennessGroup = "Siemens TIA Openness";

        public static int Run(string assemblyDirectory)
        {
            var failures = 0;

            Log.Stage("Process");
            Log.Info($"64-bit process:      {Environment.Is64BitProcess}");
            if (!Environment.Is64BitProcess)
            {
                Log.Error("The Siemens assemblies are x64 only. Build with PlatformTarget=x64.");
                failures++;
            }
            Log.Info($"64-bit OS:           {Environment.Is64BitOperatingSystem}");
            Log.Info($"CLR version:         {Environment.Version}");
            Log.Info($"User:                {WindowsIdentity.GetCurrent().Name}");

            Log.Stage("Openness user group");
            var membership = CheckGroupMembership();
            if (membership == true)
            {
                Log.Ok($"the current user is a member of \"{OpennessGroup}\"");
            }
            else if (membership == false)
            {
                Log.Error($"the current user is NOT a member of \"{OpennessGroup}\". " +
                          "Openness will refuse to connect. Add the user (as an administrator):\n" +
                          $"      net localgroup \"{OpennessGroup}\" \"%USERDOMAIN%\\%USERNAME%\" /add\n" +
                          "      then sign out and back in.");
                failures++;
            }
            else
            {
                Log.Warn("could not determine group membership; check it by hand with " +
                         $"'net localgroup \"{OpennessGroup}\"'");
            }

            Log.Stage("Openness assemblies");
            if (!string.IsNullOrWhiteSpace(assemblyDirectory))
            {
                Log.Info("using explicit directory: " + assemblyDirectory);
                var dll = Path.Combine(assemblyDirectory, "Siemens.Engineering.dll");
                if (File.Exists(dll)) Log.Ok(dll);
                else
                {
                    Log.Error("Siemens.Engineering.dll not found at " + dll);
                    failures++;
                }
            }
            else
            {
                Dictionary<string, string> found = null;
                try
                {
                    found = OpennessResolver.FromRegistry(21);
                }
                catch (Exception ex)
                {
                    Log.Error("reading the Openness registry keys failed: " + ex.Message);
                    failures++;
                }

                if (found == null || found.Count == 0)
                {
                    Log.Error(
                        @"no Openness assemblies registered under HKLM\SOFTWARE\Siemens\Automation\Openness. " +
                        "Install the 'TIA Portal Openness' component from the TIA Portal setup, " +
                        "or pass --assembly-dir pointing at the PublicAPI folder.");
                    failures++;
                }
                else
                {
                    foreach (var entry in found.OrderBy(e => e.Key))
                    {
                        Log.Ok($"{entry.Key} -> {entry.Value}");
                    }
                    if (!found.ContainsKey("Siemens.Engineering.Hmi"))
                    {
                        Log.Skip("Siemens.Engineering.Hmi not registered separately " +
                                 "(fine when it is merged into Siemens.Engineering)");
                    }
                }
            }

            Log.Stage("Result");
            if (failures == 0) Log.Ok("environment looks ready for Openness");
            else Log.Error($"{failures} problem(s) must be fixed before Openness will work");
            return failures == 0 ? 0 : 1;
        }

        /// <summary>
        /// True / false / null when the answer cannot be determined (for example on
        /// a domain-joined machine where SID translation fails).
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
