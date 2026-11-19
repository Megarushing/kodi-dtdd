"""Pure logic (no Kodi imports): which DoesTheDogDie topics hit the playing movie or episode,
and the text of the warning box. Unit-tested in tests/test_logic.py."""
import re

# Default topics, by DoesTheDogDie topic id (the ids are stable; names for reading only).
# Animal deaths (category 56) and physical harm to animals (category 55). Off by default:
# a dragon dies (196), an animal is abandoned (350), an animal is sad (355),
# an animal attacks someone (390), bugs, and every human topic.
DEFAULT_TOPICS = {
    153: "a dog dies",
    186: "a cat dies",
    183: "a horse dies",
    285: "a pet dies",
    189: "an animal dies",
    229: "animals are abused",
    435: "an animal is hit by a vehicle",
    231: "there's animal fighting",
    338: "rabbits are harmed",
    319: "animals were harmed in the making",
}

TEXT_MAX = 150          # characters of one voter's text shown in the box (longer makes the box scroll)
TEXTS_PER_TOPIC = 1     # voter texts shown per topic
ATTRIBUTION = "Powered by DoesTheDogDie.com"

# "S2E3", "s02e03", "Season 2 Episode 3", "Season 2, Ep. 3", "2x03"
_EPISODE_RES = [
    re.compile(r"\bs(?:eason)?\s*0*(\d{1,2})\s*[,:\-]?\s*e(?:p(?:isode)?)?\.?\s*0*(\d{1,3})\b", re.I),
    re.compile(r"\b0*(\d{1,2})x0*(\d{1,3})\b", re.I),
]


def parse_topics(setting):
    """Topic ids from the add-on setting ("153,186,..."); empty/invalid -> the defaults."""
    ids = set()
    for part in (setting or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids or set(DEFAULT_TOPICS)


def episodes_in_text(text):
    """Every (season, episode) the voter's text names."""
    found = set()
    for rx in _EPISODE_RES:
        for s, e in rx.findall(text or ""):
            found.add((int(s), int(e)))
    return found


def rating_episode_match(rating, season, episode):
    """True when this rating is about exactly this season + episode: its own season/episode
    fields, or (when those are unset) the episode named in its text."""
    i1, i2 = rating.get("index1", -1), rating.get("index2", -1)
    if i1 is not None and i1 >= 0 and i2 is not None and i2 >= 0:
        return (i1, i2) == (season, episode)
    if (i1 is None or i1 < 0) and (i2 is None or i2 < 0):
        return (season, episode) in episodes_in_text(rating.get("triggerDescription"))
    return False  # only a season, or only an episode: not this exact episode


def _position(rating):
    h, m, s = (rating.get("position1", -1), rating.get("position2", -1), rating.get("position3", -1))
    if h is None or m is None or h < 0 or m < 0:
        return ""
    return "%d:%02d:%02d" % (h, m, max(s or 0, 0)) if h else "%d:%02d" % (m, max(s or 0, 0))


# voter texts that advertise (links, e-mail, phone numbers, messenger names) are never shown
_SPAM_RE = re.compile(r"https?://|www\.|\S+@\S+\.\w|whats\s?app|telegram|\+\d[\d\s\-()]{7,}\d|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b", re.I)


def _text(rating):
    """The voter's text, or "" when empty or spam."""
    t = (rating.get("triggerDescription") or "").strip()
    return "" if _SPAM_RE.search(t) else t


def _clip(text):
    text = " ".join((text or "").split())
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX - 1].rstrip() + "…"


def _topic_name(topics, tid, fallback=""):
    return topics.get(tid) or DEFAULT_TOPICS.get(tid) or fallback or "topic %d" % tid


def episode_hits(ratings, wanted, season, episode, topics):
    """[{topic, verdict, texts}] for the chosen topics with a rating naming this episode.
    A NO vote with text counts too: "the dog is shot but survives" is still worth a warning."""
    by_topic = {}
    for r in ratings or []:
        tid = r.get("topicId")
        if tid not in wanted or not rating_episode_match(r, season, episode):
            continue
        if not r.get("yes") and not _text(r):
            continue  # a bare NO says nothing happened
        by_topic.setdefault(tid, []).append(r)
    hits = []
    for tid, rs in by_topic.items():
        yes = sum(1 for r in rs if r.get("yes"))
        no = len(rs) - yes
        rs.sort(key=lambda r: (not r.get("yes"), -(r.get("voteSum") or 0)))
        texts = []
        for r in [r for r in rs if _text(r)][:TEXTS_PER_TOPIC]:
            t = _clip(_text(r))
            pos = _position(r)
            texts.append(("%s%s%s" % ("" if r.get("yes") else "(voted no) ", "at %s: " % pos if pos else "", t)))
        hits.append({"topic": _topic_name(topics, tid), "yes": yes, "no": no, "texts": texts})
    hits.sort(key=lambda h: -h["yes"])
    return hits


def movie_hits(stats, ratings, wanted, topics):
    """[{topic, yes, no, texts}] for the chosen topics where more voters say YES than NO."""
    sums = {}
    for s in stats or []:
        tid = s.get("topicId")
        if tid not in wanted:
            continue
        y, n = sums.get(tid, (0, 0))
        sums[tid] = (y + (s.get("yesSum") or 0), n + (s.get("noSum") or 0))
    hits = []
    for tid, (y, n) in sums.items():
        if y <= n:
            continue
        rs = [r for r in ratings or [] if r.get("topicId") == tid and r.get("yes") and _text(r)]
        rs.sort(key=lambda r: -(r.get("voteSum") or 0))
        texts = []
        for r in rs[:TEXTS_PER_TOPIC]:
            pos = _position(r)
            texts.append("%s%s" % ("at %s: " % pos if pos else "", _clip(_text(r))))
        hits.append({"topic": _topic_name(topics, tid), "yes": y, "no": n, "texts": texts})
    hits.sort(key=lambda h: -h["yes"])
    return hits


def box_text(hits, where):
    """The warning box message. `where` = 'this movie' or 'S2E7'."""
    lines = []
    for h in hits:
        lines.append("[B]%s[/B] - %s (yes %d / no %d)" % (h["topic"][:1].upper() + h["topic"][1:], where, h["yes"], h["no"]))
        for t in h["texts"]:
            lines.append("   %s" % t)
    lines.append("[I]%s[/I]" % ATTRIBUTION)
    return "[CR]".join(lines)


TYPE_MOVIE = 15
TYPE_TV = 16


def pick_show(results, name, year=None, type_id=TYPE_TV):
    """The DoesTheDogDie item of `type_id` (16 TV, 15 movie) whose name equals `name`
    (case-insensitive); with several, the one with the same year. None when nothing matches exactly."""
    key = (name or "").casefold().strip()
    tv = [r for r in results or [] if r.get("itemTypeId") == type_id and (r.get("name") or "").casefold().strip() == key]
    if year:
        same = [r for r in tv if r.get("releaseYear") == year]
        if same:
            return same[0]
    return tv[0] if len(tv) == 1 or (tv and not year) else None
