import unittest
import os
import io
import tempfile
import pandas as pd
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from src.app import app
from src.models.database import get_session_factory, Account, Batch, BatchStatus, ActivityLog

class TestAppendToCard(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.factory = get_session_factory()
        with self.factory() as session:
            # Create a test account
            self.account = session.query(Account).filter(Account.name == "append_test_acc").first()
            if not self.account:
                self.account = Account(name="append_test_acc", enabled=True)
                session.add(self.account)
                session.commit()
                session.refresh(self.account)
            self.account_id = self.account.id

            # Clean up any existing batches for this test account
            session.query(Batch).filter(Batch.account_id == self.account_id).delete()
            session.commit()

            # Create an existing campaign with 2 batches
            self.campaign_id = "test_camp_append_123"
            self.card_name = "Spring Cleaning Pins"
            self.now = datetime.utcnow()
            
            self.b1 = Batch(
                account_id=self.account_id,
                campaign_id=self.campaign_id,
                filename="batch_001.csv",
                original_filename=self.card_name,
                pin_count=50,
                status=BatchStatus.DONE,
                scheduled_upload_at=self.now - timedelta(days=2),
                uploaded_at=self.now - timedelta(days=2),
            )
            self.b2 = Batch(
                account_id=self.account_id,
                campaign_id=self.campaign_id,
                filename="batch_002.csv",
                original_filename=self.card_name,
                pin_count=50,
                status=BatchStatus.PENDING,
                scheduled_upload_at=self.now + timedelta(days=2),
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

    def test_append_csv_file_continuation(self):
        # 100 sample Pinterest formatted pins
        csv_rows = ["Title,Media URL,Pinterest board,Thumbnail,Description,Link,Publish date,Keywords"]
        for i in range(1, 101):
            csv_rows.append(f"Pin Title {i},https://example.com/img{i}.jpg,Home Decor,,Awesome pin {i},https://myblog.com/{i},,decor")
        csv_content = "\n".join(csv_rows).encode("utf-8")

        response = self.client.post(
            "/api/history/append-to-card",
            data={
                "account_id": self.account_id,
                "original_filename": self.card_name,
                "campaign_id": self.campaign_id,
                "batch_size": 50,
                "schedule_mode": "continue",
                "interval_spacing": "2_days",
            },
            files={"file": ("new_pins.csv", io.BytesIO(csv_content), "text/csv")}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("pins_count"), 100)
        self.assertEqual(data.get("batches_created"), 2)

        # Verify batches in DB
        with self.factory() as session:
            batches = session.query(Batch).filter(
                Batch.account_id == self.account_id,
                Batch.campaign_id == self.campaign_id
            ).order_by(Batch.id.asc()).all()
            
            # Should now have 4 batches (2 original + 2 appended)
            self.assertEqual(len(batches), 4)
            self.assertTrue(batches[2].filename.endswith("_003.csv"))
            self.assertTrue(batches[3].filename.endswith("_004.csv"))
            self.assertEqual(batches[2].original_filename, self.card_name)
            self.assertEqual(batches[3].original_filename, self.card_name)
            self.assertEqual(batches[2].pin_count, 50)
            self.assertEqual(batches[3].pin_count, 50)

            # Scheduled times should continue after b2 (which was now + 2 days)
            # b3 should be now + 4 days, b4 should be now + 6 days
            diff_b3 = (batches[2].scheduled_upload_at - self.b2.scheduled_upload_at).total_seconds()
            self.assertAlmostEqual(diff_b3, 2 * 86400, delta=60)

    def test_append_pasted_data_custom_schedule(self):
        # Pasted raw pins
        lines = ["Title\tMedia URL\tPinterest board\tThumbnail\tDescription\tLink\tPublish date\tKeywords"]
        for i in range(1, 31):
            lines.append(f"Pasted Pin {i}\thttps://example.com/p{i}.png\tGarden\t\tLovely garden {i}\thttps://myblog.com/garden\t\tgardening")
        paste_text = "\n".join(lines)

        custom_time = (datetime.utcnow() + timedelta(days=10)).strftime("%Y-%m-%dT14:00")

        response = self.client.post(
            "/api/history/append-to-card",
            data={
                "account_id": self.account_id,
                "original_filename": self.card_name,
                "campaign_id": self.campaign_id,
                "batch_size": 15,
                "schedule_mode": "custom",
                "custom_start_time": custom_time,
                "interval_spacing": "1_days",
                "paste_data": paste_text,
            }
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("pins_count"), 30)
        self.assertEqual(data.get("batches_created"), 2)

        with self.factory() as session:
            batches = session.query(Batch).filter(
                Batch.account_id == self.account_id,
                Batch.campaign_id == self.campaign_id
            ).order_by(Batch.id.asc()).all()
            self.assertEqual(len(batches), 4)
            self.assertTrue(batches[2].filename.endswith("_003.csv"))
            self.assertTrue(batches[3].filename.endswith("_004.csv"))
            self.assertEqual(batches[2].pin_count, 15)
            self.assertEqual(batches[3].pin_count, 15)

            # Check custom start time applied (custom_time was passed in local time e.g. Africa/Lagos UTC+1, stored in UTC)
            expected_utc = (datetime.strptime(custom_time, "%Y-%m-%dT%H:%M") - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
            self.assertEqual(batches[2].scheduled_upload_at.strftime("%Y-%m-%d %H:%M"), expected_utc)
