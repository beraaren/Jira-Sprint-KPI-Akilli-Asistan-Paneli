"""Jira baglanti ayarlarini proje kokundeki `.env` dosyasindan (ve ortam
degiskenlerinden) okur.

Amac: kullanicinin her acilista Jira URL / proje anahtari / alan eslestirmesi
gibi HER SEFERINDE AYNI olan bilgileri elle girmemesi. Bunlar bir kez `.env`'e
yazilir, panel acilisinda otomatik dolar; alan ID'leri de verilmisse "Baglan ve
Kesfet" adimi tamamen atlanip tek tikla veri cekilir.

`.env` dosyasi `.gitignore`'dadir - PAT iceren bir dosya asla versiyon
kontrolune girmez. Sablon icin bkz. proje kokundeki `.env.example`.

Oncelik sirasi: gercek ortam degiskeni > `.env` dosyasi > varsayilan. Boylece
tek seferlik bir deneme icin `.env`'i degistirmeden ortam degiskeniyle gecici
override yapilabilir.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

# `override=False`: gercek ortam degiskeni varsa `.env` onu EZMEZ (yukaridaki
# oncelik sirasi). Dosya yoksa sessizce hicbir sey yapmaz - `.env` opsiyoneldir,
# panel onsuz da (tum alanlar elle girilerek) calisir.
load_dotenv(ENV_PATH, override=False)

DEFAULT_BASE_URL = "https://jira.turkcell.com.tr"
DEFAULT_MONTHS_BACK = 6

# Panelin alan eslestirme selectbox'lariyla AYNI anahtarlar (bkz.
# processor.JIRA_FIELD_MAP_KEYS) - `.env`'deki karsiliklari.
_FIELD_ENV_KEYS = {
    "story_points": "JIRA_FIELD_STORY_POINTS",
    "developer": "JIRA_FIELD_DEVELOPER",
    "analyst": "JIRA_FIELD_ANALYST",
    # Sprint alani panelde SORULMAZ - adi her kurulumda standart oldugu icin
    # otomatik bulunur (bkz. processor.find_sprint_field_id). Bu anahtar sadece
    # otomatik bulmanin ise yaramadigi kurulumlar icin bir elle-override yoludur.
    "sprint": "JIRA_FIELD_SPRINT",
    # Last Transition da Sprint gibi otomatik bulunur; bu anahtar sadece elle
    # override icindir (bkz. processor.find_last_transition_field_id).
    "last_transition": "JIRA_FIELD_LAST_TRANSITION",
}


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _get_bool(name: str, default: bool = False) -> bool:
    raw = _get(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "evet")


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class JiraConfig:
    """`.env`'den okunan Jira baglanti ayarlari. `token` disindaki her sey
    kuruma ozgu ama GIZLI DEGILDIR; token ise sadece bellekte tasinir ve
    `__repr__`'da maskelenir (log/hata ciktisina sizmamasi icin)."""

    base_url: str
    token: str
    project_key: str
    months_back: int
    skip_ssl: bool
    field_id_map: dict[str, str | None]
    auto_connect: bool
    sprint_disi_fallback_enabled: bool

    @property
    def has_credentials(self) -> bool:
        """Kesif adimi icin yeterli bilgi var mi (URL + token + proje)."""
        return bool(self.base_url and self.token and self.project_key)

    @property
    def has_field_map(self) -> bool:
        """Kesif adimi ATLANABILIR mi - yani alan eslestirmesi `.env`'de zaten
        verilmis mi? Sadece `story_points` zorunlu; developer/analyst opsiyonel
        (bkz. fetch_issues_from_jira_api - haritalanmamis alanlar bos kalir)."""
        return bool(self.field_id_map.get("story_points"))

    @property
    def can_fetch_directly(self) -> bool:
        """Hicbir sey sorulmadan dogrudan tam veri cekilebilir mi?"""
        return self.has_credentials and self.has_field_map

    def __repr__(self) -> str:  # pragma: no cover - sadece hata ayiklama kolayligi
        masked = "***" if self.token else "(yok)"
        return (
            f"JiraConfig(base_url={self.base_url!r}, token={masked}, "
            f"project_key={self.project_key!r}, months_back={self.months_back}, "
            f"skip_ssl={self.skip_ssl}, field_id_map={self.field_id_map!r}, "
            f"auto_connect={self.auto_connect}, "
            f"sprint_disi_fallback_enabled={self.sprint_disi_fallback_enabled})"
        )


def load_jira_config() -> JiraConfig:
    """`.env` / ortam degiskenlerinden `JiraConfig` uretir. Hicbir sey
    tanimlanmamissa da GECERLI bir nesne doner (bos token/proje ile) - panel bu
    durumda eski davranisina, yani her seyi elle sorma akisina duser."""
    return JiraConfig(
        base_url=_get("JIRA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        token=_get("JIRA_PAT"),
        project_key=_get("JIRA_PROJECT_KEY"),
        months_back=_get_int("JIRA_MONTHS_BACK", DEFAULT_MONTHS_BACK),
        skip_ssl=_get_bool("JIRA_SKIP_SSL", False),
        field_id_map={key: (_get(env_key) or None) for key, env_key in _FIELD_ENV_KEYS.items()},
        auto_connect=_get_bool("JIRA_AUTO_CONNECT", False),
        sprint_disi_fallback_enabled=_get_bool("SPRINT_DISI_FALLBACK_ENABLED", False),
    )


def sprint_disi_fallback_enabled() -> bool:
    """Created-tarihi tabanli plan-disi yedegi o anda etkin mi?

    Degeri her cagrida okumak testlerin ve uzun sure acik kalan panel surecinin
    ortam degiskeni degisikligini yeniden baslatmadan gorebilmesini saglar.
    """
    return _get_bool("SPRINT_DISI_FALLBACK_ENABLED", False)


def env_snippet(base_url: str, project_key: str, field_id_map: dict[str, str | None]) -> str:
    """Basarili bir kesiften sonra kullaniciya gosterilecek, oldugu gibi `.env`'e
    yapistirilabilir metni uretir. Alan ID'leri (`customfield_XXXXX`) her Jira
    kurulumunda FARKLIDIR ve elle bulunmasi zahmetlidir - kesif onlari zaten
    buldugu icin, sonucu kalici hale getirmenin en kolay yolu budur.

    PAT kasitli olarak BOS birakilir - token'i ekrana basip kullanicidan
    kopyalatmak, onu tarayici geçmisine/ekran goruntusune tasima riski yaratir.
    """
    lines = [
        f"JIRA_BASE_URL={base_url}",
        "JIRA_PAT=<kendi token'ınız>",
        f"JIRA_PROJECT_KEY={project_key}",
        "SPRINT_DISI_FALLBACK_ENABLED=false",
    ]
    for key, env_key in _FIELD_ENV_KEYS.items():
        lines.append(f"{env_key}={field_id_map.get(key) or ''}")
    return "\n".join(lines)
