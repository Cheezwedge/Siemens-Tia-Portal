using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using Siemens.Engineering;
using Siemens.Engineering.AddIn.Menu;
using Siemens.Engineering.HW;
using Siemens.Engineering.HW.Features;
using Siemens.Engineering.SW;
using Siemens.Engineering.SW.Blocks;
using Siemens.Engineering.SW.ExternalSources;
using Siemens.Engineering.SW.Tags;

namespace TiaGen.AddIn
{
    /// <summary>
    /// A right-click bridge between TIA Portal and Claude Code.
    ///
    ///   Claude ▸ Export for Claude         selection -> workspace folder, then opens it
    ///   Claude ▸ Apply SCL from workspace  workspace inbox -> external sources -> compile
    ///
    /// Deliberately does NOT call a model. This code runs inside the TIA Portal process:
    /// an unhandled exception or a hung HTTP request takes the IDE down and loses unsaved
    /// work. The workspace folder is the interface, which also makes every exchange
    /// inspectable and diffable.
    ///
    /// VERIFY BEFORE TRUSTING: the exact Add-In API shape (base classes, the
    /// ContextMenuAddInRoot / MenuSelectionProvider / MenuStatus types) is written from
    /// the documentation, not compiled. Install the NuGet package
    /// Siemens.Collaboration.Net.TiaPortal.AddIn.Build into an empty class library - it
    /// generates template classes that are correct for your TIA version by construction -
    /// and diff them against this file. See docs/09-add-ins.md.
    /// </summary>
    public class ClaudeBridgeAddIn : ContextMenuAddIn
    {
        private const string MenuName = "Claude";

        private readonly TiaPortal _portal;

        /// <summary>The live portal is handed to us; never construct a TiaPortal here.</summary>
        public ClaudeBridgeAddIn(TiaPortal portal) : base(MenuName)
        {
            _portal = portal;
        }

        protected override void BuildContextMenuItems(ContextMenuAddInRoot root)
        {
            // Registered for IEngineeringObject so the items appear on blocks, PLCs and
            // the project alike; what actually gets exported depends on what is selected.
            root.Items.AddActionItem<IEngineeringObject>(
                "Export for Claude", OnExport, OnStatus);
            root.Items.AddActionItem<IEngineeringObject>(
                "Apply SCL from workspace", OnApply, OnStatus);
        }

        private MenuStatus OnStatus(MenuSelectionProvider<IEngineeringObject> selection)
        {
            return selection.GetSelection().Any() ? MenuStatus.Enabled : MenuStatus.Disabled;
        }

        // ------------------------------------------------------------------
        // Export
        // ------------------------------------------------------------------
        private void OnExport(MenuSelectionProvider<IEngineeringObject> selection)
        {
            Guard(() =>
            {
                var workspace = Workspace.ForProject(_portal.Projects.FirstOrDefault());
                var blocks = new List<PlcBlock>();
                var software = new List<PlcSoftware>();

                foreach (var selected in selection.GetSelection())
                {
                    switch (selected)
                    {
                        case PlcBlock block:
                            blocks.Add(block);
                            break;
                        case Device device:
                            var sw = FindSoftware(device);
                            if (sw != null) software.Add(sw);
                            break;
                    }
                }

                // Selecting a whole PLC means "everything in it".
                foreach (var sw in software)
                {
                    blocks.AddRange(AllBlocks(sw.BlockGroup));
                }

                var exported = 0;
                foreach (var block in blocks.Distinct())
                {
                    // ExportToFile writes SCL for textual blocks; DBs and graphical
                    // blocks refuse it, so fall back to SimaticML for those.
                    var sclPath = Path.Combine(workspace.Export,
                                               Workspace.Safe(block.Name) + ".scl");
                    try
                    {
                        block.ExportToFile(new FileInfo(sclPath));
                        exported++;
                        continue;
                    }
                    catch (Exception)
                    {
                        // not a textual block
                    }

                    try
                    {
                        block.Export(new FileInfo(Path.Combine(
                            workspace.Export, Workspace.Safe(block.Name) + ".xml")),
                            ExportOptions.WithDefaults);
                        exported++;
                    }
                    catch (Exception ex)
                    {
                        workspace.Note($"could not export {block.Name}: {ex.Message}");
                    }
                }

                workspace.WriteContext(_portal, software, blocks.Distinct().ToList());
                workspace.Open();

                Message($"Exported {exported} object(s) to:\n\n{workspace.Root}\n\n" +
                        "Point Claude Code at that folder. It reads context.md and the " +
                        "exported sources, and writes revised .scl files into inbox\\.\n\n" +
                        "Then use 'Apply SCL from workspace'.");
            });
        }

        // ------------------------------------------------------------------
        // Apply
        // ------------------------------------------------------------------
        private void OnApply(MenuSelectionProvider<IEngineeringObject> selection)
        {
            Guard(() =>
            {
                var project = _portal.Projects.FirstOrDefault();
                var workspace = Workspace.ForProject(project);

                var sources = Directory.Exists(workspace.Inbox)
                    ? Directory.GetFiles(workspace.Inbox, "*.scl").OrderBy(f => f).ToArray()
                    : new string[0];

                if (sources.Length == 0)
                {
                    Message($"No .scl files in:\n\n{workspace.Inbox}\n\n" +
                            "Claude Code should write the blocks it wants imported there. " +
                            "Files are imported in filename order, so number them if one " +
                            "depends on another (10_types.scl before 20_blocks.scl).");
                    return;
                }

                // Prefer the PLC the user selected; fall back to the only one present.
                var target = selection.GetSelection().OfType<Device>()
                                      .Select(FindSoftware).FirstOrDefault(s => s != null)
                             ?? project?.Devices.Select(FindSoftware)
                                        .FirstOrDefault(s => s != null);

                if (target == null)
                {
                    Message("No PLC found. Select the PLC (or a block inside it) and retry.");
                    return;
                }

                var log = new StringBuilder();
                var imported = 0;
                foreach (var file in sources)
                {
                    var name = Path.GetFileNameWithoutExtension(file);
                    try
                    {
                        var existing = target.ExternalSourceGroup.ExternalSources
                            .FirstOrDefault(s => string.Equals(s.Name, name,
                                                 StringComparison.OrdinalIgnoreCase));
                        existing?.Delete();

                        var source = target.ExternalSourceGroup.ExternalSources
                                           .CreateFromFile(name, file);
                        source.GenerateBlocksFromSource();
                        log.AppendLine($"ok      {name}");
                        imported++;
                    }
                    catch (Exception ex)
                    {
                        log.AppendLine($"FAILED  {name}: {ex.Message}");
                    }
                }

                // Compile so the engineer sees immediately whether it is sound. This is
                // the whole point: generated code is not trustworthy until it compiles.
                var summary = "not compiled";
                try
                {
                    var result = target.GetService<Siemens.Engineering.Compiler.ICompilable>()
                                       ?.Compile();
                    if (result != null)
                    {
                        summary = $"{result.State}: {result.ErrorCount} error(s), " +
                                  $"{result.WarningCount} warning(s)";
                    }
                }
                catch (Exception ex)
                {
                    summary = "compile failed: " + ex.Message;
                }

                workspace.Note(log.ToString() + Environment.NewLine + "compile: " + summary);
                Message($"Imported {imported} of {sources.Length} source(s).\n\n" +
                        log + "\ncompile: " + summary +
                        "\n\nThis Add-In never downloads to the CPU - that stays a human step.");
            });
        }

        // ------------------------------------------------------------------
        // Helpers
        // ------------------------------------------------------------------
        private static PlcSoftware FindSoftware(Device device)
        {
            foreach (var item in Traverse(device))
            {
                if (item.GetService<SoftwareContainer>()?.Software is PlcSoftware software)
                    return software;
            }
            return null;
        }

        private static IEnumerable<DeviceItem> Traverse(Device device) =>
            device.DeviceItems.SelectMany(Traverse);

        private static IEnumerable<DeviceItem> Traverse(DeviceItem item)
        {
            yield return item;
            foreach (var child in item.DeviceItems)
                foreach (var nested in Traverse(child)) yield return nested;
        }

        private static IEnumerable<PlcBlock> AllBlocks(PlcBlockGroup group) =>
            group.Blocks.Concat(group.Groups.SelectMany(AllBlocks));

        /// <summary>
        /// Nothing may escape into TIA Portal's message loop: an unhandled exception in an
        /// Add-In terminates the IDE.
        /// </summary>
        private static void Guard(Action action)
        {
            try
            {
                action();
            }
            catch (Exception ex)
            {
                Message("The Claude bridge failed:\n\n" + ex.GetType().Name + ": " +
                        ex.Message + "\n\nYour project has not been closed - but check " +
                        "whether a partial import happened before saving.");
            }
        }

        private static void Message(string text)
        {
            System.Windows.Forms.MessageBox.Show(text, MenuName,
                System.Windows.Forms.MessageBoxButtons.OK,
                System.Windows.Forms.MessageBoxIcon.Information);
        }
    }

    /// <summary>Layout of the exchange folder, and the context file Claude reads.</summary>
    internal class Workspace
    {
        public string Root { get; private set; }
        public string Export => Path.Combine(Root, "export");
        public string Inbox => Path.Combine(Root, "inbox");

        public static Workspace ForProject(Project project)
        {
            var name = Safe(project?.Name ?? "project");
            var root = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments),
                "TiaGen", "workspace", name);

            var workspace = new Workspace { Root = root };
            Directory.CreateDirectory(workspace.Export);
            Directory.CreateDirectory(workspace.Inbox);
            return workspace;
        }

        public void Open()
        {
            try { Process.Start("explorer.exe", "\"" + Root + "\""); }
            catch (Exception) { /* opening a window is not worth failing over */ }
        }

        public void Note(string text)
        {
            try
            {
                File.AppendAllText(Path.Combine(Root, "bridge.log"),
                    DateTime.Now.ToString("s") + Environment.NewLine + text + Environment.NewLine);
            }
            catch (Exception) { }
        }

        /// <summary>
        /// context.md: what Claude needs to know before touching anything. Kept short on
        /// purpose - the exported sources carry the detail.
        /// </summary>
        public void WriteContext(TiaPortal portal, IEnumerable<PlcSoftware> software,
                                 IReadOnlyCollection<PlcBlock> blocks)
        {
            var text = new StringBuilder();
            var project = portal.Projects.FirstOrDefault();

            text.AppendLine("# TIA Portal workspace");
            text.AppendLine();
            text.AppendLine($"- project: `{project?.Name}`");
            text.AppendLine($"- exported: {DateTime.Now:yyyy-MM-dd HH:mm}");
            text.AppendLine();
            text.AppendLine("## How to use this folder");
            text.AppendLine();
            text.AppendLine("- `export/` is read-only context: blocks as `.scl`, or `.xml` when a");
            text.AppendLine("  block has no textual form (data blocks, graphical languages).");
            text.AppendLine("- Write blocks you want imported into `inbox/` as `.scl`. They are");
            text.AppendLine("  imported in filename order, so number them when one depends on");
            text.AppendLine("  another: `10_UDT_x.scl` before `20_FB_y.scl`.");
            text.AppendLine("- The engineer then runs *Claude > Apply SCL from workspace* in TIA");
            text.AppendLine("  Portal, which imports and compiles. Nothing is downloaded to a CPU.");
            text.AppendLine();

            foreach (var sw in software)
            {
                text.AppendLine($"## PLC `{sw.Name}`");
                text.AppendLine();
                try
                {
                    var tables = AllTagTables(sw.TagTableGroup).ToList();
                    text.AppendLine($"- {tables.Count} tag table(s)");
                    foreach (var table in tables)
                    {
                        text.AppendLine($"  - `{table.Name}`: " +
                                        string.Join(", ", table.Tags.Take(40)
                                            .Select(t => $"{t.Name}@{t.LogicalAddress}")));
                    }
                }
                catch (Exception ex)
                {
                    text.AppendLine("- tag tables unavailable: " + ex.Message);
                }
                text.AppendLine();
            }

            if (blocks.Count > 0)
            {
                text.AppendLine("## Exported blocks");
                text.AppendLine();
                foreach (var block in blocks)
                {
                    text.AppendLine($"- `{block.Name}`");
                }
                text.AppendLine();
            }

            try
            {
                File.WriteAllText(Path.Combine(Root, "context.md"), text.ToString());
            }
            catch (Exception ex)
            {
                Note("could not write context.md: " + ex.Message);
            }
        }

        private static IEnumerable<PlcTagTable> AllTagTables(PlcTagTableGroup group) =>
            group.TagTables.Concat(group.Groups.SelectMany(AllTagTables));

        public static string Safe(string name) =>
            string.Join("_", (name ?? "unnamed").Split(Path.GetInvalidFileNameChars()));
    }
}
