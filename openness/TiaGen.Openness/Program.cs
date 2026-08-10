using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;

namespace TiaGen.Openness
{
    internal static class Program
    {
        static Program()
        {
            // The assembly resolver must exist before the JIT has to resolve any Openness
            // type. A static constructor on the entry-point class is the earliest hook
            // available - registering "at the top of Main" is too late if Main's own body
            // mentions an Openness type. The V21 manual specifies exactly this pattern.
            OpennessResolver.Touch();
        }

        private const string Usage = @"
TiaGen.Openness - drives TIA Portal V21 from a generated build plan.

  doctor  [--assembly-dir <dir>]
      Check this machine: bitness, ""Siemens TIA Openness"" group membership and
      the registered Openness assemblies. Run this first; it needs no project.

  apply   --plan <plan.json> [options]
      Create the project, devices, network, tags and blocks described by the plan,
      then compile. Idempotent by name: safe to re-run.

  verify  --project <file.ap21> [--plan <plan.json>]
      Open an existing project, compile it and report. With --plan it also checks
      that every block the plan expects is present.

  export  --project <file.ap21> --out <dir> [--formats xml,documents,scl]
      Export the software so it can be committed to git, diffed, or used as a
      SimaticML template that matches your own TIA build exactly.

Options
  --plan <file>          plan.json produced by 'python -m tiagen build'
  --project <file>       existing .ap21 to open instead of creating one
  --out <dir>            output directory (export)
  --formats <list>       xml, documents, scl or all (default: all)
  --mode ui|nogui        run TIA Portal with or without its user interface (default: ui)
  --assembly-dir <dir>   folder holding Siemens.Engineering.Base.dll. On V21 that is the
                         target-framework subfolder, e.g.
                         ""C:\Program Files\Siemens\Automation\Portal V21\PublicAPI\V21\net48""
  --skip <stages>        comma-separated pipeline stages to skip
  --stop-after <stage>   run the pipeline up to and including this stage
  --no-compile           skip the compile stage
  --no-save              leave the project unsaved (for experiments)
  --dry-run              print what would be done, touch nothing
  --log <file>           write a full log, including every attempted API call
  --verbose              also print the detail lines to the console

Exit codes: 0 success, 1 problems reported, 2 bad usage or environment.
";

        private static int Main(string[] args)
        {
            // Nothing in this method may touch a Siemens.Engineering type: the JIT
            // resolves a method's types when the method is entered, which would run
            // before the assembly resolver below is installed.
            var options = Options.Parse(args);
            if (options == null || options.Verb == null)
            {
                Console.Error.WriteLine(Usage);
                return 2;
            }
            if (options.Verb == "help")
            {
                Console.WriteLine(Usage);
                return 0;
            }

            Log.Verbose = options.Verbose;
            if (options.LogFile != null) Log.OpenFile(options.LogFile);

            try
            {
                if (options.Verb == "doctor")
                {
                    return Doctor.Run(options.AssemblyDirectory);
                }

                var membership = Doctor.CheckGroupMembership();
                if (membership == false)
                {
                    Log.Error($"the current user is not in the \"{Doctor.OpennessGroup}\" Windows " +
                              "group, so TIA Portal will refuse the connection. Run 'doctor' for " +
                              "the exact command to fix it.");
                    return 2;
                }

                OpennessResolver.Configure(options.AssemblyDirectory);
                Log.Detail($"Openness: {OpennessResolver.PortalVersion} " +
                           $"({OpennessResolver.EngineeringVersion}, " +
                           $"{OpennessResolver.TargetFramework}) from {OpennessResolver.Directory_}");

                switch (options.Verb)
                {
                    case "apply":
                        return Runner.Apply(options);
                    case "verify":
                        return Runner.Verify(options);
                    case "export":
                        return Runner.Export(options);
                    default:
                        Console.Error.WriteLine("unknown command: " + options.Verb);
                        Console.Error.WriteLine(Usage);
                        return 2;
                }
            }
            catch (OpennessStepException ex)
            {
                Log.Error(ex.Message);
                return 1;
            }
            catch (FileNotFoundException ex)
            {
                Log.Error(ex.Message);
                return 2;
            }
            catch (Exception ex)
            {
                Log.Error(ex.GetType().Name + ": " + ex.Message);
                Log.Detail(ex.ToString());
                return 1;
            }
            finally
            {
                Log.Close();
            }
        }

        internal class Options
        {
            public string Verb, PlanPath, ProjectPath, OutDir, Formats, Mode,
                          AssemblyDirectory, StopAfter, LogFile;
            public HashSet<string> Skip = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            public bool DryRun, Verbose, NoCompile, NoSave;

            public bool WithUi => !string.Equals(Mode, "nogui", StringComparison.OrdinalIgnoreCase);

            public static Options Parse(string[] args)
            {
                if (args.Length == 0) return null;
                var options = new Options { Verb = args[0].TrimStart('-').ToLowerInvariant() };

                for (var i = 1; i < args.Length; i++)
                {
                    var arg = args[i];
                    string Next(string name)
                    {
                        if (i + 1 >= args.Length)
                        {
                            Console.Error.WriteLine($"{name} needs a value");
                            return null;
                        }
                        return args[++i];
                    }

                    switch (arg)
                    {
                        case "--plan": options.PlanPath = Next(arg); break;
                        case "--project": options.ProjectPath = Next(arg); break;
                        case "--out": options.OutDir = Next(arg); break;
                        case "--formats": options.Formats = Next(arg); break;
                        case "--mode": options.Mode = Next(arg); break;
                        case "--assembly-dir": options.AssemblyDirectory = Next(arg); break;
                        case "--stop-after": options.StopAfter = Next(arg); break;
                        case "--log": options.LogFile = Next(arg); break;
                        case "--skip":
                            var list = Next(arg);
                            if (list != null)
                            {
                                foreach (var stage in list.Split(',').Select(s => s.Trim()))
                                {
                                    if (stage.Length > 0) options.Skip.Add(stage);
                                }
                            }
                            break;
                        case "--dry-run": options.DryRun = true; break;
                        case "--verbose": options.Verbose = true; break;
                        case "--no-compile": options.NoCompile = true; break;
                        case "--no-save": options.NoSave = true; break;
                        case "-h":
                        case "--help": options.Verb = "help"; break;
                        default:
                            Console.Error.WriteLine("unknown option: " + arg);
                            return null;
                    }
                }
                return options;
            }

            public Exporter.Formats ParseFormats()
            {
                if (string.IsNullOrWhiteSpace(Formats) ||
                    Formats.Equals("all", StringComparison.OrdinalIgnoreCase))
                {
                    return Exporter.Formats.All;
                }
                var result = Exporter.Formats.None;
                foreach (var part in Formats.Split(',').Select(p => p.Trim()))
                {
                    switch (part.ToLowerInvariant())
                    {
                        case "xml": result |= Exporter.Formats.Xml; break;
                        case "documents":
                        case "docs":
                        case "sd": result |= Exporter.Formats.Documents; break;
                        case "scl": result |= Exporter.Formats.Scl; break;
                        default:
                            Log.Warn("ignoring unknown export format: " + part);
                            break;
                    }
                }
                return result == Exporter.Formats.None ? Exporter.Formats.All : result;
            }
        }
    }
}
