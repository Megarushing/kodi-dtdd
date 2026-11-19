"""DoesTheDogDie API v3 client with a JSON file cache (no Kodi imports).
Free tier: 30 requests/minute, 5,000/month -> every answer is cached."""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.doesthedogdie.com/api/v3/"
TIMEOUT = 8                     # seconds per request; playback never waits longer than a few of these
TTL_ITEM = 7 * 86400            # stats + ratings of one movie/show
TTL_LOOKUP = 30 * 86400         # imdb/tmdb/name -> DoesTheDogDie item id
TTL_LOOKUP_MISS = 3 * 86400     # "not on DoesTheDogDie" is re-checked sooner
TTL_TOPICS = 30 * 86400


class ApiError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code            # HTTP status, or None for network errors

    @property
    def key_rejected(self):
        return self.code in (401, 403)


class Client:
    def __init__(self, api_key, cache_dir, user_agent="service.dtdd"):
        self.api_key = (api_key or "").strip()
        self.cache_dir = cache_dir
        self.user_agent = user_agent
        self.remaining_month = None
        os.makedirs(cache_dir, exist_ok=True)

    # cache
    def _path(self, key):
        return os.path.join(self.cache_dir, hashlib.sha1(key.encode()).hexdigest() + ".json")

    def _cached(self, key, ttl):
        try:
            with open(self._path(key)) as f:
                entry = json.load(f)
        except (OSError, ValueError):
            return None
        if time.time() - entry.get("t", 0) > ttl:
            return None
        return entry

    def _store(self, key, data):
        tmp = "%s.%d.%d.tmp" % (self._path(key), os.getpid(), threading.get_ident())  # two players may write at once
        with open(tmp, "w") as f:
            json.dump({"t": time.time(), "key": key, "data": data}, f)
        os.replace(tmp, self._path(key))

    # http
    def _get(self, path):
        if not self.api_key:
            raise ApiError("no API key")
        req = urllib.request.Request(BASE + path, headers={
            "X-API-KEY": self.api_key, "Accept": "application/json", "User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                self.remaining_month = resp.headers.get("X-RateLimit-Remaining-Month")
                return json.load(resp)
        except urllib.error.HTTPError as e:
            raise ApiError("HTTP %s" % e.code, e.code)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ApiError(type(e).__name__)

    def _get_cached(self, key, path, ttl):
        hit = self._cached(key, ttl)
        if hit is not None:
            return hit["data"]
        data = self._get(path)
        self._store(key, data)
        return data

    # api
    def topics(self):
        return self._get_cached("topics", "topics", TTL_TOPICS)

    def find(self, imdb=None, tmdb=None, name=None, pick=None, kind=""):
        """DoesTheDogDie item id for an IMDb id, a TMDb id, or a name (with `pick(results)`
        choosing among the name results). Tries them in that order. None = not found.
        `kind` (e.g. "tv:2018") goes into the name's cache key: a movie and a show can share a name."""
        tries = []
        if imdb and str(imdb).startswith("tt"):
            tries.append(("imdb:" + imdb, "items?imdb=" + urllib.parse.quote(imdb), None))
        if tmdb and str(tmdb).isdigit():
            tries.append(("tmdb:%s" % tmdb, "items?tmdb=%s" % tmdb, None))
        if name and pick:
            tries.append(("name:%s:%s" % (kind, name.casefold()), "items?q=" + urllib.parse.quote(name), pick))
        for key, path, chooser in tries:
            hit = self._cached("find:" + key, TTL_LOOKUP)
            if hit is not None:
                if hit["data"] is not None:
                    return hit["data"]
                if time.time() - hit["t"] <= TTL_LOOKUP_MISS:
                    continue  # recent "not found"
            results = self._get(path)
            item = chooser(results) if chooser else (results[0] if results else None)
            item_id = item.get("id") if item else None
            self._store("find:" + key, item_id)
            if item_id:
                return item_id
        return None

    def item(self, item_id):
        return self._get_cached("item:%s" % item_id, "items/%s" % item_id, TTL_ITEM)

    def ratings(self, item_id):
        return self._get_cached("ratings:%s" % item_id, "items/%s/ratings" % item_id, TTL_ITEM)
