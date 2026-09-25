"""Jira Sprint & KPI Akıllı Asistan Paneli - alternatif ("new") tasarım.

`dashboard.py` ile aynı `src.processor` / `src.reporter` fonksiyonlarını kullanır,
fakat farklı bir gezinme modeli (sekmeler yerine kenar çubuğu navigasyonu),
anlamli durum rozetleri (İyi/Dikkat/Kritik) ve tema-duyarlı (koyu/açık) bir
kart tasarımı sunar. `filter_by_project` gibi orijinal panoda kullanılmayan
bir fonksiyonu da bir "Proje" filtresi olarak devreye sokar.

Çalıştırmak için proje kökünden: `streamlit run app/new_dashboard.py`
"""

from __future__ import annotations

import base64
import importlib
import inspect
import sys
import tempfile
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Streamlit keeps imported modules between reruns. An already running dashboard
# can hold the previous config module after the settings editor is added.
import config as jira_config_module  # noqa: E402
if not hasattr(jira_config_module, "save_jira_settings"):
    importlib.reload(jira_config_module)

# Keep the corrected WIP calculation active in an already open Streamlit process.
import processor as jira_processor_module  # noqa: E402
if (
    getattr(jira_processor_module, "WIP_BUCKET_LABELS", None) != (
        "0-1 Aylık Aktif İş", "2-5 Aylık Aktif İş", "6+ Aylık Aktif İş"
    )
    or "require_sprint_membership" not in inspect.signature(
        jira_processor_module.calculate_capacity_forecast_split
    ).parameters
):
    importlib.reload(jira_processor_module)

from processor import (  # noqa: E402
    JiraApiError,
    JiraSslError,
    analyze_advanced_bottlenecks,
    analyze_carried_over_issues,
    analyze_estimation_accuracy,
    analyze_projects_by_subject,
    build_month_sprint_labels,
    build_monthly_history,
    build_out_of_plan_issues_table,
    build_planned_issues_table,
    calculate_assignee_metrics,
    calculate_capacity_forecast_split,
    calculate_sprint_kpis,
    calculate_status_breakdown,
    compare_yearly_sprints,
    detect_recurring_bottlenecks,
    drop_duplicate_rows,
    explode_by_role,
    fetch_issues_from_jira_api,
    filter_by_month,
    filter_by_person,
    filter_by_project,
    filter_out_of_plan_issues,
    filter_planned_issues,
    get_assignee_deep_dive,
    get_topic_deep_dive,
    process_sprint_report,
    read_sprint_report,
    run_core_5_kpi_analyses,
    search_issues_by_query,
    standardize_dataframe,
)
from share_report import (  # noqa: E402
    ShareBullet,
    ShareDocument,
    ShareSection,
    render_share_html,
    render_share_text,
)
from weekly_report import (  # noqa: E402
    WeeklyReportError,
    analyze_week,
    available_weeks,
    build_share_document as build_weekly_share_document,
    finding_sentence as weekly_finding_sentence,
    week_label,
)
from config import load_jira_config, save_jira_settings  # noqa: E402
from pdf_reporter import PdfFontError, create_pdf_report  # noqa: E402
from reporter import create_excel_report  # noqa: E402
from llm_assistant import AssistantUnavailableError, DEFAULT_MODEL, chat_with_local_model  # noqa: E402

# --------------------------------------------------------------------------
# Renk paleti - Türkcell kurumsal kimliği (resmi Pantone karşılıkları). Anahtar
# adları (blue/orange/aqua/...) dosya genelinde sabit kaldığı için değişmedi;
# sadece değerler markanın resmi renklerine göre güncellendi. Markada karşılığı
# olmayan 3 slot (magenta/green/violet), marka dışı (mor/yeşil) tonlar yerine
# destek grinin (Cool Gray 10C) açık/koyu türevleriyle dolduruldu - böylece
# tüm palet marka ailesinin içinde kalır.
# --------------------------------------------------------------------------

CATEGORICAL = {
    "blue": "#002395",      # Ana lacivert (Pantone Reflex Blue C)
    "orange": "#FF6A13",    # Destek turuncu (Pantone 1585 C)
    "aqua": "#00A9E0",      # Açık mavi aksan (Pantone 2995 C)
    "yellow": "#FFC72C",    # Türkcell sarısı / amblem rengi (Pantone 123 C)
    "magenta": "#63666A",   # Destek gri (Cool Gray 10C)
    "green": "#3B3F45",     # Destek griden türetilmiş koyu ton
    "violet": "#A8ABAE",    # Destek griden türetilmiş açık ton
    "red": "#B23A00",       # Destek turuncudan türetilmiş koyu ton (rezerve)
}

STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
STATUS_ICON = {"good": "🟢", "warning": "🟡", "serious": "🟠", "critical": "🔴"}

ACCENT = CATEGORICAL["blue"]
GRID_COLOR = "rgba(127, 127, 127, 0.25)"

# Haftalık Özet sayfası - bulgu şiddetinin renk karşılıkları ve hafta seçicide
# gösterilecek en fazla hafta sayısı.
WEEKLY_SEVERITY_RENK = {
    "kritik": STATUS["critical"],
    "dikkat": STATUS["serious"],
    "iyi": STATUS["good"],
    "notr": CATEGORICAL["blue"],
}
WEEKLY_SELECTABLE_WEEKS = 12
# Kart zemin/kenarlığı, notr griden Türkcell lacivertine (#002395) çok hafif
# kaydırıldı - .streamlit/config.toml'daki sayfa arka planıyla (kayık sarı/lacivert
# tonlar) tutarlı, marka hissi veren ama okunabilirliği bozmayan bir doku için.
CARD_BG = "rgba(0, 35, 149, 0.055)"
CARD_BORDER = "rgba(0, 35, 149, 0.18)"

ITERATION_CHART_METRICS: dict[str, str] = {
    "Taahhüt Edilen SP": CATEGORICAL["blue"],
    "Gerçekleşen SP": CATEGORICAL["orange"],
    "Plan Dışı SP": CATEGORICAL["aqua"],
    "Toplam Tamamlanan SP": CATEGORICAL["yellow"],
}

FLOW_STAGE_COLORS = {
    "Backlog / To Do": CATEGORICAL["blue"],
    "In Progress": CATEGORICAL["orange"],
    "Review / Test": CATEGORICAL["aqua"],
    "Blocked / Hold": CATEGORICAL["yellow"],
    "Done": CATEGORICAL["magenta"],
    "Cancelled": CATEGORICAL["green"],
    "Diğer": CATEGORICAL["violet"],
}

WIP_BUCKET_STATUS = {
    "0-1 Aylık Aktif İş": "warning",
    "2-5 Aylık Aktif İş": "serious",
    "6+ Aylık Aktif İş": "critical",
}

# `analyze_advanced_bottlenecks`'in `1_wip_aging` sozlugundeki kova anahtarlari ile
# UI'da/gorafikte gosterilen Turkce etiketleri arasindaki esleme.
WIP_BUCKET_KEY_TO_LABEL = {
    "onceki_ay": "0-1 Aylık Aktif İş",
    "uc_aylik": "2-5 Aylık Aktif İş",
    "alti_aylik": "6+ Aylık Aktif İş",
}
WIP_BUCKET_LABEL_TO_KEY = {v: k for k, v in WIP_BUCKET_KEY_TO_LABEL.items()}

# Her KPI kartinin/basliginin yanindaki (ⓘ) bilgilendirme ikonunun hover metni: nasil
# hesaplandigi + yuksek/dusuk degerin ne anlama geldigi. Tek bir yerden yonetilir.
KPI_HELP: dict[str, str] = {
    "toplam_kart": (
        "O ay içinde açılmış tüm kartların sayısıdır (planlanan + plan dışı). Yüksek "
        "olması iş hacminin arttığını, düşük olması ekibin daha az iş aldığını gösterir."
    ),
    "taahhut_sp": (
        "Seçili sprintte planlanan kabul edilen kartların Story Point (SP) "
        "toplamıdır - ekibin o ay için taahhüt ettiği iş büyüklüğü. Çok yüksek taahhüt "
        "kapasiteyi zorlayabilir; çok düşük taahhüt ekibin az iş aldığına işaret eder."
    ),
    "gerceklesen_sp": (
        "Taahhüt edilen kartlardan statüsü 'Done' olanların SP toplamıdır. Taahhüt "
        "edilene ne kadar yakınsa planlama o kadar isabetlidir."
    ),
    "tamamlanma_orani": (
        "Gerçekleşen SP'nin taahhüt edilen SP'ye oranıdır (Gerçekleşen/Taahhüt × 100). "
        "Yüksek oran (≥%85) sağlıklı planlamayı, düşük oran (<%60) aşırı taahhüdü ya da "
        "yürütme sorunlarını gösterir."
    ),
    "plan_disi_orani": (
        "Toplam tamamlanan SP içinde, etiket veya etkin tarih fallback'iyle plansız sayılan "
        "işlerin oranıdır. Yüksek oran (>%30) sık sık plan dışına çıkıldığını, düşük "
        "oran (<%15) planlamanın gerçekçi olduğunu gösterir."
    ),
    "velocity": (
        "Taahhüt edilen SP'ye kıyasla gerçekleştirilen SP oranı + son 3 ayın trendi. "
        "Yüksek ve istikrarlı bir değer, öngörülebilir bir ekip anlamına gelir."
    ),
    "scope_stability": (
        "Plan dışı SP'nin toplam yük (taahhüt + plan dışı) içindeki payıdır ('scope "
        "creep'). Yüksek oran kapsamın sık sık dışarıdan müdahaleyle değiştiğini, "
        "düşük oran kapsamın stabil kaldığını gösterir."
    ),
    "workload_equity": (
        "Kişi bazlı iş yükünün varyasyon katsayısıdır (std/ortalama). 0'a yakın değer "
        "yükün dengeli dağıldığını, yüksek değer bazı kişilerin aşırı yüklendiğini "
        "(tükenmişlik riski) gösterir."
    ),
    "workload_consistency": (
        "Seçili kişinin aylar arası iş yükü varyasyon katsayısıdır. Düşük değer yükün "
        "ay ay istikrarlı olduğunu, yüksek değer ani yük sıçramalarını gösterir."
    ),
    "flow_efficiency": (
        "Henüz 'Done' olmayan/iptal edilmemiş aktif iş sayısı ve bunlardan tıkanmış/"
        "kritik büyüklükte olanların oranıdır. Yüksek kritik iş sayısı akışta darboğaz "
        "olduğuna işaret eder."
    ),
    "estimation_accuracy": (
        "Talep tipi bazında hedeflenen SP ile gerçekleşen SP arasındaki sapma oranıdır. "
        "Yüksek sapma oranı, o talep tipinde tahminlerin güvenilmez olduğunu gösterir."
    ),
    "capacity_forecast": (
        "Bu ayın taahhüt ettiği SP'nin geçmiş ayların ortalama taahhüdüne oranıdır; "
        "gelecek ay için önerilen kapasite ise TAMAMLANMIŞ ayların GERÇEKLEŞEN SP "
        "ortalamasına dayanır (seçili ay veride bulunan en güncel/devam eden aysa, "
        "henüz eksik olabilecek gerçekleşen verisi bu ortalamayı yapay şekilde "
        "düşürmesin diye hesaba katılmaz). %100 üzeri ekibin normalden fazla, altı "
        "ise az iş aldığını gösterir."
    ),
    "ekip_yuku": (
        "Her sorumlunun üzerindeki toplam kart adedidir (üzerine gelince SP'si de "
        "görünür). Diğerlerinden belirgin şekilde yüksek olan kişi, iş yükü "
        "dengesizliği ve tükenmişlik riski taşıyabilir."
    ),
    "proje_konu": (
        "Jira Component alanına göre gruplanan kartların adedidir (üzerine gelince "
        "toplam SP görünür). En yüksek değer, en çok kaynak tüketen konuyu gösterir."
    ),
    "wip_onceki_ay": (
        "Bu ay veya bir önceki ay açılmış, seçili sprintte hâlâ aktif kartların sayısı ve SP'sidir."
    ),
    "wip_uc_aylik": (
        "2-5 ay önce açılmış ve seçili sprintte hâlâ aktif olan kartlardır."
    ),
    "wip_alti_aylik": (
        "6 ay veya daha uzun süredir açık olan kartlardır. Yüksek "
        "sayı backlog temizliği veya süreç incelemesi gerektiğini gösterir."
    ),
    "blocker_hold": (
        "Statü/etiket/özetinde blok/hold/bekleme ifadesi geçen kartların oranı ve SP "
        "maliyetidir. Yüksek oran, ekibin dış bağımlılıklar yüzünden sık sık "
        "durduğunu gösterir."
    ),
    "bouncing": (
        "En yüklü %20'lik dilimin toplam iş yükü içindeki payıdır (gerçek handoff "
        "verisi yoksa proxy). Yüksek oran, işin az sayıda kişide yoğunlaştığını "
        "(bus factor riski) gösterir."
    ),
    "reopen": (
        "Statüsü tekrar açılan/test aşamasında takılan kartların oranıdır (proxy "
        "olabilir). Yüksek oran, kalite/QA sürecinde tekrarlayan sorunlar olduğunu "
        "gösterir."
    ),
    "flow_load": (
        "İşlerin akış aşamalarına (Backlog, In Progress, Review/Test, Blocked, "
        "Done...) göre dağılımıdır. Bir aşamada yığılma, o aşamanın darboğaz "
        "olduğunu gösterir."
    ),
    "durum_dagilimi": (
        "Jira'nın ham statü alanına (To Do, In Progress, Code Review, Done vb. - "
        "gruplanmamış, ham etiketler) göre kart dağılımıdır. Belirli bir statüde "
        "(özellikle Done dışı bir statüde) yığılma, o aşamanın darboğaz olduğuna "
        "işaret eder; dağılımın Done'a doğru kayması sağlıklıdır."
    ),
    "kisi_durum_dagilimi": (
        "Seçili kişinin/konunun kartlarının statülere göre dağılımıdır. Done "
        "dışındaki statülerde yığılma, işlerin tamamlanamadan biriktiğini gösterir; "
        "Done oranının yüksek olması sağlıklıdır."
    ),
    "kisi_talep_tipi_dagilimi": (
        "Seçili kişinin/konunun kartlarının talep tipine (Bug/Story/Task vb.) göre "
        "dağılımıdır. Bug oranının diğer tiplere göre yüksek olması kalite "
        "sorunlarına işaret edebilir."
    ),
    "aylik_trend_darbogaz": (
        "Blocker & Hold, Reopen ve Yük Yoğunlaşma oranlarının; WIP Aging'in (aktif "
        "iş sayısı/ortalama yaş) ay ay değişimidir. Tüm metriklerde düşük ve "
        "yatay/azalan bir çizgi sağlıklı bir süreci, yükselen bir trend sürecin "
        "kötüleştiğini gösterir."
    ),
    "yonetici_ozeti": (
        "Seçili ay/kapsam için taahhüt, gerçekleşen, tamamlanma ve plan dışı oranı "
        "gibi temel sprint KPI'larının özetidir. Bu kartlar birlikte, ekibin o "
        "dönemki planlama isabeti ve teslim performansını gösterir."
    ),
    "iterasyon_grafigi": (
        "Seçili ayın içinde bulunduğu takvim yılında, Ocak'tan o aya kadar olan "
        "her ayın taahhüt/gerçekleşen/plan dışı SP karşılaştırmasıdır. Taahhüt "
        "barı gerçekleşenden sürekli yüksekse planlama iyileştirilmeli; barlar "
        "birbirine yakınsa planlama isabetli demektir."
    ),
    "core_5_kpi_paketi": (
        "Ekibin performansını 5 farklı boyutta (Velocity, Scope Stability, "
        "Workload, Flow Efficiency, Estimation Accuracy) özetleyen profesyonel bir "
        "KPI paketidir. Her kartın kendi ⓘ ikonu, o boyutun nasıl hesaplandığını "
        "ayrı ayrı açıklar."
    ),
    "kisi_drill_down": (
        "Seçili kişinin tüm kartlarının ve performansının detaylı görünümüdür. "
        "Tamamlanma oranı yüksekse kişi işlerini zamanında bitiriyor demektir; "
        "düşükse üzerinde biriken/tamamlanamayan iş olabilir."
    ),
    "planlanan_isler": (
        "'SprintDışı' etiketi/fallback kuralıyla plan dışı sayılmayan, yani taahhüt edilmiş "
        "kartların listesidir. Bu listenin büyük kısmı 'Done' değilse, taahhüt "
        "edilen işin tamamlanamadığına işaret eder."
    ),
    "plan_disi_isler": (
        "'SprintDışı' etiketiyle veya etkin tarih fallback'iyle plansız sayılan "
        "kartların listesidir. Bu listenin kabarık olması, sprint kapsamının sık "
        "sık dışarıdan müdahaleyle değiştiğini (scope creep) gösterir."
    ),
    "ileri_darbogaz_genel": (
        "Ekibin iş akışındaki gizli tıkanıklıkları ve süreç verimsizliklerini 5 "
        "farklı açıdan (WIP Aging, Blocker & Hold, Assignee Bouncing, Reopen "
        "Oranı, Flow Load) inceleyen ileri düzey analiz paketidir. Aşağıdaki her "
        "alt bölümün kendi ⓘ ikonu, o metriğin nasıl hesaplandığını ayrı ayrı "
        "açıklar."
    ),
    "wip_aging_genel": (
        "Henüz 'Done' olmayan aktif işlerin ne kadar süredir açık kaldığını "
        "gösterir. Aktif iş sayısı ve ortalama açık kalma süresi arttıkça, "
        "ekibin işleri bitirmekte zorlandığına işaret eder."
    ),
    "blocker_akis_yuku_genel": (
        "Tıkanan işlerin maliyetini (Blocker & Hold) ve işlerin akış aşamalarına "
        "göre nerede yığıldığını (Flow Load) bir arada gösterir - ekibin "
        "sürecinde nerede zaman kaybettiğini anlamaya yardımcı olur."
    ),
    "yogunlasma_reopen_genel": (
        "İş yükünün ekipte ne kadar yoğunlaştığını (Assignee Bouncing, proxy) ve "
        "işlerin ne sıklıkla geri açıldığını/takıldığını (Reopen Oranı) bir arada "
        "gösterir - her ikisi de yüksekse ekip hem yük dengesizliği hem kalite "
        "riski taşıyor demektir."
    ),
    "devam_eden_darbogaz": (
        "Kart özetindeki '(%80)' gibi yüzde ifadeleri çıkarılarak elde edilen 'temel "
        "isim' + sorumlu ikilisine göre gruplanan işlerden, henüz Done olmamış EN AZ "
        "BİR kartı olan veya farklı yüzdelerle birden fazla kez Done olmuş işlerdir - "
        "VE bu gruplardan sadece güncel ayda da bir kartı bulunanlar (yani hâlâ devam "
        "edenler) listelenir. Bu liste doluysa, aynı işin ay ay 'bitmeden tekrar "
        "açıldığını' - yani gerçek bir tamamlanmadan çok, kronik/tekrarlayan bir "
        "yükün elden ele dolaştığını gösterir."
    ),
    "devreden_isler": (
        "Seçili sprintte bulunan ve Jira'nın Sprint alanında daha eski en az bir "
        "sprint üyeliği de taşıyan gerçek kartlardır. Created tarihine veya benzer "
        "iş adına göre tahmin yapılmaz; bu nedenle isim bazlı 'Devam Eden "
        "Darboğazlar' analizinden farklıdır."
    ),
}

# Akıllı Asistan sayfasındaki "Kullanılabilir MCP Araçları" katalogunda gösterilen
# 13 gerçek MCP aracı (bkz. src/mcp_server.py), 5 kategoriye ayrılmış (kategori ->
# [(arac_adi, kisa_aciklama), ...]) - aciklamalar ilgili aracin docstring'inin ilk
# cumlesidir.
MCP_TOOL_CATALOG: dict[str, list[tuple[str, str]]] = {
    "📊 Temel Analiz & Rapor": [
        ("analyze_sprint", "Bir Jira sprint/iterasyon raporunu işler ve KPI özetini döner."),
        ("create_sprint_excel", "Sprint raporunu tek sayfalı, biçimlendirilmiş bir Excel raporuna dönüştürür."),
        ("get_core_5_kpis_report", "5 temel/profesyonel KPI'ı (Velocity, Scope Stability, Workload Equity, Flow Efficiency, Estimation Accuracy) tek seferde çalıştırır."),
    ],
    "👤 Kişi & Ekip": [
        ("get_assignee_performance", "Sorumlu bazında iş yükü ve performans tablosunu döner."),
        ("get_assignee_details_tool", "Belirli bir kişinin tüm görevlerine detaylı (drill-down) erişim sağlar."),
    ],
    "🚧 Darboğaz & Akış": [
        ("get_bottlenecks_report", "Takımın iş akışındaki tıkanıklıkları/darboğazları tespit edip özetler."),
        ("get_advanced_bottlenecks_report", "WIP Aging, Blocker & Hold, Assignee Bouncing, Reopen Oranı, Flow Load gibi 5 ileri düzey akış metriğini raporlar."),
    ],
    "📈 Trend & Karşılaştırma": [
        ("get_sprint_trends", "Birden fazla ayın KPI'larını yan yana karşılaştırarak trend analizi sunar."),
        ("get_status_breakdown", "İşlerin statü (durum/aşama) bazında dağılım özetini döner."),
        ("get_estimation_accuracy_report", "Talep tipine göre tahmin (Estimate/SP) doğruluğunu ve sapmasını analiz eder."),
        ("get_project_subject_analysis", "Kartları Component alanına göre gruplayıp kaynak tüketimini ve tamamlanma oranını raporlar."),
    ],
    "🔎 Arama & Doğal Dil Sorgu": [
        ("search_issues_by_query", "Serbest metinle kartların metin alanlarında arama yapar."),
        ("query_sprint_data_natural", "Doğal dilde sorulan soruları anlaşılır bir Türkçe metin olarak yanıtlar."),
    ],
}

# Sayfalar ADIYLA referans verilir (`page == PAGE_EKIP`), indeksle DEĞİL: araya
# yeni bir sayfa eklendiğinde tüm indekslerin kayması, sessizce yanlış sayfanın
# açılmasına yol açardı.
PAGE_GENEL = "🏠 Genel Bakış"
PAGE_HAFTALIK = "🗓️ Haftalık Özet"
PAGE_EKIP = "👥 Ekip & Kişiler"
PAGE_PROJE = "📁 Proje & Konu"
PAGE_AKIS = "🚧 Akış & Darboğazlar"
PAGE_ASISTAN = "💬 Akıllı Asistan"
PAGE_RAPOR = "📤 Rapor Merkezi"

NAV_PAGES = [
    PAGE_GENEL,
    PAGE_HAFTALIK,
    PAGE_EKIP,
    PAGE_PROJE,
    PAGE_AKIS,
    PAGE_ASISTAN,
    PAGE_RAPOR,
]

# --------------------------------------------------------------------------
# Sayfa konfigurasyonu ve stil
# --------------------------------------------------------------------------

ASSETS_DIR = APP_DIR / "assets"
LOGO_FULL_PATH = ASSETS_DIR / "logo_full.png"
LOGO_ICON_PATH = ASSETS_DIR / "logo_icon.png"
WATERMARK_PATH = ASSETS_DIR / "teknocan_watermark.png"
FONT_REGULAR_PATH = ASSETS_DIR / "fonts" / "Turkcell_Satura_Regular.ttf"
FONT_BOLD_PATH = ASSETS_DIR / "fonts" / "Turkcell_Satura_Bold.ttf"


def _asset_data_uri(path: Path, mime_type: str) -> str | None:
    """Bir varlik dosyasini (font/gorsel) tarayicinin dogrudan erisebilecegi bir
    base64 data URI'ye cevirir. Streamlit, CSS icinde referans verilen yerel dosya
    yollarini tarayiciya servis ETMEZ (yalnizca `st.image`/`page_icon` gibi kendi
    bilesenleri iceriyi kendi sunar); bu yuzden `@font-face`/`background-image`
    gibi dogrudan CSS'te kullanilacak varliklar icin bu donusum gerekir. Dosya
    bulunamazsa None doner - cagiran kod (font/watermark) buna gore fallback
    uygulamalidir (orn. sans-serif font)."""
    if not path.exists():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


FONT_REGULAR_URI = _asset_data_uri(FONT_REGULAR_PATH, "font/ttf")
FONT_BOLD_URI = _asset_data_uri(FONT_BOLD_PATH, "font/ttf")

_font_face_rules: list[str] = []
if FONT_REGULAR_URI:
    _font_face_rules.append(
        "@font-face { font-family: 'Turkcell Satura'; "
        f"src: url('{FONT_REGULAR_URI}') format('truetype'); "
        "font-weight: 400; font-style: normal; font-display: swap; }"
    )
if FONT_BOLD_URI:
    _font_face_rules.append(
        "@font-face { font-family: 'Turkcell Satura'; "
        f"src: url('{FONT_BOLD_URI}') format('truetype'); "
        "font-weight: 700; font-style: normal; font-display: swap; }"
    )
# Font dosyalari bulunamazsa (tasindiysa/eksikse) sessizce sans-serif'e duser.
FONT_FAMILY_CSS = "'Turkcell Satura', sans-serif" if _font_face_rules else "sans-serif"
FONT_FACE_CSS_BLOCK = "\n    ".join(_font_face_rules)

# Akıllı Asistan sayfasına ozel, cok silik arka plan gorseli icin (bkz. o sayfanin
# render bloğu) - dosya bulunamazsa None kalir ve o sayfa da watermark'siz gorunur.
WATERMARK_URI = _asset_data_uri(WATERMARK_PATH, "image/png")

st.set_page_config(
    page_title="Türkcell Jira Sprint & KPI Paneli",
    page_icon=str(LOGO_ICON_PATH) if LOGO_ICON_PATH.exists() else "📊",
    layout="wide",
)

st.markdown(
    f"""
    <style>
    {FONT_FACE_CSS_BLOCK}html, body, .stApp, [class*="css"], div[data-testid="stAppViewContainer"],
    div[data-testid="stSidebar"], button, input, select, textarea {{
        font-family: {FONT_FAMILY_CSS} !important;
    }}
    .block-container {{ padding-top: 1.6rem; max-width: 1280px; }}
    /* st.chat_input, .block-container'dan BAGIMSIZ, sayfa altina sabitlenmis ayri
    bir "stBottom" konteynerinde render edilir (Streamlit 1.61 frontend paketinde
    dogrulandi: stBottomBlockContainer, stMainBlockContainer/.block-container ile
    AYNI stMain kapsaminin icinde ama kendi max-width'i yok, tam genislige yayiliyor).
    Mesaj balonlarinin genisligiyle (.block-container: max-width 1280px, ortalanmis)
    birebir eslesmesi icin ayni max-width + otomatik kenar bosluguyla ortalanir. */
    div[data-testid="stBottomBlockContainer"] {{
        max-width: 1280px !important;
        margin-left: auto !important;
        margin-right: auto !important;
    }}
    div[data-testid="stMetric"] {{
        background-color: {CARD_BG};
        border: 1px solid {CARD_BORDER};
        border-left: 4px solid {ACCENT};
        border-radius: 0.7rem;
        padding: 0.9rem 1rem 0.6rem 1rem;
    }}
    div[data-testid="stMetricLabel"] {{ font-weight: 600; }}
    div[data-testid="stChatMessage"] {{ border-radius: 1rem; }}
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        border-radius: 0.9rem !important;
    }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label {{
        padding: 0.25rem 0;
        font-size: 0.95rem;
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# Kucuk UI yardimcilari (tema-duyarli: sabit metin rengi kullanilmaz, boylece
# hem acik hem koyu Streamlit temasinda okunakli kalir)
# --------------------------------------------------------------------------


def _status_tier(value: float, good_cut: float, warn_cut: float, higher_is_better: bool = True) -> str:
    """Bir orani/degeri esik degerlere gore "good"/"warning"/"critical" katmanina ayirir."""
    if higher_is_better:
        if value >= good_cut:
            return "good"
        if value >= warn_cut:
            return "warning"
        return "critical"
    if value <= good_cut:
        return "good"
    if value <= warn_cut:
        return "warning"
    return "critical"


def _badge(tier: str, text: str) -> str:
    """Renk-tek-basina anlam tasimayan, ikon + metin iceren kucuk bir durum rozeti."""
    return (
        f'<span style="display:inline-flex;align-items:center;gap:0.3rem;'
        f'font-size:0.78rem;font-weight:600;opacity:0.9;">{STATUS_ICON[tier]} {text}</span>'
    )


def _info_icon(help_text: str | None) -> str:
    """Bir metrigin nasil hesaplandigini ve yuksek/dusuk degerin ne anlama geldigini
    aciklayan, native tarayici tooltip'i (title attribute - JS/component gerektirmez)
    kullanan kucuk bir "ⓘ" ikonu doner. `help_text` bossa hicbir sey donmez."""
    if not help_text:
        return ""
    safe = help_text.replace('"', "&quot;")
    return f' <span title="{safe}" style="cursor:help;opacity:0.55;font-size:0.85em;">ⓘ</span>'


def _section(title: str, subtitle: str | None = None, help_text: str | None = None) -> None:
    # NOT: Ciktiyi TEK SATIRLIK (icinde \n olmayan) bir HTML string olarak
    # uretiyoruz. Cok satirli bir f\"\"\"...\"\"\" bloguyla, `subtitle_html` bos
    # oldugunda ({subtitle_html} tek basina bir satir tutuyordu) veya bir div'in
    # style attribute'u satir sonunda sardiginda, Markdown bunu bos/kesik bir
    # satir sanip HTML blogunu ORADA bitmis sayiyor ve devamini (gradient
    # cubugu) HAM METIN olarak basiyordu. Ardisik string literal'leri (implicit
    # concatenation) araya HICBIR karakter eklemeden birlestirdigi icin bu desen
    # HER ZAMAN tek satirlik, guvenli bir HTML string uretir.
    subtitle_html = (
        f'<div style="font-size:0.85rem;opacity:0.65;margin-top:0.1rem;">{subtitle}</div>'
        if subtitle
        else ""
    )
    st.markdown(
        f'<div style="margin:0.3rem 0 0.8rem 0;">'
        f'<div style="font-size:1.25rem;font-weight:700;">{title}{_info_icon(help_text)}</div>'
        f'{subtitle_html}'
        f'<div style="height:3px;border-radius:2px;margin-top:0.5rem;background:linear-gradient(90deg, {ACCENT}80, transparent);"></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _tile(
    label: str,
    value: str,
    badge: str | None = None,
    caption: str | None = None,
    accent: str = ACCENT,
    help_text: str | None = None,
) -> None:
    """Basliksiz, tema-duyarli bir istatistik karti (opsiyonel durum rozeti + bilgi ikonu)."""
    # NOT: _section() ile ayni sebeple, TEK SATIRLIK (icinde \n olmayan) bir HTML
    # string uretiyoruz - cok satirli f\"\"\"...\"\"\" blogunda badge/caption
    # verilmediginde {badge_html}{caption_html} satiri bosluktan ibaret kaliyor
    # ve Markdown HTML blogunu orada bitmis sayip devamini ham metin basiyordu.
    badge_html = f'<div style="margin-top:0.35rem;">{badge}</div>' if badge else ""
    caption_html = (
        f'<div style="font-size:0.72rem;opacity:0.6;margin-top:0.15rem;">{caption}</div>' if caption else ""
    )
    st.markdown(
        f'<div style="background:{CARD_BG};border:1px solid {CARD_BORDER};border-left:4px solid {accent};'
        f'border-radius:0.7rem;padding:0.85rem 1rem;height:100%;">'
        f'<div style="font-size:0.72rem;text-transform:uppercase;letter-spacing:0.04em;opacity:0.65;">{label}{_info_icon(help_text)}</div>'
        f'<div style="font-size:1.55rem;font-weight:800;line-height:1.15;margin-top:0.25rem;">{value}</div>'
        f'{badge_html}{caption_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _tool_card(ad: str, aciklama: str) -> None:
    """Akıllı Asistan sayfasındaki MCP araç kataloğu icin kucuk, basliksiz bir kart."""
    st.markdown(
        f'<div style="background:{CARD_BG};border:1px solid {CARD_BORDER};border-left:4px solid {ACCENT};'
        f'border-radius:0.7rem;padding:0.7rem 0.9rem;height:100%;">'
        f'<div style="font-size:0.82rem;font-weight:700;font-family:monospace;">{ad}</div>'
        f'<div style="font-size:0.75rem;opacity:0.7;margin-top:0.25rem;">{aciklama}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _clickable_tile(
    key: str,
    label: str,
    value: str,
    badge: str | None = None,
    caption: str | None = None,
    accent: str = ACCENT,
    help_text: str | None = None,
) -> bool:
    """`_tile` ile ayni gorunumde, altina bir acma/kapama TOGGLE'i eklenmis tiklanabilir
    bir ozet karti. Cagiran kod, donen deger `True` ise hemen altina bir detay tablosu
    render etmelidir.

    NOT: bilincli olarak `st.button` DEGIL `st.toggle` kullanilir. `st.button` ile
    "tiklaninca hemen acik/kapali metnini degistir" desenini (dugmenin etiketini
    `session_state`'ten okuyup manuel guncelleme) taklit etmeye calismak HER ZAMAN
    bir rerun GERI kalir: dugmenin gorunen metni o rerun BASLARKEN (henuz
    guncellenmemis) eski durumla render edilir - tiklamanin sonucu ALTTAKI icerikte
    hemen dogru yansisa da, dugmenin KENDI metni ancak BASKA bir etkilesimin
    tetikledigi BIR SONRAKI rerun'da "yetisir" - kullaniciya tikladigi anda yanlis/
    gecikmis bir sinyal verir. `st.toggle`, `key` ile kendi acik/kapali durumunu
    Streamlit'in widget state mekanizmasi araciligiyla DOGRUDAN yonetir; bu yuzden
    tiklandigi ANDA gorsel durumu (switch pozisyonu) DOGRU ve GECIKMESIZ yansir.

    Baslangic degeri icin bilincli olarak `value=` PARAMETRESI GECILMEZ - sadece
    `key` verilir. Cagiran kod, varsayilani "acik" yapmak isterse widget
    olusturulmadan ONCE `st.session_state[f"open_{{key}}"] = True` atamalidir
    (bkz. WIP Aging kova ornegi); `value=` ile `session_state` uzerinden
    atanmis bir degeri AYNI ANDA vermek Streamlit'te bir uyariya yol acar (iki
    farkli mekanizmayla ayni widget'in degerini belirlemeye calismak).
    """
    _tile(label, value, badge=badge, caption=caption, accent=accent, help_text=help_text)
    return st.toggle("Detay", key=f"open_{key}")


def _apply_chart_chrome(fig: go.Figure, height: int = 380, yaxis_title: str | None = None) -> None:
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=25, b=10, l=10, r=10),
        height=height,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    )
    if yaxis_title:
        fig.update_layout(yaxis_title=yaxis_title)
    fig.update_yaxes(gridcolor=GRID_COLOR, zeroline=False)
    fig.update_xaxes(showgrid=False)


def _render_capacity_forecast_group(forecast: dict) -> None:
    """`calculate_capacity_forecast`/`calculate_capacity_forecast_split`'in ürettiği TEK
    bir tahmin sözlüğünü, "Bu Ay Alınan İş / Geçmiş Ort. Önerilen / Gelecek Ay Önerilen
    Kapasite" 3'lü kart düzeninde render eder - Sprint (Planlanan) ve Sprint Dışı
    tahminleri AYNI görünümü paylaştığı için tek bir yerden çağrılır."""
    f1, f2, f3 = st.columns(3)
    with f1:
        oran = forecast["karsilastirma_orani_yuzde"]
        if oran == 0.0 and forecast["gecmis_ort_taahhut_sp"] == 0.0:
            karsilastirma_tier, karsilastirma_metni = "warning", "Kıyaslama İçin Veri Yok"
        elif oran < 85:
            karsilastirma_tier, karsilastirma_metni = "warning", "Az İş Alındı"
        elif oran > 115:
            karsilastirma_tier, karsilastirma_metni = "serious", "Çok İş Alındı"
        else:
            karsilastirma_tier, karsilastirma_metni = "good", "Normal"
        _tile(
            "Bu Ay Alınan İş (Taahhüt SP)",
            f"{forecast['bu_ay_taahhut_sp']:.0f}",
            badge=_badge(karsilastirma_tier, karsilastirma_metni),
            caption=f"Geçmiş ortalamanın %{forecast['karsilastirma_orani_yuzde']:.0f}'i",
        )
    with f2:
        _tile(
            "Geçmiş Ort. Önerilen (Taahhüt)",
            f"{forecast['gecmis_ort_taahhut_sp']:.0f} SP",
            caption=f"Son {forecast['lookback_ay_sayisi']} ayın ortalaması",
            accent=CATEGORICAL["aqua"],
        )
    with f3:
        if forecast["hedef_ay_devam_ediyor"]:
            kapasite_caption = (
                f"Son {forecast['lookback_ay_sayisi']} TAMAMLANMIŞ ayın gerçekleşen SP "
                f"ortalaması ({forecast['hedef_ay']} henüz devam ettiği için hesaba katılmadı)"
            )
        else:
            kapasite_caption = "Hedef ay dahil son ayların gerçekleşen SP ortalamasına dayanır"
        _tile(
            "Gelecek Ay Önerilen Kapasite",
            f"{forecast['gelecek_ay_onerilen_kapasite_sp']:.0f} SP",
            caption=kapasite_caption,
            accent=CATEGORICAL["yellow"],
        )
    if forecast["hedef_ay_devam_ediyor"]:
        st.caption(
            f"ℹ️ {forecast['hedef_ay']} veride bulunan en güncel ay olduğu için henüz "
            "tamamlanmamış kabul edildi; bu ayın eksik/düşük 'gerçekleşen SP'si gelecek ay "
            "kapasite önerisini yapay şekilde düşürmesin diye hesaba katılmadı."
        )
    if not forecast["aylik_gecmis_tablo"].empty:
        with st.expander("Aylık geçmiş (taahhüt/gerçekleşen SP)"):
            st.dataframe(forecast["aylik_gecmis_tablo"], width="stretch", hide_index=True)


# Bir ayda kart acan farkli kisi sayisi bu esigin ALTINDAYSA (orn. sidebar'dan
# belirli bir kisi secilmisse kisi_sayisi=1 olur), "Yük Yoğunlaşma Oranı" o ay
# icin NaN yapilir - `_calculate_assignee_bouncing`'in top_n=max(1, round(n*0.2))
# formulu kucuk n'lerde (ozellikle n=1'de) oranin her zaman/neredeyse her zaman
# %100'e yapismasina (istatistiksel olarak anlamsiz bir "sinyale") yol aciyor.
# Gercek veride (bkz. dogrulama) tum-ekip gorunumunde aylik kisi sayisi hic 5'in
# altina dusmuyor (min. 6); esik sadece tek-kisi/az-kisi filtrelenmis gorunumlerde
# devreye giriyor.
BOUNCING_MIN_PERSON_COUNT = 5


def _build_bottleneck_monthly_trend(df: pd.DataFrame, months: list[str]) -> pd.DataFrame:
    """`months` listesindeki (kronolojik) her ay icin `analyze_advanced_bottlenecks`'i
    ayri ayri calistirip WIP/Blocker/Reopen/Bouncing metriklerini tek bir
    DataFrame'de (satir=ay) toplar - "Aylık Trend" grafiginde kullanilir. Ay sayisi
    genelde kucuk oldugundan (tipik olarak birkac ay - birkac yil) performans
    sorunu beklenmedigi icin cache'siz birakildi (bkz. kullanici talebi).

    Genel "Aktif İş Sayısı"/"Ortalama Yaş" ozetinin yaninda, WIP Aging'in ASIL 3
    kovasinin (bkz. `WIP_BUCKET_KEY_TO_LABEL`) her ay icin is sayisi VE SP'si de
    kolonlara eklenir - bu sayede "WIP Aging" bolumunde (sayfanin ustunde) tek bir
    ay icin gorulen kova kirilimi, burada aylar arasi trend olarak da izlenebilir."""
    rows = []
    for ay in months:
        adv = analyze_advanced_bottlenecks(df, target_month=ay)
        wip = adv["1_wip_aging"]
        bouncing = adv["3_assignee_bouncing"]
        kisi_sayisi = bouncing["kisi_sayisi"]
        yogunlasma = (
            bouncing["yogunlasma_orani_yuzde"] if kisi_sayisi >= BOUNCING_MIN_PERSON_COUNT else float("nan")
        )
        row = {
            "Ay": ay,
            "Aktif İş Sayısı": wip["aktif_is_sayisi"],
            "Ortalama Yaş (gün)": wip["ortalama_yas_gun"],
            "Blocker & Hold Oranı (%)": adv["2_blocker_hold"]["tikali_is_orani_yuzde"],
            "Reopen Oranı (%)": adv["4_reopen_rate"]["reopen_orani_yuzde"],
            "Yük Yoğunlaşma Oranı (%)": yogunlasma,
            "Aktif Kişi Sayısı": kisi_sayisi,
        }
        for bucket_key, label in WIP_BUCKET_KEY_TO_LABEL.items():
            row[label] = wip[bucket_key]["is_sayisi"]
            row[f"{label} (SP)"] = wip[bucket_key]["toplam_sp"]
        rows.append(row)
    return pd.DataFrame(rows)


def _render_breakdown_charts(gorev_listesi: pd.DataFrame) -> None:
    """`get_assignee_deep_dive`/`get_topic_deep_dive` ciktisindaki `gorev_listesi`
    tablosu icin, Statü ve Talep Tipi dagilimini gosteren iki kucuk yatay bar
    grafigi cizer - kisi/konu drill-down bolumlerinde ortak kullanilir."""
    if gorev_listesi.empty:
        return

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**Statü Dağılımı**{_info_icon(KPI_HELP['kisi_durum_dagilimi'])}", unsafe_allow_html=True)
        statu_counts = gorev_listesi["Statü"].value_counts().sort_values()
        fig = go.Figure()
        fig.add_bar(
            x=statu_counts.to_numpy(),
            y=statu_counts.index,
            orientation="h",
            marker_color=CATEGORICAL["blue"],
            text=statu_counts.to_numpy(),
            textposition="outside",
            hovertemplate="%{y}: %{x} iş<extra></extra>",
        )
        _apply_chart_chrome(fig, height=max(220, 28 * len(statu_counts)), yaxis_title=None)
        fig.update_xaxes(gridcolor=GRID_COLOR, title="İş Sayısı")
        fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch", theme="streamlit")
    with col_b:
        st.markdown(f"**Talep Tipi Dağılımı**{_info_icon(KPI_HELP['kisi_talep_tipi_dagilimi'])}", unsafe_allow_html=True)
        tip_counts = gorev_listesi["Talep Tipi"].value_counts().sort_values()
        fig2 = go.Figure()
        fig2.add_bar(
            x=tip_counts.to_numpy(),
            y=tip_counts.index,
            orientation="h",
            marker_color=CATEGORICAL["orange"],
            text=tip_counts.to_numpy(),
            textposition="outside",
            hovertemplate="%{y}: %{x} iş<extra></extra>",
        )
        _apply_chart_chrome(fig2, height=max(220, 28 * len(tip_counts)), yaxis_title=None)
        fig2.update_xaxes(gridcolor=GRID_COLOR, title="İş Sayısı")
        fig2.update_yaxes(gridcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig2, width="stretch", theme="streamlit")


ROLE_OPTIONS = ["Assignee", "Developer", "Analyst", "Hepsi"]


def _metrics_by_role(df: pd.DataFrame, role: str, target_month: str | None) -> pd.DataFrame:
    """`calculate_assignee_metrics`'i, sidebar'daki "Kişi" filtresinin aksine, seçili
    `role`'e göre "assignee" yerine "developers"/"analysts" (ya da ikisinin BİRLİKTE,
    "Hepsi" seçeneğinde) kolonlarından üretilen kişi listesine göre çalıştırır -
    `explode_by_role` ile "patlatılmış" veri, geçici olarak "assignee" ismiyle
    beslenir (`calculate_assignee_metrics`'in kendisi değiştirilmez, "assignee"
    kolonuna göre groupby yapmaya devam eder - böylece tek bir yerden hem normal hem
    rol bazlı görünüm üretilir, kod tekrarı olmaz).

    "Hepsi" seçeneğinde bir kart; atanan kişisi + tüm developer'ları + tüm
    analistleri BİRLİKTE (aynı karttaki aynı kişi bir kez sayılacak şekilde,
    `drop_duplicates` ile) kapsar - yani karta emeği geçen HERKESİN iş yüküne o kart
    TAM olarak sayılır.

    "Developer"/"Analyst" seçeneklerinde `fallback_to_assignee=False` kullanılır -
    yani o role KİMSE atanmamış kartlar o görünüme HİÇ girmez. `explode_by_role`'un
    varsayılan `fallback_to_assignee=True` davranışını burada kullanmak YANLIŞ
    sonuç verir: gerçek veride kartların yaklaşık yarısında Developer, dörtte
    birinde Analist alanı boş olduğundan, fallback açıkken bu kartlar sessizce
    "assignee"ye düşer ve "Developer"/"Analyst" görünümü aslında büyük ölçüde
    "Assignee" görünümüyle aynı (ve kişi başına yanlış/şişirilmiş) sayılar
    gösterirdi - o rolde gerçekten kimin çalıştığını YANSITMAZDI.
    """
    if role == "Assignee":
        return calculate_assignee_metrics(df, target_month=target_month)

    if role == "Hepsi":
        combined = pd.concat(
            [
                df.assign(person=df["assignee"]),
                explode_by_role(df, "developers", fallback_to_assignee=False),
                explode_by_role(df, "analysts", fallback_to_assignee=False),
            ],
            ignore_index=True,
        )
    else:
        role_column = "developers" if role == "Developer" else "analysts"
        combined = explode_by_role(df, role_column, fallback_to_assignee=False)

    combined = combined.loc[combined["person"].astype(str).str.strip() != ""]
    role_scoped = combined.drop(
        columns=["assignee", "developers", "analysts"], errors="ignore"
    ).rename(columns={"person": "assignee"})

    # Düz `drop_duplicates()` burada KULLANILAMAZ: standardize edilmiş veride
    # `sprint`/`sprint_months` liste tutar ve pandas listeyi hash'leyemez
    # ("TypeError: unhashable type: 'list'"). `drop_duplicate_rows` bu kolonları
    # anahtardan çıkarır ama SİLMEZ - `sprint_months` silinseydi aşağıdaki
    # `calculate_assignee_metrics -> filter_by_month` zinciri ay atamasını sprint
    # yerine sessizce `created` tarihinden yapardı.
    role_scoped = drop_duplicate_rows(role_scoped).reset_index(drop=True)
    return calculate_assignee_metrics(role_scoped, target_month=target_month)


# --------------------------------------------------------------------------
# Veri yukleme (cache'li) - dashboard.py ile ayni sozlesme
# --------------------------------------------------------------------------


@st.cache_data(show_spinner="Rapor okunuyor ve işleniyor...")
def _load_and_standardize(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    suffix = Path(file_name).suffix or ".html"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)
    try:
        raw_df = read_sprint_report(tmp_path)
        return standardize_dataframe(raw_df)
    finally:
        tmp_path.unlink(missing_ok=True)


def _persist_uploaded_file(file_bytes: bytes, file_name: str) -> str:
    """Cekilen Jira verisini, MCP tabanli Akıllı Asistan'in (mcp_server.py alt sureci
    kendi surecinde dosyayi diskten okur) erisebilmesi icin OTURUM BOYUNCA
    diskte kalan bir gecici dosyaya yazar. Ayni dosya zaten yazilmissa (rerun'lar
    arasinda) tekrar yazmaz; farkli bir dosya yuklendiyse eskisini siler."""
    upload_key = (file_name, sha256(file_bytes).hexdigest())
    if st.session_state.get("_uploaded_file_key") == upload_key:
        return st.session_state["_uploaded_file_path"]

    old_path = st.session_state.get("_uploaded_file_path")
    if old_path:
        Path(old_path).unlink(missing_ok=True)

    suffix = Path(file_name).suffix or ".html"
    tmp_dir = Path(tempfile.gettempdir()) / "jira_mcp_dashboard"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"{uuid4().hex}{suffix}"
    tmp_path.write_bytes(file_bytes)

    st.session_state["_uploaded_file_key"] = upload_key
    st.session_state["_uploaded_file_path"] = str(tmp_path)
    return str(tmp_path)


@st.cache_data(show_spinner="Excel raporu oluşturuluyor...")
def _generate_excel_report(file_bytes: bytes, file_name: str, target_month: str | None) -> dict:
    """Excel dosyasini uretir VE ekranda onizleme icin kullanilacak planlanan/plan
    disi is tablolarini (create_excel_report'un da kullandigi ayni veri, Excel'deki
    GIBI AYRI iki tablo halinde - TEK bir "Kapsam" kolonuyla birlestirilmemis) tek
    bir cagride doner - boylece ayni sprint raporu iki kez (bir excel icin, bir de
    onizleme icin) islenmez.

    Onizleme tablolarinin (`*_preview_df`, ekranda Talep Tipi/İş Listesi/Büyüklük/
    Statü kolonlariyla gosterilir) YANINDA, "Seçili Satırları İndir" akisinin
    (Jira toplu ice aktarim CSV'si) ihtiyac duydugu `assignee` dahil ham kolonlu
    (`issue_type`/`summary`/`assignee`/`estimate`) birer `*_export_df` de uretilir.
    Bu ikisi (`*_preview_df` ve `*_export_df`), AYNI filtrelenmis alt kumeden
    (`filter_by_month` + `filter_planned_issues`/`filter_out_of_plan_issues` -
    `build_planned_issues_table`/`build_out_of_plan_issues_table`'in iceride zaten
    yaptigi filtrelemenin BIREBIR AYNISI) AYNI SIRAYLA turetildigi icin satir
    sirasi/sayisi HER ZAMAN birebir eslesir - Streamlit'in on_select'ten donen
    pozisyonel row index'leri bu yuzden guvenle `*_export_df`'e de uygulanabilir.
    """
    suffix = Path(file_name).suffix or ".html"
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        upload_path = tmp_dir_path / f"upload{suffix}"
        upload_path.write_bytes(file_bytes)

        result = process_sprint_report(upload_path, target_month=target_month)

        output_path = tmp_dir_path / "sprint_raporu.xlsx"
        create_excel_report(result, output_path, target_month=result["target_month"])
        excel_bytes = output_path.read_bytes()

    month_df = filter_by_month(result["data"], result["target_month"])
    planned = filter_planned_issues(month_df)
    out_of_plan = filter_out_of_plan_issues(month_df)
    export_columns = ["issue_type", "summary", "assignee", "estimate"]

    return {
        "excel_bytes": excel_bytes,
        "planned_preview_df": result["planned_issues"],
        "out_of_plan_preview_df": result["out_of_plan_issues"],
        "planned_export_df": planned[export_columns],
        "out_of_plan_export_df": out_of_plan[export_columns],
    }


def _render_share_block(doc: ShareDocument, dosya_koku: str) -> None:
    """Bir sayfanın altına standart "Paylaş" bölümünü çizer: e-postaya
    yapıştırılabilir HTML indirme, düz metin indirme ve ekranda kopyalanabilir
    metin.

    TÜM sayfalarda aynı bileşen kullanılır; böylece kaçış (escape), tablo kırpma
    ve e-posta uyumluluk kuralları tek yerde (`src/share_report.py`) tanımlı
    kalır - sayfa başına ayrı bir HTML üreticisi yazılsaydı biri güncellenip
    diğerleri unutulduğunda sayfalar sessizce farklı davranırdı.

    `dosya_koku` indirilen dosyaların adında kullanılır; boşluklar ve Türkçe
    karakterler dosya adı güvenli hale getirilir.
    """
    metin = render_share_text(doc)
    html_govde = render_share_html(doc)

    guvenli = (
        dosya_koku.translate(str.maketrans("çğıöşüÇĞİÖŞÜ ", "cgiosuCGIOSU_"))
        .replace("/", "-")
        .replace("\\", "-")
    )

    st.divider()
    _section("Paylaş", "E-postaya yapıştırılabilir tek parça HTML ya da düz metin")
    html_col, metin_col = st.columns(2)
    with html_col:
        st.download_button(
            "⬇️ HTML olarak indir",
            data=html_govde.encode("utf-8"),
            file_name=f"{guvenli}.html",
            mime="text/html",
            width="stretch",
            key=f"share_html_{guvenli}",
        )
    with metin_col:
        st.download_button(
            "⬇️ Düz metin olarak indir",
            data=metin.encode("utf-8"),
            file_name=f"{guvenli}.txt",
            mime="text/plain",
            width="stretch",
            key=f"share_txt_{guvenli}",
        )
    with st.expander("Metni ekranda göster (kopyalamak için)"):
        st.code(metin, language=None)


def _share_table(df: pd.DataFrame | None, index_adi: str | None = None) -> pd.DataFrame | None:
    """Ekrandaki bir tabloyu paylaşıma uygun hale getirir.

    Panelde `hide_index=True` ile gösterilen tablolarda indeks zaten taşınmaz;
    ancak `compare_yearly_sprints` gibi metriklerin İNDEKSTE tutulduğu
    tablolarda indeks atılırsa satırların ne anlama geldiği kaybolur - bu yüzden
    `index_adi` verildiğinde indeks normal bir kolona çevrilir."""
    if df is None or df.empty:
        return None
    if index_adi:
        return df.reset_index().rename(columns={df.index.name or "index": index_adi})
    return df


def _scope_suffix() -> str:
    """Paylaşılan belgenin alt başlığında kullanılacak kapsam metni - hangi
    iterasyon/kişi/proje filtresiyle üretildiği çıktıdan anlaşılsın diye."""
    parcalar = [selected_scope_label or "Tüm İterasyonlar"]
    if selected_assignee:
        parcalar.append(f"Kişi: {selected_assignee}")
    if selected_project:
        parcalar.append(f"Proje: {selected_project}")
    return "  •  ".join(parcalar)


@st.cache_data(show_spinner="Haftalık özet hazırlanıyor...")
def _generate_weekly_report(
    file_bytes: bytes, file_name: str, week_start: pd.Timestamp
) -> dict:
    """Secili hafta icin kural tabanli sprint ici performans ozetini uretir.

    Bulgular ve iki ayri gosterim (duz metin / e-postalanabilir HTML) TEK bir
    `analyze_week` cagrisindan turetilir; boylece ekranda gorulen, indirilen
    metin ve HTML her zaman AYNI olcumu anlatir.

    `@st.cache_data`, dosya + hafta ikilisi degismedigi surece yeniden hesaplama
    yapmaz. `WeeklyFinding` nesneleri dondugu icin cagiran taraf severity'ye gore
    renklendirme yapabilir.
    """
    result = analyze_week(_load_and_standardize(file_bytes, file_name), week_start=week_start)
    return {
        "bulgular": result["bulgular"],
        "metrikler": result["metrikler"],
        "veri_notu": result["veri_notu"],
        # Paylasim belgesi burada uretilir; ekrandaki kartlar, indirilen HTML ve
        # metin boylece TEK bir olcumden turer.
        "belge": build_weekly_share_document(result),
    }


@st.cache_data(show_spinner="PDF raporu oluşturuluyor...")
def _generate_pdf_report(file_bytes: bytes, file_name: str, target_month: str | None) -> bytes:
    """Yazdirmaya/paylasmaya hazir PDF raporunu uretir (bkz. `pdf_reporter`).

    `_generate_excel_report` ile AYNI `process_sprint_report` ciktisindan
    beslenir - iki rapordaki sayilar bu yuzden her zaman birebir tutarlidir.
    `@st.cache_data` sayesinde ayni dosya/ay icin tekrar tekrar uretilmez.
    """
    suffix = Path(file_name).suffix or ".html"
    with tempfile.TemporaryDirectory() as tmp_dir:
        upload_path = Path(tmp_dir) / f"upload{suffix}"
        upload_path.write_bytes(file_bytes)
        result = process_sprint_report(upload_path, target_month=target_month)

    # Rapor basligindaki proje adi, veride EN COK gecen proje adindan turetilir
    # (tek projelik bir cekimde zaten tek deger vardir).
    project_label = None
    projects = result["data"]["project"].astype(str).str.strip()
    projects = projects[projects != ""]
    if not projects.empty:
        project_label = projects.mode().iloc[0]

    return create_pdf_report(
        result, target_month=result["target_month"], project_label=project_label
    )


def _selected_rows_to_jira_import_csv_bytes(df: pd.DataFrame) -> bytes:
    """Secili satirlari (`issue_type`/`summary`/`assignee`/`estimate` kolonlu),
    Jira'nin toplu ice aktarim sablonuyla AYNI kolon sirasi/basliklariyla bir
    CSV'ye donusturur: "Issue Type", "Summary", "Component/s", "Description",
    "Reporter", "Priority", "Label", "Assignee", "Sprint", "Story Points".
    `Component/s`/`Description`/`Reporter`/`Priority`/`Label`/`Sprint` sablonda
    da hep bos oldugu icin BOS BIRAKILIR. UTF-8 BOM (`utf-8-sig`) ile yazilir -
    Turkce karakterlerin Excel'de bozulmamasi icin."""
    export_df = pd.DataFrame(
        {
            "Issue Type": df["issue_type"].to_numpy(),
            "Summary": df["summary"].to_numpy(),
            "Component/s": "",
            "Description": "",
            "Reporter": "",
            "Priority": "",
            "Label": "",
            "Assignee": df["assignee"].to_numpy(),
            "Sprint": "",
            "Story Points": df["estimate"].to_numpy(),
        }
    )
    buffer = BytesIO()
    export_df.to_csv(buffer, index=False, encoding="utf-8-sig")
    return buffer.getvalue()


# --------------------------------------------------------------------------
# Jira'ya .env uzerinden otomatik baglanti
# --------------------------------------------------------------------------

def _call_jira_api_with_ssl_retry(func, *args, skip_ssl: bool, **kwargs):
    """Bir Jira API cagrisini ONCE `verify=True` (guvenli varsayilan) ile dener;
    `JiraSslError` alirsa VE kullanici "SSL doğrulamayı atla" kutusunu
    isaretlemisse `verify=False` ile SESSIZCE bir kez daha dener - boylece kutuyu
    onceden isaretleyen kullanici tek tikla sorunsuz baglanir, isaretlemeyenler
    ise varsayilan olarak GUVENLI (sertifika dogrulamali) baglantidan
    vazgecirilmez. Kutu isaretli DEGILSE (veya yine de basarisiz olursa) hata
    oldugu gibi yukselir - cagiran kod `JiraApiError`'u yakalayip `st.error`
    ile gostermelidir."""
    try:
        return func(*args, verify=True, **kwargs)
    except JiraSslError:
        if not skip_ssl:
            raise
        return func(*args, verify=False, **kwargs)


def _jira_config_signature(cfg) -> tuple:
    """Identify the .env source without keeping its token in session state."""
    return (
        cfg.base_url,
        cfg.project_key,
        cfg.months_back,
        cfg.skip_ssl,
        tuple(sorted(cfg.field_id_map.items())),
        sha256(cfg.token.encode("utf-8")).hexdigest(),
    )


def _fetch_env_jira_data(cfg) -> tuple[bytes | None, str | None, str | None]:
    """Fetch once per source; discard old-project data before any new request."""
    signature = _jira_config_signature(cfg)
    data_keys = (
        "_jira_fetched_bytes", "_jira_fetched_name", "_jira_fetch_error",
        "_jira_fetch_attempted", "_jira_ignored_person_fields", "_jira_fetched_rows",
        "chat_history", "excel_bytes", "excel_planned_df", "excel_out_of_plan_df",
        "excel_planned_export_df", "excel_out_of_plan_export_df", "excel_filename",
        "pdf_bytes", "pdf_filename",
    )
    if st.session_state.get("_jira_source_signature") != signature:
        for key in data_keys:
            st.session_state.pop(key, None)
        st.session_state["_jira_source_signature"] = signature

    if not cfg.can_fetch_directly:
        return None, None, "Jira baglantisi icin .env dosyasinda URL, PAT, proje ve Story Points alani gerekli."

    if st.session_state.get("_jira_fetch_attempted") != signature:
        st.session_state["_jira_fetch_attempted"] = signature
        with st.spinner("Jira'dan veriler cekiliyor..."):
            try:
                full_df = _call_jira_api_with_ssl_retry(
                    fetch_issues_from_jira_api,
                    cfg.base_url,
                    cfg.token,
                    cfg.project_key,
                    cfg.field_id_map,
                    months_back=cfg.months_back,
                    skip_ssl=cfg.skip_ssl,
                )
            except JiraApiError as exc:
                st.session_state["_jira_fetch_error"] = str(exc)
            else:
                if full_df.empty:
                    st.session_state["_jira_fetch_error"] = "Secilen donemde Jira karti bulunamadi."
                else:
                    st.session_state["_jira_fetched_bytes"] = full_df.to_csv(index=False).encode("utf-8-sig")
                    st.session_state["_jira_fetched_name"] = f"jira_{cfg.project_key.lower()}_canli_veri.csv"
                    st.session_state["_jira_fetched_rows"] = len(full_df)
                    st.session_state["_jira_ignored_person_fields"] = full_df.attrs.get(
                        "ignored_text_person_fields", []
                    )

    return (
        st.session_state.get("_jira_fetched_bytes"),
        st.session_state.get("_jira_fetched_name"),
        st.session_state.get("_jira_fetch_error"),
    )


# --------------------------------------------------------------------------
# .env baglantisi, gezinme ve filtreler
# --------------------------------------------------------------------------

cfg = load_jira_config()
with st.sidebar:
    if LOGO_FULL_PATH.exists():
        st.image(str(LOGO_FULL_PATH), width=200)
    st.header("📊 Kontrol Paneli")
    with st.expander("⚙️ Jira Ayarları", expanded=False):
        st.caption(f"Kaynak: `.env` · Proje: **{cfg.project_key or '—'}** · Kapsam: son {cfg.months_back} ay")
        with st.form("jira_env_settings_form", clear_on_submit=True):
            jira_url = st.text_input("Jira URL", value=cfg.base_url)
            jira_project = st.text_input("Proje anahtarı", value=cfg.project_key)
            jira_token = st.text_input(
                "Yeni PAT (boş bırakılırsa mevcut korunur)", type="password", value=""
            )
            jira_months = st.number_input("Ay kapsamı", min_value=1, max_value=36, value=cfg.months_back)
            story_field = st.text_input("Story Points alan ID", value=cfg.field_id_map.get("story_points") or "")
            developer_field = st.text_input("Developer alan ID", value=cfg.field_id_map.get("developer") or "")
            analyst_field = st.text_input("Analyst alan ID", value=cfg.field_id_map.get("analyst") or "")
            sprint_field = st.text_input("Sprint alan ID (isteğe bağlı)", value=cfg.field_id_map.get("sprint") or "")
            transition_field = st.text_input(
                "Last Transition alan ID (isteğe bağlı)", value=cfg.field_id_map.get("last_transition") or ""
            )
            skip_ssl = st.checkbox("SSL doğrulamasını atla", value=cfg.skip_ssl)
            sprint_fallback = st.checkbox("SprintDışı tarih yedeği", value=cfg.sprint_disi_fallback_enabled)
            save_settings = st.form_submit_button("Ayarları kaydet")
        settings_error = st.empty()
        settings_warning = st.empty()

if save_settings:
    updates = {
        "JIRA_BASE_URL": jira_url,
        "JIRA_PROJECT_KEY": jira_project,
        "JIRA_MONTHS_BACK": str(jira_months),
        "JIRA_FIELD_STORY_POINTS": story_field,
        "JIRA_FIELD_DEVELOPER": developer_field,
        "JIRA_FIELD_ANALYST": analyst_field,
        "JIRA_FIELD_SPRINT": sprint_field,
        "JIRA_FIELD_LAST_TRANSITION": transition_field,
        "JIRA_SKIP_SSL": str(skip_ssl).lower(),
        "SPRINT_DISI_FALLBACK_ENABLED": str(sprint_fallback).lower(),
    }
    if jira_token:
        updates["JIRA_PAT"] = jira_token
    try:
        if not (jira_token or cfg.token):
            raise ValueError("Jira PAT gerekli.")
        save_jira_settings(updates)
    except (ValueError, OSError) as exc:
        settings_error.error(str(exc))
    else:
        st.session_state.pop("_jira_source_signature", None)
        st.rerun()

file_bytes, file_name, jira_error = _fetch_env_jira_data(cfg)
ignored_fields = st.session_state.get("_jira_ignored_person_fields", [])
if ignored_fields:
    settings_warning.warning("Düz metin döndüren kişi alanları ekip hesabına alınmadı: " + ", ".join(ignored_fields))
if jira_error:
    st.error(jira_error)
    st.stop()
if file_bytes is None or file_name is None:
    st.error("Jira verisi alınamadı. `.env` bağlantı ayarlarını kontrol edin.")
    st.stop()

# .streamlit/config.toml'daki [server] maxUploadSize (50 MB) ile tutarli bir ust
# sinir - Streamlit'in kendi limiti asilirsa zaten yukleme basarisiz olur, ama
# bu kontrol dosya islenmeye baslamadan ONCE anlasilir bir Turkce hata gosterir.
MAX_UPLOAD_SIZE_BYTES = 52428800  # 50 MB
if len(file_bytes) > MAX_UPLOAD_SIZE_BYTES:
    boyut_mb = len(file_bytes) / (1024 * 1024)
    st.error(f"Jira verisi çok büyük ({boyut_mb:.1f} MB); 50 MB sınırı aşıldı.")
    st.stop()

try:
    df = _load_and_standardize(file_bytes, file_name)
    report_file_path = _persist_uploaded_file(file_bytes, file_name)
except Exception as exc:  # noqa: BLE001 - kullaniciya anlasilir hata gostermek icin genis yakalama
    st.error(f"Rapor işlenirken bir hata oluştu: {exc}")
    st.stop()

with st.sidebar:
    st.divider()
    page = st.radio("Sayfa", NAV_PAGES, index=0, label_visibility="collapsed")

    st.divider()
    st.caption("FİLTRELER")

    # Ay listesi kartların SPRINT alanından türetilir (sprint adı çözülemeyen
    # kartlarda `created` tarihine düşülür) - bkz. processor._row_month_keys.
    # Kullanıcıya "Temmuz 2026" yerine Jira'da GÖRDÜĞÜ sprint adı gösterilir;
    # filtreye giden değer yine ay etiketidir, böylece tüm alt ekranlar (KPI'lar,
    # tablolar, trend, rapor indirme) değişmeden çalışır.
    all_months = [label for label, _ in build_monthly_history(df, last_n_months=None)]
    sprint_names_by_month = build_month_sprint_labels(df)

    def _scope_label(month: str) -> str:
        names = sprint_names_by_month.get(month, [])
        if not names:
            # Bu aya sadece `created` tarihinden düşen kartlarla ulaşıldı -
            # kullanıcının sprint adı beklediği yerde onu yanıltmamak için açıkça yazılır.
            return f"{month} (sprintsiz)"
        if len(names) == 1:
            return names[0]
        return f"{month} ({len(names)} sprint)"

    month_by_scope_label: dict[str, str] = {}
    for month in reversed(all_months):  # en güncel ay en üstte
        month_by_scope_label.setdefault(_scope_label(month), month)

    scope_options = list(month_by_scope_label)
    if scope_options:
        selected_scope_label: str | None = st.selectbox("İterasyon / Sprint", scope_options, index=0)
        selected_month: str | None = month_by_scope_label[selected_scope_label]
        if selected_scope_label != selected_month:
            st.caption(f"Ay karşılığı: {selected_month}")
    else:
        selected_scope_label = None
        selected_month = None
        st.warning(
            "Veride ne 'Sprint' alanı ne de 'Created' tarihi bulunabildi; "
            "iterasyon filtresi uygulanamıyor."
        )

    # "Kişi" filtresi, sadece atanan (assignee) değil, kartların Developer/Analist
    # alanlarında geçen herkesi de kapsar - bkz. `standardize_dataframe`'in
    # "developers"/"analysts" (coklu-degerli, liste) kolonlari.
    team_scope = filter_by_month(df, selected_month) if selected_month else df
    person_pool = {a for a in team_scope["assignee"].astype(str).str.strip() if a}
    for role_column in ("developers", "analysts"):
        for values in team_scope[role_column]:
            person_pool.update(str(v).strip() for v in values if str(v).strip())
    assignee_options = ["Tüm Ekip"] + sorted(person_pool)
    selected_assignee_label = st.selectbox("Kişi", assignee_options, index=0)
    selected_assignee: str | None = (
        None if selected_assignee_label == "Tüm Ekip" else selected_assignee_label
    )

    project_options = ["Tüm Projeler"] + sorted(
        {p for p in df["project"].astype(str).str.strip() if p}
    )
    if len(project_options) > 1:
        selected_project_label = st.selectbox("Proje", project_options, index=0)
        selected_project: str | None = (
            None if selected_project_label == "Tüm Projeler" else selected_project_label
        )
    else:
        selected_project = None

    st.divider()
    st.metric("Toplam Kart Sayısı", len(df))
    st.caption(f"Kapsam: {selected_scope_label or 'Tüm İterasyonlar'} · {selected_assignee_label}")

# Sidebar'daki secimlere gore aktif kapsam. "Kişi" filtresi assignee_options'daki
# genisletilmis listeyle (assignee+developers+analysts) tutarli olmasi icin
# filter_by_person kullanir - boylece sadece Developer/Analist olarak atanmis
# (hic assignee olmamis) bir kisi secildiginde de kartlari bulunur (filter_by_
# assignee ile bu durumda 0 kart donerdi, cunku sadece assignee kolonuna bakardi).
df_scope = filter_by_person(df, selected_assignee) if selected_assignee else df
df_scope = filter_by_project(df_scope, selected_project) if selected_project else df_scope
kpi_df = filter_by_month(df_scope, selected_month) if selected_month else df_scope
kpis = calculate_sprint_kpis(kpi_df)

st.title("Türkcell Jira Sprint & KPI Paneli")
st.caption(
    "Jira verisi .env bağlantısıyla otomatik çekilir; KPI'lar, drill-down analizler ve akıllı sohbet asistanı "
    "soldaki menüden erişilebilir sayfalar halinde hazırlanır."
)

hero_cols = st.columns(4)
with hero_cols[0]:
    _tile("Toplam Kart", f"{len(df):,}")
with hero_cols[1]:
    _tile(
        "Kartlarda Görülen Kişi",
        str(len(assignee_options) - 1),
        help_text="Seçili sprintteki Assignee, Developer ve Analyst alanlarında adı geçen tekil kişilerdir; ekip kadrosundaki kartı olmayan kişiler dahil değildir.",
    )
with hero_cols[2]:
    _tile("Ay Sayısı", str(len(all_months)))
with hero_cols[3]:
    _tile("Aktif Kapsam", selected_scope_label or "Tüm İterasyonlar", accent=CATEGORICAL["orange"])

st.write("")

# --------------------------------------------------------------------------
# Sayfa: Genel Bakış
# --------------------------------------------------------------------------

if page == PAGE_GENEL:
    arama_sorgusu = st.text_input("🔍 Kart ara (talep tipi/özet/sorumlu/statü/etiket)")
    if arama_sorgusu:
        arama_sonucu = search_issues_by_query(df_scope, arama_sorgusu, target_month=selected_month)
        if not arama_sonucu.empty:
            st.dataframe(arama_sonucu, width="stretch", hide_index=True)

    _section(
        "Kapasite ve İş Yükü Tahmini",
        "Geçmiş ay ortalamasına göre bu ayın kıyası ve gelecek ay için önerilen kapasite",
        help_text=KPI_HELP["capacity_forecast"],
    )
    with st.container(border=True):
        window_col, _ = st.columns([1, 3])
        with window_col:
            lookback_months = st.selectbox(
                "Ortalama Pencere (ay)", [1, 2, 3, 4, 5, 6], index=2, key="capacity_lookback"
            )
        split_forecast = calculate_capacity_forecast_split(
            df_scope, target_month=selected_month, lookback_months=lookback_months,
            require_sprint_membership=True,
        )
        st.markdown("**Sprint (Planlanan) Tahmini**", unsafe_allow_html=True)
        st.caption("Taahhüt: seçili sprintteki, iptal edilmemiş planlanan kartların SP toplamı.")
        _render_capacity_forecast_group(split_forecast["planned_forecast"])
        st.write("")
        st.markdown("**Sprint Dışı Tahmini**", unsafe_allow_html=True)
        _render_capacity_forecast_group(split_forecast["out_of_plan_forecast"])

    st.divider()
    _section(
        "Yönetici Özeti",
        "Seçili kapsama ait üst düzey KPI'lar ve durum rozetleri",
        help_text=KPI_HELP["yonetici_ozeti"],
    )

    completion_tier = _status_tier(kpis.completion_rate, good_cut=85, warn_cut=60)
    out_of_plan_tier = _status_tier(kpis.out_of_plan_rate, good_cut=15, warn_cut=30, higher_is_better=False)

    # Onceki aya gore delta: secili ay all_months (kronolojik) icinde bulunabiliyorsa
    # ve ilk ay degilse hesaplanir; ilk ay seciliyse veya ay eslesmezse sessizce
    # atlanir (hata firlatilmaz, tile'lar sadece delta caption'i olmadan gosterilir).
    onceki_ay_kpis = None
    if selected_month and selected_month in all_months:
        onceki_ay_index = all_months.index(selected_month) - 1
        if onceki_ay_index >= 0:
            onceki_ay = all_months[onceki_ay_index]
            onceki_ay_kpis = calculate_sprint_kpis(filter_by_month(df_scope, onceki_ay))

    def _delta_caption(current: float, previous_attr: str, pp: bool = False) -> str | None:
        if onceki_ay_kpis is None:
            return None
        fark = current - getattr(onceki_ay_kpis, previous_attr)
        isaret = "+" if fark >= 0 else ""
        return f"Geçen aya göre {isaret}{fark:.1f}pp" if pp else f"Geçen aya göre {isaret}{fark:.0f} SP"

    m0, m1, m2, m3, m4 = st.columns(5)
    with m0:
        _tile("O Ayın Toplam Kart Sayısı", f"{len(kpi_df):,}", help_text=KPI_HELP["toplam_kart"])
    with m1:
        _tile(
            "Taahhüt Edilen SP",
            f"{kpis.committed_sp:.0f}",
            caption=_delta_caption(kpis.committed_sp, "committed_sp"),
            help_text=KPI_HELP["taahhut_sp"],
        )
    with m2:
        _tile(
            "Gerçekleşen SP",
            f"{kpis.completed_sp:.0f}",
            caption=_delta_caption(kpis.completed_sp, "completed_sp"),
            help_text=KPI_HELP["gerceklesen_sp"],
        )
    with m3:
        _tile(
            "Tamamlanma Oranı",
            f"%{kpis.completion_rate:.1f}",
            badge=_badge(completion_tier, {"good": "İyi", "warning": "Dikkat", "critical": "Kritik"}[completion_tier]),
            accent=STATUS[completion_tier],
            caption=_delta_caption(kpis.completion_rate, "completion_rate", pp=True),
            help_text=KPI_HELP["tamamlanma_orani"],
        )
    with m4:
        _tile(
            "Plan Dışı Oranı",
            f"%{kpis.out_of_plan_rate:.1f}",
            badge=_badge(out_of_plan_tier, {"good": "İyi", "warning": "Dikkat", "critical": "Kritik"}[out_of_plan_tier]),
            accent=STATUS[out_of_plan_tier],
            caption=_delta_caption(kpis.out_of_plan_rate, "out_of_plan_rate", pp=True),
            help_text=KPI_HELP["plan_disi_orani"],
        )

    st.divider()
    trend_df = compare_yearly_sprints(df_scope, target_month=selected_month)
    trend_year = trend_df.columns[0].split()[-1] if not trend_df.empty else None
    _section(
        "İterasyon Bazlı İş Büyüklüğü (SP)",
        f"{trend_year} yılı Ocak'tan seçili aya kadar olan ayların taahhüt/gerçekleşen/plan dışı dağılımı"
        if trend_year
        else "Yıllık taahhüt/gerçekleşen/plan dışı dağılımı",
        help_text=KPI_HELP["iterasyon_grafigi"],
    )
    with st.container(border=True):
        metric_rows = [m for m in ITERATION_CHART_METRICS if m in trend_df.index]
        if not metric_rows:
            st.caption("Aylık trend için 'Created' tarihi verisi bulunamadı.")
        else:
            chart_data = trend_df.loc[metric_rows]
            months = list(chart_data.columns)
            fig = go.Figure()
            for metric in metric_rows:
                values = chart_data.loc[metric]
                fig.add_bar(
                    name=metric,
                    x=months,
                    y=values,
                    marker_color=ITERATION_CHART_METRICS[metric],
                    text=[f"{v:.0f}" for v in values],
                    textposition="outside",
                    hovertemplate=f"%{{x}}<br>{metric}: %{{y:.0f}} SP<extra></extra>",
                )
            fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
            _apply_chart_chrome(fig, height=420, yaxis_title="Story Point (SP)")
            st.plotly_chart(fig, width="stretch", theme="streamlit")

    st.divider()
    _section(
        "5 Temel KPI Analizi",
        "Velocity · Scope · Workload · Flow · Estimation",
        help_text=KPI_HELP["core_5_kpi_paketi"],
    )
    kpi5 = run_core_5_kpi_analyses(
        df_scope, target_month=selected_month, assignee=selected_assignee, project=selected_project
    )

    c1, c2, c3, c4, c5 = st.columns(5)

    with c1, st.container(border=True):
        v = kpi5["1_velocity_predictability"]
        st.markdown(f"**Velocity**{_info_icon(KPI_HELP['velocity'])}", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:1.6rem;font-weight:800;color:{ACCENT};'>%{v['tamamlanma_orani_yuzde']:.1f}</div>", unsafe_allow_html=True)
        st.caption(f"Taahhüt: {v['taahhut_edilen_sp']:.0f} SP · Gerçekleşen: {v['gerceklesen_sp']:.0f} SP")

    with c2, st.container(border=True):
        s = kpi5["2_scope_stability"]
        st.markdown(f"**Scope Stability**{_info_icon(KPI_HELP['scope_stability'])}", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:1.6rem;font-weight:800;color:{ACCENT};'>%{s['scope_creep_orani_yuzde']:.1f}</div>", unsafe_allow_html=True)
        st.caption(f"Plan Dışı: {s['plan_disi_sp']:.0f} SP · Toplam Yük: {s['toplam_yuk_sp']:.0f} SP")

    with c3, st.container(border=True):
        w = kpi5["3_workload_equity"]
        if selected_assignee:
            st.markdown(f"**Workload Consistency**{_info_icon(KPI_HELP['workload_consistency'])}", unsafe_allow_html=True)
            st.markdown(f"<div style='font-size:1.6rem;font-weight:800;color:{ACCENT};'>{w['tutarlilik_katsayisi']:.2f}</div>", unsafe_allow_html=True)
            st.caption(f"Toplam Yük: {w['toplam_yuk_sp']:.0f} SP")
        else:
            riskli = w["tukenmislik_riski_tasiyanlar"]
            risk_metni = ", ".join(riskli["Sorumlu"]) if not riskli.empty else "Yok"
            st.markdown(f"**Workload Equity**{_info_icon(KPI_HELP['workload_equity'])}", unsafe_allow_html=True)
            st.markdown(f"<div style='font-size:1.6rem;font-weight:800;color:{ACCENT};'>{w['esitsizlik_katsayisi']:.2f}</div>", unsafe_allow_html=True)
            st.caption(f"Ort. Yük: {w['ortalama_yuk_sp']:.1f} SP · Risk: {risk_metni}")

    with c4, st.container(border=True):
        flow = kpi5["4_flow_efficiency"]
        st.markdown(f"**Flow & Bottlenecks**{_info_icon(KPI_HELP['flow_efficiency'])}", unsafe_allow_html=True)
        st.markdown(f"<div style='font-size:1.6rem;font-weight:800;color:{ACCENT};'>{flow['aktif_is_sayisi']}</div>", unsafe_allow_html=True)
        st.caption(f"Kritik/Tıkanmış: {len(flow['kritik_isler'])} · Toplam SP: {flow['toplam_aktif_sp']:.0f}")
        # `_clickable_tile` ile AYNI sebepten `st.button` degil `st.toggle` kullanilir -
        # bkz. o fonksiyonun docstring'i (dugme metni tiklandigi anda degil, bir
        # sonraki rerun'da guncellenirdi). `value=` verilmez (bkz. ayni docstring).
        flow_open = st.toggle("Detay", key="open_flow_bottleneck")

    with c5, st.container(border=True):
        e = kpi5["5_estimation_accuracy"]["talep_tipi_bazli_analiz"]
        st.markdown(f"**Estimation Accuracy**{_info_icon(KPI_HELP['estimation_accuracy'])}", unsafe_allow_html=True)
        if e.empty:
            st.markdown("<div style='font-size:1.6rem;font-weight:800;'>—</div>", unsafe_allow_html=True)
            st.caption("Veri Yok")
        else:
            en_sapmali = e.iloc[0]
            st.markdown(f"<div style='font-size:1.6rem;font-weight:800;color:{ACCENT};'>{en_sapmali['Talep Tipi']}</div>", unsafe_allow_html=True)
            st.caption(f"En Yüksek Sapma Oranı: %{en_sapmali['Sapma Oranı (%)']:.1f}")

    if flow_open:
        st.caption("Hangi kartlar hangi statüde/büyüklükte tıkanmış durumda:")
        kritik_isler = flow["kritik_isler"]
        if kritik_isler.empty:
            st.caption("Şu an tıkanmış/kritik büyüklükte bir iş bulunmuyor.")
        else:
            st.dataframe(kritik_isler, width="stretch", hide_index=True)

    st.divider()
    _section(
        "Talep Tipi Bazlı Tahmin Doğruluğu",
        "Tüm talep tiplerinin hedeflenen/gerçekleşen SP karşılaştırması",
        help_text=KPI_HELP["estimation_accuracy"],
    )
    estimation_df = analyze_estimation_accuracy(df_scope, target_month=selected_month)
    if estimation_df.empty:
        st.caption("Tahmin doğruluğu analizi için yeterli veri bulunamadı.")
    else:
        with st.container(border=True):
            top = estimation_df.sort_values("Sapma Oranı (%)")
            fig = go.Figure()
            fig.add_bar(
                x=top["Sapma Oranı (%)"],
                y=top["Talep Tipi"],
                orientation="h",
                marker_color=CATEGORICAL["orange"],
                text=[f"%{v:.1f}" for v in top["Sapma Oranı (%)"]],
                textposition="outside",
                hovertemplate="%{y}<br>Sapma Oranı: %{x:.1f}%<extra></extra>",
            )
            _apply_chart_chrome(fig, height=max(280, 30 * len(top)), yaxis_title=None)
            fig.update_xaxes(gridcolor=GRID_COLOR, title="Sapma Oranı (%)")
            fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch", theme="streamlit")
        st.dataframe(estimation_df, width="stretch", hide_index=True)

    _velocity = kpi5["1_velocity_predictability"]
    _scope = kpi5["2_scope_stability"]
    _flow = kpi5["4_flow_efficiency"]
    _render_share_block(
        ShareDocument(
            baslik="Sprint & KPI Genel Bakış",
            alt_baslik=_scope_suffix(),
            kutular=[
                ("Taahhüt Edilen SP", f"{kpis.committed_sp:.0f}"),
                ("Gerçekleşen SP", f"{kpis.completed_sp:.0f}"),
                ("Tamamlanma Oranı", f"%{kpis.completion_rate:.1f}"),
                ("Plan Dışı Oranı", f"%{kpis.out_of_plan_rate:.1f}"),
            ],
            bolumler=[
                ShareSection(
                    baslik="5 Temel KPI",
                    bullets=[
                        ShareBullet(
                            f"Velocity: taahhüt {_velocity['taahhut_edilen_sp']:.0f} SP, "
                            f"gerçekleşen {_velocity['gerceklesen_sp']:.0f} SP "
                            f"(tamamlanma %{_velocity['tamamlanma_orani_yuzde']:.1f})."
                        ),
                        ShareBullet(
                            f"Scope Stability: plan dışı iş toplam yükün "
                            f"%{_scope['scope_creep_orani_yuzde']:.1f} kadarı "
                            f"({_scope['plan_disi_sp']:.0f} SP)."
                        ),
                        ShareBullet(
                            f"Flow Efficiency: {_flow['aktif_is_sayisi']} aktif iş, "
                            f"toplam {_flow['toplam_aktif_sp']:.0f} SP."
                        ),
                    ],
                ),
                ShareSection(baslik="İterasyon Bazlı İş Büyüklüğü (SP)", tablo=_share_table(trend_df, index_adi="Metrik")),
                ShareSection(baslik="Talep Tipi Bazlı Tahmin Doğruluğu", tablo=estimation_df),
            ],
        ),
        f"genel_bakis_{selected_month or 'tum_aylar'}",
    )

# --------------------------------------------------------------------------
# Sayfa: Haftalık Özet
# --------------------------------------------------------------------------
# Ay/sprint KAPANIŞI raporlarının aksine bu sayfa sprint DEVAM EDERKEN gidişatı
# değerlendirir. Metni kural tabanlı üretir (LLM yok) - bkz. src/weekly_report.py.

elif page == PAGE_HAFTALIK:
    _section(
        "Haftalık Sprint İçi Performans",
        "Seçili haftada ne tamamlandı, tempo nasıl, sprint hedefine yetişiliyor mu",
    )

    try:
        haftalar = available_weeks(df_scope, last_n=WEEKLY_SELECTABLE_WEEKS)
    except WeeklyReportError as exc:
        # `last_transition` alanı yoksa/boşsa yanıltıcı bir "0 iş" raporu
        # üretmek yerine durum açıkça anlatılır.
        haftalar = []
        st.warning(str(exc))

    if haftalar:
        secili_hafta = st.selectbox(
            "Hafta",
            haftalar,
            index=0,
            format_func=week_label,
            help="Varsayılan: veride hareket bulunan en son hafta.",
        )
        rapor = _generate_weekly_report(file_bytes, file_name, pd.Timestamp(secili_hafta))
        metrikler = rapor["metrikler"]

        h1, h2, h3, h4 = st.columns(4)
        with h1:
            _tile("Tamamlanan İş", f"{metrikler.get('tamamlanan_kart', 0)}")
        with h2:
            _tile("Tamamlanan SP", f"{metrikler.get('tamamlanan_sp', 0):.0f}")
        with h3:
            _tile("Hareket Eden Kart", f"{metrikler.get('hareket_eden_kart', 0)}")
        with h4:
            _tile(
                "Sprint İlerlemesi",
                f"%{metrikler.get('sprint_ilerleme_yuzde', 0):.0f}",
                caption=metrikler.get("sprint"),
                accent=CATEGORICAL["orange"],
            )

        st.divider()
        _section("Değerlendirme", "Kural tabanlı; her bulgu tek cümlede özetlenir")
        for bulgu in rapor["bulgular"]:
            renk = WEEKLY_SEVERITY_RENK.get(bulgu.severity, ACCENT)
            st.markdown(
                f'<div style="border-left:4px solid {renk};background:{CARD_BG};'
                f'padding:10px 14px;border-radius:6px;margin-bottom:8px;">'
                f'<strong style="color:{renk};">{bulgu.baslik}</strong><br>'
                f"{weekly_finding_sentence(bulgu)}</div>",
                unsafe_allow_html=True,
            )
        st.caption(rapor["veri_notu"])

        st.divider()
        _section("Haftalık Tamamlanan SP", "Seçili hafta ve öncesindeki referans haftalar")
        gecmis = list(reversed(metrikler.get("gecmis_haftalar_sp", [])))
        seri_etiket = [
            week_label(pd.Timestamp(secili_hafta) - pd.Timedelta(7 * i, "D"))
            for i in range(len(gecmis), -1, -1)
        ]
        seri_deger = gecmis + [metrikler.get("tamamlanan_sp", 0.0)]
        fig = go.Figure()
        fig.add_bar(
            x=seri_etiket,
            y=seri_deger,
            marker_color=[CATEGORICAL["blue"]] * len(gecmis) + [CATEGORICAL["orange"]],
            text=[f"{v:.0f}" for v in seri_deger],
            textposition="outside",
            hovertemplate="%{x}<br>%{y:.0f} SP<extra></extra>",
        )
        _apply_chart_chrome(fig, height=300, yaxis_title="Tamamlanan SP")
        fig.update_xaxes(gridcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, width="stretch", theme="streamlit")

        _render_share_block(
            rapor["belge"], f"haftalik_ozet_{pd.Timestamp(secili_hafta):%Y-%m-%d}"
        )

# --------------------------------------------------------------------------
# Sayfa: Ekip & Kişiler
# --------------------------------------------------------------------------

elif page == PAGE_EKIP:
    # "Toplam İş Sayısı (Ekip)"/"En Yüklü Kişi" ozet kartlari, asagidaki "Rol"
    # secicisinden BAGIMSIZ olarak HER ZAMAN "Hepsi" (Assignee+Developer+Analist
    # birlesimi, ayni karttaki ayni kisi bir kez sayilir) gorunumunu kullanir -
    # Developer/Analist'ler herhangi bir secim yapilmadan otomatik olarak dahildir.
    metrics = _metrics_by_role(df_scope, "Hepsi", selected_month)

    et1, et2 = st.columns(2)
    with et1:
        toplam_is_open = _clickable_tile(
            "ekip_toplam_is",
            "Toplam İş Sayısı (Ekip)",
            f"{int(metrics['Toplam İş Sayısı'].sum()) if not metrics.empty else 0:,}",
            help_text=KPI_HELP["ekip_yuku"],
        )
    with et2:
        en_yuklu_kisi = metrics.iloc[0]["Sorumlu"] if not metrics.empty else None
        en_yuklu_open = _clickable_tile(
            "ekip_en_yuklu",
            "En Yüklü Kişi",
            en_yuklu_kisi or "—",
            caption=f"{metrics.iloc[0]['Toplam Yük (SP)']:.0f} SP" if not metrics.empty else None,
            help_text=KPI_HELP["ekip_yuku"],
        )

    if toplam_is_open:
        st.dataframe(metrics, width="stretch", hide_index=True)
    if en_yuklu_open and en_yuklu_kisi:
        st.dataframe(
            get_assignee_deep_dive(df_scope, en_yuklu_kisi, target_month=selected_month)["gorev_listesi"],
            width="stretch",
            hide_index=True,
        )

    st.divider()
    _section(
        "Ekip İş Yükü Dağılımı",
        "Sorumlu bazında kart adedi; seçili kişi vurgulanır, üzerine gelince SP görünür",
        help_text=KPI_HELP["ekip_yuku"],
    )
    selected_role = st.radio(
        "Rol", ROLE_OPTIONS, index=ROLE_OPTIONS.index("Hepsi"), horizontal=True, key="ekip_yuku_rol_secimi",
        help="Varsayılan 'Hepsi': Assignee+Developer+Analyst birleşimi (aynı karttaki aynı kişi bir "
        "kez sayılır) - dilersen sadece Assignee/Developer/Analyst'e göre daraltabilirsin.",
    )
    role_metrics = _metrics_by_role(df_scope, selected_role, selected_month)
    with st.container(border=True):
        if role_metrics.empty:
            st.caption("Kişi bazlı veri bulunamadı.")
        else:
            top = role_metrics.sort_values("Toplam İş Sayısı")
            bar_colors = [
                CATEGORICAL["orange"] if selected_assignee and name == selected_assignee else CATEGORICAL["blue"]
                for name in top["Sorumlu"]
            ]
            fig = go.Figure()
            fig.add_bar(
                x=top["Toplam İş Sayısı"],
                y=top["Sorumlu"],
                orientation="h",
                marker_color=bar_colors,
                customdata=top["Toplam Yük (SP)"],
                text=top["Toplam İş Sayısı"],
                textposition="outside",
                hovertemplate="%{y}<br>Kart Sayısı: %{x}<br>Toplam Yük: %{customdata:.0f} SP<extra></extra>",
            )
            _apply_chart_chrome(fig, height=max(320, 28 * len(top)), yaxis_title=None)
            fig.update_xaxes(gridcolor=GRID_COLOR, title="Toplam İş Sayısı")
            fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch", theme="streamlit")

    with st.expander(f"Kişi bazlı tablo ({selected_role})"):
        st.dataframe(role_metrics, width="stretch", hide_index=True)

    st.divider()
    _section(
        "Kişi Özelinde Detay (Drill-down)",
        "Seçili sorumlunun tüm görevleri, mevcut ve hedeflenen statüsü ve performansı",
        help_text=KPI_HELP["kisi_drill_down"],
    )
    if selected_assignee:
        detay = get_assignee_deep_dive(df, selected_assignee, target_month=selected_month)
        if not detay["bulundu"]:
            st.warning(f"'{selected_assignee}' için kart bulunamadı.")
        else:
            tier = _status_tier(detay["tamamlanma_orani_yuzde"], good_cut=85, warn_cut=60)
            d1, d2, d3, d4 = st.columns(4)
            with d1:
                _tile("Toplam İş", str(detay["toplam_is_sayisi"]))
            with d2:
                _tile("Toplam SP", f"{detay['toplam_sp']:.0f}")
            with d3:
                _tile("Tamamlanan SP", f"{detay['tamamlanan_sp']:.0f}")
            with d4:
                _tile(
                    "Tamamlanma Oranı",
                    f"%{detay['tamamlanma_orani_yuzde']:.1f}",
                    badge=_badge(tier, {"good": "İyi", "warning": "Dikkat", "critical": "Kritik"}[tier]),
                    accent=STATUS[tier],
                    help_text=KPI_HELP["tamamlanma_orani"],
                )
            st.write("")
            st.dataframe(detay["gorev_listesi"], width="stretch", hide_index=True)
            _render_breakdown_charts(detay["gorev_listesi"])
    else:
        st.info("Kişi bazlı detay görmek için soldaki menüden bir kişi seçin.")

    _ekip_bulgular = []
    if not metrics.empty:
        _en_yuklu = metrics.iloc[0]
        _ortalama = float(metrics["Toplam Yük (SP)"].mean())
        _ekip_bulgular.append(
            ShareBullet(
                f"Ekipte {len(metrics)} kişi var; toplam "
                f"{int(metrics['Toplam İş Sayısı'].sum()):,} iş kayıtlı."
            )
        )
        _ekip_bulgular.append(
            ShareBullet(
                f"En yüklü kişi {_en_yuklu['Sorumlu']} ({_en_yuklu['Toplam Yük (SP)']:.0f} SP); "
                f"kişi başına ortalama {_ortalama:.0f} SP."
            )
        )
    _render_share_block(
        ShareDocument(
            baslik="Ekip & Kişiler",
            alt_baslik=_scope_suffix(),
            kutular=[
                ("Ekipteki Kişi", str(len(metrics))),
                ("Toplam İş", f"{int(metrics['Toplam İş Sayısı'].sum()):,}" if not metrics.empty else "0"),
                ("Toplam Yük (SP)", f"{metrics['Toplam Yük (SP)'].sum():.0f}" if not metrics.empty else "0"),
            ],
            bolumler=[
                ShareSection(baslik="Özet", bullets=_ekip_bulgular),
                ShareSection(
                    baslik=f"Kişi Bazlı İş Yükü ({selected_role})", tablo=_share_table(role_metrics)
                ),
            ],
        ),
        f"ekip_kisiler_{selected_month or 'tum_aylar'}",
    )

# --------------------------------------------------------------------------
# Sayfa: Proje & Konu
# --------------------------------------------------------------------------

elif page == PAGE_PROJE:
    proje_df = analyze_projects_by_subject(df_scope, target_month=selected_month)

    pt1, pt2 = st.columns(2)
    with pt1:
        toplam_konu_open = _clickable_tile(
            "proje_toplam_konu", "Toplam Konu Sayısı", f"{len(proje_df):,}", help_text=KPI_HELP["proje_konu"]
        )
    with pt2:
        en_yuklu_konu = proje_df.iloc[0]["Proje/Konu"] if not proje_df.empty else None
        en_yuklu_konu_open = _clickable_tile(
            "proje_en_yuklu_konu",
            "En Çok Kaynak Tüketen Konu",
            en_yuklu_konu or "—",
            caption=f"{proje_df.iloc[0]['Toplam SP']:.0f} SP" if not proje_df.empty else None,
            help_text=KPI_HELP["proje_konu"],
        )

    if toplam_konu_open:
        st.dataframe(proje_df, width="stretch", hide_index=True)
    if en_yuklu_konu_open and en_yuklu_konu:
        konu_gorev_listesi = get_topic_deep_dive(df_scope, en_yuklu_konu, target_month=selected_month)[
            "gorev_listesi"
        ]
        st.dataframe(konu_gorev_listesi, width="stretch", hide_index=True)
        _render_breakdown_charts(konu_gorev_listesi)

    st.divider()
    _section(
        "Proje / Konu Bazlı Kaynak Tüketimi",
        "Doğrudan Jira Component alanına göre gruplanan konular (kart adedi; üzerine gelince SP görünür)",
        help_text=KPI_HELP["proje_konu"],
    )

    with st.container(border=True):
        if proje_df.empty:
            st.caption("Proje/konu verisi bulunamadı.")
        else:
            top = proje_df.head(12).sort_values("Toplam İş Sayısı")
            fig = go.Figure()
            fig.add_bar(
                x=top["Toplam İş Sayısı"],
                y=top["Proje/Konu"],
                orientation="h",
                marker_color=CATEGORICAL["blue"],
                customdata=top["Toplam SP"],
                text=top["Toplam İş Sayısı"],
                textposition="outside",
                hovertemplate="%{y}<br>Kart Sayısı: %{x}<br>Toplam SP: %{customdata:.0f}<extra></extra>",
            )
            _apply_chart_chrome(fig, height=max(320, 30 * len(top)))
            fig.update_xaxes(gridcolor=GRID_COLOR, title="Toplam İş Sayısı")
            fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch", theme="streamlit")

    st.dataframe(proje_df, width="stretch", hide_index=True)

    st.divider()
    col_planned, col_out = st.columns(2)
    with col_planned:
        _section("Planlanan İşler", help_text=KPI_HELP["planlanan_isler"])
        st.dataframe(
            build_planned_issues_table(df_scope, target_month=selected_month),
            width="stretch",
            hide_index=True,
        )
    with col_out:
        _section("Plan Dışı İşler", help_text=KPI_HELP["plan_disi_isler"])
        st.dataframe(
            build_out_of_plan_issues_table(df_scope, target_month=selected_month),
            width="stretch",
            hide_index=True,
        )

    _proje_bulgular = []
    if not proje_df.empty:
        _en_buyuk = proje_df.iloc[0]
        _proje_bulgular.append(
            ShareBullet(
                f"{len(proje_df)} konu/bileşen içinde en büyük yük "
                f"\"{_en_buyuk['Proje/Konu']}\" ({_en_buyuk['Toplam SP']:.0f} SP, "
                f"tamamlanma %{_en_buyuk['Tamamlanma Oranı (%)']:.0f})."
            )
        )
        _dusuk = proje_df.loc[proje_df["Tamamlanma Oranı (%)"] < 50]
        if not _dusuk.empty:
            _proje_bulgular.append(
                ShareBullet(
                    f"Tamamlanma oranı %50'nin altında olan {len(_dusuk)} konu var: "
                    + ", ".join(_dusuk["Proje/Konu"].head(3).astype(str)),
                    severity="dikkat",
                )
            )
    _render_share_block(
        ShareDocument(
            baslik="Proje & Konu Analizi",
            alt_baslik=_scope_suffix(),
            kutular=[
                ("Konu Sayısı", str(len(proje_df))),
                ("Toplam SP", f"{proje_df['Toplam SP'].sum():.0f}" if not proje_df.empty else "0"),
                (
                    "Tamamlanan SP",
                    f"{proje_df['Tamamlanan SP'].sum():.0f}" if not proje_df.empty else "0",
                ),
            ],
            bolumler=[
                ShareSection(baslik="Özet", bullets=_proje_bulgular),
                ShareSection(baslik="Proje / Konu Bazlı Dağılım", tablo=_share_table(proje_df)),
                ShareSection(
                    baslik="Planlanan İşler",
                    tablo=_share_table(build_planned_issues_table(df_scope, target_month=selected_month)),
                ),
                ShareSection(
                    baslik="Plan Dışı İşler",
                    tablo=_share_table(
                        build_out_of_plan_issues_table(df_scope, target_month=selected_month)
                    ),
                ),
            ],
        ),
        f"proje_konu_{selected_month or 'tum_aylar'}",
    )

# --------------------------------------------------------------------------
# Sayfa: Akış & Darboğazlar
# --------------------------------------------------------------------------

elif page == PAGE_AKIS:
    carried = analyze_carried_over_issues(df_scope, target_month=selected_month)
    _section(
        "Devreden İşler",
        "Önceki sprintlerden seçili sprinte taşınan gerçek Jira kartları",
        help_text=KPI_HELP["devreden_isler"],
    )
    c1, c2, c3 = st.columns(3)
    with c1:
        _tile(
            "Toplam Devreden",
            str(carried["toplam_kart"]),
            caption=f"{carried['toplam_sp']:.0f} SP",
        )
    with c2:
        _tile(
            "Devam Eden",
            str(carried["devam_eden_kart"]),
            caption=f"{carried['devam_eden_sp']:.0f} SP",
            accent=STATUS["warning"],
        )
    with c3:
        _tile(
            "Tamamlanan",
            str(carried["tamamlanan_kart"]),
            caption=f"{carried['tamamlanan_sp']:.0f} SP",
            accent=STATUS["good"],
        )
    if carried["kartlar"].empty:
        st.caption("Seçili sprintte önceki bir sprintten devreden kart bulunamadı.")
    else:
        st.dataframe(carried["kartlar"], width="stretch", hide_index=True)

    st.divider()
    _section(
        "Ham Statü Dağılımı",
        "Jira'nın ham statü alanına göre kart dağılımı",
        help_text=KPI_HELP["durum_dagilimi"],
    )
    with st.container(border=True):
        status_df = calculate_status_breakdown(df_scope, target_month=selected_month)
        if status_df.empty:
            st.caption("Statü dağılımı için veri bulunamadı.")
        else:
            top = status_df.sort_values("İş Sayısı")
            fig = go.Figure()
            fig.add_bar(
                x=top["İş Sayısı"],
                y=top["Statü"],
                orientation="h",
                marker_color=CATEGORICAL["blue"],
                customdata=top["Toplam SP"],
                text=top["İş Sayısı"],
                textposition="outside",
                hovertemplate="%{y}<br>İş Sayısı: %{x}<br>Toplam SP: %{customdata:.0f}<extra></extra>",
            )
            _apply_chart_chrome(fig, height=max(280, 30 * len(top)), yaxis_title=None)
            fig.update_xaxes(gridcolor=GRID_COLOR, title="İş Sayısı")
            fig.update_yaxes(gridcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig, width="stretch", theme="streamlit")

    st.divider()
    _section(
        "İleri Düzey Darboğaz ve Akış Analitiği",
        "Ekibin gizli tıkanıklıklarını ve süreç verimsizliklerini açığa çıkarır",
        help_text=KPI_HELP["ileri_darbogaz_genel"],
    )

    adv = analyze_advanced_bottlenecks(df_scope, target_month=selected_month)
    wip = adv["1_wip_aging"]
    blocker = adv["2_blocker_hold"]
    bouncing = adv["3_assignee_bouncing"]
    reopen = adv["4_reopen_rate"]
    flow = adv["5_flow_load_capacity"]

    _section(
        "WIP Aging",
        "Seçili sprintteki aktif işlerin yaşı (bütün aktif kartlar 3 kovadan birine girer)",
        help_text=KPI_HELP["wip_aging_genel"],
    )
    w1, w2 = st.columns(2)
    with w1:
        _tile("Aktif İş Sayısı", str(wip["aktif_is_sayisi"]))
    with w2:
        _tile("Ortalama Yaş (gün)", f"{wip['ortalama_yas_gun']:.1f}")

    # Bu 3 kova (0-1/2-5/6+ aylık) BİRBİRİYLE EŞLİ ("radio"
    # benzeri) çalışır: biri açılınca diğer ikisi otomatik kapanır. Bunu, widget
    # render edilmeden ÖNCE (bir sonraki rerun'u beklemeden, aynı anda) uygulamak
    # için `st.toggle`'ın `on_change` callback'i kullanılır - Streamlit, bir
    # widget'ın `on_change`'ini o widget'ın KENDİ session_state'i güncellendikten
    # HEMEN SONRA ama asıl script gövdesi (bu döngü) TEKRAR ÇALIŞMADAN ÖNCE
    # tetikler; bu yüzden döngüdeki diğer `st.toggle(...)` çağrıları, kapatılmış
    # session_state'i AYNI çalıştırmada doğru okur - ekstra bir tıklama/rerun
    # gerekmez. İlk kova ("0-1 Aylık Aktif İş") sayfa ilk
    # açıldığında varsayılan olarak AÇIK gelir, diğer ikisi kapalı başlar.
    wip_bucket_keys = ("onceki_ay", "uc_aylik", "alti_aylik")
    wip_state_key = {k: f"open_wip_{k}" for k in wip_bucket_keys}
    if wip_state_key["onceki_ay"] not in st.session_state:
        st.session_state[wip_state_key["onceki_ay"]] = True

    def _wip_bucket_opened(changed_key: str) -> None:
        """Bir kova acildiginda (True oldugunda) digerlerini kapatir; kapatildiginda
        (False oldugunda) hicbir seye dokunmaz (hepsi kapali kalabilir)."""
        if st.session_state[changed_key]:
            for other_key in wip_state_key.values():
                if other_key != changed_key:
                    st.session_state[other_key] = False

    wb1, wb2, wb3 = st.columns(3)
    wip_tile_specs = [
        (wb1, "onceki_ay", KPI_HELP["wip_onceki_ay"]),
        (wb2, "uc_aylik", KPI_HELP["wip_uc_aylik"]),
        (wb3, "alti_aylik", KPI_HELP["wip_alti_aylik"]),
    ]
    bucket_open = {}
    for col, bucket_key, help_text in wip_tile_specs:
        label = WIP_BUCKET_KEY_TO_LABEL[bucket_key]
        bucket = wip[bucket_key]
        tier = WIP_BUCKET_STATUS[label]
        state_key = wip_state_key[bucket_key]
        with col:
            _tile(
                label,
                str(bucket["is_sayisi"]),
                caption=f"{bucket['toplam_sp']:.0f} SP",
                accent=STATUS[tier],
                help_text=help_text,
            )
            bucket_open[bucket_key] = st.toggle(
                "Detay", key=state_key, on_change=_wip_bucket_opened, args=(state_key,)
            )

    with st.container(border=True):
        yaslanma = wip["yaslanma_ozeti"]
        fig = go.Figure()
        fig.add_bar(
            x=yaslanma["Kategori"],
            y=yaslanma["İş Sayısı"],
            marker_color=[STATUS[WIP_BUCKET_STATUS.get(g, "warning")] for g in yaslanma["Kategori"]],
            customdata=yaslanma["Toplam SP"],
            text=yaslanma["İş Sayısı"],
            textposition="outside",
            hovertemplate="%{x}<br>İş Sayısı: %{y}<br>Toplam SP: %{customdata:.0f}<extra></extra>",
        )
        _apply_chart_chrome(fig, height=300, yaxis_title="İş Sayısı")
        chart_event = st.plotly_chart(
            fig,
            width="stretch",
            theme="streamlit",
            on_select="rerun",
            selection_mode="points",
            key="wip_bucket_chart",
        )
        st.caption(
            "🟡 0-1 aylık · 🟠 2-5 aylık · 🔴 6+ aylık — "
            "bir sütuna tıklayarak o grubun detayını açabilirsiniz."
        )

    clicked_points = (chart_event or {}).get("selection", {}).get("points", [])
    if clicked_points:
        clicked_label = clicked_points[0].get("x")
        clicked_key = WIP_BUCKET_LABEL_TO_KEY.get(clicked_label)
        if clicked_key:
            # Kova toggle'larıyla AYNI eşli (radio-benzeri) kural: grafikten
            # tıklanan kova açılır, diğer ikisi kapanır.
            for bucket_key in wip_bucket_keys:
                is_clicked = bucket_key == clicked_key
                st.session_state[wip_state_key[bucket_key]] = is_clicked
                bucket_open[bucket_key] = is_clicked

    for bucket_key in ("onceki_ay", "uc_aylik", "alti_aylik"):
        if not bucket_open.get(bucket_key):
            continue
        label = WIP_BUCKET_KEY_TO_LABEL[bucket_key]
        kartlar = wip[bucket_key]["kartlar"]
        st.caption(f"**{label}** kartları (oluşturulma tarihine göre artan sıralı):")
        if kartlar.empty:
            st.caption("Bu grupta kart bulunmuyor.")
        else:
            st.dataframe(kartlar, width="stretch", hide_index=True)

    st.divider()
    _section(
        "Devam Eden Darboğazlar (İsim Bazlı)",
        "Aynı işin farklı aylarda/yüzdelerle tekrar açılıp güncel sprintte de devam ettiği durumlar",
        help_text=KPI_HELP["devam_eden_darbogaz"],
    )
    recurring_df = detect_recurring_bottlenecks(df_scope, target_month=selected_month)
    if recurring_df.empty:
        st.caption("Güncel ayda devam eden, isim bazlı tekrarlayan bir darboğaz bulunamadı.")
    else:
        st.dataframe(recurring_df, width="stretch", hide_index=True)

    st.divider()

    _section(
        "Blocker & Akış Yükü",
        "Tıkanan işlerin maliyeti ve akış aşaması dağılımı",
        help_text=KPI_HELP["blocker_akis_yuku_genel"],
    )
    b_col, f_col = st.columns(2)
    with b_col, st.container(border=True):
        blocker_tier = _status_tier(blocker["tikali_is_orani_yuzde"], good_cut=10, warn_cut=25, higher_is_better=False)
        st.markdown(f"**Blocker & Hold**{_info_icon(KPI_HELP['blocker_hold'])}", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:1.6rem;font-weight:800;color:{STATUS[blocker_tier]};'>"
            f"%{blocker['tikali_is_orani_yuzde']:.1f}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(_badge(blocker_tier, {"good": "İyi", "warning": "Dikkat", "critical": "Kritik"}[blocker_tier]), unsafe_allow_html=True)
        st.caption(
            f"Tıkanan iş sayısı: {blocker['tikali_is_sayisi']} · "
            f"Maliyet: {blocker['tikali_sp']:.0f} SP (%{blocker['tikali_sp_orani_yuzde']:.1f})"
        )
        if not blocker["tikali_isler"].empty:
            st.dataframe(blocker["tikali_isler"].head(5), width="stretch", hide_index=True)
    with f_col, st.container(border=True):
        st.markdown(f"**Flow Load vs. Capacity**{_info_icon(KPI_HELP['flow_load'])}", unsafe_allow_html=True)
        asama = flow["asama_dagilimi"]
        if asama.empty:
            st.caption("Akış aşaması verisi bulunamadı.")
        else:
            fig = go.Figure()
            fig.add_bar(
                x=asama["Akış Aşaması"],
                y=asama["İş Sayısı"],
                marker_color=[FLOW_STAGE_COLORS.get(a, CATEGORICAL["violet"]) for a in asama["Akış Aşaması"]],
                text=asama["İş Sayısı"],
                textposition="outside",
                hovertemplate="%{x}: %{y} iş<extra></extra>",
            )
            _apply_chart_chrome(fig, height=260, yaxis_title="İş Sayısı")
            fig.update_xaxes(tickangle=-20)
            st.plotly_chart(fig, width="stretch", theme="streamlit")

    st.divider()

    _section(
        "Yoğunlaşma ve Geri Dönüş",
        "İş yükü yoğunlaşması ve geri dönüş/test göstergesi",
        help_text=KPI_HELP["yogunlasma_reopen_genel"],
    )
    bc_col, r_col = st.columns(2)
    with bc_col, st.container(border=True):
        bounce_tier = _status_tier(bouncing["yogunlasma_orani_yuzde"], good_cut=30, warn_cut=45, higher_is_better=False)
        st.markdown(f"**İş Yükü Yoğunlaşması (Proxy)**{_info_icon(KPI_HELP['bouncing'])}", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:1.6rem;font-weight:800;color:{STATUS[bounce_tier]};'>"
            f"%{bouncing['yogunlasma_orani_yuzde']:.1f}</div>",
            unsafe_allow_html=True,
        )
        st.caption(f"En yüklü kişi: {bouncing['en_yuklu_kisi'] or '—'}")
        st.caption(bouncing["not"])
    with r_col, st.container(border=True):
        reopen_tier = _status_tier(reopen["reopen_orani_yuzde"], good_cut=5, warn_cut=15, higher_is_better=False)
        reopen_label = "Testte Bekleyen (Proxy)" if reopen["yontem"] == "test_asamasinda_takilma" else "Reopen / Geri Dönüş"
        st.markdown(f"**{reopen_label}**{_info_icon(KPI_HELP['reopen'])}", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:1.6rem;font-weight:800;color:{STATUS[reopen_tier]};'>"
            f"%{reopen['reopen_orani_yuzde']:.1f}</div>",
            unsafe_allow_html=True,
        )
        st.caption(f"Yöntem: {reopen['yontem']}")
        st.caption(reopen["aciklama"])

    st.divider()

    _section("Süreç Kaybı Özeti", "Ekibin süreç içinde nerede zaman kaybettiğine dair özet")
    with st.container(border=True):
        st.markdown(
            f"""
- **WIP Aging:** {wip['aktif_is_sayisi']} aktif işin ortalama açık kalma süresi
  **{wip['ortalama_yas_gun']:.1f} gün**; **{wip['onceki_ay']['is_sayisi']}** tanesi 0-1 aylık,
  **{wip['uc_aylik']['is_sayisi']}** tanesi 2-5 aylık, **{wip['alti_aylik']['is_sayisi']}** tanesi
  6+ aydır açık (uzun süreli).
- **Blocker & Hold:** İşlerin **%{blocker['tikali_is_orani_yuzde']:.1f}**'i tıkanmış durumda,
  bu da **{blocker['tikali_sp']:.0f} SP**'lik bir kapasite kaybına denk geliyor.
- **Yük Yoğunlaşması:** Ekibin en yüklü %20'lik dilimi toplam yükün
  **%{bouncing['yogunlasma_orani_yuzde']:.1f}**'ini taşıyor (en yüklü: **{bouncing['en_yuklu_kisi'] or '—'}**).
- **Reopen/Takılma:** **%{reopen['reopen_orani_yuzde']:.1f}** oranında iş geri dönüş/takılma belirtisi
  gösteriyor ({reopen['yontem']} yöntemiyle tahmin edildi).
            """
        )

    st.divider()
    _section(
        "Aylık Trend",
        "Darboğaz metriklerinin aylar içindeki seyri",
        help_text=KPI_HELP["aylik_trend_darbogaz"],
    )
    if len(all_months) < 2:
        st.caption("Aylık trend için en az 2 ay veri gerekiyor.")
    else:
        trend_df = _build_bottleneck_monthly_trend(df_scope, all_months)

        with st.container(border=True):
            oran_fig = go.Figure()
            for kolon, renk in (
                ("Blocker & Hold Oranı (%)", CATEGORICAL["orange"]),
                ("Reopen Oranı (%)", CATEGORICAL["aqua"]),
                ("Yük Yoğunlaşma Oranı (%)", CATEGORICAL["yellow"]),
            ):
                oran_fig.add_trace(
                    go.Scatter(
                        x=trend_df["Ay"],
                        y=trend_df[kolon],
                        mode="lines+markers",
                        name=kolon,
                        line=dict(color=renk, width=2),
                        hovertemplate="%{x}<br>" + kolon + ": %{y:.1f}%<extra></extra>",
                    )
                )
            _apply_chart_chrome(oran_fig, height=320, yaxis_title="Oran (%)")
            st.plotly_chart(oran_fig, width="stretch", theme="streamlit")
            st.caption(
                f"Not: O ay aktif kişi sayısı azsa (yaklaşık {BOUNCING_MIN_PERSON_COUNT}'in altındaysa) "
                "yoğunlaşma oranı istatistiksel olarak anlamsızlaştığından o ay için nokta gösterilmiyor."
            )

        with st.container(border=True):
            st.markdown(
                f"**WIP Aging Kova Trendi**{_info_icon(KPI_HELP['wip_aging_genel'])}",
                unsafe_allow_html=True,
            )
            st.caption("Sayfanın üstündeki WIP Aging 3 kovasının (bkz. o bölüm) aylar içindeki seyri")
            bucket_fig = go.Figure()
            for bucket_key, label in WIP_BUCKET_KEY_TO_LABEL.items():
                renk = STATUS[WIP_BUCKET_STATUS[label]]
                bucket_fig.add_trace(
                    go.Scatter(
                        x=trend_df["Ay"],
                        y=trend_df[label],
                        mode="lines+markers",
                        name=label,
                        line=dict(color=renk, width=2),
                        customdata=trend_df[f"{label} (SP)"],
                        hovertemplate="%{x}<br>" + label + ": %{y} iş (%{customdata:.0f} SP)<extra></extra>",
                    )
                )
            _apply_chart_chrome(bucket_fig, height=300, yaxis_title="İş Sayısı")
            st.plotly_chart(bucket_fig, width="stretch", theme="streamlit")

        with st.container(border=True):
            st.caption("WIP Aging genel trendi (aktif iş sayısı ve ortalama yaş, ayrı eksenlerde)")
            wip_fig = make_subplots(specs=[[{"secondary_y": True}]])
            wip_fig.add_trace(
                go.Scatter(
                    x=trend_df["Ay"],
                    y=trend_df["Aktif İş Sayısı"],
                    mode="lines+markers",
                    name="Aktif İş Sayısı",
                    line=dict(color=CATEGORICAL["blue"], width=2),
                    hovertemplate="%{x}<br>Aktif İş Sayısı: %{y}<extra></extra>",
                ),
                secondary_y=False,
            )
            wip_fig.add_trace(
                go.Scatter(
                    x=trend_df["Ay"],
                    y=trend_df["Ortalama Yaş (gün)"],
                    mode="lines+markers",
                    name="Ortalama Yaş (gün)",
                    line=dict(color=CATEGORICAL["violet"], width=2, dash="dot"),
                    hovertemplate="%{x}<br>Ortalama Yaş: %{y:.1f} gün<extra></extra>",
                ),
                secondary_y=True,
            )
            _apply_chart_chrome(wip_fig, height=280)
            wip_fig.update_yaxes(title_text="Aktif İş Sayısı", secondary_y=False, gridcolor=GRID_COLOR)
            wip_fig.update_yaxes(title_text="Ortalama Yaş (gün)", secondary_y=True, gridcolor="rgba(0,0,0,0)")
            st.plotly_chart(wip_fig, width="stretch", theme="streamlit")

    _render_share_block(
        ShareDocument(
            baslik="Akış & Darboğaz Analizi",
            alt_baslik=_scope_suffix(),
            kutular=[
                ("Aktif İş", str(wip["aktif_is_sayisi"])),
                ("Ortalama Yaş", f"{wip['ortalama_yas_gun']:.1f} gün"),
                ("Tıkanan İş Oranı", f"%{blocker['tikali_is_orani_yuzde']:.1f}"),
                ("Reopen Oranı", f"%{reopen['reopen_orani_yuzde']:.1f}"),
            ],
            bolumler=[
                ShareSection(
                    baslik="Süreç Kaybı Özeti",
                    bullets=[
                        ShareBullet(
                            f"WIP Aging: {wip['aktif_is_sayisi']} aktif işin ortalama açık kalma süresi "
                            f"{wip['ortalama_yas_gun']:.1f} gün; {wip['onceki_ay']['is_sayisi']} tanesi "
                            f"0-1 aylık, {wip['uc_aylik']['is_sayisi']} tanesi 2-5 aylık, "
                            f"{wip['alti_aylik']['is_sayisi']} tanesi 6+ aylık."
                        ),
                        ShareBullet(
                            f"Blocker & Hold: işlerin %{blocker['tikali_is_orani_yuzde']:.1f} kadarı tıkanmış, "
                            f"bu {blocker['tikali_sp']:.0f} SP'lik kapasite kaybı demek.",
                            severity="dikkat" if blocker["tikali_is_orani_yuzde"] >= 10 else None,
                        ),
                        ShareBullet(
                            f"Yük yoğunlaşması: en yüklü %20'lik dilim toplam yükün "
                            f"%{bouncing['yogunlasma_orani_yuzde']:.1f} kadarını taşıyor "
                            f"(en yüklü: {bouncing['en_yuklu_kisi'] or '—'})."
                        ),
                        ShareBullet(
                            f"Reopen/takılma: %{reopen['reopen_orani_yuzde']:.1f} "
                            f"({reopen['yontem']} yöntemiyle tahmin edildi)."
                        ),
                    ],
                ),
                ShareSection(baslik="Ham Statü Dağılımı", tablo=_share_table(status_df)),
                ShareSection(
                    baslik="Devreden İşler",
                    tablo=_share_table(carried["kartlar"]),
                ),
                ShareSection(
                    baslik="Devam Eden Darboğazlar (İsim Bazlı)", tablo=_share_table(recurring_df)
                ),
                ShareSection(
                    baslik="Tıkanan İşler",
                    tablo=_share_table(blocker["tikali_isler"]),
                ),
            ],
            dipnot=reopen.get("aciklama", ""),
        ),
        f"akis_darbogaz_{selected_month or 'tum_aylar'}",
    )

# --------------------------------------------------------------------------
# Sayfa: Akıllı Asistan
# --------------------------------------------------------------------------

elif page == PAGE_ASISTAN:
    if WATERMARK_URI:
        # SADECE bu sayfada aktif: gercek gorsel tam opaklikta arka plana konur,
        # ustune neredeyse opak bir katman (::before) binuir - boylece sadece
        # ~%6-7'lik bir iz kalir. ::before, gercek chat icerigiyle AYNI elemanin
        # gercek cocugu olmadigindan, metnin okunabilirligi (opacity ile dogrudan
        # soldurmanin aksine) hic etkilenmez.
        #
        # background-size: contain (cover DEGIL) - fotografin KIRPILMADAN
        # TAMAMININ gorunmesi istendigi icin; portre (422x726) gorsel, konteynerin
        # icine sigacak sekilde olceklenir, bos kalan kenarlar ::before katmaninin
        # duz rengiyle dolar (sayfa zeminiyle ayni gorundugu icin fark edilmez).
        #
        # Uygulama .streamlit/config.toml'da base="light" ile SABIT (koyu tema
        # secenegi yok); bu yuzden burada @media (prefers-color-scheme: dark)
        # KULLANILMAZ - kullanicinin OS'u koyu modda olsa bile bu sayfa her zaman
        # config.toml'daki backgroundColor (#FFFDF6) ile ayni acik zeminde kalmali.
        # Katmanin rengi de generic beyaz degil, dogrudan o backgroundColor'in
        # rgba karsiligidir (255, 253, 246) - watermark'li sayfa diger tum
        # sayfalarla birebir ayni zeminde gorunur.
        st.markdown(
            f"""
            <style>
            div[data-testid="stAppViewContainer"] {{
                position: relative;
                background-image: url('{WATERMARK_URI}');
                background-size: contain;
                background-position: center;
                background-repeat: no-repeat;
            }}
            div[data-testid="stAppViewContainer"]::before {{
                content: "";
                position: absolute;
                inset: 0;
                z-index: 0;
                pointer-events: none;
                background: rgba(255, 253, 246, 0.93);
            }}
            </style>
            """,
            unsafe_allow_html=True,
        )

    SUGGESTED_QUESTIONS = [
        "Kimin üzerinde kaç iş var?",
        "Hangi işler bloklu?",
        "Tahminlerimiz en çok nerede şaşıyor?",
        "En çok kaynak tüketen proje hangisi?",
    ]

    header_col, clear_col = st.columns([5, 1])
    with header_col:
        # _section()'a dokunmadan, SADECE bu baslik icin ozel render: ayni gorsel
        # stil (1.25rem/700 baslik, 0.85rem/opacity 0.65 alt yazi, gradient cizgi)
        # ama baslik satirinin SOLUNA (varsa) kucuk yuvarlak bir teknocan ikonu
        # eklenir. WATERMARK_URI None ise (dosya bulunamadiysa) ikon hic basilmaz.
        _asistan_avatar_html = (
            f'<img src="{WATERMARK_URI}" alt="" '
            'style="width:40px;height:40px;border-radius:50%;object-fit:cover;flex-shrink:0;" />'
            if WATERMARK_URI
            else ""
        )
        # NOT: _section()/_tile() ile ayni sebeple TEK SATIRLIK HTML uretiyoruz -
        # WATERMARK_URI None oldugunda _asistan_avatar_html bos string oluyor ve
        # cok satirli halde bu, Markdown'in HTML blogunu erken bitirmesine yol
        # aciyordu.
        st.markdown(
            f'<div style="margin:0.3rem 0 0.8rem 0;">'
            f'<div style="display:flex;align-items:center;gap:0.6rem;">'
            f'{_asistan_avatar_html}'
            f'<div style="font-size:1.25rem;font-weight:700;">Akıllı Jira Asistanı</div>'
            f'</div>'
            f'<div style="font-size:0.85rem;opacity:0.65;margin-top:0.1rem;">'
            f"Yerel bir dil modeli (Ollama, model: {DEFAULT_MODEL}), mcp_server.py'nin 13 gerçek MCP "
            f"aracını çağırarak verilerinizi sorgular; tüm işlem bu bilgisayarda gerçekleşir, hiçbir "
            f"veri veya soru dışarıya/bir buluta gönderilmez"
            f'</div>'
            f'<div style="height:3px;border-radius:2px;margin-top:0.5rem;background:linear-gradient(90deg, {ACCENT}80, transparent);"></div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with clear_col:
        if st.session_state.get("chat_history") and st.button("Temizle"):
            st.session_state.chat_history = []
            st.rerun()

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    def _ask(soru: str) -> None:
        history = st.session_state.chat_history
        with st.spinner(f"{DEFAULT_MODEL} düşünüyor ve MCP araçlarını çalıştırıyor..."):
            try:
                _, new_history = chat_with_local_model(report_file_path, history, soru, model=DEFAULT_MODEL)
                st.session_state.chat_history = new_history
            except AssistantUnavailableError as exc:
                st.session_state.chat_history = [
                    *history,
                    {"role": "user", "content": soru},
                    {"role": "assistant", "content": f"⚠️ {exc}"},
                ]

    if not st.session_state.chat_history:
        st.markdown("### 👋 Merhaba! Size nasıl yardımcı olabilirim?")
        st.caption(
            "Sprint verileriniz hakkında aklınıza gelen HERHANGİ bir soruyu doğrudan aşağıya "
            "yazabilirsiniz - önceden tanımlı bir soru listesiyle sınırlı değilsiniz."
        )
        secilen = st.pills(
            "Aklınıza bir şey gelmiyorsa birini deneyin:",
            SUGGESTED_QUESTIONS,
            key="suggestion_pills",
        )
        if secilen:
            _ask(secilen)
            st.rerun()
    else:
        for msg in st.session_state.chat_history:
            with st.chat_message(msg["role"]):
                st.write(msg["content"])

    soru = st.chat_input("Sprint verileriniz hakkında bir soru yazın...")
    if soru:
        _ask(soru)
        st.rerun()

    with st.expander("🛠️ Kullanılabilir MCP Araçları (13)", expanded=False):
        st.caption(
            "Bu asistan, sorularınızı yanıtlarken aşağıdaki 13 gerçek MCP aracından "
            "uygun olanı (veya birkaçını) otomatik seçip çalıştırır."
        )
        for kategori, araclar in MCP_TOOL_CATALOG.items():
            st.markdown(f"**{kategori}**")
            cols = st.columns(3)
            for i, (ad, aciklama) in enumerate(araclar):
                with cols[i % 3]:
                    _tool_card(ad, aciklama)
            st.write("")

# --------------------------------------------------------------------------
# Sayfa: Rapor Merkezi
# --------------------------------------------------------------------------

elif page == PAGE_RAPOR:
    _section(
        "Rapor Merkezi",
        "Biçimlendirilmiş, grafikli Excel raporunu oluşturun, ekranda önizleyin ve seçili kartları ayrıca indirin",
    )
    st.write(
        f"Seçili kapsam: **{selected_scope_label or 'Tüm İterasyonlar'}** iterasyon filtresiyle, "
        "tüm ekip için biçimlendirilmiş bir Excel raporu oluşturulur (grafikli özet + "
        "planlanan/plan dışı iş listeleri)."
    )

    dosya_ay = (selected_month or "tum_aylar").replace(" ", "_")
    excel_col, pdf_col = st.columns(2)

    with excel_col:
        if st.button("📊 Excel Raporu Oluştur", type="primary", width="stretch"):
            rapor = _generate_excel_report(file_bytes, file_name, selected_month)
            st.session_state["excel_bytes"] = rapor["excel_bytes"]
            st.session_state["excel_planned_df"] = rapor["planned_preview_df"]
            st.session_state["excel_out_of_plan_df"] = rapor["out_of_plan_preview_df"]
            st.session_state["excel_planned_export_df"] = rapor["planned_export_df"]
            st.session_state["excel_out_of_plan_export_df"] = rapor["out_of_plan_export_df"]
            st.session_state["excel_filename"] = f"sprint_raporu_{dosya_ay}.xlsx"
            st.success("Excel raporu hazır.")

    with pdf_col:
        if st.button("📄 PDF Raporu Oluştur", width="stretch"):
            try:
                st.session_state["pdf_bytes"] = _generate_pdf_report(
                    file_bytes, file_name, selected_month
                )
            except PdfFontError as exc:
                # Turkce karakterli font yoksa BOZUK bir PDF uretmek yerine
                # durum acikca bildirilir (bkz. pdf_reporter modul dokumantasyonu).
                st.session_state.pop("pdf_bytes", None)
                st.error(str(exc))
            else:
                st.session_state["pdf_filename"] = f"sprint_raporu_{dosya_ay}.pdf"
                st.success("PDF raporu hazır.")

    excel_dl_col, pdf_dl_col = st.columns(2)
    with excel_dl_col:
        if "excel_bytes" in st.session_state:
            st.download_button(
                "⬇️ Excel Raporunu İndir",
                data=st.session_state["excel_bytes"],
                file_name=st.session_state["excel_filename"],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
            )
    with pdf_dl_col:
        if "pdf_bytes" in st.session_state:
            st.download_button(
                "⬇️ PDF Raporunu İndir",
                data=st.session_state["pdf_bytes"],
                file_name=st.session_state["pdf_filename"],
                mime="application/pdf",
                width="stretch",
            )
            st.caption(
                f"{len(st.session_state['pdf_bytes']) / 1024:,.0f} KB · KPI özeti, trend "
                "grafiği, statü/kişi dağılımı ve iş listeleri"
            )

    if "excel_bytes" in st.session_state:

        st.caption(
            "Excel içeriğinin önizlemesi - Excel'deki gibi planlanan/plan dışı AYRI iki tablo "
            "halinde; satır seçip en alttaki butonla sadece seçili satırları ayrıca indirebilirsiniz:"
        )

        planned_preview_df = st.session_state["excel_planned_df"]
        out_of_plan_preview_df = st.session_state["excel_out_of_plan_df"]

        st.markdown("**Planlanan İşler**", unsafe_allow_html=True)
        planned_event = st.dataframe(
            planned_preview_df,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="excel_preview_planned",
        )
        st.markdown("**Plan Dışı İşler**", unsafe_allow_html=True)
        out_of_plan_event = st.dataframe(
            out_of_plan_preview_df,
            width="stretch",
            hide_index=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="excel_preview_out_of_plan",
        )

        planned_selected_rows = (planned_event or {}).get("selection", {}).get("rows", [])
        out_of_plan_selected_rows = (out_of_plan_event or {}).get("selection", {}).get("rows", [])

        planned_export_df = st.session_state["excel_planned_export_df"]
        out_of_plan_export_df = st.session_state["excel_out_of_plan_export_df"]

        selected_parts = []
        if planned_selected_rows:
            selected_parts.append(planned_export_df.iloc[planned_selected_rows])
        if out_of_plan_selected_rows:
            selected_parts.append(out_of_plan_export_df.iloc[out_of_plan_selected_rows])

        if selected_parts:
            selected_csv_bytes = _selected_rows_to_jira_import_csv_bytes(
                pd.concat(selected_parts, ignore_index=True)
            )
            st.download_button(
                "Seçili Satırları İndir",
                data=selected_csv_bytes,
                file_name="sprint_kartlari_jira_import.csv",
                mime="text/csv",
            )
        else:
            st.button("Seçili Satırları İndir", disabled=True)
            st.caption("İndirmek için yukarıdaki tablolardan en az bir satır seçin.")
