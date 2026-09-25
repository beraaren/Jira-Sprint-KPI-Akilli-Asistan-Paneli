"""Jira sprint/iterasyon HTML raporlarini okuyup KPI ve tablo formatlarina donusturur.

Streamlit arayuzu ve MCP server tarafindan dogrudan import edilip cagrilabilecek
sekilde tasarlanmistir; tum fonksiyonlar saf (side-effect'siz) ve pandas
DataFrame / dict tabanli veri yapilari ile calisir.
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from config import sprint_disi_fallback_enabled

__all__ = [
    "SprintKPIs",
    "read_sprint_report",
    "standardize_dataframe",
    "filter_planned_issues",
    "filter_out_of_plan_issues",
    "analyze_carried_over_issues",
    "calculate_sprint_kpis",
    "calculate_completion_rate",
    "build_planned_issues_table",
    "build_out_of_plan_issues_table",
    "summarize_metrics",
    "compare_iterations",
    "calculate_monthly_kpis",
    "build_monthly_history",
    "build_month_sprint_labels",
    "calculate_yearly_monthly_kpis",
    "build_yearly_monthly_history",
    "compare_yearly_sprints",
    "latest_month_label",
    "filter_by_month",
    "filter_by_assignee",
    "filter_by_person",
    "filter_by_project",
    "calculate_assignee_metrics",
    "get_assignee_deep_dive",
    "calculate_status_breakdown",
    "drop_duplicate_rows",
    "compare_multi_sprints",
    "search_issues_by_query",
    "analyze_projects_by_subject",
    "get_topic_deep_dive",
    "detect_bottlenecks",
    "analyze_estimation_accuracy",
    "run_core_5_kpi_analyses",
    "analyze_advanced_bottlenecks",
    "calculate_capacity_forecast",
    "calculate_capacity_forecast_split",
    "detect_recurring_bottlenecks",
    "explode_by_role",
    "answer_dashboard_query",
    "process_sprint_report",
    "JiraApiError",
    "JiraSslError",
    "discover_jira_fields",
    "fetch_issues_from_jira_api",
    "jira_person_field_to_display",
]

# --------------------------------------------------------------------------
# Sabitler
# --------------------------------------------------------------------------

# Kanonik alan adi -> Jira disa aktarimlarinda (HTML/CSV/XLSX) karsilasilabilecek
# (normallestirilmis / kucuk harf) baslik varyasyonlari. CSV/XLSX disa aktarimlarinda
# Jira, ozel alanlari "Custom field (...)" ile sarmaladigindan bu varyasyonlar da
# ayrica eklenmistir (orn. "Custom field (Story Points)").
COLUMN_ALIASES: dict[str, list[str]] = {
    "issue_key": ["issue key", "key", "jira key", "talep anahtari", "talep anahtarı"],
    "issue_type": ["issue type", "issuetype", "talep tipi", "is tipi"],
    "summary": ["summary", "is listesi", "özet", "ozet"],
    "labels": ["labels", "label", "etiket", "etiketler"],
    "estimate": [
        "estimate",
        "story points",
        "story point",
        "sp",
        "orijinal tahmin",
        "original estimate",
        "custom field (story points)",
        "custom field (story point estimate)",
    ],
    "status": ["status", "durum"],
    "assignee": ["assignee", "sorumlu", "atanan", "name", "kişi"],
    "project": ["project", "proje", "project name", "project key"],
    "component": ["component/s", "component", "components", "bileşen", "bilesen"],
    "created": [
        "created",
        "created date",
        "created time",
        "oluşturulma tarihi",
        "olusturulma tarihi",
        "oluşturma tarihi",
        "olusturma tarihi",
    ],
    "resolved": ["resolved", "resolved date", "çözüm tarihi", "cozum tarihi"],
    # Kartin EN SON statu degisikliginin zamani. Gercek veride `resolved` tamamen
    # bos oldugu icin "bu is ne zaman hareket etti/bitti" sorusunun TEK kaynagi
    # budur - haftalik analiz (bkz. src/weekly_report.py) buna dayanir.
    "last_transition": [
        "last transition",
        "custom field (last transition)",
        "son geçiş",
        "son gecis",
        "son geçiş tarihi",
        "son gecis tarihi",
    ],
}

# Bu alanlar bulunamazsa rapor islenemez.
REQUIRED_COLUMNS = {"issue_type", "summary", "status"}

# Jira HTML disa aktarimlarindaki tipik "Created" bicimi: "04-Aug-25 10:15".
CREATED_DATE_FORMAT = "%d-%b-%y %H:%M"

MONTH_LABELS_TR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}

# "SprintDışı" etiketinin yazim varyasyonlarini (SprintDisi, Sprint Dışı, ...)
# yakalayan, buyuk/kucuk harf duyarsiz desen.
SPRINT_DISI_PATTERN = re.compile(r"sprint[\s_-]*d[iıİI]ş?[iıİI]", re.IGNORECASE)

DONE_STATUS = "done"

# Summary icindeki "(%80)" gibi yuzde ifadelerini yakalar.
PERCENT_PATTERN = re.compile(r"%\s*(\d{1,3})")

# Turkce karakterleri ASCII karsiliklarina indirger. Sprint adlari ayni projede
# bile tutarsiz yazilir ("MS Sprint - Ağustos 25" ama "MS Sprint - Agustos 26",
# "Şubat" ama "Subat"); ay adi eslestirmesi bu indirgenmis hal uzerinden yapilir.
_TR_ASCII_TABLE = str.maketrans(
    {
        "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i", "İ": "i",
        "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
    }
)

# ASCII'ye indirgenmis ay adi -> ay numarasi (MONTH_LABELS_TR'nin tersi).
MONTH_NUMBERS_TR = {
    "ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6,
    "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12,
}

# Sprint adindan ay ve yil yakalar: "MS Sprint - Temmuz 26" -> ("temmuz", "26").
# Yil 4, 2 ya da (veride goruldugu uzere KESIK) 1 haneli olabilir - bkz.
# `build_sprint_period_map`.
#
# Ay ile yil ARASINA girebilen kelimeler: farkli board'lar farkli sablon kullanir -
# MS "MS Sprint - Eylül 26" yazarken RZN "Eylül İterasyonu - 2025" yazar. Aradaki
# bu tek kelime OPSIYONELDIR, yani MS bicimi HIC ETKILENMEZ; sadece araya
# "iterasyon(u)" / "sprint(i)" gireni de yakalamis oluruz.
#
# Kasitli olarak SERBEST metin degil, SAYILI bir kelime listesi kabul edilir
# (`.*?` gibi bir joker, "Mart raporu 2024 butcesi" turu adlarda alakasiz bir
# sayiyi yil sanip yanlis aya baglardi).
_SPRINT_ARA_KELIMELER = r"(?:iterasyonu|iterasyon|sprinti|sprint)"
SPRINT_MONTH_PATTERN = re.compile(
    r"\b(" + "|".join(MONTH_NUMBERS_TR) + r")\b"
    r"[\s._/-]*" + _SPRINT_ARA_KELIMELER + r"?[\s._/-]*"
    r"(\d{1,4})\b"
)

ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "cp1254", "iso-8859-9")


@dataclass
class SprintKPIs:
    committed_sp: float
    completed_sp: float
    out_of_plan_sp: float
    total_completed_sp: float
    completion_rate: float
    out_of_plan_rate: float
    planned_issue_count: int
    completed_issue_count: int
    out_of_plan_issue_count: int


# --------------------------------------------------------------------------
# Okuma ve temizleme
# --------------------------------------------------------------------------


def _read_html_text(file_path: str | Path) -> str:
    path = Path(file_path)
    last_error: UnicodeDecodeError | None = None
    for encoding in ENCODING_CANDIDATES:
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise UnicodeDecodeError(
        last_error.encoding if last_error else "utf-8", b"", 0, 1,
        f"'{path}' desteklenen kodlamalardan hicbiriyle ({ENCODING_CANDIDATES}) okunamadi.",
    )


def _flatten_columns(columns: pd.Index) -> list[str]:
    if isinstance(columns, pd.MultiIndex):
        return [
            " ".join(str(level) for level in tup if str(level) not in ("nan", "")).strip()
            for tup in columns
        ]
    return [str(c) for c in columns]


def _normalize_header(text: object) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().casefold()


def _select_issue_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    alias_set = {alias for aliases in COLUMN_ALIASES.values() for alias in aliases}

    def score(df: pd.DataFrame) -> int:
        normalized = {_normalize_header(c) for c in _flatten_columns(df.columns)}
        return len(normalized & alias_set)

    return max(tables, key=score)


# `read_sprint_report`'un dosya uzantisina gore hangi okuyucuya yonlendirecegini
# belirler (buyuk/kucuk harf duyarsiz).
SUPPORTED_REPORT_EXTENSIONS = (".html", ".htm", ".csv", ".xlsx")


def _read_html_report(file_path: str | Path) -> pd.DataFrame:
    html_text = _read_html_text(file_path)
    try:
        tables = pd.read_html(StringIO(html_text), attrs={"id": "issuetable"}, flavor="lxml")
    except ValueError:
        tables = pd.read_html(StringIO(html_text), flavor="lxml")

    if not tables:
        raise ValueError(f"'{file_path}' icinde okunabilir bir tablo bulunamadi.")

    return tables[0] if len(tables) == 1 else _select_issue_table(tables)


def _read_csv_report(file_path: str | Path) -> pd.DataFrame:
    path = Path(file_path)
    last_error: Exception | None = None
    for encoding in ENCODING_CANDIDATES:
        try:
            # sep=None + engine="python": ayirici (virgul/noktali virgul/tab) Jira
            # disa aktarim ayarina/bolgesine gore degisebildigi icin otomatik sezilir.
            return pd.read_csv(path, encoding=encoding, sep=None, engine="python")
        except UnicodeDecodeError as exc:
            last_error = exc
        except pd.errors.ParserError as exc:
            last_error = exc
    raise ValueError(
        f"'{path}' desteklenen kodlamalardan hicbiriyle ({ENCODING_CANDIDATES}) okunamadi: {last_error}"
    )


def _read_excel_report(file_path: str | Path) -> pd.DataFrame:
    sheets = pd.read_excel(file_path, sheet_name=None)
    tables = list(sheets.values())
    if not tables:
        raise ValueError(f"'{file_path}' icinde okunabilir bir sayfa bulunamadi.")
    return tables[0] if len(tables) == 1 else _select_issue_table(tables)


def read_sprint_report(file_path: str | Path) -> pd.DataFrame:
    """Jira sprint/iterasyon raporunu okuyup ham (standardize edilmemis) bir DataFrame
    doner. Dosya uzantisina gore uc format desteklenir: `.html`/`.htm` (Jira'nin HTML
    disa aktarimi, birden fazla tablo varsa Jira'ya ozgu basliklara en cok benzeyen
    tablo secilir - bkz. `_select_issue_table`), `.csv` (ayirici otomatik sezilir,
    birden fazla kodlama denenir) ve `.xlsx` (birden fazla sayfa varsa yine
    `_select_issue_table` ile en uygun sayfa secilir). Desteklenmeyen bir uzanti
    verilirse acik bir `ValueError` firlatir.
    """
    suffix = Path(file_path).suffix.lower()
    if suffix in (".html", ".htm"):
        return _read_html_report(file_path)
    if suffix == ".csv":
        return _read_csv_report(file_path)
    if suffix == ".xlsx":
        return _read_excel_report(file_path)
    raise ValueError(
        f"'{file_path}' desteklenmeyen bir dosya turu ('{suffix or 'uzantisiz'}'). "
        f"Desteklenen turler: {SUPPORTED_REPORT_EXTENSIONS}."
    )


# --------------------------------------------------------------------------
# Jira Data Center REST API'sinden canli veri cekme
# --------------------------------------------------------------------------

# Jira REST API v2 uc noktalari - Data Center'da bulut (cloud) API'sinden farkli
# olarak kimlik dogrulama kullanici adi/e-posta GEREKTIRMEZ, sadece Personal
# Access Token (PAT) yeterlidir (bkz. _jira_auth_headers).
JIRA_API_FIELD_ENDPOINT = "rest/api/2/field"
JIRA_API_SEARCH_ENDPOINT = "rest/api/2/search"

# discover_jira_fields'in kesif adiminda ve fetch_issues_from_jira_api'nin tam
# veri cekiminde kullanilan sayfa/ornek boyutlari.
JIRA_DISCOVERY_SAMPLE_SIZE = 5
JIRA_FETCH_PAGE_SIZE = 100

# Kesif sirasinda alan adaylarini SIRALAMAK icin bakilan kart sayisi. Onizlemede
# gosterilen `JIRA_DISCOVERY_SAMPLE_SIZE` karttan daha buyuktur: 5 kart, bir
# alanin o projede gercekten kullanilip kullanilmadigini anlamak icin yetersiz
# kalabilir (orn. %54 dolulukta bir alan 5 kartin hepsinde bos cikabilir).
# Tek bir istekte cekilir - ek maliyeti yoktur.
JIRA_RANKING_SAMPLE_SIZE = 25

# Story Points/Developer/Analist ozel alanlarini Jira'nin `/field` listesinden
# TEK bir Ingilizce kelimeye gore degil, birden fazla (kucuk/buyuk harf duyarsiz,
# alt string) varyanta gore ARAR - boylece Turkce/Ingilizce karisik alan adi
# kullanan farkli Jira kurulumlarinda da (orn. "Puan", "Geliştirici") dogru
# adaylar bulunur. `discover_jira_fields`, ESLESEN TUM adaylari doner (ilkini
# secip digerlerini atmaz) - nihai secim kullaniciya (arayuzdeki selectbox'a)
# birakilir, cunku otomatik eslesme birden fazla/yanlis alana denk gelebilir.
JIRA_FIELD_CANDIDATE_VARIANTS: dict[str, list[str]] = {
    "story_points": ["story point", "puan", "sp"],
    "developer": ["developer", "geliştirici", "gelistirici"],
    "analyst": ["analist", "analyst", "analiz"],
}

# Buyuk kurumsal Jira kurulumlarinda BINLERCE ozel alan bulunur; duz alt-string
# eslesmesi tek basina ise yaramaz. Ornegin "sp" varyanti "Sponsordan Kalite
# Notu", "Sprint", "UAT Spoc" gibi onlarca alakasiz alana takilir ve gercek
# "Story Points" alani listenin cok gerisine duser. Bu yuzden adaylar SIRALANIR
# (bkz. _rank_field_candidates) ve arayuz/varsayilan secim en ustteki adayi alir.
#
# Bu tam-adlar en yuksek onceligi alir - bir alanin adi TAM olarak bunlardan
# biriyse, o alan neredeyse kesinlikle aranan alandir.
JIRA_FIELD_EXACT_NAMES: dict[str, list[str]] = {
    "story_points": ["story points", "story point", "puan"],
    "developer": ["developers", "developer"],
    "analyst": ["analists", "analists", "analyst", "analysts", "analist"],
}

# Bu uzunluktan kisa varyantlar SADECE kelime siniri ile eslesir, alt-string
# olarak degil - "sp"nin "Sponsordan"a takilmasini engelleyen kural budur.
_SHORT_VARIANT_MAX_LEN = 3


def _count_filled(issues: list[dict], field_id: str) -> int:
    """Ornek kartlarin kacinda bu alanin DOLU oldugunu sayar."""
    count = 0
    for issue in issues:
        value = (issue.get("fields") or {}).get(field_id)
        if value not in (None, "", [], {}):
            count += 1
    return count


def _rank_field_candidates(target: str, matches: list[dict], sample_issues: list[dict]) -> list[dict]:
    """Alan adaylarini "en olasi once" sirasina dizer.

    Iki olcut BIRLIKTE kullanilir, cunku tek basina ikisi de yaniltir:

    - Sadece ADA bakmak: MS projesinde hem "Developer" (customfield_10322) hem
      "Developers" (customfield_13483) vardir; adlari neredeyse aynidir ama
      yalnizca ikincisi doludur. Ad, ikisini ayirt edemez.
    - Sadece DOLULUGA bakmak: "Analiz Dokümanı Uyarı" alani her kartta dolu
      oldugu icin, asil aradigimiz "Analists" alanini geride birakir. Doluluk,
      alakasiz ama her zaman dolu alanlari one cikarir.

    Bu yuzden once ad guveni (tier), sonra doluluk uygulanir. Ek olarak, ornek
    kartlarin HICBIRINDE dolu olmayan bir alan - adi ne kadar uygun olursa olsun -
    iki kademe geri atilir: o projede kullanilmayan bir alani secmek her zaman
    yanlistir. Boylece bos "Analist" alani, dolu "Analists" alanina yenilir ama
    yine de alakasiz alanlarin onunde kalir.

    Esitlik halinde daha KISA ad once gelir - "Story Points", "Original story
    points"ten once cikar."""
    exact = JIRA_FIELD_EXACT_NAMES.get(target, [])
    variants = JIRA_FIELD_CANDIDATE_VARIANTS.get(target, [])

    def sort_key(field: dict) -> tuple[int, int, int, str]:
        name = _normalize_header(field.get("name", ""))
        if name in exact:
            tier = 0
        elif any(re.search(rf"\b{re.escape(v)}\b", name) for v in variants):
            tier = 1
        else:
            tier = 2
        filled = _count_filled(sample_issues, field.get("id", ""))
        if filled == 0 and sample_issues:
            tier += 2
        # Doluluk NEGATIF sirada - cok dolu olan basa gelsin.
        return (tier, -filled, len(name), name)

    return sorted(matches, key=sort_key)


def _field_matches_variant(field_name: str, variants: list[str]) -> bool:
    """Bir alan adinin verilen varyantlardan biriyle eslesip eslesmedigi. Kisa
    varyantlar (bkz. `_SHORT_VARIANT_MAX_LEN`) yanlis pozitif uretmemesi icin
    yalnizca kelime siniriyla aranir."""
    name = _normalize_header(field_name)
    for variant in variants:
        if len(variant) <= _SHORT_VARIANT_MAX_LEN:
            if re.search(rf"\b{re.escape(variant)}\b", name):
                return True
        elif variant in name:
            return True
    return False

# fetch_issues_from_jira_api'nin `field_id_map`'inde beklenen anahtarlar (bkz.
# JIRA_FIELD_CANDIDATE_VARIANTS'in anahtarlariyla AYNI).
JIRA_FIELD_MAP_KEYS = ("story_points", "developer", "analyst")


class JiraApiError(Exception):
    """Jira REST API'siyle iletisimde olusan bir sorunu, kullaniciya (mentore)
    DOGRUDAN gosterilebilecek, spesifik ve anlasilir bir Turkce mesajla tasir -
    genel/belirsiz bir "bir hata olustu" yerine (401/403/400/JQL/baglanti/SSL
    gibi) HER durum icin ayri, eyleme donusturulebilir bir mesaj uretilir."""


class JiraSslError(JiraApiError):
    """SSL sertifika dogrulama hatasini SPESIFIK olarak isaretler (`JiraApiError`'un
    alt sinifidir - `except JiraApiError` onu da yakalar). Arayuz, kullanici "SSL
    doğrulamayı atla" kutusunu isaretlemisse bu hatayi ayirt edip `verify=False`
    ile SESSIZCE bir kez daha deneyebilir (bkz. app/new_dashboard.py)."""


def _jira_auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _jira_error_detail(response: requests.Response) -> str:
    """Jira'nin hata yanitindaki `errorMessages`/`errors` alanlarini tek satirlik
    bir metne cevirir (yoksa bos string doner). Jira, ozellikle 400'lerde sorunun
    ne oldugunu ("The value 'MS' does not exist for the field 'project'." gibi)
    TAM olarak soyler - bu detayi yutmak yerine kullaniciya gostermek, yanlis
    yone sevk eden genel tahmin metinlerinden ("proje anahtari hatali olabilir")
    cok daha kullanislidir."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    parts = [str(m) for m in payload.get("errorMessages", []) if m]
    errors = payload.get("errors")
    if isinstance(errors, dict):
        parts.extend(f"{k}: {v}" for k, v in errors.items())
    return " ".join(parts).strip()


def _raise_for_jira_status(response: requests.Response, project_key: str) -> None:
    """HTTP durum koduna gore SPESIFIK bir `JiraApiError` firlatir (401/403/400);
    diger "basarisiz" durum kodlari icin `requests`'in kendi genel HTTPError'una
    duser. Basarili (2xx) yanitlarda hicbir sey yapmaz.

    Her durumda, varsa Jira'nin KENDI hata mesaji da eklenir (bkz.
    `_jira_error_detail`) - sunucunun soyledigi somut sebep, bizim tahminimizden
    once gelir."""
    if response.status_code not in (400, 401, 403):
        response.raise_for_status()
        return

    detail = _jira_error_detail(response)
    suffix = f" Jira'nın yanıtı: {detail}" if detail else ""

    if response.status_code == 401:
        raise JiraApiError(
            "Token geçersiz veya süresi dolmuş. Jira profilinden yeni bir Personal "
            f"Access Token oluşturun.{suffix}"
        )
    if response.status_code == 403:
        raise JiraApiError(f"Bu token'ın '{project_key}' projesine erişim yetkisi yok.{suffix}")
    raise JiraApiError(
        f"Sorgu Jira tarafından reddedildi (400). Proje anahtarı '{project_key}' hatalı "
        "olabilir ya da token'ın bu projeyi görme (Browse Projects) yetkisi yoktur - Jira, "
        "var olmayan projeyle görme yetkisi olmayan projeyi aynı şekilde raporlar. "
        f"Proje anahtarı yerine sayısal proje ID'sini de girebilirsiniz.{suffix}"
    )


def _jira_get(
    url: str, headers: dict[str, str], params: dict | None, project_key: str, verify: bool
) -> requests.Response:
    """Jira REST API'sine GET istegi atar; `requests`'in baglanti/zaman-asimi/SSL
    istisnalarini, kullaniciya gosterilecek spesifik `JiraApiError` mesajlarina
    cevirir (bkz. modul dokumantasyonu - "genel hata" yerine eyleme
    donusturulebilir mesajlar). `verify=False` ise SSL sertifika dogrulamasi
    atlanir (arayuzdeki "SSL doğrulamayı atla" secenegi icin - bkz.
    `discover_jira_fields`/`fetch_issues_from_jira_api`'nin `verify` parametresi).
    """
    try:
        response = requests.get(url, headers=headers, params=params, timeout=30, verify=verify)
    except requests.exceptions.SSLError as exc:
        raise JiraSslError("Güvenlik sertifikası doğrulanamadı.") from exc
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        raise JiraApiError(
            "Jira sunucusuna ulaşılamadı. Kurumsal ağda/VPN'de olduğunuzdan emin olun."
        ) from exc
    _raise_for_jira_status(response, project_key)
    return response


def _jql_project_term(project_key: str) -> str:
    """JQL'in `project = ...` terimini uretir. Deger TAMAMEN RAKAMSA tirnaksiz
    birakilir - Jira bu durumda onu sayisal proje ID'si olarak cozer; tirnak
    icine alinsaydi ("10500") bir proje ANAHTARI/ADI sanilip bulunamazdi.
    Anahtarin kendisi calismadiginda (orn. ayni ada sahip birden fazla proje
    veya alisilmadik anahtar) ID ile baglanabilmek icin bir kacis yoludur."""
    value = project_key.strip()
    if value.isdigit():
        return f"project = {value}"
    return f'project = "{value}"'


def jira_person_field_to_display(value: object) -> str:
    """Bir Jira kullanici alaninin (tekil kullanici veya kullanici LISTESI)
    degerini, `standardize_dataframe`'in (`_combine_multi_value_columns`)
    HTML disa aktarimindaki gibi VIRGULLE AYRILMIS TEK bir okunakli metne cevirir -
    orn. `[{"displayName": "Kullanıcı A"}, {"displayName": "Kullanıcı B"}]` ->
    `"Kullanıcı A, Kullanıcı B"`. Deger yoksa/bossa bos string doner.

    `_jira_issue_to_row` (tam veri cekiminde) VE `app/new_dashboard.py`'deki
    kesif-asamasi onizleme tablosu (kucuk ornek veri) TARAFINDAN ORTAK
    kullanilir - boylece onizlemede de tam veride de kullanici HAM Jira JSON'unu
    (`[{'displayName': ...}]`) DEGIL, okunakli isimleri gorur. Duz metin bir
    kullanici alani degildir ve ekip hesabina alinmaz."""
    if not value:
        return ""
    if isinstance(value, list):
        names = [jira_person_field_to_display(item) for item in value]
        return ", ".join(n for n in names if n)
    if isinstance(value, dict):
        return str(value.get("displayName") or value.get("name") or "")
    # Jira user-picker values are objects or lists of objects. A plain string
    # here belongs to a text custom field, not to a person picker. File imports
    # already parse their plain-text names in standardize_dataframe.
    return ""


# Jira'nin Sprint alani her kurulumda farkli bir `customfield_XXXXX` ID'sine
# sahiptir ama ADI standarttir - bu yuzden Story Points/Developer/Analist'in
# aksine kullaniciya SORULMAZ, ad uzerinden otomatik bulunur (bkz.
# `find_sprint_field_id`). `.env`'deki JIRA_FIELD_SPRINT ile elle de verilebilir.
JIRA_SPRINT_FIELD_NAMES = ("sprint", "sprints")

# Kartin son statu degisikliginin zamani - haftalik analizin dayandigi alan
# (bkz. COLUMN_ALIASES["last_transition"]). Sprint gibi, adi standart oldugu icin
# kullaniciya sorulmaz, `/field` uzerinden ada gore bulunur.
JIRA_LAST_TRANSITION_FIELD_NAMES = ("last transition", "son geçiş", "son gecis")

# Jira Data Center'in eski Sprint temsili duz metindir:
# "com.atlassian.greenhopper.service.sprint.Sprint@1a2b[id=42,name=MS Sprint - Temmuz 26,...]"
_SPRINT_TOSTRING_NAME = re.compile(r"name=([^,\]]+)")


def find_field_id_by_name(all_fields: object, names: tuple[str, ...]) -> str | None:
    """Jira'nin `/rest/api/2/field` ciktisinda adi `names`'ten biriyle eslesen
    alanin ID'sini doner; bulunamazsa None.

    Alan ID'leri (`customfield_XXXXX`) her Jira kurulumunda farklidir ama bazi
    alanlarin ADI standarttir (Sprint, Last Transition) - bunlar Story Points/
    Developer/Analist'in aksine kullaniciya SORULMAZ, ad uzerinden bulunur.

    Girdinin BEKLENEN bicimde (sozluk listesi) gelmedigi durumlar da sessizce
    None ile karsilanir: bu fonksiyon "bulabilirse iyi" mantigiyla cagrilir, veri
    cekiminin tamamini bir alan kesfi yuzunden dusurmemelidir."""
    if not isinstance(all_fields, list):
        return None
    for field in all_fields:
        if not isinstance(field, dict):
            continue
        if _normalize_header(field.get("name")) in names:
            field_id = field.get("id")
            if field_id:
                return str(field_id)
    return None


def find_sprint_field_id(all_fields: object) -> str | None:
    """Sprint alaninin ID'si (bkz. `find_field_id_by_name`). Bulunamazsa ay
    atamasi `created` tarihine duser - bkz. `_row_month_keys`."""
    return find_field_id_by_name(all_fields, JIRA_SPRINT_FIELD_NAMES)


def find_last_transition_field_id(all_fields: object) -> str | None:
    """Last Transition alaninin ID'si (bkz. `find_field_id_by_name`).
    Bulunamazsa haftalik analiz calisamaz ama diger tum raporlar etkilenmez."""
    return find_field_id_by_name(all_fields, JIRA_LAST_TRANSITION_FIELD_NAMES)


def jira_sprint_field_to_display(value: object) -> str:
    """Bir kartin Sprint alanini, CSV disa aktarimindaki gibi VIRGULLE AYRILMIS
    tek bir metne cevirir - `standardize_dataframe` boylece API'den gelen veriyi
    CSV'den gelenle AYNI sekilde isler.

    Jira surumune gore uc bicim de karsilanir: nesne listesi
    (`[{"name": "MS Sprint - Temmuz 26"}]`), duz metin listesi ve Data Center'in
    `Sprint@...[id=..,name=..,..]` toString bicimi."""
    if not value:
        return ""
    if isinstance(value, list):
        names = [jira_sprint_field_to_display(item) for item in value]
        return ", ".join(n for n in names if n)
    if isinstance(value, dict):
        return str(value.get("name") or "")
    text = str(value)
    match = _SPRINT_TOSTRING_NAME.search(text)
    return match.group(1).strip() if match else text.strip()


def discover_jira_fields(base_url: str, token: str, project_key: str, verify: bool = True) -> dict:
    """Jira'ya baglanip TUM alan listesini VE kucuk bir ornek veri setini (son 5
    kart) doner - `fetch_issues_from_jira_api` ile TAM veri cekmeden ONCE,
    kullanicinin (mentorun) alan eslestirmesini gozle dogrulamasi/duzeltmesi
    icindir (bkz. modul basindaki "Kesif ve Onay" akisi - proje sahibiyle
    iletisime gecmeden kendi ekranindan duzeltebilmesi icin).

    Donen `dict`:
        - `all_fields`: Jira'nin `/rest/api/2/field` uc noktasindan donen HAM
          alan listesi (`[{"id": "customfield_10010", "name": "Story Points"},
          ...]`) - arayuzdeki selectbox'larin "TUM alanlar arasindan ELLE sec"
          secenegi icin.
        - `candidates`: `JIRA_FIELD_CANDIDATE_VARIANTS`'a gore otomatik eslesen
          adaylari (`{"story_points": [...], "developer": [...], "analyst":
          [...]}` - her biri `all_fields` ile ayni `{"id", "name"}` bicimli bir
          liste) tasir; hicbir varyantla eslesmezse ilgili anahtar bos liste
          olur (hata firlatilmaz - arayuz bu durumda "otomatik bulunamadi, elle
          sec" gosterir).
        - `sample_issues`: `project = "{project_key}" ORDER BY created DESC`
          sorgusunun ilk 5 sonucu, Jira'nin HAM issue JSON'u olarak (arayuz,
          kullanicinin SONRADAN sectigi alan ID'sine gore bu ham veriden onizleme
          tablosu kurar - hangi alanin secilecegi bu fonksiyon calisirken henuz
          belli olmadigindan onceden duzlestirilemez). Proje bossa/sorgu 0 sonuc
          donduruyorsa bu HATA DEGILDIR - `sample_issues` sadece bos bir liste
          olur.

    Bir baglanti/yetkilendirme/JQL sorunu olursa (401/403/400/timeout/SSL) HATA
    FIRLATMAZ - kullaniciya gosterilecek spesifik Turkce mesajla bir `JiraApiError`
    yukseltir (bkz. `_jira_get`/`_raise_for_jira_status`).
    """
    base_url = base_url.rstrip("/")
    headers = _jira_auth_headers(token)

    fields_response = _jira_get(f"{base_url}/{JIRA_API_FIELD_ENDPOINT}", headers, None, project_key, verify)
    all_fields = fields_response.json()

    # Ornek kartlar adaylardan ONCE cekilir: siralama, alan ADINA degil bu
    # kartlardaki DOLULUGA bakar (bkz. _rank_field_candidates). `fields=*all`
    # sart - varsayilan yanit tum ozel alanlari icermeyebilir ve o zaman her
    # aday "bos" gorunup siralama ada geri duserdi.
    search_response = _jira_get(
        f"{base_url}/{JIRA_API_SEARCH_ENDPOINT}",
        headers,
        {
            "jql": f"{_jql_project_term(project_key)} ORDER BY created DESC",
            "maxResults": JIRA_RANKING_SAMPLE_SIZE,
            "fields": "*all",
        },
        project_key,
        verify,
    )
    ranking_issues = search_response.json().get("issues", [])

    candidates: dict[str, list[dict]] = {}
    for target, variants in JIRA_FIELD_CANDIDATE_VARIANTS.items():
        matches = [
            {"id": f.get("id"), "name": f.get("name")}
            for f in all_fields
            if _field_matches_variant(f.get("name", ""), variants)
        ]
        # "En olasi once" - arayuz varsayilan olarak candidates[0]'i secer.
        candidates[target] = _rank_field_candidates(target, matches, ranking_issues)

    # Onizleme tablosu icin daha kucuk bir dilim - siralama icin 25 karta
    # bakariz ama kullaniciya 5 kart gostermek yeterlidir.
    sample_issues = ranking_issues[:JIRA_DISCOVERY_SAMPLE_SIZE]

    return {"all_fields": all_fields, "candidates": candidates, "sample_issues": sample_issues}


def _jira_issue_to_row(
    issue: dict,
    story_points_id: str | None,
    developer_id: str | None,
    analyst_id: str | None,
    sprint_id: str | None = None,
    last_transition_id: str | None = None,
) -> dict:
    """Jira'nin HAM issue JSON'unu, `COLUMN_ALIASES`'taki (ve Developer/Analist icin
    `DEVELOPERS_HEADER_EXACT`/`ANALYSTS_HEADER_EXACT`'taki) aday basliklarla BIREBIR
    eslesen tek bir satir sozlugune cevirir - boylece `standardize_dataframe` bu
    veriyi, HTML/CSV disa aktarimlarindan hicbir farki yokmus gibi, HIC
    DEGISTIRILMEDEN isler."""
    fields = issue.get("fields") or {}
    assignee = fields.get("assignee") or {}
    project = fields.get("project") or {}
    issuetype = fields.get("issuetype") or {}
    status = fields.get("status") or {}
    components = fields.get("components") or []
    labels = fields.get("labels") or []

    return {
        "Issue Key": issue.get("key", "") or "",
        "Issue Type": issuetype.get("name", "") or "",
        "Summary": fields.get("summary", "") or "",
        "Status": status.get("name", "") or "",
        "Assignee": assignee.get("displayName", "") or "",
        "Project": project.get("name", "") or "",
        "Component/s": ", ".join(c.get("name", "") for c in components if c.get("name")),
        "Labels": ", ".join(str(l) for l in labels if l),
        "Created": fields.get("created", "") or "",
        "Resolved": fields.get("resolutiondate", "") or "",
        "Story Points": fields.get(story_points_id, "") if story_points_id else "",
        "Developers": jira_person_field_to_display(fields.get(developer_id)) if developer_id else "",
        "Analysts": jira_person_field_to_display(fields.get(analyst_id)) if analyst_id else "",
        "Sprint": jira_sprint_field_to_display(fields.get(sprint_id)) if sprint_id else "",
        "Last Transition": (fields.get(last_transition_id, "") or "") if last_transition_id else "",
    }


def _lookup_auto_field_ids(
    base_url: str, headers: dict[str, str], project_key: str, verify: bool
) -> dict[str, str | None]:
    """Kullaniciya SORULMADAN, adi uzerinden bulunan alanlarin ID'lerini `/field`
    uc noktasindan TEK bir istekle toplar: `{"sprint": ..., "last_transition": ...}`.

    Story Points/Developer/Analist'in aksine bu iki alanin adi Jira kurulumlari
    arasinda standarttir, bu yuzden alan eslestirme ekraninda sorulmazlar
    (`fetch_issues_from_jira_api` bunlari `field_id_map`'te bulamazsa buraya duser).
    Iki alan icin ayri ayri degil, tek istekte bakilir.

    Bu istek BASARISIZ OLURSA sessizce bos harita doner ve veri cekimi bu alanlar
    olmadan devam eder - ikisi de raporun dogrulugunu artiran alanlardir ama onlar
    yuzunden tum cekimi basarisiz saymak kullanici acisindan daha kotu bir sonuc
    olur. Sprint yoksa ay atamasi `created` tarihine duser (bkz. `_row_month_keys`);
    Last Transition yoksa haftalik analiz calismaz (bkz. `src/weekly_report.py`)
    ama diger raporlar etkilenmez."""
    try:
        response = _jira_get(
            f"{base_url}/{JIRA_API_FIELD_ENDPOINT}", headers, None, project_key, verify
        )
        all_fields = response.json()
    except (JiraApiError, ValueError):  # baglanti/yetki hatasi veya bozuk JSON
        return {}
    except Exception:  # noqa: BLE001 - beklenmedik bicim de cekimi dusurmemeli
        return {}

    return {
        "sprint": find_sprint_field_id(all_fields),
        "last_transition": find_last_transition_field_id(all_fields),
    }


def fetch_issues_from_jira_api(
    base_url: str,
    token: str,
    project_key: str,
    field_id_map: dict,
    months_back: int = 6,
    verify: bool = True,
) -> pd.DataFrame:
    """`discover_jira_fields` ile ONAYLANMIS alan eslestirmesini (`field_id_map` -
    `JIRA_FIELD_MAP_KEYS` ("story_points"/"developer"/"analyst") anahtarlariyla,
    degerleri Jira alan ID'si `str` veya secilmemisse `None`) kullanarak, son
    projedeki kartlari `startAt`/`maxResults` ile SAYFALAYARAK ceker. Sonra
    `months_back` penceresindeki sprint uyeligini (sprint yoksa Created tarihini)
    kullanir. Boylece eski acilip yeni sprinte tasinan kartlar kaybolmaz.

    Performans icin `fields` parametresiyle sadece gereken alanlar istenir
    (summary/issuetype/status/assignee/labels/components/created/resolutiondate/
    project + onaylanmis story_points/developer/analyst ID'leri) - tum alanlarin
    tamamini cekmekten kaçınılır.

    Donen `DataFrame`, `COLUMN_ALIASES` ile BIREBIR eslesen basliklara sahiptir
    (bkz. `_jira_issue_to_row`) - `read_sprint_report`'un urettigi ham
    DataFrame'lerle AYNI bicimde, dogrudan `standardize_dataframe`'e verilebilir.
    Proje/tarih araligi icinde hic kart yoksa (HATA DEGIL) bos (0 satirli, yine
    de dogru kolonlu) bir `DataFrame` doner.

    Bir baglanti/yetkilendirme/JQL sorunu olursa `JiraApiError` yukseltir (bkz.
    `discover_jira_fields`).
    """
    base_url = base_url.rstrip("/")
    headers = _jira_auth_headers(token)

    start_date = (pd.Timestamp.now().normalize() - pd.DateOffset(months=months_back)).strftime("%Y-%m-%d")
    # A card created long ago can still belong to a recent sprint. Fetch the
    # project before applying the window to sprint membership below.
    jql = f'{_jql_project_term(project_key)} ORDER BY created ASC'

    story_points_id = field_id_map.get("story_points")
    developer_id = field_id_map.get("developer")
    analyst_id = field_id_map.get("analyst")
    # Sprint ve Last Transition kullaniciya sorulmaz; eslestirmede yoksa tek bir
    # `/field` istegiyle adlarindan bulunur (bkz. `_lookup_auto_field_ids`).
    sprint_id = field_id_map.get("sprint")
    last_transition_id = field_id_map.get("last_transition")
    if not (sprint_id and last_transition_id):
        auto_ids = _lookup_auto_field_ids(base_url, headers, project_key, verify)
        sprint_id = sprint_id or auto_ids.get("sprint")
        last_transition_id = last_transition_id or auto_ids.get("last_transition")

    requested_fields = [
        "summary", "issuetype", "status", "assignee", "labels",
        "components", "created", "resolutiondate", "project",
    ]
    requested_fields.extend(
        fid
        for fid in (story_points_id, developer_id, analyst_id, sprint_id, last_transition_id)
        if fid
    )

    rows: list[dict] = []
    ignored_text_person_fields: set[str] = set()
    start_at = 0
    total: int | None = None
    while total is None or start_at < total:
        response = _jira_get(
            f"{base_url}/{JIRA_API_SEARCH_ENDPOINT}",
            headers,
            {
                "jql": jql,
                "startAt": start_at,
                "maxResults": JIRA_FETCH_PAGE_SIZE,
                "fields": ",".join(requested_fields),
            },
            project_key,
            verify,
        )
        payload = response.json()
        total = payload.get("total", 0)
        issues = payload.get("issues", [])
        if not issues:
            break
        for issue in issues:
            fields = issue.get("fields") or {}
            for role, field_id in (("Developer", developer_id), ("Analyst", analyst_id)):
                if field_id and isinstance(fields.get(field_id), str) and fields[field_id].strip():
                    ignored_text_person_fields.add(f"{role}: {field_id}")
        rows.extend(
            _jira_issue_to_row(
                issue, story_points_id, developer_id, analyst_id, sprint_id, last_transition_id
            )
            for issue in issues
        )
        start_at += JIRA_FETCH_PAGE_SIZE

    export_columns = [
        "Issue Type", "Summary", "Status", "Assignee", "Project", "Component/s",
        "Labels", "Created", "Resolved", "Story Points", "Developers", "Analysts",
        "Sprint", "Last Transition",
    ]
    result = pd.DataFrame(rows, columns=export_columns)
    if result.empty:
        return result
    normalized = standardize_dataframe(result)
    cutoff = pd.Period(start_date[:7], freq="M")
    recent_sprint = normalized["sprint_months"].map(
        lambda months: any(pd.Period(month, freq="M") >= cutoff for month in months)
    )
    recent_created = normalized["created"].ge(pd.Timestamp(start_date)).fillna(False)
    # Created is a fallback only when no sprint month can be resolved. This
    # matches _row_month_keys and keeps old cards in recent iterations.
    keep = recent_sprint | (normalized["sprint_months"].map(lambda months: not months) & recent_created)
    result = result.loc[keep.to_numpy()].reset_index(drop=True)
    result.attrs["ignored_text_person_fields"] = sorted(ignored_text_person_fields)
    return result


def _parse_jira_date(series: pd.Series) -> pd.Series:
    """Bir Jira tarih sutununu (`created`, `resolved` vb.) once bilinen Jira bicimiyle
    (`04-Aug-25 10:15`), sonra (varsa) tanimadigi kalan degerler icin genel bir tarih
    ayristiriciyla parse eder. Sutun tamamen bos/yoksa (orn. bu veri setinde `resolved`)
    tum degerler icin NaT doner, hata firlatmaz.

    `fetch_issues_from_jira_api`'nin urettigi ISO 8601 (`created`/`resolutiondate`)
    degerleri saat dilimi bilgisi TASIR (orn. "+0300") - genel ayristirici bunlari
    saat-dilimi-FARKINDA (tz-aware) bir seriye cevirir. Bu, panelin geri kalaninin
    (orn. WIP Aging'deki `df["created"] <= reference_date` gibi, HER ZAMAN saat-
    dilimsiz/naive `pd.Timestamp` ile karsilastirmalar) VARSAYDIGI saat-dilimsiz
    modelle CELISIR ve `TypeError: Cannot compare tz-naive and tz-aware
    timestamps` ile CRASH EDER. Bu yuzden geri donus (fallback) ayristirmasi
    tz-farkinda cikarsa saat dilimi bilgisi ATILIR (`tz_localize(None)`) -
    degerler YEREL SAATE gore naive'e indirgenir, boylece HTML/CSV'den gelen
    (zaten naive) tarihlerle AYNI modelde kalinir.
    """
    parsed = pd.to_datetime(series, format=CREATED_DATE_FORMAT, errors="coerce")

    unmatched = parsed.isna() & series.notna() & (series.astype(str).str.strip() != "")
    if unmatched.any():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            fallback_parsed = pd.to_datetime(series[unmatched], errors="coerce")
            if isinstance(fallback_parsed.dtype, pd.DatetimeTZDtype):
                fallback_parsed = fallback_parsed.dt.tz_localize(None)
            parsed.loc[unmatched] = fallback_parsed

    return parsed


# Jira, coklu-degerli ozel alanlari (bir kartin birden fazla Developer/Analist
# atanabilmesi) DISA AKTARIM FORMATINA GORE IKI FARKLI SEKILDE temsil eder - bu
# yuzden hem TAM eslesme hem ON EK eslesmesi birlikte aranir:
#   - CSV/XLSX: her deger AYRI bir kolona boler, orn. "Custom field
#     (Developers)", ".1", ".2"... (ON EK eslesmesi gerekir - bkz. *_PREFIXES).
#   - HTML: TEK bir kolonda ("Developers"/"Analists") gelir, birden fazla isim
#     TEK BIR HUCREDE virgulle ayrilmis olabilir, orn. "KULLANICI A, KULLANICI
#     B" (TAM eslesme gerekir - bkz. *_EXACT; hucre daha sonra
#     `_combine_multi_value_columns` icinde virgulle bolunur).
# Jira'nin GERCEK ozel alan adi "Analists" (yanlis yazim - Ingilizce dogrusu
# "Analysts") oldugundan, COLUMN_ALIASES'taki gibi normallestirilmis baslikta
# HER IKI yazim da (analists/analysts) aranir.
DEVELOPERS_HEADER_EXACT = ("developers",)
DEVELOPERS_HEADER_PREFIXES = ("custom field (developers",)
ANALYSTS_HEADER_EXACT = ("analists", "analysts")
ANALYSTS_HEADER_PREFIXES = ("custom field (analists",)

# Jira'nin "Sprint" alani da coklu-degerlidir (devreden bir kart, icinde yer
# aldigi HER sprint icin bir deger tasir - veride 14 sprinte kadar cikiyor) ve
# CSV disa aktariminda ayni ada sahip ARDISIK kolonlara bolunur; pandas bunlari
# "Sprint", "Sprint.1", "Sprint.2"... diye tekillestirir - bu yuzden Developer/
# Analist ile AYNI tam-eslesme + on-ek eslesmesi mantigi kullanilir.
SPRINT_HEADER_EXACT = ("sprint", "sprints")
SPRINT_HEADER_PREFIXES = ("sprint.", "custom field (sprint")


def _combine_multi_value_columns(
    df: pd.DataFrame, exact_names: tuple[str, ...], prefixes: tuple[str, ...]
) -> pd.Series:
    """`df.columns` icinde normallestirilmis basligi `exact_names`'ten biriyle TAM
    eslesen (orn. HTML'nin tekil "Developers"/"Analists" kolonu) VEYA
    `prefixes`'ten biriyle BASLAYAN (orn. CSV'nin "Custom field (Developers)",
    ".1", ".2"... kolonlari) TUM kolonlari bulup, her satir icin bu kolonlardaki
    degerleri tek bir listede birlestirir.

    Her HUCRE degeri once VIRGULLE bolunur (`str.split(",")`), her parca strip
    edilip bos olanlar atilir - boylece HTML'de tek bir hucrede birden fazla
    ismin virgulle ayrilmis gelebilecegi durum (orn. "KULLANICI A, KULLANICI
    B" -> ["KULLANICI A", "KULLANICI B"]) da dogru ayristirilir.
    CSV'de zaten her hucre tek isim oldugundan (virgul icermez) bu bolme
    ZARARSIZDIR - tek elemanli bir liste doner.

    Eslesen kolon yoksa (orn. eski/farkli bir Jira export'unda bu alan hic
    bulunmuyorsa) her satir icin bos bir liste doner, hata firlatmaz."""

    def _matches(normalized_header: str) -> bool:
        return normalized_header in exact_names or any(
            normalized_header.startswith(prefix) for prefix in prefixes
        )

    columns = [c for c in df.columns if _matches(_normalize_header(c))]
    if not columns:
        return pd.Series([[] for _ in range(len(df))], index=df.index)

    def _collect(row: pd.Series) -> list[str]:
        values: list[str] = []
        for cell in row:
            if pd.isna(cell):
                continue
            values.extend(part.strip() for part in str(cell).split(",") if part.strip())
        return values

    return df[columns].apply(_collect, axis=1)


def _ascii_fold_tr(text: object) -> str:
    """Turkce karakterleri ASCII'ye indirger ve kucuk harfe cevirir (bkz.
    `_TR_ASCII_TABLE`) - "MS Sprint - Ağustos 26" ve "MS Sprint - Agustos 26"
    ayni sonucu verir."""
    return str(text).translate(_TR_ASCII_TABLE).lower()


def _parse_sprint_label(label: str) -> tuple[int, str] | None:
    """Bir sprint adindan `(ay_numarasi, yil_jetonu)` cikarir; ay adi
    bulunamazsa None doner (orn. "Sprint 2", "MS Sprint 3" - bu adlar hicbir aya
    baglanamaz). Yil jetonu HAM string olarak doner cunku 1 haneli (kesik) olma
    ihtimali vardir ve cozumu tum veri setinin baglamini gerektirir - bkz.
    `build_sprint_period_map`."""
    match = SPRINT_MONTH_PATTERN.search(_ascii_fold_tr(label))
    if not match:
        return None
    return MONTH_NUMBERS_TR[match.group(1)], match.group(2)


def build_sprint_period_map(labels: Iterable[str]) -> dict[str, pd.Period]:
    """Veride gecen TUM sprint adlarini tarayip her birini bir aya (`pd.Period`)
    baglayan bir sozluk uretir. Ay adi tasimayan adlar (orn. "Sprint 2") sonuca
    HIC girmez - bu kartlar `filter_by_month` icinde `created` tarihine duser.

    KESIK YIL COZUMU: veride "MS Sprint - Nisan 2" gibi, disa aktarim sirasinda
    yili kirpilmis adlar bulunur. Tek haneli bir yil jetonu, veride TAM olarak
    gecen yillar arasindan o rakamla BASLAYANLARA aday olarak bakilarak cozulur;
    aday yillardan o ay icin ZATEN tam yazilmis bir sprint adi bulunanlar elenir
    (ayni ay/yil iki farkli adla temsil edilemez). Geriye TEK bir aday kalirsa o
    secilir, aksi halde ad cozumsuz birakilir (uydurma bir yila baglanmaz).
    Ornek: "Nisan 2" -> adaylar 2025/2026; "Nisan 25" veride tam yazili oldugu
    icin 2025 elenir -> Nisan 2026.
    """
    parsed: dict[str, tuple[int, str]] = {}
    for label in {str(x).strip() for x in labels if str(x).strip()}:
        result = _parse_sprint_label(label)
        if result is not None:
            parsed[label] = result

    def _full_year(token: str) -> int | None:
        if len(token) == 4:
            return int(token)
        if len(token) == 2:
            return 2000 + int(token)
        return None

    period_map: dict[str, pd.Period] = {}
    known_years: set[int] = set()
    claimed: set[tuple[int, int]] = set()  # tam yazilmis (ay, yil) ciftleri
    for label, (month, token) in parsed.items():
        year = _full_year(token)
        if year is None:
            continue
        period_map[label] = pd.Period(year=year, month=month, freq="M")
        known_years.add(year)
        claimed.add((month, year))

    for label, (month, token) in parsed.items():
        if label in period_map:
            continue
        candidates = [
            year
            for year in known_years
            if str(year % 100).startswith(token) and (month, year) not in claimed
        ]
        if len(candidates) == 1:
            period_map[label] = pd.Period(year=candidates[0], month=month, freq="M")

    return period_map


def _sprint_month_keys(sprint_lists: pd.Series, period_map: dict[str, pd.Period]) -> pd.Series:
    """Her satirin sprint adi listesini, cozulebilen aylarin `"YYYY-MM"`
    anahtarlarindan olusan (tekrarsiz, sirali) bir listeye cevirir. Period
    nesnesi yerine duz string tutulur - liste iceren kolonlar Streamlit/Arrow
    tarafinda serilestirildiginden `developers`/`analysts` ile ayni sekilde
    guvenli kalir."""

    def _keys(labels: object) -> list[str]:
        if not isinstance(labels, list):
            return []
        return sorted({str(period_map[l]) for l in labels if l in period_map})

    return sprint_lists.map(_keys)


def standardize_dataframe(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Ham DataFrame'i kanonik kolon adlarina cevirir, eksik/bos verileri guvenli sekilde doldurur.

    Iki farkli Jira disa aktarim formatini birlikte destekler: CSV/XLSX'in
    coklu-sutun stilini ("Custom field (Developers)", ".1", ".2"...) VE
    HTML'nin tekil-sutun/virgulle-ayrilmis-hucre stilini ("Developers" kolonunda
    "KULLANICI A, KULLANICI B" gibi) - bkz. `_combine_multi_value_columns`.
    """
    df = raw_df.copy()
    df.columns = _flatten_columns(df.columns)

    # COLUMN_ALIASES tekil-degerli kolonlari kapsadigindan, coklu-degerli
    # Developer/Analist kolonlari asagidaki `df = df[list(COLUMN_ALIASES.keys())]`
    # satirindan ONCE (kolonlar hala mevcutken) ayri olarak toplanir, sonra tekrar eklenir.
    developers_series = _combine_multi_value_columns(df, DEVELOPERS_HEADER_EXACT, DEVELOPERS_HEADER_PREFIXES)
    analysts_series = _combine_multi_value_columns(df, ANALYSTS_HEADER_EXACT, ANALYSTS_HEADER_PREFIXES)
    sprint_series = _combine_multi_value_columns(df, SPRINT_HEADER_EXACT, SPRINT_HEADER_PREFIXES)

    normalized_lookup = {_normalize_header(c): c for c in df.columns}

    rename_map: dict[str, str] = {}
    missing: list[str] = []
    for canonical, aliases in COLUMN_ALIASES.items():
        match = next((normalized_lookup[a] for a in aliases if a in normalized_lookup), None)
        if match is not None:
            rename_map[match] = canonical
        elif canonical in REQUIRED_COLUMNS:
            missing.append(canonical)

    if missing:
        raise ValueError(f"Raporda zorunlu kolonlar bulunamadi: {missing}")

    df = df.rename(columns=rename_map)
    for canonical in COLUMN_ALIASES:
        if canonical not in df.columns:
            df[canonical] = 0.0 if canonical == "estimate" else ""
    df = df[list(COLUMN_ALIASES.keys())]
    df["developers"] = developers_series
    df["analysts"] = analysts_series
    df["sprint"] = sprint_series
    df["sprint_months"] = _sprint_month_keys(
        sprint_series, build_sprint_period_map(sprint_series.explode().dropna())
    )

    for text_col in ("issue_key", "issue_type", "summary", "status", "labels", "assignee", "project", "component"):
        df[text_col] = df[text_col].fillna("").astype(str).str.strip()

    df["estimate"] = pd.to_numeric(df["estimate"], errors="coerce").fillna(0.0)
    df["created"] = _parse_jira_date(df["created"])
    df["resolved"] = _parse_jira_date(df["resolved"])
    df["last_transition"] = _parse_jira_date(df["last_transition"])

    return df.reset_index(drop=True)


# --------------------------------------------------------------------------
# Filtreleme (Label bazli)
# --------------------------------------------------------------------------


def _fallback_sprint_disi_mask(df: pd.DataFrame) -> pd.Series:
    """Secili sprint baslangicindan bir gun sonra olusturulan kartlari bulur.

    Jira exportu gercek sprint startDate bilgisini tasimadigi icin sprint adindan
    cozulmus takvim ayinin ilk gunu baslangic kabul edilir. Karsilastirma tarih
    bazlidir: ayin ilk iki gunu tolerans, ayin 3'u ve sonrasi plan disidir.
    Analiz ayi `filter_by_month` attribute'undan veya aylik hesaplamalardaki
    `_period` kolonundan gelir; baglam yoksa guvenli bicimde fallback uygulanmaz.
    """
    mask = pd.Series(False, index=df.index, dtype=bool)
    if not sprint_disi_fallback_enabled() or "created" not in df.columns or df.empty:
        return mask

    if "_period" in df.columns:
        periods = df["_period"].map(
            lambda value: value if isinstance(value, pd.Period) else pd.Period(value, freq="M")
        )
        starts = periods.map(lambda period: period.start_time)
    else:
        period_value = df.attrs.get("analysis_period")
        if period_value is None:
            return mask
        period = (
            period_value
            if isinstance(period_value, pd.Period)
            else pd.Period(period_value, freq="M")
        )
        starts = pd.Series(period.start_time, index=df.index)

    created = pd.to_datetime(df["created"], errors="coerce").dt.tz_localize(None).dt.normalize()
    cutoff = pd.to_datetime(starts).dt.tz_localize(None).map(
        lambda start: start + pd.DateOffset(days=1)
    )
    return created.notna() & (created > cutoff)


def _is_sprint_disi(labels_or_df: pd.Series | pd.DataFrame) -> pd.Series:
    """Label'i ve etkinse Created-tarihi fallback'ini birlikte degerlendirir."""
    if isinstance(labels_or_df, pd.DataFrame):
        labels = labels_or_df["labels"]
        return (
            labels.astype(str).str.contains(SPRINT_DISI_PATTERN, na=False)
            | _fallback_sprint_disi_mask(labels_or_df)
        )
    # Geriye uyumluluk: yalniz Series verilen dahili/eski cagrilar label kontrolu yapar.
    return labels_or_df.astype(str).str.contains(SPRINT_DISI_PATTERN, na=False)


def _is_done(status: pd.Series) -> pd.Series:
    return status.astype(str).str.strip().str.casefold() == DONE_STATUS


def filter_planned_issues(df: pd.DataFrame) -> pd.DataFrame:
    """Label/fallback kuralina gore planlanan kartlari doner."""
    return df.loc[~_is_sprint_disi(df)].reset_index(drop=True)


def filter_out_of_plan_issues(df: pd.DataFrame) -> pd.DataFrame:
    """Label/fallback kuralina gore plan disi kartlari doner."""
    return df.loc[_is_sprint_disi(df)].reset_index(drop=True)


# --------------------------------------------------------------------------
# Filtreleme (Sorumlu / Proje bazli)
# --------------------------------------------------------------------------


def _filter_by_text_column(df: pd.DataFrame, column: str, value: str | None) -> pd.DataFrame:
    """`column`'daki degeri `value` ile (buyuk/kucuk harf duyarsiz, kismi eslesme)
    filtreler. `value` verilmezse tum `df`'i doner; `column` yoksa/eslesen kayit
    bulunamazsa BOS bir DataFrame doner (aksine `filter_by_month`, hicbir zaman
    sessizce filtrelenmemis tum veriye geri donmez - boylece "boyle bir kisi/proje
    yok" durumu acikca gorulur)."""
    if not value:
        return df
    if column not in df.columns:
        return df.iloc[0:0]

    normalized = _normalize_header(value)
    mask = df[column].map(_normalize_header).str.contains(normalized, na=False, regex=False)
    return df.loc[mask].reset_index(drop=True)


def filter_by_assignee(df: pd.DataFrame, assignee: str | None = None) -> pd.DataFrame:
    """`assignee` (sorumlu) kolonuna gore filtreler (orn. "Kullanıcı A" -> "KULLANICI A SOYADI"
    ile eslesir). `assignee` verilmezse tum `df`'i, eslesen kimse bulunamazsa bos bir
    `DataFrame` doner."""
    return _filter_by_text_column(df, "assignee", assignee)


def _list_column_contains(values: object, normalized_query: str) -> bool:
    if not isinstance(values, list):
        return False
    return any(normalized_query in _normalize_header(v) for v in values)


def filter_by_person(df: pd.DataFrame, person: str | None = None) -> pd.DataFrame:
    """`filter_by_assignee`'nin GENISLETILMIS hali: bir kart, `person` `assignee`
    kolonunda GECMESE bile, o kartin coklu-degerli `developers`/`analysts`
    (bkz. `standardize_dataframe`) listelerinden BIRINDE geciyorsa yine eslesir -
    boylece sadece Developer/Analist olarak atanmis (hic assignee olmamis) bir
    kisi de "kendi kartlarini" bulabilir; `assignee` olarak atanmis biri icin
    davranis `filter_by_assignee` ile AYNIDIR.

    Bir kart, `person` birden fazla rolde (orn. hem assignee HEM developer) gecse
    bile SONUCTA SADECE BIR KEZ gorunur (bu bir satir-maskesi filtresidir, satir
    coğaltmaz) - `explode_by_role`'un aksine (o, KASITLI olarak is yuku
    agregasyonu icin satirlari birden fazla kisiye bolup coğaltir).

    `person` verilmezse tum `df`'i, eslesen kimse (ne assignee ne developer ne
    analist olarak) bulunamazsa bos bir `DataFrame` doner.
    """
    if not person:
        return df

    normalized = _normalize_header(person)
    assignee_mask = df["assignee"].map(_normalize_header).str.contains(normalized, na=False, regex=False)

    mask = assignee_mask
    for role_column in ("developers", "analysts"):
        if role_column in df.columns:
            mask = mask | df[role_column].map(lambda values: _list_column_contains(values, normalized))

    return df.loc[mask].reset_index(drop=True)


def filter_by_project(df: pd.DataFrame, project: str | None = None) -> pd.DataFrame:
    """`project` kolonuna gore filtreler. `project` verilmezse tum `df`'i, eslesen
    proje bulunamazsa bos bir `DataFrame` doner."""
    return _filter_by_text_column(df, "project", project)


def drop_duplicate_rows(df: pd.DataFrame) -> pd.DataFrame:
    """`df.drop_duplicates()`'in, LISTE iceren kolonlara dayanikli hali.

    Standardize edilmis veride coklu-degerli alanlar Python listesi olarak tutulur
    (`developers`, `analysts`, `sprint`, `sprint_months`). pandas bir listeyi
    hash'leyemedigi icin duz `drop_duplicates()` bu kolonlar varken
    `TypeError: unhashable type: 'list'` ile duser. Burada tekillestirme SADECE
    hash'lenebilir kolonlar uzerinden yapilir; liste kolonlari sonuctan SILINMEZ,
    yalnizca ANAHTARA girmez.

    Liste kolonlari zaten karttan turedigi icin ayni kart icin hep ayni degeri
    tasir - onlari anahtardan cikarmak sonucu degistirmez. Kolonlarin korunmasi
    ise onemlidir: orn. `sprint_months` silinseydi, sonucu kullanan
    `filter_by_month` ay atamasini sprint yerine sessizce `created` tarihinden
    yapardi (bkz. `_row_month_keys`).

    Hic hash'lenebilir kolon yoksa `df` oldugu gibi doner (tekillestirilecek bir
    anahtar yoktur).
    """
    hashable_columns = [
        column
        for column in df.columns
        if not df[column].map(lambda value: isinstance(value, list)).any()
    ]
    if not hashable_columns:
        return df
    return df.drop_duplicates(subset=hashable_columns)


def explode_by_role(df: pd.DataFrame, role_column: str, fallback_to_assignee: bool = True) -> pd.DataFrame:
    """`role_column` (`"developers"` veya `"analysts"`) kolonundaki coklu-deger
    listesini "patlatip" (explode) her kisi icin AYRI bir satir + yeni bir "person"
    kolonu ekler - boylece bir kartin orn. 3 developer'i varsa, o kartin is yuku
    3 kisinin de uzerine BOLUNMEDEN (her biri icin TAM SP/kart olarak) sayilir.

    `fallback_to_assignee` `True` ise (varsayilan), `role_column` listesi BOS olan
    kartlarda (o rol icin kimse atanmamissa) `assignee` tek elemanli bir liste gibi
    kullanilir - boylece rol bazli bir gorunumde bu kartlar tamamen kaybolmaz.
    `False` ise bu kartlar (o role kimse atanmadigi icin) sonuca hic girmez.

    `role_column`/`assignee` bos stringse (kimse atanmamis VE fallback da bossa) o
    satir sonuca dahil edilmez. Donen `DataFrame`, `df`'in tum orijinal kolonlarina
    ek olarak bir "person" kolonu icerir.
    """
    working = df.copy()

    def _resolve_people(row: pd.Series) -> list[str]:
        values = row[role_column]
        if isinstance(values, list) and values:
            return values
        if fallback_to_assignee and str(row.get("assignee", "")).strip():
            return [row["assignee"]]
        return []

    working["person"] = working.apply(_resolve_people, axis=1)
    working = working.explode("person")
    working = working.loc[working["person"].notna() & (working["person"].astype(str).str.strip() != "")]
    return working.reset_index(drop=True)


# --------------------------------------------------------------------------
# KPI hesaplamalari
# --------------------------------------------------------------------------


def _safe_ratio(part: float, whole: float) -> float:
    return round((part / whole) * 100, 2) if whole else 0.0


def calculate_completion_rate(committed_sp: float, completed_sp: float) -> float:
    """Taahhut edilen SP'ye gore gerceklesen SP'nin tamamlanma yuzdesi."""
    return _safe_ratio(completed_sp, committed_sp)


def calculate_sprint_kpis(df: pd.DataFrame) -> SprintKPIs:
    """Taahhut edilen, gerceklesen, plan disi ve toplam tamamlanan SP degerlerini hesaplar."""
    planned = filter_planned_issues(df)
    out_of_plan = filter_out_of_plan_issues(df)

    planned_done_mask = _is_done(planned["status"])
    out_of_plan_done_mask = _is_done(out_of_plan["status"])

    committed_sp = float(planned["estimate"].sum())
    completed_sp = float(planned.loc[planned_done_mask, "estimate"].sum())
    out_of_plan_sp = float(out_of_plan.loc[out_of_plan_done_mask, "estimate"].sum())
    total_completed_sp = completed_sp + out_of_plan_sp

    return SprintKPIs(
        committed_sp=committed_sp,
        completed_sp=completed_sp,
        out_of_plan_sp=out_of_plan_sp,
        total_completed_sp=total_completed_sp,
        completion_rate=calculate_completion_rate(committed_sp, completed_sp),
        out_of_plan_rate=_safe_ratio(out_of_plan_sp, total_completed_sp),
        planned_issue_count=int(len(planned)),
        completed_issue_count=int(planned_done_mask.sum()),
        out_of_plan_issue_count=int(len(out_of_plan)),
    )


def calculate_assignee_metrics(df: pd.DataFrame, target_month: str | None = None) -> pd.DataFrame:
    """`assignee` (sorumlu) bazinda toplam is sayisi, toplam yuk (SP) ve tamamlanan SP hesaplar.

    `target_month` verilirse once o aya filtrelenir (bkz. `filter_by_month` - once SPRINT alani, yoksa `created`); verilmezse
    tum veri (tum aylar) kullanilir. Sonuc, toplam yuke gore azalan sirada doner.
    """
    scoped = filter_by_month(df, target_month) if target_month else df

    grouped = scoped.groupby("assignee")
    completed_sp = scoped.loc[_is_done(scoped["status"])].groupby("assignee")["estimate"].sum()

    result = pd.DataFrame(
        {
            "Toplam İş Sayısı": grouped["summary"].count(),
            "Toplam Yük (SP)": grouped["estimate"].sum(),
            "Tamamlanan SP": completed_sp,
        }
    ).fillna({"Tamamlanan SP": 0.0})
    result.index.name = "Sorumlu"

    return result.reset_index().sort_values("Toplam Yük (SP)", ascending=False).reset_index(drop=True)


def get_assignee_deep_dive(
    df: pd.DataFrame, assignee_name: str, target_month: str | None = None
) -> dict:
    """Belirli bir kisiye ait tum kartlara ve performans detaylarina "drill-down"
    erisim saglar - dashboard'da bir kisiye tiklandiginda gosterilecek tam detay
    raporunu uretir.

    Once `assignee_name`'e (kismi, buyuk/kucuk harf duyarsiz eslesme; bkz.
    `filter_by_person` - SADECE `assignee` kolonuyla sinirli degildir, kisi o
    kartta Developer/Analist olarak gecse de eslesir), sonra (verilmisse)
    `target_month`'a gore filtreler. Eslesen kimse bulunamazsa `bulundu=False`
    ve sifir/bos degerlerle doner (sessizce tum takima geri donulmez).

    Donen `dict`:
        - `bulundu`: eslesen en az bir kart bulunup bulunmadigi (`bool`).
        - `sorumlu`: aranan `assignee_name` (girildigi haliyle).
        - `toplam_is_sayisi`, `toplam_sp`, `tamamlanan_sp`, `tamamlanma_orani_yuzde`.
        - `gorev_listesi`: kisinin tum kartlarini `Talep Tipi`, `İş Listesi`,
          `Büyüklük (Sp)`, `Statü`, `Oluşturulma Tarihi` kolonlariyla listeleyen,
          buyuklüge gore azalan sirali bir `DataFrame`.
    """
    scoped = filter_by_person(df, assignee_name)
    scoped = filter_by_month(scoped, target_month) if target_month else scoped

    gorev_listesi = (
        pd.DataFrame(
            {
                "Talep Tipi": scoped["issue_type"],
                "İş Listesi": scoped["summary"],
                "Büyüklük (Sp)": scoped["estimate"],
                "Statü": scoped["status"],
                "Hedef Statü": scoped["summary"].map(_target_status),
                "Oluşturulma Tarihi": scoped["created"].dt.strftime("%d-%m-%Y").fillna(""),
            }
        )
        .sort_values("Büyüklük (Sp)", ascending=False)
        .reset_index(drop=True)
    )

    toplam_sp = float(scoped["estimate"].sum())
    tamamlanan_sp = float(scoped.loc[_is_done(scoped["status"]), "estimate"].sum())

    return {
        "bulundu": not scoped.empty,
        "sorumlu": assignee_name,
        "toplam_is_sayisi": int(len(scoped)),
        "toplam_sp": toplam_sp,
        "tamamlanan_sp": tamamlanan_sp,
        "tamamlanma_orani_yuzde": _safe_ratio(tamamlanan_sp, toplam_sp),
        "gorev_listesi": gorev_listesi,
    }


def calculate_status_breakdown(df: pd.DataFrame, target_month: str | None = None) -> pd.DataFrame:
    """`status` (statu/asama) bazinda is adedi ve toplam SP dagilimini hesaplar.

    `target_month` verilirse once o aya filtrelenir (bkz. `filter_by_month` - once SPRINT alani, yoksa `created`); verilmezse
    tum veri (tum aylar) kullanilir. Sonuc, is adedine gore azalan sirada doner.
    """
    scoped = filter_by_month(df, target_month) if target_month else df
    grouped = scoped.groupby("status")

    result = pd.DataFrame(
        {
            "İş Sayısı": grouped["summary"].count(),
            "Toplam SP": grouped["estimate"].sum(),
        }
    )
    result.index.name = "Statü"

    return result.reset_index().sort_values("İş Sayısı", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------
# Aylik gruplama (once SPRINT alani, yoksa Created tarihine gore - bkz. _row_month_keys)
# --------------------------------------------------------------------------


def _month_label(period: pd.Period) -> str:
    return f"{MONTH_LABELS_TR[period.month]} {period.year}"


def _row_month_keys(df: pd.DataFrame) -> pd.Series:
    """Her kartin HANGI AY(LAR)A ait sayilacagini `"YYYY-MM"` anahtarlarindan
    olusan bir liste olarak doner - tum ay bazli filtreleme/gruplamanin TEK
    kaynagi.

    Oncelik SPRINT alanindadir: rapor gereksinimi "ilgili sprinte ait kartlar"
    dedigi icin bir kart, ACILDIGI aya degil, icinde yer aldigi SPRINT(ler)in
    ayina aittir. Devreden bir kart birden fazla sprintte yer alir ve her birinde
    AYRI AYRI sayilir (gereksinim "her bir sprint icin ... toplami" dedigi icin
    bu kasitlidir).

    Sprint bilgisi olmayan ya da adindan ay cikarilamayan kartlarda (orn.
    "Sprint 2", "MS Sprint 3" veya Sprint alani bos kartlar) eski davranisa,
    yani `created` tarihinin ayina DUSULUR - boylece hicbir kart tamamen
    kaybolmaz. Ne sprint ne `created` varsa kart hicbir aya girmez."""
    has_sprint = "sprint_months" in df.columns
    has_created = "created" in df.columns

    created_keys = (
        df["created"].dt.to_period("M").map(lambda p: [] if pd.isna(p) else [str(p)])
        if has_created and pd.api.types.is_datetime64_any_dtype(df["created"])
        else pd.Series([[] for _ in range(len(df))], index=df.index)
    )
    if not has_sprint:
        return created_keys

    return pd.Series(
        [
            list(sprint) if isinstance(sprint, list) and sprint else created
            for sprint, created in zip(df["sprint_months"], created_keys)
        ],
        index=df.index,
    )


def _monthly_periods_window(
    df: pd.DataFrame, last_n_months: int | None, end_month: str | None
) -> tuple[pd.DataFrame, list[pd.Period]]:
    """Kartlari aylara ayirip (bkz. `_row_month_keys` - once SPRINT alani, yoksa
    `created`), `end_month`'a kadar (dahil) ve (verilmisse) sadece son
    `last_n_months` ayi kapsayacak sekilde bir donem penceresi olusturur.
    `calculate_monthly_kpis` ve plan disi kapasite tahmininin (bkz.
    `_monthly_committed_completed`) AYNI ay secme mantigini paylasmasi icin bu
    pencereleme mantigi ortak bir yerden yonetilir - boylece "hangi aylar dahil
    edilecek" kurali (end_month kaydirma, last_n_months kirpma) TEK bir yerde
    tanimlanir. Hicbir karta ay atanamazsa (bos DataFrame, bos donem listesi) doner.

    Birden fazla sprintte yer alan (devreden) bir kart, donen `DataFrame`'de HER
    ayi icin BIR satir olarak coğaltilir - boylece cagiran taraf tek bir
    `_period` kolonuna bakarak, kartin o ayin toplamina girmesini saglar.
    """
    keys = _row_month_keys(df)
    if not keys.map(bool).any():
        return df.iloc[0:0], []

    dated = df.copy()
    dated["_period"] = keys
    dated = dated.explode("_period")
    dated = dated.loc[dated["_period"].notna()].copy()
    dated["_period"] = dated["_period"].map(lambda key: pd.Period(key, freq="M"))

    periods = sorted(dated["_period"].unique())

    if end_month is not None:
        normalized_target = _normalize_header(end_month)
        matching_periods = [p for p in periods if normalized_target in _normalize_header(_month_label(p))]
        end_period = max(matching_periods) if matching_periods else None
        if end_period is not None:
            periods = [p for p in periods if p <= end_period]

    if last_n_months is not None:
        periods = periods[-last_n_months:]

    return dated, periods


def calculate_monthly_kpis(
    df: pd.DataFrame, last_n_months: int | None = 3, end_month: str | None = None
) -> dict[str, SprintKPIs]:
    """`created` tarihine gore kartlari aylara ayirip her ay icin ayri `SprintKPIs` hesaplar.

    `end_month` verilmezse (varsayilan), veride bulunan en guncel `last_n_months`
    ay, eskiden yeniye kronolojik sirada doner (orn. `{"Mayıs 2026": ..., "Haziran
    2026": ..., "Temmuz 2026": ...}`). `end_month` verilirse (`filter_by_month`
    ile ayni bicimde - tam etiket `"Temmuz 2026"` veya sadece ay adi `"Temmuz"`),
    pencere o aya kadar (o ay DAHIL) kaydirilir - yani donen son ay her zaman
    `end_month` olur, ondan sonraki (daha yeni) aylar dahil edilmez; `end_month`
    veride bulunamazsa yok sayilir (veride bulunan en guncel aya gore hesaplanir).
    `last_n_months=None` verilirse (end_month'a kadarki veya veride bulunan) tum
    aylar doner. `created` kolonu bulunamadiysa veya tamamen bossa (parse
    edilemediyse) bos bir sozluk doner.
    """
    dated, periods = _monthly_periods_window(df, last_n_months, end_month)
    return {
        _month_label(period): calculate_sprint_kpis(dated.loc[dated["_period"] == period])
        for period in periods
    }


def build_monthly_history(
    df: pd.DataFrame, last_n_months: int = 3, end_month: str | None = None
) -> list[tuple[str, dict]]:
    """`calculate_monthly_kpis` ciktisini `reporter.create_excel_report`'un
    `iteration_history` parametresine dogrudan verilebilecek `(ay_etiketi, summary_dict)`
    listesine cevirir (eskiden yeniye sirali). `end_month` verilirse pencere o aya
    kadar (dahil) kaydirilir - bkz. `calculate_monthly_kpis`."""
    return [
        (label, asdict(kpis))
        for label, kpis in calculate_monthly_kpis(df, last_n_months, end_month=end_month).items()
    ]


# Karsilastirma tablosunda satir olarak gosterilecek KPI alanlari ve Turkce etiketleri.
COMPARISON_METRICS: list[tuple[str, str]] = [
    ("committed_sp", "Taahhüt Edilen SP"),
    ("completed_sp", "Gerçekleşen SP"),
    ("out_of_plan_sp", "Plan Dışı SP"),
    ("total_completed_sp", "Toplam Tamamlanan SP"),
    ("completion_rate", "Tamamlanma Oranı (%)"),
    ("out_of_plan_rate", "Plan Dışı Oranı (%)"),
]


def compare_multi_sprints(df: pd.DataFrame, last_n_months: int | None = None) -> pd.DataFrame:
    """Birden fazla ayin (iterasyonun) KPI'larini yan yana karsilastiran bir tablo uretir.

    Donen `DataFrame`'de satirlar metrik (Taahhüt Edilen SP, Gerçekleşen SP, Plan Dışı SP,
    Toplam Tamamlanan SP, Tamamlanma Oranı, Plan Dışı Oranı), sutunlar ise `created`
    tarihine gore hesaplanan aylardir (eskiden yeniye sirali) - bu sayede bir metrigin
    aylar arasindaki degisimi tek bir satirdan okunabilir.

    `last_n_months` verilmezse veride bulunan tum aylar dahil edilir; verilirse sadece
    en guncel `last_n_months` ay kullanilir. `created` kolonu bulunamadiysa/tamamen
    bossa bos bir `DataFrame` doner.
    """
    monthly_kpis = calculate_monthly_kpis(df, last_n_months=last_n_months)
    if not monthly_kpis:
        return pd.DataFrame()

    data = {
        month_label: {turkish_label: getattr(kpis, key) for key, turkish_label in COMPARISON_METRICS}
        for month_label, kpis in monthly_kpis.items()
    }
    result = pd.DataFrame(data)
    result.index.name = "Metrik"
    return result


def calculate_yearly_monthly_kpis(df: pd.DataFrame, target_month: str | None = None) -> dict[str, SprintKPIs]:
    """Kartlari aylara ayirip (bkz. `_row_month_keys` - once SPRINT alani, yoksa
    `created`), `target_month`'un icinde
    bulundugu TAKVIM YILINA ait, hedef ay DAHIL olacak sekilde ONDAN ONCEKI (o yil
    icindeki) tum aylar icin ayri `SprintKPIs` hesaplar - "yil-basi-ndan-bugune
    (year-to-date) karsilastirma". Bu, `calculate_monthly_kpis`'in sabit sayida
    "son N ay" kayan penceresinden FARKLI olarak, o yilin OCAK ayindan hedef aya
    kadar (dahil) sinirlanir; hedef aydan SONRAKI aylar (veride bulunsa bile)
    DAHIL EDILMEZ (orn. hedef ay "Haziran 2025" ise sadece Ocak-Haziran 2025
    doner, Temmuz 2025 ve sonrasi veride olsa da gosterilmez; hedef ay "Ocak 2025"
    ise sadece Ocak 2025 doner).

    `target_month` verilmezse veride bulunan en guncel ayin yili ve o ay kullanilir.
    Hicbir karta ay atanamazsa bos bir sozluk doner.
    """
    # `_monthly_periods_window` pencereyi zaten hedef aya kadar (dahil) kirptigi
    # icin burada geriye sadece "ayni takvim yili" kisitini uygulamak kalir.
    dated, periods = _monthly_periods_window(df, last_n_months=None, end_month=target_month)
    if not periods:
        return {}

    target_period = periods[-1]
    year_to_date_periods = [p for p in periods if p.year == target_period.year]

    return {
        _month_label(period): calculate_sprint_kpis(dated.loc[dated["_period"] == period])
        for period in year_to_date_periods
    }


def build_yearly_monthly_history(df: pd.DataFrame, target_month: str | None = None) -> list[tuple[str, dict]]:
    """`calculate_yearly_monthly_kpis` ciktisini `reporter.create_excel_report`'un
    `iteration_history` parametresine dogrudan verilebilecek `(ay_etiketi,
    summary_dict)` listesine cevirir (eskiden yeniye sirali; hedef ayin yilinda,
    Ocak'tan hedef aya kadar dahil - bkz. `calculate_yearly_monthly_kpis`)."""
    return [
        (label, asdict(kpis)) for label, kpis in calculate_yearly_monthly_kpis(df, target_month).items()
    ]


def compare_yearly_sprints(df: pd.DataFrame, target_month: str | None = None) -> pd.DataFrame:
    """`compare_multi_sprints` ile ayni bicimde (satirlar metrik, sutunlar ay), fakat
    "son N ay" penceresi yerine `target_month`'un TAKVIM YILINDA, Ocak'tan hedef aya
    kadar (dahil) olan aylari karsilastirir - "yil-basindan-bugune (year-to-date)
    karsilastirma" (bkz. `calculate_yearly_monthly_kpis`). Dashboard'daki Genel Bakış
    sayfasinin "İterasyon Bazlı İş Büyüklüğü" grafiginde ve Excel raporunun ust
    bolumunde kullanilir.

    `created` kolonu bulunamadiysa/tamamen bossa bos bir `DataFrame` doner.
    """
    monthly_kpis = calculate_yearly_monthly_kpis(df, target_month)
    if not monthly_kpis:
        return pd.DataFrame()

    data = {
        month_label: {turkish_label: getattr(kpis, key) for key, turkish_label in COMPARISON_METRICS}
        for month_label, kpis in monthly_kpis.items()
    }
    result = pd.DataFrame(data)
    result.index.name = "Metrik"
    return result


def build_month_sprint_labels(df: pd.DataFrame) -> dict[str, list[str]]:
    """Her ay etiketini (orn. `"Temmuz 2026"`) o aya cozulen SPRINT ADLARINA
    (orn. `["MS Sprint - Temmuz 26"]`) esler.

    Arayuzun ay secim kutusunda, kullanicinin Jira'da GORDUGU sprint adini
    gosterebilmesi icindir (bkz. `app/new_dashboard.py`). Bir ay listede olup da
    burada karsiligi bulunmuyorsa, o aya sadece `created` tarihinden dusen
    kartlar uzerinden ulasilmis demektir (sprint adi cozulemeyen kartlar - bkz.
    `_row_month_keys`); arayuz bu durumu ayrica belirtir.

    Sprint bilgisi hic olmayan bir veri setinde bos sozluk doner."""
    if "sprint" not in df.columns or "sprint_months" not in df.columns:
        return {}

    labels_by_month: dict[str, set[str]] = {}
    period_map = build_sprint_period_map(df["sprint"].explode().dropna())
    for sprint_name, period in period_map.items():
        labels_by_month.setdefault(_month_label(period), set()).add(sprint_name)

    return {month: sorted(names) for month, names in labels_by_month.items()}


def latest_month_label(df: pd.DataFrame) -> str | None:
    """Veride bulunan en guncel ayin etiketini (orn. 'Ağustos 2026') doner - ay
    atamasi `_row_month_keys` ile yapilir (once SPRINT alani, yoksa `created`).
    Hicbir karta ay atanamazsa None doner."""
    keys = {key for row_keys in _row_month_keys(df) for key in row_keys}
    if not keys:
        return None
    return _month_label(pd.Period(max(keys), freq="M"))


def filter_by_month(df: pd.DataFrame, target_month: str | None = None) -> pd.DataFrame:
    """`target_month`'a ait kartlari filtreler. Bir kartin hangi aya ait sayildigi
    `_row_month_keys` ile belirlenir: ONCE Jira'nin SPRINT alani (gereksinimdeki
    "ilgili sprinte ait kartlar" kurali), sprint adindan ay cikarilamiyorsa
    `created` tarihi. Bu nedenle Temmuz sprintine devreden ama Mart'ta acilmis bir
    kart Temmuz raporuna DAHIL edilir; Temmuz'da acilip Agustos sprintine alinmis
    bir kart ise Temmuz raporuna GIRMEZ.

    `target_month`, tam etiket (`"Temmuz 2026"`) veya sadece ay adi (`"Temmuz"`) olabilir;
    sadece ay adi verilip birden fazla yila denk gelirse en guncel yil secilir.
    `target_month` verilmezse veride bulunan en guncel ay kullanilir. Hicbir karta ay
    atanamiyorsa (filtrelenemedigi icin) tum `df`'i doner.

    `target_month` VERILMIS ama bu spesifik `df` icinde (orn. onceden belirli bir
    kisiye/projeye `filter_by_assignee`/`filter_by_project` ile daraltilmis bir alt
    kumede) o aya ait hicbir kayit bulunamazsa BOS bir DataFrame doner - sessizce tum
    `df`'e geri DONULMEZ. Bu, `filter_by_assignee`/`filter_by_project` ile AYNI
    prensiptir ("boyle bir ay yok" ile "bu alt kumede o ay hic kayit yok" durumlari
    ayirt edilir); eskiden bu durumda tum df'e geri donuluyordu, bu da orn. bir
    kisinin secili ayda hic karti yokken tum aylardaki kartlarinin o aya aitmis gibi
    yanlislikla gosterilmesine yol aciyordu.
    """
    row_keys = _row_month_keys(df)
    all_keys = sorted({key for keys in row_keys for key in keys})
    if not all_keys:
        return df

    if target_month is None:
        target_key = all_keys[-1]
    else:
        normalized_target = _normalize_header(target_month)
        matching = [
            key
            for key in all_keys
            if normalized_target in _normalize_header(_month_label(pd.Period(key, freq="M")))
        ]
        if not matching:
            return df.iloc[0:0]
        target_key = matching[-1]

    result = df.loc[row_keys.map(lambda keys: target_key in keys)].reset_index(drop=True)
    result.attrs["analysis_period"] = target_key
    return result


def analyze_carried_over_issues(
    df: pd.DataFrame, target_month: str | None = None
) -> dict:
    """Secili sprintte olup daha eski bir sprintte de bulunan Jira kartlarini raporlar.

    Bu analiz Created yasina veya benzer is adlarina bakmaz; Jira'nin coklu Sprint
    alanindaki gercek uyelikleri kullanir. Boylece isim-bazli tekrarlayan darboğaz
    analizinden bagimsizdir.
    """
    scoped = filter_by_month(df, target_month)
    target_key = scoped.attrs.get("analysis_period")
    empty = pd.DataFrame(columns=[
        "Jira Kartı", "İş Listesi", "Önceki Sprintler", "Statü", "Sorumlu",
        "Büyüklük (SP)", "Oluşturulma Tarihi",
    ])
    if scoped.empty or target_key is None or "sprint_months" not in scoped.columns:
        return {"toplam_kart": 0, "toplam_sp": 0.0, "devam_eden_kart": 0,
                "devam_eden_sp": 0.0, "tamamlanan_kart": 0, "tamamlanan_sp": 0.0,
                "kartlar": empty}

    target_period = pd.Period(target_key, freq="M")
    period_map = build_sprint_period_map(scoped["sprint"].explode().dropna())

    def _previous_months(values: object) -> list[str]:
        if not isinstance(values, list):
            return []
        return sorted({key for key in values if pd.Period(key, freq="M") < target_period})

    previous_keys = scoped["sprint_months"].map(_previous_months)
    carried = scoped.loc[previous_keys.map(bool)].copy()
    carried_previous = previous_keys.loc[carried.index]
    if carried.empty:
        return {"toplam_kart": 0, "toplam_sp": 0.0, "devam_eden_kart": 0,
                "devam_eden_sp": 0.0, "tamamlanan_kart": 0, "tamamlanan_sp": 0.0,
                "kartlar": empty}

    def _previous_labels(row: pd.Series) -> str:
        allowed = set(carried_previous.loc[row.name])
        labels = [
            label for label in row["sprint"]
            if label in period_map and str(period_map[label]) in allowed
        ] if isinstance(row["sprint"], list) else []
        return ", ".join(labels)

    done = _is_done(carried["status"])
    table = pd.DataFrame({
        "Jira Kartı": carried["issue_key"].where(carried["issue_key"] != "", "—"),
        "İş Listesi": carried["summary"],
        "Önceki Sprintler": carried.apply(_previous_labels, axis=1),
        "Statü": carried["status"],
        "Sorumlu": carried["assignee"].where(carried["assignee"] != "", "—"),
        "Büyüklük (SP)": carried["estimate"],
        "Oluşturulma Tarihi": carried["created"].dt.strftime("%d-%m-%Y").fillna(""),
    }).sort_values(["Statü", "Büyüklük (SP)"], ascending=[True, False]).reset_index(drop=True)
    return {
        "toplam_kart": int(len(carried)),
        "toplam_sp": float(carried["estimate"].sum()),
        "devam_eden_kart": int((~done).sum()),
        "devam_eden_sp": float(carried.loc[~done, "estimate"].sum()),
        "tamamlanan_kart": int(done.sum()),
        "tamamlanan_sp": float(carried.loc[done, "estimate"].sum()),
        "kartlar": table,
    }


# --------------------------------------------------------------------------
# Tablo formatlari
# --------------------------------------------------------------------------


def _target_status(summary: str) -> str:
    match = PERCENT_PATTERN.search(str(summary))
    return f"Done(%{match.group(1)})" if match else "Done"


def build_planned_issues_table(df: pd.DataFrame, target_month: str | None = None) -> pd.DataFrame:
    """Planlanan isler icin: Talep Tipi, Is Listesi, Hedeflenen/Gerceklesen Buyukluk ve Statu kolonlari.

    `created` tarihine gore sadece `target_month`'a (verilmezse veride bulunan en guncel
    aya) ait kartlari icerir; gecmis aylarin kartlari listelenmez.
    """
    month_df = filter_by_month(df, target_month)
    planned = filter_planned_issues(month_df)
    done_mask = _is_done(planned["status"])

    return pd.DataFrame(
        {
            "Talep Tipi": planned["issue_type"],
            "İş Listesi": planned["summary"],
            "Hedeflenen Büyüklük (Sp)": planned["estimate"],
            "Gerçekleşen Büyüklük (Sp)": planned["estimate"].where(done_mask, 0),
            "Hedeflenen Statü": planned["summary"].map(_target_status),
            "Gerçekleşen Statü": planned["status"],
        }
    )


def build_out_of_plan_issues_table(df: pd.DataFrame, target_month: str | None = None) -> pd.DataFrame:
    """Plan disi isler icin: Talep Tipi, Is Listesi, Gerceklesen Buyukluk (Sp) ve
    Gerceklesen Statu kolonlari.

    Rapor gereksinimi plan disi liste icin SADECE bu DORT kolonu sayar - planlanan
    listesindeki "Hedeflenen ..." kolonlarinin plan disi isler icin karsiligi yoktur
    (bu isler taahhut edilmemistir). Bu yuzden burada bir "Hedef Statü" kolonu
    URETILMEZ.

    Sadece `target_month`'a (verilmezse veride bulunan en guncel aya) ait kartlari
    icerir; gecmis aylarin kartlari listelenmez.
    """
    month_df = filter_by_month(df, target_month)
    out_of_plan = filter_out_of_plan_issues(month_df)
    done_mask = _is_done(out_of_plan["status"])

    return pd.DataFrame(
        {
            "Talep Tipi": out_of_plan["issue_type"],
            "İş Listesi": out_of_plan["summary"],
            "Gerçekleşen Büyüklük (Sp)": out_of_plan["estimate"].where(done_mask, 0),
            "Gerçekleşen Statü": out_of_plan["status"],
        }
    )


# --------------------------------------------------------------------------
# Metin tabanli arama
# --------------------------------------------------------------------------

# Serbest metin aramasinin taranacagi kolonlar (talep tipi, is listesi, sorumlu,
# statu, etiketler).
SEARCHABLE_TEXT_COLUMNS = ("issue_type", "summary", "assignee", "status", "labels")


def search_issues_by_query(
    df: pd.DataFrame, query_text: str, target_month: str | None = None
) -> pd.DataFrame:
    """`query_text`'i (buyuk/kucuk harf duyarsiz) kartlarin metin alanlarinda arayip
    eslesen kartlari doner.

    `query_text` bosluk ile ayrilmis birden fazla kelime icerebilir (orn. "Kullanıcı A Analiz");
    bir kartin eslesmesi icin tum kelimelerin, kartin talep tipi/is listesi/sorumlu/statu/
    etiket alanlarinin birlesiminde (herhangi bir sirada, herhangi bir alanda) gecmesi
    yeterlidir (AND mantigi). `query_text` bos/bosluktan ibaretse metin filtresi
    uygulanmaz.

    `target_month` verilirse once o aya filtrelenir (bkz. `filter_by_month` - once SPRINT alani, yoksa `created`); verilmezse
    tum veri (tum aylar) icinde aranir.
    """
    scoped = filter_by_month(df, target_month) if target_month else df

    words = [w for w in query_text.strip().casefold().split() if w]
    if words:
        combined = (
            scoped[list(SEARCHABLE_TEXT_COLUMNS)].astype(str).agg(" ".join, axis=1).str.casefold()
        )
        mask = pd.Series(True, index=combined.index)
        for word in words:
            mask &= combined.str.contains(word, na=False, regex=False)
        matches = scoped.loc[mask]
    else:
        matches = scoped

    return pd.DataFrame(
        {
            "Talep Tipi": matches["issue_type"],
            "İş Listesi": matches["summary"],
            "Sorumlu": matches["assignee"],
            "Büyüklük (Sp)": matches["estimate"],
            "Statü": matches["status"],
            "Hedef Statü": matches["summary"].map(_target_status),
            "Etiketler": matches["labels"],
            "Oluşturulma Tarihi": matches["created"].dt.strftime("%d-%m-%Y").fillna(""),
        }
    ).reset_index(drop=True)


# --------------------------------------------------------------------------
# Proje / Konu gruplama analizi
# --------------------------------------------------------------------------


NO_COMPONENT_LABEL = "Component Yok"


def analyze_projects_by_subject(df: pd.DataFrame, target_month: str | None = None) -> pd.DataFrame:
    """Kartlari dogrudan Jira `component` ("Component/s") alanina gore gruplayip
    kaynak tuketimini raporlar - konu metni `summary`'den TURETILMEZ, boylece
    gruplama rastgele metin eslesmelerine degil sadece Jira'nin kendi Component
    alanina dayanir. `component` bos olan kartlar `NO_COMPONENT_LABEL`
    ("Component Yok") adinda ayri bir grupta toplanir, analizden cikarilmaz.

    `target_month` verilirse once o aya filtrelenir (bkz. `filter_by_month` - once SPRINT alani, yoksa `created`); verilmezse
    tum veri (tum aylar) kullanilir. Sonuc, Toplam SP'ye gore azalan sirada doner
    (en cok kaynak tuketen proje/konu ilk satirda) ve su kolonlari icerir:
    `Proje/Konu`, `Toplam İş Sayısı`, `Toplam SP`, `Tamamlanan SP`,
    `Tamamlanma Oranı (%)`.
    """
    scoped = filter_by_month(df, target_month) if target_month else df
    scoped = scoped.copy()
    scoped["_konu"] = scoped["component"].astype(str).str.strip().replace("", NO_COMPONENT_LABEL)

    done_mask = _is_done(scoped["status"])
    grouped = scoped.groupby("_konu")
    tamamlanan_sp = scoped.loc[done_mask].groupby("_konu")["estimate"].sum()

    result = pd.DataFrame(
        {
            "Toplam İş Sayısı": grouped["summary"].count(),
            "Toplam SP": grouped["estimate"].sum(),
            "Tamamlanan SP": tamamlanan_sp,
        }
    ).fillna({"Tamamlanan SP": 0.0})

    result["Tamamlanma Oranı (%)"] = [
        _safe_ratio(tamamlanan, toplam)
        for tamamlanan, toplam in zip(result["Tamamlanan SP"], result["Toplam SP"])
    ]

    result.index.name = "Proje/Konu"
    return result.reset_index().sort_values("Toplam SP", ascending=False).reset_index(drop=True)


def get_topic_deep_dive(df: pd.DataFrame, topic: str, target_month: str | None = None) -> dict:
    """Belirli bir Component/konu grubuna (veya `NO_COMPONENT_LABEL`'a) ait tum kartlara
    "drill-down" erisim saglar - `get_assignee_deep_dive` ile ayni desende, dashboard'da
    Proje & Konu sayfasindaki bir konuya tiklandiginda gosterilecek tam detay raporunu
    uretir.

    `topic`, `analyze_projects_by_subject`'in urettigi `Proje/Konu` degerleriyle
    (buyuk/kucuk harf duyarsiz, tam) eslestirilir. Once `topic`'e, sonra (verilmisse)
    `target_month`'a gore filtreler. Eslesen kart bulunamazsa `bulundu=False` ve
    sifir/bos degerlerle doner.

    Donen `dict`:
        - `bulundu`, `konu`, `toplam_is_sayisi`, `toplam_sp`, `tamamlanan_sp`,
          `tamamlanma_orani_yuzde`.
        - `gorev_listesi`: konuya ait tum kartlari `Talep Tipi`, `İş Listesi`,
          `Sorumlu`, `Büyüklük (Sp)`, `Statü`, `Hedef Statü`, `Oluşturulma Tarihi`
          kolonlariyla listeleyen, buyuklüge gore azalan sirali bir `DataFrame`.
    """
    scoped = df.copy()
    scoped["_konu"] = scoped["component"].astype(str).str.strip().replace("", NO_COMPONENT_LABEL)
    scoped = scoped.loc[scoped["_konu"].map(_normalize_header) == _normalize_header(topic)]
    scoped = filter_by_month(scoped, target_month) if target_month else scoped

    gorev_listesi = (
        pd.DataFrame(
            {
                "Talep Tipi": scoped["issue_type"],
                "İş Listesi": scoped["summary"],
                "Sorumlu": scoped["assignee"],
                "Büyüklük (Sp)": scoped["estimate"],
                "Statü": scoped["status"],
                "Hedef Statü": scoped["summary"].map(_target_status),
                "Oluşturulma Tarihi": scoped["created"].dt.strftime("%d-%m-%Y").fillna(""),
            }
        )
        .sort_values("Büyüklük (Sp)", ascending=False)
        .reset_index(drop=True)
    )

    toplam_sp = float(scoped["estimate"].sum())
    tamamlanan_sp = float(scoped.loc[_is_done(scoped["status"]), "estimate"].sum())

    return {
        "bulundu": not scoped.empty,
        "konu": topic,
        "toplam_is_sayisi": int(len(scoped)),
        "toplam_sp": toplam_sp,
        "tamamlanan_sp": tamamlanan_sp,
        "tamamlanma_orani_yuzde": _safe_ratio(tamamlanan_sp, toplam_sp),
        "gorev_listesi": gorev_listesi,
    }


# --------------------------------------------------------------------------
# Darbogaz (bottleneck) analizi
# --------------------------------------------------------------------------

# Statu adinda gecerse isi "tikanma/bekleme" olarak isaretleyen anahtar kelimeler
# (yazim varyasyonlarindan bagimsiz calisir, orn. "XL BLOCK", "Blocked", "On Hold").
BOTTLENECK_STATUS_KEYWORDS = ("block", "hold", "bekle", "stuck", "engel")

# "Done" disinda ama aktif is sayilmayan (kapanmis/iptal edilmis) statuleri isaretler.
CANCELLED_STATUS_KEYWORDS = ("cancel", "iptal", "vazgeç", "vazgec")

# Bu esigin ustundeki aktif isler "buyuk/riskli" (yuksek SP'li) olarak isaretlenir.
HIGH_ESTIMATE_THRESHOLD = 8.0


def _matches_any_keyword(status: pd.Series, keywords: tuple[str, ...]) -> pd.Series:
    normalized = status.astype(str).map(_ascii_fold_tr)
    mask = pd.Series(False, index=status.index)
    for keyword in keywords:
        mask |= normalized.str.contains(_ascii_fold_tr(keyword), na=False, regex=False)
    return mask


def detect_bottlenecks(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Henuz tamamlanmamis (Done disi) ve iptal edilmemis aktif isleri statu ve
    buyukluk (SP) bazinda gruplayip olasi darbogazlari one cikaran bir rapor uretir.

    `target_month` verilirse once o aya filtrelenir (bkz. `filter_by_month` - once SPRINT alani, yoksa `created`); verilmezse
    tum veri (tum aylar) kullanilir.

    Donen `dict`:
        - `aktif_is_sayisi`, `toplam_aktif_sp`: aktif (Done/iptal disi) islerin genel
          ozet sayilari.
        - `status_ozeti`: aktif isleri statuye gore gruplayan, toplam SP'ye gore azalan
          sirali bir `DataFrame` (`İş Sayısı`, `Toplam SP`, `Ortalama SP`).
        - `kritik_isler`: statusu "blocked/hold/bekle" gibi tikanma belirten kelimeler
          iceren VEYA `HIGH_ESTIMATE_THRESHOLD` (varsayilan 8 SP) ustunde buyuklukte
          olan aktif isleri, buyuklüge gore azalan sirada listeleyen bir `DataFrame`.
    """
    scoped = filter_by_month(df, target_month) if target_month else df

    is_cancelled = _matches_any_keyword(scoped["status"], CANCELLED_STATUS_KEYWORDS)
    active = scoped.loc[~_is_done(scoped["status"]) & ~is_cancelled]

    grouped = active.groupby("status")
    status_summary = pd.DataFrame(
        {
            "İş Sayısı": grouped["summary"].count(),
            "Toplam SP": grouped["estimate"].sum(),
            "Ortalama SP": grouped["estimate"].mean().round(2),
        }
    )
    status_summary.index.name = "Statü"
    status_summary = (
        status_summary.reset_index().sort_values("Toplam SP", ascending=False).reset_index(drop=True)
    )

    is_bottleneck_status = _matches_any_keyword(active["status"], BOTTLENECK_STATUS_KEYWORDS)
    is_high_estimate = active["estimate"] >= HIGH_ESTIMATE_THRESHOLD
    critical = active.loc[is_bottleneck_status | is_high_estimate]

    critical_table = (
        pd.DataFrame(
            {
                "Talep Tipi": critical["issue_type"],
                "İş Listesi": critical["summary"],
                "Sorumlu": critical["assignee"],
                "Büyüklük (Sp)": critical["estimate"],
                "Statü": critical["status"],
                "Hedef Statü": critical["summary"].map(_target_status),
            }
        )
        .sort_values("Büyüklük (Sp)", ascending=False)
        .reset_index(drop=True)
    )

    return {
        "aktif_is_sayisi": int(len(active)),
        "toplam_aktif_sp": float(active["estimate"].sum()),
        "status_ozeti": status_summary,
        "kritik_isler": critical_table,
    }


# --------------------------------------------------------------------------
# Tahmin dogruluk (sapma) analizi
# --------------------------------------------------------------------------


def analyze_estimation_accuracy(df: pd.DataFrame, target_month: str | None = None) -> pd.DataFrame:
    """Talep tipine (`issue_type`) gore, hedeflenen (planlanan) SP ile gerceklesen
    (tamamlanan/`Done`) SP arasindaki sapmayi ve tahmin dogruluk oranini hesaplar.

    `target_month` verilirse once o aya filtrelenir (bkz. `filter_by_month` - once SPRINT alani, yoksa `created`); verilmezse
    tum veri (tum aylar) kullanilir. Sonuc, `Sapma Oranı (%)`'na gore azalan sirada
    doner - boylece tahmin hatasinin/sapmanin en yuksek oldugu talep tipi ilk satirdan
    okunabilir.

    Donen `DataFrame` kolonlari: `Talep Tipi`, `İş Sayısı`, `Tamamlanan İş Sayısı`,
    `Hedeflenen SP`, `Gerçekleşen SP`, `Sapma (SP)`, `Tahmin Doğruluk Oranı (%)`,
    `Sapma Oranı (%)`.
    """
    scoped = filter_by_month(df, target_month) if target_month else df
    done_mask = _is_done(scoped["status"])

    grouped = scoped.groupby("issue_type")
    done_grouped = scoped.loc[done_mask].groupby("issue_type")

    result = pd.DataFrame(
        {
            "İş Sayısı": grouped["summary"].count(),
            "Tamamlanan İş Sayısı": done_grouped["summary"].count(),
            "Hedeflenen SP": grouped["estimate"].sum(),
            "Gerçekleşen SP": done_grouped["estimate"].sum(),
        }
    ).fillna({"Tamamlanan İş Sayısı": 0, "Gerçekleşen SP": 0.0})
    result["Tamamlanan İş Sayısı"] = result["Tamamlanan İş Sayısı"].astype(int)

    result["Sapma (SP)"] = result["Hedeflenen SP"] - result["Gerçekleşen SP"]
    result["Tahmin Doğruluk Oranı (%)"] = [
        _safe_ratio(gerceklesen, hedeflenen)
        for gerceklesen, hedeflenen in zip(result["Gerçekleşen SP"], result["Hedeflenen SP"])
    ]
    result["Sapma Oranı (%)"] = (100 - result["Tahmin Doğruluk Oranı (%)"]).round(2)

    result.index.name = "Talep Tipi"
    return (
        result.reset_index().sort_values("Sapma Oranı (%)", ascending=False).reset_index(drop=True)
    )


# --------------------------------------------------------------------------
# Performans / KPI analizi
# --------------------------------------------------------------------------


def summarize_metrics(df: pd.DataFrame) -> dict:
    """Sprint KPI'larini ve genel metrik ozetini duz bir dict olarak doner (JSON/Streamlit uyumlu).

    `assignee_metrics` (kisi bazli is sayisi/yuk/tamamlanan SP) ve `status_breakdown`
    (statu bazli is adedi/toplam SP) listeleri, ilgili DataFrame'lerin
    `to_dict(orient="records")` haline cevrilmis JSON-uyumlu halidir.
    """
    kpis = calculate_sprint_kpis(df)
    summary = asdict(kpis)
    summary["total_issue_count"] = int(len(df))
    summary["assignee_metrics"] = calculate_assignee_metrics(df).to_dict(orient="records")
    summary["status_breakdown"] = calculate_status_breakdown(df).to_dict(orient="records")
    return summary


def compare_iterations(previous_summary: dict, current_summary: dict) -> dict:
    """Iki iterasyonun `summarize_metrics` ciktilari arasindaki sayisal farklari doner."""
    return {
        key: current_summary[key] - previous_summary[key]
        for key in current_summary
        if isinstance(current_summary[key], (int, float))
        and isinstance(previous_summary.get(key), (int, float))
    }


# --------------------------------------------------------------------------
# 5 Temel KPI Analiz Paketi
# --------------------------------------------------------------------------

# Aylik trend icin geriye donuk kac ay dahil edilecegini belirler.
CORE_KPI_TREND_MONTHS = 3

# Bir kisinin yukunun "esitsizlik/tukenmislik riski" olarak isaretlenmesi icin,
# takim ortalama yukunun kac katini gecmesi gerektigini belirler.
BURNOUT_LOAD_MULTIPLIER = 1.5


def _calculate_velocity_predictability(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 1 - Velocity & Predictability Index: taahhut edilen SP'ye karsi
    gerceklesen SP, tamamlanma orani ve son `CORE_KPI_TREND_MONTHS` ayin trendi.

    `aylik_trend`, `target_month`'tan bagimsiz olarak veride bulunan son
    `CORE_KPI_TREND_MONTHS` ayi kapsar (tek bir aya degil, trende odaklanir).
    """
    scoped = filter_by_month(df, target_month) if target_month else df
    kpis = calculate_sprint_kpis(scoped)

    return {
        "taahhut_edilen_sp": kpis.committed_sp,
        "gerceklesen_sp": kpis.completed_sp,
        "tamamlanma_orani_yuzde": kpis.completion_rate,
        "aylik_trend": compare_multi_sprints(df, last_n_months=CORE_KPI_TREND_MONTHS),
    }


def _calculate_scope_stability(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 2 - Scope Stability / Creep Ratio: plan disi SP'nin toplam yuk
    (taahhut edilen + plan disi) icindeki orani, yani "scope creep" seviyesi."""
    scoped = filter_by_month(df, target_month) if target_month else df
    kpis = calculate_sprint_kpis(scoped)
    toplam_yuk_sp = kpis.committed_sp + kpis.out_of_plan_sp

    return {
        "plan_disi_sp": kpis.out_of_plan_sp,
        "toplam_yuk_sp": toplam_yuk_sp,
        "scope_creep_orani_yuzde": _safe_ratio(kpis.out_of_plan_sp, toplam_yuk_sp),
        "plan_disi_tamamlanma_orani_yuzde": kpis.out_of_plan_rate,
    }


def _calculate_workload_equity(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 3 - Workload Equity & Burnout Risk: kisi bazli is yuku dagilimindaki
    esitsizligi (varyasyon katsayisi) ve tukenmislik riski tasiyan kisileri hesaplar.

    `esitsizlik_katsayisi`, yukun standart sapmasinin takim ortalamasina orani
    (coefficient of variation) olup 0'a ne kadar yakinsa dagilim o kadar dengelidir.
    Takim ortalamasinin `BURNOUT_LOAD_MULTIPLIER` katindan fazla yuk tasiyan kisiler
    `tukenmislik_riski_tasiyanlar` icinde doner.
    """
    metrics = calculate_assignee_metrics(df, target_month=target_month)

    if metrics.empty:
        return {
            "kisi_bazli_yuk": metrics,
            "ortalama_yuk_sp": 0.0,
            "esitsizlik_katsayisi": 0.0,
            "tukenmislik_riski_tasiyanlar": metrics,
        }

    yuk = metrics["Toplam Yük (SP)"]
    ortalama_yuk_sp = float(yuk.mean())
    std_yuk = float(yuk.std()) if len(yuk) > 1 else 0.0
    esitsizlik_katsayisi = round(std_yuk / ortalama_yuk_sp, 4) if ortalama_yuk_sp else 0.0

    esik = ortalama_yuk_sp * BURNOUT_LOAD_MULTIPLIER
    riskli = metrics.loc[yuk > esik].reset_index(drop=True)

    return {
        "kisi_bazli_yuk": metrics,
        "ortalama_yuk_sp": round(ortalama_yuk_sp, 2),
        "esitsizlik_katsayisi": esitsizlik_katsayisi,
        "tukenmislik_riski_tasiyanlar": riskli,
    }


def _calculate_personal_workload_consistency(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 3'un KISI BAZLI varyanti - `run_core_5_kpi_analyses`'e `assignee`
    verildiginde, takim ici "esitsizlik" kavrami tek bir kisi icin anlamsiz oldugundan
    onun yerine o kisinin AYLAR ARASI is yuku tutarliligini olcer.

    `tutarlilik_katsayisi`, kisinin aylik yukunun standart sapmasinin kendi aylik
    ortalamasina orani (coefficient of variation) olup 0'a ne kadar yakinsa yuku o
    kadar duzenli/istikrarlidir; yuksek deger aylar arasi ani yuk sicramalarina
    (potansiyel tukenmislik riskine) isaret eder. `aylik_yuk` her zaman (target_month'tan
    bagimsiz) kisinin tum aylardaki yukunu gosterir, boylece trend her zaman gorulur.
    """
    if "created" not in df.columns or df["created"].isna().all():
        aylik_yuk = pd.DataFrame(columns=["Ay", "Yük (SP)"])
    else:
        dated = df.loc[df["created"].notna()].copy()
        dated["_period"] = dated["created"].dt.to_period("M")
        aylik = dated.groupby("_period")["estimate"].sum().sort_index()
        aylik_yuk = pd.DataFrame(
            {"Ay": [_month_label(p) for p in aylik.index], "Yük (SP)": aylik.to_numpy()}
        )

    if len(aylik_yuk) > 1:
        ortalama = float(aylik_yuk["Yük (SP)"].mean())
        std = float(aylik_yuk["Yük (SP)"].std())
        tutarlilik_katsayisi = round(std / ortalama, 4) if ortalama else 0.0
    else:
        tutarlilik_katsayisi = 0.0

    scoped = filter_by_month(df, target_month) if target_month else df

    return {
        "toplam_yuk_sp": float(scoped["estimate"].sum()),
        "aylik_yuk": aylik_yuk,
        "tutarlilik_katsayisi": tutarlilik_katsayisi,
    }


def _calculate_flow_efficiency(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 4 - Flow Efficiency & Bottlenecks: statu dagilimi ile aktif/tikanmis
    isleri bir araya getirir (bkz. `calculate_status_breakdown`, `detect_bottlenecks`)."""
    bottlenecks = detect_bottlenecks(df, target_month=target_month)

    return {
        "statu_dagilimi": calculate_status_breakdown(df, target_month=target_month),
        "aktif_is_sayisi": bottlenecks["aktif_is_sayisi"],
        "toplam_aktif_sp": bottlenecks["toplam_aktif_sp"],
        "kritik_isler": bottlenecks["kritik_isler"],
    }


def _calculate_estimation_variance(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 5 - Estimation Accuracy & Variance: talep tipine gore tahmin sapma ve
    tutarlilik analizi (bkz. `analyze_estimation_accuracy`)."""
    return {"talep_tipi_bazli_analiz": analyze_estimation_accuracy(df, target_month=target_month)}


def run_core_5_kpi_analyses(
    df: pd.DataFrame,
    target_month: str | None = None,
    assignee: str | None = None,
    project: str | None = None,
) -> dict:
    """Takimin (veya belirli bir kisinin/projenin) performansini 5 temel/profesyonel
    KPI analiz metoduyla ozetler:

        1. `1_velocity_predictability` - Velocity & Predictability Index
        2. `2_scope_stability` - Scope Stability / Creep Ratio
        3. `3_workload_equity` - Workload Equity & Burnout Risk
        4. `4_flow_efficiency` - Flow Efficiency & Bottlenecks
        5. `5_estimation_accuracy` - Estimation Accuracy & Variance

    `target_month` verilirse ilgili analizler o aya (`created` tarihine gore)
    filtrelenir; verilmezse tum veri (tum aylar) kullanilir. Metot 1 icindeki
    `aylik_trend` bu filtreden bagimsiz olarak her zaman son `CORE_KPI_TREND_MONTHS`
    ayi kapsar. Her metot, sayisal deger uretemedigi bos/eksik veri durumlarinda
    (`NaN` yerine) 0 veya bos tablo doner.

    `assignee` verilirse analiz once o kisiye ait kartlarla sinirlanir (kisi bazli
    KPI raporu - bkz. `filter_by_person`: SADECE `assignee` kolonuyla sinirli
    degildir, kisi Developer/Analist olarak gectigi kartlar da dahil edilir); bu
    durumda 3. metot, takim ici esitsizlik yerine o kisinin AYLAR ARASI is yuku
    tutarliligini olcer (bkz. `_calculate_personal_workload_consistency`), cunku
    "takim esitsizligi" kavrami tek bir kisi icin anlamsizdir.

    `project` verilirse analiz once o projeye ait kartlarla sinirlanir (proje bazli
    KPI raporu). `assignee` ve `project` birlikte verilirse ikisi de uygulanir
    (o projede o kisinin isleri). Eslesen `assignee`/`project` bulunamazsa tum
    metotlar bos/sifir sonuclarla doner (sessizce tum takima geri donulmez).
    """
    scoped = filter_by_person(df, assignee)
    scoped = filter_by_project(scoped, project)

    workload = (
        _calculate_personal_workload_consistency(scoped, target_month)
        if assignee
        else _calculate_workload_equity(scoped, target_month)
    )

    return {
        "1_velocity_predictability": _calculate_velocity_predictability(scoped, target_month),
        "2_scope_stability": _calculate_scope_stability(scoped, target_month),
        "3_workload_equity": workload,
        "4_flow_efficiency": _calculate_flow_efficiency(scoped, target_month),
        "5_estimation_accuracy": _calculate_estimation_variance(scoped, target_month),
    }


# --------------------------------------------------------------------------
# Kapasite / Is Yuku Tahmini
# --------------------------------------------------------------------------

# Kapasite tahmininde, aksi belirtilmezse gecmis kac ayin ortalamasinin baz alinacagi.
CAPACITY_FORECAST_DEFAULT_LOOKBACK_MONTHS = 3


def _calculate_simple_committed_completed(df: pd.DataFrame) -> tuple[float, float]:
    """Verilen `df`'in TAMAMINI (herhangi bir Sprint/SprintDisi ayrimi yapmadan, TEK
    bir kapsam olarak) ele alip (toplam SP, Done SP) ciftini doner.

    `calculate_sprint_kpis`'ten farki budur: `calculate_sprint_kpis` HER ZAMAN
    kendi ic `filter_planned_issues`/`filter_out_of_plan_issues` ayrimini
    UYGULAR - bu yuzden ona zaten SADECE plan disi (veya sadece planlanan)
    kartlardan olusan bir alt kume verilirse, o alt kume icinde tekrar
    "planlanan" arar ve BOS bulur (`committed_sp` yanlislikla 0 ciker). Bu
    fonksiyon boyle bir ON-FILTRELENMIS kapsamin TAMAMINI "taahhut/toplam" olarak
    sayar - `_monthly_committed_completed` (plan disi kapasite tahmini) bunun
    icin kullanir.
    """
    committed = float(df["estimate"].sum())
    completed = float(df.loc[_is_done(df["status"]), "estimate"].sum())
    return committed, completed


def _monthly_committed_completed(
    df: pd.DataFrame, last_n_months: int | None, end_month: str | None
) -> dict[str, tuple[float, float]]:
    """`_monthly_periods_window` ile `calculate_monthly_kpis` ile AYNI ay penceresini
    kullanarak, her ay icin `_calculate_simple_committed_completed`'i uygular -
    `df` onceden belirli bir kapsama (orn. sadece plan disi isler) filtrelenmis
    olabilecegi icin, `calculate_monthly_kpis`'in aksine `SprintKPIs`'in kendi
    planlanan/plan disi ayrimini TEKRAR UYGULAMAZ (bkz.
    `_calculate_simple_committed_completed`).
    """
    dated, periods = _monthly_periods_window(df, last_n_months, end_month)
    return {
        _month_label(period): _calculate_simple_committed_completed(dated.loc[dated["_period"] == period])
        for period in periods
    }


def _monthly_out_of_plan_completed(
    df: pd.DataFrame, last_n_months: int | None, end_month: str | None
) -> dict[str, tuple[float, float]]:
    """Her ayi kendi sprint baglaminda siniflandirip plan-disi toplam/Done SP doner."""
    dated, periods = _monthly_periods_window(df, last_n_months, end_month)
    result: dict[str, tuple[float, float]] = {}
    for period in periods:
        month_df = dated.loc[dated["_period"] == period]
        result[_month_label(period)] = _calculate_simple_committed_completed(
            filter_out_of_plan_issues(month_df)
        )
    return result


def _build_capacity_forecast(monthly_pairs: dict[str, tuple[float, float]], latest_month: str | None) -> dict:
    """`{ay_etiketi: (taahhut/toplam_sp, gerceklesen_sp)}` seklindeki bir aylik
    seriden `calculate_capacity_forecast`/`calculate_capacity_forecast_split`
    icin ORTAK tahmin hesaplama mantigini (gecmis ortalama, hedef ay kiyasi,
    gelecek ay onerisi) uygular - bkz. `calculate_capacity_forecast` docstring'i
    (donen alanlarin anlami AYNI, sadece "taahhut/gerceklesen" verisinin
    KAYNAGI degisir: planlanan tahmininde Sprint KPI'lari, plan disi tahmininde
    `_monthly_committed_completed`).
    """
    empty_table = pd.DataFrame(columns=["Ay", "Taahhüt Edilen SP", "Gerçekleşen SP"])
    if not monthly_pairs:
        return {
            "hedef_ay": "—",
            "lookback_ay_sayisi": 0,
            "bu_ay_taahhut_sp": 0.0,
            "bu_ay_gerceklesen_sp": 0.0,
            "gecmis_ort_taahhut_sp": 0.0,
            "gecmis_ort_gerceklesen_sp": 0.0,
            "karsilastirma_orani_yuzde": 0.0,
            "hedef_ay_devam_ediyor": False,
            "gelecek_ay_onerilen_kapasite_sp": 0.0,
            "aylik_gecmis_tablo": empty_table,
        }

    labels = list(monthly_pairs.keys())
    hedef_ay = labels[-1]
    hedef_taahhut, hedef_gerceklesen = monthly_pairs[hedef_ay]
    gecmis_labels = labels[:-1]
    gecmis_pairs = [monthly_pairs[label] for label in gecmis_labels]
    tum_pairs = gecmis_pairs + [(hedef_taahhut, hedef_gerceklesen)]

    gecmis_ort_taahhut_sp = (
        round(sum(p[0] for p in gecmis_pairs) / len(gecmis_pairs), 2) if gecmis_pairs else 0.0
    )
    gecmis_ort_gerceklesen_sp = (
        round(sum(p[1] for p in gecmis_pairs) / len(gecmis_pairs), 2) if gecmis_pairs else 0.0
    )

    hedef_ay_devam_ediyor = hedef_ay == latest_month
    kapasite_pairs = gecmis_pairs if (hedef_ay_devam_ediyor and gecmis_pairs) else tum_pairs
    gelecek_ay_onerilen_kapasite_sp = round(sum(p[1] for p in kapasite_pairs) / len(kapasite_pairs), 2)

    aylik_gecmis_tablo = pd.DataFrame(
        {
            "Ay": labels,
            "Taahhüt Edilen SP": [p[0] for p in tum_pairs],
            "Gerçekleşen SP": [p[1] for p in tum_pairs],
        }
    )

    return {
        "hedef_ay": hedef_ay,
        "lookback_ay_sayisi": len(gecmis_labels),
        "bu_ay_taahhut_sp": hedef_taahhut,
        "bu_ay_gerceklesen_sp": hedef_gerceklesen,
        "gecmis_ort_taahhut_sp": gecmis_ort_taahhut_sp,
        "gecmis_ort_gerceklesen_sp": gecmis_ort_gerceklesen_sp,
        "karsilastirma_orani_yuzde": _safe_ratio(hedef_taahhut, gecmis_ort_taahhut_sp),
        "hedef_ay_devam_ediyor": hedef_ay_devam_ediyor,
        "gelecek_ay_onerilen_kapasite_sp": gelecek_ay_onerilen_kapasite_sp,
        "aylik_gecmis_tablo": aylik_gecmis_tablo,
    }


def calculate_capacity_forecast(
    df: pd.DataFrame,
    target_month: str | None = None,
    lookback_months: int = CAPACITY_FORECAST_DEFAULT_LOOKBACK_MONTHS,
) -> dict:
    """Kapasite ve is yuku tahmini: gecmis `lookback_months` ayin taahhut/gerceklesen
    SP ortalamasina gore, hedef ayin ekibin normalde almasi gereken is buyuklugune
    kiyasla az mi cok mu is aldigini ve gelecek ay icin onerilen kapasiteyi hesaplar.

    Hedef ay (`target_month` verilmezse veride bulunan en guncel ay), `calculate_
    monthly_kpis` ile ondan onceki `lookback_months` ayin (bulunabildigi kadariyla)
    KPI'lariyla birlikte alinir:
        - `gecmis_ort_taahhut_sp` / `gecmis_ort_gerceklesen_sp`: hedef aydan ONCEKI
          gecmis aylarin taahhut/gerceklesen SP ortalamasi (hedef ay HARIC).
        - `bu_ay_taahhut_sp` / `bu_ay_gerceklesen_sp`: hedef ayin gercek degerleri.
        - `karsilastirma_orani_yuzde`: hedef ayin taahhudunun gecmis ortalamaya orani
          (100'den yuksekse ekip normalden fazla is almis, dusukse az is almis demektir).
        - `hedef_ay_devam_ediyor`: hedef ay, veride bulunan EN GUNCEL ay ile ayniysa
          `True` - yani hedef ay muhtemelen henuz kapanmamis/devam eden bir ay,
          "gerceklesen SP"si eksik/yapay dusuk olabilir (kartlarin cogu henuz "Done"
          olmamis olabilir).
        - `gelecek_ay_onerilen_kapasite_sp`: ekibin fiilen basarabildigi is buyuklugune
          dayanan, gelecek ay icin gercekci bir kapasite onerisi - GERCEKLESEN SP
          ortalamasidir. `hedef_ay_devam_ediyor` `True` ise (hedef ay henuz
          tamamlanmamis olabilecegi icin) SADECE ONDAN ONCEKI (tamamlanmis)
          `lookback_months` ayin ortalamasi kullanilir, hedef ay bu ortalamaya DAHIL
          EDILMEZ - aksi halde henuz bitmemis bir ayin eksik gerceklesen SP'si
          ortalamayi yapay sekilde asagi ceker. `hedef_ay_devam_ediyor` `False` ise
          (hedef ay zaten tamamlanmis gecmis bir aysa) hedef ay da dahil edilir (bir
          veri noktasi daha, guvenilir). Geriye donuk hicbir tamamlanmis ay yoksa
          (orn. veride sadece devam eden tek bir ay varsa) istisnai olarak hedef ay
          yine de kullanilir - hic sonuc gostermemekten iyidir.
        - `aylik_gecmis_tablo`: kullanilan tum aylarin (gecmis + hedef) Ay/Taahhüt
          Edilen SP/Gerçekleşen SP kolonlu bir `DataFrame`'i (seffaflik icin - hedef
          ay burada her zaman gorunur, `gelecek_ay_onerilen_kapasite_sp` hesabina
          dahil edilmese bile).

    `created` tarihi bulunamazsa veya gecmiste hic ay yoksa (ilk ay/veri), ilgili
    ortalama/oneri alanlari 0.0 ve `aylik_gecmis_tablo` bos doner, hata firlatmaz.
    """
    monthly_kpis = calculate_monthly_kpis(df, last_n_months=lookback_months + 1, end_month=target_month)
    monthly_pairs = {label: (k.committed_sp, k.completed_sp) for label, k in monthly_kpis.items()}
    return _build_capacity_forecast(monthly_pairs, latest_month_label(df))


def calculate_capacity_forecast_split(
    df: pd.DataFrame,
    target_month: str | None = None,
    lookback_months: int = CAPACITY_FORECAST_DEFAULT_LOOKBACK_MONTHS,
    require_sprint_membership: bool = False,
) -> dict:
    """`calculate_capacity_forecast`'i planlanan (Sprint) ve plan dışı (Sprint Dışı)
    işler için AYRI AYRI çalıştırıp iki bağımsız tahmini bir arada döner.
    Aylık gruplama Sprint alanını, çözülemezse Created tarihini kullanır.
    `require_sprint_membership=True` olduğunda planlanan tahmini yalnızca gerçek
    sprint üyeliği olan ve iptal edilmemiş kartlardan hesaplanır.

    Taahhüt edilen ve plan dışı iş genellikle çok farklı büyüklük/dinamiklere sahip
    olduğundan (plan dışı işler taahhüt edilmemiş, sprint ortasında eklenen işlerdir),
    ikisini TEK bir ortalamada karıştırmak yanıltıcı bir kapasite önerisi üretebilir;
    bu yüzden her ikisi kendi geçmiş ortalamasına göre ayrı ayrı değerlendirilir.

    Döner `dict`:
        - `planned_forecast`: `calculate_capacity_forecast(df, ...)` ile AYNI (planlanan
          SP'yi kendi içinde zaten izole eder - bkz. o fonksiyonun docstring'i).
        - `out_of_plan_forecast`: `filter_out_of_plan_issues(df)` üzerinde, "taahhüt
          edilen SP" yerine o ay AÇILAN TÜM plan dışı SP'yi ve "gerçekleşen SP"
          yerine bunlardan Done olanları kullanan AYNI tahmin.

          ÖNEMLİ (düzeltilmiş hata): `calculate_capacity_forecast`, kendisine
          verilen `df`'in İÇİNDE `filter_planned_issues`/`filter_out_of_plan_issues`
          ile TEKRAR bir planlanan/plan dışı ayrımı yapar (bkz. `calculate_sprint_kpis`).
          Bu fonksiyona doğrudan `filter_out_of_plan_issues(df)` (yani zaten SADECE
          plan dışı kartlardan oluşan bir alt küme) verilirse, o alt küme İÇİNDE
          tekrar "planlanan" arar ve HİÇBİR ŞEY bulamaz - "taahhüt SP" her zaman
          0 çıkar (plan dışı iş olsa bile). Bu yüzden plan dışı tahmini burada
          `calculate_capacity_forecast`'i ÇAĞIRMAZ; bunun yerine
          `_monthly_committed_completed` ile (kendi içinde tekrar bir ayrım
          yapmadan) doğrudan aylık plan dışı toplam/Done SP çiftlerini hesaplayıp
          AYNI ortak tahmin mantığını (`_build_capacity_forecast`) bu çiftler
          üzerinde çalıştırır.

    Her iki alt sonuç da `calculate_capacity_forecast` ile AYNI şema/alanlara sahiptir
    (bkz. o fonksiyonun docstring'i).
    """
    planned_source = df
    if require_sprint_membership:
        # The Created-date fallback used by other monthly reports must not turn
        # sprintless cards into committed sprint work. Cancelled cards also do
        # not contribute to the commitment shown on the Jira board.
        if "sprint_months" in df.columns:
            planned_source = df.loc[df["sprint_months"].map(bool)]
        else:
            planned_source = df.iloc[0:0]
        planned_source = planned_source.loc[
            ~_matches_any_keyword(planned_source["status"], CANCELLED_STATUS_KEYWORDS)
        ]

    out_of_plan_pairs = _monthly_out_of_plan_completed(
        df, last_n_months=lookback_months + 1, end_month=target_month
    )

    return {
        "planned_forecast": calculate_capacity_forecast(
            planned_source, target_month=target_month, lookback_months=lookback_months
        ),
        "out_of_plan_forecast": _build_capacity_forecast(
            out_of_plan_pairs, latest_month_label(df)
        ),
    }


# --------------------------------------------------------------------------
# Ileri Duzey Darbogaz ve Akis Analitigi
# --------------------------------------------------------------------------

# WIP Aging kovalarinin toplami aktif is sayisina esittir. Bu ay acilan ve 4-5
# aylik kartlar da bir kovaya girer; hicbir aktif kart grafikten kaybolmaz.
WIP_BUCKET_LABELS = ("0-1 Aylık Aktif İş", "2-5 Aylık Aktif İş", "6+ Aylık Aktif İş")

# "Blocker & Hold" analizinde statu/etiket/ozet metninde aranan anahtar kelimeler
# (BOTTLENECK_STATUS_KEYWORDS'e ek olarak Ingilizce "waiting" de eklenmistir).
ADVANCED_BLOCKER_KEYWORDS = BOTTLENECK_STATUS_KEYWORDS + ("wait", "beklet")

# "Flow Load vs. Capacity" icin genel akis asamalari ve bu asamayi tanimlayan anahtar
# kelimeler (gercek Jira statu isimleri firmadan firmaya degistigi icin kelime tabanli
# eslesme kullanilir; hicbiri eslesmezse kart "Diğer" grubuna duser).
FLOW_STAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Backlog / To Do": ("to do", "todo", "backlog", "yapılacak", "bekliyor"),
    "In Progress": ("progress", "development", "analysis", "geliştirme", "analiz", "devam"),
    "Review / Test": ("review", "test", "qa", "kontrol", "onay"),
}

# Akis asamalarinin gosterilecegi sabit, mantikli sira (soldan saga is akisi).
FLOW_STAGE_ORDER = ["Backlog / To Do", "In Progress", "Review / Test", "Blocked / Hold", "Done", "Cancelled", "Diğer"]


def _empty_wip_bucket() -> dict:
    return {
        "is_sayisi": 0,
        "toplam_sp": 0.0,
        "kartlar": pd.DataFrame(
            columns=["Talep Tipi", "İş Listesi", "Sorumlu", "Statü", "Hedef Statü", "Büyüklük (Sp)", "Oluşturulma Tarihi"]
        ),
    }


def _wip_bucket_of(ay_farki: int) -> str | None:
    """Aktif karti 0-1, 2-5 veya 6+ aylik kovaya yerlestirir."""
    if ay_farki < 0:
        return None
    if ay_farki <= 1:
        return WIP_BUCKET_LABELS[0]
    if ay_farki <= 5:
        return WIP_BUCKET_LABELS[1]
    return WIP_BUCKET_LABELS[2]


def _calculate_wip_aging(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Secili sprintte Done/iptal olmayan kartlarin yasini hesaplar.

    Referans tarihi secili ayin sonu, devam eden ay icin bugundur. Yaslanma
    kovalari 0-1, 2-5 ve 6+ aylik kartlari gosterir. Kova toplami aktif kart
    sayisina esittir. Isim/sorumlu eslesmesi bu metrikte aranmaz.
    """
    month_scoped = filter_by_month(df, target_month) if target_month else df
    reference_date = pd.Timestamp.now()
    if target_month and month_scoped.attrs.get("analysis_period"):
        period = pd.Period(month_scoped.attrs["analysis_period"], freq="M")
        reference_date = min(period.end_time, reference_date)
    ref_period = pd.Timestamp(reference_date).to_period("M")

    is_cancelled = _matches_any_keyword(month_scoped["status"], CANCELLED_STATUS_KEYWORDS)
    active = month_scoped.loc[
        ~_is_done(month_scoped["status"]) & ~is_cancelled
        & month_scoped["created"].notna() & (month_scoped["created"] <= reference_date)
    ].copy()

    if active.empty:
        return {
            "aktif_is_sayisi": 0,
            "ortalama_yas_gun": 0.0,
            "onceki_ay": _empty_wip_bucket(),
            "uc_aylik": _empty_wip_bucket(),
            "alti_aylik": _empty_wip_bucket(),
            "yaslanma_ozeti": pd.DataFrame(
                {"Kategori": list(WIP_BUCKET_LABELS), "İş Sayısı": [0, 0, 0], "Toplam SP": [0.0, 0.0, 0.0]}
            ),
        }

    active["_age_days"] = (pd.Timestamp(reference_date) - active["created"]).dt.days.clip(lower=0)
    created_period = active["created"].dt.to_period("M")
    active["_ay_farki"] = created_period.apply(lambda p: ref_period.ordinal - p.ordinal)
    active["_kova"] = active["_ay_farki"].map(_wip_bucket_of)

    def _build_bucket(label: str) -> dict:
        subset = active.loc[active["_kova"] == label].sort_values("created", ascending=True)
        kartlar = pd.DataFrame(
            {
                "Talep Tipi": subset["issue_type"],
                "İş Listesi": subset["summary"],
                "Sorumlu": subset["assignee"],
                "Statü": subset["status"],
                "Hedef Statü": subset["summary"].map(_target_status),
                "Büyüklük (Sp)": subset["estimate"],
                "Oluşturulma Tarihi": subset["created"].dt.strftime("%d-%m-%Y").fillna(""),
            }
        ).reset_index(drop=True)
        return {"is_sayisi": int(len(subset)), "toplam_sp": float(subset["estimate"].sum()), "kartlar": kartlar}

    onceki_ay = _build_bucket(WIP_BUCKET_LABELS[0])
    uc_aylik = _build_bucket(WIP_BUCKET_LABELS[1])
    alti_aylik = _build_bucket(WIP_BUCKET_LABELS[2])

    yaslanma_ozeti = pd.DataFrame(
        {
            "Kategori": list(WIP_BUCKET_LABELS),
            "İş Sayısı": [onceki_ay["is_sayisi"], uc_aylik["is_sayisi"], alti_aylik["is_sayisi"]],
            "Toplam SP": [onceki_ay["toplam_sp"], uc_aylik["toplam_sp"], alti_aylik["toplam_sp"]],
        }
    )

    return {
        "aktif_is_sayisi": int(len(active)),
        "ortalama_yas_gun": round(float(active["_age_days"].mean()), 1),
        "onceki_ay": onceki_ay,
        "uc_aylik": uc_aylik,
        "alti_aylik": alti_aylik,
        "yaslanma_ozeti": yaslanma_ozeti,
    }


def _calculate_blocker_hold(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 2 - Blocker & Hold Analizi: statu, etiket veya ozet metninde
    `ADVANCED_BLOCKER_KEYWORDS` (block/hold/bekle/stuck/engel/wait) gecen kartlarin
    is/SP oranini ve "maliyetini" (tikanmis SP miktarini) hesaplar.

    `target_month` verilmisse secili sprintteki butun kartlar dikkate alinir.
    """
    scoped = filter_by_month(df, target_month) if target_month else df

    combined_text = (
        scoped["status"].astype(str)
        + " "
        + scoped["labels"].astype(str)
        + " "
        + scoped["summary"].astype(str)
    ).str.casefold()

    blocked_mask = pd.Series(False, index=scoped.index)
    for keyword in ADVANCED_BLOCKER_KEYWORDS:
        blocked_mask |= combined_text.str.contains(keyword, na=False, regex=False)

    blocked = scoped.loc[blocked_mask]
    toplam_is = len(scoped)
    toplam_sp = float(scoped["estimate"].sum())
    tikali_sp = float(blocked["estimate"].sum())

    tikali_isler = (
        pd.DataFrame(
            {
                "Talep Tipi": blocked["issue_type"],
                "İş Listesi": blocked["summary"],
                "Sorumlu": blocked["assignee"],
                "Statü": blocked["status"],
                "Hedef Statü": blocked["summary"].map(_target_status),
                "Büyüklük (Sp)": blocked["estimate"],
            }
        )
        .sort_values("Büyüklük (Sp)", ascending=False)
        .reset_index(drop=True)
    )

    return {
        "tikali_is_sayisi": int(len(blocked)),
        "tikali_is_orani_yuzde": _safe_ratio(len(blocked), toplam_is),
        "tikali_sp": tikali_sp,
        "tikali_sp_orani_yuzde": _safe_ratio(tikali_sp, toplam_sp),
        "tikali_isler": tikali_isler,
    }


def _calculate_assignee_bouncing(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 3 - Assignee Bouncing / El Değiştirme Eğilimi: Jira HTML disa
    aktarimlarinda kart bazli el degistirme (handoff) gecmisi bulunmadigindan, bu
    metrik mevcut is yuku yogunlasmasini (en yuklu %20'lik dilimin toplam yuk
    icindeki Pareto payi) bir PROXY olarak kullanir - `not` alaninda bu acikca
    belirtilir.

    `target_month` verilmisse secili sprintteki butun kartlar hesaba katilir.
    """
    metrics = calculate_assignee_metrics(filter_by_month(df, target_month) if target_month else df)
    not_metni = (
        "El değiştirme (handoff) geçmişi Jira export'unda bulunmadığından, bu metrik "
        "mevcut iş yükü yoğunlaşması (Pareto oranı) üzerinden tahmini olarak hesaplanmıştır."
    )

    if metrics.empty:
        return {
            "kisi_sayisi": 0,
            "yogunlasma_orani_yuzde": 0.0,
            "en_yuklu_kisi": None,
            "kisi_bazli_yuk": metrics,
            "not": not_metni,
        }

    toplam_yuk = float(metrics["Toplam Yük (SP)"].sum())
    kisi_sayisi = len(metrics)
    top_n = max(1, round(kisi_sayisi * 0.2))
    top_yuk = float(metrics.nlargest(top_n, "Toplam Yük (SP)")["Toplam Yük (SP)"].sum())

    return {
        "kisi_sayisi": kisi_sayisi,
        "yogunlasma_orani_yuzde": _safe_ratio(top_yuk, toplam_yuk),
        "en_yuklu_yuzde_yirmi_kisi_sayisi": top_n,
        "en_yuklu_kisi": metrics.iloc[0]["Sorumlu"],
        "kisi_bazli_yuk": metrics,
        "not": not_metni,
    }


def _calculate_reopen_rate(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 4 - Reopen / Geri Dönüş Oranı: `resolved` tarihi doluyken statusu
    artik `Done` olmayan kartlari "reopen" olarak sayar. `resolved` kolonu bu Jira
    export'unda oldugu gibi tamamen bossa, bunun yerine test/dogrulama asamasinda
    takili kalan kartlar (statude "test" gecen ama "Done" olmayanlar) bir PROXY
    olarak kullanilir - hangi yontemin kullanildigi `yontem`/`aciklama` alanlarinda
    seffaf sekilde belirtilir.

    `target_month` verilmisse secili sprintteki butun kartlar dikkate alinir.
    """
    scoped = filter_by_month(df, target_month) if target_month else df
    toplam = len(scoped)

    if toplam == 0:
        return {
            "yontem": "veri_yok",
            "aciklama": "Analiz edilecek kart bulunamadı.",
            "reopen_is_sayisi": 0,
            "reopen_orani_yuzde": 0.0,
            "detay_isler": pd.DataFrame(columns=["Talep Tipi", "İş Listesi", "Sorumlu", "Statü", "Hedef Statü"]),
        }

    has_resolved_signal = "resolved" in scoped.columns and scoped["resolved"].notna().any()

    if has_resolved_signal:
        reopened_mask = scoped["resolved"].notna() & ~_is_done(scoped["status"])
        yontem = "resolved_tarihi"
        aciklama = (
            "Çözüm (Resolved) tarihi atanmış ama statüsü artık 'Done' olmayan kartlar "
            "geri dönüş (reopen) olarak sayıldı."
        )
    else:
        test_status = _matches_any_keyword(scoped["status"], ("test",))
        phase_done = scoped["status"].astype(str).str.contains(r"\bdone\b", case=False, na=False)
        reopened_mask = test_status & ~phase_done & ~_is_done(scoped["status"])
        yontem = "test_asamasinda_takilma"
        aciklama = (
            "Jira export'unda 'Resolved' tarihi bulunmadığından gerçek reopen izlenemiyor; "
            "bunun yerine test/doğrulama aşamasında takılı kalan kartlar bir proxy olarak kullanıldı."
        )

    reopened = scoped.loc[reopened_mask]
    detay_isler = pd.DataFrame(
        {
            "Talep Tipi": reopened["issue_type"],
            "İş Listesi": reopened["summary"],
            "Sorumlu": reopened["assignee"],
            "Statü": reopened["status"],
            "Hedef Statü": reopened["summary"].map(_target_status),
        }
    ).reset_index(drop=True)

    return {
        "yontem": yontem,
        "aciklama": aciklama,
        "reopen_is_sayisi": int(len(reopened)),
        "reopen_orani_yuzde": _safe_ratio(len(reopened), toplam),
        "detay_isler": detay_isler,
    }


def _calculate_flow_load_capacity(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Metot 5 - Flow Load vs. Capacity: kartlari genel akis asamalarina (Backlog/
    To Do, In Progress, Review/Test, Blocked/Hold, Done, Cancelled, Diğer) gore
    kumulatif olarak gruplayip nerede yigilma oldugunu gosterir.
    """
    scoped = filter_by_month(df, target_month) if target_month else df

    if scoped.empty:
        empty_cols = ["Akış Aşaması", "İş Sayısı", "Toplam SP", "Kümülatif İş Sayısı", "Kümülatif Oran (%)"]
        return {"asama_dagilimi": pd.DataFrame(columns=empty_cols)}

    stage = pd.Series("Diğer", index=scoped.index, name="Akış Aşaması")
    for label, keywords in FLOW_STAGE_KEYWORDS.items():
        stage[_matches_any_keyword(scoped["status"], keywords)] = label
    stage[_matches_any_keyword(scoped["status"], BOTTLENECK_STATUS_KEYWORDS)] = "Blocked / Hold"
    stage[_matches_any_keyword(scoped["status"], CANCELLED_STATUS_KEYWORDS)] = "Cancelled"
    stage[_is_done(scoped["status"])] = "Done"

    grouped = pd.DataFrame(
        {
            "İş Sayısı": scoped.groupby(stage)["summary"].count(),
            "Toplam SP": scoped.groupby(stage)["estimate"].sum(),
        }
    )
    grouped = grouped.reindex([s for s in FLOW_STAGE_ORDER if s in grouped.index]).fillna(0)
    grouped["İş Sayısı"] = grouped["İş Sayısı"].astype(int)
    grouped["Kümülatif İş Sayısı"] = grouped["İş Sayısı"].cumsum()
    toplam_is = int(grouped["İş Sayısı"].sum())
    grouped["Kümülatif Oran (%)"] = (
        (grouped["Kümülatif İş Sayısı"] / toplam_is * 100).round(2) if toplam_is else 0.0
    )
    grouped.index.name = "Akış Aşaması"
    grouped = grouped.reset_index()

    return {"asama_dagilimi": grouped}


def analyze_advanced_bottlenecks(df: pd.DataFrame, target_month: str | None = None) -> dict:
    """Takimin gizli tikanikliklarini ve surec verimsizliklerini acmak icin 5 ileri
    duzey darbogaz/akis metrigini tek bir cagrida birlestirir:

        1. `1_wip_aging` - WIP Aging (aktif islerin yas dagilimi)
        2. `2_blocker_hold` - Blocker & Hold Analizi (tikanan is orani/maliyeti)
        3. `3_assignee_bouncing` - Assignee Bouncing / yuk yogunlasmasi (proxy)
        4. `4_reopen_rate` - Reopen / geri donus orani (proxy destekli)
        5. `5_flow_load_capacity` - Flow Load vs. Capacity (akis asamasi yigilmasi)

    `target_month` verilirse ilgili analizler o aya (`created` tarihine gore)
    filtrelenir; verilmezse tum veri (tum aylar) kullanilir.

    Jira veri yapisinda `Resolved` (reopen tespiti) veya el degistirme (handoff)
    gecmisi gibi alanlar cogu zaman bos/mevcut degildir; bu durumlarda ilgili
    metotlar seffaf bir sekilde (donen `dict` icindeki `yontem`/`not`/`aciklama`
    alanlariyla belirtilerek) makul bir proxy'ye duser, hata firlatmaz.
    """
    return {
        "1_wip_aging": _calculate_wip_aging(df, target_month),
        "2_blocker_hold": _calculate_blocker_hold(df, target_month),
        "3_assignee_bouncing": _calculate_assignee_bouncing(df, target_month),
        "4_reopen_rate": _calculate_reopen_rate(df, target_month),
        "5_flow_load_capacity": _calculate_flow_load_capacity(df, target_month),
    }


# --------------------------------------------------------------------------
# Isim bazli tekrarlayan (devam eden) darbogaz tespiti
# --------------------------------------------------------------------------

RECURRING_BOTTLENECK_COLUMNS = [
    "Temel İsim", "Sorumlu", "Kart Sayısı", "İlk Görüldüğü Ay",
    "Son Görüldüğü Ay", "Güncel Statü", "Toplam SP", "Kartlar",
]


def _strip_percent_suffix(summary: str) -> str:
    """Bir kart ozetinden `PERCENT_PATTERN`'in yakaladigi "%80" gibi yuzde ifadesini
    (varsa etrafinda kalan bos parantezle birlikte) cikarip normallestirilmis bir
    "temel isim" uretir - orn. "Analiz (%80)" -> "Analiz", "Analiz  (%98)" -> "Analiz".
    Boylece ayni isin farkli aylarda/yuzdelerle acilan kartlari ayni temel isim
    altinda eslestirilebilir hale gelir."""
    stripped = PERCENT_PATTERN.sub("", str(summary))
    stripped = re.sub(r"\(\s*\)", "", stripped)
    return re.sub(r"\s+", " ", stripped).strip()


def _find_continuing_name_pairs(
    df: pd.DataFrame, target_month: str | None = None
) -> tuple[set[tuple[str, str]], pd.Period | None, pd.Period | None]:
    """Projenin en kritik is kuralinin ORTAK/tek uygulamasi: bir (temel_isim,
    assignee) cifti "AYLAR ARASI SUREKLILIK" gosteriyor mu, yani bir is bir
    onceki (ardisik takvim) aydan bu güncel referans aya GERCEKTEN DEVAM ETMIS
    mi? `detect_recurring_bottlenecks` icin aylar arasi ayni isim ve sorumluyu
    eslestirir. WIP ve secili sprint akisi bu eslesmeye bagli degildir.

    KURAL: bir cift SADECE VE SADECE GUNCEL REFERANS AYDA (bkz. asagida) EN AZ
    BIR karti VE bir ONCEKI (ardisik takvim) ayda DA EN AZ BIR karti varsa
    "devam eden" sayilir. `Done`/not-`Done` durumu bu karara HIC KARISMAZ -
    onceki ayki kart `Done` gorunse bile, ayni isim (yuzdesiz) bu ay yine
    acildiysa bu bir sureklilik isaretidir. Gecmisi olmayan, bu ay ilk kez
    acilmis bir kart bu fonksiyona gore "devam eden" SAYILMAZ.

    Referans ay: `target_month` verilmisse `filter_by_month` ile AYNI
    eslestirme mantigiyla (kismi/buyuk-kucuk harf duyarsiz ay adi eslesmesi,
    en guncel eslesen yil secilir) o aya karsilik gelen `Period` bulunur;
    verilmezse veride bulunan EN GUNCEL `Period` kullanilir. Bir onceki
    (ardisik takvim ayi) `Period`, referans ayin tam bir ay oncesidir.

    Doner: `(devam_eden_ciftler, referans_period, onceki_period)`.
        - `devam_eden_ciftler`: hem referans ayda hem onceki ayda gorulen
          (temel_isim, assignee) ciftlerinin kesisimi (`set`). Onceki ayda
          HIC kart yoksa (o `Period`'a ait hicbir satir bulunamazsa) bos bir
          `set` doner - "devam ettigi" ISPATLANAMADIGI icin hicbir cift dahil
          edilmez.
        - `referans_period`/`onceki_period`: `created` verisi hic yoksa, hic
          gecerli temel isim cikarilamiyorsa veya `target_month` veride
          eslesmiyorsa `(None, None)` doner ve `devam_eden_ciftler` bos `set`
          olur.
    """
    if df.empty or "created" not in df.columns or df["created"].isna().all():
        return set(), None, None

    working = df.loc[df["created"].notna()].copy()
    working["_period"] = working["created"].dt.to_period("M")
    working["_temel_isim"] = working["summary"].map(_strip_percent_suffix)
    working = working.loc[working["_temel_isim"] != ""]
    if working.empty:
        return set(), None, None

    periods = sorted(working["_period"].unique())

    if target_month is None:
        referans_period = periods[-1]
    else:
        normalized_target = _normalize_header(target_month)
        matching_periods = sorted(
            p for p in periods if normalized_target in _normalize_header(_month_label(p))
        )
        if not matching_periods:
            return set(), None, None
        referans_period = matching_periods[-1]

    onceki_period = referans_period - 1

    guncel_satirlar = working.loc[working["_period"] == referans_period]
    onceki_satirlar = working.loc[working["_period"] == onceki_period]

    guncel_ciftler = set(zip(guncel_satirlar["_temel_isim"], guncel_satirlar["assignee"]))
    onceki_ciftler = set(zip(onceki_satirlar["_temel_isim"], onceki_satirlar["assignee"]))

    return guncel_ciftler & onceki_ciftler, referans_period, onceki_period


def detect_recurring_bottlenecks(df: pd.DataFrame, target_month: str | None = None) -> pd.DataFrame:
    """Ayni isin (ozet metninden yuzde ifadesi cikarilarak elde edilen "temel isim"),
    bir onceki (ardisik takvim) aydan GUNCEL referans aya GERCEKTEN DEVAM ETTIGI
    durumlari tespit eder - "isim bazli devam eden darbogaz" analizi.

    Eslestirme YONTEMI: sadece temel isme gore gruplamak, veride cok genel/tekrar
    eden ("Analiz", "Pazarlama Raporu" gibi) ozet metinleri yuzunden fazla sayida
    yanlis pozitif uretebilir (farkli kisilerin farkli isleri ayni "Analiz" adiyla
    yanlislikla ayni gruba dusebilir). Bu riski azaltmak icin gruplama (temel_isim,
    assignee) IKILISINE gore yapilir - "ayni temel isim VE ayni sorumlu = ayni is"
    varsayimi kullanilir.

    DAHIL ETME KURALI (TEK kriter - bkz. `_find_continuing_name_pairs`): bir
    (temel_isim, assignee) grubu SADECE VE SADECE bu ciftin GUNCEL REFERANS AYDA
    (target_month verilmisse o ay, verilmezse veride bulunan en guncel ay) EN AZ
    BIR karti VE bir ONCEKI (ardisik) ayda DA EN AZ BIR karti varsa sonuca dahil
    edilir. `Done` durumu bu karari HIC ETKILEMEZ - onceki ayki kart `Done`
    gorunse bile isim (yuzdesiz) bu ay yine acildiysa "devam eden" sayilir; "bu ay
    Done degil" olmasi TEK BASINA yeterli DEGILDIR. Gecmisi olmayan, bu ay ilk kez
    acilmis bir kart (bir onceki ayda hic karsiligi yoksa) darbogaz SAYILMAZ.

    Donen `DataFrame` kolonlari: `Temel İsim`, `Sorumlu`, `Kart Sayısı`,
    `İlk Görüldüğü Ay`, `Son Görüldüğü Ay`, `Güncel Statü` (gruptaki en son
    acilan kartin statusu - SADECE BILGI amaclidir, dahil etme kararini
    ETKILEMEZ), `Toplam SP`, `Kartlar` (gruptaki her kartin ay/ozet/statusunu
    ozetleyen birlestirilmis bir metin). Sonuc, `Kart Sayısı`'na gore azalan
    sirada doner. Uygun grup bulunamazsa veya `created` verisi bulunamazsa bos
    bir `DataFrame` (yine de yukaridaki kolonlarla) doner.
    """
    empty_result = pd.DataFrame(columns=RECURRING_BOTTLENECK_COLUMNS)

    if df.empty or "created" not in df.columns or df["created"].isna().all():
        return empty_result

    working = df.loc[df["created"].notna()].copy()
    working["_temel_isim"] = working["summary"].map(_strip_percent_suffix)
    working = working.loc[working["_temel_isim"] != ""]
    if working.empty:
        return empty_result

    devam_eden_ciftler, _, _ = _find_continuing_name_pairs(df, target_month)
    if not devam_eden_ciftler:
        return empty_result

    records = []
    for (temel_isim, sorumlu), grup in working.groupby(["_temel_isim", "assignee"], sort=False):
        if (temel_isim, sorumlu) not in devam_eden_ciftler:
            continue

        grup_sirali = grup.sort_values("created")
        ilk_kart = grup_sirali.iloc[0]
        son_kart = grup_sirali.iloc[-1]

        kartlar_metni = "; ".join(
            f"{row['created'].strftime('%d-%m-%Y')} - {row['summary']} [{row['status']}]"
            for _, row in grup_sirali.iterrows()
        )

        records.append(
            {
                "Temel İsim": temel_isim,
                "Sorumlu": sorumlu,
                "Kart Sayısı": int(len(grup)),
                "İlk Görüldüğü Ay": _month_label(ilk_kart["created"].to_period("M")),
                "Son Görüldüğü Ay": _month_label(son_kart["created"].to_period("M")),
                "Güncel Statü": son_kart["status"],
                "Toplam SP": float(grup["estimate"].sum()),
                "Kartlar": kartlar_metni,
            }
        )

    if not records:
        return empty_result

    return (
        pd.DataFrame(records, columns=RECURRING_BOTTLENECK_COLUMNS)
        .sort_values("Kart Sayısı", ascending=False)
        .reset_index(drop=True)
    )


# --------------------------------------------------------------------------
# Dogal dil soru motoru (dashboard)
# --------------------------------------------------------------------------

# "answer_dashboard_query" niyet (intent) tespiti icin anahtar kelime gruplari.
# Sirali kontrol edilir: ilk eslesen niyet kazanir (bkz. answer_dashboard_query).
QUERY_INTENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "bottleneck": ("blok", "block", "tikan", "engel", "bekleyen", "darbogaz", "stuck", "hold"),
    "estimation": ("tahmin", "sapma", "dogruluk"),
    "project": ("proje", "konu", "component", "epic"),
    "trend": ("trend", "degisim", "karsilastir", "gecmis"),
    "status": ("durum", "statu", "asama"),
    "person": ("kim", "kimde", "kimin", "sorumlu", "kisi"),
    "top": ("en cok", "en fazla", "en yuklu", "maksimum"),
}


def _query_contains_any(normalized_query: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in normalized_query for keyword in keywords)


def _find_mentioned_assignee(df: pd.DataFrame, normalized_query: str) -> str | None:
    """Sorgu metninde gecen kelimeler arasinda veride bulunan bir sorumlunun adinin
    (soyadi da dahil, herhangi bir parcasi) gecip gecmedigini kontrol eder.

    Tam kelime esitligi yerine `startswith` kullanilir - boylece Turkce kesme
    isaretli iyelik ekleri de ("Kullanıcı A'nın", "Kullanıcı A'ya" gibi) dogru yakalanir.
    """
    query_words = normalized_query.split()
    for name in df["assignee"].dropna().unique():
        name = str(name).strip()
        if not name:
            continue
        name_words = [w for w in _normalize_header(name).split() if len(w) >= 3]
        if name_words and any(
            word.startswith(name_word) for word in query_words for name_word in name_words
        ):
            return name
    return None


def answer_dashboard_query(
    df: pd.DataFrame, query_text: str, target_month: str | None = None
) -> str:
    """Dashboard'daki soru cubuguna yazilan dogal dil sorularini (orn. "Kimin
    üzerinde kaç iş var?", "Hangi işler bloklu?") basit bir anahtar kelime/niyet
    tespitiyle en uygun mevcut analiz fonksiyonuna yonlendirip, sonucu okunakli bir
    Turkce metin/cumle olarak doner.

    Tespit edilen niyet sirasiyla kontrol edilir: (1) sorguda gecen belirli bir kisi
    adi varsa o kisinin detay ozeti (`get_assignee_deep_dive`), (2) darbogaz/
    tikaniklik kelimeleri (`detect_bottlenecks`), (3) tahmin sapmasi
    (`analyze_estimation_accuracy`), (4) proje/konu (`analyze_projects_by_subject`),
    (5) aylik trend (`compare_multi_sprints`), (6) statu dagilimi
    (`calculate_status_breakdown`), (7) kisi/"en cok" yuk sorulari
    (`calculate_assignee_metrics`); hicbiri eslesmezse serbest metin aramasina
    (`search_issues_by_query`) duser, o da sonuc vermezse genel sprint KPI ozetiyle
    kapanir - boylece fonksiyon her zaman anlamli bir cevap doner, asla hata
    firlatmaz.

    `target_month` verilirse ilgili alt analizler (kisi adi eslesmesi haric tum
    dallarda) o aya filtrelenir.
    """
    normalized_query = query_text.strip().casefold()
    if not normalized_query:
        return "Lütfen bir soru yazın."

    mentioned_assignee = _find_mentioned_assignee(df, normalized_query)
    if mentioned_assignee:
        detay = get_assignee_deep_dive(df, mentioned_assignee, target_month=target_month)
        return (
            f"{detay['sorumlu']} üzerinde {detay['toplam_is_sayisi']} iş var; "
            f"toplam {detay['toplam_sp']:.0f} SP'nin {detay['tamamlanan_sp']:.0f} SP'si "
            f"(%{detay['tamamlanma_orani_yuzde']:.1f}) tamamlanmış."
        )

    if _query_contains_any(normalized_query, QUERY_INTENT_KEYWORDS["bottleneck"]):
        rapor = detect_bottlenecks(df, target_month=target_month)
        kritik = rapor["kritik_isler"]
        if kritik.empty:
            return "Şu an tıkanmış veya kritik seviyede bekleyen bir iş bulunmuyor."
        ornekler = ", ".join(kritik["İş Listesi"].head(3))
        return (
            f"{rapor['aktif_is_sayisi']} aktif işten {len(kritik)} tanesi tıkanmış/kritik "
            f"görünüyor (toplam {rapor['toplam_aktif_sp']:.0f} SP). Örnekler: {ornekler}."
        )

    if _query_contains_any(normalized_query, QUERY_INTENT_KEYWORDS["estimation"]):
        accuracy = analyze_estimation_accuracy(df, target_month=target_month)
        if accuracy.empty:
            return "Tahmin sapması analizi için yeterli veri bulunamadı."
        en_sapmali = accuracy.iloc[0]
        return (
            f"En yüksek tahmin sapması '{en_sapmali['Talep Tipi']}' talep tipinde "
            f"(sapma oranı %{en_sapmali['Sapma Oranı (%)']:.1f}, hedeflenen "
            f"{en_sapmali['Hedeflenen SP']:.0f} SP, gerçekleşen "
            f"{en_sapmali['Gerçekleşen SP']:.0f} SP)."
        )

    if _query_contains_any(normalized_query, QUERY_INTENT_KEYWORDS["project"]):
        analiz = analyze_projects_by_subject(df, target_month=target_month)
        if analiz.empty:
            return "Proje/konu bazlı analiz için yeterli veri bulunamadı."
        en_yuklu = analiz.iloc[0]
        return (
            f"En çok kaynak tüketen proje/konu '{en_yuklu['Proje/Konu']}' "
            f"({en_yuklu['Toplam SP']:.0f} SP, %{en_yuklu['Tamamlanma Oranı (%)']:.1f} tamamlandı)."
        )

    if _query_contains_any(normalized_query, QUERY_INTENT_KEYWORDS["trend"]):
        karsilastirma = compare_multi_sprints(df, last_n_months=3)
        if karsilastirma.empty:
            return "Aylık trend karşılaştırması için yeterli veri (Created tarihi) bulunamadı."
        aylar = list(karsilastirma.columns)
        gerceklesen = karsilastirma.loc["Gerçekleşen SP"]
        detaylar = ", ".join(f"{ay}: {gerceklesen[ay]:.0f} SP" for ay in aylar)
        return f"Son {len(aylar)} ayin gerçekleşen SP trendi - {detaylar}."

    if _query_contains_any(normalized_query, QUERY_INTENT_KEYWORDS["status"]):
        breakdown = calculate_status_breakdown(df, target_month=target_month)
        if breakdown.empty:
            return "Statü dağılımı için yeterli veri bulunamadı."
        en_kalabalik = breakdown.iloc[0]
        return (
            f"En kalabalık statü '{en_kalabalik['Statü']}' ({en_kalabalik['İş Sayısı']} iş, "
            f"{en_kalabalik['Toplam SP']:.0f} SP)."
        )

    if _query_contains_any(
        normalized_query, QUERY_INTENT_KEYWORDS["person"]
    ) or _query_contains_any(normalized_query, QUERY_INTENT_KEYWORDS["top"]):
        metrics = calculate_assignee_metrics(df, target_month=target_month)
        if metrics.empty:
            return "Kişi bazlı iş dağılımı için yeterli veri bulunamadı."
        en_yuklu = metrics.iloc[0]
        return (
            f"En çok iş yükü '{en_yuklu['Sorumlu']}' üzerinde ({en_yuklu['Toplam İş Sayısı']} iş, "
            f"{en_yuklu['Toplam Yük (SP)']:.0f} SP). Toplamda {len(metrics)} sorumlu bulunuyor."
        )

    eslesenler = search_issues_by_query(df, query_text, target_month=target_month)
    if not eslesenler.empty:
        ornekler = ", ".join(eslesenler["İş Listesi"].head(3))
        return f"'{query_text}' ile eşleşen {len(eslesenler)} kart bulundu. Örnekler: {ornekler}."

    kpis = calculate_sprint_kpis(filter_by_month(df, target_month) if target_month else df)
    return (
        f"'{query_text}' sorgusuna özel bir eşleşme bulunamadı. Genel özet: "
        f"taahhüt {kpis.committed_sp:.0f} SP, gerçekleşen {kpis.completed_sp:.0f} SP "
        f"(%{kpis.completion_rate:.1f} tamamlanma)."
    )


# --------------------------------------------------------------------------
# Ust seviye orkestrasyon
# --------------------------------------------------------------------------


def process_sprint_report(file_path: str | Path, target_month: str | None = None) -> dict:
    """Dosyayi okur, temizler, filtreler ve tum tablo/KPI ciktilarini tek bir dict icinde doner.

    `planned_issues` ve `out_of_plan_issues`, `created` tarihine gore sadece `target_month`'a
    (verilmezse veride bulunan en guncel aya) ait kartlari icerir; `target_month` sonucta
    hangi ayin kullanildigini gosterir (orn. `"Ağustos 2026"`).

    `monthly_history`, `resolved_month`'un (yani secilen/en guncel hedef ayin) icinde
    bulundugu TAKVIM YILINDA, Ocak'tan hedef aya kadar (DAHIL) olan aylarin
    `(ay_etiketi, summary_dict)` ciftlerinden olusur - "yil-basindan-bugune"
    karsilastirma (bkz. `build_yearly_monthly_history`). Orn. `target_month="Haziran
    2025"` ise sadece Ocak-Haziran 2025 doner (Temmuz 2025 ve sonrasi veride olsa
    bile DAHIL EDILMEZ); `target_month="Ocak 2025"` ise sadece Ocak 2025 doner.
    `created` kolonu bulunamazsa bos liste doner.
    """
    raw_df = read_sprint_report(file_path)
    df = standardize_dataframe(raw_df)

    resolved_month = target_month or latest_month_label(df)

    # `kpis`/`summary` HEDEF AYA gore hesaplanir - `planned_issues`/
    # `out_of_plan_issues` zaten o aya filtreli oldugundan, ayni ciktinin ust
    # (ozet) ve alt (liste) yarisi AYNI kart kumesini anlatmalidir. Eskiden bu iki
    # deger tum veri uzerinden hesaplaniyordu; sonuc olarak PDF'in KPI kutulari ve
    # MCP'nin `analyze_sprint` araci "Temmuz" derken 1229 kartin tamamina ait
    # rakamlari (orn. 7688 SP) bildiriyordu.
    #
    # `data` ve `monthly_history` KASITLI olarak tum veriyi kullanmaya devam eder:
    # ilki dashboard'un kendi filtrelerini uygulayabilmesi, ikincisi ise aylar
    # arasi karsilastirma yapabilmesi icin gereklidir.
    month_df = filter_by_month(df, resolved_month) if resolved_month else df

    return {
        "data": df,
        "planned_issues": build_planned_issues_table(df, target_month=resolved_month),
        "out_of_plan_issues": build_out_of_plan_issues_table(df, target_month=resolved_month),
        "kpis": calculate_sprint_kpis(month_df),
        "summary": summarize_metrics(month_df),
        "monthly_history": build_yearly_monthly_history(df, target_month=resolved_month),
        "target_month": resolved_month,
    }
