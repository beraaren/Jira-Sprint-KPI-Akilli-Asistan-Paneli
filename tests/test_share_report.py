"""`src/share_report.py` - tum panel sayfalarinin ortak "Paylas" ciktisi.

Bu testler ozellikle E-POSTA UYUMLULUK kisitlarini kilitler (satir ici stil,
harici kaynak yok, kacis) - bunlar gozle fark edilmesi zor ama bozuldugunda
cikti Outlook/Gmail'de sessizce bicimsiz gorunen turden kurallardir.

Calistirmak icin proje kokunden: `python -m unittest tests.test_share_report -v`
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from share_report import (  # noqa: E402
    MAX_TABLE_ROWS,
    ShareBullet,
    ShareDocument,
    ShareSection,
    render_share_html,
    render_share_text,
)


def _doc(**kwargs) -> ShareDocument:
    varsayilan = dict(
        baslik="Test Raporu",
        alt_baslik="Temmuz 2026  •  Tüm Ekip",
        kutular=[("Taahhüt", "752"), ("Gerçekleşen", "268")],
        bolumler=[
            ShareSection(
                baslik="Değerlendirme",
                bullets=[
                    ShareBullet("Tempo ortalamanın altında.", severity="dikkat"),
                    ShareBullet("Bu hafta 26 iş tamamlandı.", severity="iyi"),
                ],
            ),
            ShareSection(
                baslik="Tablo",
                tablo=pd.DataFrame({"Talep Tipi": ["Task"], "SP": [13.0]}),
            ),
        ],
        dipnot="Veri notu: örnek.",
    )
    varsayilan.update(kwargs)
    return ShareDocument(**varsayilan)


class TextRenderTests(unittest.TestCase):
    def test_baslik_kutular_bolumler_ve_dipnot_yer_alir(self):
        metin = render_share_text(_doc())
        for beklenen in ("Test Raporu", "Temmuz 2026", "Taahhüt: 752", "Değerlendirme",
                         "Tempo ortalamanın altında.", "Talep Tipi", "Veri notu: örnek."):
            with self.subTest(beklenen=beklenen):
                self.assertIn(beklenen, metin)

    def test_severity_isaretle_gosterilir(self):
        metin = render_share_text(_doc())
        self.assertIn("🟠 Tempo ortalamanın altında.", metin)
        self.assertIn("🟢 Bu hafta 26 iş tamamlandı.", metin)

    def test_severitysiz_madde_duz_isaret_alir(self):
        doc = _doc(bolumler=[ShareSection("Özet", bullets=[ShareBullet("Nötr bir cümle.")])])
        self.assertIn("• Nötr bir cümle.", render_share_text(doc))

    def test_ondalik_olmayan_sayilar_tam_sayi_yazilir(self):
        doc = _doc(bolumler=[ShareSection("T", tablo=pd.DataFrame({"SP": [13.0]}))])
        metin = render_share_text(doc)
        self.assertIn("13", metin)
        self.assertNotIn("13.0", metin)


class EmptySectionTests(unittest.TestCase):
    def test_bos_bolumler_ciktiya_yazilmaz(self):
        """Cagiran tarafin 'veri var mi' kontrolu yapmasi gerekmesin diye."""
        doc = _doc(bolumler=[
            ShareSection("Boş Bölüm"),
            ShareSection("Boş Tablo", tablo=pd.DataFrame()),
            ShareSection("Dolu", bullets=[ShareBullet("Var.")]),
        ])
        for renderer in (render_share_text, render_share_html):
            with self.subTest(renderer=renderer.__name__):
                cikti = renderer(doc)
                self.assertNotIn("Boş Bölüm", cikti)
                self.assertNotIn("Boş Tablo", cikti)
                self.assertIn("Dolu", cikti)

    def test_tamamen_bos_belge_hata_vermez(self):
        doc = ShareDocument(baslik="Boş")
        self.assertIn("Boş", render_share_text(doc))
        self.assertIn("Boş", render_share_html(doc))


class TableTruncationTests(unittest.TestCase):
    def test_uzun_tablo_kirpilir_ve_kirpma_ACIKCA_yazilir(self):
        """Sessiz kesme, okuyana 'hepsi bu kadar' izlenimi verirdi."""
        tablo = pd.DataFrame({"No": range(MAX_TABLE_ROWS + 25)})
        doc = _doc(bolumler=[ShareSection("Uzun", tablo=tablo)])
        for renderer in (render_share_text, render_share_html):
            with self.subTest(renderer=renderer.__name__):
                cikti = renderer(doc)
                self.assertIn(f"İlk {MAX_TABLE_ROWS} satır", cikti)
                self.assertIn(f"toplam {MAX_TABLE_ROWS + 25} satır", cikti)

    def test_kisa_tablo_kirpma_notu_almaz(self):
        doc = _doc(bolumler=[ShareSection("Kısa", tablo=pd.DataFrame({"No": [1, 2]}))])
        self.assertNotIn("satır gösteriliyor", render_share_text(doc))


class HtmlEmailCompatibilityTests(unittest.TestCase):
    """E-posta istemcilerinin sessizce bozdugu seyleri kilitler."""

    def setUp(self):
        self.html = render_share_html(_doc())

    def test_tek_parca_html_belgesidir(self):
        self.assertTrue(self.html.startswith("<!DOCTYPE html>"))
        self.assertTrue(self.html.rstrip().endswith("</html>"))

    def test_style_blogu_kullanilmaz(self):
        """Outlook/Gmail <style> bloklarini soyar - stiller satir ici olmali."""
        self.assertNotIn("<style", self.html)
        self.assertNotIn("class=", self.html)

    def test_disaridan_kaynak_cekilmez(self):
        """Font/CDN/gorsel istegi olmamali - cevrimdisi ve engelli-gorsel
        ortamlarda da ayni gorunsun."""
        for sema in ("http://", "https://", "//cdn", "url("):
            with self.subTest(sema=sema):
                self.assertNotIn(sema, self.html)

    def test_duzen_table_uzerine_kuruludur(self):
        """E-posta istemcilerinde flex/grid guvenilir degildir."""
        self.assertIn("<table", self.html)
        self.assertNotIn("display:flex", self.html)
        self.assertNotIn("display:grid", self.html)


class HtmlEscapingTests(unittest.TestCase):
    def test_madde_metni_kacisla_yazilir(self):
        doc = _doc(bolumler=[ShareSection("X", bullets=[ShareBullet("<script>alert(1)</script>")])])
        h = render_share_html(doc)
        self.assertNotIn("<script>alert", h)
        self.assertIn("&lt;script&gt;", h)

    def test_baslik_ve_tablo_hucreleri_kacisla_yazilir(self):
        doc = _doc(
            baslik="<b>Başlık</b>",
            bolumler=[ShareSection("T", tablo=pd.DataFrame({"<i>Kolon</i>": ["<img src=x>"]}))],
        )
        h = render_share_html(doc)
        self.assertNotIn("<b>Başlık</b>", h)
        self.assertNotIn("<img src=x>", h)
        self.assertNotIn("<i>Kolon</i>", h)

    def test_turkce_karakterler_bozulmaz(self):
        doc = _doc(baslik="ığşĞİŞçöüÇÖÜ")
        self.assertIn("ığşĞİŞçöüÇÖÜ", render_share_html(doc))
        self.assertIn("ığşĞİŞçöüÇÖÜ", render_share_text(doc))


class MissingValueTests(unittest.TestCase):
    def test_bos_hucreler_tire_olarak_yazilir(self):
        doc = _doc(bolumler=[ShareSection("T", tablo=pd.DataFrame({"A": [None], "B": [float("nan")]}))])
        self.assertIn("—", render_share_text(doc))
        self.assertIn("—", render_share_html(doc))


if __name__ == "__main__":
    unittest.main()
