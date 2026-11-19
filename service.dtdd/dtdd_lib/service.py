"""Background service: when any video starts (library, TMDb Helper, any plugin), look the
movie or episode up on DoesTheDogDie. When a chosen topic hits, pause and ask Exit / Continue.
Never blocks playback: no key, no id, API down or slow -> the video just keeps playing."""
import json
import os
import threading

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from dtdd_lib import api, logic

ADDON_ID = "service.dtdd"
LOG_TAG = "[service.dtdd]"
ID_WAIT = 12        # seconds to wait for ids (some plugins fill them a few s after start)
ID_POLL = 1.0


def log(msg, level=xbmc.LOGINFO):
    xbmc.log("%s %s" % (LOG_TAG, msg), level)


def rpc(method, params):
    res = json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})))
    return res.get("result") or {}


def client(addon):
    cache = os.path.join(xbmcvfs.translatePath(addon.getAddonInfo("profile")), "cache")
    return api.Client(addon.getSetting("api_key"), cache, "service.dtdd/" + addon.getAddonInfo("version"))


def imdb_id(*values):
    """The first real IMDb id ("tt..."). Library movies often carry the TMDb number in the IMDb field."""
    for v in values:
        if v and str(v).startswith("tt"):
            return str(v)
    return ""


def topic_names(c):
    try:
        return {t["id"]: t["name"] for t in c.topics()}
    except api.ApiError:
        return {}


class Player(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.generation = 0
        self.lock = threading.Lock()
        self.warned_no_key = False

    def _bump(self):
        with self.lock:
            self.generation += 1
            return self.generation

    def _current(self, gen):
        with self.lock:
            return gen == self.generation

    def onAVStarted(self):
        gen = self._bump()
        try:
            if not self.isPlayingVideo():
                return
        except RuntimeError:
            return
        threading.Thread(target=self._check, args=(gen,), daemon=True).start()

    def onPlayBackStopped(self):
        self._bump()

    def onPlayBackEnded(self):
        self._bump()

    def onPlayBackError(self):
        self._bump()

    # ids of the playing video
    def _ids(self):
        tag = self.getVideoInfoTag()
        season, episode = tag.getSeason(), tag.getEpisode()
        mediatype = tag.getMediaType()
        if mediatype == "episode" or (season >= 0 and episode > 0 and tag.getTVShowTitle()):
            show = {"imdb": imdb_id(tag.getUniqueID("tvshow.imdb")), "tmdb": tag.getUniqueID("tvshow.tmdb"),
                    "name": tag.getTVShowTitle(), "year": None}
            dbid = tag.getDbId()
            if dbid and dbid > 0 and not show["imdb"]:
                ep = rpc("VideoLibrary.GetEpisodeDetails", {"episodeid": dbid, "properties": ["tvshowid"]})
                tvid = (ep.get("episodedetails") or {}).get("tvshowid")
                if tvid:
                    d = rpc("VideoLibrary.GetTVShowDetails",
                            {"tvshowid": tvid, "properties": ["uniqueid", "imdbnumber", "year"]}).get("tvshowdetails") or {}
                    u = d.get("uniqueid") or {}
                    show["imdb"] = imdb_id(u.get("imdb"), d.get("imdbnumber"))
                    show["tmdb"] = show["tmdb"] or u.get("tmdb")
                    show["year"] = d.get("year")
            return {"kind": "episode", "season": season, "episode": episode, **show}
        imdb = imdb_id(xbmc.getInfoLabel("VideoPlayer.IMDBNumber"), tag.getIMDBNumber(), tag.getUniqueID("imdb"))
        return {"kind": "movie", "imdb": imdb, "tmdb": tag.getUniqueID("tmdb"),
                "name": tag.getTitle(), "year": tag.getYear() or None}

    def _wait_ids(self, gen):
        monitor = xbmc.Monitor()
        ids, waited = None, 0.0
        while waited <= ID_WAIT and self._current(gen):
            try:
                ids = self._ids()
            except RuntimeError:
                return None
            if ids["imdb"] or ids["tmdb"]:
                return ids
            if monitor.waitForAbort(ID_POLL):
                return None
            waited += ID_POLL
        return ids if ids and ids.get("name") else None  # name lookup as the last resort

    # the check
    def _check(self, gen):
        addon = xbmcaddon.Addon(ADDON_ID)
        if not addon.getSetting("api_key").strip():
            if not self.warned_no_key:  # once per Kodi start
                self.warned_no_key = True
                log("no API key set; not checking", xbmc.LOGWARNING)
                xbmcgui.Dialog().notification("DoesTheDogDie", "Add your API key in the add-on settings",
                                              xbmcgui.NOTIFICATION_INFO, 5000)
            return
        ids = self._wait_ids(gen)
        if not ids:
            log("no ids for the playing video; not checking")
            return
        c = client(addon)
        wanted = logic.parse_topics(addon.getSetting("topics"))
        try:
            tv = ids["kind"] == "episode"
            type_id = logic.TYPE_TV if tv else logic.TYPE_MOVIE
            item_id = c.find(imdb=ids["imdb"], tmdb=ids["tmdb"], name=ids["name"],
                             pick=lambda res: logic.pick_show(res, ids["name"], ids["year"], type_id),
                             kind="%d:%s" % (type_id, ids["year"] or ""))
            if not item_id:
                log("not on DoesTheDogDie: %s" % _describe(ids))
                return
            names = topic_names(c)
            if tv:
                hits = logic.episode_hits(c.ratings(item_id), wanted, ids["season"], ids["episode"], names)
                where = "S%dE%d" % (ids["season"], ids["episode"])
            else:
                hits = logic.movie_hits(c.item(item_id).get("topicItemStats"), c.ratings(item_id), wanted, names)
                where = "this movie"
        except api.ApiError as e:
            log("DoesTheDogDie lookup failed (%s); playing normally" % e, xbmc.LOGWARNING)
            if e.key_rejected:  # always say it: no setting can make a wrong key work
                xbmcgui.Dialog().notification("DoesTheDogDie", "API key rejected, check the add-on settings",
                                              xbmcgui.NOTIFICATION_WARNING, 5000)
            elif addon.getSetting("notify_errors") != "false":
                xbmcgui.Dialog().notification("DoesTheDogDie", "Lookup failed, playing normally",
                                              xbmcgui.NOTIFICATION_WARNING, 3000)
            return
        log("%s -> DoesTheDogDie %s: %d hit(s), %s requests left this month"
            % (_describe(ids), item_id, len(hits), c.remaining_month or "?"))
        if hits and self._current(gen):
            self._warn(gen, hits, where)

    def _warn(self, gen, hits, where):
        try:
            if not self.isPlayingVideo():
                return
        except RuntimeError:
            return
        paused_by_us = False
        if not xbmc.getCondVisibility("Player.Paused"):
            self.pause()
            paused_by_us = True
        go_on = xbmcgui.Dialog().yesno("DoesTheDogDie warning", logic.box_text(hits, where),
                                       nolabel="Exit", yeslabel="Continue")
        if not self._current(gen):
            return  # playback changed while the box was open
        if go_on:
            log("Continue chosen")
            if paused_by_us and xbmc.getCondVisibility("Player.Paused"):
                self.pause()
        else:
            log("Exit chosen")
            self.stop()


def _describe(ids):
    if ids["kind"] == "episode":
        return "%s S%sE%s (show imdb=%s tmdb=%s)" % (ids["name"], ids["season"], ids["episode"], ids["imdb"], ids["tmdb"])
    return "%s (%s) imdb=%s tmdb=%s" % (ids["name"], ids["year"], ids["imdb"], ids["tmdb"])


def run():
    monitor = xbmc.Monitor()
    player = Player()  # noqa: F841 (kept alive for its callbacks)
    log("started")
    monitor.waitForAbort()
    log("stopped")
