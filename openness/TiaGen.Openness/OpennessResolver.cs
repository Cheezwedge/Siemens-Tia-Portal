using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using Microsoft.Win32;

namespace TiaGen.Openness
{
    /// <summary>
    /// Finds the Openness assemblies at runtime.
    ///
    /// V21 redesigned this completely, so read carefully before changing anything:
    ///
    /// 1. Openness is now MODULAR. There is no single Siemens.Engineering.dll. The base
    ///    library is Siemens.Engineering.Base.dll, and each installed TIA product adds
    ///    its own assembly (Siemens.Engineering.Step7.dll for PLC software,
    ///    Siemens.Engineering.Hmi.dll for HMI, and so on).
    ///
    /// 2. They live under a target-framework subfolder:
    ///       ...\Portal V21\PublicAPI\V21\net48\
    ///
    /// 3. The registry publishes ONLY the base assembly path. Per the V21 manual:
    ///    "Regardless which TIA Portal products are installed, the Windows registry only
    ///    provides the installation path to the base assembly Siemens.Engineering.Base."
    ///    So we resolve Base from the registry and then load its siblings from the same
    ///    directory.
    ///
    /// 4. The handler is registered in this class's static constructor, and Program's
    ///    static constructor calls Touch() before anything else. Registering "early in
    ///    Main" is not early enough if Main's own body references an Openness type.
    ///
    /// Registry layout (V21):
    ///   HKLM\SOFTWARE\Siemens\Automation\Openness
    ///     \AllowList                                  (version independent, V21+)
    ///     \&lt;xx.xx&gt;                                    PortalVersion
    ///       \PublicAPI
    ///         \&lt;xx.xx.xx.xx&gt;                          EngineeringVersion
    ///           \&lt;netxxx&gt;                             AssemblyVersion, PublicKeyToken,
    ///                                                 Siemens.Engineering.Base = path
    ///
    /// Pre-V21 installations have no target-framework subkey and publish one value per
    /// assembly, so both layouts are handled.
    /// </summary>
    internal static class OpennessResolver
    {
        private const string RegistryRoot = @"SOFTWARE\Siemens\Automation\Openness";
        private const string BaseAssembly = "Siemens.Engineering.Base";

        /// <summary>Preferred target framework subkey, most specific first.</summary>
        private static readonly string[] TargetFrameworks = { "net48", "net472", "net462" };

        private static string _directory;
        private static string _portalVersion;
        private static string _engineeringVersion;
        private static string _targetFramework;
        private static string _explicitDirectory;
        private static int? _preferredMajor = 21;

        static OpennessResolver()
        {
            // Registered before any Openness type can be resolved by the JIT.
            AppDomain.CurrentDomain.AssemblyResolve += Resolve;
        }

        /// <summary>
        /// Forces the static constructor to run. Call this from the entry point's static
        /// constructor, before any method that mentions an Openness type is entered.
        /// </summary>
        public static void Touch()
        {
        }

        /// <param name="explicitDirectory">
        /// Overrides discovery (--assembly-dir, or TIA_OPENNESS_DLL_DIR). Point it at the
        /// folder holding Siemens.Engineering.Base.dll - on V21 that is
        /// ...\PublicAPI\V21\net48, not ...\PublicAPI\V21.
        /// </param>
        public static void Configure(string explicitDirectory = null, int? preferredMajor = 21)
        {
            _explicitDirectory = explicitDirectory
                                 ?? Environment.GetEnvironmentVariable("TIA_OPENNESS_DLL_DIR");
            _preferredMajor = preferredMajor;

            if (!string.IsNullOrWhiteSpace(_explicitDirectory))
            {
                if (!Directory.Exists(_explicitDirectory))
                    throw new OpennessStepException(
                        "assembly directory does not exist: " + _explicitDirectory);
                _directory = _explicitDirectory;
            }
            else
            {
                var found = Discover(preferredMajor);
                if (found == null)
                {
                    throw new OpennessStepException(
                        "no TIA Portal Openness installation found under HKLM\\" + RegistryRoot +
                        ". From V21 Openness is an inherent part of TIA Portal, so this normally " +
                        "means TIA Portal is not installed on this machine. Pass --assembly-dir " +
                        "pointing at the folder containing Siemens.Engineering.Base.dll if the " +
                        "registry is unavailable.");
                }
                _directory = found.Directory;
                _portalVersion = found.PortalVersion;
                _engineeringVersion = found.EngineeringVersion;
                _targetFramework = found.TargetFramework;
            }

            var basePath = Path.Combine(_directory, BaseAssembly + ".dll");
            if (!File.Exists(basePath))
            {
                throw new OpennessStepException(
                    $"{BaseAssembly}.dll not found in {_directory}. On V21 the assemblies live " +
                    "in a target-framework subfolder, e.g. " +
                    @"...\Portal V21\PublicAPI\V21\net48.");
            }
        }

        public static string Directory_ => _directory;
        public static string PortalVersion => _portalVersion;
        public static string EngineeringVersion => _engineeringVersion;
        public static string TargetFramework => _targetFramework;

        /// <summary>Openness assemblies present in the resolved directory.</summary>
        public static IEnumerable<string> AvailableAssemblies()
        {
            if (string.IsNullOrEmpty(_directory) || !System.IO.Directory.Exists(_directory))
                return Enumerable.Empty<string>();
            return System.IO.Directory
                .GetFiles(_directory, "Siemens.Engineering*.dll")
                .Select(Path.GetFileNameWithoutExtension)
                .OrderBy(name => name);
        }

        private static Assembly Resolve(object sender, ResolveEventArgs args)
        {
            var requested = new AssemblyName(args.Name);

            // Only answer for Openness, and never for a file we do not have: returning
            // null lets the CLR continue with its normal probing.
            if (!requested.Name.StartsWith("Siemens.Engineering", StringComparison.Ordinal))
                return null;
            if (string.IsNullOrEmpty(_directory))
                return null;

            var path = Path.Combine(_directory, requested.Name + ".dll");
            if (!File.Exists(path))
            {
                Log.Detail($"resolve {requested.Name}: not present in {_directory}");
                return null;
            }

            var loaded = Assembly.LoadFrom(path);

            // The manual is explicit that a version mismatch must be treated as fatal:
            // silently loading a different build produces failures far from the cause.
            if (requested.FullName != loaded.GetName().FullName)
            {
                throw new FileNotFoundException(
                    "TIA Portal Openness version does not match. Requested " +
                    requested.FullName + " but " + path + " is " + loaded.GetName().FullName +
                    ". Rebuild against the installed version - V21 is not compatible with " +
                    "assemblies from V17-V20.",
                    path);
            }

            Log.Detail($"resolved {requested.Name} -> {path}");
            return loaded;
        }

        // ------------------------------------------------------------------
        // Registry discovery
        // ------------------------------------------------------------------
        internal class Installation
        {
            public string PortalVersion, EngineeringVersion, TargetFramework, Directory, BasePath;
            public override string ToString() =>
                $"{PortalVersion ?? "?"} (Openness {EngineeringVersion ?? "?"}" +
                (TargetFramework == null ? "" : ", " + TargetFramework) + ")";
        }

        /// <summary>All Openness installations the registry knows about, best first.</summary>
        public static List<Installation> DiscoverAll(int? preferredMajor = 21)
        {
            var results = new List<Installation>();

            using (var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine,
                                                      RegistryView.Registry64))
            using (var root = hklm.OpenSubKey(RegistryRoot))
            {
                if (root == null) return results;

                foreach (var portalVersion in root.GetSubKeyNames())
                {
                    // "AllowList" is a sibling of the version keys, not a version.
                    if (portalVersion.Equals("AllowList", StringComparison.OrdinalIgnoreCase))
                        continue;

                    using (var versionKey = root.OpenSubKey(portalVersion))
                    using (var publicApi = versionKey?.OpenSubKey("PublicAPI"))
                    {
                        if (publicApi == null) continue;
                        var display = versionKey.GetValue("PortalVersion") as string
                                      ?? portalVersion;

                        foreach (var engineeringVersion in publicApi.GetSubKeyNames())
                        {
                            using (var apiKey = publicApi.OpenSubKey(engineeringVersion))
                            {
                                if (apiKey == null) continue;
                                var engineeringDisplay =
                                    apiKey.GetValue("EngineeringVersion") as string
                                    ?? engineeringVersion;

                                // V21+: one subkey per target framework.
                                var handled = false;
                                foreach (var tfm in apiKey.GetSubKeyNames())
                                {
                                    using (var tfmKey = apiKey.OpenSubKey(tfm))
                                    {
                                        var basePath = tfmKey?.GetValue(BaseAssembly) as string;
                                        if (string.IsNullOrEmpty(basePath)) continue;
                                        results.Add(new Installation
                                        {
                                            PortalVersion = display,
                                            EngineeringVersion = engineeringDisplay,
                                            TargetFramework = tfm,
                                            BasePath = basePath,
                                            Directory = Path.GetDirectoryName(basePath),
                                        });
                                        handled = true;
                                    }
                                }
                                if (handled) continue;

                                // Pre-V21: one value per assembly, directly on this key.
                                foreach (var valueName in apiKey.GetValueNames())
                                {
                                    var simpleName = valueName.Split(',')[0].Trim();
                                    if (!simpleName.StartsWith("Siemens.Engineering",
                                                               StringComparison.OrdinalIgnoreCase))
                                        continue;
                                    var path = apiKey.GetValue(valueName) as string;
                                    if (string.IsNullOrEmpty(path)) continue;
                                    results.Add(new Installation
                                    {
                                        PortalVersion = display,
                                        EngineeringVersion = engineeringDisplay,
                                        TargetFramework = null,
                                        BasePath = path,
                                        Directory = Path.GetDirectoryName(path),
                                    });
                                    break;
                                }
                            }
                        }
                    }
                }
            }

            return results
                .Where(i => !string.IsNullOrEmpty(i.Directory) && Directory.Exists(i.Directory))
                .OrderByDescending(i => Score(i, preferredMajor))
                .ToList();
        }

        private static Installation Discover(int? preferredMajor) =>
            DiscoverAll(preferredMajor).FirstOrDefault();

        private static double Score(Installation installation, int? preferredMajor)
        {
            var score = ParseVersion(installation.EngineeringVersion);

            // A requested major version outranks everything else.
            if (preferredMajor.HasValue && (int)ParseVersion(installation.PortalVersion) == preferredMajor.Value)
                score += 100000;

            // Prefer the target framework this binary is built for.
            var index = Array.IndexOf(TargetFrameworks, installation.TargetFramework ?? "");
            if (index >= 0) score += (TargetFrameworks.Length - index) * 100;

            return score;
        }

        private static double ParseVersion(string text)
        {
            if (string.IsNullOrEmpty(text)) return 0;
            var digits = new string(text.Where(c => char.IsDigit(c) || c == '.').ToArray());
            var parts = digits.Split(new[] { '.' }, StringSplitOptions.RemoveEmptyEntries);
            if (parts.Length == 0) return 0;
            double.TryParse(parts[0], out var major);
            return major;
        }

        // ------------------------------------------------------------------
        // The Openness firewall (AllowList)
        // ------------------------------------------------------------------
        /// <summary>
        /// TIA Portal only accepts connections from executables on its AllowList. The
        /// first connection from a new executable raises a confirmation dialog, and
        /// accepting it adds the entry. From V21 the list is version independent.
        ///
        /// Returns true when this process's executable appears to be listed, false when
        /// it is not, null when the list cannot be read.
        /// </summary>
        public static bool? IsCurrentProcessAllowListed()
        {
            try
            {
                var exe = Process.GetCurrentProcess().MainModule?.FileName;
                if (string.IsNullOrEmpty(exe)) return null;
                var name = Path.GetFileName(exe);

                foreach (var key in AllowListKeys())
                {
                    using (key)
                    {
                        if (key == null) continue;
                        foreach (var valueName in key.GetValueNames())
                        {
                            if (valueName.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0)
                                return true;
                        }
                        foreach (var subKeyName in key.GetSubKeyNames())
                        {
                            if (subKeyName.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0)
                                return true;
                        }
                    }
                }
                return false;
            }
            catch (Exception)
            {
                return null;
            }
        }

        private static IEnumerable<RegistryKey> AllowListKeys()
        {
            var hklm = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64);

            // V21: version independent.
            yield return hklm.OpenSubKey(RegistryRoot + @"\AllowList");

            // Earlier versions kept a per-version list.
            using (var root = hklm.OpenSubKey(RegistryRoot))
            {
                if (root == null) yield break;
                foreach (var version in root.GetSubKeyNames())
                {
                    if (version.Equals("AllowList", StringComparison.OrdinalIgnoreCase)) continue;
                    yield return hklm.OpenSubKey($"{RegistryRoot}\\{version}\\AllowList");
                }
            }
        }
    }
}
