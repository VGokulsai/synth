"""A research paper, read back as structure, with every claim anchored to a page.

The point is not the summary. It is that each claim carries the page it came
from, and that page is checked here rather than trusted. Twice: an anchor
pointing outside the paper is set to null and reported, and an anchor pointing
inside it is scored against what is actually printed on that page, because
in range is not the same as on that page. A summary you cannot check is the
same problem as reading a blog post about the paper, which is the thing this
exists to stop.

The second check answers "could I find this claim on the page it cites", never
"is this claim true". Its three answers are verified, unverified and skipped.
Nothing here can label a claim false, so nothing here does.

    py -3 synth.py paper.pdf              read it, write paper.synthesis.json
    py -3 synth.py paper.pdf --md         also write paper.synthesis.md
    py -3 synth.py paper.pdf --pages      just show the per-page extraction
    py -3 synth.py --check                is everything this needs present

No Python dependencies. Text comes from `pdftotext`, which is already on this
machine, split back into pages on the form feeds it writes between them, so a
claim can say "p.7" and mean the printed page seven. The model
call goes through `claude -p` on the subscription, with the flags that strip
CLAUDE.md, skills, plugins and MCP definitions out of the request - measured
at $0.0017 a call here against $0.0240 without them.
"""

import argparse
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OK, BLOCKED, ERROR = "OK", "BLOCKED", "ERROR"

MODEL = os.environ.get("SYNTH_MODEL", "claude-sonnet-5")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Sections a paper is expected to have. Used only to label the extract that
# gets sent; a paper that names them differently still works, it just sends
# more of itself.
SECTIONS = [
    ("abstract", r"\babstract\b"),
    ("methods", r"\b(methods?|methodology|approach|experimental setup)\b"),
    ("results", r"\b(results?|findings|evaluation)\b"),
    ("discussion", r"\b(discussion|analysis)\b"),
    ("limitations", r"\b(limitations?|threats to validity|future work)\b"),
    ("conclusion", r"\bconclusions?\b"),
]

SYSTEM = ("You read research papers and return one JSON object and nothing "
          "else. Every claim carries the page number it came from. If you "
          "cannot find something in the text you were given, use null - never "
          "infer it from what the paper is probably like.")

SHAPE = """{
 "title": "",
 "question": "the question the paper is actually asking",
 "design": "what they did, concretely",
 "findings": [{"claim": "", "page": 0}],
 "limitations": [{"claim": "", "page": 0}],
 "gaps": ["what this paper leaves unanswered"],
 "numbers": [{"value": "", "means": "", "page": 0}]
}"""


def have(command):
    try:
        subprocess.run([command, "-v"], capture_output=True, timeout=10,
                       creationflags=NO_WINDOW)
        return True
    except Exception:
        return False


def pages(pdf):
    """([(page number, text)], "") for the whole paper, or (None, problem).

    One call, split on the form feeds pdftotext writes between pages, so the
    page numbers here are the printed ones and an anchor can be checked
    against them.

    This used to ask page by page and stop at the first page that came back
    empty. That cannot work. pdftotext exits 0 with no output BOTH for a
    blank page inside the paper and for a page past the end of it, so
    emptiness does not mean the end of the document - and a paper with a
    blank page in the middle was silently cut off there. A ten page paper
    with pages four and five blank read as a three page paper, every anchor
    past p.3 was then reported as "the model cited a page that does not
    exist", and the run exited 0 as though it had gone fine.

    A failed read is never an empty paper, so a non-zero exit is returned as
    a problem instead of as pages.
    """
    try:
        out = subprocess.run(["pdftotext", pdf, "-"], capture_output=True,
                             timeout=300, creationflags=NO_WINDOW)
    except Exception as exc:
        return None, "could not run pdftotext: %s" % exc
    if out.returncode != 0:
        detail = out.stderr.decode("utf-8", "replace").strip().replace("\n", " ")
        return None, "pdftotext could not read %s: %s" % (
            os.path.basename(pdf), detail[:200] or "exit %d" % out.returncode)
    text = out.stdout.decode("utf-8", "replace")
    chunks = text.split("\f")
    # pdftotext ends the last page with a form feed too, so the split leaves
    # one empty tail that is not a page. Exactly one, and exactly empty - a
    # genuinely blank last page is a real page and is kept.
    if chunks and chunks[-1] == "":
        chunks.pop()
    if not chunks:
        return None, "pdftotext read %s and returned no pages" % os.path.basename(pdf)
    return list(enumerate(chunks, 1)), ""


def label(text):
    """Which sections a page looks like it belongs to. Cheap and approximate:
    it only decides what to send, never what is true."""
    head = text[:600].lower()
    return [name for name, pattern in SECTIONS if re.search(pattern, head)]


def build_prompt(paper):
    """Pages, numbered, with the section guesses attached."""
    parts = ["The paper, one page at a time. The number before each page is "
             "the page number to cite.", ""]
    for n, text in paper:
        text = " ".join(text.split())
        if not text:
            continue
        tag = ("  [" + ", ".join(label(text)) + "]") if label(text) else ""
        parts.append("--- page %d%s ---" % (n, tag))
        # Long papers blow the request out; the head of each page carries the
        # claims and the tail is usually references and figure captions.
        parts.append(text[:3000])
    parts.append("")
    parts.append("Return exactly this shape, and nothing outside it:")
    parts.append(SHAPE)
    return "\n".join(parts)


def carve(text):
    """The first balanced JSON object in a reply. Models fence their JSON,
    prefix it with a sentence, or both."""
    text = (text or "").strip()
    start = text.find("{")
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except Exception:
                    return None
    return None


def ask(prompt):
    """One headless call, with the environment stripped out of it."""
    # The prompt goes on stdin, not in argv. Windows caps a command line at
    # 32KB and a fifteen-page paper is comfortably past that, which failed as
    # "the filename or extension is too long" rather than anything readable.
    cmd = ["claude", "-p", "--model", MODEL, "--output-format", "json",
           "--setting-sources", "", "--tools", "", "--strict-mcp-config",
           "--system-prompt", SYSTEM]
    try:
        out = subprocess.run(cmd, input=prompt, capture_output=True,
                             timeout=600, creationflags=NO_WINDOW,
                             encoding="utf-8", errors="replace")
    except Exception as exc:
        return None, "could not run claude: %s" % exc

    envelope = carve(out.stdout)
    if envelope is None:
        return None, "claude returned no envelope: %s" % (out.stderr or "")[:200]
    if envelope.get("is_error"):
        return None, str(envelope.get("result") or "claude reported an error")
    result = envelope.get("result")
    if not isinstance(result, str):
        return None, "claude returned no reply text, got %s" % type(result).__name__
    data = carve(result)
    if data is None:
        return None, "the reply was not JSON: %s" % result[:200]
    return check_shape(data, result)


ANCHORED = ("findings", "limitations", "numbers")


def check_shape(data, raw):
    """(data, "") if the reply is the shape that was asked for, else (None, why).

    A reply can parse as JSON and still be nothing this can check. `{"error":
    "prompt is too long"}` is valid JSON, and it used to sail through to the
    end and print "0 anchored claims, 0 dropped, 0 verified, 0 unverified" and
    exit 0 - a total failure wearing the clothes of a clean read. A shape this
    cannot check is a blocked run, not an empty paper.
    """
    if not isinstance(data, dict):
        return None, "the reply was JSON but not an object: %s" % raw[:200]
    for key in ANCHORED:
        items = data.get(key)
        if items is None:
            continue
        if not isinstance(items, list):
            return None, ("the reply's %s was %s, not a list, so no anchor in "
                          "it can be checked" % (key, type(items).__name__))
        bad = [i for i in items if not isinstance(i, dict)]
        if bad:
            return None, ("the reply's %s holds %d entry(s) that are not "
                          "objects, so they carry no page to check"
                          % (key, len(bad)))
    if not any(isinstance(data.get(k), list) for k in ANCHORED):
        return None, ("the reply carries no findings, limitations or numbers, "
                      "so there is nothing anchored to check: %s" % raw[:200])
    return data, ""


def check_anchors(data, total):
    """Every page number is checked against the paper, not trusted.

    This is the whole point of the tool. A claim anchored to a page that does
    not exist is a claim the model invented a source for, so the anchor is
    dropped and counted rather than quietly kept.
    """
    dropped = []
    for key in ANCHORED:
        for item in (data.get(key) or []):
            if not isinstance(item, dict):
                continue
            page = item.get("page")
            if page is None:
                continue
            try:
                # int() is too willing: it turns True into page 1 and 7.9 into
                # page 7, both of which are values this could not read being
                # quietly rewritten as ones it could.
                if isinstance(page, bool):
                    raise ValueError("a boolean is not a page number")
                number = int(page)
                if float(page) != number:
                    raise ValueError("%r is not a whole page number" % (page,))
                page = number
            except Exception:
                item["page"] = None
                dropped.append((key, item.get("claim") or item.get("value")))
                continue
            if page < 1 or page > total:
                item["page"] = None
                dropped.append((key, item.get("claim") or item.get("value")))
            else:
                item["page"] = page
    return dropped


# Words a paraphrase and an unrelated page have equally in common, so they
# say nothing about whether the claim came from that page. Only 4+ letters
# are listed - shorter tokens are dropped before this is consulted.
STOP = frozenset("""
about above across after against also among another because been before being
between both cannot could does done during each either else even every from
have here however into itself just like made make many more most much must
none only other others over same should since some such than that their them
then there these they this those through thus under until upon very were what
when where whether which while will with within without would your
""".split())

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.'\-]*")

# A claim is verified when this much of its distinctive weight is on the page.
# Measured, not guessed: over the 40 anchored claims in attention.synthesis.json
# the scores fall in two groups with a wide gap - the one genuinely miscited
# claim scores 0.16, and the next lowest scores 0.43 and is on its page (it
# cites Adam's beta1/beta2/epsilon, which pdftotext throws away with the Greek
# letters). Anything from 0.20 to 0.42 separates them; 0.35 sits in the middle
# of that gap, so neither a mangled symbol nor a heavy paraphrase tips a real
# citation over the edge. Raising this to 0.45 costs a false negative.
VERIFY_THRESHOLD = 0.35

# Below this much total weight the claim is too thin to say anything about -
# a two-word value with no number in it would "verify" on any page by luck.
MIN_SIGNAL = 6


def _weighted(text):
    """{token: weight} for the tokens that actually identify a claim.

    The model paraphrases, so shared wording proves nothing and missing wording
    disproves nothing. What survives a paraphrase is the specifics: numbers,
    units, and named terms. Those are weighted up so the score tracks the
    content of the claim rather than its grammar.
    """
    out = {}
    for m in _WORD.finditer(text):
        tok = m.group().strip(".-'")
        if not tok:
            continue
        low = tok.lower()
        # A sentence's first word is capitalised by grammar, not by being a
        # name, so it gets no credit for the capital.
        before = text[:m.start()].rstrip()
        opening = not before or before[-1] in ".!?:;"
        if any(c.isdigit() for c in tok):
            weight = 3
        elif tok[0].isupper() and len(tok) > 2 and not opening:
            weight = 2
        elif len(tok) > 3 and low not in STOP:
            weight = 1
        else:
            continue
        out[low] = max(out.get(low, 0), weight)
    return out


def _page_index(text):
    """(token set, flattened text) for one page."""
    flat = " ".join(text.lower().split())
    return {m.group().strip(".-'") for m in _WORD.finditer(flat)}, flat


def verify_claim(text, index):
    """(state, score) for one claim against its cited page.

    Three states, never two. "unverified" means this check could not find the
    claim's content on that page - the claim may still be true and the page may
    still be right. pdftotext loses subscripts, tables reflow, and a model can
    cite the page a result is discussed on rather than the one it is printed
    on. None of that makes a claim false, and this function has no way to know
    that it is.
    """
    tokens, flat = index
    want = _weighted(text)
    total = sum(want.values())
    if total < MIN_SIGNAL:
        return "skipped", 0.0
    hit = 0
    for tok, weight in want.items():
        # A longer token counts a shorter one: pdftotext flattens "d_model" to
        # "dmodel" and glues table cells together, so exact tokens go missing
        # even when the text is right there.
        if tok in tokens or (len(tok) > 3 and tok in flat):
            hit += weight
    score = hit / float(total)
    return ("verified" if score >= VERIFY_THRESHOLD else "unverified"), score


def check_content(data, paper):
    """Tag every anchored claim with whether its content is on the page it
    cites. Returns the count per state."""
    index = {n: _page_index(t) for n, t in paper}
    counts = {"verified": 0, "unverified": 0, "skipped": 0}
    for key in ANCHORED:
        for item in (data.get(key) or []):
            if not isinstance(item, dict):
                continue
            page = item.get("page")
            if not page or page not in index:
                continue
            text = item.get("claim") or ""
            if not text:
                # A number is only checkable together with what it means.
                text = "%s %s" % (item.get("value") or "", item.get("means") or "")
            state, score = verify_claim(text, index[page])
            item["anchor_check"] = state
            item["anchor_score"] = round(score, 2)
            counts[state] += 1
    return counts


def selftest():
    page = _page_index(
        "Our model achieves 28.4 BLEU on the WMT 2014 English-to-German "
        "translation task. In this work we employ h = 8 parallel attention "
        "layers, or heads.")
    # A paraphrase, which an exact substring match would fail on.
    assert verify_claim("The Transformer (big) model reaches 28.4 BLEU on WMT "
                        "2014 English-to-German translation.", page)[0] == "verified"
    # The same shape of sentence carrying content from a different paper.
    assert verify_claim("ResNet-152 reaches 78.6% top-1 accuracy on ImageNet "
                        "with residual bottleneck blocks.", page)[0] == "unverified"
    # Nothing distinctive enough to decide either way.
    assert verify_claim("It works well.", page)[0] == "skipped"
    print("  selftest ok")


def to_markdown(data, source, total, dropped):
    out = ["# %s" % (data.get("title") or os.path.basename(source)), ""]
    out.append("%d pages, read from `%s`" % (total, os.path.basename(source)))
    out.append("")
    for key, heading in (("question", "The question"), ("design", "What they did")):
        if data.get(key):
            out.append("## %s" % heading)
            out.append("")
            out.append(str(data[key]))
            out.append("")
    for key, heading in (("findings", "Findings"), ("limitations", "Limitations")):
        items = data.get(key) or []
        if not items:
            continue
        out.append("## %s" % heading)
        out.append("")
        for item in items:
            page = item.get("page")
            where = "p.%d" % page if page else "**no page**"
            state = item.get("anchor_check")
            out.append("- %s  (%s%s)" % (item.get("claim", ""), where,
                                         (", %s" % state) if state else ""))
        out.append("")
    numbers = data.get("numbers") or []
    if numbers:
        out.append("## Numbers")
        out.append("")
        out.append("| value | means | page | check |")
        out.append("|---|---|---|---|")
        for n in numbers:
            page = n.get("page")
            out.append("| %s | %s | %s | %s |" % (
                n.get("value", ""), n.get("means", ""),
                ("p.%d" % page) if page else "unanchored",
                n.get("anchor_check", "")))
        out.append("")
    gaps = data.get("gaps") or []
    if gaps:
        out.append("## Gaps")
        out.append("")
        out.extend("- %s" % g for g in gaps)
        out.append("")
    unverified = [(k, i) for k in ANCHORED
                  for i in (data.get(k) or [])
                  if isinstance(i, dict) and i.get("anchor_check") == "unverified"]
    if unverified:
        out.append("## Unverified")
        out.append("")
        out.append("These claims cite a page that exists, but not enough of the")
        out.append("claim's distinctive wording - its numbers, units and named")
        out.append("terms - was found on that page. That is a check that failed,")
        out.append("not a claim shown to be wrong. The claim may be true and the")
        out.append("page may be right: the page may be discussing a result printed")
        out.append("elsewhere, the text may be in a figure or a table this cannot")
        out.append("read, or the wording may be a heavy paraphrase. Go and read the")
        out.append("page before believing or disbelieving any of them.")
        out.append("")
        for key, item in unverified:
            page = item.get("page")
            out.append("- [%s, p.%s] %s" % (key, page,
                                            item.get("claim") or item.get("value", "")))
        out.append("")
    if dropped:
        out.append("## Anchors dropped")
        out.append("")
        out.append("These claims cited a page outside the paper, so the anchor")
        out.append("was removed. Treat the claim as unsourced.")
        out.append("")
        out.extend("- [%s] %s" % (k, c) for k, c in dropped)
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--pages", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        selftest()
        return 0

    if args.check:
        print()
        print("  pdftotext : %s" % ("found" if have("pdftotext") else "MISSING"))
        print("  claude    : %s" % ("found" if have("claude") else "MISSING"))
        print("  model     : %s" % MODEL)
        print()
        return 0

    if not args.pdf:
        print("  usage: py -3 synth.py paper.pdf", file=sys.stderr)
        return 2
    if not os.path.isfile(args.pdf):
        print("  no file at %s" % args.pdf, file=sys.stderr)
        return 2
    if not have("pdftotext"):
        print("\n  BLOCKED: pdftotext is not on PATH. That is a missing reader,"
              "\n  not a paper with no text in it.\n", file=sys.stderr)
        return 2

    paper, problem = pages(args.pdf)
    if problem:
        print("\n  BLOCKED: %s" % problem, file=sys.stderr)
        print("  The paper was not read, so there is nothing to check.\n",
              file=sys.stderr)
        return 2
    total = len(paper)
    empty = sum(1 for _, t in paper if not t.strip())

    if args.pages:
        for n, text in paper:
            words = len(text.split())
            print("  p.%-3d %5d words  %s" % (n, words, ", ".join(label(text))))
        return 0

    if empty == total:
        # pdftotext exited 0, so it read the file and there was no text in it.
        print("\n  BLOCKED: pdftotext read all %d pages and found no text on"
              "\n  any of them. This is probably a scanned PDF, which needs"
              "\n  OCR. It is not an empty paper.\n" % total, file=sys.stderr)
        return 2

    print("  %d pages, %d with no text" % (total, empty))
    print("  reading with %s ..." % MODEL)

    data, problem = ask(build_prompt(paper))
    if problem:
        print("\n  BLOCKED: %s" % problem, file=sys.stderr)
        print("  Nothing was synthesised, so there is nothing to trust.\n",
              file=sys.stderr)
        return 2

    dropped = check_anchors(data, total)
    counts = check_content(data, paper)
    data["_source"] = os.path.basename(args.pdf)
    data["_pages"] = total
    data["_anchors_dropped"] = len(dropped)
    data["_anchor_check"] = counts

    stem = os.path.splitext(args.pdf)[0]
    with open(stem + ".synthesis.json", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=1, ensure_ascii=False))
    print("  wrote %s.synthesis.json" % os.path.basename(stem))

    if args.md:
        with open(stem + ".synthesis.md", "w", encoding="utf-8", newline="\n") as fh:
            fh.write(to_markdown(data, args.pdf, total, dropped))
        print("  wrote %s.synthesis.md" % os.path.basename(stem))

    anchored = sum(1 for k in ANCHORED
                   for i in (data.get(k) or [])
                   if isinstance(i, dict) and i.get("page"))
    print("  %d anchored claims, %d anchors dropped as out of range"
          % (anchored, len(dropped)))
    print("  %d verified on the cited page, %d unverified, %d too short to check"
          % (counts["verified"], counts["unverified"], counts["skipped"]))
    if not anchored:
        # A zero has to say which zero it is. This one is real: the reply came
        # back in the shape that was asked for and carried no anchored claim,
        # which is the model answering "nothing", not a check that failed.
        # Every way of failing to get here exits 2 with a BLOCKED line above.
        print("  that is a real zero: %s read all %d pages, answered in the"
              " shape asked for, and anchored nothing" % (MODEL, total))
    if dropped:
        print("  those claims are unsourced - the model cited a page that does"
              " not exist")
    if counts["unverified"]:
        print("  unverified means the check could not find the claim on that"
              " page, not that the claim is wrong - go read the page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
