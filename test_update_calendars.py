import unittest
from datetime import date
import update_calendars


class TestUpdateCalendars(unittest.TestCase):

    def test_guess_url(self):
        url_a = update_calendars._guess_url("A")
        self.assertIn("secteur-A.pdf", url_a)
        self.assertTrue(url_a.startswith("https://www.pointe-claire.ca/assets/images/collectes/"))

        url_b = update_calendars._guess_url("B")
        self.assertIn("secteur-B.pdf", url_b)

    def test_sha256(self):
        data = b"hello world"
        expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
        self.assertEqual(update_calendars.sha256(data), expected)

    def test_section_of(self):
        # _SECTION_TOPS = [18, 171, 325, 479, 9999]
        self.assertEqual(update_calendars._section_of(10), -1)
        self.assertEqual(update_calendars._section_of(18), 0)
        self.assertEqual(update_calendars._section_of(50), 0)
        self.assertEqual(update_calendars._section_of(170.9), 0)
        self.assertEqual(update_calendars._section_of(171), 1)
        self.assertEqual(update_calendars._section_of(325), 2)
        self.assertEqual(update_calendars._section_of(479), 3)
        self.assertEqual(update_calendars._section_of(10000), -1)

    def test_grid_of(self):
        # _GRID_X_STARTS = [436.0, 859.3, 1282.0]
        # Boundaries are offset by -5
        self.assertEqual(update_calendars._grid_of(430), -1)
        self.assertEqual(update_calendars._grid_of(431), 0)
        self.assertEqual(update_calendars._grid_of(854.2), 0)
        self.assertEqual(update_calendars._grid_of(854.3), 1)
        self.assertEqual(update_calendars._grid_of(1276.9), 1)
        self.assertEqual(update_calendars._grid_of(1277.0), 2)
        self.assertEqual(update_calendars._grid_of(1500), 2)

    def test_col_of(self):
        # _GRID_X_STARTS = [436.0, 859.3, 1282.0], _COL_WIDTH = 55.3
        # round((x0 - start) / 55.3)
        self.assertEqual(update_calendars._col_of(436.0, 0), 0)
        self.assertEqual(update_calendars._col_of(436.0 + 55.3, 0), 1)
        self.assertEqual(update_calendars._col_of(436.0 + 55.3 * 6, 0), 6)
        self.assertEqual(update_calendars._col_of(436.0 + 55.3 * 7, 0), -1)  # out of bounds (> 6)
        self.assertEqual(update_calendars._col_of(400, 0), -1)  # out of bounds (< 0)

    def test_year_range_from_text(self):
        text = "Some text 2024-2025 calendar"
        self.assertEqual(update_calendars._year_range_from_text(text), (2024, 2025))

        text_en_dash = "Some text 2025–2026 calendar"
        self.assertEqual(update_calendars._year_range_from_text(text_en_dash), (2025, 2026))

        # Fallback to current year
        today = date.today()
        expected_start = today.year if today.month >= 4 else today.year - 1
        self.assertEqual(update_calendars._year_range_from_text("no years here"), (expected_start, expected_start + 1))

    def test_extract_christmas_tree_dates(self):
        # English format
        text_en = "Christmas tree collection will take place on January 7 and 14"
        self.assertEqual(
            update_calendars._extract_christmas_tree_dates(text_en, 2025, "A"),
            [date(2025, 1, 7), date(2025, 1, 14)]
        )

        # French format
        text_fr = "collectes des arbres de Noël: 7 et 14 janvier"
        self.assertEqual(
            update_calendars._extract_christmas_tree_dates(text_fr, 2025, "A"),
            [date(2025, 1, 7), date(2025, 1, 14)]
        )

        # Fallback
        self.assertEqual(
            update_calendars._extract_christmas_tree_dates("no mentions", 2025, "A"),
            [date(2025, 1, 7), date(2025, 1, 14)]
        )
        self.assertEqual(
            update_calendars._extract_christmas_tree_dates("no mentions", 2025, "B"),
            [date(2025, 1, 6), date(2025, 1, 13)]
        )


if __name__ == "__main__":
    unittest.main()
