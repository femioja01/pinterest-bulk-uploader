"""CSV Batch Splitting module for Pinterest Bulk Uploader.

Handles flexible delimiters (tab, comma, semicolon), header alias normalization,
smart validation, and splits master CSVs into ≤100 pin batches formatted
strictly for Pinterest's bulk upload tool.
"""

import csv
import io
import logging
from pathlib import Path
from src.config import PINTEREST_CSV_COLUMNS

logger = logging.getLogger(__name__)

# Column aliases mapping variations to standard Pinterest column names
HEADER_ALIASES = {
    # Title
    "title": "Title",
    "pin title": "Title",
    "pin_title": "Title",
    "name": "Title",
    "headline": "Title",

    # Media URL
    "media url": "Media URL",
    "media_url": "Media URL",
    "image url": "Media URL",
    "image_url": "Media URL",
    "media": "Media URL",
    "image": "Media URL",
    "photo_url": "Media URL",
    "video_url": "Media URL",
    "file url": "Media URL",
    "file_url": "Media URL",

    # Pinterest Board
    "pinterest board": "Pinterest Board",
    "pinterest_board": "Pinterest Board",
    "board": "Pinterest Board",
    "board name": "Pinterest Board",
    "board_name": "Pinterest Board",
    "boards": "Pinterest Board",

    # Thumbnail
    "thumbnail": "Thumbnail",
    "thumbnail url": "Thumbnail",
    "thumbnail_url": "Thumbnail",
    "thumb": "Thumbnail",

    # Description
    "description": "Description",
    "desc": "Description",
    "pin description": "Description",
    "pin_description": "Description",
    "caption": "Description",

    # Link
    "link": "Link",
    "url": "Link",
    "destination link": "Link",
    "destination_link": "Link",
    "destination url": "Link",
    "destination_url": "Link",
    "website": "Link",

    # Publish Date
    "publish date": "Publish Date",
    "publish_date": "Publish Date",
    "schedule date": "Publish Date",
    "schedule_date": "Publish Date",
    "date": "Publish Date",
    "post date": "Publish Date",

    # Keywords
    "keywords": "Keywords",
    "tags": "Keywords",
    "hashtags": "Keywords",
    "keyword": "Keywords",
    "tag": "Keywords",
}

# Essential columns required by Pinterest
REQUIRED_CANONICAL_COLUMNS = ["Title", "Media URL", "Pinterest Board"]


def detect_delimiter(filepath: Path) -> str:
    """Detect delimiter (tab, comma, semicolon, pipe) from a CSV file."""
    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            for _ in range(5):
                line = f.readline()
                if not line:
                    break
                if "\t" in line:
                    return "\t"
                if ";" in line:
                    return ";"
                if "|" in line:
                    return "|"
                if "," in line:
                    return ","
    except Exception:
        pass
    return ","


def _normalize_header_name(raw_header: str) -> str:
    cleaned = raw_header.strip().replace('"', '').replace("'", "")
    lowered = cleaned.lower()
    return HEADER_ALIASES.get(lowered, cleaned)


def count_rows(filepath: Path) -> int:
    """Count data rows (excluding header) in a CSV."""
    delimiter = detect_delimiter(filepath)
    count = 0
    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f, delimiter=delimiter)
            try:
                next(reader)  # skip header
            except StopIteration:
                return 0
            for row in reader:
                if any(cell.strip() for cell in row):
                    count += 1
        return count
    except Exception as e:
        logger.error(f"Error counting rows in {filepath}: {e}")
        return 0


def validate_csv(filepath: Path) -> tuple[bool, str]:
    """Validate that the CSV has the essential Pinterest columns."""
    delimiter = detect_delimiter(filepath)
    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f, delimiter=delimiter)
            try:
                raw_header = next(reader)
            except StopIteration:
                return False, "The uploaded file is empty."

            if not raw_header or not any(raw_header):
                return False, "The uploaded file has no header row."

            normalized_headers = [_normalize_header_name(h) for h in raw_header]

            missing = [col for col in REQUIRED_CANONICAL_COLUMNS if col not in normalized_headers]
            if missing:
                return False, f"Missing required column(s): {', '.join(missing)}. Found: {', '.join(raw_header)}"

            # Count valid data rows
            row_count = 0
            for row in reader:
                if any(cell.strip() for cell in row):
                    row_count += 1

            if row_count == 0:
                return False, "CSV header was found, but there are no data rows to upload."

            return True, ""
    except Exception as e:
        logger.error(f"Error validating CSV {filepath}: {e}")
        return False, f"Error reading CSV file: {str(e)}"


def preview_csv(filepath: Path) -> dict:
    """Return preview statistics and sample rows."""
    delimiter = detect_delimiter(filepath)
    preview = {
        "row_count": 0,
        "column_names": [],
        "boards": [],
        "sample_rows": [],
    }

    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f, delimiter=delimiter)
            raw_header = next(reader)
            normalized_headers = [_normalize_header_name(h) for h in raw_header]
            preview["column_names"] = normalized_headers

            board_col_idx = normalized_headers.index("Pinterest Board") if "Pinterest Board" in normalized_headers else -1
            boards = set()
            sample_rows = []

            for row in reader:
                if not any(cell.strip() for cell in row):
                    continue
                preview["row_count"] += 1

                # Map row to dictionary with canonical keys
                row_dict = {}
                for idx, val in enumerate(row):
                    if idx < len(normalized_headers):
                        row_dict[normalized_headers[idx]] = val.strip()

                if board_col_idx >= 0 and board_col_idx < len(row):
                    b_val = row[board_col_idx].strip()
                    if b_val:
                        boards.add(b_val)

                if len(sample_rows) < 5:
                    sample_rows.append(row_dict)

            preview["boards"] = sorted(list(boards))
            preview["sample_rows"] = sample_rows
            return preview

    except Exception as e:
        logger.error(f"Error previewing CSV {filepath}: {e}")
        return preview


def split_csv(filepath: Path, batch_size: int, output_dir: Path, prefix: str | None = None) -> list[Path]:
    """Split master CSV into standardized Pinterest batch files (max 100 pins per batch).

    Always outputs strictly standardized 8-column comma-separated CSVs:
    Title, Media URL, Pinterest Board, Thumbnail, Description, Link, Publish Date, Keywords
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    delimiter = detect_delimiter(filepath)
    batch_files = []
    prefix_str = prefix or "batch"

    try:
        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f, delimiter=delimiter)
            raw_header = next(reader)
            normalized_headers = [_normalize_header_name(h) for h in raw_header]

            batch_num = 1
            current_batch_rows = []

            for row in reader:
                if not any(cell.strip() for cell in row):
                    continue

                # Build standardized 8-column row
                row_dict = {}
                for idx, val in enumerate(row):
                    if idx < len(normalized_headers):
                        row_dict[normalized_headers[idx]] = val.strip()

                standardized_row = [
                    row_dict.get(col, "") for col in PINTEREST_CSV_COLUMNS
                ]

                current_batch_rows.append(standardized_row)

                if len(current_batch_rows) == batch_size:
                    batch_file = output_dir / f"{prefix_str}_{batch_num:03d}.csv"
                    _write_standard_batch(batch_file, current_batch_rows)
                    batch_files.append(batch_file)
                    batch_num += 1
                    current_batch_rows = []

            if current_batch_rows:
                batch_file = output_dir / f"{prefix_str}_{batch_num:03d}.csv"
                _write_standard_batch(batch_file, current_batch_rows)
                batch_files.append(batch_file)

        logger.info(f"Successfully split {filepath.name} into {len(batch_files)} batches of size ≤ {batch_size}")
        return batch_files

    except Exception as e:
        logger.error(f"Error splitting CSV {filepath}: {e}")
        return []


def _write_standard_batch(filepath: Path, rows: list[list[str]]):
    """Write standard comma-separated Pinterest batch CSV with UTF-8 encoding."""
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(PINTEREST_CSV_COLUMNS)
        writer.writerows(rows)
