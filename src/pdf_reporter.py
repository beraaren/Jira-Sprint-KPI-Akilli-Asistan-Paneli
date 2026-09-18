"""processor.py'nin urettigi KPI ve tablo verilerini, yazdirmaya/paylasmaya
hazir TEK bir PDF dosyasina yazar - `reporter.create_excel_report`'un PDF
karsiligidir ve AYNI `process_sprint_report` ciktisini girdi alir.

Neden ReportLab (HTML->PDF degil):
    Streamlit sunucu tarafinda tarayicidaki DOM'a erisemez, dolayisiyla "ekrani
    oldugu gibi PDF'e cevirmek" mumkun degil - rapor ayni VERIDEN yeniden
    uretilir. HTML->PDF kutuphaneleri de bu is icin elendi: wkhtmltopdf/WeasyPrint
    harici binary/GTK kurulumu ister (kurumsal makinede yonetici yetkisi gerekir),
    xhtml2pdf ise Windows'ta TTF gomerken gecici dosya hatasi verip Turkce
    karakterleri bozuyor. ReportLab saf Python'dur ve TTF gomme destegi
    guvenilirdir.

Turkce karakterler:
    ReportLab'in varsayilan fontu (Helvetica) Latin-1'dir; `ğ Ğ ı İ ş Ş`
    KARAKTERLERINI ICERMEZ ve bunlari kutucuga cevirir. Bu yuzden sistemden bir
    Unicode TTF bulunup GOMULUR (bkz. `_register_turkish_font`) - font
    bulunamazsa sessizce bozuk cikti vermek yerine acik bir hata yukseltilir.

Grafikler:
    Panodaki plotly figurleri `kaleido` ile PNG'ye cevrilip gomulur. `kaleido`
    yoksa/calismazsa rapor grafiksiz ama EKSIKSIZ uretilir (bkz.
    `_monthly_trend_image`) - tek bir opsiyonel bilesen yuzunden raporun tamami
    kaybedilmez.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

__all__ = ["create_pdf_report", "PdfFontError"]

# --------------------------------------------------------------------------
# Sayfa ve renk sabitleri (Excel raporuyla AYNI kurumsal mavi tonlari)
# --------------------------------------------------------------------------

PAGE_SIZE = landscape(A4)  # is listesi tablolari 6 kolonlu - yatay sayfa daha okunakli
MARGIN = 14 * mm

HEADER_BG = colors.HexColor("#1F4E78")
HEADER_FG = colors.white
SECTION_BG = colors.HexColor("#DDEBF7")
SECTION_FG = colors.HexColor("#1F4E78")
ROW_ALT_BG = colors.HexColor("#F5F8FC")
GRID_COLOR = colors.HexColor("#BFD3E6")
MUTED = colors.HexColor("#5A6472")

BASE_FONT = "RaporTR"
BOLD_FONT = "RaporTR-Bold"

# Sistemde aranacak Unicode TTF adaylari (once Windows, sonra Linux/macOS).
# (normal, kalin) ciftleri - kalin bulunamazsa normal font kalin yerine de kullanilir.
FONT_CANDIDATES: list[tuple[str, str]] = [
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\calibrib.ttf"),
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
]

# Uzun metin iceren (sarmalanmasi gereken) kolonlar - digerleri tek satirda kalir.
WRAP_COLUMNS = {"İş Listesi", "Summary", "Sorumlu", "Statü"}

# Bir tabloda gosterilecek azami satir - cok uzun listeler PDF'i sisirir.
# Asilirsa kirpilir VE kirpildigi rapora ACIKCA yazilir (sessiz kesme yapilmaz).
MAX_TABLE_ROWS = 400

METRIC_COLUMNS: list[tuple[str, str]] = [
    ("committed_sp", "Taahhüt Edilen"),
    ("completed_sp", "Gerçekleşen"),
    ("out_of_plan_sp", "Plan Dışı"),
    ("total_completed_sp", "Toplam Tamamlanan"),
]

CHART_COLORS = ["#1F4E78", "#2E86C1", "#E67E22", "#27AE60"]


class PdfFontError(RuntimeError):
    """Sistemde Turkce karakterleri iceren bir TTF font bulunamadi. Bu durumda
    PDF uretmek yerine hata yukseltilir - aksi halde rapor sessizce `ğ/ş/İ`
    yerine kutucuklarla dolu, kullanilamaz halde cikardi."""


# --------------------------------------------------------------------------
# Font
# --------------------------------------------------------------------------


def _register_turkish_font() -> None:
    """Turkce karakter iceren bir TTF'i ReportLab'a kaydeder (idempotent -
    Streamlit her yeniden calistirmada bu modulu yeniden cagirabilir)."""
    if BASE_FONT in pdfmetrics.getRegisteredFontNames():
        return

    for regular, bold in FONT_CANDIDATES:
        if not Path(regular).exists():
            continue
        try:
            pdfmetrics.registerFont(TTFont(BASE_FONT, regular))
            bold_path = bold if Path(bold).exists() else regular
            pdfmetrics.registerFont(TTFont(BOLD_FONT, bold_path))
        except Exception:  # noqa: BLE001 - bozuk/erisilemeyen font: sonraki adaya gec
            continue
        pdfmetrics.registerFontFamily(BASE_FONT, normal=BASE_FONT, bold=BOLD_FONT)
        return

    raise PdfFontError(
        "PDF için Türkçe karakter içeren bir font bulunamadı (Arial, Segoe UI, "
        "Calibri, Tahoma veya DejaVu Sans arandı). Excel raporunu kullanabilir "
        "ya da bu fontlardan birini sisteme kurabilirsiniz."
    )


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle("title", fontName=BOLD_FONT, fontSize=18, leading=22, textColor=SECTION_FG),
        "subtitle": ParagraphStyle("subtitle", fontName=BASE_FONT, fontSize=9.5, leading=13, textColor=MUTED),
        "section": ParagraphStyle("section", fontName=BOLD_FONT, fontSize=12, leading=16, textColor=SECTION_FG),
        "note": ParagraphStyle("note", fontName=BASE_FONT, fontSize=8, leading=11, textColor=MUTED),
        "cell": ParagraphStyle("cell", fontName=BASE_FONT, fontSize=7.5, leading=9.5),
        "cell_head": ParagraphStyle(
            "cell_head", fontName=BOLD_FONT, fontSize=7.5, leading=9.5, textColor=HEADER_FG
        ),
        "kpi_label": ParagraphStyle(
            "kpi_label", fontName=BASE_FONT, fontSize=8, leading=10, textColor=MUTED, alignment=TA_CENTER
        ),
        "kpi_value": ParagraphStyle(
            "kpi_value", fontName=BOLD_FONT, fontSize=17, leading=20, textColor=SECTION_FG, alignment=TA_CENTER
        ),
    }


# --------------------------------------------------------------------------
# Yardimcilar
# --------------------------------------------------------------------------


def _fmt(value: object) -> str:
    """Hucre degerini rapora uygun, kisa bir metne cevirir (NaN -> bos,
    tam sayiya esit float -> ondaliksiz)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, float):
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


def _section_title(text: str, styles: dict) -> Table:
    """Excel raporundaki mavi bolum basligiyla ayni gorunumu verir."""
    table = Table([[Paragraph(text, styles["section"])]], colWidths=[PAGE_SIZE[0] - 2 * MARGIN])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SECTION_BG),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _dataframe_table(df: pd.DataFrame, styles: dict) -> list:
    """Bir DataFrame'i, basligi her sayfada tekrarlanan bicimli bir tabloya cevirir.

    Metin kolonlari `Paragraph`'a sarilir (uzun is basliklari hucreden tasmak
    yerine alt satira gecer); sayisal kolonlar saga yaslanir.
    """
    if df is None or df.empty:
        return [Paragraph("Bu bölüm için kayıt bulunamadı.", styles["note"]), Spacer(1, 6)]

    truncated = len(df) > MAX_TABLE_ROWS
    shown = df.head(MAX_TABLE_ROWS)

    numeric = {col: pd.api.types.is_numeric_dtype(df[col]) for col in df.columns}

    header = [Paragraph(str(col), styles["cell_head"]) for col in shown.columns]
    body = [
        [
            Paragraph(_fmt(row[col]), styles["cell"]) if col in WRAP_COLUMNS else _fmt(row[col])
            for col in shown.columns
        ]
        for _, row in shown.iterrows()
    ]

    available = PAGE_SIZE[0] - 2 * MARGIN
    # Sarmalanan (uzun metin) kolonlara diger kolonlarin 3 kati genislik verilir.
    weights = [3.0 if col in WRAP_COLUMNS else 1.0 for col in shown.columns]
    total_weight = sum(weights)
    col_widths = [available * w / total_weight for w in weights]

    table = Table([header, *body], colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), HEADER_FG),
        ("FONTNAME", (0, 0), (-1, -1), BASE_FONT),
        ("FONTSIZE", (0, 1), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.4, GRID_COLOR),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ROW_ALT_BG]),
    ]
    for idx, col in enumerate(shown.columns):
        if numeric[col]:
            style.append(("ALIGN", (idx, 1), (idx, -1), "RIGHT"))

    table.setStyle(TableStyle(style))

    flow: list = [table]
    if truncated:
        flow.append(Spacer(1, 3))
        flow.append(
            Paragraph(
                f"Not: Tabloda {len(df):,} kaydın ilk {MAX_TABLE_ROWS:,} tanesi gösterilmiştir. "
                "Tam liste için Excel raporunu kullanın.",
                styles["note"],
            )
        )
    return flow


def _kpi_cards(summary: dict, styles: dict) -> Table:
    """Ust bolumdeki 6 KPI kutusu."""
    cards = [
        ("Taahhüt Edilen SP", _fmt(summary.get("committed_sp", 0))),
        ("Gerçekleşen SP", _fmt(summary.get("completed_sp", 0))),
        ("Plan Dışı SP", _fmt(summary.get("out_of_plan_sp", 0))),
        ("Toplam Tamamlanan SP", _fmt(summary.get("total_completed_sp", 0))),
        ("Tamamlanma Oranı", f"%{summary.get('completion_rate', 0):.1f}"),
        ("Plan Dışı Oranı", f"%{summary.get('out_of_plan_rate', 0):.1f}"),
    ]
    row = [
        [Paragraph(value, styles["kpi_value"]), Paragraph(label, styles["kpi_label"])]
        for label, value in cards
    ]
    # Her kutu kendi icinde 2 satirli (deger ustte, etiket altta) bir mini tablodur.
    boxes = []
    for cell in row:
        box = Table([[cell[0]], [cell[1]]])
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), SECTION_BG),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        boxes.append(box)

    available = PAGE_SIZE[0] - 2 * MARGIN
    outer = Table([boxes], colWidths=[available / len(boxes)] * len(boxes))
    outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (-1, -1), 3)]))
    return outer


def _history_dataframe(iteration_history: list[tuple[str, dict]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"İterasyon": label, **{tr: summary.get(key, 0) for key, tr in METRIC_COLUMNS}}
            for label, summary in iteration_history
        ]
    )


def _monthly_trend_image(history_df: pd.DataFrame) -> Image | None:
    """Aylik trend bar chart'ini PNG olarak gomulebilir bir `Image`'a cevirir.

    `kaleido` kurulu degilse veya PNG uretimi basarisiz olursa `None` doner -
    cagiran taraf grafigi ATLAR, rapor yine de uretilir (grafik opsiyonel bir
    susleme; sayisal tablo zaten hemen altinda yer alir).
    """
    if history_df.empty:
        return None
    try:
        import plotly.graph_objects as go

        fig = go.Figure()
        for idx, (_, label) in enumerate(METRIC_COLUMNS):
            fig.add_bar(
                x=history_df["İterasyon"],
                y=history_df[label],
                name=label,
                marker_color=CHART_COLORS[idx % len(CHART_COLORS)],
            )
        fig.update_layout(
            barmode="group",
            template="plotly_white",
            height=340,
            width=1100,
            margin=dict(l=50, r=20, t=40, b=50),
            legend=dict(orientation="h", y=-0.22),
            yaxis_title="SP",
            font=dict(size=12),
        )
        png = fig.to_image(format="png", scale=2)
    except Exception:  # noqa: BLE001 - kaleido yok / render hatasi: grafiksiz devam
        return None

    available = PAGE_SIZE[0] - 2 * MARGIN
    return Image(BytesIO(png), width=available, height=available * 340 / 1100)


# --------------------------------------------------------------------------
# Genel API
# --------------------------------------------------------------------------


def create_pdf_report(
    processed_data: dict,
    target_month: str | None = None,
    iteration_history: list[tuple[str, dict]] | None = None,
    project_label: str | None = None,
) -> bytes:
    """`process_sprint_report` ciktisini tek bir PDF'e yazar ve BYTE olarak doner
    (diske yazmaz - Streamlit'in `st.download_button`'ina dogrudan verilebilir).

    Rapor bolumleri, iterasyon kapanis e-postasinin formatini BIREBIR takip eder
    (bkz. `reporter.create_excel_report` - Excel ile AYNI bolumler, AYNI sirada):
        1) Baslik + kapsam (ay, proje, olusturma zamani)
        2) 6 KPI kutusu (taahhut/gerceklesen/plan disi/toplam SP, tamamlanma ve
           plan disi oranlari) - hedef aya ait ozet
        3) "İterasyon Bazlı İş Büyüklüğü (SP)": Taahhüt Edilen/Gerçekleşen/
           Plan Dışı/Toplam grafigi + ayni verinin tablosu
        4) "<ay> iterasyonunda planlanan iş listemiz ve statüleri" (6 kolon)
        5) "<ay> iterasyonunda plan dışı iş listemiz ve statüleri" (4 kolon)

    Rapor formati e-postada sayilan bu bolumlerle SINIRLIDIR; statu dagilimi ve
    kisi bazli yuk gibi ek analizler KASITLI olarak bu rapora girmez (panelde
    kendi sayfalarinda incelenir).

    `processed_data`, `reporter.create_excel_report` ile AYNI sozluktur (`data`,
    `planned_issues`, `out_of_plan_issues`, `summary`, `monthly_history`,
    `target_month`). Ay etiketi oncelik sirasi da aynidir: `target_month`
    parametresi > `processed_data["target_month"]` > ay onekisiz genel ifade.

    Sistemde Turkce karakterli bir TTF font yoksa `PdfFontError` yukseltir.
    """
    _register_turkish_font()
    styles = _styles()

    if iteration_history is None:
        iteration_history = processed_data.get("monthly_history") or [
            (target_month or "Güncel İterasyon", processed_data.get("summary", {}))
        ]

    label = target_month or processed_data.get("target_month")
    summary = processed_data.get("summary", {}) or {}

    story: list = []

    # 1) Baslik
    # Baslik, iterasyon kapanis e-postasinin basligiyla ayni kaliptadir:
    # "<Proje> - <Ay> İterasyon Kapanış Sonuçları".
    baslik_parcalari = [p for p in (project_label, label) if p]
    baslik = " - ".join(baslik_parcalari) + " İterasyon Kapanış Sonuçları" if baslik_parcalari else "İterasyon Kapanış Sonuçları"
    story.append(Paragraph(baslik.replace("&", "&amp;"), styles["title"]))
    kapsam = [f"Kapsam: {label}" if label else "Kapsam: Tüm Aylar"]
    if project_label:
        kapsam.insert(0, f"Proje: {project_label}")
    kapsam.append(f"Oluşturulma: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    kapsam.append(f"Toplam kart: {summary.get('total_issue_count', 0):,}")
    story.append(Paragraph("  •  ".join(kapsam), styles["subtitle"]))
    story.append(Spacer(1, 10))

    # 2) KPI kutulari
    story.append(_kpi_cards(summary, styles))
    story.append(Spacer(1, 14))

    # 3) Aylik trend - grafik + tablo
    history_df = _history_dataframe(iteration_history)
    story.append(_section_title("İterasyon Bazlı İş Büyüklüğü (SP)", styles))
    story.append(Spacer(1, 6))
    chart = _monthly_trend_image(history_df)
    if chart is not None:
        story.append(chart)
        story.append(Spacer(1, 8))
    story.extend(_dataframe_table(history_df, styles))
    story.append(Spacer(1, 14))

    # 4-5) Is listeleri
    prefix = f"{label} iterasyonunda " if label else ""
    planned_title = (
        f"{prefix}planlanan iş listemiz ve statüleri:" if prefix else "Planlanan iş listemiz ve statüleri:"
    )
    out_of_plan_title = (
        f"{prefix}plan dışı iş listemiz ve statüleri:" if prefix else "Plan dışı iş listemiz ve statüleri:"
    )

    story.append(PageBreak())
    story.append(_section_title(planned_title, styles))
    story.append(Spacer(1, 6))
    story.extend(_dataframe_table(processed_data.get("planned_issues"), styles))
    story.append(Spacer(1, 14))

    story.append(KeepTogether([_section_title(out_of_plan_title, styles), Spacer(1, 6)]))
    story.extend(_dataframe_table(processed_data.get("out_of_plan_issues"), styles))

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title="Sprint & KPI Raporu",
        author="Jira Sprint & KPI Paneli",
    )
    doc.build(story, onFirstPage=_draw_page_number, onLaterPages=_draw_page_number)
    return buffer.getvalue()


def _draw_page_number(canvas, doc) -> None:
    """Her sayfanin altina sayfa numarasi ve kaynak notu yazar."""
    canvas.saveState()
    canvas.setFont(BASE_FONT, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(PAGE_SIZE[0] - MARGIN, MARGIN * 0.55, f"Sayfa {doc.page}")
    canvas.drawString(MARGIN, MARGIN * 0.55, "Jira Sprint & KPI Akıllı Asistan Paneli")
    canvas.restoreState()
