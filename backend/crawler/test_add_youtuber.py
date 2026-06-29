"""
Unit tests for add_youtuber — verify it targets the real repo config.

Run via:
    make test   (included in the crawler suite)
"""
from scripts.add_youtuber import CONFIG, _normalise


def test_config_path_points_to_repo_config():
    # Regression: the script lives in backend/crawler/scripts/, so the repo root
    # is parents[3]. A wrong level resolved to backend/config/config.yaml, which
    # does not exist, and `make add-youtuber` crashed with FileNotFoundError.
    assert CONFIG.name == 'config.yaml'
    assert CONFIG.parent.name == 'config'
    assert CONFIG.exists()


def test_normalise_handle_forms():
    assert _normalise('@NateBJones') == 'https://www.youtube.com/@NateBJones'
    assert _normalise('NateBJones') == 'https://www.youtube.com/@NateBJones'
    assert _normalise('https://www.youtube.com/@NateBJones/featured') == \
        'https://www.youtube.com/@NateBJones/featured'
    assert _normalise('UC0C-17n9iuUQPylguM1d-lQ') == \
        'https://www.youtube.com/channel/UC0C-17n9iuUQPylguM1d-lQ'
