import cv2
import numpy as np

from ai_service.local_validate import draw


def test_alarm_banner_does_not_cover_raised_hands_in_small_source():
    image = np.full((120, 160, 3), (20, 40, 80), dtype=np.uint8)
    # Pixels near the top stand in for wrists/head in a low-resolution clip.
    image[2:12, 40:120] = (0, 240, 240)
    original = image.copy()
    annotated = draw(image, [], [], [], 0.96)
    assert annotated.shape[0] > 480
    np.testing.assert_array_equal(annotated[-480:], cv2.resize(original, (640, 480)))
    np.testing.assert_array_equal(image, original)


def test_local_image_decode_supports_chinese_windows_paths(tmp_path):
    from ai_service.local_validate import media_frames
    path = tmp_path / "举手求助图片.png"
    image = np.full((60, 80, 3), (30, 80, 120), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    path.write_bytes(encoded.tobytes())
    samples = list(media_frames(path, image_seconds=0.2))
    assert len(samples) == 2
    np.testing.assert_array_equal(samples[0][2], image)
