"""State layer: single-use confirmations and reminder dedupe."""

from __future__ import annotations

import datetime as dt

import pytest

from app import db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


class TestPendingConfirmations:
    def test_round_trip(self, conn):
        token = db.store_pending(conn, {"title": "Acme sync"}, chat_id=1)
        assert db.take_pending(conn, token) == {"title": "Acme sync"}

    def test_pending_is_single_use(self, conn):
        """Prevents a double-tap on Confirm creating the meeting twice."""
        token = db.store_pending(conn, {"title": "Acme sync"})
        assert db.take_pending(conn, token) is not None
        assert db.take_pending(conn, token) is None

    def test_expired_pending_is_refused(self, conn):
        token = db.store_pending(conn, {"title": "stale"}, ttl_minutes=-1)
        assert db.take_pending(conn, token) is None

    def test_unknown_token_is_refused(self, conn):
        assert db.take_pending(conn, "not-a-real-token") is None

    def test_purge_removes_only_expired(self, conn):
        fresh = db.store_pending(conn, {"a": 1}, ttl_minutes=30)
        db.store_pending(conn, {"b": 2}, ttl_minutes=-1)
        assert db.purge_expired(conn) == 1
        assert db.take_pending(conn, fresh) == {"a": 1}


class TestReminderDedupe:
    def test_first_claim_succeeds(self, conn):
        when = dt.datetime(2026, 9, 24, 15, tzinfo=dt.timezone.utc)
        assert db.claim_reminder(conn, "evt1", when, 60) is True

    def test_second_claim_is_refused(self, conn):
        """This is what stops a service restart re-sending every reminder."""
        when = dt.datetime(2026, 9, 24, 15, tzinfo=dt.timezone.utc)
        db.claim_reminder(conn, "evt1", when, 60)
        assert db.claim_reminder(conn, "evt1", when, 60) is False

    def test_different_offsets_are_independent(self, conn):
        """A 24h and a 1h reminder for the same meeting are both legitimate."""
        when = dt.datetime(2026, 9, 24, 15, tzinfo=dt.timezone.utc)
        assert db.claim_reminder(conn, "evt1", when, 1440) is True
        assert db.claim_reminder(conn, "evt1", when, 60) is True

    def test_different_occurrences_of_a_series_are_independent(self, conn):
        """Each occurrence of a recurring meeting gets its own reminder."""
        first = dt.datetime(2026, 9, 24, 15, tzinfo=dt.timezone.utc)
        second = dt.datetime(2026, 10, 1, 15, tzinfo=dt.timezone.utc)
        assert db.claim_reminder(conn, "evt1", first, 60) is True
        assert db.claim_reminder(conn, "evt1", second, 60) is True

    def test_old_records_can_be_pruned(self, conn):
        old = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
        db.claim_reminder(conn, "evt1", old, 60)
        assert db.forget_reminders_before(conn, dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc)) == 1
