using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;

namespace TiaGen.Openness
{
    /// <summary>
    /// Typed view over plan.json as produced by <c>python -m tiagen build</c>.
    /// Missing optional sections come back empty rather than null, so callers do
    /// not have to null-check their way through a build.
    /// </summary>
    internal class Plan
    {
        public const int SupportedSchema = 1;

        public int Schema { get; private set; }
        public string PlanPath { get; private set; }
        public string BaseDirectory { get; private set; }

        public ProjectInfo Project { get; private set; }
        public DeviceInfo Plc { get; private set; }
        public DeviceInfo Hmi { get; private set; }
        public List<DeviceInfo> IoDevices { get; private set; }
        public NetworkInfo Network { get; private set; }
        public SoftwareInfo Software { get; private set; }
        public HmiSoftwareInfo HmiSoftware { get; private set; }
        public List<string> Pipeline { get; private set; }
        public HashSet<string> Skip { get; private set; }
        public bool Compile { get; private set; }
        public List<string> ExpectedBlocks { get; private set; }

        public static Plan Load(string path)
        {
            var full = Path.GetFullPath(path);
            if (!File.Exists(full))
                throw new FileNotFoundException("plan file not found: " + full);

            var root = JObject.Parse(File.ReadAllText(full));
            var plan = new Plan
            {
                PlanPath = full,
                BaseDirectory = Path.GetDirectoryName(full),
                Schema = root.Value<int?>("schema") ?? 0,
                Compile = root.Value<bool?>("compile") ?? true,
                Pipeline = Strings(root["pipeline"]),
                Skip = new HashSet<string>(Strings(root["skip"]), StringComparer.OrdinalIgnoreCase),
            };

            if (plan.Schema != SupportedSchema)
            {
                throw new OpennessStepException(
                    $"plan.json declares schema {plan.Schema}, this driver understands {SupportedSchema}. " +
                    "Regenerate the plan with a matching version of tiagen.");
            }

            var project = root["project"] as JObject ?? new JObject();
            plan.Project = new ProjectInfo
            {
                Name = project.Value<string>("name"),
                Directory = project.Value<string>("directory"),
                Author = project.Value<string>("author"),
                Comment = project.Value<string>("comment"),
                TiaVersion = project.Value<string>("tia_version"),
            };

            var devices = root["devices"] as JObject ?? new JObject();
            plan.Plc = DeviceInfo.From(devices["plc"] as JObject);
            plan.Hmi = DeviceInfo.From(devices["hmi"] as JObject);
            plan.IoDevices = (devices["io_devices"] as JArray ?? new JArray())
                .OfType<JObject>().Select(DeviceInfo.From).Where(d => d != null).ToList();

            var network = root["network"] as JObject ?? new JObject();
            plan.Network = new NetworkInfo
            {
                SubnetName = network.Value<string>("subnet_name") ?? "PN_IE_1",
                IoSystemName = network.Value<string>("io_system_name") ?? "PROFINET IO-System",
                Nodes = (network["nodes"] as JArray ?? new JArray()).OfType<JObject>()
                    .Select(n => new NodeInfo
                    {
                        Device = n.Value<string>("device"),
                        Ip = n.Value<string>("ip"),
                        SubnetMask = n.Value<string>("subnet_mask"),
                        Gateway = n.Value<string>("gateway"),
                        Role = n.Value<string>("role"),
                    }).ToList(),
            };

            var software = root["software"] as JObject ?? new JObject();
            plan.Software = new SoftwareInfo
            {
                ExternalSources = (software["external_sources"] as JArray ?? new JArray())
                    .OfType<JObject>()
                    .OrderBy(s => s.Value<int?>("order") ?? 0)
                    .Select(s => s.Value<string>("file"))
                    .Where(s => !string.IsNullOrEmpty(s))
                    .ToList(),
                DeleteBeforeImport = Strings(software["delete_before_import"]),
                ObSource = software.Value<string>("ob_source"),
                InstanceDbs = (software["instance_dbs"] as JArray ?? new JArray()).OfType<JObject>()
                    .Select(d => new InstanceDbInfo
                    {
                        Name = d.Value<string>("name"),
                        FromFb = d.Value<string>("from_fb"),
                        Optimized = d.Value<bool?>("optimized") ?? true,
                    }).ToList(),
                TagTables = (software["tag_tables"] as JArray ?? new JArray()).OfType<JObject>()
                    .Select(t => new TagTableInfo
                    {
                        Name = t.Value<string>("name"),
                        Tags = (t["tags"] as JArray ?? new JArray()).OfType<JObject>()
                            .Select(g => new TagInfo
                            {
                                Name = g.Value<string>("name"),
                                DataType = g.Value<string>("datatype"),
                                Address = g.Value<string>("address"),
                                Comment = g.Value<string>("comment"),
                            }).ToList(),
                    }).ToList(),
            };

            plan.HmiSoftware = HmiSoftwareInfo.From(root["hmi_software"] as JObject);

            var verification = root["verification"] as JObject ?? new JObject();
            plan.ExpectedBlocks = Strings(verification["expect_blocks"]);

            return plan;
        }

        public bool ShouldRun(string stage) => !Skip.Contains(stage);

        /// <summary>Absolute path of a file referenced relative to plan.json.</summary>
        public string Resolve(string relative)
        {
            if (string.IsNullOrEmpty(relative)) return null;
            return Path.GetFullPath(Path.Combine(BaseDirectory, relative.Replace('/', Path.DirectorySeparatorChar)));
        }

        private static List<string> Strings(JToken token)
        {
            return (token as JArray ?? new JArray()).Select(t => t.ToString()).ToList();
        }

        internal class ProjectInfo
        {
            public string Name, Directory, Author, Comment, TiaVersion;
        }

        internal class DeviceInfo
        {
            public string Name, OrderNumber, Firmware, DeviceItemName, Ip, ProfinetName,
                          TypeIdentifierOverride, Runtime, Resolution;
            public List<ModuleInfo> Modules = new List<ModuleInfo>();

            public static DeviceInfo From(JObject o)
            {
                if (o == null) return null;
                return new DeviceInfo
                {
                    Name = o.Value<string>("name"),
                    OrderNumber = o.Value<string>("order_number"),
                    Firmware = o.Value<string>("firmware"),
                    DeviceItemName = o.Value<string>("device_name") ?? o.Value<string>("name"),
                    Ip = o.Value<string>("ip"),
                    ProfinetName = o.Value<string>("profinet_name"),
                    TypeIdentifierOverride = o.Value<string>("type_identifier"),
                    Runtime = o.Value<string>("runtime"),
                    Resolution = o.Value<string>("resolution"),
                    Modules = (o["modules"] as JArray ?? new JArray()).OfType<JObject>()
                        .Select(m => new ModuleInfo
                        {
                            Name = m.Value<string>("name"),
                            OrderNumber = m.Value<string>("order_number"),
                            Slot = m.Value<int?>("slot") ?? 0,
                            Firmware = m.Value<string>("firmware"),
                        })
                        .OrderBy(m => m.Slot)
                        .ToList(),
                };
            }

            /// <summary>
            /// The catalog identifier Openness expects, e.g.
            /// "OrderNumber:6ES7 214-1AH50-0XB0/V1.1".
            /// </summary>
            public IEnumerable<string> TypeIdentifierCandidates()
            {
                if (!string.IsNullOrWhiteSpace(TypeIdentifierOverride))
                {
                    yield return TypeIdentifierOverride;
                    yield break;
                }

                var mlfb = (OrderNumber ?? string.Empty).Trim();
                var suffix = string.IsNullOrWhiteSpace(Firmware) ? string.Empty : "/" + Firmware.Trim();

                // The catalog writes the MLFB with a space after the 4-character family
                // prefix ("6ES7 214-..."), but people paste it both ways. Try both, and
                // try without the firmware qualifier last - some catalog entries have
                // only one firmware and reject the suffix.
                foreach (var form in MlfbForms(mlfb))
                {
                    yield return "OrderNumber:" + form + suffix;
                }
                if (suffix.Length > 0)
                {
                    foreach (var form in MlfbForms(mlfb))
                    {
                        yield return "OrderNumber:" + form;
                    }
                }
            }

            private static IEnumerable<string> MlfbForms(string mlfb)
            {
                yield return mlfb;
                if (mlfb.Length > 4 && mlfb[4] != ' ')
                    yield return mlfb.Substring(0, 4) + " " + mlfb.Substring(4);
                if (mlfb.Contains(" "))
                    yield return mlfb.Replace(" ", string.Empty);
            }
        }

        internal class ModuleInfo
        {
            public string Name, OrderNumber, Firmware;
            public int Slot;

            public IEnumerable<string> TypeIdentifierCandidates()
            {
                var device = new DeviceInfo { OrderNumber = OrderNumber, Firmware = Firmware };
                return device.TypeIdentifierCandidates();
            }
        }

        internal class NetworkInfo
        {
            public string SubnetName, IoSystemName;
            public List<NodeInfo> Nodes = new List<NodeInfo>();
        }

        internal class NodeInfo
        {
            public string Device, Ip, SubnetMask, Gateway, Role;
        }

        internal class SoftwareInfo
        {
            public List<string> ExternalSources = new List<string>();
            public List<string> DeleteBeforeImport = new List<string>();
            public List<InstanceDbInfo> InstanceDbs = new List<InstanceDbInfo>();
            public List<TagTableInfo> TagTables = new List<TagTableInfo>();
            public string ObSource;

            /// <summary>Sources except the OB, which is imported after the instance DB exists.</summary>
            public IEnumerable<string> SourcesBeforeOb =>
                ExternalSources.Where(s => !string.Equals(s, ObSource, StringComparison.OrdinalIgnoreCase));
        }

        internal class InstanceDbInfo
        {
            public string Name, FromFb;
            public bool Optimized;
        }

        internal class TagTableInfo
        {
            public string Name;
            public List<TagInfo> Tags = new List<TagInfo>();
        }

        internal class TagInfo
        {
            public string Name, DataType, Address, Comment;
        }

        internal class HmiSoftwareInfo
        {
            public bool Enabled;
            public string ConnectionName, Partner, StartScreen;
            public List<HmiTagTableInfo> TagTables = new List<HmiTagTableInfo>();
            public List<ScreenInfo> Screens = new List<ScreenInfo>();
            public List<AlarmInfo> Alarms = new List<AlarmInfo>();

            public static HmiSoftwareInfo From(JObject o)
            {
                var info = new HmiSoftwareInfo();
                if (o == null) return info;
                info.Enabled = o.Value<bool?>("enabled") ?? false;
                if (!info.Enabled) return info;

                var connection = o["connection"] as JObject ?? new JObject();
                info.ConnectionName = connection.Value<string>("name") ?? "HMI_Connection_1";
                info.Partner = connection.Value<string>("partner");
                info.StartScreen = o.Value<string>("start_screen");

                info.TagTables = (o["tag_tables"] as JArray ?? new JArray()).OfType<JObject>()
                    .Select(t => new HmiTagTableInfo
                    {
                        Name = t.Value<string>("name"),
                        Tags = (t["tags"] as JArray ?? new JArray()).OfType<JObject>()
                            .Select(g => new HmiTagInfo
                            {
                                Name = g.Value<string>("name"),
                                DataType = g.Value<string>("datatype"),
                                PlcTag = g.Value<string>("plc_tag"),
                                Access = g.Value<string>("access"),
                                Comment = g.Value<string>("comment"),
                            }).ToList(),
                    }).ToList();

                info.Screens = (o["screens"] as JArray ?? new JArray()).OfType<JObject>()
                    .Select(s => new ScreenInfo
                    {
                        Name = s.Value<string>("name"),
                        Title = s.Value<string>("title"),
                        Kind = s.Value<string>("kind"),
                    }).ToList();

                info.Alarms = (o["alarms"] as JArray ?? new JArray()).OfType<JObject>()
                    .Select(a => new AlarmInfo
                    {
                        Number = a.Value<int?>("number") ?? 0,
                        Name = a.Value<string>("name"),
                        Text = a.Value<string>("text"),
                        Class = a.Value<string>("class"),
                        TriggerTag = a.Value<string>("trigger_tag"),
                    }).ToList();

                return info;
            }
        }

        internal class HmiTagTableInfo
        {
            public string Name;
            public List<HmiTagInfo> Tags = new List<HmiTagInfo>();
        }

        internal class HmiTagInfo
        {
            public string Name, DataType, PlcTag, Access, Comment;
        }

        internal class ScreenInfo
        {
            public string Name, Title, Kind;
        }

        internal class AlarmInfo
        {
            public int Number;
            public string Name, Text, Class, TriggerTag;
        }
    }
}
