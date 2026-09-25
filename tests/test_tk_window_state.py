"""Размер и место окна Tkinter между запусками (tk_window_state.py).

Правило restore переписано с window-state.js калькуляторов и Paint Pro; главное в нём -
не «запомнило ли», а «что бы ни лежало в файле, окно откроется там, где его видно и можно
взять за заголовок». Если рядом лежат калькуляторы и есть node, ответы Python сверяются с
ответами самого window-state.js на тысяче случайных входов.
"""
import json
import random
import shutil
import subprocess
import tkinter as tk
from pathlib import Path

import pytest
from hypothesis import given, strategies as st

from sorter import tk_window_state as ws

OPTS = {"width": 760, "height": 520, "minWidth": 620, "minHeight": 420}
FULL_HD = {"x": 0, "y": 0, "width": 1920, "height": 1040}
RIGHT = {"x": 1920, "y": 0, "width": 2560, "height": 1400}


def test_no_file_gives_the_default_size_centred():
    assert ws.restore(None, [FULL_HD], OPTS) == {"width": 760, "height": 520, "maximized": False}
    assert ws.geometry_text({"width": 760, "height": 520}, [FULL_HD]) == "760x520+580+260"


def test_a_window_on_screen_comes_back_exactly():
    saved = {"x": 100, "y": 50, "width": 900, "height": 700, "maximized": False}
    assert ws.restore(saved, [FULL_HD], OPTS) == saved


def test_an_unplugged_monitor_centres_the_window_keeping_its_size():
    saved = {"x": 2500, "y": 100, "width": 900, "height": 700, "maximized": True}
    assert ws.restore(saved, [FULL_HD, RIGHT], OPTS) == saved
    assert ws.restore(saved, [FULL_HD], OPTS) == {"width": 900, "height": 700, "maximized": True}


def test_too_small_grows_too_large_shrinks_half_off_moves_back():
    assert ws.restore({"width": 10, "height": 10}, [FULL_HD], OPTS)["width"] == 620
    assert ws.restore({"x": 0, "y": 0, "width": 9000, "height": 9000}, [FULL_HD], OPTS) == \
        {"x": 0, "y": 0, "width": 1920, "height": 1040, "maximized": False}
    assert ws.restore({"x": 1800, "y": 900, "width": 900, "height": 600}, [FULL_HD], OPTS) == \
        {"x": 1020, "y": 440, "width": 900, "height": 600, "maximized": False}


junk_number = st.one_of(st.none(), st.just(float("nan")), st.just(float("inf")), st.text(max_size=3),
                        st.booleans(), st.integers(-20000, 20000), st.floats(-5e4, 5e4))
junk = st.one_of(st.none(), st.integers(), st.text(max_size=5), st.lists(st.integers(), max_size=2),
                 st.fixed_dictionaries({}, optional={"x": junk_number, "y": junk_number, "width": junk_number,
                                                     "height": junk_number, "maximized": st.booleans()}))


@st.composite
def screens(draw):
    out, x = [], draw(st.integers(-5000, 5000))
    for _ in range(draw(st.integers(1, 3))):
        a = {"x": x, "y": draw(st.integers(-3000, 3000)), "width": draw(st.integers(640, 4000)),
             "height": draw(st.integers(480, 2500))}
        out.append(a)
        x += a["width"]
    return out


@given(saved=junk, areas=screens())
def test_whatever_is_in_the_file_the_title_bar_is_on_a_screen(saved, areas):
    w = ws.restore(saved, areas, OPTS)
    assert isinstance(w["width"], int) and isinstance(w["height"], int)
    assert w["width"] >= 620 and w["height"] >= 420
    assert ("x" in w) == ("y" in w)
    if "x" in w:
        assert any(w["x"] >= a["x"] and w["y"] >= a["y"] and w["x"] < a["x"] + a["width"]
                   and w["y"] + ws.GRIP_HEIGHT <= a["y"] + a["height"] for a in areas)
        assert ws.restore(w, areas, OPTS) == w  # повтор ничего не меняет
        assert ws.parse_geometry(ws.geometry_text(w, areas)) == {k: w[k] for k in ("x", "y", "width", "height")}


def test_parse_geometry_takes_negative_positions_and_rejects_junk():
    assert ws.parse_geometry("800x600+-1900+-4") == {"x": -1900, "y": -4, "width": 800, "height": 600}
    assert ws.parse_geometry("800x600-10+20") == {"x": -10, "y": 20, "width": 800, "height": 600}
    for bad in ["", None, "800x600", "axb+1+2", "800x600+1"]:
        assert ws.parse_geometry(bad) is None


def test_the_file_is_written_through_a_temporary_and_junk_reads_as_nothing(tmp_path):
    f = tmp_path / "sub" / "window.json"
    assert ws.save(f, {"x": 1, "y": 2, "width": 700, "height": 500, "maximized": False})
    assert ws.load(f)["width"] == 700
    assert not (tmp_path / "sub" / "window.json.tmp").exists()
    for text in ["", "{", "null", "\x00\xff"]:
        f.write_text(text, encoding="latin-1")
        assert ws.restore(ws.load(f), [FULL_HD], OPTS) == {"width": 760, "height": 520, "maximized": False}


def test_windows_reports_at_least_one_work_area():
    areas = ws.work_areas()
    assert areas and all(a["width"] > 0 and a["height"] > 0 for a in areas)


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError as e:  # нет экрана
        pytest.skip(str(e))
    yield r
    r.destroy()


def test_a_real_tk_window_comes_back_where_it_was_closed(root, tmp_path):
    area = ws.work_areas(root)[0]
    f = tmp_path / "window.json"
    first = ws.Remember(root, f, OPTS, key="tk")
    want = f"700x480+{area['x'] + 40}+{area['y'] + 60}"
    root.geometry(want)
    root.update()
    assert first.save()
    data = json.loads(f.read_text(encoding="utf-8"))
    assert data["tk"] == {"x": area["x"] + 40, "y": area["y"] + 60, "width": 700, "height": 480, "maximized": False}
    # Ключ другого окна (у SortProgramm - PyQt) при записи не пропадает.
    f.write_text(json.dumps({**data, "qt": "AAAA"}), encoding="utf-8")
    first.save()
    assert json.loads(f.read_text(encoding="utf-8"))["qt"] == "AAAA"

    second = tk.Toplevel(root)
    ws.Remember(second, f, OPTS, key="tk")
    second.update()
    assert ws.parse_geometry(second.wm_geometry()) == {k: data["tk"][k] for k in ("x", "y", "width", "height")}


JS = Path(__file__).resolve().parents[2] / "Calculators" / "calcpro-glass" / "window-state.js"


def test_python_answers_match_window_state_js():
    node = shutil.which("node")
    if not node or not JS.exists():
        pytest.skip("нет node или калькуляторов рядом (CI)")
    rnd = random.Random(20260925)
    pick = [lambda: None, lambda: "x", lambda: rnd.uniform(-5e4, 5e4), lambda: rnd.randint(-20000, 20000),
            lambda: rnd.randint(-100, 5000), lambda: rnd.randint(0, 4000) + 0.5]
    cases = []
    for _ in range(1000):
        areas, x = [], rnd.randint(-5000, 5000)
        for _ in range(rnd.randint(1, 3)):
            a = {"x": x, "y": rnd.randint(-3000, 3000), "width": rnd.randint(640, 4000), "height": rnd.randint(480, 2500)}
            areas.append(a)
            x += a["width"]
        saved = {k: rnd.choice(pick)() for k in ("x", "y", "width", "height")}
        saved["maximized"] = rnd.random() < 0.5
        cases.append({"saved": saved, "areas": areas})
    script = ("const ws=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{"
              "const o=JSON.parse(s);console.log(JSON.stringify(o.cases.map(c=>ws.restore(c.saved,c.areas,o.opts))));});")
    out = subprocess.run([node, "-e", script, str(JS)], input=json.dumps({"cases": cases, "opts": OPTS}),
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    for case, want in zip(cases, json.loads(out)):
        assert ws.restore(case["saved"], case["areas"], OPTS) == want, case
