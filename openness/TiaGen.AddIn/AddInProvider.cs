using System.Collections.Generic;
using Siemens.Engineering;
using Siemens.Engineering.AddIn.Menu;

namespace TiaGen.AddIn
{
    /// <summary>
    /// Declares where the "Claude" menu appears. One provider class per TIA Portal area;
    /// each provider hands back one or more context menus.
    ///
    /// Other areas, if you want the menu there too:
    ///   DevicesAndNetworksAddInProvider   hardware and network editor
    ///   ProjectLibraryTreeAddInProvider   project library
    ///   GlobalLibraryTreeAddInProvider    global libraries
    ///   VciEditorAddInProvider            Version Control Interface workspace
    /// </summary>
    public class ProjectTreeProvider : ProjectTreeAddInProvider
    {
        private readonly TiaPortal _portal;

        public ProjectTreeProvider(TiaPortal portal)
        {
            _portal = portal;
        }

        protected override IEnumerable<ContextMenuAddIn> GetContextMenuAddIns()
        {
            yield return new ClaudeBridgeAddIn(_portal);
        }
    }
}
