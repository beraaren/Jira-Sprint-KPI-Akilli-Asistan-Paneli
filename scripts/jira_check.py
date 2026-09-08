"""Jira baglantisini `.env`'deki ayarlarla ucdan uca test eden teshis scripti.

Panelde bir hata alindiginda, sorunun HANGI adimda oldugunu (ag/SSL, token,
proje gorunurlugu, alan eslestirmesi) tek tek ayirir - "400 Bad Request" gibi
tek bir belirsiz mesajla ugrasmak yerine, her adimin ayri ayri sonucunu ve
Jira'nin KENDI hata metnini gosterir.

Calistirmak icin proje kokunden:
    .venv\\Scripts\\python.exe scripts/jira_check.py

Token asla ekrana basilmaz.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import requests  # noqa: E402

from config import env_snippet, load_jira_config  # noqa: E402
from processor import (  # noqa: E402
    JIRA_FIELD_CANDIDATE_VARIANTS,
    JiraApiError,
    JiraSslError,
    _jira_auth_headers,
    _jira_error_detail,
    _jql_project_term,
    discover_jira_fields,
)

OK = "[ OK ]"
FAIL = "[HATA]"
INFO = "[BILGI]"


def _print_header(text: str) -> None:
    print(f"\n{'-' * 70}\n{text}\n{'-' * 70}")


def _request(url: str, token: str, params: dict | None, verify: bool) -> requests.Response:
    return requests.get(url, headers=_jira_auth_headers(token), params=params, timeout=30, verify=verify)


def _describe(response: requests.Response) -> str:
    """HTTP durum kodunu ve varsa Jira'nin kendi hata metnini tek satira sikistirir."""
    detail = _jira_error_detail(response)
    return f"HTTP {response.status_code}" + (f" - {detail}" if detail else "")


def main() -> int:
    cfg = load_jira_config()

    _print_header("0) .env okundu")
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print(f"{FAIL} .env dosyasi bulunamadi: {env_file}")
        print(f"       Once sablonu kopyalayin:  copy .env.example .env")
        return 1
    print(f"{OK} {env_file}")
    print(f"       JIRA_BASE_URL     = {cfg.base_url or '(bos)'}")
    print(f"       JIRA_PAT          = {'*** (tanimli)' if cfg.token else '(BOS!)'}")
    print(f"       JIRA_PROJECT_KEY  = {cfg.project_key or '(bos)'}")
    print(f"       JIRA_SKIP_SSL     = {cfg.skip_ssl}")

    if not cfg.base_url or not cfg.token or not cfg.project_key:
        print(f"\n{FAIL} JIRA_BASE_URL, JIRA_PAT ve JIRA_PROJECT_KEY dolu olmali.")
        return 1

    # SSL: once dogrulamali dene; sadece basarisiz olur VE .env izin verirse
    # sertifikasiz tekrar dene (panelin davranisiyla ayni).
    verify = True
    _print_header("1) Ag ve SSL")
    try:
        response = _request(f"{cfg.base_url}/rest/api/2/myself", cfg.token, None, verify=True)
        print(f"{OK} Sunucuya sertifika dogrulamasiyla ulasildi.")
    except requests.exceptions.SSLError:
        if not cfg.skip_ssl:
            print(f"{FAIL} SSL sertifikasi dogrulanamadi (kurumsal sertifika kesmesi olabilir).")
            print("       .env'de JIRA_SKIP_SSL=true yapip tekrar deneyin.")
            return 1
        print(f"{INFO} SSL dogrulamasi basarisiz; JIRA_SKIP_SSL=true oldugu icin sertifikasiz deneniyor...")
        verify = False
        try:
            response = _request(f"{cfg.base_url}/rest/api/2/myself", cfg.token, None, verify=False)
        except requests.exceptions.RequestException as exc:
            print(f"{FAIL} Sertifikasiz denemede de ulasilamadi: {type(exc).__name__}")
            return 1
        print(f"{OK} Sunucuya (sertifika dogrulamasi atlanarak) ulasildi.")
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
        print(f"{FAIL} Jira sunucusuna ulasilamadi: {type(exc).__name__}")
        print("       Kurumsal agda/VPN'de oldugunuzdan ve adresin dogru oldugundan emin olun.")
        return 1

    _print_header("2) Token gecerli mi (/rest/api/2/myself)")
    if response.status_code != 200:
        print(f"{FAIL} {_describe(response)}")
        print("       Token gecersiz/suresi dolmus olabilir. Jira profilinden yeni bir PAT olusturun.")
        return 1
    me = response.json()
    print(f"{OK} Kimlik dogrulandi: {me.get('displayName')} ({me.get('name') or me.get('key')})")

    _print_header(f"3) '{cfg.project_key}' projesi bu token'a gorunuyor mu")
    proj = _request(f"{cfg.base_url}/rest/api/2/project/{cfg.project_key}", cfg.token, None, verify)
    if proj.status_code == 200:
        data = proj.json()
        print(f"{OK} Proje bulundu: {data.get('name')}")
        print(f"       key = {data.get('key')}   id = {data.get('id')}")
        resolved_key = data.get("key") or cfg.project_key
    else:
        print(f"{FAIL} {_describe(proj)}")
        print("       Proje yok VEYA bu token'in 'Browse Projects' yetkisi yok (Jira ikisini ayirt ettirmez).")
        resolved_key = cfg.project_key

    _print_header("4) Erisilebilen projeler (ilk 25)")
    all_projects = _request(f"{cfg.base_url}/rest/api/2/project", cfg.token, None, verify)
    if all_projects.status_code == 200:
        projects = all_projects.json()
        if not projects:
            print(f"{FAIL} Bu token hicbir projeyi goremiyor - PAT'in yetkisi yok gibi gorunuyor.")
        else:
            print(f"{INFO} Toplam {len(projects)} proje goruluyor. Asagidaki 'key' degerlerinden birini")
            print("       .env icindeki JIRA_PROJECT_KEY alanina yazabilirsiniz:")
            for p in projects[:25]:
                print(f"         {p.get('key'):<12} id={p.get('id'):<8} {p.get('name')}")
            if len(projects) > 25:
                print(f"         ... ve {len(projects) - 25} tane daha")
    else:
        print(f"{FAIL} Proje listesi alinamadi: {_describe(all_projects)}")

    _print_header("5) JQL sorgusu calisiyor mu")
    jql = f"{_jql_project_term(cfg.project_key)} ORDER BY created DESC"
    print(f"{INFO} JQL: {jql}")
    search = _request(
        f"{cfg.base_url}/rest/api/2/search", cfg.token, {"jql": jql, "maxResults": 1}, verify
    )
    if search.status_code != 200:
        print(f"{FAIL} {_describe(search)}")
        print("       Yukaridaki 4. adimdaki listeden dogru 'key' degerini alip .env'i guncelleyin.")
        return 1
    total = search.json().get("total", 0)
    print(f"{OK} Sorgu calisti. Projede toplam {total:,} kart var.")
    if total == 0:
        print(f"{INFO} Kart sayisi 0 - proje dogru ama icinde kart yok (ya da hepsi gizli).")

    _print_header("6) Alan esletirmesi (Story Points / Developer / Analyst)")
    try:
        discovery = discover_jira_fields(cfg.base_url, cfg.token, cfg.project_key, verify=verify)
    except (JiraSslError, JiraApiError) as exc:
        print(f"{FAIL} {exc}")
        return 1

    suggested: dict[str, str | None] = {}
    for target in JIRA_FIELD_CANDIDATE_VARIANTS:
        candidates = discovery["candidates"].get(target, [])
        if candidates:
            suggested[target] = candidates[0]["id"]
            extra = f"  (+{len(candidates) - 1} alternatif)" if len(candidates) > 1 else ""
            print(f"{OK} {target:<13} -> {candidates[0]['name']} ({candidates[0]['id']}){extra}")
            for alt in candidates[1:]:
                print(f"       alternatif: {alt['name']} ({alt['id']})")
        else:
            suggested[target] = None
            print(f"{INFO} {target:<13} -> otomatik eslesme bulunamadi (panelden elle secebilirsiniz)")

    _print_header("SONUC - asagidakini .env dosyaniza yapistirin")
    print(env_snippet(cfg.base_url, resolved_key, suggested))
    print("\n(JIRA_PAT satirini kendi token'inizla degistirmeyi unutmayin - o satir")
    print(" guvenlik geregi bos birakildi.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
