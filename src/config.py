"""Jira baglanti ayarlarini proje kokundeki `.env` dosyasindan okur.

Amac: kullanicinin her acilista Jira URL / proje anahtari / alan eslestirmesi
gibi HER SEFERINDE AYNI olan bilgileri elle girmemesi. Bunlar bir kez `.env`'e
yazilir, panel acilisinda veri otomatik cekilir.

`.env` dosyasi `.gitignore`'dadir - PAT iceren bir dosya asla versiyon
kontrolune girmez. Sablon icin bkz. proje kokundeki `.env.example`.

`.env` her okumada yenilenir ve ayni adli ortam degiskenine gore onceliklidir.
Dosyada bulunmayan anahtarlar ortamdan okunur.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

DEFAULT_BASE_URL = "https://jira.turkcell.com.tr"
DEFAULT_MONTHS_BACK = 6

# Jira alanlarinin `.env` anahtarlari.
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

EDITABLE_JIRA_ENV_KEYS = frozenset({
    "JIRA_BASE_URL", "JIRA_PAT", "JIRA_PROJECT_KEY", "JIRA_MONTHS_BACK",
    "JIRA_SKIP_SSL", "JIRA_FIELD_STORY_POINTS", "JIRA_FIELD_DEVELOPER",
    "JIRA_FIELD_ANALYST", "JIRA_FIELD_SPRINT", "JIRA_FIELD_LAST_TRANSITION",
    "SPRINT_DISI_FALLBACK_ENABLED",
})


def save_jira_settings(updates: dict[str, str]) -> None:
    """Save only known Jira settings to .env, preserving other lines and comments."""
    if not updates or set(updates) - EDITABLE_JIRA_ENV_KEYS:
        raise ValueError("Geçersiz Jira ayarı.")
    values = {key: str(value).strip() for key, value in updates.items()}
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Ayar değerleri tek satır olmalıdır.")
    if "JIRA_BASE_URL" in values:
        url = urlsplit(values["JIRA_BASE_URL"])
        if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password:
            raise ValueError("Geçerli bir Jira URL girin.")
    for key in ("JIRA_PROJECT_KEY", "JIRA_FIELD_STORY_POINTS"):
        if key in values and not values[key]:
            raise ValueError(f"{key} boş bırakılamaz.")
    if "JIRA_MONTHS_BACK" in values:
        try:
            months = int(values["JIRA_MONTHS_BACK"])
        except ValueError as exc:
            raise ValueError("Ay kapsamı 1 ile 36 arasında olmalıdır.") from exc
        if not 1 <= months <= 36:
            raise ValueError("Ay kapsamı 1 ile 36 arasında olmalıdır.")

    ENV_PATH.touch(exist_ok=True)
    for key, value in values.items():
        success, _, _ = set_key(str(ENV_PATH), key, value, quote_mode="auto")
        if not success:
            raise OSError(".env dosyasına yazılamadı.")


def _get(name: str, default: str = "") -> str:
    file_values = dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}
    if name in file_values:
        return (file_values[name] or "").strip()
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
    sprint_disi_fallback_enabled: bool

    @property
    def has_credentials(self) -> bool:
        """Kesif adimi icin yeterli bilgi var mi (URL + token + proje)."""
        return bool(self.base_url and self.token and self.project_key)

    @property
    def has_field_map(self) -> bool:
        """Zorunlu alan eslestirmesi `.env`'de var mi? Yalniz `story_points`
        zorunludur; developer/analyst opsiyonel
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
