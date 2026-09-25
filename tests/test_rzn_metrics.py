"""RZN phase statuses and sprint scope must not hide active cards."""

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from processor import analyze_advanced_bottlenecks, standardize_dataframe  # noqa: E402
from weekly_report import _is_bloke, _is_terminal  # noqa: E402


class RznMetricsTests(unittest.TestCase):
    def test_phase_block_and_turkish_cancelled_status(self):
        statuses = pd.Series(["Analiz XL Block", "Development XL BLOCK", "Test XL Block", "İptal", "Analiz Done"])
        self.assertEqual(_is_bloke(statuses).tolist(), [True, True, True, False, False])
        self.assertEqual(_is_terminal(statuses).tolist(), [False, False, False, True, False])

    def test_selected_sprint_counts_active_work_without_matching_previous_name(self):
        raw = pd.DataFrame([
            {"Issue Type": "Task", "Summary": "Old phase", "Status": "Done",
             "Assignee": "Ada", "Story Points": 3, "Created": "2026-08-02",
             "Sprint": "Ağustos İterasyonu - 2026"},
            {"Issue Type": "Task", "Summary": "New test", "Status": "Test",
             "Assignee": "Ada", "Story Points": 5, "Created": "2026-08-03",
             "Sprint": "Eylül İterasyonu - 2026"},
            {"Issue Type": "Task", "Summary": "Finished test phase", "Status": "Test Done",
             "Assignee": "Ece", "Story Points": 2, "Created": "2026-09-03",
             "Sprint": "Eylül İterasyonu - 2026"},
            {"Issue Type": "Task", "Summary": "Older active phase", "Status": "Development",
             "Assignee": "Ada", "Story Points": 1, "Created": "2026-05-03",
             "Sprint": "Eylül İterasyonu - 2026"},
            {"Issue Type": "Task", "Summary": "Cancelled", "Status": "İptal",
             "Assignee": "Ece", "Story Points": 1, "Created": "2026-09-04",
             "Sprint": "Eylül İterasyonu - 2026"},
        ])
        result = analyze_advanced_bottlenecks(standardize_dataframe(raw), "Eylül 2026")

        wip = result["1_wip_aging"]
        self.assertEqual(wip["aktif_is_sayisi"], 3)
        self.assertEqual(wip["onceki_ay"]["is_sayisi"], 2)
        self.assertEqual(wip["uc_aylik"]["is_sayisi"], 1)
        self.assertEqual(sum(wip[key]["is_sayisi"] for key in ("onceki_ay", "uc_aylik", "alti_aylik")), 3)
        self.assertEqual(result["4_reopen_rate"]["reopen_is_sayisi"], 1)
        self.assertEqual(result["3_assignee_bouncing"]["kisi_sayisi"], 2)


if __name__ == "__main__":
    unittest.main()
