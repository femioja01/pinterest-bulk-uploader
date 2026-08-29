#!/usr/bin/env python3
"""
Pinterest Bulk Pin Exporter CLI
Converts Master CSV files into Pinterest Official Bulk CSV format with:
- Week filtering (e.g. Weeks 2-5 or specific weeks)
- Natural numerical week ordering (Week 1, Week 2, ...)
- Intelligent description trimming to strictly <= 500 characters
- Title length verification (<= 100 characters)
"""

import argparse
from pathlib import Path
from src.services.formatter import format_master_csv


def main():
    parser = argparse.ArgumentParser(
        description="Format master pin CSV into Pinterest Official Bulk Upload format."
    )
    parser.add_argument("-i", "--input", required=True, help="Path to Master CSV input file")
    parser.add_argument("-o", "--output", required=True, help="Path to Output CSV file")
    parser.add_argument(
        "--start-week",
        type=int,
        default=None,
        help="Start week number (e.g. 2)",
    )
    parser.add_argument(
        "--end-week",
        type=int,
        default=None,
        help="End week number (e.g. 5)",
    )
    parser.add_argument(
        "--weeks",
        type=str,
        default=None,
        help="Comma-separated specific week numbers (e.g. 1,3,5)",
    )

    args = parser.parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found at {input_path}")
        return

    specific_weeks = None
    if args.weeks:
        specific_weeks = [int(w.strip()) for w in args.weeks.split(",") if w.strip().isdigit()]

    print(f"[+] Reading Master CSV: {input_path}")
    out_df, qa = format_master_csv(
        input_path,
        start_week=args.start_week,
        end_week=args.end_week,
        specific_weeks=specific_weeks,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(output_path, index=False, encoding="utf-8-sig")

    print("\n--- QA Verification ---")
    print(f"Total raw rows: {qa['total_raw_rows']}")
    print(f"Total output pins: {qa['total_output_pins']}")
    print(f"Max Title Length: {qa['max_title_length']} (Limit: 100)")
    print(f"Titles > 100 chars: {qa['titles_over_100']}")
    print(f"Max Description Length: {qa['max_desc_length']} (Limit: 500)")
    print(f"Descriptions > 500 chars: {qa['descriptions_over_500']}")
    print(f"Missing values in mandatory fields: {qa['missing_mandatory_fields']}")
    print(f"\n[✓] Successfully generated: {output_path}")


if __name__ == "__main__":
    main()
