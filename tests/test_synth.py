"""Tests for synth.py, aimed at one property above all others:

    a blocked read must never come out looking like a clean result.

Every failure path is asserted to produce a stated problem, never an empty,
zero or passing value. The rest of the file covers the logic those paths run
through: page splitting, JSON carving, anchor range checking and the
verified/unverified/skipped boundary.

    py -3 -m pytest tests/ -q
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import synth  # noqa: E402


class FakeRun(object):
    """Stands in for subprocess.run's result."""

    def __init__(self, stdout=b"", returncode=0, stderr=b""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def fake_pdftotext(monkeypatch, stdout=b"", returncode=0, stderr=b""):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: FakeRun(stdout, returncode, stderr))


def pdf_bytes(page_texts):
    """What pdftotext prints: each page followed by a form feed, including
    the last one."""
    return "".join(t + "\f" for t in page_texts).encode("utf-8")


# --------------------------------------------------------------------------
# pages(): the paper must not be silently shortened
# --------------------------------------------------------------------------

def test_pages_splits_on_form_feed(monkeypatch):
    fake_pdftotext(monkeypatch, pdf_bytes(["one", "two", "three"]))
    paper, problem = synth.pages("x.pdf")
    assert problem == ""
    assert paper == [(1, "one"), (2, "two"), (3, "three")]


def test_blank_page_in_the_middle_does_not_truncate_the_paper(monkeypatch):
    """The bug this file exists for.

    Asking pdftotext page by page and stopping at the first empty page read
    this ten page paper as a three page paper. Pages 4-10 vanished, and every
    anchor to them was then reported as the model citing a page that does not
    exist - a false accusation, delivered by a run that exited 0.
    """
    ten = ["Alpha", "Beta", "Gamma", "", "", "Zeta", "Eta", "Theta", "Iota", "Kappa"]
    fake_pdftotext(monkeypatch, pdf_bytes(ten))
    paper, problem = synth.pages("x.pdf")
    assert problem == ""
    assert len(paper) == 10
    assert [n for n, t in paper if not t.strip()] == [4, 5]
    assert paper[9] == (10, "Kappa")


def test_blank_last_page_is_kept_but_the_trailing_split_is_not(monkeypatch):
    fake_pdftotext(monkeypatch, pdf_bytes(["one", "two", ""]))
    paper, problem = synth.pages("x.pdf")
    assert problem == ""
    assert len(paper) == 3          # not 2 (blank page eaten), not 4 (artifact kept)
    assert paper[2] == (3, "")


def test_single_page(monkeypatch):
    fake_pdftotext(monkeypatch, pdf_bytes(["only"]))
    paper, problem = synth.pages("x.pdf")
    assert (paper, problem) == ([(1, "only")], "")


def test_all_pages_blank_is_still_the_right_number_of_pages(monkeypatch):
    fake_pdftotext(monkeypatch, pdf_bytes(["", "", ""]))
    paper, problem = synth.pages("x.pdf")
    assert problem == ""
    assert len(paper) == 3          # a scanned PDF, not a one page paper


def test_unreadable_pdf_is_a_problem_not_an_empty_paper(monkeypatch):
    fake_pdftotext(monkeypatch, b"", returncode=1,
                   stderr=b"Syntax Error: Couldn't read xref table")
    paper, problem = synth.pages("broken.pdf")
    assert paper is None
    assert "broken.pdf" in problem and "xref" in problem


def test_nonzero_exit_with_output_is_still_a_problem(monkeypatch):
    """Partial output from a failed read is not a paper."""
    fake_pdftotext(monkeypatch, pdf_bytes(["half a page"]), returncode=1)
    paper, problem = synth.pages("broken.pdf")
    assert paper is None
    assert problem


def test_pdftotext_missing_or_timing_out_is_a_problem(monkeypatch):
    def boom(*a, **k):
        raise OSError("no pdftotext")
    monkeypatch.setattr(subprocess, "run", boom)
    paper, problem = synth.pages("x.pdf")
    assert paper is None
    assert "pdftotext" in problem


def test_no_output_at_all_is_a_problem(monkeypatch):
    fake_pdftotext(monkeypatch, b"")
    paper, problem = synth.pages("x.pdf")
    assert paper is None
    assert problem


@pytest.mark.parametrize("stdout,returncode", [
    (b"", 0), (b"", 1), (pdf_bytes(["a"]), 3),
])
def test_no_failure_path_of_pages_returns_a_silent_empty_paper(
        monkeypatch, stdout, returncode):
    fake_pdftotext(monkeypatch, stdout, returncode)
    paper, problem = synth.pages("x.pdf")
    assert not (paper == [] and problem == ""), "an empty paper with no problem stated"
    assert paper is None and problem


# --------------------------------------------------------------------------
# carve()
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go:\n{"a": 1}\nhope that helps', {"a": 1}),
    ('{"a": {"b": 2}}', {"a": {"b": 2}}),
    ('{"a": "a } brace in a string"}', {"a": "a } brace in a string"}),
    ('{"a": "an escaped \\" quote"}', {"a": 'an escaped " quote'}),
])
def test_carve_finds_the_object(raw, want):
    assert synth.carve(raw) == want


@pytest.mark.parametrize("raw", ["", None, "no json here", '{"a": ', '{"a": 1,,}'])
def test_carve_returns_none_rather_than_a_wrong_guess(raw):
    assert synth.carve(raw) is None


# --------------------------------------------------------------------------
# check_shape(): valid JSON is not the same as a checkable reply
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw", [
    '{"error": "prompt is too long"}',   # the signature bug: read as 0/0/0, exit 0
    '{}',
    '{"title": "A paper", "gaps": ["something"]}',
    '{"findings": "a sentence instead of a list"}',
    '{"findings": ["a bare string"]}',
    '{"findings": [{"claim": "ok", "page": 1}, "a bare string"]}',
    '{"numbers": {"value": "28.4"}}',
])
def test_uncheckable_reply_is_blocked(raw):
    data, problem = synth.check_shape(synth.carve(raw), raw)
    assert data is None, "an uncheckable reply was accepted as a clean read"
    assert problem


@pytest.mark.parametrize("raw", [
    '{"findings": [{"claim": "x", "page": 1}]}',
    '{"limitations": []}',                       # a real, stated zero
    '{"numbers": [{"value": "28.4", "means": "BLEU", "page": 2}]}',
])
def test_checkable_reply_passes(raw):
    data, problem = synth.check_shape(synth.carve(raw), raw)
    assert data is not None and problem == ""


def test_check_shape_rejects_a_non_object():
    assert synth.check_shape([1, 2], "[1, 2]")[0] is None


# --------------------------------------------------------------------------
# ask(): every failure is stated
# --------------------------------------------------------------------------

def fake_claude(monkeypatch, stdout, returncode=0, stderr=""):
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: FakeRun(stdout, returncode, stderr))


GOOD = '{"findings": [{"claim": "x", "page": 1}]}'


@pytest.mark.parametrize("stdout", [
    "",                                             # nothing back
    "claude: command failed",                       # no envelope
    '{"is_error": true, "result": "usage limit"}',  # error envelope
    '{"result": "I could not read the paper."}',    # reply is not JSON
    '{"result": {"not": "a string"}}',              # reply is not even text
    '{"result": "{\\"error\\": \\"prompt is too long\\"}"}',   # wrong shape
    '{"result": "{}"}',                             # empty object
])
def test_ask_states_every_failure(monkeypatch, stdout):
    fake_claude(monkeypatch, stdout)
    data, problem = synth.ask("prompt")
    assert data is None, "a failed call returned data"
    assert problem, "a failed call returned no problem to report"


def test_ask_reports_a_crash_rather_than_returning_nothing(monkeypatch):
    def boom(*a, **k):
        raise OSError("claude is not on PATH")
    monkeypatch.setattr(subprocess, "run", boom)
    data, problem = synth.ask("prompt")
    assert data is None and "claude" in problem


def test_ask_returns_the_data_on_success(monkeypatch):
    fake_claude(monkeypatch, '{"result": %s}' % synth.json.dumps(GOOD))
    data, problem = synth.ask("prompt")
    assert problem == ""
    assert data["findings"][0]["page"] == 1


# --------------------------------------------------------------------------
# check_anchors(): a page number is checked, never trusted
# --------------------------------------------------------------------------

def anchored(page):
    return {"findings": [{"claim": "a claim", "page": page}]}


@pytest.mark.parametrize("page,kept", [
    (1, 1), (5, 5), (10, 10),        # in range
    ("7", 7), (7.0, 7),              # written as a string or a whole float
])
def test_anchor_in_range_is_kept(page, kept):
    data = anchored(page)
    assert synth.check_anchors(data, 10) == []
    assert data["findings"][0]["page"] == kept


@pytest.mark.parametrize("page", [
    0, -1, 11, 999,                  # outside the paper
    True, False,                     # a bool is not a page, and int(True) is 1
    7.9, "7.5",                      # not a whole page
    "page seven", [7], {}, "",       # not a number at all
])
def test_anchor_that_cannot_be_read_is_dropped_not_guessed(page):
    data = anchored(page)
    dropped = synth.check_anchors(data, 10)
    assert data["findings"][0]["page"] is None
    assert dropped == [("findings", "a claim")]


def test_null_anchor_is_left_alone_and_not_counted_as_dropped():
    data = anchored(None)
    assert synth.check_anchors(data, 10) == []
    assert data["findings"][0]["page"] is None


def test_check_anchors_covers_every_anchored_key():
    data = {"findings": [{"claim": "f", "page": 99}],
            "limitations": [{"claim": "l", "page": 99}],
            "numbers": [{"value": "1", "means": "m", "page": 99}]}
    dropped = synth.check_anchors(data, 10)
    assert len(dropped) == 3
    assert all(i["page"] is None for k in synth.ANCHORED for i in data[k])


# --------------------------------------------------------------------------
# verify_claim(): verified / unverified / skipped
# --------------------------------------------------------------------------

PAGE = synth._page_index(
    "Our model achieves 28.4 BLEU on the WMT 2014 English-to-German "
    "translation task. In this work we employ h = 8 parallel attention "
    "layers, or heads.")


def test_paraphrase_of_the_page_verifies():
    state, score = synth.verify_claim(
        "The Transformer (big) model reaches 28.4 BLEU on WMT 2014 "
        "English-to-German translation.", PAGE)
    assert state == "verified" and score >= synth.VERIFY_THRESHOLD


def test_content_from_a_different_paper_does_not_verify():
    state, _ = synth.verify_claim(
        "ResNet-152 reaches 78.6% top-1 accuracy on ImageNet with residual "
        "bottleneck blocks.", PAGE)
    assert state == "unverified"


def test_a_claim_with_nothing_distinctive_is_skipped_not_verified():
    state, score = synth.verify_claim("It works well.", PAGE)
    assert state == "skipped"
    assert score == 0.0


def test_skipped_is_never_reported_as_verified():
    """A skipped claim scores 0.0, and 0.0 must not read as a passing score."""
    for text in ["It works well.", "", "Good.", "The model is better."]:
        state, score = synth.verify_claim(text, PAGE)
        if state == "skipped":
            assert score == 0.0
            assert state != "verified"


def test_a_blank_page_can_never_verify_anything():
    blank = synth._page_index("")
    state, score = synth.verify_claim(
        "The Transformer reaches 28.4 BLEU on WMT 2014 English-to-German.", blank)
    assert state == "unverified" and score == 0.0


def test_a_long_token_is_found_inside_flattened_text():
    """pdftotext glues cells together, so d_model arrives as part of a run."""
    page = synth._page_index("the dmodel512 column of the table")
    assert "dmodel512" in page[1]
    state, _ = synth.verify_claim("dmodel 512 dimensions transformer encoder", page)
    assert state in ("verified", "unverified", "skipped")   # only: it must not crash


# --------------------------------------------------------------------------
# check_content(): the counts
# --------------------------------------------------------------------------

PAPER = [(1, "Our model achieves 28.4 BLEU on the WMT 2014 English-to-German "
              "translation task."),
         (2, "Training took 3.5 days on eight NVIDIA P100 GPUs."),
         (3, "")]


def test_counts_are_kept_apart():
    data = {"findings": [
        {"claim": "The model reaches 28.4 BLEU on WMT 2014 English-to-German.",
         "page": 1},                                        # verified
        {"claim": "ResNet-152 reaches 78.6% top-1 accuracy on ImageNet.",
         "page": 1},                                        # unverified
        {"claim": "It works.", "page": 1},                  # skipped
    ]}
    counts = synth.check_content(data, PAPER)
    assert counts == {"verified": 1, "unverified": 1, "skipped": 1}
    assert [i["anchor_check"] for i in data["findings"]] == [
        "verified", "unverified", "skipped"]


def test_a_claim_anchored_to_a_blank_page_is_not_verified():
    data = {"findings": [{"claim": "Training took 3.5 days on eight NVIDIA "
                                   "P100 GPUs.", "page": 3}]}
    counts = synth.check_content(data, PAPER)
    assert counts["verified"] == 0
    assert data["findings"][0]["anchor_check"] == "unverified"


def test_every_anchored_claim_lands_in_exactly_one_count():
    """The headline numbers must add up to the anchored claims, or a claim
    has gone missing between the two."""
    data = {"findings": [{"claim": "28.4 BLEU on WMT 2014 English-to-German", "page": 1},
                         {"claim": "unrelated ImageNet ResNet accuracy claim", "page": 2},
                         {"claim": "short", "page": 2},
                         {"claim": "no anchor at all", "page": None}],
            "numbers": [{"value": "3.5 days", "means": "training time on P100 GPUs",
                         "page": 2}]}
    synth.check_anchors(data, len(PAPER))
    counts = synth.check_content(data, PAPER)
    total_anchored = sum(1 for k in synth.ANCHORED
                         for i in (data.get(k) or []) if i.get("page"))
    assert sum(counts.values()) == total_anchored


def test_a_number_is_checked_on_its_value_and_meaning_together():
    data = {"numbers": [{"value": "3.5 days", "means": "to train on eight "
                                                       "NVIDIA P100 GPUs", "page": 2}]}
    counts = synth.check_content(data, PAPER)
    assert counts["verified"] == 1


# --------------------------------------------------------------------------
# to_markdown()
# --------------------------------------------------------------------------

def test_markdown_separates_unverified_from_dropped():
    data = {"title": "A paper",
            "findings": [{"claim": "verified thing", "page": 1,
                          "anchor_check": "verified"},
                         {"claim": "unverified thing", "page": 2,
                          "anchor_check": "unverified"},
                         {"claim": "unsourced thing", "page": None}]}
    md = synth.to_markdown(data, "p.pdf", 3, [("findings", "unsourced thing")])
    assert "## Unverified" in md and "unverified thing" in md
    assert "## Anchors dropped" in md and "unsourced thing" in md
    assert "**no page**" in md
    # the verified claim must not be listed under either failure heading
    assert md.index("verified thing") < md.index("## Unverified")


def test_markdown_never_prints_a_page_for_a_dropped_anchor():
    data = {"findings": [{"claim": "c", "page": None}],
            "numbers": [{"value": "v", "means": "m", "page": None}]}
    md = synth.to_markdown(data, "p.pdf", 3, [])
    assert "**no page**" in md and "unanchored" in md
    assert "p.None" not in md and "p.0" not in md
