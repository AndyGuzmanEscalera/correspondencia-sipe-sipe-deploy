"""Physical EDIE form, Legal portrait; rendering never changes routing data."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import legal
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, Indenter, KeepTogether, PageTemplate, Paragraph, Spacer,
)
from app.core.branding import OFFICIAL_CREST
from app.core.institutional_time import institutional_datetime

MOVEMENT_TYPE_LABELS = {
    "CREATED": "Registro",
    "DERIVED": "Derivación",
    "RECEIVED": "Recepción",
    "RESPONDED": "Respuesta",
    "CONCLUDED": "Conclusión",
    "REOPENED": "Reapertura",
    "CANCELLED": "Cancelación",
    "ANNULLED": "Anulación",
}

RECIPIENT_ORDINALS = (
    "Primer",
    "Segundo",
    "Tercer",
    "Cuarto",
    "Quinto",
    "Sexto",
    "Séptimo",
    "Octavo",
    "Noveno",
    "Décimo",
)


@dataclass(frozen=True, slots=True)
class EncadenamientoMovementRow:
    sequence_number: int
    movement_type: str
    movement_type_label: str
    from_unit: str | None
    from_user: str | None
    to_unit: str | None
    to_user: str | None
    instruction: str | None
    created_at: datetime
    is_cancelled: bool
    cancellation_reason: str | None


@dataclass(frozen=True, slots=True)
class EncadenamientoPdfContext:
    route_number: str
    route_sequence: int
    document_number: str
    correspondence_type: str
    para: str
    de: str
    referencia: str
    issued_at: datetime
    generated_at: datetime
    page_count_label: str = "—"
    is_urgent: bool = False
    movements: tuple[EncadenamientoMovementRow, ...] = ()
    logo_path: Path | None = None
    form_title: str = "ENCADENAMIENTO DE DOCUMENTOS INTERNOS Y EXTERNOS"
    content: str | None = None
    refined_layout: bool = False


# Measurements are points (72 per inch). No text is truncated or shrunk to fit.
PAGE_WIDTH, PAGE_HEIGHT = legal
MARGIN = 30
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
HEADER_HEIGHT = 106
FOOTER_HEIGHT = 22
TEXT = ParagraphStyle("form", fontName="Helvetica", fontSize=9, leading=12)
TITLE = ParagraphStyle("section", parent=TEXT, fontName="Helvetica-Bold", alignment=TA_CENTER)


def _paragraph(text: str, *, bold: bool = False) -> Paragraph:
    # User data must remain plain text, including <, >, &, and newlines.
    value = escape(text or "").replace("\n", "<br/>")
    return Paragraph(value or " ", TITLE if bold else TEXT)


def _field(label: str, value: str | None) -> Paragraph:
    return Paragraph(f"<b>{escape(label)}</b> {escape(value or '').replace(chr(10), '<br/>')}", TEXT)


def _recipient_label(index: int, *, continuation: bool = False) -> str:
    if index < len(RECIPIENT_ORDINALS):
        return f"{RECIPIENT_ORDINALS[index]} destinatario".upper()
    return f"DESTINATARIO {index + 1}"


class _RuledField(Flowable):
    """Wrapped printed value above a writing rule, measured using actual glyphs."""
    def __init__(self, label: str, value: str | None):
        super().__init__()
        self.paragraph = _field(label, value)

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        self.height = self.paragraph.wrap(availWidth - 16, availHeight)[1] + 8
        return self.width, self.height

    def draw(self):
        self.canv.setStrokeColor(colors.black)
        self.canv.setLineWidth(0.35)
        self.canv.line(8, 4, self.width - 8, 4)
        self.paragraph.drawOn(self.canv, 8, 8)


class _IssueDetails(Flowable):
    def __init__(self, context):
        super().__init__()
        issued_at = institutional_datetime(context.issued_at)
        self.paragraphs = [
            _field("FECHA DE EMISIÓN:", issued_at.strftime("%d / %m / %Y")),
            _field("HORA:", issued_at.strftime("%H:%M")),
            _field("N° HOJAS:", context.page_count_label),
        ]

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        self.widths = [(availWidth - 16) * ratio for ratio in (0.51, 0.22, 0.27)]
        self.height = max(p.wrap(w - 8, availHeight)[1]
                          for p, w in zip(self.paragraphs, self.widths)) + 8
        return self.width, self.height

    def draw(self):
        c, x = self.canv, 8
        c.setLineWidth(0.35)
        for p, w in zip(self.paragraphs, self.widths):
            p.drawOn(c, x, self.height - p.height)
            c.line(x, 4, x + w - 8, 4)
            x += w


class _RecipientBlock(Flowable):
    """A complete physical form block, split only when its text exceeds a page.

    A split block repeats the same recipient ordinal; it never invents a new
    derivation. Signature/stamp spaces are retained on its final fragment.
    """
    def __init__(self, index, movement, urgent=False, *, instruction=None, continued=False, full_page_height=None, refined_layout=False):
        super().__init__()
        self.index, self.movement, self.urgent = index, movement, urgent
        self.continued = continued
        self.refined_layout = refined_layout
        self.full_page_height = full_page_height or PAGE_HEIGHT - 2 * MARGIN - HEADER_HEIGHT - FOOTER_HEIGHT
        self.instruction = instruction if instruction is not None else _paragraph(
            movement.instruction or "" if movement else ""
        )
        self.final = True

    def wrap(self, availWidth, availHeight):
        self.width = availWidth
        m = self.movement
        # A checkbox marks the actual initial recipient, not a hardcoded unit list.
        if self.index == 0:
            self.fields = [
                _field("RECEPTOR INSTITUCIONAL:", m.to_unit if m else ""),
                _field("FUNCIONARIO:", m.to_user if m else ""),
            ]
            field_width = availWidth - 202
        else:
            self.fields = [
                _field("DESTINO:", (m.to_user or m.to_unit) if m else ""),
                _field("UNIDAD:", m.to_unit if m else ""),
                _field("FUNCIONARIO:", m.to_user if m else ""),
            ]
            field_width = availWidth - 24
        self.field_heights = [max(14, p.wrap(field_width, 10000)[1] + 2) for p in self.fields]
        self.instruction_width = availWidth - (202 if self.index == 0 else 24)
        self.instruction_height = self.instruction.wrap(self.instruction_width, 1000000)[1]
        self.status = None
        if m and m.is_cancelled:
            self.status = _field("CANCELADO - MOTIVO:", m.cancellation_reason or "Sin motivo registrado")
        self.status_height = self.status.wrap(availWidth - (202 if self.index == 0 else 24), 1000000)[1] + 5 if self.status else 0
        self.before_instruction = 24 + sum(self.field_heights) + self.status_height
        if self.index == 1:
            self.before_instruction += 18  # Urgency is explicit in second-recipient form.
        if self.index != 0:
            self.before_instruction += 17  # Instructivo / proveído label.
        self.after_instruction = (118 if self.index == 1 else 76) if self.final else 8
        if self.index == 0:
            self.after_instruction = 12
        self.height = max(
            ((110 if self.refined_layout else 116) if self.index == 0 else (240 if self.index == 1 else 182)) if self.final else 0,
            self.before_instruction + self.instruction_height + 8 + self.after_instruction,
        )
        # Unused writing slots complete the physical form only if they fit.
        # They must never create a continuation sheet with no real movement.
        self.skip_blank = self.movement is None and self.height > availHeight
        if self.skip_blank:
            self.height = 0
        return self.width, self.height

    def split(self, availWidth, availHeight):
        self.wrap(availWidth, availHeight)
        # Move the block intact to the next page unless it cannot fit a full page.
        full_page = self.full_page_height
        if self.height <= full_page:
            return []
        room = availHeight - self.before_instruction - 20
        if room < TEXT.leading * 2:
            return []
        pieces = self.instruction.split(self.instruction_width, room)
        if len(pieces) < 2:
            return []
        first = _RecipientBlock(self.index, self.movement, self.urgent,
                                instruction=pieces[0], continued=self.continued, full_page_height=self.full_page_height,
                                refined_layout=self.refined_layout)
        first.final = False
        rest = _RecipientBlock(self.index, self.movement, self.urgent,
                               instruction=pieces[1], continued=True, full_page_height=self.full_page_height,
                               refined_layout=self.refined_layout)
        first.wrap(availWidth, availHeight)
        return [first, Spacer(1, 4), rest]

    def draw(self):
        if self.skip_blank:
            return
        c, w, h = self.canv, self.width, self.height
        c.setStrokeColor(colors.black)
        c.setFillColor(colors.black)
        c.setLineWidth(0.5)
        c.roundRect(0, 0, w, h, 3, stroke=1, fill=0)
        c.line(0, h - 19, w, h - 19)
        c.setFont("Helvetica-Bold", 9)
        title = _recipient_label(self.index) + (" (CONTINUACIÓN)" if self.continued else "")
        c.drawCentredString(w / 2, h - 13, title)
        y = h - 24
        x = 12
        if self.index == 0:
            c.rect(12, y - 10, 8, 8)
            if self.movement:
                c.setFont("Helvetica", 8)
                c.drawCentredString(16, y - 8, "X")
            x = 26
        for p, height in zip(self.fields, self.field_heights):
            ph = p.height
            p.drawOn(c, x, y - ph)
            c.setLineWidth(0.3)
            c.line(x, y - height + 2, w - (178 if self.index == 0 else 12), y - height + 2)
            y -= height
        if self.status:
            self.status.drawOn(c, x, y - self.status_height + 5)
            y -= self.status_height
        if self.index == 1:
            c.setFont("Helvetica-Bold", 9)
            c.drawString(12, y - 10, "URGENTE:")
            c.setFont("Helvetica", 9)
            c.drawString(72, y - 10, ("SÍ" if self.urgent else "NO") if self.movement else "")
            c.line(72, y - 13, w - 12, y - 13)
            y -= 18
        if self.index != 0:
            c.setFont("Helvetica-Bold", 9)
            c.drawString(12, y - 11, "INSTRUCTIVO / PROVEÍDO:")
            y -= 17
        # The normal form keeps writing lines even when no instruction exists.
        self.instruction.drawOn(c, x if self.index == 0 else 12, y - self.instruction_height)
        y -= self.instruction_height + 3
        if self.index != 0 and self.final:
            c.setLineWidth(0.3)
            for line in range(3 if self.index == 1 else 2):
                c.line(12, y - line * 12, w - 12, y - line * 12)
        if not self.final:
            c.setFont("Helvetica-Oblique", 7)
            c.drawRightString(w - 12, 5, "Proveído continúa en la siguiente hoja")
            return
        if self.index == 0:
            _stamp(c, w - 164, 12, 150, max(66, h - 40), "SELLO DE RECEPCIÓN")
            return
        # Lower right stamp and signature/date fields remain blank for handwriting.
        if self.index == 1:
            sx, sw, sh = w - 230, 216, 52
            _stamp(c, sx, 10, sw, sh, "ESPACIO PARA SELLO")
            c.setFont("Helvetica", 8)
            c.drawString(sx, 88, "FIRMA:")
            c.line(sx + 38, 86, w - 14, 86)
            c.drawString(sx, 72, "FECHA: ____ / ____ / ______")
        else:
            _stamp(c, 14, 10, 130, 44, "SELLO")
            c.setFont("Helvetica", 8)
            c.drawString(164, 42, "FIRMA:")
            c.line(202, 40, w - 14, 40)
            c.drawString(164, 22, "FECHA: ____ / ____ / ______")



def _stamp(c, x, y, width, height, label):
    c.setLineWidth(0.4)
    c.roundRect(x, y, width, height, 2, stroke=1, fill=0)
    c.setFont("Helvetica", 7)
    c.drawCentredString(x + width / 2, y + height / 2 - 2, label)


def _document_number(context):
    if context.refined_layout:
        style = ParagraphStyle("form-number", parent=TEXT, fontName="Helvetica-Bold",
                               fontSize=11, leading=13, alignment=TA_CENTER)
        return Paragraph(escape(context.document_number or ""), style)
    return _paragraph(context.document_number or "")


def _header_height(context):
    number = _document_number(context)
    minimum = 92 if context.refined_layout else HEADER_HEIGHT
    number_width = 54 if context.refined_layout else 62
    return max(minimum, number.wrap(number_width, 1000000)[1] + 78)


def _draw_page(c, doc, context):
    left, right, top = MARGIN, PAGE_WIDTH - MARGIN, PAGE_HEIGHT - MARGIN
    header_height = _header_height(context)
    c.saveState()
    c.setStrokeColor(colors.black)
    c.setFillColor(colors.black)
    c.setLineWidth(0.5)
    c.roundRect(left, MARGIN, CONTENT_WIDTH, PAGE_HEIGHT - 2 * MARGIN, 3)
    if OFFICIAL_CREST.is_file():
        c.drawImage(str(OFFICIAL_CREST),
                    left + (13 if context.refined_layout else 10),
                    top - (57 if context.refined_layout else 72),
                    width=44 if context.refined_layout else 52,
                    height=50 if context.refined_layout else 64,
                    preserveAspectRatio=True, anchor="c", mask="auto")
    header_x, header_width = left + 72, CONTENT_WIDTH - 160
    style = ParagraphStyle("institution", fontName="Helvetica-Bold", fontSize=10,
                           leading=13, alignment=TA_CENTER)
    p = Paragraph("GOBIERNO AUTÓNOMO MUNICIPAL DE SIPE SIPE", style)
    ph = p.wrap(header_width, 100)[1]
    p.drawOn(c, header_x, top - 18 - ph)
    p = Paragraph("SEGUNDA SECCIÓN DE LA PROVINCIA QUILLACOLLO",
                  ParagraphStyle("province", parent=style, fontName="Helvetica", fontSize=8, leading=11))
    ph = p.wrap(header_width, 100)[1]
    p.drawOn(c, header_x, top - 38 - ph)
    p = _document_number(context)
    if context.refined_layout:
        ph = p.wrap(54, 1000000)[1]
        c.setLineWidth(0.45)
        c.roundRect(right - 76, top - 7 - max(42, ph + 20), 64,
                    max(42, ph + 20), 2, stroke=1, fill=0)
        c.setFont("Helvetica", 7)
        c.drawCentredString(right - 44, top - 19, "NÚMERO")
        p.drawOn(c, right - 71, top - 25 - ph)
    else:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(right - 70, top - 18, "N°")
        ph = p.wrap(62, 1000000)[1]
        p.drawOn(c, right - 70, top - 24 - ph)
    c.setLineWidth(0.35)
    if not context.refined_layout:
        c.line(right - 70, top - header_height + 46, right - 8, top - header_height + 46)
    c.line(left, top - header_height + 30, right, top - header_height + 30)
    c.setFont("Helvetica-Bold", 11 if context.refined_layout else 9)
    c.drawCentredString(PAGE_WIDTH / 2, top - header_height + 16,
                       context.form_title)
    c.line(left, top - header_height + 6, right, top - header_height + 6)
    c.setFont("Helvetica", 7)
    c.drawString(left + 8, MARGIN + 8,
                 context.route_number if context.refined_layout else "HR: " + context.route_number)
    folio = f"HOJA {doc.page}"
    if context.refined_layout and context.page_count_label.isdigit():
        folio += " DE " + context.page_count_label
    c.drawRightString(right - 8, MARGIN + 8, folio)
    c.restoreState()


def generate_encadenamiento_pdf(context: EncadenamientoPdfContext, *, page_observer=None) -> bytes:
    buffer = BytesIO()
    # Reserve the institutional header on every Legal page, including continuations.
    header_height = _header_height(context)
    content_top = PAGE_HEIGHT - MARGIN - header_height
    bottom = MARGIN + FOOTER_HEIGHT
    doc = BaseDocTemplate(buffer, pagesize=legal, leftMargin=MARGIN, rightMargin=MARGIN,
                          topMargin=MARGIN + header_height, bottomMargin=bottom,
                          title=context.form_title,
                          author="Gobierno Autónomo Municipal de Sipe Sipe")
    frame = Frame(MARGIN + 6, bottom, CONTENT_WIDTH - 12, content_top - bottom,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    def draw_page(canvas, document):
        if page_observer is not None:
            page_observer(document.page)
        _draw_page(canvas, document, context)

    doc.addPageTemplates(PageTemplate(id="oficio", frames=frame, onPage=draw_page))
    story = [KeepTogether([
        _RuledField("PARA:", context.para),
        _RuledField("DE:", context.de),
        _RuledField("REFERENCIA:", context.referencia),
        _IssueDetails(context),
    ]), Spacer(1, 4)]
    if context.content:
        if context.refined_layout:
            story.append(Indenter(left=8, right=8))
        story.append(_field("TEXTO / CONTENIDO:", ""))
        for line in context.content.splitlines():
            story.append(_paragraph(line) if line else Spacer(1, 6))
        if not context.refined_layout:
            story.append(Spacer(1, 6))
        if context.refined_layout:
            story.append(Indenter(left=-8, right=-8))
    recipients = sorted((m for m in context.movements if m.movement_type == "DERIVED"),
                        key=lambda m: m.sequence_number)
    for index in range(max(4, len(recipients))):
        movement = recipients[index] if index < len(recipients) else None
        story.extend([_RecipientBlock(index, movement, context.is_urgent,
                                     full_page_height=content_top - bottom,
                                     refined_layout=context.refined_layout), Spacer(1, 3)])
    doc.build(story)
    return buffer.getvalue()
