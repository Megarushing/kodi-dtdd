import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "service.dtdd"))

from dtdd_lib import api, logic  # noqa: E402


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name + ".json")) as f:
        return json.load(f)


TOPICS = {t["id"]: t["name"] for t in fixture("topics")}
WANTED = set(logic.DEFAULT_TOPICS)


class EpisodeText(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(logic.episodes_in_text("Season 2 Episode 3 - suspect"), {(2, 3)})
        self.assertEqual(logic.episodes_in_text("in S02E07 the dog"), {(2, 7)})
        self.assertEqual(logic.episodes_in_text("Season 4, Ep. 12"), {(4, 12)})
        self.assertEqual(logic.episodes_in_text("see 3x05"), {(3, 5)})
        self.assertEqual(logic.episodes_in_text("A dog is shot at 17 minutes"), set())


class BodyCam(unittest.TestCase):
    ratings = fixture("bodycam_ratings")

    def hits(self, s, e):
        return logic.episode_hits(self.ratings, WANTED, s, e, TOPICS)

    def test_s2e3_fire_index_and_text(self):
        h = self.hits(2, 3)
        self.assertEqual([x["topic"] for x in h], ["a dog dies"])
        self.assertEqual(h[0]["yes"], 2)  # one by season/episode fields, one by "Season 2 Episode 3" in text

    def test_s2e7_no_vote_with_text_still_warns(self):
        h = self.hits(2, 7)
        self.assertEqual(len(h), 1)
        self.assertEqual((h[0]["yes"], h[0]["no"]), (0, 1))
        self.assertIn("Police dog is shot", h[0]["texts"][0])
        self.assertTrue(h[0]["texts"][0].startswith("(voted no)"))

    def test_s4e7_harm_topic(self):
        self.assertEqual([x["topic"] for x in self.hits(4, 7)], ["animals are abused"])

    def test_other_episode_no_box(self):
        self.assertEqual(self.hits(2, 1), [])
        self.assertEqual(self.hits(11, 5), [])

    def test_bare_no_vote_no_box(self):
        bare = [{"topicId": 186, "yes": 0, "index1": 29, "index2": 1, "triggerDescription": ""}]
        self.assertEqual(logic.episode_hits(bare, WANTED, 29, 1, TOPICS), [])

    def test_spam_text_never_shown(self):
        spam = [{"topicId": 153, "yes": 1, "index1": 1, "index2": 2, "voteSum": 9,
                 "triggerDescription": "BE A VAMPIRE TODAY, contact lord at x@example.com"},
                {"topicId": 153, "yes": 0, "index1": 1, "index2": 2, "voteSum": 0,
                 "triggerDescription": "call +1 555 123 4567 now"}]
        h = logic.episode_hits(spam, WANTED, 1, 2, TOPICS)
        self.assertEqual([(x["yes"], x["no"], x["texts"]) for x in h], [(1, 0, [])])  # the YES vote counts, no text, spam NO dropped
        self.assertNotIn("example.com", logic.box_text(h, "S1E2"))

    def test_harm_topic_off_no_box(self):
        self.assertEqual(logic.episode_hits(self.ratings, {153}, 4, 7, TOPICS), [])


class Marley(unittest.TestCase):
    def test_dog_dies(self):
        item = fixture("marley_item")
        h = logic.movie_hits(item["topicItemStats"], fixture("marley_ratings"), WANTED, TOPICS)
        names = [x["topic"] for x in h]
        self.assertEqual(names[0], "a dog dies")
        self.assertIn("a pet dies", names)
        self.assertNotIn("an animal is sad", names)  # off by default
        self.assertTrue(h[0]["texts"])
        text = logic.box_text(h, "this movie")
        self.assertIn("A dog dies", text)
        self.assertIn(logic.ATTRIBUTION, text)

    def test_human_only_topics_no_box(self):
        item = fixture("marley_item")
        self.assertEqual(logic.movie_hits(item["topicItemStats"], [], {9999}, TOPICS), [])


class Misc(unittest.TestCase):
    def test_parse_topics(self):
        self.assertEqual(logic.parse_topics(""), WANTED)
        self.assertEqual(logic.parse_topics("153, 186,x"), {153, 186})

    def test_pick_show(self):
        res = [{"id": 1, "name": "Body Cam", "releaseYear": 2018, "itemTypeId": 16},
               {"id": 2, "name": "Body Cam", "releaseYear": 2020, "itemTypeId": 15},
               {"id": 3, "name": "Body Cam EMT", "releaseYear": 2022, "itemTypeId": 16}]
        self.assertEqual(logic.pick_show(res, "body cam")["id"], 1)
        self.assertEqual(logic.pick_show(res, "body cam", 2018)["id"], 1)
        self.assertIsNone(logic.pick_show(res, "Nope"))


class ApiCache(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        self.c = api.Client("key", self.dir)
        self.calls = []
        self.c._get = lambda path: self.calls.append(path) or [{"id": 7, "name": "X", "itemTypeId": 15}]

    def test_name_cache_keeps_movie_and_show_apart(self):
        movie = self.c.find(name="X", pick=lambda r: logic.pick_show(r, "X", None, logic.TYPE_MOVIE), kind="15:")
        show = self.c.find(name="X", pick=lambda r: logic.pick_show(r, "X", None, logic.TYPE_TV), kind="16:")
        self.assertEqual((movie, show), (7, None))
        self.assertEqual(len(self.calls), 2)  # the show did not reuse the movie's cached answer

    def test_lookup_is_cached(self):
        self.c.find(imdb="tt1")
        self.c.find(imdb="tt1")
        self.assertEqual(len(self.calls), 1)

    def test_key_rejected(self):
        self.assertTrue(api.ApiError("HTTP 401", 401).key_rejected)
        self.assertFalse(api.ApiError("URLError").key_rejected)


if __name__ == "__main__":
    unittest.main()
