"""Panel sayfalarinin "Paylas" ciktisi - duz metin ve e-postalanabilir HTML.

Panodaki her sayfa (Genel Bakis, Haftalik Ozet, Ekip, Proje, Akis) ekranda
gordugu seyi e-postaya yapistirilabilir bir metne cevirebilsin diye, sayfa
icerigi ONCE ortak bir belge modeline (`ShareDocument`) donusturulur, SONRA
buradaki iki renderer'dan biriyle yazilir.

Neden ortak bir model:
    Her sayfaya ayri bir HTML/metin uretici yazmak, ayni kacis (escape) /
    kirpma / e-posta uyumluluk kurallarini bes kez tekrarlamak demekti - biri
    guncellenip digerleri unutuldugunda sayfalar sessizce farkli davranirdi.
    Bu modul o kurallarin TEK yeridir; sayfalar sadece "ne yazilacagini"
    (baslik, kutular, cumleler, tablolar) tarif eder.

E-POSTA UYUMLULUGU (bu kisitlar bilincli):
    - TUM stiller satir ici (inline) yazilir. Outlook/Gmail `<style>` bloklarini
      siklikla soyar; harici bir stil dosyasi zaten hic yuklenmez.
    - Disaridan HICBIR kaynak (font, CDN, gorsel) cekilmez - cikti kendi kendine
      yeter ve cevrimdisi/engelli-gorsel ortamlarda da ayni gorunur.
    - Duzen `<table>` uzerine kurulur; e-posta istemcilerinde flex/grid guvenilir
      degildir.
    - Kullanicidan/Jira'dan gelen her metin `html.escape` ile kacisla yazilir.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field

import pandas as pd

__all__ = [
    "ShareBullet",
    "ShareDocument",
    "ShareSection",
    "render_share_html",
    "render_share_text",
]

# Bir tabloda paylasilacak azami satir. Asilirsa KIRPILIR ve kirpildigi ciktiya
# ACIKCA yazilir - sessiz kesme, okuyana "hepsi bu kadar" izlenimi verirdi.
MAX_TABLE_ROWS = 50

SEVERITY_RENK = {
    "kritik": "#C0392B",
    "dikkat": "#E67E22",
    "iyi": "#27AE60",
    "notr": "#5A6472",
}
SEVERITY_ISARET = {"kritik": "🔴", "dikkat": "🟠", "iyi": "🟢", "notr": "⚪"}

_GOVDE_FONT = "14px/1.55 Arial,Helvetica,sans-serif"
_MUTED = "#5A6472"
_BASLIK_RENK = "#1F4E78"
_CIZGI = "#E3E9F0"
_ZEMIN = "#F7F9FC"


@dataclass
class ShareBullet:
    """Tek cumlelik bir tespit. `severity` verilirse metinde isaret, HTML'de renk
    olarak gosterilir; verilmezse duz madde isareti kullanilir."""

    metin: str
    severity: str | None = None


@dataclass
class ShareSection:
    """Bir bolum: baslik + (madde listesi ve/veya tablo). Ikisi de bos olan
    bolumler ciktiya HIC yazilmaz (bkz. `_dolu_bolumler`) - boylece cagiran
    tarafin veri olup olmadigini ayrica kontrol etmesi gerekmez."""

    baslik: str
    bullets: list[ShareBullet] = field(default_factory=list)
    tablo: pd.DataFrame | None = None
    not_metni: str | None = None


@dataclass
class ShareDocument:
    """Paylasilacak sayfanin tamami."""

    baslik: str
    alt_baslik: str = ""
    kutular: list[tuple[str, str]] = field(default_factory=list)
    bolumler: list[ShareSection] = field(default_factory=list)
    dipnot: str = ""


def _dolu_bolumler(doc: ShareDocument) -> list[ShareSection]:
    return [
        b
        for b in doc.bolumler
        if b.bullets or (b.tablo is not None and not b.tablo.empty) or b.not_metni
    ]


def _kirpilmis(tablo: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    if len(tablo) <= MAX_TABLE_ROWS:
        return tablo, None
    return (
        tablo.head(MAX_TABLE_ROWS),
        f"(İlk {MAX_TABLE_ROWS} satır gösteriliyor; toplam {len(tablo)} satır var.)",
    )


def _hucre(value: object) -> str:
    """Tablo hucresini okunakli metne cevirir: ondalik olmayan sayilar tam sayi
    gibi yazilir (13.0 -> 13), bos degerler tire olur."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


# --------------------------------------------------------------------------
# Duz metin
# --------------------------------------------------------------------------


def render_share_text(doc: ShareDocument) -> str:
    """Belgeyi duz metne cevirir - panelde kopyalanmak ve e-posta govdesine
    yapistirilmak icin."""
    satirlar: list[str] = [doc.baslik]
    if doc.alt_baslik:
        satirlar.append(doc.alt_baslik)
    satirlar.append("")

    if doc.kutular:
        satirlar.append(" · ".join(f"{etiket}: {deger}" for etiket, deger in doc.kutular))
        satirlar.append("")

    for bolum in _dolu_bolumler(doc):
        satirlar.append(f"— {bolum.baslik} —")
        for bullet in bolum.bullets:
            isaret = SEVERITY_ISARET.get(bullet.severity or "", "•")
            satirlar.append(f"{isaret} {bullet.metin}")
        if bolum.tablo is not None and not bolum.tablo.empty:
            tablo, kirpma_notu = _kirpilmis(bolum.tablo)
            satirlar.append(
                tablo.map(_hucre).to_string(index=False)
                if hasattr(tablo, "map")
                else tablo.applymap(_hucre).to_string(index=False)
            )
            if kirpma_notu:
                satirlar.append(kirpma_notu)
        if bolum.not_metni:
            satirlar.append(bolum.not_metni)
        satirlar.append("")

    if doc.dipnot:
        satirlar.append(doc.dipnot)
    return "\n".join(satirlar).rstrip() + "\n"


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def _html_kutular(kutular: list[tuple[str, str]]) -> str:
    if not kutular:
        return ""
    hucreler = "".join(
        f'<td align="center" style="padding:12px 8px;background:#EEF3F9;border-radius:6px;'
        f'font:13px Arial,Helvetica,sans-serif;color:{_MUTED};">'
        f'<div style="font-size:22px;font-weight:bold;color:{_BASLIK_RENK};">{html.escape(str(deger))}</div>'
        f"{html.escape(str(etiket))}</td>"
        f'<td style="width:8px;">&nbsp;</td>'
        for etiket, deger in kutular
    )
    return (
        '<tr><td><table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        f'width="100%"><tr>{hucreler}</tr></table></td></tr>'
        '<tr><td style="height:18px;line-height:18px;">&nbsp;</td></tr>'
    )


def _html_tablo(tablo: pd.DataFrame) -> str:
    tablo, kirpma_notu = _kirpilmis(tablo)
    basliklar = "".join(
        f'<th align="left" style="padding:6px 10px;background:{_BASLIK_RENK};color:#FFFFFF;'
        f'font:bold 12px Arial,Helvetica,sans-serif;">{html.escape(str(c))}</th>'
        for c in tablo.columns
    )
    satirlar = "".join(
        "<tr>"
        + "".join(
            f'<td style="padding:6px 10px;border-bottom:1px solid {_CIZGI};'
            f'font:13px Arial,Helvetica,sans-serif;color:#1F2933;">{html.escape(_hucre(v))}</td>'
            for v in row
        )
        + "</tr>"
        for row in tablo.itertuples(index=False, name=None)
    )
    not_html = (
        f'<div style="font:12px Arial,Helvetica,sans-serif;color:{_MUTED};padding-top:6px;">'
        f"{html.escape(kirpma_notu)}</div>"
        if kirpma_notu
        else ""
    )
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" '
        f'style="border-collapse:collapse;"><tr>{basliklar}</tr>{satirlar}</table>{not_html}'
    )


def render_share_html(doc: ShareDocument) -> str:
    """Belgeyi tek parca, gomulu stilli HTML'e cevirir (bkz. modul basindaki
    e-posta uyumluluk kisitlari)."""
    esc = html.escape
    parcalar: list[str] = [
        f'<tr><td style="font:bold 20px Arial,Helvetica,sans-serif;color:{_BASLIK_RENK};'
        f'padding-bottom:4px;">{esc(doc.baslik)}</td></tr>'
    ]
    if doc.alt_baslik:
        parcalar.append(
            f'<tr><td style="font:14px Arial,Helvetica,sans-serif;color:{_MUTED};'
            f'padding-bottom:18px;">{esc(doc.alt_baslik)}</td></tr>'
        )
    parcalar.append(_html_kutular(doc.kutular))

    for bolum in _dolu_bolumler(doc):
        parcalar.append(
            f'<tr><td style="font:bold 15px Arial,Helvetica,sans-serif;color:{_BASLIK_RENK};'
            f'padding:6px 0 10px;">{esc(bolum.baslik)}</td></tr>'
        )
        for bullet in bolum.bullets:
            renk = SEVERITY_RENK.get(bullet.severity or "", _MUTED)
            parcalar.append(
                f'<tr><td style="padding:10px 14px;border-left:4px solid {renk};background:{_ZEMIN};'
                f'border-radius:4px;font:{_GOVDE_FONT};color:#1F2933;">{esc(bullet.metin)}</td></tr>'
                '<tr><td style="height:8px;line-height:8px;">&nbsp;</td></tr>'
            )
        if bolum.tablo is not None and not bolum.tablo.empty:
            parcalar.append(f"<tr><td>{_html_tablo(bolum.tablo)}</td></tr>")
        if bolum.not_metni:
            parcalar.append(
                f'<tr><td style="font:12px Arial,Helvetica,sans-serif;color:{_MUTED};'
                f'padding:6px 0 0;">{esc(bolum.not_metni)}</td></tr>'
            )
        parcalar.append('<tr><td style="height:18px;line-height:18px;">&nbsp;</td></tr>')

    if doc.dipnot:
        parcalar.append(
            f'<tr><td style="padding-top:14px;border-top:1px solid {_CIZGI};'
            f'font:12px/1.5 Arial,Helvetica,sans-serif;color:#8B95A3;">{esc(doc.dipnot)}</td></tr>'
        )

    return (
        '<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">'
        f"<title>{esc(doc.baslik)}</title></head>"
        '<body style="margin:0;padding:24px;background:#FFFFFF;">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:820px;margin:0 auto;width:100%;">'
        + "".join(parcalar)
        + "</table></body></html>"
    )
