# synth

Turns a research paper into a structured summary where every claim carries the
page it came from, and each page anchor is checked against the paper instead of
trusted. It catches citations the model invented.

The check runs twice. An anchor pointing outside the paper is set to null and
reported. An anchor pointing inside it is scored against the words actually
printed on that page, because in range is not the same as on that page.

```
py -3 synth.py paper.pdf          read it, write paper.synthesis.json
py -3 synth.py paper.pdf --md     also write paper.synthesis.md
py -3 synth.py paper.pdf --pages  show the per-page extraction only
py -3 synth.py --check            confirm pdftotext and claude are present
py -3 synth.py --selftest         run the scoring self-check
```

## What it is not

Not a fact checker. The content check has three answers: `verified`,
`unverified`, `skipped`. `unverified` means the checker could not find the
claim's wording on the cited page, not that the claim is wrong. Nothing here
can label a claim false, so nothing here does. It replaces reading a blog post
about a paper, not reading the paper.

## How the check works

A claim is `verified` when enough of its distinctive weight (numbers, units,
named terms, weighted above ordinary words) appears on its page. The threshold
is 0.35, chosen from the 40 anchored claims in the shipped example: the one
miscited claim scores 0.16 and the lowest correctly-cited claim scores 0.43, so
0.35 sits in the gap between them. Claims carrying less than 6 total signal are
too thin to judge and come back `skipped`.

## A zero says which zero

Every way the read can fail exits with a BLOCKED message: no `pdftotext`, an
unreadable PDF, a scanned PDF with no text, a reply the model wrapped in the
wrong shape. Only when the model reads every page and anchors nothing does it
print a real zero, and it says so in those words.

## Dependencies

No Python packages. Text comes from `pdftotext`, split into pages on the form
feeds it writes, which is what makes a page number mean anything. The model call
goes through `claude -p` with the flags that strip CLAUDE.md, skills, plugins
and MCP definitions out of the request: measured at $0.0017 a call here against
$0.0240 without them.

## Tests

74 tests in `tests/test_synth.py`. Run them with `py -3 -m pytest`.

## Example output

`attention.synthesis.md`, from *Attention Is All You Need*: 40 claims, 0 dropped
anchors. The PDF is gitignored; bring your own.

## Where it started

From [Matthew Park's synthesis](https://github.com/mattypark/ai-synthesis-research-paper-idea),
a Next.js app that needs an `ANTHROPIC_API_KEY`. This is one Python file with no
packages that runs on a Claude subscription. The part added here is the second
check: his validates that an anchor is in range, this then scores the claim
against what is printed on that page.
