# Jira Sprint & KPI Akıllı Asistan Paneli

## ⚡ Hızlı Başlangıç

1. Bu repoyu klonlayın/indirin.
2. `Kurulum.bat` dosyasına çift tıklayın (yalnızca ilk seferde gerekir).
3. Sonraki her açılışta `Uygulamayi_Baslat.bat` dosyasına çift tıklayın - uygulama tarayıcınızda otomatik açılır.

Jira'dan dışa aktarılan bir sprint/iterasyon raporunu (HTML, CSV veya XLSX) okuyup; KPI hesaplama,
aylık trend analizi, kişi/proje/konu bazlı performans, statü dağılımı, klasik ve
ileri düzey darboğaz tespiti, tahmin sapma analizi, 5 boyutlu profesyonel KPI
paketi ve doğal dilde soru-cevap gibi geniş bir analiz yelpazesini otomatik
üreten uçtan uca bir "sprint zekâsı" sistemi. Sonuçlar; biçimlendirilmiş/grafikli
bir **Excel raporuna**, durum rozetli/koyu-açık tema uyumlu bir **Streamlit web
arayüzüne** (yerel bir dil modeline - Ollama - bağlı, `mcp_server.py`'nin 13
aracını çağıran gerçek bir sohbet asistanı dahil) ve bir LLM'in (Claude Desktop
vb.) doğal dille sorgulayabileceği **13 MCP (Model Context Protocol) aracına**
dönüştürülür.

> 🔒 **Gizlilik notu:** Akıllı Asistan sayfası **tamamen yerel** çalışır
> (Ollama, varsayılan model: `qwen2.5:3b`). Sorduğunuz sorular ve ilgili Jira/
> personel verisi (isimler, iş yükü, performans metrikleri dahil) bu
> bilgisayarın dışına ASLA çıkmaz, hiçbir buluta/üçüncü taraf API'sine
> gönderilmez - internet bağlantısı olmadan da çalışır.

## İçindekiler

- [Özellikler](#özellikler)
- [Mimari](#mimari)
- [Proje yapısı](#proje-yapısı)
- [Kurulum](#kurulum)
- [Kullanım](#kullanım)
  - [Streamlit web arayüzü](#1-streamlit-web-arayüzü)
  - [Jira'ya Canlı Bağlanma](#jiraya-canlı-bağlanma)
  - [MCP sunucusu (Claude Desktop vb.)](#2-mcp-sunucusu-claude-desktop-vb)
  - [Akıllı Asistan için Ollama kurulumu](#3-akıllı-asistan-için-ollama-kurulumu)
- [Girdi veri formatı](#girdi-veri-formatı)

## Özellikler

- **Temel Sprint KPI'ları** - Taahhüt edilen / gerçekleşen / plan dışı SP,
  tamamlanma oranı, plan dışı oranı.
- **Aylık trend karşılaştırması** - ay ay KPI kıyaslaması. Bir kartın hangi aya
  ait sayıldığı Jira'nın **Sprint** alanından belirlenir ("ilgili sprinte ait
  kartlar"); böylece devreden bir kart, açıldığı ayda değil yer aldığı her
  sprintin ayında sayılır. Sprint alanı boş olan ya da adından ay çıkarılamayan
  kartlarda (`Sprint 2`, `MS Sprint 3` gibi) `created` tarihine düşülür.
- **5 Temel KPI paketi** - Velocity & Predictability, Scope Stability, Workload
  Equity/Consistency, Flow Efficiency & Bottlenecks, Estimation Accuracy &
  Variance; takım/kişi/proje bazında.
- **Kişi ve proje/konu bazlı analizler** - İş yükü dağılımı, kişi drill-down,
  Component/Epic/Summary'den türetilen proje/konu grupları.
- **Klasik + ileri düzey darboğaz analitiği** - Blocked/hold tespiti, WIP Aging,
  Blocker & Hold maliyeti, Assignee Bouncing (proxy), Reopen Oranı (proxy), Flow
  Load vs. Capacity.
- **Tahmin doğruluk analizi** - Talep tipine göre hedeflenen/gerçekleşen SP sapması.
- **Doğal dilde serbest arama** - Çok kelimeli, AND mantıklı metin araması.
- **Biçimlendirilmiş Excel raporu** - Tek sayfada gömülü bar chart + planlanan/plan
  dışı iş listeleri.
- **Streamlit web arayüzü** - Kenar çubuğu/sayfa tabanlı gezinme, esik-bazlı durum
  rozetleri (İyi/Dikkat/Kritik), koyu/açık temaya otomatik uyum ve **tamamen
  yerel (Ollama) çalışan, gerçek araç-çağırma (tool-calling) ile çalışan bir
  sohbet asistanı** (🔒 veriler bu bilgisayarın dışına çıkmaz - bkz. yukarıdaki
  gizlilik notu).
- **13 MCP aracı** - Claude Desktop gibi bir MCP istemcisinden doğal dille
  sorgulanabilir (bkz. [MCP sunucusu](#2-mcp-sunucusu-claude-desktop-vb)).

## Mimari

```
Jira raporu (HTML / CSV / XLSX)
      │
      ▼
processor.py   (okuma / temizleme / tüm KPI ve analiz hesaplamaları - saf, yan etkisiz motor)
      │
      ├──▶ reporter.py            (tek sayfalık, grafikli Excel raporu)
      │
      ├──▶ mcp_server.py          (13 MCP aracı ile LLM'e doğal dilde açılır)
      │
      └──▶ app/new_dashboard.py   (Streamlit web arayüzü)
                 │
                 └──▶ llm_assistant.py  (Ollama tabanlı, tamamen yerel sohbet asistanı -
                                          mcp_server.py'nin 13 aracını çağırır)
```

`processor.py`, hiçbir yan etkisi olmayan (diske yazmayan, sadece okuyan) saf
fonksiyonlardan oluşan tek bir "motor" katmanıdır; Streamlit arayüzü ve MCP
sunucusu aynı bu katmanı doğrudan kullanır - iş mantığı tek bir yerde (single
source of truth) tanımlıdır.

## Proje yapısı

```
JIRA_MCP/
├── app/
│   └── new_dashboard.py         Streamlit web arayüzü (sayfa tabanlı + yerel Ollama asistanı)
├── src/
│   ├── processor.py             Veri işleme ve analiz motoru (ana katman)
│   ├── reporter.py               Excel rapor üretici
│   ├── mcp_server.py             MCP sunucusu / 13 araç tanımı
│   └── llm_assistant.py          Ollama (yerel) tabanlı, tool-calling ile çalışan sohbet motoru
├── requirements.txt
└── pyproject.toml
```

## Kurulum

Ortam Python'un kendi `venv` modülü ile yönetilir:

```bash
# 1) Sanal ortamı oluşturun ve aktifleştirin
python -m venv .venv
.venv\Scripts\activate

# 2) Bağımlılıkları kurun
pip install -r requirements.txt

# 3) Akıllı Asistan için Ollama'yı kurun ve modeli çekin
#    https://ollama.com/download adresinden Ollama'yı kurun, sonra:
ollama pull qwen2.5:3b
```

Gerekli Python sürümü: **3.10+**. Ana bağımlılıklar: `pandas`, `lxml`, `openpyxl`,
`mcp`, `streamlit`, `plotly` ve `ollama` (Akıllı Asistan için, bkz. aşağıda).

## Kullanım

### 1) Streamlit web arayüzü

```bash
.venv\Scripts\activate
streamlit run app/new_dashboard.py
```

Panel, proje kökündeki `.env` bağlantısıyla Jira verisini açılışta otomatik çeker.
Ay/Kişi/Proje filtreleriyle tüm pano anında güncellenir. Kenar çubuğundaki sayfa seçiciyle
şu 6 sayfa arasında gezinilir: Genel Bakış, Ekip & Kişiler, Proje & Konu, Akış &
Darboğazlar, Akıllı Asistan (Ollama tabanlı, tamamen yerel sohbet - 🔒 bkz.
yukarıdaki gizlilik notu) ve Rapor Merkezi (Excel indirme).

### Jira'ya Canlı Bağlanma

`.env.example` dosyasını `.env` olarak kopyalayıp Jira URL, PAT, proje anahtarı
ve Story Points alan ID'sini doldurun. Developer ve Analyst alan ID'leri isteğe
bağlıdır. Panel açılınca veri otomatik çekilir. Proje veya diğer bağlantı
ayarları değişirse önceki projenin oturum verisi temizlenir ve yeni proje çekilir.

Sol panelin üstündeki **Jira Ayarları** bölümünde `.env` bağlantısı ve alan ID'leri
düzenlenip kaydedilir. PAT alanı boş bırakılırsa mevcut token korunur. Kaydetme,
yeni oturum veya `.env` değişikliği veriyi otomatik yeniden çeker.
Jira erişilemezse önceki projenin verisi gösterilmez; bağlantı hatası yazılır.
PAT ekranda gösterilmez.

WIP Aging seçili sprintteki açık işleri oluşturulma ayına göre **0-1 ay**,
**2-5 ay** ve **6+ ay** gruplarına ayırır. Üç grubun toplamı aktif iş sayısıdır.

### 2) MCP sunucusu (Claude Desktop vb.)

`src/mcp_server.py`, bir MCP istemcisinin `stdio` üzerinden bağlanabileceği
13 araç sunar - tümü `file_path` parametresi alır (kendi Jira raporunuzun -
HTML/CSV/XLSX - yolu); göreli yollar proje köküne göre otomatik çözümlenir.

| Araç | Ne yapar |
|---|---|
| `analyze_sprint` | Bir ayın KPI özetini + örnek iş listelerini döner |
| `create_sprint_excel` | Biçimlendirilmiş, grafikli Excel raporu üretir |
| `get_assignee_performance` | Kişi bazlı iş yükü/performans tablosu |
| `get_status_breakdown` | Statü bazlı iş/SP dağılımı |
| `get_sprint_trends` | Aylar arası KPI karşılaştırması |
| `search_issues_by_query` | Doğal dilde çok kelimeli serbest arama |
| `get_bottlenecks_report` | Klasik tıkanıklık/darboğaz tespiti |
| `get_estimation_accuracy_report` | Talep tipi bazlı tahmin sapma analizi |
| `get_core_5_kpis_report` | 5 boyutlu profesyonel KPI paketi (kişi/proje bazına inilebilir) |
| `get_project_subject_analysis` | Proje/konu bazlı kaynak tüketimi analizi |
| `get_assignee_details_tool` | Bir kişinin tüm görevlerine drill-down erişim |
| `query_sprint_data_natural` | Serbest, tek cümlelik doğal dil soru-cevap |
| `get_advanced_bottlenecks_report` | WIP Aging, Blocker & Hold, Bouncing, Reopen, Flow Load |

Claude Desktop'a bağlamak için `claude_desktop_config.json` dosyanıza şunun
benzerini ekleyin:

```json
{
  "mcpServers": {
    "jira-sprint-analyzer": {
      "command": "python",
      "args": ["/mutlak/yol/JIRA_MCP/src/mcp_server.py"]
    }
  }
}
```

(`/mutlak/yol/JIRA_MCP/src/mcp_server.py` kısmını kendi ortamınızdaki gerçek
mutlak yolla değiştirin, örn. Windows'ta `C:/.../JIRA_MCP/src/mcp_server.py`.
`command` alanına, `.venv` sanal ortamındaki `python` yorumlayıcısının tam
yolunu vermeniz gerekebilir, örn. `C:/.../JIRA_MCP/.venv/Scripts/python.exe`.)

### 3) Akıllı Asistan için Ollama kurulumu

`app/new_dashboard.py`'deki "💬 Akıllı Asistan" sayfası, [Ollama](https://ollama.com)
üzerinden **tamamen yerel** çalışan bir dil modeliyle (varsayılan model:
`qwen2.5:3b`) çalışır. Asistan, sorularınızı yanıtlarken `mcp_server.py`'nin 13
gerçek MCP aracını çağırır - sayılar asla tahmin edilmez, her zaman gerçek
hesaplanmış veriden gelir.

> 🔒 **Bu tamamen yerel bir kurulumdur, internet/bulut gerektirmez.** Sorularınız
> ve ilgili Jira/personel verisi (isimler, iş yükü, performans metrikleri
> dahil) bu bilgisayarın dışına ASLA çıkmaz - Ollama, kendi makinenizde
> (varsayılan `localhost:11434`) çalışan bir servistir. Ollama'ya erişilemezse
> veya model çekilmemişse, asistan başka bir buluta SESSİZCE düşmez; açık bir
> hata gösterir (bkz. `AssistantUnavailableError`) - bu, veri sızıntısını
> önlemek için kasıtlı bir tasarım kararıdır.

Kurulum:

1. [ollama.com/download](https://ollama.com/download) adresinden Ollama'yı
   indirip kurun ve çalıştırın.
2. Varsayılan modeli çekin:
   ```
   ollama pull qwen2.5:3b
   ```

Farklı bir model kullanmak isterseniz (`ollama pull <model-adı>` ile çektikten
sonra) `src/llm_assistant.py` içindeki `DEFAULT_MODEL` sabitini güncelleyin.
Farklı modellerin araç-seçme doğruluğunu/hızını karşılaştırmak için
`scripts/test_ollama_tool_calling.py` bağımsız test scriptini kullanabilirsiniz.

## Girdi veri formatı

Sistem, Jira'nın **HTML** (`.html`/`.htm`), **CSV** (`.csv`) veya **Excel**
(`.xlsx`) dışa aktarım (export) dosyalarından herhangi birini kabul eder -
dosya uzantısına göre otomatik olarak doğru okuyucu seçilir. CSV/XLSX
dosyalarında birden fazla sayfa/tablo varsa, Jira'ya özgü başlıklara en çok
benzeyen tablo otomatik seçilir (HTML'de birden fazla tablo olması durumuyla
aynı mantık). Kolon başlıkları Türkçe/İngilizce varyasyonlarıyla otomatik
eşlenir (`Sorumlu`/`Assignee`, `Story Points`/`Estimate`/`Custom field (Story
Points)`, `Oluşturulma Tarihi`/`Created`, `Project`/`Project name` vb.);
zorunlu alanlar `issue_type`, `summary` ve `status`'tür. Plan dışı işler her
zaman `labels` alanındaki "SprintDışı" etiketiyle (yazım varyasyonlarından
bağımsız) tespit edilir. Ek olarak `SPRINT_DISI_FALLBACK_ENABLED=true` ise,
seçili sprint ayının ilk gününden bir tam gün sonra oluşturulan etiketsiz
kartlar da yalnızca analizde plan dışı kabul edilir; Jira'ya etiket yazılmaz.

Akış & Darboğazlar sayfasındaki **Devreden İşler** paneli, seçili sprintte olup
Jira Sprint alanında daha eski bir sprint üyeliği de bulunan kartları ayrı
gösterir. Bu panel Created tarihine veya benzer iş adına göre tahmin yapmaz.
