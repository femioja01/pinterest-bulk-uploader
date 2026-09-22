import unittest
import pandas as pd
from src.services.formatter import format_master_csv, inspect_master_csv


class TestStrictPinFiltering(unittest.TestCase):
    def setUp(self):
        # Sample master dataset with 5 rows:
        # Row 0: Valid complete pin
        # Row 1: Missing board (NaN)
        # Row 2: Missing Media Link ("")
        # Row 3: Missing Article Link (NaN)
        # Row 4: Valid complete pin
        self.raw_data = {
            "main_keyword": ["meal prep", "diet tips", "quick snacks", "dinner ideas", "healthy food"],
            "search_volume": [1000, 2000, 3000, 4000, 5000],
            "additional_keywords": ["kw1", "kw2", "kw3", "kw4", "kw5"],
            "related_interests": ["rel1", "rel2", "rel3", "rel4", "rel5"],
            "summary": ["sum1", "sum2", "sum3", "sum4", "sum5"],
            "board": ["Healthy Recipes", None, "Snacks Board", "Dinner Board", "Nutrition"],
            "blogpost_title": ["Post 1", "Post 2", "Post 3", "Post 4", "Post 5"],
            "blogpost_url": ["/p1", "/p2", "/p3", "/p4", "/p5"],
            "keyword": ["meal prep", "diet", "snacks", "dinner", "food"],
            "pin_title": ["Meal Prep Ideas", "Diet Tips 101", "Quick Snacks", "Easy Dinners", "Healthy Plates"],
            "pin_description": ["Desc 1", "Desc 2", "Desc 3", "Desc 4", "Desc 5"],
            "image_prompt_index": ["001", "002", "003", "004", "005"],
            "image_prompt_text": ["Prompt 1", "Prompt 2", "Prompt 3", "Prompt 4", "Prompt 5"],
            "week": ["Week 1", "Week 1", "Week 2", "Week 2", "Week 3"],
            "Article Link": [
                "https://example.com/p1",
                "https://example.com/p2",
                "https://example.com/p3",
                None,
                "https://example.com/p5",
            ],
            "Media Link": [
                "https://example.com/img1.jpg",
                "https://example.com/img2.jpg",
                "",
                "https://example.com/img4.jpg",
                "https://example.com/img5.jpg",
            ],
        }
        self.df = pd.DataFrame(self.raw_data)

    def test_strict_discard_of_incomplete_rows(self):
        # Format the CSV
        out_df, qa = format_master_csv(self.df, target_template="pinterest")

        # Out of 5 rows, only Row 0 and Row 4 are complete
        self.assertEqual(len(out_df), 2)
        self.assertEqual(qa["discarded_rows_count"], 3)
        self.assertEqual(qa["total_output_pins"], 2)

        # Verify only the 8 Pinterest columns exist (all extra metadata dropped)
        expected_cols = [
            "Title", "Media URL", "Pinterest Board", "Thumbnail",
            "Description", "Link", "Publish Date", "Keywords"
        ]
        self.assertEqual(list(out_df.columns), expected_cols)

        # Check values of the 2 retained pins
        titles = list(out_df["Title"])
        self.assertEqual(titles, ["Meal Prep Ideas", "Healthy Plates"])

        boards = list(out_df["Pinterest Board"])
        self.assertEqual(boards, ["Healthy Recipes", "Nutrition"])

        links = list(out_df["Link"])
        self.assertEqual(links, ["https://example.com/p1", "https://example.com/p5"])

        media_urls = list(out_df["Media URL"])
        self.assertEqual(media_urls, ["https://example.com/img1.jpg", "https://example.com/img5.jpg"])

    def test_all_empty_boards_discards_all_rows(self):
        # If board column is all NaN, all rows should be discarded
        df_no_boards = self.df.copy()
        df_no_boards["board"] = None

        with self.assertRaises(ValueError) as ctx:
            format_master_csv(df_no_boards, target_template="pinterest")
        self.assertIn("No valid rows found to format!", str(ctx.exception))

    def test_inspect_master_reports_valid_and_discarded_counts(self):
        info = inspect_master_csv(self.df)
        self.assertTrue(info["valid"])
        self.assertEqual(info["total_rows"], 5)
        self.assertEqual(info["valid_rows"], 2)
        self.assertEqual(info["discarded_rows"], 3)


if __name__ == "__main__":
    unittest.main()
