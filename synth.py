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
machine, one page at a time so a claim can say "p.7" and mean it. The model
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


def page_count(pdf):
    """How many pages, found by walking until pdftotext returns nothing.

    pdfinfo is not on this machine and is a second dependency to require for
    one integer, so this asks the tool that is here.
    """
    low, high = 1, 2
    while high < 4096 and page_text(pdf, high).strip():
        low, high = high, high * 2
    while low + 1 < high:
        mid = (low + high) // 2
        if page_text(pdf, mid).strip():
            low = mid
        else:
            high = mid
    return low


def page_text(pdf, n):
    try:
        out = subprocess.run(["pdftotext", "-f", str(n), "-l", str(n), pdf, "-"],
                             capture_output=True, timeout=60,
                             creationflags=NO_WINDOW)
        return out.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def pages(pdf):
    """[(page number, text)]. One call per page, because that is what makes an
    anchor checkable later."""
    total = page_count(pdf)
    return [(n, page_text(pdf, n)) for n in range(1, total + 1)]


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
    data = carve(envelope.get("result") or "")
    if data is None:
        return None, "the reply was not JSON: %s" % (envelope.get("result") or "")[:200]
    return data, ""


def check_anchors(data, total):
    """Every page number is checked against the paper, not trusted.

    This is the whole point of the tool. A claim anchored to a page that does
    not exist is a claim the model invented a source for, so the anchor is
    dropped and counted rather than quietly kept.
    """
    dropped = []
    for key in ("findings", "limitations", "numbers"):
        for item in (data.get(key) or []):
            if not isinstance(item, dict):
                continue
            page = item.get("page")
            if page is None:
                continue
            try:
                page = int(page)
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
    for key in ("findings", "limitations", "numbers"):
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
    unverified = [(k, i) for k in ("findings", "limitations", "numbers")
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

    paper = pages(args.pdf)
    total = len(paper)
    empty = sum(1 for _, t in paper if not t.strip())

    if args.pages:
        for n, text in paper:
            words = len(text.split())
            print("  p.%-3d %5d words  %s" % (n, words, ", ".join(label(text))))
        return 0

    if total == 0 or empty == total:
        print("\n  BLOCKED: no text came out of any page. This is probably a"
              "\n  scanned PDF, which needs OCR. It is not an empty paper.\n",
              file=sys.stderr)
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

    anchored = sum(1 for k in ("findings", "limitations", "numbers")
                   for i in (data.get(k) or [])
                   if isinstance(i, dict) and i.get("page"))
    print("  %d anchored claims, %d anchors dropped as out of range"
          % (anchored, len(dropped)))
    print("  %d verified on the cited page, %d unverified, %d too short to check"
          % (counts["verified"], counts["unverified"], counts["skipped"]))
    if dropped:
        print("  those claims are unsourced - the model cited a page that does"
              " not exist")
    if counts["unverified"]:
        print("  unverified means the check could not find the claim on that"
              " page, not that the claim is wrong - go read the page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
