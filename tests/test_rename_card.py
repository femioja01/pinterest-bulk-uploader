import unittest
from fastapi.testclient import TestClient
from src.app import app
from src.models.database import get_session_factory, Account, Batch, BatchStatus, ActivityLog

class TestRenameCard(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.factory = get_session_factory()
        with self.factory() as session:
            # Create a test account
            self.account = session.query(Account).filter(Account.name == "rename_test_acc").first()
            if not self.account:
                self.account = Account(name="rename_test_acc", enabled=True)
                session.add(self.account)
                session.commit()
                session.refresh(self.account)
            self.account_id = self.account.id

            # Clean up any existing batches for this account
            session.query(Batch).filter(Batch.account_id == self.account_id).delete()
            session.commit()

            # Create test batches under an initial card name
            self.b1 = Batch(
                account_id=self.account_id,
                filename="batch_test_001.csv",
                original_filename="Old Campaign Name.csv",
                pin_count=20,
                status=BatchStatus.PENDING,
            )
            self.b2 = Batch(
                account_id=self.account_id,
                filename="batch_test_002.csv",
                original_filename="Old Campaign Name.csv",
                pin_count=25,
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

    def test_rename_card_success_with_batch_ids(self):
        payload = {
            "account_id": self.account_id,
            "old_name": "Old Campaign Name.csv",
            "new_name": "Brand New Campaign",
            "batch_ids": [self.b1_id, self.b2_id]
        }
        response = self.client.post("/api/history/rename-card", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("Brand New Campaign", data.get("message"))

        # Verify database update
        with self.factory() as session:
            batches = session.query(Batch).filter(Batch.id.in_([self.b1_id, self.b2_id])).all()
            for b in batches:
                self.assertEqual(b.original_filename, "Brand New Campaign")

            # Check activity log
            log = session.query(ActivityLog).filter(
                ActivityLog.account_id == self.account_id,
                ActivityLog.event_type == "card_renamed"
            ).first()
            self.assertIsNotNone(log)
            self.assertIn("Brand New Campaign", log.message)

    def test_rename_card_empty_name_error(self):
        payload = {
            "account_id": self.account_id,
            "old_name": "Old Campaign Name.csv",
            "new_name": "   ",
            "batch_ids": [self.b1_id]
        }
        response = self.client.post("/api/history/rename-card", json=payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_rename_card_not_found(self):
        payload = {
            "account_id": self.account_id,
            "old_name": "Old Campaign Name.csv",
            "new_name": "Brand New Campaign",
            "batch_ids": [99999999]
        }
        response = self.client.post("/api/history/rename-card", json=payload)
        self.assertEqual(response.status_code, 404)
