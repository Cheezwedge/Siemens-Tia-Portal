using System;
using System.Linq;
using Siemens.Engineering.HW;
#if HAS_HMI_ASSEMBLY
using Siemens.Engineering.Hmi;
using Siemens.Engineering.Hmi.Screen;
using Siemens.Engineering.Hmi.Tag;
// The namespace Siemens.Engineering.Hmi.Tag also contains a type called Tag, so the
// bare name is ambiguous. Alias it.
using HmiTag = Siemens.Engineering.Hmi.Tag.Tag;
#endif

namespace TiaGen.Openness
{
    /// <summary>
    /// HMI side: tag tables, tags bound to the PLC, and the screens.
    ///
    /// This is the least uniform corner of Openness - what is writable differs by
    /// panel family and TIA version. Everything here is therefore attempted rather
    /// than assumed, and each failure is logged with the object it concerned, so an
    /// unsupported call costs you one item instead of the whole run.
    ///
    /// Screen *content* is deliberately not created here. The generator emits
    /// hmi/screens.json with every object, position and binding; see
    /// docs/05-hmi-and-screens.md for the routes that actually place objects.
    /// </summary>
    internal class HmiBuilder
    {
        private readonly Plan _plan;

        public HmiBuilder(Plan plan)
        {
            _plan = plan;
        }

#if HAS_HMI_ASSEMBLY
        private HmiTarget _target;

        public bool Attach(Device hmiDevice)
        {
            _target = HardwareBuilder.FindSoftware<HmiTarget>(hmiDevice);
            if (_target == null)
            {
                Log.Error($"no HMI software found on '{hmiDevice.Name}'. Check the panel order " +
                          "number - a device that is not an HMI has no HmiTarget.");
                return false;
            }
            Log.Ok("HMI target: " + _target.Name);
            return true;
        }

        public void CreateConnection()
        {
            var info = _plan.HmiSoftware;
            if (_target == null || string.IsNullOrEmpty(info.Partner)) return;

            var existing = _target.Connections
                .FirstOrDefault(c => string.Equals(c.Name, info.ConnectionName,
                                                   StringComparison.OrdinalIgnoreCase));
            if (existing != null)
            {
                Log.Skip($"connection '{info.ConnectionName}' already exists");
                return;
            }

            // Connection creation needs the partner device; the HMI tag import below
            // works without it too, but then the tags stay unconnected.
            if (Log.Try($"creating HMI connection '{info.ConnectionName}' to '{info.Partner}'", () =>
            {
                var connection = _target.Connections.Create(info.ConnectionName);
                // Set through SetAttribute: whether Partner is typed as a string or as
                // a device reference has varied, and SetAttribute takes object.
                connection.SetAttribute("Partner", info.Partner);
            }))
            {
                Log.Ok($"connection '{info.ConnectionName}' -> '{info.Partner}'");
            }
            else
            {
                Log.Warn("create the HMI connection by hand in Devices & networks (drag a line " +
                         $"from '{_target.Name}' to '{info.Partner}'), then re-run with " +
                         "--skip create_hmi_connection");
            }
        }

        public void CreateTags()
        {
            var info = _plan.HmiSoftware;
            if (_target == null || info.TagTables.Count == 0)
            {
                Log.Skip("no HMI tags in the plan");
                return;
            }

            foreach (var tableInfo in info.TagTables)
            {
                TagTable table = _target.TagFolder.TagTables
                    .FirstOrDefault(t => string.Equals(t.Name, tableInfo.Name,
                                                       StringComparison.OrdinalIgnoreCase));
                if (table == null)
                {
                    if (!Log.Try($"creating HMI tag table '{tableInfo.Name}'",
                                 () => table = _target.TagFolder.TagTables.Create(tableInfo.Name)))
                    {
                        continue;
                    }
                }
                else
                {
                    Log.Skip($"HMI tag table '{tableInfo.Name}' already exists");
                }

                var created = 0;
                foreach (var tagInfo in tableInfo.Tags)
                {
                    if (table.Tags.Any(t => string.Equals(t.Name, tagInfo.Name,
                                                          StringComparison.OrdinalIgnoreCase)))
                    {
                        continue;
                    }

                    HmiTag tag = null;
                    if (!Log.Try($"creating HMI tag '{tagInfo.Name}'",
                                 () => tag = table.Tags.Create(tagInfo.Name)))
                    {
                        continue;
                    }

                    // Binding an HMI tag to a PLC tag means naming the connection and
                    // the symbolic PLC address. Attribute names have moved between
                    // versions, so set them individually and report what did not take.
                    Log.Try($"binding '{tagInfo.Name}' to {tagInfo.PlcTag}", () =>
                    {
                        tag.SetAttribute("Connection", info.ConnectionName);
                        tag.SetAttribute("PlcTag", tagInfo.PlcTag);
                    });
                    Log.Try($"typing '{tagInfo.Name}' as {tagInfo.DataType}", () =>
                    {
                        tag.SetAttribute("DataType", tagInfo.DataType);
                    });
                    if (!string.IsNullOrEmpty(tagInfo.Comment))
                    {
                        Log.Try($"commenting '{tagInfo.Name}'", () =>
                        {
                            foreach (var item in tag.Comment.Items) item.Text = tagInfo.Comment;
                        });
                    }
                    created++;
                }
                Log.Ok($"HMI table '{tableInfo.Name}': {created} tags created");
            }
        }

        public void CreateScreens()
        {
            var info = _plan.HmiSoftware;
            if (_target == null || info.Screens.Count == 0)
            {
                Log.Skip("no screens in the plan");
                return;
            }

            foreach (var screenInfo in info.Screens)
            {
                if (_target.ScreenFolder.Screens.Any(
                        s => string.Equals(s.Name, screenInfo.Name, StringComparison.OrdinalIgnoreCase)))
                {
                    Log.Skip($"screen '{screenInfo.Name}' already exists");
                    continue;
                }
                if (Log.Try($"creating screen '{screenInfo.Name}'",
                            () => _target.ScreenFolder.Screens.Create(screenInfo.Name)))
                {
                    Log.Ok($"screen '{screenInfo.Name}' created (empty)");
                }
            }

            Log.Info("screens are created empty on purpose. The objects, positions and tag " +
                     "bindings are in hmi/screens.json - see docs/05-hmi-and-screens.md for how " +
                     "to place them.");
        }
#else
        public bool Attach(Device hmiDevice)
        {
            Log.Warn("built without Siemens.Engineering.Hmi, so the HMI stages are unavailable. " +
                     "Rebuild with TiaOpennessDir pointing at a PublicAPI folder that contains " +
                     "Siemens.Engineering.Hmi.dll.");
            return false;
        }

        public void CreateConnection() { }
        public void CreateTags() { }
        public void CreateScreens() { }
#endif
    }
}
