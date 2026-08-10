using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.CompilerServices;
using Siemens.Engineering;
using Siemens.Engineering.HW;
using Siemens.Engineering.SW;

namespace TiaGen.Openness
{
    /// <summary>
    /// Executes the verbs. Kept out of Program so that the assembly resolver is
    /// installed before the JIT has to resolve any Siemens.Engineering type.
    /// </summary>
    internal static class Runner
    {
        [MethodImpl(MethodImplOptions.NoInlining)]
        public static int Apply(Program.Options options)
        {
            if (string.IsNullOrEmpty(options.PlanPath))
                throw new OpennessStepException("apply needs --plan <plan.json>");

            var plan = Plan.Load(options.PlanPath);
            foreach (var stage in options.Skip) plan.Skip.Add(stage);
            if (options.NoCompile) plan.Skip.Add("compile");
            if (options.NoSave) plan.Skip.Add("save");

            Describe(plan, options);
            if (options.DryRun)
            {
                Log.Ok("dry run: nothing was changed");
                return 0;
            }

            var state = new BuildState { Plan = plan, Options = options };

            Log.Stage("Connecting to TIA Portal");
            var mode = options.WithUi ? TiaPortalMode.WithUserInterface
                                      : TiaPortalMode.WithoutUserInterface;
            using (var portal = new TiaPortal(mode))
            {
                Log.Ok($"connected ({mode})");
                state.Portal = portal;

                var stages = Stages(state);
                foreach (var name in plan.Pipeline)
                {
                    if (!stages.TryGetValue(name, out var action))
                    {
                        Log.Warn($"the plan lists an unknown stage '{name}'; ignoring it");
                        continue;
                    }
                    if (!plan.ShouldRun(name))
                    {
                        Log.Skip($"stage '{name}' skipped");
                        continue;
                    }

                    Log.Stage(name);
                    action();

                    if (string.Equals(name, options.StopAfter, StringComparison.OrdinalIgnoreCase))
                    {
                        Log.Info($"stopping after '{name}' as requested");
                        break;
                    }
                }
            }

            return Report(state);
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        public static int Verify(Program.Options options)
        {
            if (string.IsNullOrEmpty(options.ProjectPath))
                throw new OpennessStepException("verify needs --project <file.ap21>");

            var plan = string.IsNullOrEmpty(options.PlanPath) ? null : Plan.Load(options.PlanPath);
            var mode = options.WithUi ? TiaPortalMode.WithUserInterface
                                      : TiaPortalMode.WithoutUserInterface;

            using (var portal = new TiaPortal(mode))
            {
                var builder = new HardwareBuilder(plan ?? EmptyPlan(options.ProjectPath));
                var project = builder.OpenProject(portal, options.ProjectPath, options.Upgrade);

                var results = new List<KeyValuePair<string, Verifier.Result>>();
                foreach (var device in project.Devices)
                {
                    var software = HardwareBuilder.FindSoftware<PlcSoftware>(device);
                    if (software == null) continue;

                    if (plan != null)
                    {
                        Log.Stage($"checking blocks on {device.Name}");
                        new SoftwareBuilder(plan, software).ReportBlocks();
                    }

                    Log.Stage($"compiling {device.Name}");
                    results.Add(new KeyValuePair<string, Verifier.Result>(
                        device.Name, Verifier.Compile(software, device.Name)));
                }

                if (results.Count == 0) Log.Warn("the project contains no PLC software to compile");

                Log.Stage("Summary");
                Console.Write(Verifier.Summarise(results));
                return results.All(r => r.Value.Ok) && !HasErrors() ? 0 : 1;
            }
        }

        [MethodImpl(MethodImplOptions.NoInlining)]
        public static int Export(Program.Options options)
        {
            if (string.IsNullOrEmpty(options.ProjectPath))
                throw new OpennessStepException("export needs --project <file.ap21>");
            if (string.IsNullOrEmpty(options.OutDir))
                throw new OpennessStepException("export needs --out <dir>");

            var mode = options.WithUi ? TiaPortalMode.WithUserInterface
                                      : TiaPortalMode.WithoutUserInterface;
            using (var portal = new TiaPortal(mode))
            {
                var builder = new HardwareBuilder(EmptyPlan(options.ProjectPath));
                var project = builder.OpenProject(portal, options.ProjectPath, options.Upgrade);
                return Exporter.Run(project, options.OutDir, options.ParseFormats());
            }
        }

        // ------------------------------------------------------------------
        // Pipeline
        // ------------------------------------------------------------------
        private class BuildState
        {
            public Plan Plan;
            public Program.Options Options;
            public TiaPortal Portal;
            public Project Project;
            public Device PlcDevice, HmiDevice;
            public readonly Dictionary<string, Device> Devices =
                new Dictionary<string, Device>(StringComparer.OrdinalIgnoreCase);
            public PlcSoftware Software;
            public HmiBuilder Hmi;
            public bool HmiAttached;
            public Verifier.Result Compilation;

            public HardwareBuilder Hardware => _hardware ?? (_hardware = new HardwareBuilder(Plan));
            private HardwareBuilder _hardware;

            /// <summary>Resolved on first use, so stages can be skipped freely.</summary>
            public SoftwareBuilder Sw
            {
                get
                {
                    if (_sw == null)
                    {
                        if (PlcDevice == null)
                            throw new OpennessStepException(
                                "the PLC has not been created yet - do not skip create_plc");
                        Software = Software ?? SoftwareBuilder.Resolve(PlcDevice);
                        _sw = new SoftwareBuilder(Plan, Software);
                    }
                    return _sw;
                }
            }
            private SoftwareBuilder _sw;
        }

        private static Dictionary<string, Action> Stages(BuildState state)
        {
            var plan = state.Plan;

            return new Dictionary<string, Action>(StringComparer.OrdinalIgnoreCase)
            {
                ["create_project"] = () =>
                {
                    state.Project = string.IsNullOrEmpty(state.Options.ProjectPath)
                        ? state.Hardware.CreateProject(state.Portal)
                        : state.Hardware.OpenProject(state.Portal, state.Options.ProjectPath,
                                                     state.Options.Upgrade);
                },

                ["create_plc"] = () =>
                {
                    state.PlcDevice = state.Hardware.CreateDevice(state.Project, plan.Plc, "PLC");
                    state.Devices[plan.Plc.Name] = state.PlcDevice;
                },

                ["add_plc_modules"] = () => state.Hardware.PlugModules(state.PlcDevice, plan.Plc),

                ["create_hmi"] = () =>
                {
                    if (plan.Hmi == null)
                    {
                        Log.Skip("the spec has no HMI");
                        return;
                    }
                    state.HmiDevice = state.Hardware.CreateDevice(state.Project, plan.Hmi, "HMI");
                    state.Devices[plan.Hmi.Name] = state.HmiDevice;
                },

                ["create_io_devices"] = () =>
                {
                    if (plan.IoDevices.Count == 0)
                    {
                        Log.Skip("no PROFINET IO devices in the spec");
                        return;
                    }
                    foreach (var info in plan.IoDevices)
                    {
                        var device = state.Hardware.CreateDevice(state.Project, info, "IO device");
                        state.Devices[info.Name] = device;
                        state.Hardware.PlugModules(device, info);
                    }
                },

                ["build_subnet"] = () =>
                {
                    if (plan.Network.Nodes.Count == 0)
                    {
                        Log.Skip("no addressed nodes in the spec (set plc.ip to build a subnet)");
                        return;
                    }
                    state.Hardware.BuildSubnet(state.Project, state.Devices);
                },

                ["assign_io_devices"] = () =>
                    state.Hardware.AssignIoDevices(state.PlcDevice, state.Devices),

                ["create_tag_tables"] = () => state.Sw.CreateTagTables(),

                ["delete_blocks"] = () => state.Sw.DeleteBlocks(),

                ["import_sources"] = () =>
                    state.Sw.ImportSources(plan.Software.SourcesBeforeOb),

                ["create_instance_dbs"] = () => state.Sw.CreateInstanceDbs(),

                ["import_ob"] = () => state.Sw.ImportOb(),

                ["create_hmi_connection"] = () =>
                {
                    if (!AttachHmi(state)) return;
                    state.Hmi.CreateConnection();
                },

                ["create_hmi_tags"] = () =>
                {
                    if (!AttachHmi(state)) return;
                    state.Hmi.CreateTags();
                },

                ["create_hmi_screens"] = () =>
                {
                    if (!AttachHmi(state)) return;
                    state.Hmi.CreateScreens();
                },

                ["compile"] = () =>
                {
                    state.Sw.ReportBlocks();
                    state.Compilation = Verifier.Compile(state.Software, plan.Plc.Name);
                },

                ["save"] = () =>
                {
                    Log.Try("saving the project", () => state.Project.Save(), fatal: true);
                    Log.Ok("project saved: " + state.Project.Path.FullName);
                },
            };
        }

        private static bool AttachHmi(BuildState state)
        {
            if (!state.Plan.HmiSoftware.Enabled || state.HmiDevice == null)
            {
                Log.Skip("no HMI to configure");
                return false;
            }
            if (state.Hmi == null)
            {
                state.Hmi = new HmiBuilder(state.Plan);
                state.HmiAttached = state.Hmi.Attach(state.HmiDevice);
            }
            return state.HmiAttached;
        }

        // ------------------------------------------------------------------
        // Reporting
        // ------------------------------------------------------------------
        private static void Describe(Plan plan, Program.Options options)
        {
            Log.Stage("Plan");
            Log.Info($"project      {plan.Project.Name} ({plan.Project.TiaVersion}) in " +
                     $"{plan.Project.Directory}");
            Log.Info($"PLC          {plan.Plc?.Name} = {plan.Plc?.OrderNumber} {plan.Plc?.Firmware}" +
                     (string.IsNullOrEmpty(plan.Plc?.Ip) ? "" : $" @ {plan.Plc.Ip}"));
            if (plan.Plc?.Modules.Count > 0)
            {
                Log.Info($"modules      {string.Join(", ", plan.Plc.Modules.Select(m => $"slot {m.Slot}: {m.OrderNumber}"))}");
            }
            if (plan.Hmi != null)
            {
                Log.Info($"HMI          {plan.Hmi.Name} = {plan.Hmi.OrderNumber}" +
                         (string.IsNullOrEmpty(plan.Hmi.Ip) ? "" : $" @ {plan.Hmi.Ip}"));
            }
            if (plan.IoDevices.Count > 0)
            {
                Log.Info($"IO devices   {string.Join(", ", plan.IoDevices.Select(d => d.Name))}");
            }

            var tagCount = plan.Software.TagTables.Sum(t => t.Tags.Count);
            Log.Info($"tags         {tagCount} in {plan.Software.TagTables.Count} table(s)");
            Log.Info($"sources      {plan.Software.ExternalSources.Count} SCL file(s)");
            Log.Info($"blocks       {plan.ExpectedBlocks.Count} expected");
            if (plan.HmiSoftware.Enabled)
            {
                Log.Info($"HMI tags     {plan.HmiSoftware.TagTables.Sum(t => t.Tags.Count)}, " +
                         $"screens {plan.HmiSoftware.Screens.Count}, " +
                         $"alarms {plan.HmiSoftware.Alarms.Count}");
            }
            if (plan.Skip.Count > 0)
            {
                Log.Info($"skipping     {string.Join(", ", plan.Skip)}");
            }
            if (!string.IsNullOrEmpty(options.StopAfter))
            {
                Log.Info($"stop after   {options.StopAfter}");
            }
        }

        private static bool HasErrors()
        {
            return Log.Collected.Any(p => p.StartsWith("ERROR", StringComparison.Ordinal));
        }

        private static int Report(BuildState state)
        {
            Log.Stage("Summary");

            var errors = Log.Collected.Count(p => p.StartsWith("ERROR", StringComparison.Ordinal));
            var warnings = Log.Collected.Count(p => p.StartsWith("WARNING", StringComparison.Ordinal));

            if (state.Compilation != null)
            {
                Log.Info($"compile: {state.Compilation.State}, {state.Compilation.Errors} error(s), " +
                         $"{state.Compilation.Warnings} warning(s)");
            }
            else
            {
                Log.Info("compile: not run");
            }
            Log.Info($"driver:  {errors} error(s), {warnings} warning(s)");

            var compileOk = state.Compilation == null || state.Compilation.Ok;
            if (errors == 0 && compileOk)
            {
                Log.Ok("done. Next: write the auto sequence in the machine FB, then re-import " +
                       "that one source with --skip create_project,create_plc,add_plc_modules," +
                       "create_hmi,create_io_devices,build_subnet,assign_io_devices,create_tag_tables");
                return 0;
            }

            Log.Error("finished with problems - review the messages above" +
                      (state.Options.LogFile != null ? " and " + state.Options.LogFile : string.Empty));
            return 1;
        }

        private static Plan EmptyPlan(string projectPath)
        {
            // verify/export do not need a plan; HardwareBuilder only uses it for the
            // project section, and OpenProject takes its path from the command line.
            return Plan.Load(WriteMinimalPlan(projectPath));
        }

        private static string WriteMinimalPlan(string projectPath)
        {
            var path = System.IO.Path.Combine(System.IO.Path.GetTempPath(),
                                              "tiagen-minimal-plan.json");
            System.IO.File.WriteAllText(path,
                "{\"schema\":1,\"project\":{\"name\":\"" +
                System.IO.Path.GetFileNameWithoutExtension(projectPath).Replace("\"", "") +
                "\"},\"pipeline\":[],\"skip\":[]}");
            return path;
        }
    }
}
