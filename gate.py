"""
gate.py - Deterministic conformance checks on the deliverables.

Runs before any model-based review, so mechanical failures never consume
reviewer tokens, and runs again at the end so the harness cannot report success
on a file it never actually produced.

Measured motivation: both data-hall MEP tasks scored 0.0 while their final
messages claimed "express-rules validation reports 0 issues and there are 0
clashes" - the agent wrote its own checks and believed them. Verification was
3.1% of all tool calls across the baseline. These checks are not negotiable by
the model: they read the bytes on disk and compare them to the contract.

Standard library only, except that openpyxl is used for column headers when it
happens to be installed. Everything degrades to a warning rather than a crash.
"""
import io, json, re, zipfile

try:
    from skeleton import PLACEHOLDER_MARK
except ImportError:                                    # pragma: no cover
    PLACEHOLDER_MARK = "PLE-HARNESS-PLACEHOLDER-NOT-YET-COMPLETED"

FAIL, WARN, OK = "FAIL", "WARN", "OK"
_MIN_REAL_BYTES = 256          # below this, a "finished" deliverable is suspect


def _has_marker(blob, depth=0):
    if PLACEHOLDER_MARK.encode() in blob:
        return True
    if depth > 3:
        return False
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        return any(_has_marker(z.read(n), depth + 1) for n in z.namelist())
    except Exception:
        return False


def _sheet_names(xlsx_bytes):
    """Sheet names straight out of xl/workbook.xml, no openpyxl needed."""
    try:
        z = zipfile.ZipFile(io.BytesIO(xlsx_bytes))
        wb = z.read("xl/workbook.xml").decode("utf8", "replace")
        return re.findall(r'<sheet[^>]*\bname="([^"]+)"', wb)
    except Exception:
        return []


def _headers(xlsx_bytes, sheet):
    """First-row values for one sheet. Needs openpyxl; returns None without it."""
    try:
        import openpyxl
    except ImportError:
        return None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
        if sheet not in wb.sheetnames:
            return None
        ws = wb[sheet]
        for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
            return [str(c).strip() for c in row if c is not None]
        return []
    except Exception:
        return None


def _parses(name, blob):
    """(ok, detail) for one file, judged by its extension."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    try:
        if ext == "zip":
            z = zipfile.ZipFile(io.BytesIO(blob))
            if z.testzip() is not None:
                return False, "corrupt archive"
            if not z.namelist():
                return False, "archive is empty"
            return True, f"{len(z.namelist())} members"
        if ext in ("xlsx", "xls", "docx", "doc", "pptx"):
            z = zipfile.ZipFile(io.BytesIO(blob))
            if "[Content_Types].xml" not in z.namelist():
                return False, "not a valid Office package"
            return True, "office package opens"
        if ext == "json":
            json.loads(blob.decode("utf8", "replace"))
            return True, "valid json"
        if ext == "py":
            compile(blob.decode("utf8", "replace"), name, "exec")
            return True, "compiles"
        if ext == "ifc":
            head = blob[:64].lstrip()
            if not head.startswith(b"ISO-10303-21;"):
                return False, "missing ISO-10303-21 header"
            if b"END-ISO-10303-21;" not in blob[-256:]:
                return False, "missing END-ISO-10303-21 footer"
            return True, "ifc header and footer present"
        if ext in ("dxf",):
            if b"EOF" not in blob[-64:]:
                return False, "missing DXF EOF"
            return True, "dxf terminated"
        if ext == "pdf":
            if not blob.startswith(b"%PDF-"):
                return False, "missing %PDF header"
            if b"%%EOF" not in blob[-2048:]:
                return False, "missing %%EOF"
            return True, "pdf header and trailer present"
        return True, f"{len(blob)} bytes"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _same_tail(a, b):
    x, y = a.lstrip("./").lstrip("/"), b.lstrip("./").lstrip("/")
    return x == y or x.endswith("/" + y) or y.endswith("/" + x)


def check(contract, reader, final=True, extra_paths=None, output_files=None):
    """Check the contract against what is actually on disk.

    Args:
      contract: dict from contract.build()
      reader:   callable(path) -> bytes, or None if the file is absent
      final:    True for the end-of-run gate (a placeholder is then a FAIL);
                False mid-run (a placeholder is expected and only noted)

    Returns:
      (verdict, findings) where verdict is FAIL/WARN/OK and findings is a list
      of (severity, path, message).
    """
    findings = []
    targets = list(contract.get("deliverables") or [])
    for p in (extra_paths or []):
        if not any(_same_tail(p, t) for t in targets):
            targets.append(p)

    if not targets:
        # Briefs that name no file say "save your answer under output/", and
        # output/ is what the judge reads. There is no schema to check, but
        # there is still one thing that must be true: the directory cannot be
        # empty. Without this the gate reports "nothing to verify" and the
        # agent treats that as permission to call done having written nothing.
        d = contract.get("output_dir") or "output/"
        listed = sorted(output_files) if output_files else []
        if output_files is None:
            findings.append((WARN, d,
                             "the brief names no file, so any file under this directory is the answer"))
        elif not listed:
            findings.append((FAIL, d,
                             "the brief names no file, so this directory is the answer - and it is "
                             "empty. Write the deliverable the brief asks for into it before finishing."))
        else:
            findings.append((OK, d, "answer files present: " + ", ".join(listed[:12])))

    for path in targets:
        blob = reader(path)
        if blob is None:
            findings.append((FAIL, path, "required deliverable does not exist"))
            continue
        if not blob:
            findings.append((FAIL, path, "file exists but is empty"))
            continue

        name = path.rsplit("/", 1)[-1]
        ok, detail = _parses(name, blob)
        findings.append((OK if ok else FAIL, path,
                         f"{'parses' if ok else 'does not parse'}: {detail}"))
        if not ok:
            continue

        if _has_marker(blob):
            findings.append((FAIL if final else WARN, path,
                             "still contains the placeholder marker - the work is not finished"))
        elif final and len(blob) < _MIN_REAL_BYTES:
            findings.append((WARN, path, f"only {len(blob)} bytes; suspiciously small for a deliverable"))

        # zip must carry every member the brief named
        if name.lower().endswith(".zip"):
            want = [m["name"] for m in (contract.get("zip_members") or [])]
            if want:
                have = set(zipfile.ZipFile(io.BytesIO(blob)).namelist())
                basenames = {h.rsplit("/", 1)[-1] for h in have}
                for w in want:
                    wb_ = w.rsplit("/", 1)[-1]
                    if w in have or wb_ in basenames:
                        continue
                    # A brief may name a member as a pattern, e.g. CT_CALC_P.xlsx
                    # where P stands for each plant (CT_CALC_CUP.xlsx and so on).
                    # Same prefix and extension means the member is probably
                    # present under a real name, so warn instead of failing.
                    stem, _, ext = wb_.rpartition(".")
                    prefix = stem.rsplit("_", 1)[0] if "_" in stem else stem
                    siblings = [h for h in basenames
                                if h.lower().endswith("." + ext.lower())
                                and h.lower().startswith(prefix.lower())]
                    if siblings:
                        findings.append((WARN, f"{path}!{w}",
                                         "named member absent; the brief name may be a pattern "
                                         f"(found: {', '.join(sorted(siblings)[:4])})"))
                    else:
                        findings.append((FAIL, f"{path}!{w}", "named archive member is missing"))

        # worksheet schema
        sheets_wanted = contract.get("sheets") or {}
        xlsx_blobs = {}
        if name.lower().endswith((".xlsx", ".xls")):
            xlsx_blobs[name] = blob
        elif name.lower().endswith(".zip"):
            z = zipfile.ZipFile(io.BytesIO(blob))
            for m in z.namelist():
                if m.lower().endswith((".xlsx", ".xls")):
                    xlsx_blobs[m] = z.read(m)
        for wbname, wbblob in xlsx_blobs.items():
            present = _sheet_names(wbblob)
            for sheet, cols in sheets_wanted.items():
                if sheet not in present:
                    findings.append((WARN, f"{path}!{wbname}",
                                     f"sheet '{sheet}' named in the brief is not in the workbook "
                                     f"(has: {', '.join(present[:8]) or 'none'})"))
                    continue
                got = _headers(wbblob, sheet)
                if got is None:
                    continue
                missing = [c for c in cols if c not in got]
                if missing:
                    findings.append((WARN, f"{path}!{wbname}:{sheet}",
                                     f"missing column headers: {', '.join(missing[:10])}"))

        # CAD layers
        if name.lower().endswith(".dxf") and contract.get("cad_layers"):
            txt = blob.decode("utf8", "replace")
            for layer in contract["cad_layers"]:
                if layer not in txt:
                    findings.append((WARN, path, f"layer '{layer}' named in the brief is absent"))

    verdict = FAIL if any(s == FAIL for s, _, _ in findings) else (
        WARN if any(s == WARN for s, _, _ in findings) else OK)
    return verdict, findings


def report(verdict, findings):
    lines = [f"conformance gate: {verdict}"]
    for sev, path, msg in findings:
        lines.append(f"  [{sev}] {path}: {msg}")
    return "\n".join(lines)
