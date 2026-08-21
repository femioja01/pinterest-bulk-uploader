import csv
import tempfile
from pathlib import Path
import pytest

from src.services.splitter import validate_csv, split_csv, preview_csv, count_rows
from src.config import PINTEREST_CSV_COLUMNS


@pytest.fixture
def sample_csv():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
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
        tmp_path = Path(f.name)
    yield tmp_path
    tmp_path.unlink(missing_ok=True)


def test_validate_csv_success(sample_csv):
    valid, err = validate_csv(sample_csv)
    assert valid is True
    assert err == ""


def test_validate_csv_missing_column(sample_csv):
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Title", "Media URL", "Description"])  # missing columns
        writer.writerow(["Pin 1", "https://img.com/1.jpg", "Desc"])
        tmp_path = Path(f.name)

    valid, err = validate_csv(tmp_path)
    assert valid is False
    assert "Missing required column" in err
    tmp_path.unlink(missing_ok=True)


def test_count_rows(sample_csv):
    assert count_rows(sample_csv) == 12


def test_split_csv(sample_csv, tmp_path):
    output_dir = tmp_path / "queue"
    batch_files = split_csv(sample_csv, batch_size=5, output_dir=output_dir)

    # 12 rows / 5 = 3 batches (5 + 5 + 2)
    assert len(batch_files) == 3
    assert count_rows(batch_files[0]) == 5
    assert count_rows(batch_files[1]) == 5
    assert count_rows(batch_files[2]) == 2

    # Check headers preserved in each batch
    for bf in batch_files:
        valid, _ = validate_csv(bf)
        assert valid is True


def test_preview_csv(sample_csv):
    preview = preview_csv(sample_csv)
    assert preview["row_count"] == 12
    assert set(preview["boards"]) == {"Quick Crafts", "Home Decor"}
    assert len(preview["sample_rows"]) == 5
