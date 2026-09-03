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
from datetime import datetime, date, time, timedelta
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


def clean_url(val: str) -> str:
    """Extracts a clean URL from text, stripping markdown link syntax [text](url)."""
    if not val or pd.isna(val):
        return ""
    val = str(val).strip()
    m = re.search(r"\((https?://[^\s\)]+)\)", val)
    if m:
        return m.group(1)
    m2 = re.search(r"\[(https?://[^\]]+)\]", val)
    if m2:
        return m2.group(1)
    m3 = re.search(r"https?://\S+", val)
    if m3:
        return m3.group(0).rstrip(")]\"'")
    return val


def is_image_url(s: str) -> bool:
    """Checks if a string represents an image or media link."""
    s = s.strip().lower()
    return bool(re.search(r"(ibb\.co|imgur|cloudinary|\.(jpg|jpeg|png|webp|gif|avif))", s))


def parse_pasted_data(text: str) -> pd.DataFrame:
    """Intelligently parses pasted data (TSV, Markdown table, CSV, or vertical line streams) into a DataFrame."""
    text = text.strip()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("Pasted text is empty")

    first_few = lines[:5]

    # 1. Tab separated (TSV copied from Google Sheets / Excel)
    if any("\t" in l for l in first_few):
        try:
            df = pd.read_csv(io.StringIO(text), sep="\t", encoding_errors="replace")
            if len(df.columns) >= 2 and len(df) > 0:
                for col in df.columns:
                    if any(term in col.lower() for term in ["link", "url"]):
                        df[col] = df[col].apply(clean_url)
                return df
        except Exception:
            pass

    # 2. Markdown table (| col1 | col2 |)
    if any(l.startswith("|") and l.endswith("|") for l in first_few):
        tbl_lines = [l for l in lines if l.startswith("|") and not re.match(r"^\|(\s*:?-+:?\s*\|)+$", l)]
        if len(tbl_lines) >= 2:
            rows = []
            for line in tbl_lines:
                cells = [c.strip() for c in line.split("|")[1:-1]]
                rows.append(cells)
            if rows:
                df = pd.DataFrame(rows[1:], columns=rows[0])
                for col in df.columns:
                    if any(term in col.lower() for term in ["link", "url"]):
                        df[col] = df[col].apply(clean_url)
                return df

    # 3. Standard CSV with commas
    if any("," in l for l in first_few):
        try:
            df = pd.read_csv(io.StringIO(text), encoding_errors="replace")
            if len(df.columns) >= 2 and len(df) > 0:
                for col in df.columns:
                    if any(term in col.lower() for term in ["link", "url"]):
                        df[col] = df[col].apply(clean_url)
                return df
        except Exception:
            pass

    # 4. Vertical stream (ChatGPT / notion / web copy-paste where each cell is on a line)
    known_headers = [
        "main_keyword", "search_volume", "additional_keywords", "related_interests",
        "summary", "board", "blogpost_title", "blogpost_url", "keyword", "pin_title",
        "pin_description", "image_prompt_index", "image_prompt_text", "week",
        "article link", "media link", "title", "description", "link", "media url",
        "pinterest board", "thumbnail", "publish date", "keywords"
    ]

    header_count = 0
    for l in lines:
        if l.lower() in known_headers:
            header_count += 1
        else:
            break

    if header_count >= 3:
        headers = lines[:header_count]
        data = lines[header_count:]
    else:
        headers = []
        data = lines

    # Try to segment into records
    img_indices = [i for i, l in enumerate(data) if is_image_url(l)]
    records = []

    if len(img_indices) >= 1:
        distances = [img_indices[0] + 1] + [img_indices[i] - img_indices[i-1] for i in range(1, len(img_indices))]
        if len(set(distances)) == 1:
            step = distances[0]
            for i in range(0, len(data), step):
                chunk = data[i:i+step]
                if chunk:
                    records.append(chunk)
        else:
            start = 0
            for img_idx in img_indices:
                records.append(data[start:img_idx+1])
                start = img_idx + 1

    if not records:
        chunk_len = len(headers) if headers else 15
        for i in range(0, len(data), chunk_len):
            chunk = data[i:i+chunk_len]
            if chunk:
                records.append(chunk)

    # Build DataFrame from records
    rows = []
    for r in records:
        row_dict = {}
        if headers:
            if len(r) == len(headers):
                for h, val in zip(headers, r):
                    row_dict[h] = val
            elif len(r) == len(headers) - 1:
                # One header omitted in data (e.g. empty 'summary')
                if "summary" in [h.lower() for h in headers]:
                    sum_idx = [h.lower() for h in headers].index("summary")
                    for i in range(sum_idx):
                        row_dict[headers[i]] = r[i]
                    row_dict[headers[sum_idx]] = ""
                    for i in range(sum_idx + 1, len(headers)):
                        row_dict[headers[i]] = r[i - 1]
                else:
                    for i, val in enumerate(r):
                        row_dict[headers[i]] = val
            else:
                for i, val in enumerate(r):
                    col_name = headers[i] if i < len(headers) else f"Column_{i+1}"
                    row_dict[col_name] = val
        else:
            if len(r) == 15 and is_image_url(r[-1]):
                default_cols = [
                    "main_keyword", "search_volume", "additional_keywords", "related_interests",
                    "board", "blogpost_title", "blogpost_url", "keyword", "pin_title",
                    "pin_description", "image_prompt_index", "image_prompt_text", "week",
                    "Article link", "Media Link"
                ]
                for h, val in zip(default_cols, r):
                    row_dict[h] = val
            elif len(r) == 16:
                default_cols = [
                    "main_keyword", "search_volume", "additional_keywords", "related_interests",
                    "summary", "board", "blogpost_title", "blogpost_url", "keyword", "pin_title",
                    "pin_description", "image_prompt_index", "image_prompt_text", "week",
                    "Article link", "Media Link"
                ]
                for h, val in zip(default_cols, r):
                    row_dict[h] = val
            elif len(r) == 8:
                for h, val in zip(PINTEREST_COLUMNS, r):
                    row_dict[h] = val
            else:
                for i, val in enumerate(r):
                    row_dict[f"Column_{i+1}"] = val
        rows.append(row_dict)

    df = pd.DataFrame(rows)

    # Clean URL columns
    for col in df.columns:
        if any(term in str(col).lower() for term in ["link", "url"]):
            df[col] = df[col].apply(clean_url)

    return df


def load_as_dataframe(input_data: bytes | Path | str | pd.DataFrame) -> pd.DataFrame:
    """Helper to load any input type (bytes, path, raw text, DataFrame) into a DataFrame."""
    if isinstance(input_data, pd.DataFrame):
        return input_data.copy()
    if isinstance(input_data, bytes):
        return pd.read_csv(io.BytesIO(input_data), encoding="utf-8-sig", encoding_errors="replace")
    if isinstance(input_data, Path):
        return pd.read_csv(input_data, encoding="utf-8-sig", encoding_errors="replace")
    if isinstance(input_data, str):
        if "\n" in input_data or "\r" in input_data or len(input_data) > 255:
            return parse_pasted_data(input_data)
        try:
            p = Path(input_data)
            if p.exists() and p.is_file():
                return pd.read_csv(p, encoding="utf-8-sig", encoding_errors="replace")
        except (OSError, ValueError):
            pass
        return parse_pasted_data(input_data)
    raise ValueError(f"Unsupported data input type: {type(input_data)}")


def inspect_master_csv(file_bytes_or_path: bytes | Path | str | pd.DataFrame) -> dict:
    """Inspects a Master CSV and extracts column mapping, detected weeks, and row counts."""
    df = load_as_dataframe(file_bytes_or_path)

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
        unique_weeks = sorted([int(w) for w in df["_week_num"].unique() if int(w) != 999])
        for w in unique_weeks:
            count = int((df["_week_num"] == w).sum())
            detected_weeks.append({"week_num": int(w), "label": f"Week {w}", "count": int(count)})

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


PUBLER_COLUMNS = [
    "Date - Intl. format or prompt",
    "Text",
    "Link(s) - Separated by comma for FB carousels",
    "Media URL(s) - Separated by comma",
    "Title - For the video, pin, PDF ..",
    "Label(s) - Separated by comma",
    "Alt text(s) - Separated by ||",
    "Comment(s) - Separated by ||",
    "Pin board, FB album, or Google category",
    "Post subtype - I.e. story, reel, PDF ..",
    "CTA - For Facebook links or Google",
    "Reminder - For stories, reels, shorts, and TikToks",
]


def generate_pin_publish_dates(
    total_pins: int,
    start_date_str: str,
    pins_per_day: int = 25,
    daily_start: str = "08:00",
    daily_end: str = "22:00",
    date_format: str = "iso",
) -> list[str]:
    """Generates publish dates formatted as ISO (YYYY-MM-DDTHH:MM:SS) or Standard (YYYY-MM-DD HH:MM:SS)."""
    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except Exception:
        start_date = (datetime.now() + timedelta(days=1)).date()

    try:
        s_h, s_m = map(int, daily_start.split(":"))
        e_h, e_m = map(int, daily_end.split(":"))
    except Exception:
        s_h, s_m = 8, 0
        e_h, e_m = 22, 0

    start_seconds = s_h * 3600 + s_m * 60
    end_seconds = e_h * 3600 + e_m * 60
    if end_seconds <= start_seconds:
        end_seconds = start_seconds + 14 * 3600

    window_duration = end_seconds - start_seconds

    dates = []
    current_day = start_date
    generated = 0

    while generated < total_pins:
        today_pins = min(pins_per_day, total_pins - generated)
        step_seconds = window_duration / (today_pins - 1) if today_pins > 1 else 0

        for i in range(today_pins):
            sec_offset = int(start_seconds + i * step_seconds)
            h = (sec_offset // 3600) % 24
            m = (sec_offset % 3600) // 60
            s = sec_offset % 60
            dt = datetime.combine(current_day, time(hour=h, minute=m, second=s))
            if date_format == "standard":
                dates.append(dt.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                dates.append(dt.strftime("%Y-%m-%dT%H:%M:%S"))
            generated += 1

        current_day += timedelta(days=1)

    return dates


def format_master_csv(
    file_bytes_or_path: bytes | Path | str | pd.DataFrame,
    target_template: str = "pinterest",
    start_week: int | None = None,
    end_week: int | None = None,
    specific_weeks: list[int] | None = None,
    schedule_publish_dates: bool = False,
    publish_start_date: str | None = None,
    publish_pins_per_day: int = 25,
    publish_daily_start: str = "08:00",
    publish_daily_end: str = "22:00",
) -> tuple[pd.DataFrame, dict]:
    """Formats a Master CSV into Pinterest Official or Publer Bulk Upload format."""
    df = load_as_dataframe(file_bytes_or_path)

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

    # Pre-generate publish dates if requested
    pub_dates = []
    if schedule_publish_dates and len(df) > 0:
        if not publish_start_date:
            publish_start_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        dt_format = "standard" if target_template.lower() == "publer" else "iso"
        pub_dates = generate_pin_publish_dates(
            total_pins=len(df),
            start_date_str=publish_start_date,
            pins_per_day=publish_pins_per_day,
            daily_start=publish_daily_start,
            daily_end=publish_daily_end,
            date_format=dt_format,
        )

    # Build target DataFrame based on selected template
    out_df = pd.DataFrame()
    if target_template.lower() == "publer":
        out_df["Date - Intl. format or prompt"] = pub_dates if schedule_publish_dates else ""
        out_df["Text"] = cleaned_descriptions
        out_df["Link(s) - Separated by comma for FB carousels"] = df[link_col].astype(str).str.strip()
        out_df["Media URL(s) - Separated by comma"] = df[media_col].astype(str).str.strip()
        out_df["Title - For the video, pin, PDF .."] = cleaned_titles
        out_df["Label(s) - Separated by comma"] = ""
        out_df["Alt text(s) - Separated by ||"] = ""
        out_df["Comment(s) - Separated by ||"] = ""
        out_df["Pin board, FB album, or Google category"] = df[board_col].astype(str).str.strip()
        out_df["Post subtype - I.e. story, reel, PDF .."] = ""
        out_df["CTA - For Facebook links or Google"] = ""
        out_df["Reminder - For stories, reels, shorts, and TikToks"] = ""

        # Drop empty rows
        out_df = out_df.dropna(subset=["Text", "Media URL(s) - Separated by comma"]).reset_index(drop=True)
    else:
        # Pinterest Official 8-column format
        out_df["Title"] = cleaned_titles
        out_df["Media URL"] = df[media_col].astype(str).str.strip()
        out_df["Pinterest Board"] = df[board_col].astype(str).str.strip()
        out_df["Thumbnail"] = ""
        out_df["Description"] = cleaned_descriptions
        out_df["Link"] = df[link_col].astype(str).str.strip()
        out_df["Publish Date"] = pub_dates if schedule_publish_dates else ""
        out_df["Keywords"] = ""

        # Drop empty rows
        out_df = out_df.dropna(subset=["Title", "Media URL", "Pinterest Board"]).reset_index(drop=True)

    # QA Verification Stats
    desc_series = out_df["Text"] if target_template.lower() == "publer" else out_df["Description"]
    title_series = out_df["Title - For the video, pin, PDF .."] if target_template.lower() == "publer" else out_df["Title"]
    date_col_name = "Date - Intl. format or prompt" if target_template.lower() == "publer" else "Publish Date"

    desc_lens = desc_series.astype(str).str.len()
    title_lens = title_series.astype(str).str.len()

    qa_report = {
        "target_template": target_template,
        "total_raw_rows": total_raw_rows,
        "total_output_pins": len(out_df),
        "max_title_length": int(title_lens.max()) if len(title_lens) > 0 else 0,
        "titles_over_100": int((title_lens > 100).sum()),
        "max_desc_length": int(desc_lens.max()) if len(desc_lens) > 0 else 0,
        "descriptions_over_500": int((desc_lens > 500).sum()),
        "has_publish_dates": schedule_publish_dates,
        "first_publish_date": str(out_df[date_col_name].iloc[0]) if len(out_df) > 0 and schedule_publish_dates else "",
        "last_publish_date": str(out_df[date_col_name].iloc[-1]) if len(out_df) > 0 and schedule_publish_dates else "",
        "missing_mandatory_fields": {
            "Title": int(title_series.isna().sum()),
            "Description": int(desc_series.isna().sum()),
        },
        "sample_rows": out_df.head(5).to_dict(orient="records"),
    }

    return out_df, qa_report

