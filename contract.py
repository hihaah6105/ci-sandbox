"""
contract.py - Step 1 of the PLE harness.

Reads a task brief and works out exactly what must be produced, before the agent
does anything. No model call, no network: pure text parsing.

Why this exists: in the measured baseline the agent first touched its required
deliverable at a median 65% of the run, and in one case (foundation-strategy)
never touched it at all across 73 steps. Knowing the contract at step 0 is what
lets the harness write a valid placeholder immediately.

Usage
    python3 contract.py <brief.txt>              print the contract as JSON
    python3 contract.py --selftest <briefdir>    score against a folder of briefs
"""
import json, re, sys, os

EXT = r'xlsx|xls|dxf|dwg|docx|doc|pdf|csv|json|py|ifc|md|zip|eml|txt'

# Ordered: the first pattern that matches wins. Earlier patterns are more explicit.
TARGET_PATTERNS = [
    r'write (?:your|the final) deliverable to\s+`([^`]+)`',
    r'write (?:your|the final) deliverable to\s+(/[^\s,;]+\.(?:' + EXT + r'))',
    r'\bsave (?:it|them|this|your work)?\s*(?:as|to)\s+`([^`]+\.(?:' + EXT + r'))`',
    r'\bsave (?:it|them|this|your work)?\s*(?:as|to)\s+(/?[A-Za-z0-9_\-./]+\.(?:' + EXT + r'))',
    r'\b(?:named|called)\s+`?([A-Za-z0-9_\-.]+\.(?:' + EXT + r'))`?',
    r'\bwrite (?:one file|your (?:output|submission))[^`]{0,40}`([^`]+)`',
    r'\breturn your work as[^.]{0,60}?([A-Za-z0-9_\-.]+\.(?:' + EXT + r'))',
]
DIR_PATTERNS = [
    r'save the files that make up your answer under\s+`([^`]+)`',
    r'\bunder\s+`(output/?)`\s+in this workspace',
]


def _norm(p):
    return p.lstrip("./").lstrip("/")


def same_file(a, b):
    """Two paths name the same file if one is a tail of the other.

    The briefs mix absolute container paths with relative ones, so
    /filesystem/<task>/output/submission.zip and output/submission.zip are the
    same file written two ways.
    """
    x, y = _norm(a), _norm(b)
    return x == y or x.endswith("/" + y) or y.endswith("/" + x)


def is_output_path(p):
    """True only for paths inside an output directory.

    Anything else named in a brief is almost always one of the task's own input
    files, and treating those as deliverables would mean overwriting the task.
    """
    return bool(re.search(r"(^|/)(output|submission|deliverables?|out)/", _norm(p).lower()))


def resolve(path, workdir):
    """Absolute container path for a deliverable.

    Briefs mix absolute paths (/filesystem/...) with ones relative to the task's
    working directory (output/solution.py). The evaluator gives the harness the
    working directory, so relative paths are joined onto it.
    """
    if not path:
        return path
    if path.startswith("/"):
        return path
    return (workdir.rstrip("/") + "/" + path.lstrip("./")) if workdir else path


# Phrases that mark a path as something the brief does NOT require on disk:
# either explicitly optional, or a file the delivered program writes when it is
# run. Measured against the kit's dev_set.yaml: data-hall-mep-routing names
# output/solution.py as its only graded deliverable, while output/model.ifc is
# what that script writes at runtime ("writes one IFC4 file to sys.argv[1]
# (default output/model.ifc)") and output/reconciliation.json is introduced as
# "optional side file ... Its absence is not a gate failure". Requiring either
# makes the gate refuse `done` over a file no grader ever reads.
_SOFT = re.compile(
    r"optional|you may write|if modelled|if modeled|absence of the file|"
    r"absence is not|not a gate failure|not required", re.I)
# "... writes one IFC4 file to sys.argv[1] (default output/model.ifc)" - a
# default value for an argument of the delivered program, not a deliverable.
_DEFAULT_OF = re.compile(r"default[^\n]{0,20}$", re.I)


def _soft_paths(text, paths):
    """Paths the brief marks optional, or names only as a program's default output."""
    soft = set()
    for p in paths:
        for m in re.finditer(re.escape(p), text):
            before = text[max(0, m.start() - 240): m.start()]
            if _DEFAULT_OF.search(before) or _SOFT.search(before + text[m.end(): m.end() + 160]):
                soft.add(p)
                break
    return soft


def deliverables(contract):
    """The authoritative list of files this task must produce.

    A zip target is the single deliverable: its named members are checked inside
    the archive, never as separate paths on disk. Paths the brief marks optional
    or describes as runtime output are reported separately, never required.
    """
    target = contract.get("primary_target")
    if target and (contract.get("primary_format") or "").lower() == "zip":
        return [target]
    out = [target] if target else []
    soft = set(contract.get("optional_files") or [])
    for p in (contract.get("required_files") or []):
        if any(same_file(p, q) for q in out):
            continue
        if not is_output_path(p) or p in soft:
            continue
        out.append(p)
    return out


def _first(text, patterns):
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(1).strip()
    return None


def _workspace_paths(text):
    """Every plausible output path named in the brief, best first.

    Prefers paths under output/ or submission/, since that is where PLE tasks put
    deliverables, and drops documentation, examples and templates.
    """
    cands = re.findall(r'`([A-Za-z0-9_\-./]+\.(?:' + EXT + r'))`', text)
    cands += re.findall(r'(/filesystem/[A-Za-z0-9_\-./]+\.(?:' + EXT + r'))', text)
    # bare relative paths written without backticks, e.g.  output/solution.py
    cands += re.findall(r'\b([A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*\.(?:' + EXT + r'))\b', text)
    out, seen = [], set()
    for c in cands:
        low = c.lower()
        if any(b in low for b in ('template', 'example', 'docs/', 'contract.md', 'agent_view', 'readme')):
            continue
        if c in seen:
            continue
        seen.add(c)
        out.append(c)

    def rank(p):
        low = p.lower()
        in_out = 0 if re.search(r'(^|/)(output|submission)/', low) else 1
        ext = low.rsplit('.', 1)[-1]
        ext_rank = {'zip': 0, 'xlsx': 1, 'docx': 1, 'ifc': 1, 'py': 1, 'dxf': 2,
                    'pdf': 2, 'csv': 3, 'json': 4, 'md': 5, 'txt': 5}.get(ext, 3)
        return (in_out, ext_rank, -len(p))

    return sorted(out, key=rank)


def _fallback_path(text):
    p = _workspace_paths(text)
    return p[0] if p else None


def _members(text):
    out, seen = [], set()
    for m in re.finditer(r'^#{2,4}\s*\d+\.\s*`([^`]+\.(?:' + EXT + r'))`(.*)$', text, re.M | re.I):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            out.append({'name': m.group(1), 'note': m.group(2).strip(' -—*')[:120]})
    if not out and 'containing' in text:
        for m in re.finditer(r'`([A-Za-z0-9_\-]+\.(?:' + EXT + r'))`', text.split('containing', 1)[1]):
            if m.group(1) not in seen:
                seen.add(m.group(1))
                out.append({'name': m.group(1), 'note': ''})
    return out


def _sheets(text):
    """Worksheet/section schemas. Handles '* **Name**: `a`, `b`' and '**Name** - `a`, `b`'."""
    sheets = {}
    blocks = re.finditer(
        r'(?:^\s*[*\-]\s*)?\*\*([A-Za-z][A-Za-z0-9 _/]{1,30})\*\*\s*[:\-—]\s*(.{0,1200}?)'
        r'(?=\n\s*[*\-]?\s*\*\*|\n#{2,}|\Z)', text, re.S | re.M)
    for m in blocks:
        cols, seen, keep = re.findall(r'`([a-zA-Z][a-zA-Z0-9_]{1,40})`', m.group(2)), set(), []
        for c in cols:
            if c not in seen and '.' not in c:
                seen.add(c); keep.append(c)
        if len(keep) >= 2:
            sheets[m.group(1).strip()] = keep
    return sheets


def build(text):
    target = _first(text, TARGET_PATTERNS)
    directory = _first(text, DIR_PATTERNS)
    source = 'explicit'
    if not target:
        target = _fallback_path(text)
        source = 'inferred-from-prose' if target else None
    if not target and directory:
        source = 'directory-only'
    if not target and not directory:
        directory, source = 'output/', 'default-output-dir'
    fmt = target.rsplit('.', 1)[-1].lower() if target and '.' in target else None
    required = _workspace_paths(text)
    if target and target not in required:
        required.insert(0, target)
    c = {
        'primary_target': target,
        'required_files': required[:8],
        'target_source': source,
        'output_dir': directory,
        'primary_format': fmt,
        'zip_members': _members(text) if fmt == 'zip' else [],
        'sheets': _sheets(text),
        'cad_layers': sorted(set(re.findall(r'layer\s+`([A-Z0-9\-_]+)`', text))),
        'first_version_by_step': (lambda m: int(m.group(1)) if m else None)(
            re.search(r'within your first\s*~?(\d+)\s*steps', text, re.I)),
    }
    # The primary target is never demoted, however it is phrased.
    c['optional_files'] = sorted(_soft_paths(text, required[:8]) - {target})
    c['deliverables'] = deliverables(c)
    return c


def selftest(d, manifest=None):
    truth = {}
    if manifest:
        for tid, rec in json.load(open(manifest))['tasks'].items():
            fs = [k for k in (rec.get('files') or {}) if 'db_diff' not in k]
            truth[tid] = [os.path.basename(f) for f in fs]
    briefs = sorted(f for f in os.listdir(d) if f.endswith('.txt'))
    hit = miss = nogold = 0
    print(f"{'task':34s} {'extracted':>42s} {'source':>20s} {'match':>6s}")
    for b in briefs:
        tid = b[:-4]
        c = build(open(os.path.join(d, b), encoding='utf8', errors='ignore').read())
        got = os.path.basename(c['primary_target']) if c['primary_target'] else (c['output_dir'] or '-')
        gold = truth.get(tid, [])
        if not gold:
            verdict, nogold = 'NOGOLD', nogold + 1
        elif got in gold:
            verdict, hit = 'yes', hit + 1
        else:
            verdict, miss = 'NO', miss + 1
        print(f"{tid:34s} {got[:42]:>42s} {str(c['target_source'])[:20]:>20s} {verdict:>6s}")
    print(f"\nmatched {hit}/{hit + miss} tasks that produced a file "
          f"({100 * hit / max(hit + miss, 1):.0f}%); {miss} wrong; {nogold} produced nothing to compare")


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--selftest':
        selftest(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif len(sys.argv) > 1:
        print(json.dumps(build(open(sys.argv[1], encoding='utf8', errors='ignore').read()), indent=2))
    else:
        print(__doc__)
