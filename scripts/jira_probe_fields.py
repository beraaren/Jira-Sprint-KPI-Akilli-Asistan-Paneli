"""Bir projede HANGI ozel alanlarin GERCEKTEN dolu oldugunu ornek kartlara
bakarak bulur.

Neden gerekli: `discover_jira_fields`'in ad-benzerligine dayali otomatik
eslestirmesi, binlerce ozel alani olan buyuk kurumsal Jira'larda yaniltici
sonuc verir (orn. "sp" alt dizesi "Sponsordan..." alanina denk gelir). Alan
ADINA degil, alanin o projedeki DOLULUK ORANINA ve ornek DEGERLERINE bakmak
tek guvenilir yontemdir.

Calistirmak icin proje kokunden:
    .venv\\Scripts\\python.exe scripts/jira_probe_fields.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import requests  # noqa: E402

from config import load_jira_config  # noqa: E402
from processor import _jira_auth_headers, _jql_project_term, jira_person_field_to_display  # noqa: E402

SAMPLE_SIZE = 100

# Ilgilendigimiz alanlari, ADLARINDA gectiginde raporlanacak anahtar kelimeler.
# Genis tutulur - eleme, doluluk oranina ve ornek degerlere bakilarak ELLE
# yapilir (otomatik "en iyi tahmin" bu scriptin isi degildir).
INTERESTING = {
    "STORY POINTS ADAYLARI": ["story point", "puan", "point", "estimate", "sp "],
    "GELISTIRICI ADAYLARI": ["developer", "geliştirici", "gelistirici", "developers"],
    "ANALIST ADAYLARI": ["analist", "analyst", "analists", "analysts"],
}


def _short(value: object, limit: int = 60) -> str:
    """Bir alan degerini tek satirlik, okunakli bir ozete cevirir."""
    if isinstance(value, (dict, list)):
        rendered = jira_person_field_to_display(value)
        if not rendered:
            rendered = str(value)
    else:
        rendered = str(value)
    rendered = " ".join(rendered.split())
    return rendered[:limit] + ("..." if len(rendered) > limit else "")


def main() -> int:
    cfg = load_jira_config()
    if not cfg.has_credentials:
        print("[HATA] .env icinde JIRA_BASE_URL / JIRA_PAT / JIRA_PROJECT_KEY dolu olmali.")
        return 1

    headers = _jira_auth_headers(cfg.token)
    verify = not cfg.skip_ssl

    # 1) Alan ID -> ad haritasi
    fields = requests.get(
        f"{cfg.base_url}/rest/api/2/field", headers=headers, timeout=30, verify=verify
    ).json()
    id_to_name = {f["id"]: f.get("name", f["id"]) for f in fields}

    # 2) Ornek kartlar - TUM alanlariyla
    jql = f"{_jql_project_term(cfg.project_key)} ORDER BY created DESC"
    response = requests.get(
        f"{cfg.base_url}/rest/api/2/search",
        headers=headers,
        params={"jql": jql, "maxResults": SAMPLE_SIZE, "fields": "*all"},
        timeout=60,
        verify=verify,
    )
    response.raise_for_status()
    issues = response.json().get("issues", [])
    if not issues:
        print(f"[HATA] '{cfg.project_key}' projesinde kart bulunamadi.")
        return 1

    print(f"'{cfg.project_key}' projesinden {len(issues)} kart incelendi.\n")

    # 3) Her alanin kac kartta DOLU oldugunu ve ornek degerlerini topla
    filled = Counter()
    samples: dict[str, list[str]] = {}
    for issue in issues:
        for fid, value in (issue.get("fields") or {}).items():
            if value in (None, "", [], {}):
                continue
            filled[fid] += 1
            samples.setdefault(fid, [])
            if len(samples[fid]) < 3:
                rendered = _short(value)
                if rendered and rendered not in samples[fid]:
                    samples[fid].append(rendered)

    total = len(issues)
    for baslik, keywords in INTERESTING.items():
        print("=" * 78)
        print(baslik)
        print("=" * 78)
        matches = [
            fid
            for fid in id_to_name
            if any(k in id_to_name[fid].lower() for k in keywords)
        ]
        # DOLULUK ORANINA gore sirala - bos alanlar isimleri ne olursa olsun ise yaramaz.
        matches.sort(key=lambda fid: filled.get(fid, 0), reverse=True)
        if not matches:
            print("  (eslesen alan adi yok)\n")
            continue
        for fid in matches:
            count = filled.get(fid, 0)
            if count == 0:
                continue
            pct = 100 * count / total
            print(f"  {id_to_name[fid]}")
            print(f"      id={fid}   dolu: {count}/{total} ({pct:.0f}%)")
            print(f"      ornek: {' | '.join(samples.get(fid, []))}")
        bos = [fid for fid in matches if filled.get(fid, 0) == 0]
        if bos:
            print(f"\n  Bu projede TAMAMEN BOS olan {len(bos)} aday atlandi.")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
