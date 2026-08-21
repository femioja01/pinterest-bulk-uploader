import tempfile
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.database import Base, Account, Batch, BatchStatus, Setting, _seed_defaults, get_setting, set_setting


def test_database_models_and_settings(tmp_path):
    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        _seed_defaults(session)
        
        # Verify defaults seeded
        assert get_setting(session, "upload_schedule") == "0 9 * * *"
        assert get_setting(session, "default_batch_size") == "50"

        # Update setting
        set_setting(session, "upload_schedule", "0 12 * * *")
        assert get_setting(session, "upload_schedule") == "0 12 * * *"

        # Create account
        acc = Account(name="fitnigeriana", batch_size=50, enabled=True)
        session.add(acc)
        session.commit()

        assert acc.id is not None
        assert acc.session_valid is False

        # Add batch
        batch = Batch(account_id=acc.id, filename="batch_001.csv", pin_count=50, status=BatchStatus.PENDING)
        session.add(batch)
        session.commit()

        assert batch.id is not None
        assert len(acc.batches) == 1
        assert acc.batches[0].filename == "batch_001.csv"
