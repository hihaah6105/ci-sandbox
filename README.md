# contract-first — a PLE agent harness

An entry for *Google — Agent Harness on Physical-Engineering's Last Exam*.

Every design choice here comes from a measurement taken on the organizer-supplied
baseline archive (29 tasks, Pi + Claude Opus 5.5, local mean reward 0.4912). The
measurements are written up in the project docs `FINDINGS-2026-11-27-*`.

## What the baseline measurement showed

| finding | number |
|---|---|
| Completion tokens as a share of all tokens | **1.5%** — the model reads, it barely writes |
| Cached (re-sent) share of prompt tokens | **93–97%** |
| Observation bytes ever read twice | **0.3%** — each file is read about once |
| Reading vs verifying, as a share of tool calls | **64.7%** vs **3.1%** |
| Correlation of prompt tokens with reward | **−0.42** |
| Correlation of verification share with reward | **+0.24** |
| Median first touch of the required deliverable | **65% of the run** |
| Design tasks where the design changes its own demand | mean **0.245**, vs **0.601** when it does not |

Read together: the budget goes almost entirely to re-reading a transcript the
agent never consults twice, while the one activity that correlates with success
gets 3% of the effort — and the deliverable is an afterthought until two thirds
of the way through.

## How the harness is built

```
brief ──▶ 0 contract ──▶ 1 skeleton ──▶ 2 work ──▶ 3 gate ──▶ 4 finish
          no model        no model       model      no model   no model
```

**0. Contract** (`contract.py`) parses the required deliverable paths, formats,
worksheet schemas and CAD layer names straight out of the brief, before any model
call. Scored **25/27 (93%)** against the 29 real briefs, and the two it does not
pin down are the tasks that deliberately name no file, where it returns the output
directory instead. It handles seven different phrasings found in the briefs.

**1. Skeleton** (`skeleton.py`) writes a valid, clearly-marked placeholder for
every missing deliverable immediately. `.xlsx` and `.docx` are built as raw
OOXML, so no Office library is needed; all 33 generated placeholders were
verified to open with `openpyxl` / `python-docx`, compile, or carry a valid
header. It **never overwrites an existing file** — several briefs name their own
*input* documents, and writing over those would destroy the task.

**2. Work** (`harness.py`, `state.py`, `model.py`) is an agent loop over five
tools — `bash`, `read_file`, `write_file`, `record`, `done` — with a **bounded
working state** in place of a growing transcript. Constraints are frozen once
recorded: `record` refuses to change a constraint's limit, so a design that will
not pass has to change the design. In the baseline, `foundation-strategy` spent
its last steps editing its own safety factors (`slide_pass` 2.0 → 1.5) while
watching cost fall, and scored 0.0.

**3. Gate** (`gate.py`) checks the bytes on disk against the contract: files
exist at the exact paths, parse in their format, archives contain the named
members, worksheets have the named sheets and headers. `done` is **refused**
while the gate fails. On the baseline archive this gate flags exactly the four
tasks that scored 0.000 and none of the twenty-five that did not.

**4. Finish** re-runs the gate and reports what passed and what did not. A
placeholder still on disk is reported as unfinished, never as done.

## Running it

**Offline smoke test, no Docker, no model, no cost:**

```bash
python3 run_local.py path/to/brief.txt /filesystem/<task-workdir>
```

**A free dev run:** copy `submission.dev.yaml` over `submission.yaml`, set
`MODEL_API_KEY`, and use the kit: `uv run python eval/run.py <this-entry>`.
`kit check` will not call the dev config submittable — that is correct, the
competition accepts only `google-cloud/<id>` models.

**On GitHub Actions**, one runner per task: see `SETUP-GITHUB-ACTIONS.md`.
A private repo gets 2 vCPU / 8 GB and 2,000 free minutes a month; a public one
gets 4 vCPU / 16 GB and unlimited minutes.

## The experiment switch

```bash
PLE_STATE_MODE=transcript   # resend the whole conversation, like the baseline
PLE_STATE_MODE=bounded      # fixed-size state plus recent turns (default)
```

One variable, the whole A/B. On a 50-step scripted run with ~6 KB observations,
bounded was **5.9× cheaper cumulatively and 8.6× smaller at peak**. Cost grows
quadratically with steps, so longer runs widen the gap.

Other knobs: `PLE_STATE_CAP` (8000), `PLE_RECENT_TURNS` (6), `PLE_OBS_CLIP`
(4000), `PLE_MAX_STEPS` (160), `PLE_GATE_EVERY` (15), `PLE_RESERVE_SEC` (180),
`PLE_MAX_IDLE` (3).

## Before this will run

1. Put your name in `LICENSE`.
2. For a local run outside CI, copy `taskbridge.py` in from the kit's
   `sample_submissions/`. The GitHub Actions workflow does this for you.

## Honest limits

- The 5.9× figure measures the harness's token plumbing with a stub model. It
  says nothing yet about score. That needs a real dev run.
- The 10× projection in the findings assumes an 8k state is sufficient. Whether
  it is, is exactly what the first real A/B tests.
- Nothing here has been run against a live grader. No score is claimed.
- The exact `eval/run.py` flags and the mechanism for capping the agent clock
  were not verified against a live kit; check them on the first run.
- The convergence loop for coupled design tasks — the 0.245 group — is **not
  built yet**. The system prompt asks for it; nothing enforces it. That is the
  next piece.
