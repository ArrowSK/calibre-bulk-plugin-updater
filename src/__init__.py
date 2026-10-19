# Calibre Bulk Plugin Updater — GPL-3.0-or-later
from calibre.customize import InterfaceActionBase


class BulkPluginUpdater(InterfaceActionBase):
    name = 'Bulk Plugin Updater'
    description = 'Review, preflight and sequentially update installed calibre plugins.'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'ArrowSK'
    version = (0, 1, 0)
    minimum_calibre_version = (8, 0, 0)
    actual_plugin = 'calibre_plugins.bulk_plugin_updater.ui:BulkPluginUpdaterAction'
