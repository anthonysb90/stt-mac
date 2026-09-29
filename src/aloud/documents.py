"""Word and PDF files, written directly — no document library required.

Both formats are written by hand on purpose. A new dependency would mean every
installed copy has to be rebuilt, not just updated, and neither format needs
much for a transcript: a title, a line of details, paragraphs, bold speaker
names and grey timestamps.

* **Word (.docx)** is a zip of a few XML files. Opens in Word, Pages and
  Google Docs, and is the format to pick when the transcript will be edited.
* **PDF** uses the fonts every PDF reader already has (Helvetica), so nothing
  is embedded and the file stays small. The cost is that those fonts only
  cover Western European alphabets: English, Spanish, French, Portuguese,
  German and similar are fine; Korean, Chinese, Russian or Arabic text shows
  as "?". For those, export Word or HTML and print to PDF from there — the
  export window says so when it matters (see :func:`pdf_can_render`).
"""

from __future__ import annotations

import io
import re
import time
import zipfile
from typing import List, Sequence, Tuple
from xml.sax.saxutils import escape

#: XML 1.0 forbids most control characters; one in a transcript would make
#: Word refuse to open the whole document.
_XML_INVALID = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")


def _x(text: str) -> str:
    return escape(_XML_INVALID.sub("", text))


# ---------------------------------------------------------------------------
# Word
# ---------------------------------------------------------------------------

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""

_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles {_W}>
<w:docDefaults>
<w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Calibri"/><w:sz w:val="23"/><w:szCs w:val="23"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="288" w:lineRule="auto"/></w:pPr></w:pPrDefault>
</w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="360"/></w:pPr><w:rPr><w:color w:val="6E6E73"/><w:sz w:val="20"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>
<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="60"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Compact"><w:name w:val="Compact"/><w:basedOn w:val="Normal"/><w:qFormat/>
<w:pPr><w:spacing w:after="60"/></w:pPr></w:style>
<w:style w:type="character" w:styleId="Timestamp"><w:name w:val="Timestamp"/><w:rPr><w:color w:val="6E6E73"/></w:rPr></w:style>
<w:style w:type="character" w:styleId="Speaker"><w:name w:val="Speaker"/><w:rPr><w:b/></w:rPr></w:style>
</w:styles>"""


def _core(title: str) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{_x(title)}</dc:title><dc:creator>Aloud</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
        "</cp:coreProperties>"
    )


def _run(text: str, style: str = "") -> str:
    props = f'<w:rPr><w:rStyle w:val="{style}"/></w:rPr>' if style else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{_x(text)}</w:t></w:r>'


def _para(runs: str, style: str = "") -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{props}{runs}</w:p>"


def docx(title: str, subtitle: str, blocks: Sequence) -> bytes:
    """A Word document from export blocks (see :mod:`aloud.export`)."""
    from .export import clock

    compact = all(b.time is not None for b in blocks if not b.heading) and any(
        b.time is not None for b in blocks)
    body = [_para(_run(title), "Title")]
    if subtitle:
        body.append(_para(_run(subtitle), "Subtitle"))
    for block in blocks:
        if block.heading:
            body.append(_para(_run(block.text), "Heading3"))
            continue
        runs = ""
        if block.time is not None:
            runs += _run(f"{clock(block.time)}  ", "Timestamp")
        if block.speaker:
            runs += _run(f"{block.speaker}: ", "Speaker")
        runs += _run(block.text)
        body.append(_para(runs, "Compact" if compact else ""))

    section = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
               '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
               'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>')
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<w:document {_W}>'
        f'<w:body>{"".join(body)}{section}</w:body></w:document>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        # [Content_Types].xml first: some readers sniff for it.
        package.writestr("[Content_Types].xml", _CONTENT_TYPES)
        package.writestr("_rels/.rels", _ROOT_RELS)
        package.writestr("word/_rels/document.xml.rels", _DOCUMENT_RELS)
        package.writestr("word/document.xml", document)
        package.writestr("word/styles.xml", _STYLES)
        package.writestr("docProps/core.xml", _core(title))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = 612.0, 792.0  # US Letter, points
MARGIN = 72.0
BODY_SIZE, BODY_LEADING = 11.0, 15.5
GREY = "0.43 0.43 0.45"

#: Helvetica advance widths (per 1000 em) for ASCII 32..126, from its AFM.
_HELVETICA = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
    1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
    667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
    333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
    556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]


def pdf_can_render(text: str) -> bool:
    """Whether every character survives the standard PDF fonts."""
    try:
        text.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


def _width(text: str, size: float, bold: bool = False) -> float:
    total = 0
    for char in text:
        code = ord(char)
        total += _HELVETICA[code - 32] if 32 <= code <= 126 else 556
    # Helvetica-Bold runs about 6% wider; close enough to wrap on.
    return total * size / 1000.0 * (1.06 if bold else 1.0)


def _pdf_string(text: str) -> str:
    raw = text.encode("cp1252", errors="replace").decode("latin-1")
    return "(" + raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


Run = Tuple[str, str, str]  # (text, font "F1"/"F2", colour)


def _wrap_runs(runs: List[Run], size: float, width: float) -> List[List[Run]]:
    """Break styled runs into lines no wider than ``width``."""
    words: List[Run] = []
    for text, font, colour in runs:
        for piece in re.findall(r"\S+\s*", text):
            words.append((piece, font, colour))
    lines: List[List[Run]] = [[]]
    used = 0.0
    for piece, font, colour in words:
        w = _width(piece.rstrip(), size, font == "F2")
        if lines[-1] and used + w > width:
            lines.append([])
            used = 0.0
        lines[-1].append((piece, font, colour))
        used += _width(piece, size, font == "F2")
    return [line for line in lines if line]


class _Pages:
    """Lays lines out top to bottom, starting a new page when one fills."""

    def __init__(self, footer: str) -> None:
        self.pages: List[List[str]] = []
        self.footer = footer
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    def space(self, points: float) -> None:
        self.y -= points

    def line(self, runs: List[Run], size: float, leading: float, keep: float = 0.0) -> None:
        if self.y - leading - keep < MARGIN:
            self._new_page()
        self.y -= leading
        ops = [f"BT {MARGIN:.2f} {self.y:.2f} Td"]
        for text, font, colour in runs:
            ops.append(f"/{font} {size:g} Tf {colour} rg {_pdf_string(text)} Tj")
        ops.append("ET")
        self.pages[-1].append(" ".join(ops))

    def finish(self) -> List[str]:
        total = len(self.pages)
        streams = []
        for number, ops in enumerate(self.pages, start=1):
            label = f"{self.footer}  ·  {number} of {total}" if self.footer else f"{number} of {total}"
            ops = ops + [f"BT {MARGIN:.2f} {MARGIN / 2:.2f} Td /F1 8.5 Tf {GREY} rg "
                         f"{_pdf_string(label)} Tj ET"]
            streams.append("\n".join(ops))
        return streams


def pdf(title: str, subtitle: str, blocks: Sequence) -> bytes:
    """A paginated PDF from export blocks (see :mod:`aloud.export`)."""
    from .export import clock

    width = PAGE_W - 2 * MARGIN
    pages = _Pages(footer=title[:60])
    compact = all(b.time is not None for b in blocks if not b.heading) and any(
        b.time is not None for b in blocks)

    for line in _wrap_runs([(title, "F2", "0 0 0")], 20, width):
        pages.line(line, 20, 26)
    if subtitle:
        pages.space(4)
        for line in _wrap_runs([(subtitle, "F1", GREY)], 10, width):
            pages.line(line, 10, 14)
    pages.space(18)

    for block in blocks:
        if block.heading:
            pages.space(8)
            # keep: never strand a speaker's name at the foot of a page.
            pages.line([(block.text, "F2", "0 0 0")], 12, 17, keep=BODY_LEADING * 2)
            continue
        runs: List[Run] = []
        if block.time is not None:
            runs.append((f"{clock(block.time)}  ", "F1", GREY))
        if block.speaker:
            runs.append((f"{block.speaker}: ", "F2", "0 0 0"))
        runs.append((block.text, "F1", "0 0 0"))
        for line in _wrap_runs(runs, BODY_SIZE, width):
            pages.line(line, BODY_SIZE, BODY_LEADING)
        pages.space(3 if compact else 8)

    return _assemble(pages.finish(), title)


def _assemble(streams: List[str], title: str) -> bytes:
    """Objects, cross-reference table and trailer around the page streams."""
    objects: List[bytes] = []

    def add(body: str) -> int:
        objects.append(body.encode("latin-1"))
        return len(objects)

    catalog = add("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add("")  # filled in once the kids are known
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    bold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
    info = add(f"<< /Title {_pdf_string(title)} /Producer (Aloud) >>")
    kids = []
    for stream in streams:
        data = stream.encode("latin-1")
        content = add(f"<< /Length {len(data)} >>\nstream\n{stream}\nendstream")
        kids.append(add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W:g} {PAGE_H:g}] "
            f"/Resources << /Font << /F1 {font} 0 R /F2 {bold} 0 R >> >> /Contents {content} 0 R >>"
        ))
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    ).encode("latin-1")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\n"
              f"startxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()
