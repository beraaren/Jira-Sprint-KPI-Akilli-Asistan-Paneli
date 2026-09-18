"""`src/processor.py`'deki Jira Data Center REST API entegrasyonu (`discover_jira_fields`,
`fetch_issues_from_jira_api`) icin, gercek bir Jira sunucusuna baglanmadan `requests.get`'i
mock'layarak calisan birim testleri.

Calistirmak icin proje kokunden: `python -m unittest tests.test_jira_api -v`
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from processor import (  # noqa: E402
    JiraApiError,
    JiraSslError,
    discover_jira_fields,
    fetch_issues_from_jira_api,
    standardize_dataframe,
)

BASE_URL = "https://jira.example.com"
TOKEN = "dummy-pat-token"
PROJECT_KEY = "MS"

# Gercek bir Jira `/rest/api/2/field` yanitini taklit eder - Ingilizce/Turkce
# karisik alan adlari VE eslesmemesi gereken "capraz" (decoy) alanlar icerir.
FAKE_FIELDS = [
    {"id": "customfield_10010", "name": "Story Points"},
    {"id": "customfield_10020", "name": "Puan (Effort)"},
    {"id": "customfield_10030", "name": "Developers"},
    {"id": "customfield_10031", "name": "Geliştirici Ekibi"},
    {"id": "customfield_10040", "name": "Analists"},
    {"id": "customfield_10041", "name": "Business Analyst"},
    {"id": "customfield_10050", "name": "Sprint"},
    {"id": "summary", "name": "Summary"},
    {"id": "customfield_99999", "name": "Renk Kodu"},  # hicbir varyantla eslesmemeli
]


def _make_response(status_code: int, json_data: dict | list | None = None) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.json.return_value = json_data or {}
    if status_code >= 400 and status_code not in (400, 401, 403):
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(f"{status_code} error")
    else:
        response.raise_for_status.return_value = None
    return response


class DiscoverJiraFieldsTests(unittest.TestCase):
    def test_field_candidates_match_multiple_variants(self):
        """Story Points/Developer/Analist adaylari, TEK bir Ingilizce kelimeye
        degil, coklu (Turkce dahil) varyanta gore doğru bulunmali; alakasiz
        alanlar (orn. "Renk Kodu") hicbir adaya girmemeli."""
        sample_issues = [{"key": "MS-1", "fields": {"summary": "Test kart"}}]

        field_response = _make_response(200, FAKE_FIELDS)
        search_response = _make_response(200, {"issues": sample_issues, "total": 1})

        with patch("processor.requests.get", side_effect=[field_response, search_response]) as mock_get:
            result = discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)

        # Authorization header'i dogru gonderildi mi?
        first_call_kwargs = mock_get.call_args_list[0].kwargs
        self.assertEqual(first_call_kwargs["headers"]["Authorization"], f"Bearer {TOKEN}")

        story_point_ids = {f["id"] for f in result["candidates"]["story_points"]}
        self.assertEqual(story_point_ids, {"customfield_10010", "customfield_10020"})

        developer_ids = {f["id"] for f in result["candidates"]["developer"]}
        self.assertEqual(developer_ids, {"customfield_10030", "customfield_10031"})

        analyst_ids = {f["id"] for f in result["candidates"]["analyst"]}
        self.assertEqual(analyst_ids, {"customfield_10040", "customfield_10041"})

        self.assertEqual(result["all_fields"], FAKE_FIELDS)
        self.assertEqual(result["sample_issues"], sample_issues)

    def test_no_matching_candidates_returns_empty_list_not_error(self):
        fields = [{"id": "customfield_1", "name": "Tamamen Alakasiz Alan"}]
        field_response = _make_response(200, fields)
        search_response = _make_response(200, {"issues": [], "total": 0})

        with patch("processor.requests.get", side_effect=[field_response, search_response]):
            result = discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)

        self.assertEqual(result["candidates"]["story_points"], [])
        self.assertEqual(result["candidates"]["developer"], [])
        self.assertEqual(result["candidates"]["analyst"], [])
        self.assertEqual(result["sample_issues"], [])


class FetchIssuesFromJiraApiTests(unittest.TestCase):
    FIELD_MAP = {
        "story_points": "customfield_10010",
        "developer": "customfield_10030",
        "analyst": "customfield_10040",
    }

    # Sprint alani kullaniciya SORULMAZ; `fetch_issues_from_jira_api` adini
    # `/field` uzerinden kendisi bulur (bkz. processor.find_sprint_field_id).
    # Bu yuzden her cekim, arama isteklerinden ONCE bir `/field` istegi yapar.
    def _field_response(self) -> MagicMock:
        return _make_response(200, FAKE_FIELDS)

    def _search_calls(self, mock_get: MagicMock) -> list:
        return [c for c in mock_get.call_args_list if c.args[0].endswith("/search")]

    def _fake_issue(self, key: str, summary: str, developers: list[str], analysts: list[str]) -> dict:
        return {
            "key": key,
            "fields": {
                "summary": summary,
                "issuetype": {"name": "Task"},
                "status": {"name": "Done" if "Done" in summary else "In Progress"},
                "assignee": {"displayName": "Kullanıcı A"},
                "project": {"name": "Mobil Squad"},
                "components": [{"name": "Backend"}],
                "labels": ["SprintDışı"] if "plandisi" in summary else [],
                "created": "2026-07-15T10:30:00.000+0300",
                "resolutiondate": None,
                "customfield_10010": 5,
                "customfield_10030": [{"displayName": d} for d in developers],
                "customfield_10040": [{"displayName": a} for a in analysts],
                "customfield_10050": [{"name": "MS Sprint - Temmuz 26"}],
            },
        }

    def test_pagination_collects_all_pages(self):
        """total=150, sayfa boyutu 100 -> 2 sayfa cekilmeli (100 + 50)."""
        page1_issues = [
            self._fake_issue(f"MS-{i}", f"Kart {i}", ["Kullanıcı B"], ["Kullanıcı C"]) for i in range(100)
        ]
        page2_issues = [
            self._fake_issue(f"MS-{i}", f"Kart {i}", ["Kullanıcı D"], []) for i in range(100, 150)
        ]
        response_page1 = _make_response(200, {"issues": page1_issues, "total": 150})
        response_page2 = _make_response(200, {"issues": page2_issues, "total": 150})

        side_effect = [self._field_response(), response_page1, response_page2]
        with patch("processor.requests.get", side_effect=side_effect) as mock_get:
            df = fetch_issues_from_jira_api(BASE_URL, TOKEN, PROJECT_KEY, self.FIELD_MAP, months_back=6)

        self.assertEqual(len(df), 150)
        search_calls = self._search_calls(mock_get)
        self.assertEqual(len(search_calls), 2)
        start_ats = [call.kwargs["params"]["startAt"] for call in search_calls]
        self.assertEqual(start_ats, [0, 100])

        # Sadece gerekli alanlar istendi mi (performans)?
        requested = search_calls[0].kwargs["params"]["fields"]
        self.assertIn("customfield_10010", requested)
        self.assertIn("summary", requested)
        # Sprint alani otomatik bulunup istege eklenmis olmali.
        self.assertIn("customfield_10050", requested)

    def test_multi_value_developer_field_and_standardize_dataframe_roundtrip(self):
        """Coklu developer/analist listesi virgulle-ayrilmis TEK hucreye donmeli
        VE sonuc DataFrame standardize_dataframe'den sorunsuz gecmeli - HTML
        disa aktarimindan gelen veriyle AYNI bicimde."""
        issue = self._fake_issue("MS-914", "Test kart", ["Kullanıcı C", "Kullanıcı D"], ["Kullanıcı E"])
        response = _make_response(200, {"issues": [issue], "total": 1})

        with patch("processor.requests.get", side_effect=[self._field_response(), response]):
            raw_df = fetch_issues_from_jira_api(BASE_URL, TOKEN, PROJECT_KEY, self.FIELD_MAP)

        self.assertEqual(raw_df.loc[0, "Developers"], "Kullanıcı C, Kullanıcı D")
        self.assertEqual(raw_df.loc[0, "Story Points"], 5)
        self.assertEqual(raw_df.loc[0, "Sprint"], "MS Sprint - Temmuz 26")

        # standardize_dataframe HIC DEGISTIRILMEDEN calismali
        std_df = standardize_dataframe(raw_df)
        self.assertEqual(std_df.loc[0, "developers"], ["Kullanıcı C", "Kullanıcı D"])
        self.assertEqual(std_df.loc[0, "estimate"], 5.0)
        self.assertEqual(std_df.loc[0, "assignee"], "Kullanıcı A")
        self.assertTrue(pd.notna(std_df.loc[0, "created"]), "ISO 8601 'created' tarihi doğru ayrıştırılmalı")
        # Sprint, CSV disa aktarimindan gelmis gibi aya cozulmus olmali.
        self.assertEqual(std_df.loc[0, "sprint"], ["MS Sprint - Temmuz 26"])
        self.assertEqual(std_df.loc[0, "sprint_months"], ["2026-07"])

    def test_zero_results_returns_empty_dataframe_not_error(self):
        response = _make_response(200, {"issues": [], "total": 0})
        with patch("processor.requests.get", side_effect=[self._field_response(), response]):
            df = fetch_issues_from_jira_api(BASE_URL, TOKEN, PROJECT_KEY, self.FIELD_MAP)
        self.assertTrue(df.empty)
        self.assertIn("Issue Type", df.columns)
        self.assertIn("Sprint", df.columns)

    def test_unmapped_optional_fields_are_blank_not_crash(self):
        """story_points/developer/analyst haritalanmamissa (None) hata firlatmadan
        bos kolon uretmeli."""
        issue = self._fake_issue("MS-1", "Kart", [], [])
        response = _make_response(200, {"issues": [issue], "total": 1})
        empty_map = {"story_points": None, "developer": None, "analyst": None}
        with patch("processor.requests.get", side_effect=[self._field_response(), response]):
            df = fetch_issues_from_jira_api(BASE_URL, TOKEN, PROJECT_KEY, empty_map)
        self.assertEqual(df.loc[0, "Story Points"], "")
        self.assertEqual(df.loc[0, "Developers"], "")
        standardize_dataframe(df)  # hata firlatmamali

    def test_sprint_field_lookup_failure_does_not_break_fetch(self):
        """Sprint alani `/field` uzerinden bulunamazsa (orn. istek patlarsa) cekim
        DUSMEMELI; sadece Sprint kolonu bos kalmali - ay atamasi `created`
        tarihine duser (bkz. processor._row_month_keys)."""
        issue = self._fake_issue("MS-2", "Kart", [], [])
        response = _make_response(200, {"issues": [issue], "total": 1})
        broken_field_response = _make_response(200, {"beklenmedik": "bicim"})

        with patch("processor.requests.get", side_effect=[broken_field_response, response]):
            df = fetch_issues_from_jira_api(BASE_URL, TOKEN, PROJECT_KEY, self.FIELD_MAP)

        self.assertEqual(len(df), 1)
        self.assertEqual(df.loc[0, "Sprint"], "")
        std_df = standardize_dataframe(df)
        self.assertEqual(std_df.loc[0, "sprint_months"], [])


class JiraApiErrorMessageTests(unittest.TestCase):
    def test_401_unauthorized(self):
        response = _make_response(401)
        with patch("processor.requests.get", return_value=response):
            with self.assertRaises(JiraApiError) as ctx:
                discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)
        self.assertIn("Token geçersiz", str(ctx.exception))

    def test_403_forbidden_includes_project_key(self):
        response = _make_response(403)
        with patch("processor.requests.get", return_value=response):
            with self.assertRaises(JiraApiError) as ctx:
                discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)
        self.assertIn(PROJECT_KEY, str(ctx.exception))
        self.assertIn("erişim yetkisi yok", str(ctx.exception))

    def test_400_bad_request_jql_hint(self):
        response = _make_response(400)
        with patch("processor.requests.get", return_value=response):
            with self.assertRaises(JiraApiError) as ctx:
                discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)
        self.assertIn("Proje anahtarı", str(ctx.exception))
        self.assertIn(PROJECT_KEY, str(ctx.exception))

    def test_connection_error(self):
        with patch("processor.requests.get", side_effect=requests.exceptions.ConnectionError("boom")):
            with self.assertRaises(JiraApiError) as ctx:
                discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)
        self.assertIn("Jira sunucusuna ulaşılamadı", str(ctx.exception))

    def test_timeout(self):
        with patch("processor.requests.get", side_effect=requests.exceptions.Timeout("boom")):
            with self.assertRaises(JiraApiError) as ctx:
                discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)
        self.assertIn("Jira sunucusuna ulaşılamadı", str(ctx.exception))

    def test_ssl_error(self):
        with patch("processor.requests.get", side_effect=requests.exceptions.SSLError("boom")):
            with self.assertRaises(JiraApiError) as ctx:
                discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)
        self.assertIn("Güvenlik sertifikası doğrulanamadı", str(ctx.exception))

    def test_ssl_error_is_a_distinct_catchable_subclass(self):
        """Arayuzun 'checkbox isaretliyse verify=False ile otomatik tekrar dene'
        mantigi, SSL hatasini DIGER JiraApiError turlerinden (401/403/400/timeout)
        ayirt edebilmelidir - bu yuzden JiraSslError ayri, yakalanabilir bir alt
        sinif olmali (bkz. app/new_dashboard.py'deki yeniden deneme mantigi)."""
        with patch("processor.requests.get", side_effect=requests.exceptions.SSLError("boom")):
            with self.assertRaises(JiraSslError):
                discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)

        response = _make_response(401)
        with patch("processor.requests.get", return_value=response):
            try:
                discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)
            except JiraSslError:
                self.fail("401 hatası JiraSslError olarak sınıflandırılmamalı")
            except JiraApiError:
                pass

    def test_token_never_leaks_into_error_message(self):
        response = _make_response(401)
        with patch("processor.requests.get", return_value=response):
            with self.assertRaises(JiraApiError) as ctx:
                discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY)
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_verify_false_is_passed_through_on_ssl_bypass_retry(self):
        """Arayuzdeki 'SSL doğrulamayı atla' secilip verify=False ile tekrar
        cagirildiginda, bu deger requests.get'e dogru iletilmeli."""
        response = _make_response(200, FAKE_FIELDS)
        search_response = _make_response(200, {"issues": [], "total": 0})
        with patch("processor.requests.get", side_effect=[response, search_response]) as mock_get:
            discover_jira_fields(BASE_URL, TOKEN, PROJECT_KEY, verify=False)
        for call in mock_get.call_args_list:
            self.assertFalse(call.kwargs["verify"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
