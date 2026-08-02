"""Классификация по регулярным выражениям — то, что не выразить подстрокой."""
from sorter.classifier import classify, match_pattern
from sorter.config import Config

RENDER = r"^\d{4}-\d{4}(\s*\(\d+\))?\.(mp4|mkv|avi|webm|mov|png|jpg|jpeg|exr)$"
SHOT = [r"^photo_\d{4}-\d{2}-\d{2}", r"^image\d{14}", r"^(снимок экрана|screenshot)"]


def make_config():
    return Config(
        downloads_path="X:/dummy",
        categories={"Медиа": ["music", "клип"], "Программы": ["setup"]},
        patterns={"3D": [RENDER], "Скриншоты": SHOT},
        type_map={"Videos": ["mp4"], "Images": ["png", "jpg"], "Installers": ["exe"]},
        fallback_category="Others",
        fallback_type="Misc",
    )


def test_blender_render_range_goes_to_3d():
    """`0001-0250.mp4` — диапазон кадров, вывод рендера Blender, а не фильм."""
    assert match_pattern("0001-0250.mp4", {"3D": [RENDER]}) == "3D"


def test_render_with_dedup_suffix_still_matches():
    assert match_pattern("0001-0250 (3).mp4", {"3D": [RENDER]}) == "3D"


def test_plain_video_is_not_a_render():
    assert match_pattern("Матрица 1999.mp4", {"3D": [RENDER]}) is None


def test_short_number_is_not_a_render():
    """`0619.mp3` — четыре цифры без диапазона, это трек."""
    assert match_pattern("0619.mp3", {"3D": [RENDER]}) is None


def test_telegram_photo_is_a_screenshot():
    assert match_pattern("photo_2026-05-23_20-14-35.jpg", {"Скриншоты": SHOT}) == "Скриншоты"


def test_russian_screenshot_name():
    assert match_pattern("Снимок экрана 2026-05-28 185311.png", {"Скриншоты": SHOT}) == "Скриншоты"


def test_broken_regex_is_skipped_not_raised():
    """Опечатка в config.json не должна ронять всю сортировку."""
    assert match_pattern("a.mp4", {"3D": ["(("], "Медиа": [r"\.mp4$"]}) == "Медиа"


def test_pattern_beats_keyword():
    """Слово в категории не должно перебивать более точную регулярку."""
    cfg = make_config()
    cfg.categories = {"Медиа": [".mp4"]}
    assert classify("0001-0250.mp4", "", cfg)[0] == "3D"


def test_override_still_beats_pattern():
    cfg = make_config()
    cfg.overrides = {"0001-0250.mp4": "Учёба"}
    assert classify("0001-0250.mp4", "", cfg)[0] == "Учёба"


def test_keyword_used_when_no_pattern_matches():
    cfg = make_config()
    assert classify("Nirvana music.mp4", "", cfg)[0] == "Медиа"
