# synth

A research paper, read back as structure, with every claim anchored to a page.

The point is not the summary. It is that each claim carries the page it came
from, and that page is **checked here rather than trusted** — twice. An anchor
pointing outside the paper is set to null and reported. An anchor pointing
inside it is scored against what is actually printed on that page, because in
range is not the same as on that page.

The second check answers "could I find this claim on the page it cites", never
"is this claim true". Its three answers are `verified`, `unverified` and
`skipped`. Nothing here can label a claim false, so nothing here does.

```
py -3 synth.py paper.pdf          read it, write paper.synthesis.json
py -3 synth.py paper.pdf --md     also write paper.synthesis.md
py -3 synth.py paper.pdf --pages  just show the per-page extraction
py -3 synth.py --check            is everything this needs present
```

## Where it came from

Started from [Matthew Park's synthesis](https://github.com/mattypark/ai-synthesis-research-paper-idea).
Same idea, deliberately. His is a Next.js app that needs an `ANTHROPIC_API_KEY`;
this is one Python file with no dependencies that runs on a Claude subscription.

The one thing added: his validates that an anchor is **in range** and drops the
rest to null. This does that, then scores the claim against what is actually
printed on that page - because in range is not the same as on that page.

## What it is not

Not a paper summariser you should trust unread — a claim marked `unverified`
means the checker could not find it on the cited page, not that it is wrong.
Not a fact checker. Not a replacement for reading the paper; it is a
replacement for reading a blog post *about* the paper.

## Dependencies

None in Python. Text comes from `pdftotext`, split back into pages on the form
feeds it writes between them — which is what makes an anchor mean anything. The
model call goes through `claude -p` with the
flags that strip CLAUDE.md, skills, plugins and MCP definitions out of the
request: measured at **$0.0017 a call** here against **$0.0240** without them.

## Example output

`attention.synthesis.md` — *Attention Is All You Need*, 40 claims, 0 dropped
anchors. The PDF itself is gitignored; bring your own.
