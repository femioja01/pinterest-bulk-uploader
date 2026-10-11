import unittest
import os
import io
import pandas as pd
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from src.app import app
from src.config import ensure_account_dirs
from src.models.database import get_session_factory, Account, Batch, BatchStatus, ActivityLog

class TestRepopulatePublishDates(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.factory = get_session_factory()
        with self.factory() as session:
            # Create a test account
            self.account = session.query(Account).filter(Account.name == "repop_test_acc").first()
            if not self.account:
                self.account = Account(name="repop_test_acc", enabled=True)
                session.add(self.account)
                session.commit()
                session.refresh(self.account)
            self.account_id = self.account.id

            # Clean up existing batches for this account
            session.query(Batch).filter(Batch.account_id == self.account_id).delete()
            session.commit()

            self.dirs = ensure_account_dirs("repop_test_acc")
            self.queue_dir = self.dirs["queue"]

            # Create sample CSV with empty or outdated publish dates
            self.batch_filename_1 = "repop_test_batch_001.csv"
            self.batch_file_1 = self.queue_dir / self.batch_filename_1
            df1 = pd.DataFrame({
                "Title": [f"Pin Title {i}" for i in range(1, 11)],
                "Media URL": [f"https://example.com/img{i}.jpg" for i in range(1, 11)],
                "Pinterest Board": ["Home" for _ in range(10)],
                "Thumbnail": ["" for _ in range(10)],
                "Description": [f"Desc {i}" for i in range(1, 11)],
                "Link": [f"https://example.com/link{i}" for i in range(1, 11)],
                "Publish Date": ["" for _ in range(10)],
                "Keywords": ["" for _ in range(10)],
            })
            df1.to_csv(self.batch_file_1, index=False, encoding="utf-8-sig")

            self.batch_filename_2 = "repop_test_batch_002.csv"
            self.batch_file_2 = self.queue_dir / self.batch_filename_2
            df2 = pd.DataFrame({
                "Title": [f"Pin Title B {i}" for i in range(1, 11)],
                "Media URL": [f"https://example.com/imgB{i}.jpg" for i in range(1, 11)],
                "Pinterest Board": ["Home" for _ in range(10)],
                "Thumbnail": ["" for _ in range(10)],
                "Description": [f"Desc B {i}" for i in range(1, 11)],
                "Link": [f"https://example.com/linkB{i}" for i in range(1, 11)],
                "Publish Date": ["" for _ in range(10)],
                "Keywords": ["" for _ in range(10)],
            })
            df2.to_csv(self.batch_file_2, index=False, encoding="utf-8-sig")

            self.campaign_id = "repop_camp_999"
            self.card_name = "Decor Campaign"

            self.b1 = Batch(
                account_id=self.account_id,
                campaign_id=self.campaign_id,
                filename=self.batch_filename_1,
                original_filename=self.card_name,
                pin_count=10,
                status=BatchStatus.PENDING,
            )
            self.b2 = Batch(
                account_id=self.account_id,
                campaign_id=self.campaign_id,
                filename=self.batch_filename_2,
                original_filename=self.card_name,
                pin_count=10,
                status=BatchStatus.PENDING,
            )
            session.add_all([self.b1, self.b2])
            session.commit()
            session.refresh(self.b1)
            session.refresh(self.b2)
            self.b1_id = self.b1.id
            self.b2_id = self.b2.id

    def tearDown(self):
        with self.factory() as session:
            session.query(Batch).filter(Batch.account_id == self.account_id).delete()
            session.query(ActivityLog).filter(ActivityLog.account_id == self.account_id).delete()
            session.query(Account).filter(Account.id == self.account_id).delete()
            session.commit()

        if self.batch_file_1.exists():
            self.batch_file_1.unlink(missing_ok=True)
        if self.batch_file_2.exists():
            self.batch_file_2.unlink(missing_ok=True)

    def test_recalculate_single_batch_publish_dates(self):
        target_start_date = "2026-11-01"
        payload = {
            "start_date": target_start_date,
            "daily_start": "09:00",
            "daily_end": "18:00",
            "pins_per_day": 5,
        }
        res = self.client.post(f"/api/history/{self.b1_id}/recalculate-publish-dates", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("pins_updated"), 10)

        # Check updated CSV file
        df = pd.read_csv(self.batch_file_1)
        pub_dates = df["Publish Date"].dropna().tolist()
        self.assertEqual(len(pub_dates), 10)
        
        # 10 pins at 5 pins/day spans 2 days: 2026-11-01 and 2026-11-02
        self.assertTrue(pub_dates[0].startswith("2026-11-01T09:00"))
        self.assertTrue(pub_dates[4].startswith("2026-11-01T18:00"))
        self.assertTrue(pub_dates[5].startswith("2026-11-02T09:00"))
        self.assertTrue(pub_dates[9].startswith("2026-11-02T18:00"))

    def test_recalculate_campaign_all_publish_dates(self):
        target_start_date = "2026-12-01"
        payload = {
            "account_id": self.account_id,
            "campaign_id": self.campaign_id,
            "start_date": target_start_date,
            "daily_start": "08:00",
            "daily_end": "20:00",
            "pins_per_day": 10,
        }
        res = self.client.post("/api/history/campaign/recalculate-all-publish-dates", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("batches_updated"), 2)
        self.assertEqual(data.get("total_pins"), 20)

        # Batch 1 (10 pins) should take day 1 (2026-12-01)
        df1 = pd.read_csv(self.batch_file_1)
        b1_dates = df1["Publish Date"].tolist()
        self.assertEqual(len(b1_dates), 10)
        self.assertTrue(b1_dates[0].startswith("2026-12-01T08:00"))
        self.assertTrue(b1_dates[-1].startswith("2026-12-01T20:00"))

        # Batch 2 (10 pins) should take day 2 (2026-12-02)
        df2 = pd.read_csv(self.batch_file_2)
        b2_dates = df2["Publish Date"].tolist()
        self.assertEqual(len(b2_dates), 10)
        self.assertTrue(b2_dates[0].startswith("2026-12-02T08:00"))
        self.assertTrue(b2_dates[-1].startswith("2026-12-02T20:00"))
