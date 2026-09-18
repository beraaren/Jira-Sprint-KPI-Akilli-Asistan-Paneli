"""Sprint ICI haftalik performans ozeti - KURAL TABANLI (LLM kullanmaz).

Panel bugune kadar yalnizca ay/sprint KAPANISINI raporluyordu. Sprint bir ay
surdugu icin "sprint devam ederken nasil gidiyoruz?" sorusunun cevabi yoktu. Bu
modul o boslugu doldurur: secilen ISO haftasi icin sprint ici gidisati olcup
degerlendirir.

IKI KATMAN - bu ayrim KASITLIDIR:
    A) `analyze_week`  -> BULGU (`WeeklyFinding`) uretir. Sadece sayi/durum, metin
       YOK. Deterministik ve birim testi yazilabilir.
    B) `render_weekly_text` / `render_weekly_html` -> bulgulari cumleye cevirir.
       Hesap YAPMAZ.

Boylece ileride metni bir LLM yazacak olursa sadece B katmani degisir; A aynen
kalir ve LLM erisilemedigi anda yedek (fallback) olarak calisir.

VERI TEMELI VE SINIRI (dogru okuma icin kritik):
    Gercek Jira verisinde `resolved` kolonu TAMAMEN BOSTUR; "bu is ne zaman
    hareket etti/bitti" sorusunun tek kaynagi `last_transition` (Jira'nin
    "Last Transition" ozel alani) olur. Bu alan kartin YALNIZCA EN SON statu
    degisimini tutar, tam gecmisini degil. Sonuc:

      - `Done`/`Cancelled` TERMINAL statulerdir; bir kart oraya girdikten sonra
        tekrar hareket etmez. Bu yuzden TAMAMLANAN is icin haftalik seri GECMISE
        DONUK DE GUVENILIRDIR - trend/kiyaslama sadece bu seriden kurulur.
      - Terminal olmayan statulerde (To Do, XL BLOCK, Development...) kartin daha
        sonra tekrar hareket etmesi o kayidi ONCEKI haftadan SILER. Bu yuzden
        "hafta icinde hareket eden kart" sayisi YALNIZCA guncel/son hafta icin
        dogrudur; gecmis haftalarla KIYASLANMAZ.

    Bu sinir rapor ciktisinda acikca yazilir (bkz. `VERI_NOTU`), dipnotta
    gizlenmez.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

import pandas as pd

from share_report import (
    ShareBullet,
    ShareDocument,
    ShareSection,
    render_share_html,
    render_share_text,
)
from processor import (
    BURNOUT_LOAD_MULTIPLIER,
    MONTH_LABELS_TR,
    _is_done,
    _is_sprint_disi,
    _month_label,
    filter_by_month,
    latest_month_label,
)

__all__ = [
    "WeeklyFinding",
    "WeeklyReportError",
    "analyze_week",
    "available_weeks",
    "build_share_document",
    "finding_sentence",
    "render_weekly_text",
    "render_weekly_html",
    "week_bounds",
    "week_label",
    "week_sprint_month",
]


# --------------------------------------------------------------------------
# Esikler - BUNLAR IS KARARIDIR
# --------------------------------------------------------------------------
# Asagidaki degerler "neyin iyi/kotu sayilacagini" belirler ve teknik degil IS
# kararlaridir. Urun sahibiyle (PO) teyit edilmeden kesin dogru kabul edilmemeli;
# tek bir yerde toplanmalarinin sebebi de tartisilip degistirilmelerinin kolay
# olmasidir.

# Tempo, son haftalarin ortalamasindan bu oranda saparsa "dikkat"/"iyi" sayilir.
TEMPO_SAPMA_ESIGI_YUZDE = 25.0
# Tempo kiyasinin dayandigi gecmis hafta sayisi.
TEMPO_REFERANS_HAFTA = 4
# Sprintteki acik bir kart bu kadar gundur hareket etmiyorsa "duran is" sayilir.
DURGUNLUK_ESIGI_GUN = 14
# Duran is sayisi bunu asarsa bulgu "kritik"e yukselir.
DURGUN_IS_KRITIK_ESIGI = 5
# Plan disi is, haftanin tamamlanan SP'sinin bu yuzdesini asarsa "dikkat".
PLAN_DISI_ESIGI_YUZDE = 30.0
# Sprint sonuna yetismek icin gereken hiz, mevcut hizin bu katini asarsa "kritik".
YETISME_RISK_KATSAYISI = 1.25

# Terminal (bir daha hareket etmeyen) statuler - kucuk harfe indirgenmis.
TERMINAL_STATUSES = frozenset({"done", "cancelled", "canceled", "iptal"})
# "Bloke" sayilan statuler.
BLOKE_STATUSES = frozenset({"xl block", "block", "blocked", "bloke"})

GUN_ADLARI = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")

VERI_NOTU = (
    "Veri notu: Jira'da 'Resolved' alanı boş olduğundan haftalık hesaplar 'Last "
    "Transition' (son statü değişimi) alanına dayanır. Bu alan kartın yalnızca en "
    "son hareketini tutar; bu yüzden tamamlanan iş serisi geçmişe dönük güvenilir, "
    "'hareket eden kart' sayısı ise yalnızca seçili hafta için kesindir."
)

# Bulgular ciktida bu sirayla yazilir - once eyleme cagiranlar.
# (Isaret/renk karsiliklari `share_report`'ta, tum sayfalar icin ORTAK tanimli.)
SEVERITY_SIRASI = {"kritik": 0, "dikkat": 1, "iyi": 2, "notr": 3}


class WeeklyReportError(RuntimeError):
    """Haftalik ozet uretilemedi - genellikle `last_transition` alani veride hic
    yoksa (eski bir disa aktarim) ya da tamamen bossa firlatilir. Sessizce bos/
    yaniltici bir rapor uretmek yerine durum acikca bildirilir."""


@dataclass
class WeeklyFinding:
    """Tek bir sinyalin OLCUM sonucu. `veri` icinde sayilar bulunur; cumle
    KURULMAZ - metni `render_weekly_*` fonksiyonlari uretir."""

    signal: str
    severity: str  # "iyi" | "notr" | "dikkat" | "kritik"
    baslik: str
    veri: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Hafta yardimcilari (ISO: Pazartesi - Pazar)
# --------------------------------------------------------------------------


def week_bounds(any_day: date) -> tuple[pd.Timestamp, pd.Timestamp]:
    """`any_day`'in icinde bulundugu ISO haftasinin (Pazartesi 00:00 - Pazar
    23:59:59) sinirlarini doner."""
    start = pd.Timestamp(any_day).normalize() - pd.Timedelta(pd.Timestamp(any_day).weekday(), "D")
    return start, start + pd.Timedelta(7, "D") - pd.Timedelta(1, "s")


def week_sprint_month(week_start: pd.Timestamp) -> pd.Period:
    """Bir haftanin HANGI ayin sprintine ait sayilacagini belirler.

    Ay sinirini asan haftalarda (orn. 27 Temmuz - 2 Agustos) haftanin bitis
    gunune bakmak YANLIS sonuc verir: o haftanin 7 gununun 5'i Temmuz'dadir ve o
    hafta tamamlanan isler de Temmuz sprintine aittir, ama bitis gunu (2 Agustos)
    haftayi Agustos'a yazardi.

    Bu yuzden ISO 8601'in hafta-numaralandirmada kullandigi PERSEMBE kurali
    uygulanir: bir hafta, PERSEMBESININ dustugu aya aittir. Persembe her zaman
    haftanin cogunlugunun bulundugu aydadir, dolayisiyla bu kural "gunlerin
    cogunlugu" ile aynidir ve ek bir sayim gerektirmez."""
    return (week_start + pd.Timedelta(3, "D")).to_period("M")


def week_label(week_start: pd.Timestamp) -> str:
    """'04-10 Ağustos 2026 (H32)' bicimli okunakli hafta etiketi."""
    end = week_start + pd.Timedelta(6, "D")
    iso_week = week_start.isocalendar()[1]
    if week_start.month == end.month:
        gun = f"{week_start.day:02d}-{end.day:02d} {MONTH_LABELS_TR[week_start.month]}"
    else:
        gun = (
            f"{week_start.day:02d} {MONTH_LABELS_TR[week_start.month]} - "
            f"{end.day:02d} {MONTH_LABELS_TR[end.month]}"
        )
    return f"{gun} {end.year} (H{iso_week:02d})"


def _require_last_transition(df: pd.DataFrame) -> pd.Series:
    if "last_transition" not in df.columns:
        raise WeeklyReportError(
            "Haftalık özet için Jira'nın 'Last Transition' alanı gerekiyor, veride bu "
            "kolon yok. Jira'dan canlı veri çekerseniz alan otomatik bulunur; dosya "
            "yüklüyorsanız dışa aktarıma 'Last Transition' kolonunu ekleyin."
        )
    series = df["last_transition"]
    if series.notna().sum() == 0:
        raise WeeklyReportError(
            "Veride 'Last Transition' kolonu var ama tamamen boş; haftalık hareket "
            "hesaplanamıyor. Jira dışa aktarımında bu alanın dolu geldiğinden emin olun."
        )
    return series


def available_weeks(df: pd.DataFrame, last_n: int = 12) -> list[pd.Timestamp]:
    """Veride hareket bulunan ISO haftalarinin baslangic tarihlerini, en YENI
    once olacak sekilde doner (en fazla `last_n` adet)."""
    series = _require_last_transition(df).dropna()
    starts = (series.dt.normalize() - pd.to_timedelta(series.dt.weekday, unit="D")).unique()
    return sorted(pd.to_datetime(starts), reverse=True)[:last_n]


# --------------------------------------------------------------------------
# Katman A - bulgu uretimi
# --------------------------------------------------------------------------


def _is_terminal(status: pd.Series) -> pd.Series:
    return status.astype(str).str.strip().str.casefold().isin(TERMINAL_STATUSES)


def _is_bloke(status: pd.Series) -> pd.Series:
    return status.astype(str).str.strip().str.casefold().isin(BLOKE_STATUSES)


def _completed_sp_in_week(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Verilen hafta icinde `Done`'a gecen islerin SP toplami. Yalnizca terminal
    statuler sayildigi icin bu deger GECMISE DONUK de guvenilirdir."""
    mask = (
        _is_done(df["status"])
        & df["last_transition"].between(start, end)
    )
    return float(df.loc[mask, "estimate"].sum())


def _kisi_listesi(row: pd.Series) -> list[str]:
    """Bir kartin emegi gecen kisileri: assignee + developers + analysts (tekrarsiz)."""
    kisiler: list[str] = []
    assignee = str(row.get("assignee", "") or "").strip()
    if assignee:
        kisiler.append(assignee)
    for column in ("developers", "analysts"):
        values = row.get(column)
        if isinstance(values, list):
            kisiler.extend(str(v).strip() for v in values if str(v).strip())
    return list(dict.fromkeys(kisiler))


def analyze_week(
    df: pd.DataFrame,
    week_start: pd.Timestamp | date | None = None,
    target_month: str | None = None,
) -> dict:
    """Secilen ISO haftasi icin sprint ici performans bulgularini uretir.

    `week_start` verilmezse veride hareket bulunan EN SON hafta kullanilir.
    `target_month` verilmezse haftanin bittigi ayin sprinti secilir (bkz.
    `filter_by_month` - once SPRINT alani, yoksa `created`).

    Donen sozluk:
        `hafta_basi`, `hafta_sonu`, `hafta_etiketi`, `sprint`,
        `bulgular` (severity'ye gore sirali `WeeklyFinding` listesi),
        `metrikler` (ham sayilar - grafik/tablo icin),
        `veri_notu`.

    `last_transition` alani yoksa/bossa `WeeklyReportError` firlatir - yaniltici
    bir "0 iş" raporu uretmez.
    """
    _require_last_transition(df)

    if week_start is None:
        weeks = available_weeks(df, last_n=1)
        if not weeks:
            raise WeeklyReportError("Veride hareket bulunan hiçbir hafta yok.")
        week_start = weeks[0]
    start, end = week_bounds(pd.Timestamp(week_start).date())

    # Sprint kapsami: hafta hangi ayin sprintine ait sayilacak (persembe kurali -
    # bkz. `week_sprint_month`; bitis gunune bakmak ay sinirini asan haftalarda
    # haftayi yanlis sprinte yazardi).
    sprint_period = week_sprint_month(start)
    sprint_label = target_month or _month_label(sprint_period)
    sprint_df = filter_by_month(df, sprint_label)
    if sprint_df.empty:
        # Hafta, veride sprinti olmayan bir aya dusuyorsa en guncel sprinte duselim.
        sprint_label = latest_month_label(df) or sprint_label
        sprint_df = filter_by_month(df, sprint_label)

    bulgular: list[WeeklyFinding] = []
    metrikler: dict = {}

    # --- 1) Bu hafta tamamlanan is -----------------------------------------
    hafta_mask = df["last_transition"].between(start, end)
    done_mask = hafta_mask & _is_done(df["status"])
    tamamlanan = df.loc[done_mask]
    tamamlanan_sp = float(tamamlanan["estimate"].sum())
    metrikler["tamamlanan_kart"] = int(len(tamamlanan))
    metrikler["tamamlanan_sp"] = tamamlanan_sp
    bulgular.append(
        WeeklyFinding(
            signal="tamamlanan",
            # Is bitmis olmasi tek basina "iyi" degildir - az mi cok mu oldugunu
            # `tempo` bulgusu soyler. Burada yesil isaret verilseydi, tempo %70
            # dusmus bir haftada bile rapor yesille aciliyor olurdu.
            severity="notr" if tamamlanan_sp > 0 else "dikkat",
            baslik="Bu hafta tamamlanan iş",
            veri={"kart": int(len(tamamlanan)), "sp": tamamlanan_sp},
        )
    )

    # --- 2) Tempo: son haftalarin ortalamasiyla kiyas ------------------------
    gecmis = [
        _completed_sp_in_week(df, *week_bounds((start - pd.Timedelta(7 * i, "D")).date()))
        for i in range(1, TEMPO_REFERANS_HAFTA + 1)
    ]
    ortalama = sum(gecmis) / len(gecmis) if gecmis else 0.0
    metrikler["tempo_referans_ortalama_sp"] = ortalama
    metrikler["gecmis_haftalar_sp"] = gecmis
    if ortalama > 0:
        sapma = (tamamlanan_sp - ortalama) / ortalama * 100
        if sapma <= -TEMPO_SAPMA_ESIGI_YUZDE:
            tempo_severity = "dikkat"
        elif sapma >= TEMPO_SAPMA_ESIGI_YUZDE:
            tempo_severity = "iyi"
        else:
            tempo_severity = "notr"
        bulgular.append(
            WeeklyFinding(
                signal="tempo",
                severity=tempo_severity,
                baslik="Tempo",
                veri={
                    "bu_hafta_sp": tamamlanan_sp,
                    "ortalama_sp": ortalama,
                    "sapma_yuzde": sapma,
                    "referans_hafta": TEMPO_REFERANS_HAFTA,
                },
            )
        )

    # --- 3) Sprint ilerlemesi ------------------------------------------------
    planli = sprint_df.loc[~_is_sprint_disi(sprint_df["labels"])]
    taahhut_sp = float(planli["estimate"].sum())
    sprint_tamamlanan_sp = float(planli.loc[_is_done(planli["status"]), "estimate"].sum())
    ilerleme = (sprint_tamamlanan_sp / taahhut_sp * 100) if taahhut_sp else 0.0
    metrikler["sprint"] = sprint_label
    metrikler["sprint_taahhut_sp"] = taahhut_sp
    metrikler["sprint_tamamlanan_sp"] = sprint_tamamlanan_sp
    metrikler["sprint_ilerleme_yuzde"] = ilerleme

    # Sprint takvimi de haftanin ATANDIGI aya gore hesaplanir (bitis gunune gore
    # degil) - aksi halde ay sinirini asan bir hafta "yeni sprintin 1. haftasi"
    # gorunurdu.
    ay_basi = sprint_period.start_time
    ay_sonu = sprint_period.end_time
    gecen_hafta_sayisi = max(1, ((start - ay_basi).days // 7) + 1)
    kalan_hafta = max(0, (ay_sonu - end).days // 7)
    metrikler["sprint_hafta_no"] = gecen_hafta_sayisi
    metrikler["sprint_kalan_hafta"] = kalan_hafta
    bulgular.append(
        WeeklyFinding(
            signal="sprint_ilerleme",
            severity="notr",
            baslik="Sprint ilerlemesi",
            veri={
                "sprint": sprint_label,
                "hafta_no": gecen_hafta_sayisi,
                "kalan_hafta": kalan_hafta,
                "taahhut_sp": taahhut_sp,
                "tamamlanan_sp": sprint_tamamlanan_sp,
                "ilerleme_yuzde": ilerleme,
            },
        )
    )

    # --- 4) Sprint sonuna yetisme tahmini ------------------------------------
    kalan_sp = max(0.0, taahhut_sp - sprint_tamamlanan_sp)
    if kalan_hafta > 0 and kalan_sp > 0:
        gereken_hiz = kalan_sp / kalan_hafta
        mevcut_hiz = ortalama if ortalama > 0 else tamamlanan_sp
        metrikler["gereken_haftalik_hiz_sp"] = gereken_hiz
        metrikler["mevcut_haftalik_hiz_sp"] = mevcut_hiz
        if mevcut_hiz <= 0:
            yetisme_severity = "kritik"
        elif gereken_hiz > mevcut_hiz * YETISME_RISK_KATSAYISI:
            yetisme_severity = "kritik"
        elif gereken_hiz > mevcut_hiz:
            yetisme_severity = "dikkat"
        else:
            yetisme_severity = "iyi"
        bulgular.append(
            WeeklyFinding(
                signal="yetisme_tahmini",
                severity=yetisme_severity,
                baslik="Sprint sonu tahmini",
                veri={
                    "kalan_sp": kalan_sp,
                    "kalan_hafta": kalan_hafta,
                    "gereken_hiz_sp": gereken_hiz,
                    "mevcut_hiz_sp": mevcut_hiz,
                },
            )
        )

    # --- 5) Hareket (YALNIZCA bu hafta icin gecerli) -------------------------
    hareket = df.loc[hafta_mask]
    metrikler["hareket_eden_kart"] = int(len(hareket))
    bulgular.append(
        WeeklyFinding(
            signal="hareket",
            severity="notr",
            baslik="Hafta içi hareket",
            veri={
                "kart": int(len(hareket)),
                "sp": float(hareket["estimate"].sum()),
                "tamamlanan_kart": int(len(tamamlanan)),
            },
        )
    )

    # --- 6) Duran isler ------------------------------------------------------
    acik = sprint_df.loc[~_is_terminal(sprint_df["status"])].copy()
    if not acik.empty and acik["last_transition"].notna().any():
        acik["_durgunluk_gun"] = (end - acik["last_transition"]).dt.days
        duran = acik.loc[acik["_durgunluk_gun"] >= DURGUNLUK_ESIGI_GUN]
        duran = duran.sort_values("_durgunluk_gun", ascending=False)
        metrikler["duran_is_sayisi"] = int(len(duran))
        metrikler["duran_is_sp"] = float(duran["estimate"].sum())
        if len(duran) > 0:
            bulgular.append(
                WeeklyFinding(
                    signal="duran_isler",
                    severity="kritik" if len(duran) > DURGUN_IS_KRITIK_ESIGI else "dikkat",
                    baslik="Duran işler",
                    veri={
                        "kart": int(len(duran)),
                        "sp": float(duran["estimate"].sum()),
                        "esik_gun": DURGUNLUK_ESIGI_GUN,
                        "en_uzun_gun": int(duran["_durgunluk_gun"].max()),
                        "ornekler": [
                            {
                                "is": str(r["summary"])[:70],
                                "gun": int(r["_durgunluk_gun"]),
                                "statu": str(r["status"]),
                            }
                            for _, r in duran.head(5).iterrows()
                        ],
                    },
                )
            )

    # --- 7) Bloke isler ------------------------------------------------------
    bloke = sprint_df.loc[_is_bloke(sprint_df["status"])]
    metrikler["bloke_kart"] = int(len(bloke))
    metrikler["bloke_sp"] = float(bloke["estimate"].sum())
    if len(bloke) > 0:
        bu_hafta_bloke = int((_is_bloke(df["status"]) & hafta_mask).sum())
        bulgular.append(
            WeeklyFinding(
                signal="bloke",
                severity="kritik" if len(bloke) > DURGUN_IS_KRITIK_ESIGI else "dikkat",
                baslik="Bloke işler",
                veri={
                    "kart": int(len(bloke)),
                    "sp": float(bloke["estimate"].sum()),
                    "bu_hafta_bloke_olan": bu_hafta_bloke,
                },
            )
        )

    # --- 8) Plan disi is -----------------------------------------------------
    plan_disi_sp = float(tamamlanan.loc[_is_sprint_disi(tamamlanan["labels"]), "estimate"].sum())
    metrikler["plan_disi_tamamlanan_sp"] = plan_disi_sp
    # Plan disi is YOKSA bulgu uretilmez - "0 SP (%0) plan disi geldi" satiri
    # ozeti uzatir ama hicbir sey soylemez.
    if tamamlanan_sp > 0 and plan_disi_sp > 0:
        oran = plan_disi_sp / tamamlanan_sp * 100
        bulgular.append(
            WeeklyFinding(
                signal="plan_disi",
                severity="dikkat" if oran > PLAN_DISI_ESIGI_YUZDE else "notr",
                baslik="Plan dışı iş",
                veri={"sp": plan_disi_sp, "oran_yuzde": oran, "toplam_sp": tamamlanan_sp},
            )
        )

    # --- 9) Kisi katkisi -----------------------------------------------------
    katki: dict[str, float] = {}
    for _, row in tamamlanan.iterrows():
        for kisi in _kisi_listesi(row):
            katki[kisi] = katki.get(kisi, 0.0) + float(row["estimate"])
    metrikler["kisi_katkisi"] = dict(sorted(katki.items(), key=lambda kv: kv[1], reverse=True))
    if katki:
        en_iyi = max(katki.items(), key=lambda kv: kv[1])
        bulgular.append(
            WeeklyFinding(
                signal="kisi_katki",
                severity="iyi",
                baslik="Haftanın katkısı",
                veri={"kisi": en_iyi[0], "sp": en_iyi[1], "katilan_kisi_sayisi": len(katki)},
            )
        )

    # --- 10) Kisi yuku (acik isler) ------------------------------------------
    acik_yuk: dict[str, float] = {}
    for _, row in sprint_df.loc[~_is_terminal(sprint_df["status"])].iterrows():
        for kisi in _kisi_listesi(row):
            acik_yuk[kisi] = acik_yuk.get(kisi, 0.0) + float(row["estimate"])
    metrikler["acik_yuk"] = dict(sorted(acik_yuk.items(), key=lambda kv: kv[1], reverse=True))
    if len(acik_yuk) >= 2:
        ekip_ort = sum(acik_yuk.values()) / len(acik_yuk)
        riskli = [
            {"kisi": k, "sp": v}
            for k, v in acik_yuk.items()
            if v > ekip_ort * BURNOUT_LOAD_MULTIPLIER
        ]
        if riskli:
            bulgular.append(
                WeeklyFinding(
                    signal="kisi_yuku",
                    severity="dikkat",
                    baslik="İş yükü dengesi",
                    veri={
                        "riskli": sorted(riskli, key=lambda r: r["sp"], reverse=True),
                        "ekip_ortalama_sp": ekip_ort,
                        "katsayi": BURNOUT_LOAD_MULTIPLIER,
                    },
                )
            )

    bulgular.sort(key=lambda b: SEVERITY_SIRASI.get(b.severity, 9))

    return {
        "hafta_basi": start,
        "hafta_sonu": end,
        "hafta_etiketi": week_label(start),
        "sprint": sprint_label,
        "bulgular": bulgular,
        "metrikler": metrikler,
        "veri_notu": VERI_NOTU,
    }


# --------------------------------------------------------------------------
# Katman B - bulgulardan metin
# --------------------------------------------------------------------------
# Buradaki fonksiyonlar HESAP YAPMAZ; yalnizca `analyze_week`'in urettigi
# bulgulari cumleye cevirir. Ileride metni bir LLM yazacak olursa degisecek tek
# katman burasidir.


def _n(value: float) -> str:
    """Sayiyi kisa ve okunakli yazar: 13.0 -> '13', 13.5 -> '13,5'."""
    if float(value).is_integer():
        return f"{int(value)}"
    return f"{value:.1f}".replace(".", ",")


def finding_sentence(finding: WeeklyFinding) -> str:
    """Tek bir bulgunun Turkce cumlesi. Bilinmeyen bir sinyal gelirse (ileride
    yeni bir kural eklenip burasi guncellenmezse) sessizce atlanmasin diye
    basligi ve ham verisi ile birlikte donulur."""
    d = finding.veri
    s = finding.signal

    if s == "tamamlanan":
        if d["kart"] == 0:
            return "Bu hafta hiçbir iş tamamlanmadı."
        return f"Bu hafta {d['kart']} iş tamamlandı, toplam {_n(d['sp'])} SP."

    if s == "tempo":
        sapma = d["sapma_yuzde"]
        yon = "üzerinde" if sapma >= 0 else "altında"
        if abs(sapma) < TEMPO_SAPMA_ESIGI_YUZDE:
            return (
                f"Tempo son {d['referans_hafta']} haftanın ortalamasıyla (~{_n(d['ortalama_sp'])} SP) "
                "benzer seyrediyor."
            )
        return (
            f"Tempo, son {d['referans_hafta']} haftanın ortalamasının (~{_n(d['ortalama_sp'])} SP) "
            f"%{abs(sapma):.0f} {yon}."
        )

    if s == "sprint_ilerleme":
        kalan = (
            f"{d['kalan_hafta']} hafta kaldı"
            if d["kalan_hafta"] > 0
            else "sprintin son haftasındayız"
        )
        # Not: yuzdeye Turkce ek getirmekten kacinilir ("%36'i" yanlis, "%36'si"
        # dogru ve ek sayiya gore degisir) - cumle eksiz kurulur.
        return (
            f"{d['sprint']} sprintinin {d['hafta_no']}. haftasındayız ({kalan}); "
            f"taahhüt edilen {_n(d['taahhut_sp'])} SP'de tamamlanma %{d['ilerleme_yuzde']:.0f}."
        )

    if s == "yetisme_tahmini":
        if finding.severity == "iyi":
            return (
                f"Kalan {_n(d['kalan_sp'])} SP için gereken hız (~{_n(d['gereken_hiz_sp'])} SP/hafta) "
                "mevcut hızın altında; sprint hedefi ulaşılabilir görünüyor."
            )
        return (
            f"Kalan {_n(d['kalan_sp'])} SP'yi {d['kalan_hafta']} haftada bitirmek için "
            f"~{_n(d['gereken_hiz_sp'])} SP/hafta gerekiyor; mevcut hız ~{_n(d['mevcut_hiz_sp'])} SP/hafta. "
            "Bu farkla sprint hedefinin tamamı riskte."
        )

    if s == "hareket":
        return (
            f"Hafta boyunca {d['kart']} kart hareket etti ({_n(d['sp'])} SP); "
            f"bunların {d['tamamlanan_kart']} tanesi tamamlandı."
        )

    if s == "duran_isler":
        ornek = d["ornekler"][0] if d["ornekler"] else None
        ek = f" En uzun bekleyen: \"{ornek['is']}\" ({ornek['gun']} gün, {ornek['statu']})." if ornek else ""
        return (
            f"{d['kart']} açık iş {d['esik_gun']} günden uzun süredir hiç hareket etmiyor "
            f"(toplam {_n(d['sp'])} SP).{ek}"
        )

    if s == "bloke":
        ek = f" Bu hafta {d['bu_hafta_bloke_olan']} kart bloke duruma geçti." if d["bu_hafta_bloke_olan"] else ""
        return f"Sprintte {d['kart']} bloke iş var ({_n(d['sp'])} SP).{ek}"

    if s == "plan_disi":
        return (
            f"Tamamlanan işin {_n(d['sp'])} SP'si (%{d['oran_yuzde']:.0f}) plan dışı geldi."
        )

    if s == "kisi_katki":
        return (
            f"Haftaya {d['katilan_kisi_sayisi']} kişi katkı verdi; en çok "
            f"{d['kisi']} ({_n(d['sp'])} SP)."
        )

    if s == "kisi_yuku":
        isimler = ", ".join(f"{r['kisi']} ({_n(r['sp'])} SP)" for r in d["riskli"][:3])
        return (
            f"Açık iş yükü ekip ortalamasının (~{_n(d['ekip_ortalama_sp'])} SP) "
            f"{d['katsayi']} katını aşanlar: {isimler}."
        )

    return f"{finding.baslik}: {d}"


def build_share_document(result: dict) -> ShareDocument:
    """Haftalik analiz sonucunu, ortak paylasim belgesine (`ShareDocument`)
    cevirir. Metin/HTML uretimi bu noktadan sonra `share_report`'un ISIDIR -
    boylece tum sayfalar ayni kacis/kirpma/e-posta uyumluluk kurallarini paylasir."""
    m = result["metrikler"]
    return ShareDocument(
        baslik="Haftalık Jira Özeti",
        alt_baslik=f"{result['hafta_etiketi']}  •  Sprint: {result['sprint']}",
        kutular=[
            ("Tamamlanan iş", str(m.get("tamamlanan_kart", 0))),
            ("Tamamlanan SP", _n(m.get("tamamlanan_sp", 0))),
            ("Hareket eden kart", str(m.get("hareket_eden_kart", 0))),
            ("Sprint ilerlemesi", f"%{m.get('sprint_ilerleme_yuzde', 0):.0f}"),
        ],
        bolumler=[
            ShareSection(
                baslik="Değerlendirme",
                bullets=[
                    ShareBullet(metin=finding_sentence(b), severity=b.severity)
                    for b in result["bulgular"]
                ],
            )
        ],
        dipnot=result["veri_notu"],
    )


def render_weekly_text(result: dict) -> str:
    """Haftalik ozetin duz metin hali - panelde gosterilir, e-postaya da
    yapistirilabilir. Sinyal basina bir cumle; kasitli olarak KISA tutulur."""
    return render_share_text(build_share_document(result))


def render_weekly_html(result: dict) -> str:
    """Haftalik ozetin tek parca, e-postalanabilir HTML hali."""
    return render_share_html(build_share_document(result))
