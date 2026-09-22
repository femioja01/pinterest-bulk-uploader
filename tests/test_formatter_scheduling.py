import asyncio
import unittest
import zoneinfo
from datetime import datetime, timezone, timedelta
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import tempfile
import shutil

from src.models.database import Base, Account, Batch, BatchStatus, Setting, set_setting
from src.routes.formatter import convert_and_queue_endpoint
import src.routes.formatter as formatter_module


SAMPLE_CSV = """pin_title,pin_description,board,Article Link,Media Link,Publish Date
Pin 1,Desc 1,Board A,https://example.com/1,https://example.com/img1.jpg,
Pin 2,Desc 2,Board A,https://example.com/2,https://example.com/img2.jpg,
Pin 3,Desc 3,Board A,https://example.com/3,https://example.com/img3.jpg,
Pin 4,Desc 4,Board A,https://example.com/4,https://example.com/img4.jpg,
"""


class TestFormatterScheduling(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_path = Path(self.tmp_dir)
        self.db_file = self.tmp_path / "test_sched.db"
        self.engine = create_engine(f"sqlite:///{self.db_file}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        self.orig_session_factory = formatter_module.get_session_factory
        formatter_module.get_session_factory = lambda: self.Session

        with self.Session() as s:
            set_setting(s, "timezone", "Africa/Lagos")
            acc = Account(name="test_account", batch_size=2, enabled=True)
            s.add(acc)
            s.commit()
            self.acc_id = acc.id

        self.orig_ensure_dirs = formatter_module.ensure_account_dirs
        formatter_module.ensure_account_dirs = lambda name: {
            "pins": self.tmp_path / "accounts" / name / "pins",
            "queue": self.tmp_path / "accounts" / name / "queue",
            "done": self.tmp_path / "accounts" / name / "done",
            "failed": self.tmp_path / "accounts" / name / "failed",
        }
        for sub in ["pins", "queue", "done", "failed"]:
            (self.tmp_path / "accounts" / "test_account" / sub).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        formatter_module.get_session_factory = self.orig_session_factory
        formatter_module.ensure_account_dirs = self.orig_ensure_dirs
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_default_schedule_mode(self):
        """Default schedule mode leaves scheduled_upload_at as None to follow account cron."""
        res = asyncio.run(
            convert_and_queue_endpoint(
                file=None,
                raw_text=SAMPLE_CSV,
                account_id=self.acc_id,
                batch_size=2,
                schedule_publish_dates=False,
                queue_schedule_mode="default",
            )
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["batch_count"], 2)

        with self.Session() as s:
            batches = s.query(Batch).filter(Batch.account_id == self.acc_id).order_by(Batch.id.asc()).all()
            self.assertEqual(len(batches), 2)
            for b in batches:
                self.assertIsNone(b.scheduled_upload_at)

    def test_custom_schedule_mode_with_publish_dates(self):
        """Custom upload time (15:30) with auto-generated publish dates spaced automatically."""
        tz = zoneinfo.ZoneInfo("Africa/Lagos")
        res = asyncio.run(
            convert_and_queue_endpoint(
                file=None,
                raw_text=SAMPLE_CSV,
                account_id=self.acc_id,
                batch_size=2,
                schedule_publish_dates=True,
                publish_start_date="2026-10-01",
                publish_pins_per_day=2,  # 2 pins per day, batch size 2 => 1 day interval
                queue_schedule_mode="custom",
                queue_upload_time="15:30",
            )
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["batch_count"], 2)

        with self.Session() as s:
            batches = s.query(Batch).filter(Batch.account_id == self.acc_id).order_by(Batch.id.asc()).all()
            self.assertEqual(len(batches), 2)

            # Batch 1 on 2026-10-01 at 15:30 Lagos time
            b1_dt = batches[0].scheduled_upload_at.replace(tzinfo=timezone.utc).astimezone(tz)
            self.assertEqual(b1_dt.strftime("%Y-%m-%d %H:%M"), "2026-10-01 15:30")

            # Batch 2 on 2026-10-02 at 15:30 Lagos time
            b2_dt = batches[1].scheduled_upload_at.replace(tzinfo=timezone.utc).astimezone(tz)
            self.assertEqual(b2_dt.strftime("%Y-%m-%d %H:%M"), "2026-10-02 15:30")

    def test_custom_schedule_mode_manual_interval(self):
        """Custom upload time (21:15) without publish dates, specifying start date and 3-day interval."""
        tz = zoneinfo.ZoneInfo("Africa/Lagos")
        res = asyncio.run(
            convert_and_queue_endpoint(
                file=None,
                raw_text=SAMPLE_CSV,
                account_id=self.acc_id,
                batch_size=2,
                schedule_publish_dates=False,
                queue_schedule_mode="custom",
                queue_upload_time="21:15",
                queue_first_date="2026-11-10",
                queue_interval_days=3,
            )
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["batch_count"], 2)

        with self.Session() as s:
            batches = s.query(Batch).filter(Batch.account_id == self.acc_id).order_by(Batch.id.asc()).all()
            self.assertEqual(len(batches), 2)

            # Batch 1 on 2026-11-10 at 21:15 Lagos time
            b1_dt = batches[0].scheduled_upload_at.replace(tzinfo=timezone.utc).astimezone(tz)
            self.assertEqual(b1_dt.strftime("%Y-%m-%d %H:%M"), "2026-11-10 21:15")

            # Batch 2 on 2026-11-13 at 21:15 Lagos time (3 days later)
            b2_dt = batches[1].scheduled_upload_at.replace(tzinfo=timezone.utc).astimezone(tz)
            self.assertEqual(b2_dt.strftime("%Y-%m-%d %H:%M"), "2026-11-13 21:15")


if __name__ == "__main__":
    unittest.main()
