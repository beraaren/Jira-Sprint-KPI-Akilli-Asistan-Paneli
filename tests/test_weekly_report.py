"""`src/weekly_report.py` - kural tabanli haftalik sprint ici performans ozeti.

Testler iki katmani AYRI dogrular:
    A) `analyze_week` -> dogru bulgulari/severity'leri uretiyor mu (sayisal)
    B) `render_weekly_text`/`render_weekly_html` -> bulgulari metne ceviriyor mu

Calistirmak icin proje kokunden: `python -m unittest tests.test_weekly_report -v`
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

from processor import standardize_dataframe  # noqa: E402
from weekly_report import (  # noqa: E402
    DURGUNLUK_ESIGI_GUN,
    WeeklyReportError,
    analyze_week,
    available_weeks,
    render_weekly_html,
    render_weekly_text,
    week_bounds,
    week_label,
    week_sprint_month,
)


def _row(summary: str, last_transition: str, status: str = "Done", sp: float = 5.0,
         sprint: str = "MS Sprint - Temmuz 26", labels: str = "", assignee: str = "",
         created: str = "01-Jul-26 09:00") -> dict:
    return {
        "Issue Type": "Task",
        "Summary": summary,
        "Status": status,
        "Labels": labels,
        "Created": created,
        "Custom field (Story Points)": sp,
        "Assignee": assignee,
        "Sprint": sprint,
        "Custom field (Last Transition)": last_transition,
    }


def _frame(rows: list[dict]) -> pd.DataFrame:
    return standardize_dataframe(pd.DataFrame(rows))


# 2026-07-13 Pazartesi -> 2026-07-19 Pazar (H29)
HAFTA = pd.Timestamp("2026-07-13")


class WeekBoundaryTests(unittest.TestCase):
    def test_hafta_pazartesi_pazar_araligidir(self):
        start, end = week_bounds(pd.Timestamp("2026-07-16").date())  # Persembe
        self.assertEqual(start.strftime("%Y-%m-%d %A"), "2026-07-13 Monday")
        self.assertEqual(end.strftime("%Y-%m-%d"), "2026-07-19")

    def test_ay_sinirini_asan_hafta_persembesinin_ayina_aittir(self):
        """27 Tem - 2 Agu haftasinin 5 gunu Temmuz'da; Agustos'a yazilmamali."""
        self.assertEqual(str(week_sprint_month(pd.Timestamp("2026-07-27"))), "2026-07")
        # 31 Agu - 6 Eyl: persembe 3 Eylul -> Eylul.
        self.assertEqual(str(week_sprint_month(pd.Timestamp("2026-08-31"))), "2026-09")

    def test_hafta_etiketi_okunakli(self):
        self.assertEqual(week_label(pd.Timestamp("2026-08-03")), "03-09 Ağustos 2026 (H32)")
        self.assertEqual(
            week_label(pd.Timestamp("2026-07-27")), "27 Temmuz - 02 Ağustos 2026 (H31)"
        )


class MissingDataTests(unittest.TestCase):
    def test_last_transition_kolonu_yoksa_acik_hata(self):
        """Yaniltici bir '0 iş' raporu yerine acik hata verilmeli."""
        df = _frame([_row("Kart", "2026-07-15 10:00:00.0")]).drop(columns=["last_transition"])
        with self.assertRaises(WeeklyReportError) as ctx:
            analyze_week(df, week_start=HAFTA)
        self.assertIn("Last Transition", str(ctx.exception))

    def test_last_transition_tamamen_bossa_acik_hata(self):
        df = _frame([_row("Kart", "")])
        with self.assertRaises(WeeklyReportError) as ctx:
            analyze_week(df, week_start=HAFTA)
        self.assertIn("boş", str(ctx.exception))


class CompletedWorkTests(unittest.TestCase):
    def test_sadece_o_hafta_done_olanlar_sayilir(self):
        df = _frame([
            _row("Bu hafta biten", "2026-07-15 10:00:00.0", sp=8.0),
            _row("Onceki hafta biten", "2026-07-06 10:00:00.0", sp=13.0),
            _row("Bu hafta hareket etti ama bitmedi", "2026-07-16 10:00:00.0",
                 status="In Progress", sp=21.0),
        ])
        m = analyze_week(df, week_start=HAFTA)["metrikler"]
        self.assertEqual(m["tamamlanan_kart"], 1)
        self.assertEqual(m["tamamlanan_sp"], 8.0)
        self.assertEqual(m["hareket_eden_kart"], 2)

    def test_hic_is_bitmediyse_dikkat_bulgusu(self):
        df = _frame([_row("Acik", "2026-07-15 10:00:00.0", status="In Progress")])
        bulgular = {b.signal: b for b in analyze_week(df, week_start=HAFTA)["bulgular"]}
        self.assertEqual(bulgular["tamamlanan"].severity, "dikkat")
        self.assertEqual(bulgular["tamamlanan"].veri["kart"], 0)


class TempoTests(unittest.TestCase):
    def _df_with_history(self, bu_hafta_sp: float) -> pd.DataFrame:
        rows = [_row(f"Gecmis {i}", f"2026-0{7 if i < 13 else 6}-{i:02d} 10:00:00.0", sp=10.0)
                for i in (8, 9, 1, 2)]  # onceki haftalara dagilmis, her biri 10 SP
        rows.append(_row("Bu hafta", "2026-07-15 10:00:00.0", sp=bu_hafta_sp))
        return _frame(rows)

    def test_tempo_dususu_dikkat_olarak_isaretlenir(self):
        bulgular = {b.signal: b for b in analyze_week(self._df_with_history(1.0), week_start=HAFTA)["bulgular"]}
        self.assertEqual(bulgular["tempo"].severity, "dikkat")
        self.assertLess(bulgular["tempo"].veri["sapma_yuzde"], 0)

    def test_tempo_artisi_iyi_olarak_isaretlenir(self):
        bulgular = {b.signal: b for b in analyze_week(self._df_with_history(100.0), week_start=HAFTA)["bulgular"]}
        self.assertEqual(bulgular["tempo"].severity, "iyi")


class StaleWorkTests(unittest.TestCase):
    def test_esik_gunu_asan_acik_isler_duran_sayilir(self):
        eski = pd.Timestamp("2026-07-19") - pd.Timedelta(DURGUNLUK_ESIGI_GUN + 10, "D")
        df = _frame([
            _row("Duran", eski.strftime("%Y-%m-%d %H:%M:%S.0"), status="To Do", sp=8.0),
            _row("Taze", "2026-07-17 10:00:00.0", status="In Progress", sp=3.0),
        ])
        bulgular = {b.signal: b for b in analyze_week(df, week_start=HAFTA)["bulgular"]}
        self.assertIn("duran_isler", bulgular)
        self.assertEqual(bulgular["duran_isler"].veri["kart"], 1)
        self.assertEqual(bulgular["duran_isler"].veri["ornekler"][0]["is"], "Duran")

    def test_terminal_statuler_duran_sayilmaz(self):
        """Done/Cancelled kartlar aylardir hareketsiz olsa da 'duran iş' degildir."""
        eski = "2025-01-05 10:00:00.0"
        df = _frame([
            _row("Bitmis", eski, status="Done"),
            _row("Iptal", eski, status="Cancelled"),
        ])
        bulgular = {b.signal: b for b in analyze_week(df, week_start=HAFTA)["bulgular"]}
        self.assertNotIn("duran_isler", bulgular)


class ScopeAndPeopleTests(unittest.TestCase):
    def test_plan_disi_orani_hesaplanir(self):
        df = _frame([
            _row("Planli", "2026-07-15 10:00:00.0", sp=10.0),
            _row("Plan disi", "2026-07-15 11:00:00.0", sp=10.0, labels="SprintDışı"),
        ])
        bulgular = {b.signal: b for b in analyze_week(df, week_start=HAFTA)["bulgular"]}
        self.assertEqual(bulgular["plan_disi"].veri["oran_yuzde"], 50.0)
        self.assertEqual(bulgular["plan_disi"].severity, "dikkat")

    def test_bloke_isler_raporlanir(self):
        df = _frame([_row("Bloke", "2026-07-15 10:00:00.0", status="XL BLOCK", sp=13.0)])
        bulgular = {b.signal: b for b in analyze_week(df, week_start=HAFTA)["bulgular"]}
        self.assertEqual(bulgular["bloke"].veri["kart"], 1)
        self.assertEqual(bulgular["bloke"].veri["bu_hafta_bloke_olan"], 1)

    def test_haftanin_en_cok_katki_verenini_bulur(self):
        df = _frame([
            _row("A", "2026-07-15 10:00:00.0", sp=21.0, assignee="KULLANICI A"),
            _row("B", "2026-07-16 10:00:00.0", sp=3.0, assignee="KULLANICI B"),
        ])
        bulgular = {b.signal: b for b in analyze_week(df, week_start=HAFTA)["bulgular"]}
        self.assertEqual(bulgular["kisi_katki"].veri["kisi"], "KULLANICI A")
        self.assertEqual(bulgular["kisi_katki"].veri["katilan_kisi_sayisi"], 2)

    def test_yuk_dengesizligi_tespit_edilir(self):
        df = _frame([
            _row("Yuklu", "2026-07-15 10:00:00.0", status="To Do", sp=100.0, assignee="KULLANICI A"),
            _row("Hafif", "2026-07-15 10:00:00.0", status="To Do", sp=5.0, assignee="KULLANICI B"),
        ])
        bulgular = {b.signal: b for b in analyze_week(df, week_start=HAFTA)["bulgular"]}
        self.assertEqual(bulgular["kisi_yuku"].veri["riskli"][0]["kisi"], "KULLANICI A")


class RenderTests(unittest.TestCase):
    def setUp(self):
        df = _frame([
            _row("Biten iş", "2026-07-15 10:00:00.0", sp=8.0, assignee="KULLANICI A"),
            _row("Bloke iş", "2026-07-16 10:00:00.0", status="XL BLOCK", sp=13.0),
        ])
        self.result = analyze_week(df, week_start=HAFTA)

    def test_metin_hafta_sprint_ve_veri_notu_icerir(self):
        text = render_weekly_text(self.result)
        self.assertIn("13-19 Temmuz 2026 (H29)", text)
        self.assertIn("Temmuz 2026", text)
        self.assertIn("Last Transition", text)  # veri sinirinin acikca yazilmasi

    def test_metin_her_bulgu_icin_bir_satir_yazar(self):
        satirlar = [s for s in render_weekly_text(self.result).splitlines() if s.startswith(("🔴", "🟠", "🟢", "⚪"))]
        self.assertEqual(len(satirlar), len(self.result["bulgular"]))

    def test_metin_kisa_kalir(self):
        """Ozet 'kisa haftalik not' olmali - bulgu basina tek cumle."""
        self.assertLessEqual(len(render_weekly_text(self.result).splitlines()), 20)

    def test_html_tek_parca_ve_epostaya_uygun(self):
        h = render_weekly_html(self.result)
        self.assertTrue(h.startswith("<!DOCTYPE html>"))
        # E-posta istemcileri <style> bloklarini soyar - stiller satir ici olmali.
        self.assertNotIn("<style", h)
        # Disaridan kaynak cekilmemeli (e-posta/çevrimdışı uyumu).
        self.assertNotIn("http://", h)
        self.assertNotIn("https://", h)
        self.assertIn("Haftalık Jira Özeti", h)

    def test_html_kullanici_metnini_kacisla_yazar(self):
        df = _frame([_row("<script>alert(1)</script>", "2026-07-15 10:00:00.0")])
        h = render_weekly_html(analyze_week(df, week_start=HAFTA))
        self.assertNotIn("<script>alert", h)


class AvailableWeeksTests(unittest.TestCase):
    def test_haftalar_en_yeniden_eskiye_siralanir(self):
        df = _frame([
            _row("A", "2026-07-15 10:00:00.0"),
            _row("B", "2026-06-10 10:00:00.0"),
            _row("C", "2026-08-05 10:00:00.0"),
        ])
        weeks = available_weeks(df)
        self.assertEqual([w.strftime("%Y-%m-%d") for w in weeks],
                         ["2026-08-03", "2026-07-13", "2026-06-08"])

    def test_hafta_verilmezse_en_son_hafta_kullanilir(self):
        df = _frame([
            _row("Eski", "2026-06-10 10:00:00.0"),
            _row("Yeni", "2026-08-05 10:00:00.0"),
        ])
        self.assertEqual(analyze_week(df)["hafta_basi"].strftime("%Y-%m-%d"), "2026-08-03")


if __name__ == "__main__":
    unittest.main()
