# Jira Sprint & KPI Paneli — RZN Ekibi Kullanım Rehberi

Bu panel, Jira verinizden iterasyon kapanış raporunu ve sprint içi haftalık
değerlendirmeyi otomatik üretir. Elle Excel hazırlamaya gerek kalmaz.

---

## 1. Kurulum (bir kez)

1. Proje klasöründeki **`Kurulum.bat`** dosyasına çift tıklayın.
   Python ortamını kurar ve bağımlılıkları indirir. Birkaç dakika sürer.
2. Bittiğinde **`Uygulamayi_Baslat.bat`** ile açın. Panel tarayıcınızda açılır.

Sonraki her kullanımda sadece 2. adım yeterli.

> Kurulum sırasında *"Ollama bulunamadı"* uyarısı görebilirsiniz. Sorun değil —
> bu yalnızca **Akıllı Asistan** sohbet sayfasını etkiler, panelin geri kalanı
> normal çalışır.

---

## 2. Veriyi yükleme

Sol menüdeki **Veri Kaynağı** seçiminden iki yol var:

### 📁 Dosya Yükle
Jira'dan dışa aktardığınız raporu (HTML, CSV veya XLSX) sürükleyip bırakın.

### 🔗 Jira'ya Canlı Bağlan *(önerilen)*
Dışa aktarma adımını atlar, veriyi doğrudan çeker:

| Alan | RZN için değer |
|---|---|
| Jira URL | `https://jira.turkcell.com.tr` |
| Personal Access Token | Jira → sağ üst profil → **Profile → Personal Access Tokens → Create token** |
| Proje Anahtarı | **`RZN`** |
| Kaç ay geriye | Varsayılan 6 (ihtiyacınıza göre artırın) |

**Bağlan ve Keşfet** → Story Points / Developer / Analist alanları otomatik bulunur,
örnek kartlarla gözle doğrularsınız → **Onayla ve Tam Veriyi Çek**.

Token diske yazılmaz, sadece o oturumda bellekte tutulur.

---

## 3. Board konvansiyonları — doğru sonuç için gerekli

Panel bu dört alanı okur. Board'da nasıl doldurduğunuz, raporun doğruluğunu
doğrudan belirler.

### Sprint alanı → hangi iterasyona ait

Bir kartın hangi aya sayılacağı **Sprint alanından** belirlenir, kartın açılma
tarihinden değil. Böylece aylardır devreden bir iş, açıldığı ayda değil **içinde
bulunduğu her iterasyonda** sayılır.

Mevcut adlandırmanız desteklenir:

```
Eylül İterasyonu - 2025   ✅
Şubat İterasyonu - 2026   ✅
```

Tek kural: **ay adı ve yıl bulunsun.** `Sprint 2`, `İterasyon 3` gibi adlar hiçbir
aya bağlanamaz; o kartlar açılma tarihine göre sayılır.

### `SprintDışı` etiketi → planlanan mı, plan dışı mı

Rapor iş listesini ikiye ayırır:

| Kart | Etiket |
|---|---|
| Sprint başında planlanan iş | *(etiket yok)* |
| Sprint ortasında gelen, planlanmamış iş | **`SprintDışı`** |

Fallback kapalıyken etiket yoksa her şey "planlanan" sayılır ve *"plan dışı iş yükümüz ne kadar?"*
sorusu cevapsız kalır. Yazım esnektir: `SprintDışı`, `SprintDisi`, `Sprint Dışı`
hepsi tanınır.

### Kart başlığında `(%x)` → tek sprintte bitmeyen işler

Bir iş sprint sonunda tamamlanmayacaksa, o sprintte hedeflenen oranı başlığa yazın:

```
Fairvalue detail graph (%70)
5G Etki Analizi (%40)
```

İki işe yarar:

1. Raporun **Hedeflenen Statü** kolonuna `Done (%70)` yazılır.
2. Panel yüzdeyi silip "temel isim" üretir — böylece farklı aylarda açılan
   `Analiz (%40)` ve `Analiz (%80)` kartlarını **aynı iş** olarak eşleştirir ve
   *"bu iş 3 aydır devam ediyor"* tespitini yapabilir.

> Panel yüzdeleri birbiriyle **karşılaştırmaz**. *"%40'tan %80'e çıktı, 2 ayda 40
> puan ilerledi"* diyemez; *"aynı iş, hâlâ devam ediyor"* diyebilir. Yüzde
> yazmanın bugünkü faydası iş eşleştirme ve hedef statü üretimidir.

### Story Points → bütün hesapların temeli

Taahhüt, gerçekleşen, tempo, kişi yükü — hepsi SP üzerinden hesaplanır. Boş
bırakılan kart hiçbir toplama girmez. *(RZN'de kartların %98'i dolu, sorun yok.)*

---

## 4. Panelde ne var

Sol menüden **İterasyon / Sprint**, **Kişi** ve **Proje** filtreleri seçilir; tüm
sayfalar bu seçime göre güncellenir.

| Sayfa | Ne gösterir |
|---|---|
| 🏠 **Genel Bakış** | Taahhüt/gerçekleşen/plan dışı SP, tamamlanma oranı, iterasyon bazlı bar grafiği, 5 temel KPI, tahmin doğruluğu |
| 🗓️ **Haftalık Özet** | Sprint **devam ederken** haftalık değerlendirme — aşağıda ayrıntı |
| 👥 **Ekip & Kişiler** | Kişi bazlı iş yükü, en yüklü kişi, rol bazlı dağılım, kişi drill-down |
| 📁 **Proje & Konu** | Component bazlı dağılım *(RZN'de component boş — bkz. bölüm 6)* |
| 🚧 **Akış & Darboğazlar** | Statü dağılımı, WIP yaşlanması, tıkanan işler, tekrarlayan darboğazlar |
| 💬 **Akıllı Asistan** | Doğal dilde soru-cevap *(Ollama kurulumu gerekir)* |
| 📤 **Rapor Merkezi** | Excel ve PDF iterasyon raporu |

### Haftalık Özet

Hafta seçersiniz, panel o haftayı kural bazlı değerlendirir. Örnek çıktı:

```
🔴 Kalan 732 SP'yi 3 haftada bitirmek için ~244 SP/hafta gerekiyor;
   mevcut hız ~87 SP/hafta. Bu farkla sprint hedefinin tamamı riskte.
🟠 Tempo, son 4 haftanın ortalamasının %72 altında.
🟢 Bu hafta 26 iş tamamlandı, toplam 295 SP.
⚪ Eylül 2026 sprintinin 2. haftasındayız (3 hafta kaldı).
```

Yapay zekâ kullanmaz — eşikler bellidir, aynı veriye hep aynı cevabı verir.

### Raporlar ve paylaşım

**Rapor Merkezi** iterasyon kapanış raporunu **Excel** ve **PDF** olarak üretir:
bar grafiği + "planlanan iş listemiz ve statüleri" + "plan dışı iş listemiz ve
statüleri".

Ayrıca beş sayfanın altında **Paylaş** bölümü var: ekrandaki içeriği
**e-postaya yapıştırılabilir HTML** ya da düz metin olarak indirir. Outlook'a
yapıştırınca biçim bozulmaz.

---

## 5. Tipik akış

**Sprint sonunda:**
Canlı bağlan → İterasyon filtresinden ilgili sprinti seç → Rapor Merkezi →
Excel/PDF indir → maille paylaş.

**Sprint devam ederken (haftalık):**
Haftalık Özet → geçen haftayı seç → değerlendirmeyi oku → Paylaş'tan HTML indirip
ekibe gönder.

---

## 6. Bilinen kısıtlar

Bunlar RZN'ye özgü; panel hata vermez ama bu başlıklarda **yanlış sonuç üretir**.

### Bloke ve iptal statüleri tanınmıyor

Statü yapınız fazlara ayrılmış:

```
Analiz · Analiz Done · Analiz XL Block · Development · Development Done ·
Development XL BLOCK · Done · Test · Test Done · Test XL Block · To Do · İptal
```

Panel bu statülerden:
- **`Done`'u doğru tanır** → tamamlanma oranları, KPI'lar, raporlar **doğru**
- **Üç `XL Block` varyantını tanımaz** → *bloke iş* ve *duran iş* sayıları **0 çıkar**
- **`İptal`'i tanımaz** → iptal edilen kartlar "hâlâ açık" sayılır

**Pratik sonuç:** Haftalık Özet'teki "bloke iş" ve "duran iş" satırlarına ve Akış &
Darboğazlar sayfasındaki bloke sayılarına RZN'de **güvenmeyin**. Sprint KPI'ları,
tamamlanma oranları, tempo ve raporlar etkilenmez.

### Component alanı boş

RZN kartlarında component dolu değil; **Proje & Konu** sayfası tek bir "Component
Yok" grubu gösterir. Konu bazlı analiz istiyorsanız component doldurulmalı.

### Epic yok

RZN'de Epic issue type tanımlı değil, Epic bazlı gruplama karşılıksız kalır.

---

## 7. Sık sorulanlar

**Verilerim dışarı çıkıyor mu?**
Hayır. Panel bu bilgisayarda çalışır. Akıllı Asistan da yerel bir modele (Ollama)
bağlanır; hiçbir buluta veri gönderilmez.

**Bir kart iki sprintte görünüyorsa iki kez mi sayılır?**
Evet, yer aldığı her sprintin toplamına girer. Devreden işin her iterasyonda
görünmesi kasıtlıdır. Akış & Darboğazlar sayfasındaki **Devreden İşler** paneli,
seçili sprintteki bu kartları önceki sprintleri, statüleri, sorumluları ve SP
değerleriyle ayrıca listeler. Bu, iş adı+sorumlu benzerliğine bakan **Devam Eden
Darboğazlar (İsim Bazlı)** bölümünden farklıdır.

**SprintDışı etiketi kullanılmayan ekiplerde ne olur?**
`.env` içinde `SPRINT_DISI_FALLBACK_ENABLED=true` yapılırsa seçili sprint ayının
ilk gününden bir tam gün sonra oluşturulan kartlar analiz sırasında plan dışı
kabul edilir. Ayın ilk iki günü toleranstır; ayın 3'ü ve sonrası fallback'e
girer. Bu sınıflandırma Jira kartını veya etiketlerini değiştirmez. Ayar `false`
iken yalnızca mevcut `SprintDışı` etiketi kullanılır.

**Sprint alanı boş kartlar ne olur?**
Kaybolmazlar; açılma tarihinin ayına sayılırlar.

**Rapordaki sayı elle hesapladığımdan farklı çıkıyor.**
Önce şunları kontrol edin: doğru iterasyon seçili mi, Story Points boş kart var mı,
devreden kartları siz tek kez mi sayıyorsunuz.

---

*Hazırlayan: Bera Eren Tutkun · 21.09.2026*
