using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Siemens.Engineering;
using Siemens.Engineering.HW;
using Siemens.Engineering.SW;
using Siemens.Engineering.SW.Blocks;
using Siemens.Engineering.SW.Tags;
using Siemens.Engineering.SW.Types;

namespace TiaGen.Openness
{
    /// <summary>
    /// Exports an existing project's software so it can be diffed, version
    /// controlled, or used as a schema template.
    ///
    /// Three formats, for three jobs:
    ///   xml       SimaticML, one file per object. Use one of these as the
    ///             ground-truth template for anything you hand-generate - it
    ///             carries the exact schema version your TIA build expects.
    ///   documents SIMATIC Source Documents (.s7dcl + .s7res), the text format
    ///             added in V20. This is the one to commit to git.
    ///   scl       Plain .scl for code blocks, the format this toolchain imports.
    ///
    /// The document API is called through reflection so the same binary still runs
    /// against a pre-V20 assembly, where it simply reports the format as absent.
    /// </summary>
    internal static class Exporter
    {
        [Flags]
        public enum Formats
        {
            None = 0,
            Xml = 1,
            Documents = 2,
            Scl = 4,
            All = Xml | Documents | Scl,
        }

        public static int Run(Project project, string outputDirectory, Formats formats)
        {
            var root = Directory.CreateDirectory(outputDirectory);
            var failures = 0;

            foreach (var device in project.Devices)
            {
                var software = HardwareBuilder.FindSoftware<PlcSoftware>(device);
                if (software == null) continue;

                Log.Stage($"exporting {device.Name}");
                var deviceRoot = root.CreateSubdirectory(Sanitise(device.Name));

                if (formats.HasFlag(Formats.Xml))
                {
                    failures += ExportXml(software, deviceRoot.CreateSubdirectory("xml"));
                }
                if (formats.HasFlag(Formats.Scl))
                {
                    failures += ExportScl(software, deviceRoot.CreateSubdirectory("scl"));
                }
                if (formats.HasFlag(Formats.Documents))
                {
                    failures += ExportDocuments(software, deviceRoot.CreateSubdirectory("documents"));
                }
            }

            if (failures == 0) Log.Ok("export complete: " + root.FullName);
            else Log.Warn($"export finished with {failures} problem(s); see above");
            return failures == 0 ? 0 : 1;
        }

        private static int ExportXml(PlcSoftware software, DirectoryInfo target)
        {
            var failures = 0;
            var blocks = Blocks(software.BlockGroup).ToList();
            var types = Types(software.TypeGroup).ToList();
            var tables = TagTables(software).ToList();

            foreach (var block in blocks)
            {
                var file = new FileInfo(Path.Combine(target.FullName, Sanitise(block.Name) + ".xml"));
                if (!Log.Try($"exporting block {block.Name} to XML",
                             () => block.Export(file, ExportOptions.WithDefaults)))
                {
                    failures++;
                }
            }
            foreach (var type in types)
            {
                var file = new FileInfo(Path.Combine(target.FullName, Sanitise(type.Name) + ".xml"));
                if (!Log.Try($"exporting type {type.Name} to XML",
                             () => type.Export(file, ExportOptions.WithDefaults)))
                {
                    failures++;
                }
            }
            foreach (var table in tables)
            {
                var file = new FileInfo(Path.Combine(target.FullName,
                                                    "TagTable_" + Sanitise(table.Name) + ".xml"));
                if (!Log.Try($"exporting tag table {table.Name} to XML",
                             () => table.Export(file, ExportOptions.WithDefaults)))
                {
                    failures++;
                }
            }

            Log.Ok($"XML: {blocks.Count} blocks, {types.Count} types, {tables.Count} tag tables " +
                   "-> " + target.FullName);
            return failures;
        }

        private static int ExportScl(PlcSoftware software, DirectoryInfo target)
        {
            // Only code blocks have an SCL representation; DBs and types do not.
            var exported = 0;
            var failures = 0;
            foreach (var block in Blocks(software.BlockGroup))
            {
                var file = new FileInfo(Path.Combine(target.FullName, Sanitise(block.Name) + ".scl"));
                var method = block.GetType().GetMethod(
                    "ExportToFile", new[] { typeof(FileInfo) });
                if (method == null)
                {
                    Log.Skip("this assembly has no PlcBlock.ExportToFile; skipping SCL export");
                    return failures;
                }
                try
                {
                    method.Invoke(block, new object[] { file });
                    exported++;
                }
                catch (TargetInvocationException ex)
                {
                    // Data blocks and non-textual languages refuse this - expected.
                    Log.Detail($"{block.Name}: no SCL representation ({ex.InnerException?.Message})");
                }
                catch (Exception ex)
                {
                    Log.Warn($"exporting {block.Name} as SCL failed: {ex.Message}");
                    failures++;
                }
            }
            Log.Ok($"SCL: {exported} block(s) -> {target.FullName}");
            return failures;
        }

        private static int ExportDocuments(PlcSoftware software, DirectoryInfo target)
        {
            // V20+ only: .s7dcl (code) plus .s7res (comments and translations).
            var exported = 0;
            var failures = 0;
            var unsupported = false;

            foreach (var block in Blocks(software.BlockGroup))
            {
                var method = block.GetType().GetMethod(
                    "ExportAsDocuments", new[] { typeof(DirectoryInfo), typeof(string) });
                if (method == null)
                {
                    unsupported = true;
                    break;
                }
                try
                {
                    method.Invoke(block, new object[] { target, Sanitise(block.Name) });
                    exported++;
                }
                catch (Exception ex)
                {
                    var inner = (ex as TargetInvocationException)?.InnerException ?? ex;
                    Log.Warn($"exporting {block.Name} as a source document failed: {inner.Message}");
                    failures++;
                }
            }

            if (unsupported)
            {
                Log.Skip("PlcBlock.ExportAsDocuments is not in this Openness assembly. " +
                         "SIMATIC Source Documents (.s7dcl/.s7res) need TIA Portal V20 or newer.");
                return failures;
            }

            Log.Ok($"documents: {exported} block(s) -> {target.FullName}");
            return failures;
        }

        private static string Sanitise(string name)
        {
            return string.Join("_", name.Split(Path.GetInvalidFileNameChars()));
        }

        private static IEnumerable<PlcBlock> Blocks(PlcBlockGroup group)
        {
            foreach (var block in group.Blocks) yield return block;
            foreach (var sub in group.Groups)
            {
                foreach (var block in Blocks(sub)) yield return block;
            }
        }

        private static IEnumerable<PlcType> Types(PlcTypeGroup group)
        {
            foreach (var type in group.Types) yield return type;
            foreach (var sub in group.Groups)
            {
                foreach (var type in Types(sub)) yield return type;
            }
        }

        private static IEnumerable<PlcTagTable> TagTables(PlcSoftware software)
        {
            return TagTables(software.TagTableGroup);
        }

        private static IEnumerable<PlcTagTable> TagTables(PlcTagTableGroup group)
        {
            foreach (var table in group.TagTables) yield return table;
            foreach (var sub in group.Groups)
            {
                foreach (var table in TagTables(sub)) yield return table;
            }
        }
    }
}
