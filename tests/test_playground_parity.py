"""The playground reimplements the classifier in JavaScript so it can run on a
static host. Two implementations of one algorithm is a lie waiting to happen: the
moment someone tunes a constant in shapes.py, the demo starts confidently
reporting a different shape than the product does, and nothing would say so.

So this test extracts the JS straight out of site/playground.html -- not a copy,
the shipped source -- runs it under node, and asserts it agrees with the Python
on every field that appears in the demo's own verdict panel.

Skipped where node is unavailable, so the suite stays runnable everywhere.
"""

import datetime as dt
import json
import os
import shutil
import subprocess

import pytest

import shapes

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(os.path.dirname(HERE), "site", "playground.html")

PRESETS = {
    "cascade": (["+1", "+1", "+1", "+1", "+1", "heart"], [41, 43, 45, 48, 52, 300]),
    "trickle": (["+1", "eyes", "+1", "white_check_mark", "+1"],
                [120, 900, 1750, 2600, 3400]),
    "stall": (["+1", "+1", "+1", "+1", "+1"], [60, 3100, 3140, 3155, 3170]),
    "split": (["+1", "-1", "+1", "-1", "-1", "+1"], [90, 140, 260, 300, 420, 610]),
}

BASE = dt.datetime(2026, 8, 18, 12, 0, 0, tzinfo=dt.timezone.utc)


def _extract_js():
    """Pull the classifier out of the page between its two known markers."""
    with open(PAGE) as f:
        src = f.read()
    start = src.index("const MIN_REACTIONS = 4;")
    end = src.index("/* ---------------------------- rendering")
    return src[start:end] + "\nexport {classify, MIN_REACTIONS, SPLIT_MINORITY};\n"


def _run_js(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed; JS parity unverifiable here")
    (tmp_path / "c.mjs").write_text(_extract_js())
    harness = """
import {classify, MIN_REACTIONS, SPLIT_MINORITY} from "./c.mjs";
const P = %s;
const out = {constants: {MIN_REACTIONS, SPLIT_MINORITY}, shapes: {}};
for (const [id, [emoji, offs]] of Object.entries(P)) {
  const evs = emoji.map((n, i) => ({name: n, glyph: "", t: offs[i]}));
  const r = classify(evs);
  out.shapes[id] = {
    shape: r.shape, timing_shape: r.timing_shape,
    mean_u: Number(r.mean_u.toFixed(3)), burstiness: Number(r.B.toFixed(3)),
    n: r.n, split: r.split ? [r.split.for, r.split.against] : null,
  };
}
out.refusal = {
  three: classify([1,2,3].map(t => ({name:"+1", t}))).shape,
  four: classify([1,2,3,4].map(t => ({name:"+1", t}))).shape,
};
console.log(JSON.stringify(out));
""" % json.dumps(PRESETS)
    (tmp_path / "h.mjs").write_text(harness)
    res = subprocess.run([node, str(tmp_path / "h.mjs")],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _python_shape(emoji, offsets):
    rx = [{"emoji": e, "ts": (BASE + dt.timedelta(seconds=o)).isoformat(),
           "user": f"u{i}"}
          for i, (e, o) in enumerate(zip(emoji, offsets))]
    return shapes.classify(rx, msg_ts=BASE.isoformat())


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_the_js_port_agrees_with_the_python_classifier(preset, tmp_path):
    """Same input, same verdict, same numbers -- to three decimals, which is all
    the demo displays."""
    js = _run_js(tmp_path)["shapes"][preset]
    py = _python_shape(*PRESETS[preset])

    assert js["shape"] == py["shape"], f"{preset}: shape drifted"
    assert js["timing_shape"] == py["timing_shape"], f"{preset}: timing shape drifted"
    assert js["n"] == py["n"]
    assert js["mean_u"] == pytest.approx(py["mean_u"], abs=1e-3)
    assert js["burstiness"] == pytest.approx(py["burstiness"], abs=1e-3)
    assert bool(js["split"]) == bool(py["split"])


def test_a_split_overrides_the_timing_shape_in_both(tmp_path):
    """The precedence rule specifically, because the first version of the port
    got it wrong: it reported the timing shape and listed the split separately,
    while shapes.py returns `"split" if split else shape`."""
    js = _run_js(tmp_path)["shapes"]["split"]
    py = _python_shape(*PRESETS["split"])
    assert py["shape"] == "split" and js["shape"] == "split"
    assert py["timing_shape"] == js["timing_shape"] == "trickle"


def test_the_shared_constants_have_not_diverged(tmp_path):
    consts = _run_js(tmp_path)["constants"]
    assert consts["MIN_REACTIONS"] == shapes.MIN_REACTIONS
    assert consts["SPLIT_MINORITY"] == pytest.approx(shapes.SPLIT_MINORITY)


def test_both_refuse_below_the_minimum(tmp_path):
    """The refusal is the honest part of the classifier; it must survive the port."""
    refusal = _run_js(tmp_path)["refusal"]
    assert refusal["three"] == "forming"
    assert refusal["four"] != "forming"
    # and the Python simply returns None below the minimum
    assert _python_shape(["+1"] * 3, [1, 2, 3]) is None
    assert _python_shape(["+1"] * 4, [1, 2, 3, 4]) is not None

# Fixtures that straddle each mean_u threshold. Without these the parity tests
# pass while the thresholds drift: the four demo presets sit at 0.182, 0.410,
# 0.498 and 0.793, so moving the cascade cut from 0.25 to 0.35 reclassifies
# none of them and nothing fails. Verified by doing exactly that -- the suite
# stayed green, which is how these got written.
BOUNDARY = {
    "below_025": ([100, 140, 170, 200, 1100], "cascade"),      # mean_u 0.242
    "above_025": ([100, 190, 250, 330, 1100], "trickle"),      # mean_u 0.294
    "below_075": ([100, 880, 940, 1000, 1100], "trickle"),     # mean_u 0.704
    "above_075": ([100, 990, 1030, 1060, 1100], "stall-burst"),  # mean_u 0.756
}


@pytest.mark.parametrize("case", sorted(BOUNDARY))
def test_both_implementations_agree_across_the_threshold(case, tmp_path):
    """Pins the boundary itself, in both languages, against the expected shape --
    so a drifted threshold fails here rather than quietly changing what the demo
    tells people."""
    offsets, expected = BOUNDARY[case]
    py = _python_shape(["+1"] * len(offsets), offsets)
    assert py["shape"] == expected, f"python drifted on {case}"

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    (tmp_path / "c.mjs").write_text(_extract_js())
    (tmp_path / "b.mjs").write_text(
        'import {classify} from "./c.mjs";\n'
        f'const offs = {json.dumps(offsets)};\n'
        'const r = classify(offs.map(t => ({name:"+1", t})));\n'
        'console.log(JSON.stringify({shape:r.shape, mean_u:Number(r.mean_u.toFixed(4))}));\n')
    res = subprocess.run([node, str(tmp_path / "b.mjs")],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    js = json.loads(res.stdout)
    assert js["shape"] == expected, f"js drifted on {case}"
    assert js["mean_u"] == pytest.approx(py["mean_u"], abs=1e-3)
