from PIL import Image
from app.fixtures.renderer import render_case_documents
from app.fixtures.loader import load_cases
from tests.conftest import REPO_ROOT

CASES = {c["case_id"]: c for c in load_cases(str(REPO_ROOT / "test_cases.json"))}

def test_renders_all_cases(tmp_path):
    for cid, case in CASES.items():
        paths = render_case_documents(case, str(tmp_path / cid))
        assert len(paths) == len(case["input"]["documents"])
        for p in paths.values():
            img = Image.open(p); assert img.width >= 700 and img.height >= 700

def test_tc002_bill_is_blurred_tc003_names_differ(tmp_path):
    paths2 = render_case_documents(CASES["TC002"], str(tmp_path / "t2"))
    from PIL import ImageFilter
    import statistics
    def edge_energy(p):
        g = Image.open(p).convert("L").filter(ImageFilter.FIND_EDGES)
        return statistics.fmean(g.getdata())
    assert edge_energy(paths2["F004"]) < edge_energy(paths2["F003"]) * 0.5
