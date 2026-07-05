"""
tests/test_notifications.py — Mixtape

Regression test for Issue #4: rating a song should notify the sharer,
the same way adding a song to a playlist does.
"""

import pytest
from app import create_app, db
from models import User, Song
from services.notification_service import rate_song, get_notifications


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def seed_users_and_song(app):
    """Create a sharer, a rater, and a song shared by the sharer."""
    with app.app_context():
        sharer = User(username="sharer", email="sharer@example.com")
        rater = User(username="rater", email="rater@example.com")
        db.session.add_all([sharer, rater])
        db.session.flush()

        song = Song(
            title="Test Song", artist="Test Artist",
            genre="test", shared_by=sharer.id
        )
        db.session.add(song)
        db.session.commit()

        yield {"sharer": sharer, "rater": rater, "song": song}


def test_rating_a_song_notifies_the_sharer(app, seed_users_and_song):
    """
    Regression test for Issue #4.

    When a user rates a song shared by someone else, the sharer should
    receive a 'song_rated' notification — mirroring the notification
    already sent when a song is added to a playlist.
    """
    with app.app_context():
        sharer = seed_users_and_song["sharer"]
        rater = seed_users_and_song["rater"]
        song = seed_users_and_song["song"]

        before = get_notifications(sharer.id)
        assert len(before) == 0

        rate_song(rater.id, song.id, 5)

        after = get_notifications(sharer.id)
        assert len(after) == 1
        assert after[0]["type"] == "song_rated"
        assert rater.username in after[0]["body"]
        assert song.title in after[0]["body"]


def test_rating_your_own_song_does_not_notify_yourself(app, seed_users_and_song):
    """
    A user rating their own shared song should not receive a
    notification about their own action.
    """
    with app.app_context():
        sharer = seed_users_and_song["sharer"]
        song = seed_users_and_song["song"]

        before = get_notifications(sharer.id)
        assert len(before) == 0

        rate_song(sharer.id, song.id, 4)

        after = get_notifications(sharer.id)
        assert len(after) == 0