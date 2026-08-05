using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Siemens.Engineering.HW;
using Siemens.Engineering.SW;
using Siemens.Engineering.SW.Blocks;
using Siemens.Engineering.SW.ExternalSources;
using Siemens.Engineering.SW.Tags;

namespace TiaGen.Openness
{
    /// <summary>
    /// PLC software: tag tables, then the SCL sources, then the instance DB, then
    /// the OB that calls it. That order is the whole point of the plan - a block
    /// can only reference something that already exists.
    /// </summary>
    internal class SoftwareBuilder
    {
        private readonly Plan _plan;
        private readonly PlcSoftware _software;

        public SoftwareBuilder(Plan plan, PlcSoftware software)
        {
            _plan = plan;
            _software = software;
        }

        public static PlcSoftware Resolve(Device plcDevice)
        {
            var software = HardwareBuilder.FindSoftware<PlcSoftware>(plcDevice);
            if (software == null)
                throw new OpennessStepException(
                    $"no PLC software found on device '{plcDevice.Name}'. The device was created " +
                    "but is not a controller - check the order number in the spec.");
            return software;
        }

        // ------------------------------------------------------------------
        // Tag tables
        // ------------------------------------------------------------------
        public void CreateTagTables()
        {
            if (_plan.Software.TagTables.Count == 0)
            {
                Log.Skip("no tag tables in the plan");
                return;
            }

            foreach (var tableInfo in _plan.Software.TagTables)
            {
                PlcTagTable table = _software.TagTableGroup.TagTables
                    .FirstOrDefault(t => string.Equals(t.Name, tableInfo.Name,
                                                       StringComparison.OrdinalIgnoreCase));
                if (table == null)
                {
                    Log.Try($"creating tag table '{tableInfo.Name}'",
                            () => table = _software.TagTableGroup.TagTables.Create(tableInfo.Name),
                            fatal: true);
                }
                else
                {
                    Log.Skip($"tag table '{tableInfo.Name}' already exists");
                }

                var created = 0;
                var reused = 0;
                foreach (var tagInfo in tableInfo.Tags)
                {
                    var existing = table.Tags.FirstOrDefault(
                        t => string.Equals(t.Name, tagInfo.Name, StringComparison.OrdinalIgnoreCase));
                    if (existing != null)
                    {
                        reused++;
                        SetComment(existing, tagInfo.Comment);
                        continue;
                    }

                    PlcTag tag = null;
                    if (Log.Try($"creating tag {tagInfo.Name} {tagInfo.DataType} at {tagInfo.Address}",
                                () => tag = table.Tags.Create(tagInfo.Name, tagInfo.DataType, tagInfo.Address)))
                    {
                        created++;
                        SetComment(tag, tagInfo.Comment);
                    }
                }
                Log.Ok($"'{tableInfo.Name}': {created} tags created, {reused} already present");
            }
        }

        private static void SetComment(PlcTag tag, string comment)
        {
            if (string.IsNullOrEmpty(comment)) return;
            // Comment is multilingual; write the same text into every configured language.
            Log.Try($"commenting {tag.Name}", () =>
            {
                foreach (var item in tag.Comment.Items) item.Text = comment;
            });
        }

        // ------------------------------------------------------------------
        // Blocks that must go before the import
        // ------------------------------------------------------------------
        public void DeleteBlocks()
        {
            foreach (var name in _plan.Software.DeleteBeforeImport)
            {
                var block = FindBlock(name);
                if (block == null)
                {
                    Log.Skip($"no block '{name}' to delete");
                    continue;
                }
                // The default Main [OB1] is replaced by the generated SCL OB. Deleting
                // it first makes the outcome deterministic instead of depending on how
                // the import resolves a name clash.
                if (Log.Try($"deleting existing block '{name}'", () => block.Delete()))
                {
                    Log.Ok($"deleted '{name}' so the generated version can take its place");
                }
            }
        }

        // ------------------------------------------------------------------
        // External SCL sources
        // ------------------------------------------------------------------
        /// <summary>
        /// Imports each SCL file as an external source and generates the blocks from
        /// it. Sources are processed in plan order, so types exist before the blocks
        /// that declare them.
        /// </summary>
        public bool ImportSources(IEnumerable<string> relativePaths)
        {
            var ok = true;
            foreach (var relative in relativePaths)
            {
                var path = _plan.Resolve(relative);
                if (path == null || !File.Exists(path))
                {
                    Log.Error($"source file listed in the plan is missing: {relative}");
                    ok = false;
                    continue;
                }

                var sourceName = Path.GetFileNameWithoutExtension(path);

                // A stale source with the same name would silently regenerate old code.
                var existing = _software.ExternalSourceGroup.ExternalSources
                    .FirstOrDefault(s => string.Equals(s.Name, sourceName,
                                                       StringComparison.OrdinalIgnoreCase));
                if (existing != null)
                {
                    Log.Try($"removing previous source '{sourceName}'", () => existing.Delete());
                }

                PlcExternalSource source = null;
                if (!Log.Try($"adding source '{sourceName}'",
                             () => source = _software.ExternalSourceGroup.ExternalSources
                                                     .CreateFromFile(sourceName, path)))
                {
                    ok = false;
                    continue;
                }

                if (Log.Try($"generating blocks from '{sourceName}'",
                            () => source.GenerateBlocksFromSource()))
                {
                    Log.Ok($"{sourceName} -> blocks generated");
                }
                else
                {
                    Log.Error(
                        $"'{sourceName}' did not compile as an external source. Open " +
                        "External source files in the project tree, double-click it and read the " +
                        "error - the SCL text is at " + path);
                    ok = false;
                }
            }
            return ok;
        }

        // ------------------------------------------------------------------
        // Instance DBs
        // ------------------------------------------------------------------
        public void CreateInstanceDbs()
        {
            foreach (var info in _plan.Software.InstanceDbs)
            {
                if (FindBlock(info.Name) != null)
                {
                    Log.Skip($"instance DB '{info.Name}' already exists");
                    continue;
                }
                if (FindBlock(info.FromFb) == null)
                {
                    Log.Error($"cannot create '{info.Name}': the FB '{info.FromFb}' was not generated. " +
                              "Fix the source import errors above first.");
                    continue;
                }

                if (Log.Try($"creating instance DB '{info.Name}' of '{info.FromFb}'",
                            () => _software.BlockGroup.Blocks
                                           .CreateInstanceDB(info.Name, true, 1, info.FromFb)))
                {
                    Log.Ok($"instance DB '{info.Name}' created");
                }
                else
                {
                    Log.Error($"could not create instance DB '{info.Name}'. Create it by hand " +
                              $"(right-click Program blocks > Add new block > Data block > " +
                              $"Type = {info.FromFb}) and re-run with --skip create_instance_dbs.");
                }
            }
        }

        // ------------------------------------------------------------------
        // The OB, imported last
        // ------------------------------------------------------------------
        public void ImportOb()
        {
            var relative = _plan.Software.ObSource;
            if (string.IsNullOrEmpty(relative))
            {
                Log.Skip("the plan has no OB source");
                return;
            }

            if (ImportSources(new[] { relative })) return;

            var db = _plan.Software.InstanceDbs.FirstOrDefault()?.Name ?? "the machine instance DB";
            Log.Warn(
                "the generated OB could not be imported. This is the one step with a trivial " +
                "manual fallback: open Main [OB1] in the project and add the single line\n" +
                $"          \"{db}\"();\n" +
                "      That is the whole cyclic program - everything else is already generated.");
        }

        // ------------------------------------------------------------------
        // Reporting
        // ------------------------------------------------------------------
        public void ReportBlocks()
        {
            var present = AllBlocks(_software.BlockGroup).Select(b => b.Name).ToList();
            var types = AllTypes().ToList();
            var known = new HashSet<string>(present.Concat(types), StringComparer.OrdinalIgnoreCase);

            Log.Info($"{present.Count} program blocks and {types.Count} PLC data types in the project");
            var missing = _plan.ExpectedBlocks.Where(b => !known.Contains(b)).ToList();
            if (missing.Count == 0)
            {
                Log.Ok("every block the plan expects is present");
            }
            else
            {
                Log.Error("missing from the project: " + string.Join(", ", missing));
            }
        }

        private PlcBlock FindBlock(string name)
        {
            return AllBlocks(_software.BlockGroup)
                .FirstOrDefault(b => string.Equals(b.Name, name, StringComparison.OrdinalIgnoreCase));
        }

        private static IEnumerable<PlcBlock> AllBlocks(PlcBlockGroup group)
        {
            foreach (var block in group.Blocks) yield return block;
            foreach (var sub in group.Groups)
            {
                foreach (var block in AllBlocks(sub)) yield return block;
            }
        }

        private IEnumerable<string> AllTypes()
        {
            var names = new List<string>();
            Log.Try("enumerating PLC data types", () =>
            {
                names.AddRange(AllTypes(_software.TypeGroup));
            });
            return names;
        }

        private static IEnumerable<string> AllTypes(Siemens.Engineering.SW.Types.PlcTypeGroup group)
        {
            foreach (var type in group.Types) yield return type.Name;
            foreach (var sub in group.Groups)
            {
                foreach (var name in AllTypes(sub)) yield return name;
            }
        }
    }
}
