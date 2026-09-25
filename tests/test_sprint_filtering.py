"""Ay filtresinin Jira'nin SPRINT alanina gore calistigini dogrulayan testler.

Rapor gereksinimi "ilgili sprinte ait kartlar" dedigi icin bir kart, ACILDIGI
aya degil, icinde yer aldigi SPRINT(ler)in ayina aittir. Bu dosya, eskiden
`created` tarihi uzerinden yapilan (ve Temmuz 2026'da 95 kartin 49'unu kaciran)
filtrelemenin geri gelmemesini garanti eder.

Calistirmak icin proje kokunden: `python -m unittest tests.test_sprint_filtering -v`
"""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from processor import (  # noqa: E402
    analyze_carried_over_issues,
    build_month_sprint_labels,
    build_planned_issues_table,
    build_sprint_period_map,
    calculate_capacity_forecast_split,
    calculate_assignee_metrics,
    calculate_sprint_kpis,
    drop_duplicate_rows,
    explode_by_role,
    filter_by_month,
    filter_out_of_plan_issues,
    filter_planned_issues,
    latest_month_label,
    standardize_dataframe,
)


def _raw_row(summary: str, created: str, sprints: list[str], sp: float = 5.0,
             status: str = "Done", labels: str = "", assignee: str = "",
             developers: str = "", analysts: str = "") -> dict:
    """CSV disa aktarimini taklit eden tek satir. Jira ayni ada sahip Sprint
    kolonlarini yan yana yazar; pandas bunlari "Sprint", "Sprint.1"... diye
    tekillestirdigi icin test de ayni bicimi kullanir."""
    row = {
        "Issue Type": "Task",
        "Summary": summary,
        "Status": status,
        "Labels": labels,
        "Created": created,
        "Custom field (Story Points)": sp,
        "Assignee": assignee,
        "Developers": developers,
        "Analists": analysts,
    }
    for index, sprint in enumerate(sprints):
        row["Sprint" if index == 0 else f"Sprint.{index}"] = sprint
    return row


def _frame(rows: list[dict]) -> pd.DataFrame:
    return standardize_dataframe(pd.DataFrame(rows))


class SprintBasedMonthFilterTests(unittest.TestCase):
    def test_capacity_commitment_uses_sprint_membership_and_excludes_cancelled(self):
        df = _frame([
            _raw_row("Sprint work", "03-Mar-26 03:02", ["Eylül İterasyonu - 2026"], sp=194),
            _raw_row("Created this month", "01-Sep-26 10:00", [], sp=19),
            _raw_row("Cancelled", "01-Sep-26 10:00", ["Eylül İterasyonu - 2026"],
                     sp=3, status="İptal"),
        ])
        forecast = calculate_capacity_forecast_split(
            df, target_month="Eylül 2026", require_sprint_membership=True
        )["planned_forecast"]
        self.assertEqual(forecast["bu_ay_taahhut_sp"], 194)

    def test_devreden_kart_acildigi_ayda_degil_sprint_ayinda_sayilir(self):
        """Mart'ta acilip Temmuz sprintine devretmis kart Temmuz raporuna GIRMELI
        (eski `created` davranisinda kayboluyordu)."""
        df = _frame([
            _raw_row("Devreden kart", "03-Mar-26 03:02",
                     ["MS Sprint - Mart 26", "MS Sprint - Temmuz 26"]),
        ])
        temmuz = filter_by_month(df, "Temmuz 2026")
        self.assertEqual(list(temmuz["summary"]), ["Devreden kart"])

    def test_sprinti_baska_ay_olan_kart_acildigi_aya_girmez(self):
        """1 Temmuz'da acilmis ama Haziran sprintinde kalmis kart Temmuz'a
        GIRMEMELI (eski davranista yanlislikla giriyordu)."""
        df = _frame([
            _raw_row("Haziran karti", "01-Jul-26 07:52", ["MS Sprint - Haziran 26"]),
        ])
        self.assertTrue(filter_by_month(df, "Temmuz 2026").empty)
        self.assertEqual(len(filter_by_month(df, "Haziran 2026")), 1)

    def test_kart_her_sprintinde_ayri_ayri_sayilir(self):
        """Gereksinim "her bir sprint icin ... toplami" dedigi icin devreden bir
        kart, yer aldigi HER ayin toplamina girer."""
        df = _frame([
            _raw_row("Uc sprintlik kart", "03-Mar-26 03:02", sp=8.0, sprints=[
                "MS Sprint - Mayıs 26", "MS Sprint - Haziran 26", "MS Sprint - Temmuz 26",
            ]),
        ])
        for ay in ("Mayıs 2026", "Haziran 2026", "Temmuz 2026"):
            with self.subTest(ay=ay):
                self.assertEqual(calculate_sprint_kpis(filter_by_month(df, ay)).committed_sp, 8.0)

    def test_sprintdisi_etiketi_sprint_filtresiyle_birlikte_calisir(self):
        df = _frame([
            _raw_row("Planli", "03-Mar-26 03:02", ["MS Sprint - Temmuz 26"], sp=13.0),
            _raw_row("Plan disi", "03-Mar-26 03:02", ["MS Sprint - Temmuz 26"], sp=3.0,
                     labels="SprintDışı"),
            _raw_row("Baska ay", "03-Jul-26 03:02", ["MS Sprint - Agustos 26"], sp=21.0),
        ])
        kpis = calculate_sprint_kpis(filter_by_month(df, "Temmuz 2026"))
        self.assertEqual(kpis.committed_sp, 13.0)
        self.assertEqual(kpis.out_of_plan_sp, 3.0)
        self.assertEqual(kpis.total_completed_sp, 16.0)

        planli = build_planned_issues_table(df, "Temmuz 2026")
        self.assertEqual(list(planli["İş Listesi"]), ["Planli"])


class SprintLabelParsingTests(unittest.TestCase):
    def test_turkce_karakter_varyasyonlari_ayni_aya_cozulur(self):
        period_map = build_sprint_period_map([
            "MS Sprint - Ağustos 25", "MS Sprint - Agustos 26", "MS Sprint - Subat 26",
        ])
        self.assertEqual(str(period_map["MS Sprint - Ağustos 25"]), "2025-08")
        self.assertEqual(str(period_map["MS Sprint - Agustos 26"]), "2026-08")
        self.assertEqual(str(period_map["MS Sprint - Subat 26"]), "2026-02")

    def test_kesik_yil_veri_baglamiyla_cozulur(self):
        """Disa aktarimda yili kirpilmis "Nisan 2", ayni ayin tam yazilmis
        "Nisan 25" adi zaten var oldugu icin 2026'ya cozulmeli."""
        period_map = build_sprint_period_map([
            "MS Sprint - Mart 26", "MS Sprint - Nisan 25", "MS Sprint - Nisan 2",
        ])
        self.assertEqual(str(period_map["MS Sprint - Nisan 2"]), "2026-04")

    def test_rzn_iterasyon_bicimi_cozulur(self):
        """RZN board'u "Eylül İterasyonu - 2025" sablonunu kullanir; MS ise
        "MS Sprint - Eylül 26". Ay ile yil arasina giren kelime opsiyoneldir."""
        period_map = build_sprint_period_map([
            "Eylül İterasyonu - 2025",
            "Şubat İterasyonu - 2026",
            "Agustos İterasyonu - 2026",
            "Aralık İterasyonu - 2023",
        ])
        self.assertEqual(str(period_map["Eylül İterasyonu - 2025"]), "2025-09")
        self.assertEqual(str(period_map["Şubat İterasyonu - 2026"]), "2026-02")
        self.assertEqual(str(period_map["Agustos İterasyonu - 2026"]), "2026-08")
        self.assertEqual(str(period_map["Aralık İterasyonu - 2023"]), "2023-12")

    def test_ms_bicimi_RZN_destegi_eklenince_DEGISMEZ(self):
        """Regresyon kilidi: RZN destegi MS'in mevcut cozumlerini kaydirmamali.
        Gercek MS sprint adlariyla dogrulanir."""
        ms_adlari = {
            "MS Sprint - Ocak 26": "2026-01",
            "MS Sprint - Subat 26": "2026-02",
            "MS Sprint - Mart 26": "2026-03",
            "MS Sprint - Mayıs 26": "2026-05",
            "MS Sprint - Haziran 26": "2026-06",
            "MS Sprint - Temmuz 26": "2026-07",
            "MS Sprint - Agustos 26": "2026-08",
            "MS Sprint - Eylül 26": "2026-09",
            "MS Sprint - Aralık 25": "2025-12",
            "MS Sprint - Temmuz 25": "2025-07",
        }
        period_map = build_sprint_period_map(ms_adlari)
        for ad, beklenen in ms_adlari.items():
            with self.subTest(sprint=ad):
                self.assertEqual(str(period_map[ad]), beklenen)

    def test_iki_board_ayni_anda_dogru_cozulur(self):
        """Tek bir veri setinde her iki sablon birlikte bulunursa ikisi de
        kendi ayina cozulmeli (ayni ay + ayni yil ise ayni Period)."""
        period_map = build_sprint_period_map([
            "MS Sprint - Eylül 26", "Eylül İterasyonu - 2026",
        ])
        self.assertEqual(str(period_map["MS Sprint - Eylül 26"]), "2026-09")
        self.assertEqual(str(period_map["Eylül İterasyonu - 2026"]), "2026-09")

    def test_ay_tasimayan_sprint_adi_cozulmez(self):
        period_map = build_sprint_period_map(["Sprint 2", "MS Sprint 3", ""])
        self.assertEqual(period_map, {})

    def test_ay_ile_yil_arasinda_serbest_metin_KABUL_EDILMEZ(self):
        """Ara kelime listesi kasitli olarak DAR: joker bir desen, ay adiyla
        alakasiz bir sayiyi yil sanip kartlari yanlis aya baglardi."""
        period_map = build_sprint_period_map([
            "Mart raporu ve 2024 butcesi",
            "Nisan ayinda alinan 15 karar",
        ])
        self.assertEqual(period_map, {})


class CreatedFallbackTests(unittest.TestCase):
    def test_sprintsiz_kart_created_ayina_duser(self):
        """Sprint alani bos ya da adi cozulemeyen kartlar kaybolmamali - eski
        `created` davranisi bu kartlar icin gecerli kalir."""
        df = _frame([
            _raw_row("Sprintsiz", "15-Jul-26 10:00", []),
            _raw_row("Cozulemeyen sprint", "15-Jul-26 10:00", ["Sprint 2"]),
            _raw_row("Sprintli", "15-Jul-26 10:00", ["MS Sprint - Agustos 26"]),
        ])
        temmuz = filter_by_month(df, "Temmuz 2026")
        self.assertEqual(sorted(temmuz["summary"]), ["Cozulemeyen sprint", "Sprintsiz"])

    def test_sprint_kolonu_hic_yoksa_eski_davranis_korunur(self):
        df = _frame([
            {"Issue Type": "Task", "Summary": "Eski format", "Status": "Done",
             "Created": "15-Jul-26 10:00", "Custom field (Story Points)": 5.0},
        ])
        self.assertEqual(len(filter_by_month(df, "Temmuz 2026")), 1)
        self.assertEqual(latest_month_label(df), "Temmuz 2026")

    def test_bos_veri_hata_firlatmaz(self):
        df = _frame([_raw_row("Kart", "15-Jul-26 10:00", ["MS Sprint - Temmuz 26"])]).iloc[0:0]
        self.assertTrue(filter_by_month(df, "Temmuz 2026").empty)
        self.assertIsNone(latest_month_label(df))

    def test_sprint_disi_fallback_env_ile_acilip_kapanir(self):
        df = _frame([
            _raw_row("Tolerans", "02-Jul-26 23:59", ["MS Sprint - Temmuz 26"]),
            _raw_row("Sonradan gelen", "03-Jul-26 00:01", ["MS Sprint - Temmuz 26"]),
            _raw_row("Etiketli", "01-Jul-26 00:01", ["MS Sprint - Temmuz 26"], labels="SprintDışı"),
        ])
        scoped = filter_by_month(df, "Temmuz 2026")

        with patch.dict("os.environ", {"SPRINT_DISI_FALLBACK_ENABLED": "false"}):
            self.assertEqual(list(filter_out_of_plan_issues(scoped)["summary"]), ["Etiketli"])
        with patch.dict("os.environ", {"SPRINT_DISI_FALLBACK_ENABLED": "true"}):
            self.assertEqual(
                list(filter_out_of_plan_issues(scoped)["summary"]),
                ["Sonradan gelen", "Etiketli"],
            )
            self.assertEqual(list(filter_planned_issues(scoped)["summary"]), ["Tolerans"])

    def test_devreden_kart_yeni_sprintte_fallbacke_takilmaz(self):
        df = _frame([
            _raw_row("Devreden", "03-Mar-26 03:02", [
                "MS Sprint - Mart 26", "MS Sprint - Temmuz 26",
            ]),
        ])
        with patch.dict("os.environ", {"SPRINT_DISI_FALLBACK_ENABLED": "true"}):
            self.assertEqual(len(filter_planned_issues(filter_by_month(df, "Temmuz 2026"))), 1)
            self.assertEqual(len(filter_out_of_plan_issues(filter_by_month(df, "Mart 2026"))), 1)


class CarriedOverIssueTests(unittest.TestCase):
    def test_yalniz_onceki_sprint_uyeligi_olan_kartlari_listeler(self):
        rows = [
            _raw_row("Devreden aktif", "03-Mar-26 03:02", [
                "MS Sprint - Mart 26", "MS Sprint - Temmuz 26",
            ], sp=8.0, status="In Progress", assignee="Ada"),
            _raw_row("Devreden tamam", "03-Apr-26 03:02", [
                "MS Sprint - Nisan 26", "MS Sprint - Temmuz 26",
            ], sp=5.0, status="Done", assignee="Ece"),
            _raw_row("Yeni kart", "01-Jul-26 03:02", ["MS Sprint - Temmuz 26"], sp=3.0),
        ]
        rows[0]["Issue Key"] = "MS-1"
        rows[1]["Issue Key"] = "MS-2"
        result = analyze_carried_over_issues(_frame(rows), "Temmuz 2026")

        self.assertEqual(result["toplam_kart"], 2)
        self.assertEqual(result["toplam_sp"], 13.0)
        self.assertEqual(result["devam_eden_kart"], 1)
        self.assertEqual(result["tamamlanan_kart"], 1)
        self.assertEqual(set(result["kartlar"]["Jira Kartı"]), {"MS-1", "MS-2"})
        self.assertNotIn("Yeni kart", set(result["kartlar"]["İş Listesi"]))


class MonthSprintLabelTests(unittest.TestCase):
    """Arayuzdeki "İterasyon / Sprint" secim kutusunun icerigini uretir - kullanici
    "Temmuz 2026" yerine Jira'da gordugu sprint adini gorur."""

    def test_ay_kendi_sprint_adina_eslenir(self):
        df = _frame([
            _raw_row("A", "03-Mar-26 03:02", ["MS Sprint - Temmuz 26"]),
            _raw_row("B", "03-Mar-26 03:02", ["MS Sprint - Agustos 26"]),
        ])
        self.assertEqual(
            build_month_sprint_labels(df),
            {"Temmuz 2026": ["MS Sprint - Temmuz 26"], "Ağustos 2026": ["MS Sprint - Agustos 26"]},
        )

    def test_sprintsiz_ay_eslemede_yer_almaz(self):
        """Sadece `created` uzerinden ulasilan aylar sozlukte BULUNMAZ - arayuz bu
        aylari "(sprintsiz)" olarak isaretleyebilsin diye."""
        df = _frame([_raw_row("Sprintsiz", "15-Jul-26 10:00", [])])
        self.assertEqual(build_month_sprint_labels(df), {})

    def test_ayni_ayda_birden_fazla_sprint_hepsi_listelenir(self):
        df = _frame([
            _raw_row("A", "03-Mar-26 03:02", ["MS Sprint - Temmuz 26"]),
            _raw_row("B", "03-Mar-26 03:02", ["Temmuz 26 Hotfix"]),
        ])
        self.assertEqual(
            build_month_sprint_labels(df)["Temmuz 2026"],
            ["MS Sprint - Temmuz 26", "Temmuz 26 Hotfix"],
        )


class DropDuplicateRowsTests(unittest.TestCase):
    """Panelin "Ekip & Kişiler" sayfasi (`_metrics_by_role`) satirlari
    tekillestirir. Standardize edilmis veri LISTE iceren kolonlar tasidigi icin
    (`sprint`, `sprint_months`, `developers`, `analysts`) duz `drop_duplicates()`
    "TypeError: unhashable type: 'list'" ile duserdi."""

    def test_liste_kolonlu_veride_hata_firlatmaz(self):
        df = _frame([
            _raw_row("Kart", "03-Mar-26 03:02", ["MS Sprint - Temmuz 26"], assignee="KULLANICI A"),
            _raw_row("Kart", "03-Mar-26 03:02", ["MS Sprint - Temmuz 26"], assignee="KULLANICI A"),
        ])
        self.assertEqual(len(drop_duplicate_rows(df)), 1)

    def test_liste_kolonlari_sonucta_KORUNUR(self):
        """Liste kolonlari anahtardan cikarilir ama SILINMEZ - `sprint_months`
        silinseydi ay atamasi sessizce `created` tarihine duserdi."""
        df = _frame([_raw_row("Kart", "03-Mar-26 03:02", ["MS Sprint - Temmuz 26"])])
        result = drop_duplicate_rows(df)
        self.assertIn("sprint_months", result.columns)
        self.assertEqual(result.iloc[0]["sprint_months"], ["2026-07"])

    def test_ekip_metrikleri_sprint_ayina_gore_hesaplanir(self):
        """"Ekip & Kişiler" sayfasinin tam zinciri: explode -> dedup -> metrik.
        Mart'ta acilip Temmuz sprintine devretmis kart Temmuz metriklerine girmeli."""
        df = _frame([
            _raw_row("Devreden", "03-Mar-26 03:02", ["MS Sprint - Temmuz 26"],
                     sp=13.0, assignee="KULLANICI A", developers="KULLANICI A, KULLANICI B"),
        ])
        combined = pd.concat(
            [
                df.assign(person=df["assignee"]),
                explode_by_role(df, "developers", fallback_to_assignee=False),
            ],
            ignore_index=True,
        )
        role_scoped = combined.drop(
            columns=["assignee", "developers", "analysts"], errors="ignore"
        ).rename(columns={"person": "assignee"})
        role_scoped = drop_duplicate_rows(role_scoped).reset_index(drop=True)

        metrics = calculate_assignee_metrics(role_scoped, target_month="Temmuz 2026")
        # Ayni kisi hem assignee hem developer - kart onun icin BIR kez sayilmali.
        self.assertEqual(sorted(metrics["Sorumlu"]), ["KULLANICI A", "KULLANICI B"])
        self.assertEqual(list(metrics["Toplam İş Sayısı"]), [1, 1])


class LatestMonthTests(unittest.TestCase):
    def test_en_guncel_ay_sprintten_belirlenir(self):
        """En guncel ay, en son ACILAN karta degil, en ileri SPRINTe gore
        belirlenmeli."""
        df = _frame([
            _raw_row("Eski acilis ileri sprint", "03-Mar-26 03:02", ["MS Sprint - Agustos 26"]),
            _raw_row("Yeni acilis geri sprint", "31-Jul-26 23:00", ["MS Sprint - Haziran 26"]),
        ])
        self.assertEqual(latest_month_label(df), "Ağustos 2026")


if __name__ == "__main__":
    unittest.main()
