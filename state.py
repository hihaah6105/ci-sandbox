"""
state.py - The bounded working state that replaces the raw transcript.

This is the research claim, so it is built as a switch rather than a belief:
  PLE_STATE_MODE=transcript   resend the whole conversation (the baseline)
  PLE_STATE_MODE=bounded      resend a fixed-size state plus recent turns
Flipping that one variable is the whole A/B.

Measured motivation, from the baseline archive: completion tokens were 1.5% of
all tokens, 97% of prompt tokens were cached re-reads, and only 0.3% of
observation bytes were ever read twice - so the transcript carries each file
forever while the agent uses it once. Modelling a fixed state at 8k tokens put
the 29-task total at 23.6M tokens against 235M, about 10x.

Nothing here calls a model. est_tokens() is chars/4, the same estimator used to
produce those numbers, so the logged figures are comparable.
"""
import json, os

MODE = os.environ.get("PLE_STATE_MODE", "bounded").strip().lower()
STATE_TOKEN_CAP = int(os.environ.get("PLE_STATE_CAP", "8000"))
RECENT_TURNS = int(os.environ.get("PLE_RECENT_TURNS", "6"))
OBS_CLIP = int(os.environ.get("PLE_OBS_CLIP", "4000"))     # chars kept per observation


def est_tokens(text):
    return int(len(text) / 4)


def clip(text, limit=OBS_CLIP):
    """Keep the head and tail of a long observation; the middle is rarely load-bearing."""
    if text is None:
        return ""
    text = str(text)
    if len(text) <= limit:
        return text
    head, tail = int(limit * 0.7), int(limit * 0.3)
    return (text[:head] + f"\n...[{len(text) - limit} characters omitted]...\n" + text[-tail:])


class State:
    """The five tables. Bounded by construction: rows are capped, not appended forever."""

    MAX = {"files": 120, "facts": 150, "constraints": 60, "decisions": 120, "checks": 60}

    def __init__(self, contract):
        self.contract = contract
        self.files = []        # (path, one-line note) - the document map
        self.facts = []        # (quantity, value, unit, source) - evidence
        self.constraints = []  # (name, limit, unit, hard/soft, source)
        self.decisions = []    # (item, choice, slack_note)
        self.checks = []       # (what, result)
        self.notes = []        # free-form, last few only

    def _add(self, bucket, row):
        rows = getattr(self, bucket)
        if row in rows:
            return
        rows.append(row)
        cap = self.MAX[bucket]
        if len(rows) > cap:
            del rows[: len(rows) - cap]

    def note_file(self, path, note=""):   self._add("files", (path, note[:100]))
    def add_fact(self, q, v, u, src):     self._add("facts", (q[:60], str(v)[:40], u[:16], src[:90]))
    def add_constraint(self, n, lim, u, hard, src):
        self._add("constraints", (n[:60], str(lim)[:30], u[:16], "hard" if hard else "soft", src[:90]))
    def add_decision(self, item, choice, slack=""):
        self._add("decisions", (item[:60], str(choice)[:60], str(slack)[:40]))
    def add_check(self, what, result):    self._add("checks", (what[:80], str(result)[:80]))

    def add_note(self, text):
        self.notes.append(str(text)[:300])
        del self.notes[:-8]

    def render(self, cap=None):
        """Compact text form of the state, trimmed to the token cap."""
        cap = STATE_TOKEN_CAP if cap is None else cap
        c = self.contract
        parts = ["## Deliverable contract (fixed - do not change)"]
        for p in (c.get("deliverables") or []):
            parts.append(f"- MUST EXIST: {p}")
        for m in (c.get("zip_members") or []):
            parts.append(f"- archive member: {m['name']}")
        for sheet, cols in (c.get("sheets") or {}).items():
            parts.append(f"- sheet {sheet}: {', '.join(cols)}")
        for layer in (c.get("cad_layers") or []):
            parts.append(f"- CAD layer: {layer}")

        def table(title, rows, fmt):
            if not rows:
                return
            parts.append(f"\n## {title} ({len(rows)})")
            parts.extend(fmt(r) for r in rows)

        table("Files seen", self.files, lambda r: f"- {r[0]}" + (f" - {r[1]}" if r[1] else ""))
        table("Evidence", self.facts, lambda r: f"- {r[0]} = {r[1]} {r[2]}  [{r[3]}]")
        table("Constraints (frozen once set)", self.constraints,
              lambda r: f"- {r[0]} {r[1]} {r[2]} ({r[3]})  [{r[4]}]")
        table("Decisions", self.decisions,
              lambda r: f"- {r[0]}: {r[1]}" + (f"  slack {r[2]}" if r[2] else ""))
        table("Checks run", self.checks, lambda r: f"- {r[0]} -> {r[1]}")
        if self.notes:
            parts.append("\n## Recent notes")
            parts.extend(f"- {n}" for n in self.notes)

        text = "\n".join(parts)
        if est_tokens(text) <= cap:
            return text
        # Trim from the least load-bearing end: notes, then files, then evidence.
        keep = text
        for bucket in ("Recent notes", "Files seen", "Evidence", "Checks run"):
            marker = f"\n## {bucket}"
            if marker in keep and est_tokens(keep) > cap:
                keep = keep.split(marker)[0] + f"\n[{bucket} trimmed to fit the state budget]"
        return keep[: cap * 4]


class Conversation:
    """Builds the message list for each model call, in either mode."""

    def __init__(self, system, state):
        self.system, self.state = system, state
        self.turns = []            # (role, content) in order
        self.peak_tokens = 0
        self.total_prompt_estimate = 0

    MAX_TURNS_KEPT = 60     # bounded in memory too, not just on the wire

    def add(self, role, content):
        self.turns.append((role, str(content)))
        if MODE != "transcript" and len(self.turns) > self.MAX_TURNS_KEPT:
            del self.turns[: len(self.turns) - self.MAX_TURNS_KEPT]

    def messages(self):
        if MODE == "transcript":
            body = list(self.turns)
        else:
            recent = self.turns[-(RECENT_TURNS * 2):]
            body = [("user", self.state.render())] + recent
        msgs = [{"role": "system", "content": self.system}]
        for role, content in body:
            msgs.append({"role": "user" if role not in ("assistant",) else "assistant",
                         "content": content})
        n = est_tokens(json.dumps(msgs))
        self.peak_tokens = max(self.peak_tokens, n)
        self.total_prompt_estimate += n
        return msgs

    def stats(self):
        return {"mode": MODE, "turns": len(self.turns), "peak_prompt_tokens": self.peak_tokens,
                "cumulative_prompt_tokens_estimate": self.total_prompt_estimate,
                "state_cap": STATE_TOKEN_CAP}
