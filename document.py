"""
document.py - Turn plain markdown into the deliverable's real file format.

Why this exists. In run 8 the agent read documents for 23 steps with pypdf and
openpyxl and never wrote the memo, leaving the 1,085-byte placeholder in place
and scoring 0.016 on a task the official starter scored 1.000. To produce a
.docx it had to hand-write python-docx boilerplate inside a shell command, and
it never attempted it. That is a lot of machinery between a model and the one
thing it is reliably good at, which is writing prose.

So the harness does the format and the model does the writing. `write_document`
takes markdown and emits a real .docx, .xlsx, .md, .txt or .pdf. Nothing here
carries skeleton.PLACEHOLDER_MARK, so writing through this module is what
clears the conformance gate.

Standard library only, same as skeleton.py: .docx and .xlsx are ZIP archives of
XML, so no python-docx or openpyxl is needed and the install stays offline-safe.
"""
from __future__ import annotations

import io
import re
import zipfile

from skeleton import _CT, _RELS, _esc

SUPPORTED = (".docx", ".xlsx", ".md", ".txt", ".csv", ".json", ".py", ".pdf")


def _lines(markdown):
    return [ln.rstrip() for ln in str(markdown or "").replace("\r\n", "\n").split("\n")]


# ---------- markdown pipe tables ----------

def _is_rule(line):
    """The |---|---| separator under a markdown table header."""
    s = line.strip()
    return bool(s) and set(s) <= set("|-: ") and "-" in s


def tables(markdown):
    """Every markdown pipe table, as (caption, [[cell, ...], ...])."""
    found, caption, rows = [], "", []
    for line in _lines(markdown):
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and len(s) > 1:
            if _is_rule(s):
                continue
            rows.append([c.strip() for c in s[1:-1].split("|")])
            continue
        if rows:
            found.append((caption, rows))
            rows = []
        if s.startswith("#"):
            caption = s.lstrip("#").strip()
    if rows:
        found.append((caption, rows))
    return found


# ---------- .docx ----------

def docx_bytes(markdown):
    """Headings become bold paragraphs; table rows become tab-separated lines."""
    body = []
    for line in _lines(markdown):
        s = line.strip()
        if not s:
            continue
        if _is_rule(s):
            continue
        bold = False
        if s.startswith("#"):
            s, bold = s.lstrip("#").strip(), True
        elif s.startswith("**") and s.endswith("**") and len(s) > 4:
            s, bold = s[2:-2], True
        if s.startswith("|") and s.endswith("|"):
            s = "\t".join(c.strip() for c in s[1:-1].split("|"))
        props = "<w:rPr><w:b/></w:rPr>" if bold else ""
        body.append(f'<w:p><w:r>{props}<w:t xml:space="preserve">{_esc(s)}</w:t></w:r></w:p>')
    if not body:
        body.append('<w:p><w:r><w:t/></w:r></w:p>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CT % (
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/>'))
        z.writestr("_rels/.rels", _RELS % "word/document.xml")
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                   'wordprocessingml/2006/main">'
                   f'<w:body>{"".join(body)}</w:body></w:document>')
    return out.getvalue()


# ---------- .xlsx ----------

def _col(j):
    """0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    j += 1
    while j:
        j, rem = divmod(j - 1, 26)
        name = chr(65 + rem) + name
    return name


_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def xlsx_bytes(markdown):
    """Every markdown table becomes a sheet. Numeric-looking cells stay numeric."""
    found = tables(markdown) or [("Sheet1", [["note"], [str(markdown or "")[:200]]])]
    names, seen = [], set()
    for i, (caption, _) in enumerate(found):
        n = (caption or f"Sheet{i + 1}")[:31] or f"Sheet{i + 1}"
        n = re.sub(r"[\[\]\*/\\\?:]", "-", n)
        while n.lower() in seen:
            n = (n + "_")[:31]
        seen.add(n.lower())
        names.append(n)

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        overrides = ('<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
                     'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                               'ContentType="application/vnd.openxmlformats-officedocument.'
                               'spreadsheetml.worksheet+xml"/>' for i in range(len(names))))
        z.writestr("[Content_Types].xml", _CT % overrides)
        z.writestr("_rels/.rels", _RELS % "xl/workbook.xml")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships"><sheets>'
                   + "".join(f'<sheet name="{_esc(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                             for i, n in enumerate(names))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                   'relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.'
                             'openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                             f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(names)))
                   + "</Relationships>")
        for i, (_, rows) in enumerate(found):
            xml = []
            for r, row in enumerate(rows[:5000], 1):
                cells = []
                for j, cell in enumerate(row[:256]):
                    ref = f"{_col(j)}{r}"
                    if _NUM.match(cell.strip()):
                        cells.append(f'<c r="{ref}"><v>{_esc(cell.strip())}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space='
                                     f'"preserve">{_esc(cell)}</t></is></c>')
                xml.append(f'<row r="{r}">{"".join(cells)}</row>')
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
                       f'2006/main"><sheetData>{"".join(xml)}</sheetData></worksheet>')
    return out.getvalue()


# ---------- .pdf ----------

def pdf_bytes(markdown):
    """One page per 52 lines, Helvetica 11pt. Enough for a memo, not a report."""
    rows = [ln for ln in _lines(markdown)]
    pages = [rows[i:i + 52] for i in range(0, max(len(rows), 1), 52)] or [[""]]
    objs, page_ids = [], []
    # 1 catalog, 2 pages, then per page: content then page object; font last.
    next_id = 3
    contents = []
    for page in pages:
        text = "".join(
            f"({_pdf_esc(ln[:95])}) Tj T*" for ln in page)
        stream = (f"BT /F1 11 Tf 54 740 Td 14 TL {text} ET").encode()
        contents.append(stream)
    for _ in pages:
        page_ids.append(next_id + 1)
        next_id += 2
    font_id = next_id
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for k, stream in enumerate(contents):
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
                     f"<< /Font << /F1 {font_id} 0 R >> >> /Contents {3 + 2 * k} 0 R >>"
                     ).encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

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


def _pdf_esc(s):
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


# ---------- dispatch ----------

def build(path, markdown):
    """Bytes for `path` from markdown, or (None, reason) if the format is not ours."""
    ext = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
    if ext == ".docx":
        return docx_bytes(markdown), None
    if ext == ".xlsx":
        return xlsx_bytes(markdown), None
    if ext == ".pdf":
        return pdf_bytes(markdown), None
    if ext in (".md", ".txt", ".csv", ".json", ".py"):
        return str(markdown or "").encode("utf8"), None
    return None, (f"write_document does not build {ext or 'that format'}. "
                  f"It handles {', '.join(SUPPORTED)}. For anything else, build the "
                  "file with a script and run it with bash.")
