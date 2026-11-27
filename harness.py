"""
harness.py - A staged PLE harness built from what the baseline archive measured.

Order of work, and why each stage exists:

  0. contract   Parse the required deliverable paths and schemas out of the brief
                before any model call. Measured: the baseline first touched its
                required deliverable at a median 65% of the run, and
                foundation-strategy never referenced submission.zip once in 73
                steps.
  1. skeleton   Write a valid, marked placeholder for every missing deliverable
                immediately. The Pi prompt already asked the agent to do this and
                the agent didn't; so the harness does it, not the model. Worth a
                modelled +1.7 to +4.3 dev points.
  2. work       An agent loop over four tools, with a bounded working state
                instead of a growing transcript (see state.py).
  3. gate       Deterministic conformance checks the model cannot argue with.
                Measured: verification was 3.1% of baseline tool calls, and both
                MEP tasks scored 0.0 while reporting their own checks as passing.
  4. finish     Re-run the gate, and say plainly in the final message what passed
                and what did not. A placeholder still on disk is reported as
                unfinished, never as done.

Constraints are frozen once recorded: the `record` tool refuses to change a
constraint's limit. In the baseline, foundation-strategy spent its final steps
editing its own safety factors (phi_v, slide_pass 2.0 -> 1.5) and watching cost
fall, and scored 0.0.
"""
from __future__ import annotations

import asyncio, json, os, time

import contract as contract_mod
import gate as gate_mod
import model as model_mod
import skeleton as skeleton_mod
import state as state_mod

RESERVE_SEC = int(os.environ.get("PLE_RESERVE_SEC", "180"))     # kept for the final gate
MAX_STEPS = int(os.environ.get("PLE_MAX_STEPS", "160"))
GATE_EVERY = int(os.environ.get("PLE_GATE_EVERY", "15"))
MAX_IDLE = int(os.environ.get("PLE_MAX_IDLE", "3"))   # replies with no tool call

SYSTEM = """You are a licensed professional engineer completing a billable deliverable.

How this harness works, and what it expects of you:

- The deliverable contract has already been parsed from the brief and is shown in
  your working state. Valid placeholder files already exist at those paths. Your
  job is to replace their contents with real work, at exactly those paths.
- Record what you learn with the `record` tool: evidence (value, unit, source
  file), constraints (limit, unit, hard or soft, governing clause), decisions
  (what you chose and how much slack it leaves), and checks you ran. Your working
  state is what carries between steps, not the conversation.
- Constraints are frozen once recorded. If a design will not pass, change the
  design, never the constraint, the safety factor, or the analysis assumption.
  Trying to loosen a recorded limit will be refused.
- Compute with code rather than in your head. Where feasibility is monotone in a
  catalogue (a larger conductor always lowers voltage drop) search the catalogue
  instead of reasoning about it.
- Where a change to your design alters the demand on other parts - diaphragm
  stiffness redistributing load, a pipe network, a routed service - re-check
  every constraint after every change, and converge before you compare cost.
  Extra stiffness is not spare capacity.
- A deterministic conformance gate checks the files on disk. It runs during the
  run and again at the end. You cannot call `done` until it passes.
- State your uncertainty. Never claim a file exists, or a check passed, without
  having actually produced or run it.
"""


async def _exists(session, path):
    r = await session.exec(f'test -f {json.dumps(path)} && echo YES || echo NO', timeout_sec=30)
    return "YES" in (r.get("output") or "")


async def _read(session, path):
    try:
        return await session.read_file(path)
    except Exception:
        return None


async def _gate(session, con, created, final):
    """Run the conformance gate. Reads happen inline, not through a thread."""
    blobs = {}
    paths = list(con.get("deliverables") or []) + [p for p in created if p not in (con.get("deliverables") or [])]
    for p in paths:
        blobs[p] = await _read(session, p)

    def read(path):
        if path in blobs:
            return blobs[path]
        return next((v for k, v in blobs.items() if gate_mod._same_tail(k, path)), None)

    return gate_mod.check(con, read, final=final, extra_paths=created)


async def solve(session) -> str:
    t0 = time.monotonic()
    brief = session.instruction
    workdir = getattr(session, "workdir", "/") or "/"

    # ---- stage 0: the contract -------------------------------------------
    con = contract_mod.build(brief)
    con["deliverables"] = [contract_mod.resolve(p, workdir) for p in con["deliverables"]]
    await session.log(f"contract: {json.dumps({k: con[k] for k in ('deliverables', 'primary_format')})}")

    st = state_mod.State(con)
    conv = state_mod.Conversation(SYSTEM, st)

    # ---- stage 1: placeholders now, never overwriting anything -----------
    created = []
    for rel, blob in skeleton_mod.build(con).items():
        path = contract_mod.resolve(rel, workdir)
        if await _exists(session, path):
            st.add_note(f"{path} already exists; left untouched")
            continue
        try:
            parent = path.rsplit("/", 1)[0]
            if parent:
                await session.exec(f'mkdir -p {json.dumps(parent)}', timeout_sec=30)
            await session.write_file(path, blob)
            created.append(path)
            st.add_check(f"placeholder written at {path}", "ok")
        except Exception as e:
            st.add_note(f"could not write placeholder {path}: {e}")
    await session.log(f"placeholders created: {created}")

    # ---- stage 2: work ----------------------------------------------------
    try:
        client = model_mod.Client()
    except model_mod.ModelError as e:
        verdict, findings = await _gate(session, con, created, final=True)
        return (f"Could not reach a model endpoint ({e}). Placeholder deliverables "
                f"are in place so the task is gradable.\n\n{gate_mod.report(verdict, findings)}")

    conv.add("user", f"TASK BRIEF\n\n{brief}")
    steps = 0
    idle = 0
    last_text = ""
    while steps < MAX_STEPS:
        left = session.time_left()
        if left is not None and left < RESERVE_SEC:
            st.add_note("stopping work to leave time for the final gate")
            break
        steps += 1
        try:
            reply = await asyncio.to_thread(client.chat, conv.messages(), model_mod.TOOLS)
        except model_mod.ModelError as e:
            st.add_note(f"model error, stopping: {e}")
            break
        u = reply["usage"]
        try:
            await session.usage(client.model, input_tokens=u["in"], output_tokens=u["out"],
                                cache_read_tokens=u.get("cache_read", 0))
        except Exception:
            pass
        if reply["text"]:
            last_text = reply["text"]
            conv.add("assistant", reply["text"])

        if not reply["tool_calls"]:
            idle += 1
            if idle >= MAX_IDLE:
                st.add_note(f"model produced no tool call {idle} times in a row; stopping")
                break
            conv.add("user", "Keep going, or call done once the gate passes.")
            continue
        idle = 0

        finished = False
        for call in reply["tool_calls"]:
            name, args = call["name"], call["args"]
            out = ""
            if name == "bash":
                r = await session.exec(str(args.get("command", "")), timeout_sec=300)
                out = f"exit={r.get('exit_code')}\n{state_mod.clip(r.get('output'))}"
            elif name == "read_file":
                p = contract_mod.resolve(str(args.get("path", "")), workdir)
                b = await _read(session, p)
                out = "not found" if b is None else state_mod.clip(b.decode("utf8", "replace"))
                st.note_file(p, "read")
            elif name == "write_file":
                p = contract_mod.resolve(str(args.get("path", "")), workdir)
                try:
                    n = await session.write_file(p, str(args.get("content", "")))
                    out = f"wrote {n} bytes to {p}"
                except Exception as e:
                    out = f"write failed: {e}"
            elif name == "record":
                out = _record(st, args)
            elif name == "done":
                verdict, findings = await _gate(session, con, created, final=True)
                if verdict == gate_mod.FAIL:
                    out = ("REFUSED: the conformance gate fails, so the work is not done.\n"
                           + gate_mod.report(verdict, findings))
                    st.add_check("done refused by gate", verdict)
                else:
                    last_text = str(args.get("summary") or last_text)
                    finished = True
                    out = "accepted"
            else:
                out = f"unknown tool {name}"
            conv.add("user", f"[{name}] {state_mod.clip(out)}")
        if finished:
            break

        if steps % GATE_EVERY == 0:
            verdict, findings = await _gate(session, con, created, final=False)
            st.add_check(f"interim gate at step {steps}", verdict)
            conv.add("user", gate_mod.report(verdict, findings))

    # ---- stage 3/4: final gate and an honest final message ---------------
    verdict, findings = await _gate(session, con, created, final=True)
    stats = conv.stats()
    stats.update(steps=steps, seconds=int(time.monotonic() - t0))
    await session.log("state stats: " + json.dumps(stats))

    lines = [last_text.strip() or "Work completed.", "", gate_mod.report(verdict, findings)]
    if verdict == gate_mod.FAIL:
        lines.insert(0, "NOT COMPLETE - the conformance gate did not pass. Details below.\n")
    if st.constraints:
        lines += ["", "Constraints applied:"]
        lines += [f"- {n} {lim} {u} ({h}) [{src}]" for n, lim, u, h, src in st.constraints[:40]]
    if st.decisions:
        lines += ["", "Decisions and slack:"]
        lines += [f"- {i}: {c}" + (f" (slack {s})" if s else "") for i, c, s in st.decisions[:40]]
    lines += ["", f"harness: {json.dumps(stats)}"]
    return "\n".join(lines)


def _record(st, args):
    kind = str(args.get("kind", "note")).lower()
    name = str(args.get("name", ""))
    value = str(args.get("value", ""))
    unit = str(args.get("unit", ""))
    source = str(args.get("source", ""))
    if kind == "fact":
        st.add_fact(name, value, unit, source)
        return f"recorded evidence: {name} = {value} {unit}"
    if kind == "constraint":
        for n, lim, u, h, src in st.constraints:
            if n == name[:60] and lim != str(value)[:30]:
                return (f"REFUSED: constraint '{name}' is already frozen at {lim} {u} from {src}. "
                        "Change the design, not the constraint.")
        st.add_constraint(name, value, unit, bool(args.get("hard")), source)
        return f"recorded constraint: {name} {value} {unit}"
    if kind == "decision":
        st.add_decision(name, value, str(args.get("slack", "")))
        return f"recorded decision: {name} = {value}"
    if kind == "check":
        st.add_check(name, value)
        return f"recorded check: {name} -> {value}"
    st.add_note(f"{name} {value}".strip())
    return "noted"
