"""Tests for proper_roger.drill."""
import tempfile
import unittest
from pathlib import Path

from proper_roger.drill import INTERVALS, WordDrill, WordList


WORDS = ("TU", "73", "R", "UR", "ES", "FB", "DE", "K", "TNX", "RST", "NAME", "QTH", "HR")


def drill_through(drill, grades):
    """Send and grade one word per entry: "p" proper, "h" helped, "x" peeked."""
    sent = []
    for grade in grades:
        sent.append(drill.next())
        drill.grade(grade == "p", grade == "x")
    return sent


class WordListTests(unittest.TestCase):
    def test_in_order_then_round_again(self):
        words = WordList(("TU", "73", "R"))
        self.assertEqual([words.next() for _ in range(5)], ["TU", "73", "R", "TU", "73"])
        self.assertFalse(words.new_station)

    def test_needs_words(self):
        with self.assertRaises(ValueError):
            WordList(())


class WordDrillTests(unittest.TestCase):
    def test_new_words_come_in_frequency_order(self):
        self.assertEqual(drill_through(WordDrill(WORDS), "pppp"), ["TU", "73", "R", "UR"])

    def test_a_peeked_word_comes_back_at_once(self):
        sent = drill_through(WordDrill(WORDS), "xppp")   # two words, then TU again
        self.assertEqual(sent, ["TU", "73", "R", "TU"])

    def test_known_words_come_back_rarely_helped_ones_often(self):
        drill = WordDrill(WORDS)
        drill_through(drill, "ph")         # TU at first hearing, 73 after help
        self.assertEqual(drill.cards["TU"][0], 2)
        self.assertEqual(drill.cards["73"][0], 1)
        sent = drill_through(drill, "p" * 12)
        self.assertLess(sent.index("73"), sent.index("TU"))

    def test_boxes_climb_and_fall(self):
        drill = WordDrill(("TU",))
        backs = []
        for _ in range(4):
            drill.next()
            backs.append(drill.grade(True, False))
        self.assertEqual(backs, list(INTERVALS[2:6]))
        drill.next()
        self.assertEqual(drill.grade(False, False), INTERVALS[1])
        drill.next()
        self.assertEqual(drill.grade(False, True), INTERVALS[0])

    def test_never_the_same_word_twice_in_a_row_when_avoidable(self):
        sent = drill_through(WordDrill(("TU", "73")), "xxxxxx")
        self.assertTrue(all(a != b for a, b in zip(sent, sent[1:])), sent)

    def test_one_station_for_the_whole_drill(self):
        drill = WordDrill(WORDS)
        drill.next()
        self.assertTrue(drill.new_station)
        drill.grade(True, False)
        drill.next()
        self.assertFalse(drill.new_station)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / "sub" / "drill.json"

    def test_progress_survives_the_session(self):
        drill = WordDrill(WORDS, self.path)
        drill_through(drill, "phx")
        again = WordDrill(WORDS, self.path)
        self.assertEqual((again.turn, again.cards), (drill.turn, drill.cards))
        self.assertEqual(again.seen, 3)

    def test_words_cut_from_the_list_are_forgotten(self):
        drill_through(WordDrill(WORDS, self.path), "ppp")
        self.assertNotIn("73", WordDrill(("TU", "R"), self.path).cards)

    def test_a_broken_file_starts_afresh_with_a_warning(self):
        self.path.parent.mkdir()
        self.path.write_text("{not json")
        drill = WordDrill(WORDS, self.path)
        self.assertEqual(drill.cards, {})
        self.assertIn("starting afresh", drill.take_error())
        self.assertIsNone(drill.take_error())

    def test_unwritable_file_is_reported_once(self):
        self.path.parent.mkdir()
        self.path.mkdir()                  # a directory where the file should be
        drill = WordDrill(WORDS, self.path)
        drill.take_error()                 # the failed read
        drill = WordDrill(WORDS, self.path / "x" / "drill.json")
        self.path.joinpath("x").write_text("")   # its folder is a file: cannot save
        drill_through(drill, "pp")
        self.assertIn("cannot save", drill.take_error())
        self.assertIsNone(drill.take_error())

    def test_a_box_out_of_range_is_clamped(self):
        self.path.parent.mkdir()
        self.path.write_text('{"turn": 3, "cards": {"TU": [-4, 1], "73": [99, 2]}}')
        drill = WordDrill(WORDS, self.path)
        self.assertEqual(drill.cards, {"TU": [0, 1], "73": [len(INTERVALS) - 1, 2]})


if __name__ == "__main__":
    unittest.main()
