from pathlib import Path

from src import state


def test_roundtrip(tmp_path: Path):
    s = state.State()
    assert s.mark_seen("x:elonmusk", "111") is True
    assert s.mark_seen("x:elonmusk", "111") is False
    state.save(s, tmp_path)

    loaded = state.load(tmp_path)
    assert loaded.seen_posts["x:elonmusk"] == ["111"]


def test_seen_cap(tmp_path: Path):
    s = state.State()
    for i in range(state.State.MAX_SEEN_PER_ACCOUNT + 50):
        s.mark_seen("x:someone", str(i))
    assert len(s.seen_posts["x:someone"]) == state.State.MAX_SEEN_PER_ACCOUNT
    # oldest ids should have been trimmed
    assert "0" not in s.seen_posts["x:someone"]


def test_load_missing(tmp_path: Path):
    s = state.load(tmp_path / "does-not-exist")
    assert s.seen_posts == {}
    assert s.pending_hits == []


def test_load_corrupt(tmp_path: Path):
    (tmp_path / "seen_posts.json").write_text("not-json{")
    s = state.load(tmp_path)
    assert s.seen_posts == {}
