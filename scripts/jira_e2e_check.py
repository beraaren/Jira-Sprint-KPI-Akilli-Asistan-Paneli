"""`.env`'deki ayarlarla Jira'dan TAM veriyi cekip, panelin kullandigi isleme
hattindan (`process_sprint_report`) gecirir ve KPI'lari basar.

`jira_check.py` baglantinin ve alan eslestirmesinin dogrulugunu test eder; bu
script ise bir adim otesini - cekilen verinin gercekten islenebildigini ve
anlamli KPI urettigini - dogrular. Panelde bir sey gormeden once burada
gormek, sorunun Jira'da mi yoksa isleme katmaninda mi oldugunu ayirir.

Calistirmak icin proje kokunden:
    .venv\\Scripts\\python.exe scripts/jira_e2e_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from config import load_jira_config  # noqa: E402
from processor import calculate_sprint_kpis, fetch_issues_from_jira_api, standardize_dataframe  # noqa: E402


def main() -> int:
    cfg = load_jira_config()
    if not cfg.can_fetch_directly:
        print("[HATA] .env eksik - JIRA_PAT / JIRA_PROJECT_KEY / JIRA_FIELD_STORY_POINTS dolu olmali.")
        return 1

    print(f"'{cfg.project_key}' projesinden son {cfg.months_back} ay cekiliyor...")
    raw = fetch_issues_from_jira_api(
        cfg.base_url,
        cfg.token,
        cfg.project_key,
        cfg.field_id_map,
        months_back=cfg.months_back,
        verify=not cfg.skip_ssl,
    )
    print(f"[ OK ] {len(raw)} kart cekildi. Ham kolonlar: {list(raw.columns)}\n")
    if raw.empty:
        print("[HATA] Hic kart donmedi - JIRA_MONTHS_BACK degerini artirmayi deneyin.")
        return 1

    df = standardize_dataframe(raw)
    print(f"[ OK ] Standartlastirildi. Kolonlar: {list(df.columns)}\n")

    # `standardize_dataframe` Story Points kolonunu `estimate` adiyla verir.
    print("Ilk 5 kart:")
    preview_cols = [
        c for c in ("summary", "assignee", "estimate", "developers", "status") if c in df.columns
    ]
    print(df[preview_cols].head(5).to_string(index=False))

    print("\nDoluluk:")
    for col in ("estimate", "assignee", "developers", "analysts", "status", "created"):
        if col not in df.columns:
            print(f"  {col:<14} (kolon yok)")
            continue
        series = df[col]
        filled = series.notna() & (series.astype(str).str.strip() != "")
        print(f"  {col:<14} {filled.sum()}/{len(df)}")

    kpis = calculate_sprint_kpis(df)
    print("\nKPI'lar:")
    # `calculate_sprint_kpis` bir dataclass (SprintKPIs) doner, dict degil.
    for field_name, value in vars(kpis).items():
        print(f"  {field_name:<28} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
