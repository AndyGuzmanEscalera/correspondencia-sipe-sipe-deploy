"""ReportLab institutional report, alongside the existing document PDF generator."""
from io import BytesIO
from app.core.branding import OFFICIAL_CREST, PRIMARY_HEX, SOFT_HEX
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, LongTable, TableStyle

INSTITUTION = "Gobierno Autónomo Municipal de Sipe Sipe"
WIDTH, HEIGHT = landscape(A4)


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages = []
    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()
    def save(self):
        total = len(self.pages)
        for state in self.pages:
            self.__dict__.update(state)
            self.setStrokeColor(colors.HexColor("#CBD5E1"))
            self.line(32, 39, WIDTH-32, 39)
            self.setFont("Helvetica", 7)
            self.setFillColor(colors.HexColor("#475569"))
            self.drawString(32, 28, "Sistema de Gestión de Correspondencia")
            self.drawString(32, 18, INSTITUTION)
            self.drawRightString(WIDTH-32, 23, f"Página {self._pageNumber} de {total}")
            super().showPage()
        super().save()


def generate_report_pdf(metadata, summary, rows):
    stream = BytesIO()
    document = SimpleDocTemplate(stream, pagesize=(WIDTH, HEIGHT), leftMargin=32, rightMargin=32,
        topMargin=85, bottomMargin=54, title="Reporte de Correspondencia", author=INSTITUTION)
    small = ParagraphStyle("ReportCell", fontName="Helvetica", fontSize=8, leading=11, splitLongWords=True)
    def paragraph(value):
        text = ''.join(c for c in str(value or "") if ord(c) >= 32 or c in "\n\t")
        return Paragraph(escape(text).replace("\n", "<br/>"), small)
    story = [paragraph(f"{label}: {value}") for label, value in metadata]
    story += [Spacer(1, 10), paragraph(f"Total: {summary['total']}     Pendientes: {summary['pending']}     Concluidos: {summary['concluded']}"), Spacer(1, 12)]
    data = [[paragraph(v) for v in ["HR", "Fecha (Bolivia)", "Tipo", "Asunto / Referencia", "Origen", "Destino actual", "Estado"]]]
    data.extend([paragraph(value) for value in row] for row in rows)
    if len(data) == 1:
        story.append(paragraph("Sin correspondencias para los filtros aplicados."))
    else:
        table = LongTable(data, colWidths=[94, 77, 83, 196, 116, 151, 60], repeatRows=1, splitByRow=1, splitInRow=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#" + SOFT_HEX)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), .6, colors.HexColor("#" + PRIMARY_HEX)),
            ("LINEBELOW", (0, 1), (-1, -1), .25, colors.HexColor("#E2E8F0")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(table)
    def header(c, _):
        c.saveState();c.setFillColor(colors.HexColor("#" + PRIMARY_HEX))
        c.drawImage(str(OFFICIAL_CREST), 32, HEIGHT-66, width=40, height=48, preserveAspectRatio=True, mask="auto")
        c.setFont("Helvetica-Bold", 12);c.drawString(86, HEIGHT-34, INSTITUTION.upper())
        c.setFont("Helvetica", 10);c.drawString(86, HEIGHT-51, "REPORTE DE CORRESPONDENCIA")
        c.setStrokeColor(colors.HexColor("#CBD5E1"));c.line(32, HEIGHT-71, WIDTH-32, HEIGHT-71);c.restoreState()
    document.build(story, onFirstPage=header, onLaterPages=header, canvasmaker=NumberedCanvas)
    return stream.getvalue()
