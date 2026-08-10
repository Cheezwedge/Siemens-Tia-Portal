using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Siemens.Engineering;
using Siemens.Engineering.HW;
using Siemens.Engineering.HW.Features;

namespace TiaGen.Openness
{
    /// <summary>
    /// Project, devices, modules and the PROFINET network.
    ///
    /// Every step is idempotent by name: running the same plan twice reuses what is
    /// already there instead of creating "PLC_1_1".
    /// </summary>
    internal class HardwareBuilder
    {
        private readonly Plan _plan;

        public HardwareBuilder(Plan plan)
        {
            _plan = plan;
        }

        // ------------------------------------------------------------------
        // Project
        // ------------------------------------------------------------------
        public Project CreateProject(TiaPortal portal)
        {
            var directory = _plan.Project.Directory;
            if (string.IsNullOrWhiteSpace(directory))
                throw new OpennessStepException(
                    "project.directory is empty in the spec; set it to the folder that should " +
                    "hold the TIA project (for example C:\\TIA\\Projects)");

            var target = new DirectoryInfo(directory);
            if (!target.Exists) target.Create();

            var existing = Path.Combine(target.FullName, _plan.Project.Name);
            if (Directory.Exists(existing))
                throw new OpennessStepException(
                    $"'{existing}' already exists. Delete it, choose another project.name, or " +
                    "run with --project <path to the .ap21> to work on the existing project.");

            Log.Info($"creating project '{_plan.Project.Name}' in {target.FullName}");
            var project = portal.Projects.Create(target, _plan.Project.Name);
            // Set through SetAttribute rather than the typed properties: the
            // attribute names are stable across versions, the property surface is not.
            if (!string.IsNullOrEmpty(_plan.Project.Author))
            {
                Log.Try("setting the project author", () =>
                {
                    project.SetAttribute("Author", _plan.Project.Author);
                });
            }
            Log.Ok("project created: " + project.Path.FullName);
            return project;
        }

        /// <summary>
        /// Opens an existing project.
        ///
        /// Projects.Open refuses a project written by a different TIA version - the
        /// manual's long-term-stability chapter recommends OpenWithUpgrade instead. That
        /// upgrade is irreversible, though, so it stays behind --upgrade: the default is
        /// a plain Open that fails with the command to run rather than silently migrating
        /// somebody's project.
        /// </summary>
        public Project OpenProject(TiaPortal portal, string path, bool allowUpgrade = false)
        {
            var file = new FileInfo(path);
            if (!file.Exists)
                throw new OpennessStepException("project file not found: " + file.FullName);

            if (allowUpgrade)
            {
                Log.Info("opening with upgrade " + file.FullName);
                var upgraded = portal.Projects.OpenWithUpgrade(file);
                Log.Ok("project opened, upgraded to " + OpennessResolver.PortalVersion);
                return upgraded;
            }

            Log.Info("opening " + file.FullName);
            try
            {
                var project = portal.Projects.Open(file);
                Log.Ok("project opened");
                return project;
            }
            catch (Exception ex)
            {
                // A version mismatch is the overwhelmingly likely cause and the only one
                // with a fix the caller can act on, so name it without claiming certainty.
                throw new OpennessStepException(
                    $"could not open {file.FullName}: {ex.GetType().Name}: {ex.Message}\n" +
                    "      If the project was written by a different TIA Portal version, " +
                    $"this one ({OpennessResolver.PortalVersion}) will not open it as-is. " +
                    "Back the project up, then re-run with --upgrade to upgrade it on open.");
            }
        }

        // ------------------------------------------------------------------
        // Devices
        // ------------------------------------------------------------------
        public Device CreateDevice(Project project, Plan.DeviceInfo info, string what)
        {
            var existing = project.Devices.FirstOrDefault(
                d => string.Equals(d.Name, info.Name, StringComparison.OrdinalIgnoreCase));
            if (existing != null)
            {
                Log.Skip($"{what} '{info.Name}' already exists");
                return existing;
            }

            var attempts = new List<string>();
            foreach (var typeIdentifier in info.TypeIdentifierCandidates())
            {
                attempts.Add(typeIdentifier);
                try
                {
                    var device = project.Devices.CreateWithItem(
                        typeIdentifier, info.DeviceItemName ?? info.Name, info.Name);
                    Log.Ok($"{what} '{device.Name}' from {typeIdentifier}");
                    return device;
                }
                catch (Exception ex)
                {
                    Log.Detail($"{typeIdentifier} rejected: {ex.Message}");
                }
            }

            throw new OpennessStepException(
                $"could not create {what} '{info.Name}'. None of these catalog identifiers were " +
                "accepted:\n      " + string.Join("\n      ", attempts) +
                "\n      Open the hardware catalog in TIA Portal, select the exact device, and copy " +
                "its order number and firmware version into the spec. The identifier must match the " +
                "catalog string character for character.");
        }

        public void PlugModules(Device device, Plan.DeviceInfo info)
        {
            if (info.Modules.Count == 0)
            {
                Log.Skip($"'{info.Name}' has no modules to plug");
                return;
            }

            foreach (var module in info.Modules)
            {
                if (Traverse(device).Any(di => string.Equals(di.Name, module.Name,
                                                             StringComparison.OrdinalIgnoreCase)))
                {
                    Log.Skip($"module '{module.Name}' already plugged");
                    continue;
                }

                var plugged = false;
                var attempts = new List<string>();

                // Which DeviceItem accepts a module differs between families: for
                // S7-1500 it is the rack, for S7-1200 the CPU itself. Ask each
                // candidate rather than guessing.
                foreach (var container in PlugTargets(device))
                {
                    foreach (var typeIdentifier in module.TypeIdentifierCandidates())
                    {
                        attempts.Add($"{container.Name} slot {module.Slot} <- {typeIdentifier}");
                        try
                        {
                            if (!container.CanPlugNew(typeIdentifier, module.Name, module.Slot))
                                continue;
                            container.PlugNew(typeIdentifier, module.Name, module.Slot);
                            Log.Ok($"module '{module.Name}' in slot {module.Slot} of '{container.Name}'");
                            plugged = true;
                            break;
                        }
                        catch (Exception ex)
                        {
                            Log.Detail($"plug into '{container.Name}' failed: {ex.Message}");
                        }
                    }
                    if (plugged) break;
                }

                if (!plugged)
                {
                    Log.Error($"could not plug module '{module.Name}' ({module.OrderNumber}) into slot " +
                              $"{module.Slot}. Tried:\n      " + string.Join("\n      ", attempts) +
                              "\n      Check the order number against the catalog and that the slot is " +
                              "valid and free for this CPU.");
                }
            }
        }

        // ------------------------------------------------------------------
        // Network
        // ------------------------------------------------------------------
        public void BuildSubnet(Project project, IDictionary<string, Device> devices)
        {
            var subnetName = _plan.Network.SubnetName;
            Subnet subnet = project.Subnets.FirstOrDefault(
                s => string.Equals(s.Name, subnetName, StringComparison.OrdinalIgnoreCase));

            foreach (var node in _plan.Network.Nodes)
            {
                if (!devices.TryGetValue(node.Device, out var device))
                {
                    Log.Warn($"network node references unknown device '{node.Device}'");
                    continue;
                }

                var itf = FindInterface(device);
                if (itf == null)
                {
                    Log.Warn($"'{node.Device}' has no PROFINET interface; skipping addressing");
                    continue;
                }

                var ethernetNode = itf.Nodes.FirstOrDefault();
                if (ethernetNode == null)
                {
                    Log.Warn($"'{node.Device}' interface exposes no node; skipping addressing");
                    continue;
                }

                Log.Try($"setting {node.Device} address to {node.Ip}", () =>
                {
                    ethernetNode.SetAttribute("Address", node.Ip);
                });
                if (!string.IsNullOrWhiteSpace(node.SubnetMask))
                {
                    Log.Try($"setting {node.Device} subnet mask", () =>
                    {
                        ethernetNode.SetAttribute("SubnetMask", node.SubnetMask);
                    });
                }
                if (!string.IsNullOrWhiteSpace(node.Gateway))
                {
                    Log.Try($"setting {node.Device} router", () =>
                    {
                        ethernetNode.SetAttribute("UseRouter", true);
                        ethernetNode.SetAttribute("RouterAddress", node.Gateway);
                    });
                }

                if (subnet == null)
                {
                    // Creating through the node avoids having to name the subnet type.
                    Log.Try($"creating subnet '{subnetName}' from {node.Device}", () =>
                    {
                        subnet = ethernetNode.CreateAndConnectToSubnet(subnetName);
                    }, fatal: true);
                    Log.Ok($"subnet '{subnetName}' created");
                }
                else if (ethernetNode.ConnectedSubnet == null)
                {
                    Log.Try($"connecting {node.Device} to '{subnetName}'", () =>
                    {
                        ethernetNode.ConnectToSubnet(subnet);
                    });
                }
                else
                {
                    Log.Skip($"{node.Device} already on a subnet");
                }

                var profinetName = devices.ContainsKey(node.Device)
                    ? ProfinetNameFor(node.Device)
                    : null;
                if (!string.IsNullOrWhiteSpace(profinetName))
                {
                    Log.Try($"setting PROFINET device name of {node.Device}", () =>
                    {
                        itf.SetAttribute("PnDeviceNameAutoGeneration", false);
                        itf.SetAttribute("PnDeviceName", profinetName);
                    });
                }
            }

            if (subnet != null) Log.Ok($"subnet '{subnet.Name}' holds the station addresses");
        }

        /// <summary>Creates the controller's IO system and attaches every IO device.</summary>
        public void AssignIoDevices(Device plcDevice, IDictionary<string, Device> devices)
        {
            if (_plan.IoDevices.Count == 0)
            {
                Log.Skip("no PROFINET IO devices in the plan");
                return;
            }

            var controllerInterface = FindInterface(plcDevice);
            if (controllerInterface == null)
            {
                Log.Error("the PLC has no PROFINET interface; cannot build an IO system");
                return;
            }

            var controller = controllerInterface.IoControllers.FirstOrDefault();
            if (controller == null)
            {
                Log.Error("the PLC interface exposes no IO controller; cannot build an IO system");
                return;
            }

            IoSystem ioSystem = null;
            Log.Try("reading the existing IO system", () => { ioSystem = controller.IoSystem; });
            if (ioSystem == null)
            {
                Log.Try($"creating IO system '{_plan.Network.IoSystemName}'", () =>
                {
                    ioSystem = controller.CreateIoSystem(_plan.Network.IoSystemName);
                }, fatal: true);
                Log.Ok("IO system created");
            }
            else
            {
                Log.Skip("IO system already present: " + ioSystem.Name);
            }

            foreach (var info in _plan.IoDevices)
            {
                if (!devices.TryGetValue(info.Name, out var device)) continue;

                var itf = FindInterface(device);
                var connector = itf?.IoConnectors.FirstOrDefault();
                if (connector == null)
                {
                    Log.Warn($"'{info.Name}' has no IO connector; assign it by hand in the network view");
                    continue;
                }

                // Rather than probing a version-specific "already connected" property,
                // just attempt the assignment: a second call is rejected harmlessly.
                if (Log.Try($"assigning '{info.Name}' to the IO system",
                            () => connector.ConnectToIoSystem(ioSystem)))
                {
                    Log.Ok($"'{info.Name}' assigned to " + ioSystem.Name);
                }
                else
                {
                    Log.Info($"'{info.Name}' was not assigned - if it is already in the IO system " +
                             "this is expected; otherwise assign it in the network view");
                }
            }
        }

        private string ProfinetNameFor(string deviceName)
        {
            if (string.Equals(_plan.Plc?.Name, deviceName, StringComparison.OrdinalIgnoreCase))
                return _plan.Plc.ProfinetName;
            if (string.Equals(_plan.Hmi?.Name, deviceName, StringComparison.OrdinalIgnoreCase))
                return _plan.Hmi.ProfinetName;
            return _plan.IoDevices
                .FirstOrDefault(d => string.Equals(d.Name, deviceName, StringComparison.OrdinalIgnoreCase))
                ?.ProfinetName;
        }

        // ------------------------------------------------------------------
        // Traversal helpers, shared with the software and HMI builders
        // ------------------------------------------------------------------

        /// <summary>Every DeviceItem in a device, depth first.</summary>
        public static IEnumerable<DeviceItem> Traverse(Device device)
        {
            foreach (var item in device.DeviceItems)
            {
                foreach (var nested in Traverse(item)) yield return nested;
            }
        }

        public static IEnumerable<DeviceItem> Traverse(DeviceItem item)
        {
            yield return item;
            foreach (var child in item.DeviceItems)
            {
                foreach (var nested in Traverse(child)) yield return nested;
            }
        }

        /// <summary>
        /// DeviceItems that might accept a plugged module, outermost first - which is
        /// the order that works for both the S7-1200 (CPU) and S7-1500 (rack) layout.
        /// </summary>
        private static IEnumerable<DeviceItem> PlugTargets(Device device)
        {
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var item in device.DeviceItems.Concat(Traverse(device)))
            {
                // DeviceItem has no stable id here, so de-duplicate on name+position.
                var key = item.Name + "#" + item.PositionNumber;
                if (seen.Add(key)) yield return item;
            }
        }

        public static NetworkInterface FindInterface(Device device)
        {
            foreach (var item in Traverse(device))
            {
                var itf = item.GetService<NetworkInterface>();
                if (itf != null) return itf;
            }
            return null;
        }

        /// <summary>The software container attached to a device, whatever its nesting.</summary>
        public static T FindSoftware<T>(Device device) where T : class
        {
            foreach (var item in Traverse(device))
            {
                var container = item.GetService<SoftwareContainer>();
                if (container?.Software is T match) return match;
            }
            return null;
        }
    }
}
