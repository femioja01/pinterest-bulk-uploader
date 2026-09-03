import csv
import tempfile
import unittest
from pathlib import Path

from src.services.splitter import validate_csv, split_csv, preview_csv, count_rows
from src.config import PINTEREST_CSV_COLUMNS


class TestSplitter(unittest.TestCase):
    def setUp(self):
        self.tmp_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv", newline="", encoding="utf-8-sig")
        writer = csv.writer(self.tmp_file)
        writer.writerow(PINTEREST_CSV_COLUMNS)
        for i in range(12):
            writer.writerow([
                f"Pin Title {i+1}",
                f"https://example.com/images/pin_{i+1}.jpg",
                "Quick Crafts" if i % 2 == 0 else "Home Decor",
                "",
                f"Description for pin {i+1}",
                f"https://example.com/post-{i+1}",
                "2026-08-20",
                "crafts, diy"
            ])
        self.tmp_file.close()
        self.sample_csv = Path(self.tmp_file.name)
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.sample_csv.unlink(missing_ok=True)
        self.tmp_dir.cleanup()

    def test_validate_csv_success(self):
        valid, err = validate_csv(self.sample_csv)
        self.assertTrue(valid)
        self.assertEqual(err, "")

    def test_validate_csv_missing_column(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["Title", "Media URL", "Description"])
            writer.writerow(["Pin 1", "https://img.com/1.jpg", "Desc"])
            tmp_path = Path(f.name)

        valid, err = validate_csv(tmp_path)
        self.assertFalse(valid)
        self.assertIn("Missing required column", err)
        tmp_path.unlink(missing_ok=True)

    def test_count_rows(self):
        self.assertEqual(count_rows(self.sample_csv), 12)

    def test_split_csv_default_prefix(self):
        batch_files = split_csv(self.sample_csv, batch_size=5, output_dir=self.output_dir)
        self.assertEqual(len(batch_files), 3)
        self.assertEqual(count_rows(batch_files[0]), 5)
        self.assertEqual(count_rows(batch_files[1]), 5)
        self.assertEqual(count_rows(batch_files[2]), 2)
        self.assertEqual(batch_files[0].name, "batch_001.csv")

        for bf in batch_files:
            valid, _ = validate_csv(bf)
            self.assertTrue(valid)

    def test_split_csv_custom_account_prefix(self):
        prefix = "postagemaster_batch_20260903_140532"
        batch_files = split_csv(self.sample_csv, batch_size=5, output_dir=self.output_dir, prefix=prefix)
        self.assertEqual(len(batch_files), 3)
        self.assertEqual(batch_files[0].name, "postagemaster_batch_20260903_140532_001.csv")
        self.assertEqual(batch_files[1].name, "postagemaster_batch_20260903_140532_002.csv")
        self.assertEqual(batch_files[2].name, "postagemaster_batch_20260903_140532_003.csv")

    def test_preview_csv(self):
        preview = preview_csv(self.sample_csv)
        self.assertEqual(preview["row_count"], 12)
        self.assertEqual(set(preview["boards"]), {"Quick Crafts", "Home Decor"})
        self.assertEqual(len(preview["sample_rows"]), 5)


if __name__ == "__main__":
    unittest.main()
