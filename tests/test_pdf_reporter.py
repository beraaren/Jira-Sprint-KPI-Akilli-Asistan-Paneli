"""`src/pdf_reporter.py` icin testler - uretilen PDF tekrar OKUNUP icerigi
dogrulanir (dosyanin sadece "olusmus olmasi" yeterli sayilmaz).

En kritik kontrol Turkce karakterlerdir: ReportLab'in varsayilan fontu `ğ/ş/İ`
icermez ve bunlari sessizce kutucuga cevirir - yani font gomme bozulursa PDF
yine de HATASIZ uretilir, sadece okunamaz hale gelir. Bu yuzden metin PDF'ten
geri cikarilip karsilastirilir.

Calistirmak icin proje kokunden: `python -m unittest tests.test_pdf_reporter -v`
"""

from __future__ import annotations

import sys
import unittest
from io import BytesIO
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pandas as pd  # noqa: E402

from pdf_reporter import PdfFontError, create_pdf_report  # noqa: E402

# Turkce'ye ozgu TUM sorunlu harfleri iceren, kasitli olarak zorlayici metinler.
TURKISH_PROBE = "Iğdır Şişli çğüöş ÇĞÜÖŞİ"


def _processed_data() -> dict:
    """`process_sprint_report`'un doner yapisinin, PDF icin gereken minimum
    alanlarini iceren sentetik bir ornek (Jira baglantisi gerektirmez)."""
    return {
        "data": pd.DataFrame(
            {
                "project": ["Money Stalkers", "Money Stalkers"],
                "summary": [TURKISH_PROBE, "İkinci iş"],
            }
        ),
        "planned_issues": pd.DataFrame(
            {
                "Talep Tipi": ["Task"],
                "İş Listesi": [TURKISH_PROBE],
                "Hedeflenen Büyüklük": [5.0],
                "Gerçekleşen Büyüklük": [3.0],
                "Hedeflenen Statü": ["Done"],
                "Gerçekleşen Statü": ["In Progress"],
            }
        ),
        "out_of_plan_issues": pd.DataFrame(
            {
                "Talep Tipi": ["Bug"],
                "İş Listesi": ["Plan dışı iş - ığşĞİŞ"],
                "Gerçekleşen Büyüklük": [2.0],
                "Gerçekleşen Statü": ["Done"],
                "Hedef Statü": ["Done"],
            }
        ),
        "summary": {
            "committed_sp": 10.0,
            "completed_sp": 7.0,
            "out_of_plan_sp": 2.0,
            "total_completed_sp": 9.0,
            "completion_rate": 70.0,
            "out_of_plan_rate": 100.0,
            "total_issue_count": 2,
            "assignee_metrics": [
                {"Sorumlu": "GİZEM YILMAZ", "Toplam İş Sayısı": 1, "Toplam Yük (SP)": 5.0, "Tamamlanan SP": 3.0}
            ],
            "status_breakdown": [{"Statü": "Done", "İş Sayısı": 1, "Toplam SP": 2.0}],
        },
        "monthly_history": [
            ("Ağustos 2026", {"committed_sp": 8.0, "completed_sp": 6.0, "out_of_plan_sp": 1.0, "total_completed_sp": 7.0}),
            ("Eylül 2026", {"committed_sp": 10.0, "completed_sp": 7.0, "out_of_plan_sp": 2.0, "total_completed_sp": 9.0}),
        ],
        "target_month": "Eylül 2026",
    }


def _pdf_text(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader

    return "".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf_bytes)).pages)


class CreatePdfReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.pdf = create_pdf_report(_processed_data(), target_month="Eylül 2026")
        except PdfFontError as exc:  # pragma: no cover - fontsuz sistemde anlamli atlama
            raise unittest.SkipTest(f"Turkce font yok, PDF testleri atlandi: {exc}") from exc
        cls.text = _pdf_text(cls.pdf)

    def test_gecerli_bir_pdf_uretilir(self):
        self.assertTrue(self.pdf.startswith(b"%PDF-"))
        self.assertGreater(len(self.pdf), 5000)

    def test_turkce_karakterler_bozulmaz(self):
        """Font gomme bozulursa PDF yine hatasiz uretilir ama `ğ/ş/İ` kutucuga
        doner - bu yuzden metin geri okunup BIREBIR karsilastirilir."""
        self.assertIn(TURKISH_PROBE, self.text)
        self.assertNotIn("�", self.text)
        self.assertNotIn("■", self.text)

    def test_tum_bolumler_yer_alir(self):
        for beklenen in (
            "İterasyon Bazlı İş Büyüklüğü",
            "Statü Dağılımı",
            "Kişi Bazlı İş Yükü",
            "planlanan iş listemiz",
            "plan dışı iş listemiz",
        ):
            with self.subTest(bolum=beklenen):
                self.assertIn(beklenen, self.text)

    def test_kpi_degerleri_yazilir(self):
        self.assertIn("Taahhüt Edilen SP", self.text)
        self.assertIn("%70.0", self.text)

    def test_is_listesi_satirlari_yazilir(self):
        self.assertIn("Plan dışı iş", self.text)
        self.assertIn("GİZEM YILMAZ", self.text)

    def test_hedef_ay_basliklara_islenir(self):
        self.assertIn("Eylül 2026", self.text)

    def test_bos_tablolar_hata_vermez(self):
        """Kayit bulunmayan bir ay secildiginde rapor COKMEZ; ilgili bolume
        aciklama yazip devam eder."""
        veri = _processed_data()
        veri["planned_issues"] = veri["planned_issues"].iloc[0:0]
        veri["out_of_plan_issues"] = veri["out_of_plan_issues"].iloc[0:0]
        pdf = create_pdf_report(veri, target_month="Eylül 2026")
        self.assertIn("kayıt bulunamadı", _pdf_text(pdf))

    def test_ay_verilmezse_genel_basliklar_kullanilir(self):
        veri = _processed_data()
        veri["target_month"] = None
        text = _pdf_text(create_pdf_report(veri, target_month=None))
        self.assertIn("Planlanan iş listemiz", text)
        self.assertIn("Tüm Aylar", text)


if __name__ == "__main__":
    unittest.main()
