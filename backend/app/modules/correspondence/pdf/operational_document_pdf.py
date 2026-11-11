"""Version 1 institutional template. Its semantic input is a frozen snapshot."""

from datetime import datetime, timedelta
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether
from app.core.branding import OFFICIAL_CREST, PRIMARY_HEX


def generate_operational_document_pdf(snapshot, *, story_builder=None):
    if snapshot.get("template") != "institutional-document-v1":
        raise ValueError("Unsupported document snapshot version")
    out = BytesIO()
    width, height = A4
    document = SimpleDocTemplate(
        out,
        pagesize=A4,
        leftMargin=46,
        rightMargin=46,
        topMargin=104,
        bottomMargin=65,
        title=snapshot["name"],
        author="GAM Sipe Sipe",
    )
    text = ParagraphStyle(
        "Body",
        fontName="Helvetica",
        fontSize=10,
        leading=15,
        spaceAfter=7,
        splitLongWords=True,
    )
    heading = ParagraphStyle(
        "Title",
        parent=text,
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=20,
        spaceAfter=13,
    )

    def p(value, style=text):
        safe = "".join(c for c in str(value or "") if ord(c) >= 32 or c in "\n\t")
        return Paragraph(escape(safe).replace("\n", "<br/>"), style)

    def person(party):
        return " · ".join(
            v
            for v in [
                party.get("employee_name"),
                party.get("position_name"),
                party.get("unit_name"),
            ]
            if v
        )

    issued = datetime.fromisoformat(snapshot["issued_at"]) - timedelta(hours=4)
    story = [
        p(snapshot["name"].upper(), heading),
        p(f'N.º {snapshot["document_number"]} / {snapshot["document_year"]}'),
        p("DE: " + person(snapshot["origin"])),
        p("A: " + person(snapshot["destination"])),
        p("FECHA: " + issued.strftime("%d/%m/%Y %H:%M")),
        p("ASUNTO: " + snapshot["subject"]),
    ]
    if story_builder:
        story = story_builder(snapshot, p, heading)
    elif snapshot.get("reference"):
        story.append(p("REFERENCIA: " + snapshot["reference"]))
    story.append(Spacer(1, 12))
    # Separate paragraphs preserve multipage flow without putting long content in
    # an unsplittable table or depending on mutable master data.
    for line in (snapshot.get("content") or "").split("\n"):
        story.append(p(line) if line else Spacer(1, 8))
    actor = snapshot["actor"]
    hierarchy = actor.get("hierarchy")
    signature = [
        Spacer(1, 28),
        p("________________________________"),
        p("Firma y sello"),
        p(person(actor)),
    ]
    if hierarchy:
        signature.append(p("Jerarquía de emisión: " + hierarchy["name"]))
    story.append(KeepTogether(signature))

    def decorate(canvas, doc):
        canvas.saveState()
        if OFFICIAL_CREST.is_file():
            canvas.drawImage(
                str(OFFICIAL_CREST),
                46,
                height - 83,
                width=42,
                height=54,
                preserveAspectRatio=True,
                mask="auto",
            )
        canvas.setFillColor(colors.HexColor("#" + PRIMARY_HEX))
        canvas.setFont("Helvetica-Bold", 10)
        canvas.drawString(100, height - 49, "GOBIERNO AUTÓNOMO MUNICIPAL DE SIPE SIPE")
        canvas.setFont("Helvetica", 8)
        canvas.drawString(100, height - 65, "Sistema de Gestión de Correspondencia")
        canvas.setStrokeColor(colors.HexColor("#CBD5E1"))
        canvas.line(46, height - 93, width - 46, height - 93)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.drawString(46, 35, snapshot["route_number"])
        canvas.drawRightString(width - 46, 35, f"Página {doc.page}")
        canvas.restoreState()

    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return out.getvalue()
