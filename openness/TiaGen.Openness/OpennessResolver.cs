using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Microsoft.Win32;

namespace TiaGen.Openness
{
    /// <summary>
    /// Finds Siemens.Engineering at runtime.
    ///
    /// The exe is built against one Openness assembly but must load whatever the
    /// local TIA Portal installed. TIA registers the assembly paths under
    ///   HKLM\SOFTWARE\Siemens\Automation\Openness\&lt;version&gt;\PublicAPI\&lt;version&gt;
    /// where each value name is an assembly name (sometimes the simple name,
    /// sometimes the full strong name) and the value data is the DLL path.
    ///
    /// Install this handler before touching any Siemens.Engineering type. Because
    /// the JIT resolves types when a method is first entered, the handler must be
    /// registered in a method that does not itself reference those types - hence
    /// Program.Main installing it before calling into anything else.
    /// </summary>
    internal static class OpennessResolver
    {
        private const string RegistryRoot = @"SOFTWARE\Siemens\Automation\Openness";

        private static readonly string[] WantedAssemblies =
        {
            "Siemens.Engineering",
            "Siemens.Engineering.Hmi",
            "Siemens.Engineering.AddIn",
        };

        private static Dictionary<string, string> _map;
        private static bool _installed;

        /// <param name="explicitDirectory">
        /// Overrides discovery entirely (--assembly-dir, or the TIA_OPENNESS_DLL_DIR
        /// environment variable). Point it at the PublicAPI\V21 folder.
        /// </param>
        /// <param name="preferredMajor">
        /// Major TIA version to prefer when several are installed, e.g. 21.
        /// </param>
        public static void Install(string explicitDirectory = null, int? preferredMajor = 21)
        {
            if (_installed) return;

            _map = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

            var directory = explicitDirectory
                            ?? Environment.GetEnvironmentVariable("TIA_OPENNESS_DLL_DIR");

            if (!string.IsNullOrWhiteSpace(directory))
            {
                if (!Directory.Exists(directory))
                    throw new OpennessStepException("assembly directory does not exist: " + directory);
                foreach (var name in WantedAssemblies)
                {
                    var candidate = Path.Combine(directory, name + ".dll");
                    if (File.Exists(candidate)) _map[name] = candidate;
                }
                if (!_map.ContainsKey("Siemens.Engineering"))
                    throw new OpennessStepException(
                        "Siemens.Engineering.dll not found in " + directory);
            }
            else
            {
                foreach (var entry in FromRegistry(preferredMajor))
                {
                    if (!_map.ContainsKey(entry.Key)) _map[entry.Key] = entry.Value;
                }
            }

            AppDomain.CurrentDomain.AssemblyResolve += Resolve;
            _installed = true;
        }

        public static IReadOnlyDictionary<string, string> Resolved =>
            _map ?? (IReadOnlyDictionary<string, string>)new Dictionary<string, string>();

        private static Assembly Resolve(object sender, ResolveEventArgs args)
        {
            var simpleName = new AssemblyName(args.Name).Name;
            if (_map != null && _map.TryGetValue(simpleName, out var path) && File.Exists(path))
            {
                Log.Detail("resolving " + simpleName + " -> " + path);
                return Assembly.LoadFrom(path);
            }
            return null;
        }

        /// <summary>
        /// Walks the Openness registry keys and returns assembly name -> DLL path.
        /// Newer installations are preferred, with <paramref name="preferredMajor"/>
        /// winning outright when present.
        /// </summary>
        public static Dictionary<string, string> FromRegistry(int? preferredMajor = null)
        {
            var result = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

            using (var root = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64)
                                         .OpenSubKey(RegistryRoot))
            {
                if (root == null) return result;

                var versions = root.GetSubKeyNames()
                    .Select(name => new { Name = name, Sort = SortableVersion(name, preferredMajor) })
                    .OrderByDescending(v => v.Sort)
                    .Select(v => v.Name)
                    .ToList();

                foreach (var version in versions)
                {
                    using (var versionKey = root.OpenSubKey(version))
                    {
                        if (versionKey == null) continue;
                        CollectAssemblyValues(versionKey, result);
                    }
                }
            }
            return result;
        }

        private static void CollectAssemblyValues(RegistryKey key, Dictionary<string, string> into)
        {
            foreach (var valueName in key.GetValueNames())
            {
                var path = key.GetValue(valueName) as string;
                if (string.IsNullOrEmpty(path) || !File.Exists(path)) continue;

                // The value name is either "Siemens.Engineering" or the full strong
                // name; both start with the simple name.
                var simpleName = valueName.Split(',')[0].Trim();
                if (WantedAssemblies.Contains(simpleName, StringComparer.OrdinalIgnoreCase)
                    && !into.ContainsKey(simpleName))
                {
                    into[simpleName] = path;
                }
            }

            foreach (var subKeyName in key.GetSubKeyNames())
            {
                using (var subKey = key.OpenSubKey(subKeyName))
                {
                    if (subKey != null) CollectAssemblyValues(subKey, into);
                }
            }
        }

        /// <summary>
        /// Orders version key names such as "21.0" or "V21". The preferred major
        /// version sorts above everything else.
        /// </summary>
        private static double SortableVersion(string name, int? preferredMajor)
        {
            var digits = new string(name.Where(c => char.IsDigit(c) || c == '.').ToArray());
            double value;
            if (!double.TryParse(digits, System.Globalization.NumberStyles.Any,
                                 System.Globalization.CultureInfo.InvariantCulture, out value))
            {
                value = 0;
            }
            if (preferredMajor.HasValue && (int)value == preferredMajor.Value) value += 1000;
            return value;
        }
    }
}
