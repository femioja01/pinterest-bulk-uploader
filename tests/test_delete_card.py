import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from src.app import app
from src.models.database import get_session_factory, Account, Batch, BatchStatus, ActivityLog
from src.config import get_account_dir

class TestDeleteCard(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.factory = get_session_factory()
        with self.factory() as session:
            # Create a test account
            self.account = session.query(Account).filter(Account.name == "delete_test_acc").first()
            if not self.account:
                self.account = Account(name="delete_test_acc", enabled=True)
                session.add(self.account)
                session.commit()
                session.refresh(self.account)
            self.account_id = self.account.id
            self.account_name = self.account.name

            # Clean up any existing batches for this account
            session.query(Batch).filter(Batch.account_id == self.account_id).delete()
            session.commit()

            # Create test batches under an initial card name
            self.b1 = Batch(
                account_id=self.account_id,
                filename="batch_delete_001.csv",
                original_filename="Completed Campaign Alpha.csv",
                campaign_id="camp_delete_alpha",
                pin_count=15,
                status=BatchStatus.DONE,
            )
            self.b2 = Batch(
                account_id=self.account_id,
                filename="batch_delete_002.csv",
                original_filename="Completed Campaign Alpha.csv",
                campaign_id="camp_delete_alpha",
                pin_count=20,
                status=BatchStatus.DONE,
            )
            session.add_all([self.b1, self.b2])
            session.commit()
            session.refresh(self.b1)
            session.refresh(self.b2)
            self.b1_id = self.b1.id
            self.b2_id = self.b2.id

        # Create dummy batch files and master file on disk
        dirs = get_account_dir(self.account_name)
        (dirs / "done").mkdir(parents=True, exist_ok=True)
        self.f1 = dirs / "done" / "batch_delete_001.csv"
        self.f2 = dirs / "done" / "batch_delete_002.csv"
        self.master_f = dirs / "done" / "master_Completed Campaign Alpha.csv"
        self.f1.write_text("pin data 1")
        self.f2.write_text("pin data 2")
        self.master_f.write_text("master pin data")

    def tearDown(self):
        with self.factory() as session:
            session.query(Batch).filter(Batch.account_id == self.account_id).delete()
            session.query(ActivityLog).filter(ActivityLog.account_id == self.account_id).delete()
            session.query(Account).filter(Account.id == self.account_id).delete()
            session.commit()

        dirs = get_account_dir("delete_test_acc")
        for f in [self.f1, self.f2, self.master_f]:
            if f.exists():
                f.unlink(missing_ok=True)

    def test_delete_group_by_batch_ids(self):
        payload = {
            "account_id": self.account_id,
            "card_name": "Completed Campaign Alpha.csv",
            "batch_ids": [self.b1_id, self.b2_id]
        }
        response = self.client.post("/api/history/delete-group", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("Completed Campaign Alpha", data.get("message"))

        # Verify DB deletion
        with self.factory() as session:
            batches = session.query(Batch).filter(Batch.id.in_([self.b1_id, self.b2_id])).all()
            self.assertEqual(len(batches), 0)

            # Check ActivityLog
            log = session.query(ActivityLog).filter(
                ActivityLog.account_id == self.account_id,
                ActivityLog.event_type == "card_deleted"
            ).first()
            self.assertIsNotNone(log)

        # Verify disk file cleanup
        self.assertFalse(self.f1.exists())
        self.assertFalse(self.f2.exists())
        self.assertFalse(self.master_f.exists())

    def test_delete_group_by_campaign_id(self):
        payload = {
            "account_id": self.account_id,
            "campaign_id": "camp_delete_alpha"
        }
        response = self.client.post("/api/history/delete-group", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))

        with self.factory() as session:
            batches = session.query(Batch).filter(Batch.id.in_([self.b1_id, self.b2_id])).all()
            self.assertEqual(len(batches), 0)

    def test_delete_group_by_card_name(self):
        payload = {
            "account_id": self.account_id,
            "original_filename": "Completed Campaign Alpha.csv"
        }
        response = self.client.post("/api/history/delete-group", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))

        with self.factory() as session:
            batches = session.query(Batch).filter(Batch.id.in_([self.b1_id, self.b2_id])).all()
            self.assertEqual(len(batches), 0)

    def test_delete_group_missing_params(self):
        response = self.client.post("/api/history/delete-group", json={})
        self.assertEqual(response.status_code, 400)

    def test_delete_group_not_found(self):
        response = self.client.post("/api/history/delete-group", json={"batch_ids": [999999, 999998]})
        self.assertEqual(response.status_code, 404)

    def test_delete_single_batch_in_archive(self):
        response = self.client.post(f"/api/history/{self.b1_id}/delete")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))

        # Verify b1 deleted but b2 still exists
        with self.factory() as session:
            b1 = session.query(Batch).filter(Batch.id == self.b1_id).first()
            b2 = session.query(Batch).filter(Batch.id == self.b2_id).first()
            self.assertIsNone(b1)
            self.assertIsNotNone(b2)

        self.assertFalse(self.f1.exists())
        self.assertTrue(self.f2.exists())
