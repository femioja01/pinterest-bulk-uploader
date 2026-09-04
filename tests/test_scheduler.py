import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.database import Base, Account, Batch, BatchStatus
from src.services.scheduler import run_single_account


class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        with self.Session() as session:
            acc = Account(name="test_account", enabled=True, session_valid=True)
            session.add(acc)
            session.commit()
            self.account_id = acc.id

    @patch("src.services.scheduler.check_session", return_value=True)
    @patch("src.services.scheduler.upload_csv_to_pinterest", return_value=(True, ""))
    @patch("src.services.scheduler.ensure_account_dirs")
    def test_run_single_account_skips_future_batches(self, mock_dirs, mock_upload, mock_session):
        mock_dirs.return_value = {
            "queue": MagicMock(exists=lambda: True, __truediv__=lambda s, x: MagicMock(exists=lambda: True, rename=MagicMock())),
            "done": MagicMock(),
            "failed": MagicMock(),
        }

        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        future_batch = Batch(
            account_id=self.account_id,
            filename="test_batch_future.csv",
            status=BatchStatus.PENDING,
            scheduled_upload_at=tomorrow,
        )
        with self.Session() as session:
            session.add(future_batch)
            session.commit()

        # Patch get_session_factory to return self.Session
        with patch("src.services.scheduler.get_session_factory", return_value=self.Session):
            # Run without force_future (default cron behavior)
            run_single_account("test_account", max_batches=1, force_future=False)

        # Batch should NOT be processed
        with self.Session() as session:
            b = session.query(Batch).filter(Batch.filename == "test_batch_future.csv").first()
            self.assertEqual(b.status, BatchStatus.PENDING)
            self.assertIsNone(b.uploaded_at)

        mock_upload.assert_not_called()

    @patch("src.services.scheduler.check_session", return_value=True)
    @patch("src.services.scheduler.upload_csv_to_pinterest", return_value=(True, ""))
    @patch("src.services.scheduler.ensure_account_dirs")
    def test_run_single_account_picks_due_and_untimed_batches(self, mock_dirs, mock_upload, mock_session):
        mock_file = MagicMock()
        mock_file.exists.return_value = True
        mock_dirs.return_value = {
            "queue": MagicMock(__truediv__=lambda s, x: mock_file),
            "done": MagicMock(__truediv__=lambda s, x: MagicMock()),
            "failed": MagicMock(__truediv__=lambda s, x: MagicMock()),
        }

        past = datetime.now(timezone.utc) - timedelta(hours=1)
        due_batch = Batch(
            account_id=self.account_id,
            filename="test_batch_due.csv",
            status=BatchStatus.PENDING,
            scheduled_upload_at=past,
        )
        with self.Session() as session:
            session.add(due_batch)
            session.commit()

        with patch("src.services.scheduler.get_session_factory", return_value=self.Session):
            run_single_account("test_account", max_batches=1, force_future=False)

        with self.Session() as session:
            b = session.query(Batch).filter(Batch.filename == "test_batch_due.csv").first()
            self.assertEqual(b.status, BatchStatus.DONE)

        mock_upload.assert_called_once()

    @patch("src.services.scheduler.check_session", return_value=True)
    @patch("src.services.scheduler.upload_csv_to_pinterest", return_value=(True, ""))
    @patch("src.services.scheduler.ensure_account_dirs")
    def test_run_single_account_force_future(self, mock_dirs, mock_upload, mock_session):
        mock_file = MagicMock()
        mock_file.exists.return_value = True
        mock_dirs.return_value = {
            "queue": MagicMock(__truediv__=lambda s, x: mock_file),
            "done": MagicMock(__truediv__=lambda s, x: MagicMock()),
            "failed": MagicMock(__truediv__=lambda s, x: MagicMock()),
        }

        future = datetime.now(timezone.utc) + timedelta(days=3)
        future_batch = Batch(
            account_id=self.account_id,
            filename="test_batch_forced.csv",
            status=BatchStatus.PENDING,
            scheduled_upload_at=future,
        )
        with self.Session() as session:
            session.add(future_batch)
            session.commit()

        with patch("src.services.scheduler.get_session_factory", return_value=self.Session):
            # Explicit manual force_future=True
            run_single_account("test_account", max_batches=1, force_future=True)

        with self.Session() as session:
            b = session.query(Batch).filter(Batch.filename == "test_batch_forced.csv").first()
            self.assertEqual(b.status, BatchStatus.DONE)

        mock_upload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
