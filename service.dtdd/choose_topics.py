"""Settings button: pick the DoesTheDogDie topics that warn (multiselect), saved as topic ids."""
import os
import sys

# own package first: other add-ons also ship a 'resources.lib' package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import xbmcaddon
import xbmcgui

from dtdd_lib import api, logic
from dtdd_lib.service import ADDON_ID, client

addon = xbmcaddon.Addon(ADDON_ID)
try:
    topics = client(addon).topics()
except api.ApiError as e:
    xbmcgui.Dialog().ok("DoesTheDogDie", "Could not load the topic list (%s). Check the API key." % e)
    topics = None
if topics:
    chosen = logic.parse_topics(addon.getSetting("topics"))
    # chosen topics first, then the rest A-Z
    topics.sort(key=lambda t: (t["id"] not in chosen, t["name"].casefold()))
    picked = xbmcgui.Dialog().multiselect("Topics that warn", [t["name"] for t in topics],
                                          preselect=[i for i, t in enumerate(topics) if t["id"] in chosen])
    if picked is not None:
        ids = sorted(topics[i]["id"] for i in picked)
        if ids:
            addon.setSetting("topics", ",".join(str(i) for i in ids))
        else:
            xbmcgui.Dialog().ok("DoesTheDogDie", "No topic chosen; keeping the previous choice.")
