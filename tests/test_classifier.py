from sorter.classifier import extension_of, match_type, match_category, classify
from sorter.config import Config


def make_config():
    return Config(
        downloads_path="X:/dummy",
        categories={
            "Учёба": ["класс", "демоверсия", "задач"],
            "Игры": ["roblox", "steam"],
        },
        type_map={
            "Documents": ["pdf", "docx", "txt"],
            "Installers": ["exe", "msi"],
        },
        managed_folders=["Учёба", "Игры", "Others"],
        ignore=["*.crdownload", "desktop.ini"],
    )


# --- extension_of ---

def test_extension_lowercased_without_dot():
    assert extension_of("Photo.PNG") == "png"


def test_extension_empty_when_none():
    assert extension_of("README") == ""


# --- match_type ---

def test_type_from_map():
    cfg = make_config()
    assert match_type("pdf", cfg.type_map) == "Documents"


def test_type_misc_when_unknown():
    cfg = make_config()
    assert match_type("xyz", cfg.type_map) == "Misc"


# --- match_category ---

def test_category_by_filename():
    cfg = make_config()
    assert match_category("Информатика демоверсия 9.pdf", "", cfg.categories) == "Учёба"


def test_category_case_insensitive():
    cfg = make_config()
    assert match_category("SteamSetup.exe", "", cfg.categories) == "Игры"


def test_category_by_content_when_name_has_nothing():
    cfg = make_config()
    assert match_category("file.txt", "тут есть слово задача внутри", cfg.categories) == "Учёба"


def test_category_none_when_no_match():
    cfg = make_config()
    assert match_category("random.bin", "", cfg.categories) is None


# --- classify (full triple) ---

def test_classify_returns_category_type_extension():
    cfg = make_config()
    assert classify("Информатика демоверсия 9.pdf", "", cfg) == ("Учёба", "Documents", "pdf")


def test_classify_falls_back_to_others_category():
    cfg = make_config()
    assert classify("mystery.exe", "", cfg) == ("Others", "Installers", "exe")


# --- overrides ---

def test_override_beats_keyword():
    cfg = make_config()
    cfg.overrides = {"SteamSetup.exe": "Программы"}
    # без override это были бы "Игры"; override побеждает
    assert classify("SteamSetup.exe", "", cfg)[0] == "Программы"


def test_override_used_when_no_keyword_match():
    cfg = make_config()
    cfg.overrides = {"mystery.exe": "Код"}
    assert classify("mystery.exe", "", cfg)[0] == "Код"


def test_no_override_falls_through_to_keyword():
    cfg = make_config()
    cfg.overrides = {"other.pdf": "Игры"}
    assert classify("Информатика демоверсия 9.pdf", "", cfg)[0] == "Учёба"
