from pathlib import Path

from sorter.util import rel_to


def test_rel_to_inside_root():
    root = Path("C:/Downloads")
    assert rel_to(Path("C:/Downloads/3D/bear.blend"), root) == str(Path("3D/bear.blend"))


def test_rel_to_outside_root_returns_absolute():
    root = Path("C:/Downloads")
    p = Path("C:/Drive/Alexey/All_3d/model.obj")
    assert rel_to(p, root) == str(p)
