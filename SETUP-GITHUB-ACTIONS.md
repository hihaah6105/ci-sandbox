# Running the PLE dev set on GitHub Actions

Six steps. Nothing here needs you to write code, and you never type a key
anywhere except GitHub's own secrets page.

## Why we're doing this

Your laptop has 16 GB of RAM and one of it. A GitHub Actions runner on a
**public** repository gives 4 vCPU and 16 GB free with no cap on minutes, and
every task gets its own fresh machine — so tasks run several at a time instead
of one after another, overnight, without your laptop on.

## Step 1 — visibility is already decided

This repo is **private**, which means it is genuinely not findable. The trade-off
is the runner: a private repo gets **2 vCPU and 8 GB RAM** and draws on 2,000
free Actions minutes a month, where a public one gets 4 vCPU, 16 GB and
unlimited minutes. Either way the SSD is 14 GB, which is why the workflow's
first step frees up disk.

Note what privacy does and does not buy. An obscure name on a *public* repo
protects nothing, because GitHub indexes public repository contents for code
search — anyone searching for `submission.yaml` or a sponsored model id would
find it. Private actually hides it.

Two consequences to watch:

- **8 GB may be too little for the heavier tasks.** The kit sizes a task slot at
  roughly 16 GB. Light tasks should be fine; if a heavy one is killed for memory,
  that is the reason, and switching this repo to public is the fix.
- **Minutes are metered.** A job can run up to 330 minutes, so a careless full
  29-task run can exhaust the monthly allowance in one go. Start with one task
  and a small `agent_minutes`.

Visibility is reversible at any time in Settings → General.

## Step 2 — add the workflow file

Put `ple-dev-run.yml` at this exact path in the repo:

```
.github/workflows/ple-dev-run.yml
```

Easiest way in the browser: Add file → Create new file → type the path above →
paste the contents → Commit.

## Step 3 — add your two secrets

Settings → Secrets and variables → Actions → New repository secret.

| Name | What it is |
|---|---|
| `HF_TOKEN` | A Hugging Face **read** token. The PLE dataset is gated, so the runner needs it to download the tasks. |
| `MODEL_API_KEY` | The key for whichever free dev model you're using. |

Type these straight into GitHub. Don't paste them into a chat, a file, or a
commit — and don't send them to me. Encrypted secrets are never printed in logs,
and they are not given to pull requests from forks.

If your `submission.yaml` expects a differently-named variable (say
`OPENROUTER_API_KEY` or `GEMINI_API_KEY`), change the env line in the workflow to
match. Only the name needs to agree.

## Step 4 — run one small task first

Actions tab → **PLE dev run** → Run workflow. Leave the default task
(`landscape-sheet-revision`). It's the lightest one in the set: 25 corpus files,
a plain Word memo out, no CAD or IFC toolchain needed. The baseline scored 0.857
on it, so a working setup should produce something non-zero.

Watch for three things in the log:
1. The resource line — confirm 4 CPUs and ~16 GB. If it says 2 CPUs, the repo is
   still private.
2. The disk line after cleanup — you want 40 GB or more free.
3. Whether `eval/run.py` accepted the flags.

**This first run is a dry run of the plumbing, not an experiment.** Expect to fix
one or two things.

## Step 5 — read the result

When the run finishes, the Summary page shows a task/reward table. The full
`artifacts/` folder and the run log are attached at the bottom of the run page as
a downloadable zip, kept for 14 days.

## Step 6 — scale up

Once one task works, put a comma-separated list in the task box, or type `all`
for the full 29. `max-parallel: 4` keeps four running at once so you don't blow
through a free model's requests-per-minute limit — raise it if your provider
allows, lower it to 1 if you hit 429s.

## Things I could not verify, so check them on the first run

- **The exact `eval/run.py` flags.** I used `--tasks <id>`, which the Winning Plan
  and the kit docs both reference, but I haven't run the kit. If it rejects them,
  the kit's own README has the truth and it's a one-line fix.
- **`PLE_AGENT_TIMEOUT_MINUTES`.** I'm passing this to cap the agent's own clock,
  but I'm guessing at the variable name. The real mechanism may be a flag or a
  field in `submission.yaml`. This one matters: see below.

## The timeout trap

A GitHub job is killed at **360 minutes**, and for hosted runners you can only
lower that limit, never raise it. PLE allows the agent 6 hours — exactly the same
360 minutes. So an agent that uses its full budget gets its job killed before it
can upload anything, and you lose the run.

The workflow sets the job to 330 minutes and defaults the agent to 240, which
leaves time for image builds at the start and the artifact upload at the end. Do
not raise the agent cap above 300.

## Concurrency note

A free GitHub plan limits how many jobs run at once across the account, so
`all` will queue in batches rather than firing 29 machines simultaneously. That's
fine — it just takes longer, and it costs nothing.
