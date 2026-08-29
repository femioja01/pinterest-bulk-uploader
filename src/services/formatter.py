"""Pinterest Bulk Pin CSV Formatter Service.

Converts Master/Raw Pin CSV files into Pinterest's Official Bulk Upload format with:
- Week filtering (e.g. Weeks 2-5 or specific weeks)
- Natural numerical week ordering (Week 1 -> Week 2 -> ... -> Week 10)
- Intelligent description trimming to strictly <= 500 characters
- Title length verification (<= 100 characters)
- Pinterest official 8-column schema output
"""

import io
import re
import logging
from pathlib import Path
import pandas as pd

logger = logging.getLogger(__name__)

PINTEREST_COLUMNS = [
    "Title",
    "Media URL",
    "Pinterest Board",
    "Thumbnail",
    "Description",
    "Link",
    "Publish Date",
    "Keywords",
]


def clean_description(text: str, max_len: int = 500) -> str:
    """Trims text to max_len gracefully at sentence or word boundaries."""
    if pd.isna(text) or text is None:
        return ""
    text = str(text).strip()
    # Normalize multiple whitespace
    text = re.sub(r"[ \t\r\n]+", " ", text)
    if len(text) <= max_len:
        return text

    # Split into complete sentences
    sentences = re.findall(r"[^.!?]+[.!?]+", text)
    current = ""
    for s in sentences:
        candidate = (current + " " + s.strip()).strip()
        if len(candidate) <= max_len:
            current = candidate
        else:
            break

    # If sentence-based trimming captured substantial content, return it
    if len(current) >= 150:
        return current

    # Otherwise fallback to nearest word boundary
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space].rstrip(",;:- ") + "."
    return truncated


def clean_title(text: str, max_len: int = 100) -> str:
    """Cleans and truncates title to strictly <= 100 characters."""
    if pd.isna(text) or text is None:
        return ""
    text = str(text).strip()
    text = re.sub(r"[ \t\r\n]+", " ", text)
    if len(text) <= max_len:
        return text
    truncated = text[:max_len]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        return truncated[:last_space].rstrip(",;:- ")
    return truncated


def find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Finds a matching column name in the DataFrame case-insensitively."""
    for c in candidates:
        for col in df.columns:
            if col.strip().lower() == c.strip().lower():
                return col
    return None


def inspect_master_csv(file_bytes_or_path: bytes | Path | str) -> dict:
    """Inspects a Master CSV and extracts column mapping, detected weeks, and row counts."""
    if isinstance(file_bytes_or_path, bytes):
        df = pd.read_csv(io.BytesIO(file_bytes_or_path), encoding="utf-8-sig", encoding_errors="replace")
    else:
        df = pd.read_csv(file_bytes_or_path, encoding="utf-8-sig", encoding_errors="replace")

    total_raw_rows = len(df)
    columns = [str(c).strip() for c in df.columns]

    # Find week column
    week_col = find_col(df, ["week", "weeks", "week_no", "week_num"])
    detected_weeks = []
    if week_col:
        # Extract unique week numbers in natural order
        df["_week_num"] = df[week_col].apply(
            lambda x: int(re.search(r"\d+", str(x)).group()) if re.search(r"\d+", str(x)) else 999
        )
        unique_weeks = sorted([w for w in df["_week_num"].unique() if w != 999])
        for w in unique_weeks:
            count = int((df["_week_num"] == w).sum())
            detected_weeks.append({"week_num": w, "label": f"Week {w}", "count": count})

    # Find required columns
    title_col = find_col(df, ["pin_title", "title", "pintitle"])
    media_col = find_col(df, ["Media Link", "media_link", "media_url", "image_url", "image link"])
    board_col = find_col(df, ["board", "pinterest board", "pinterest_board", "target_board"])
    desc_col = find_col(df, ["pin_description", "description", "pin description", "desc"])
    link_col = find_col(df, ["Article Link", "Article link", "article_link", "link", "blogpost_url", "url"])

    missing = []
    if not title_col:
        missing.append("pin_title / Title")
    if not media_col:
        missing.append("Media Link / Media URL")
    if not board_col:
        missing.append("board / Pinterest Board")
    if not desc_col:
        missing.append("pin_description / Description")
    if not link_col:
        missing.append("Article Link / Link")

    return {
        "valid": len(missing) == 0,
        "missing_columns": missing,
        "total_rows": total_raw_rows,
        "columns": columns,
        "has_weeks": len(detected_weeks) > 0,
        "detected_weeks": detected_weeks,
        "mapped_columns": {
            "title": title_col,
            "media_url": media_col,
            "board": board_col,
            "description": desc_col,
            "link": link_col,
        },
    }


def format_master_csv(
    file_bytes_or_path: bytes | Path | str,
    start_week: int | None = None,
    end_week: int | None = None,
    specific_weeks: list[int] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Formats a Master CSV into Pinterest's Official Bulk Upload format."""
    if isinstance(file_bytes_or_path, bytes):
        df = pd.read_csv(io.BytesIO(file_bytes_or_path), encoding="utf-8-sig", encoding_errors="replace")
    else:
        df = pd.read_csv(file_bytes_or_path, encoding="utf-8-sig", encoding_errors="replace")

    total_raw_rows = len(df)

    # Determine week column
    week_col = find_col(df, ["week", "weeks", "week_no", "week_num"])
    if week_col:
        df["_week_num"] = df[week_col].apply(
            lambda x: int(re.search(r"\d+", str(x)).group()) if re.search(r"\d+", str(x)) else 999
        )
        if specific_weeks:
            df = df[df["_week_num"].isin(specific_weeks)].copy()
        else:
            if start_week is not None:
                df = df[df["_week_num"] >= start_week].copy()
            if end_week is not None:
                df = df[df["_week_num"] <= end_week].copy()

        # Sort naturally by week number, maintaining stable order within each week
        df = df.sort_values(by=["_week_num"], kind="stable").reset_index(drop=True)

    # Resolve required columns
    title_col = find_col(df, ["pin_title", "title", "pintitle"])
    media_col = find_col(df, ["Media Link", "media_link", "media_url", "image_url", "image link"])
    board_col = find_col(df, ["board", "pinterest board", "pinterest_board", "target_board"])
    desc_col = find_col(df, ["pin_description", "description", "pin description", "desc"])
    link_col = find_col(df, ["Article Link", "Article link", "article_link", "link", "blogpost_url", "url"])

    if not all([title_col, media_col, board_col, desc_col, link_col]):
        missing = [
            req
            for req, found in [
                ("pin_title", title_col),
                ("Media Link", media_col),
                ("board", board_col),
                ("pin_description", desc_col),
                ("Article Link", link_col),
            ]
            if not found
        ]
        raise ValueError(f"Missing required columns in CSV: {missing}")

    # Clean text
    cleaned_titles = df[title_col].apply(lambda x: clean_title(x, max_len=100))
    cleaned_descriptions = df[desc_col].apply(lambda x: clean_description(x, max_len=500))

    # Build Official Pinterest DataFrame
    out_df = pd.DataFrame()
    out_df["Title"] = cleaned_titles
    out_df["Media URL"] = df[media_col].astype(str).str.strip()
    out_df["Pinterest Board"] = df[board_col].astype(str).str.strip()
    out_df["Thumbnail"] = ""
    out_df["Description"] = cleaned_descriptions
    out_df["Link"] = df[link_col].astype(str).str.strip()
    out_df["Publish Date"] = ""
    out_df["Keywords"] = ""

    # Drop any completely empty rows
    out_df = out_df.dropna(subset=["Title", "Media URL", "Pinterest Board"]).reset_index(drop=True)

    # QA Verification Stats
    desc_lens = out_df["Description"].astype(str).str.len()
    title_lens = out_df["Title"].astype(str).str.len()

    qa_report = {
        "total_raw_rows": total_raw_rows,
        "total_output_pins": len(out_df),
        "max_title_length": int(title_lens.max()) if len(title_lens) > 0 else 0,
        "titles_over_100": int((title_lens > 100).sum()),
        "max_desc_length": int(desc_lens.max()) if len(desc_lens) > 0 else 0,
        "descriptions_over_500": int((desc_lens > 500).sum()),
        "missing_mandatory_fields": {
            "Title": int(out_df["Title"].isna().sum()),
            "Media URL": int(out_df["Media URL"].isna().sum()),
            "Pinterest Board": int(out_df["Pinterest Board"].isna().sum()),
            "Description": int(out_df["Description"].isna().sum()),
            "Link": int(out_df["Link"].isna().sum()),
        },
        "sample_rows": out_df.head(5).to_dict(orient="records"),
    }

    return out_df, qa_report
