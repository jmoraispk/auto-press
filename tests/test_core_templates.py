import pytest

from press_core import best_run_match


def test_best_run_match_selects_highest_scoring_template():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")

    region = np.zeros((12, 12), dtype=np.uint8)
    region[5:8, 7:10] = 255

    tpl_low = np.zeros((3, 3), dtype=np.uint8)
    tpl_low[0, 0] = 255
    tpl_high = np.full((3, 3), 255, dtype=np.uint8)

    score, center = best_run_match(region, [tpl_low, tpl_high])
    assert score > 0.9
    assert center is not None
