"""
skeleton.py - Step 2: write a valid, clearly-marked placeholder deliverable
              immediately, before the agent starts work.

Why: in the measured baseline, two of 29 tasks scored 0.0 because the run ended
before any deliverable existed - foundation-strategy never once referenced its
required submission.zip across 73 steps. The Pi prompt already *told* the agent
to write an early version and the agent ignored it. Instruction is not
enforcement, so the harness does it mechanically.

Everything here is built with the standard library only. .xlsx and .docx are
just ZIP archives of XML, so we can emit valid ones without openpyxl or
python-docx, keeping the install step small and offline-safe.

Every placeholder carries PLACEHOLDER_MARK. gate.py fails any deliverable that
still contains it at the end of the run, so a placeholder can never be passed
off as finished work.
"""
import io, json, posixpath, re, zipfile


def _same_tail(a, b):
    x, y = a.lstrip("./").lstrip("/"), b.lstrip("./").lstrip("/")
    return x == y or x.endswith("/" + y) or y.endswith("/" + x)

PLACEHOLDER_MARK = "PLE-HARNESS-PLACEHOLDER-NOT-YET-COMPLETED"
_NOTE = ("This file was created automatically at the start of the run so that a "
         "gradable deliverable always exists. If this text is still here, the "
         "work was not finished. " + PLACEHOLDER_MARK)


# ---------- minimal OOXML ----------

_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
%s</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="%s"/>
</Relationships>"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def xlsx_bytes(sheets):
    """sheets: {sheet_name: [column, ...]}. Emits a workbook with one header row each."""
    if not sheets:
        sheets = {"Sheet1": ["note"]}
    names = list(sheets)[:24]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        overrides = ('<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     + "".join(f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" '
                               'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                               for i in range(len(names))))
        z.writestr("[Content_Types].xml", _CT % overrides)
        z.writestr("_rels/.rels", _RELS % "xl/workbook.xml")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{_esc(n)[:31]}" sheetId="{i+1}" r:id="rId{i+1}"/>'
                             for i, n in enumerate(names))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i+1}" '
                             'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                             f'Target="worksheets/sheet{i+1}.xml"/>' for i in range(len(names)))
                   + "</Relationships>")
        for i, n in enumerate(names):
            cols = (sheets[n] or ["note"])[:64]
            cells = "".join(
                f'<c r="{chr(65 + (j % 26))}1" t="inlineStr"><is><t>{_esc(c)}</t></is></c>'
                for j, c in enumerate(cols))
            z.writestr(f"xl/worksheets/sheet{i+1}.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       f'<sheetData><row r="1">{cells}</row>'
                       f'<row r="2"><c r="A2" t="inlineStr"><is><t>{_esc(PLACEHOLDER_MARK)}</t></is></c></row>'
                       "</sheetData></worksheet>")
    return out.getvalue()


def docx_bytes(title, paragraphs=()):
    paras = [title] + list(paragraphs or []) + [_NOTE]
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{_esc(p)}</w:t></w:r></w:p>' for p in paras)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT % (
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'))
        z.writestr("_rels/.rels", _RELS % "word/document.xml")
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f"<w:body>{body}</w:body></w:document>")
    return out.getvalue()


def ifc_bytes(name="placeholder.ifc"):
    return (
        "ISO-10303-21;\nHEADER;\n"
        f"FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');\n"
        f"FILE_NAME('{name}','1970-01-01T00:00:00',(''),(''),'','{PLACEHOLDER_MARK}','');\n"
        "FILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n"
        "#1=IFCPROJECT('0000000000000000000000',$,'Placeholder',$,$,$,$,$,$);\n"
        "ENDSEC;\nEND-ISO-10303-21;\n").encode()


def dxf_bytes():
    return ("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n"
            "0\nTEXT\n8\n0\n10\n0.0\n20\n0.0\n40\n1.0\n1\n"
            f"{PLACEHOLDER_MARK}\n0\nENDSEC\n0\nEOF\n").encode()


def pdf_bytes():
    txt = f"({PLACEHOLDER_MARK}) Tj"
    content = f"BT /F1 12 Tf 72 720 Td {txt} ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
        b"<< /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, start))
    return bytes(out)


def py_bytes(note=""):
    return (f'"""Placeholder solution. {PLACEHOLDER_MARK}\n{note}\n"""\n'
            "import sys, pathlib\n\n"
            "def main(out='output/model.ifc'):\n"
            "    p = pathlib.Path(out); p.parent.mkdir(parents=True, exist_ok=True)\n"
            "    p.write_text('ISO-10303-21;\\nEND-ISO-10303-21;\\n')\n\n"
            "if __name__ == '__main__':\n"
            "    main(sys.argv[1] if len(sys.argv) > 1 else 'output/model.ifc')\n").encode()


# ---------- dispatch ----------

def file_for(name, sheets=None):
    """Valid placeholder bytes for one filename, chosen by extension."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "txt"
    if ext in ("xlsx", "xls"):
        return xlsx_bytes(sheets or {})
    if ext in ("docx", "doc"):
        return docx_bytes("Placeholder - work not yet completed")
    if ext == "ifc":
        return ifc_bytes(name)
    if ext in ("dxf", "dwg"):
        return dxf_bytes()
    if ext == "pdf":
        return pdf_bytes()
    if ext == "py":
        return py_bytes()
    if ext == "json":
        return json.dumps({"status": "placeholder", "marker": PLACEHOLDER_MARK},
                          indent=2).encode()
    if ext == "csv":
        return f"note\n{PLACEHOLDER_MARK}\n".encode()
    if ext == "eml":
        return (f"Subject: Placeholder\nFrom: harness@example.invalid\n"
                f"To: harness@example.invalid\n\n{_NOTE}\n").encode()
    return (_NOTE + "\n").encode()


def build(contract):
    """Returns {path: bytes} for every file the contract requires.

    A zip target becomes one archive containing each named member; anything else
    becomes the file itself. Sheet schemas found in the brief are applied to the
    first .xlsx member so the header row is already right.
    """
    out = {}
    target = contract.get("primary_target")
    fmt = (contract.get("primary_format") or "").lower()
    sheets = contract.get("sheets") or {}

    if target and fmt == "zip":
        members = [m["name"] for m in (contract.get("zip_members") or [])] or ["README.md"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            used_sheets = False
            for m in members:
                s = sheets if (not used_sheets and m.lower().endswith((".xlsx", ".xls"))) else {}
                if s:
                    used_sheets = True
                z.writestr(m, file_for(m, s))
        out[target] = buf.getvalue()
        return out

    if target:
        out[target] = file_for(posixpath.basename(target), sheets)

    for extra in (contract.get("deliverables") or []):
        if extra == target or any(_same_tail(extra, k) for k in out):
            continue
        out[extra] = file_for(posixpath.basename(extra), {})

    # Two of the 29 dev briefs (landscape-sheet-revision, fire-test-validation-memo)
    # name no file at all: they say "save your answer under output/", and output/ is
    # what the judge reads. Writing a PLACEHOLDER.md there puts a file stamped
    # NOT-YET-COMPLETED straight into the graded answer, and the gate - which
    # refuses `done` while that marker exists anywhere - then deadlocks the run
    # against a file the harness itself planted. Where there is no file contract
    # there is nothing to pre-create, so write nothing.
    return out


if __name__ == "__main__":
    import sys
    from contract import build as build_contract
    c = build_contract(open(sys.argv[1], encoding="utf8", errors="ignore").read())
    for p, b in build(c).items():
        print(f"{len(b):>9d}  {p}")
