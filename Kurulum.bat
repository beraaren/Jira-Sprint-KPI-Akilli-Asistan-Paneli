@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   Jira Sprint ve KPI Panosu - Kurulum
echo ============================================
echo.

REM 1-2) Surumu UYGUN (3.10+) bir Python bul.
REM     Adaylar sirayla denenir: once "python", sonra "py -3".
REM     Sebep: PATH'teki "python" cogu makinede eski bir surume (orn. 3.9)
REM     isaret edebiliyor. Eskiden sadece ona bakilip surum eskiyse pes
REM     ediliyordu; oysa ayni makinede "py -3" guncel surumu verebiliyor ve
REM     kurulum gereksiz yere durduruluyordu.
set PYEXE=
set PYVER=
call :SURUM_DENE "python"
if "!PYEXE!"=="" call :SURUM_DENE "py -3"

if "!PYEXE!"=="" (
    echo [HATA] Surumu uygun bir Python bulunamadi ^(en az 3.10 gerekli^).
    if not "!PYBULUNAN!"=="" echo        Bulunan surum: !PYBULUNAN!
    echo.
    echo Guncel bir surumu su adresten indirip kurun:
    echo   https://python.org/downloads
    echo Kurulum ekraninda "Add python.exe to PATH" kutusunu isaretlemeyi
    echo unutmayin. Kurulumdan sonra bu dosyayi tekrar calistirin.
    echo.
    pause
    exit /b 1
)
echo [OK] Python bulundu ^(!PYVER!, "!PYEXE!"^).

REM 3) ".venv" klasoru yoksa olustur
echo.
if not exist ".venv" (
    echo [BILGI] ".venv" bulunamadi, olusturuluyor...
    %PYEXE% -m venv .venv
    if errorlevel 1 (
        echo.
        echo [HATA] Sanal ortam olusturulamadi. Yukaridaki hata mesajina bakin.
        pause
        exit /b 1
    )
    echo [OK] ".venv" olusturuldu.
) else (
    echo [OK] ".venv" zaten mevcut.
)

REM 4) Ortami aktive et ve bagimliliklari kur
echo.
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo.
    echo [HATA] Sanal ortam aktive edilemedi.
    pause
    exit /b 1
)
echo Pip guncelleniyor...
python -m pip install --upgrade pip
echo Bagimliliklar kuruluyor (pip install -r requirements.txt)...
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [HATA] Bagimliliklar kurulamadi. Yukaridaki hata mesajina bakin.
    echo Bu genellikle internet baglantisi sorunu veya cok eski/cok yeni bir Python
    echo surumunden kaynaklanir. Python surumunuzu guncel bir surumle ^(python.org^)
    echo degistirmeyi deneyin.
    pause
    exit /b 1
)
echo [OK] Bagimliliklar kuruldu.

REM 5) Ollama kurulu mu? (OPSIYONEL - kurulumu DURDURMAZ)
REM     Ollama yalnizca "Akilli Asistan" sayfasinin sohbet motorudur; panelin
REM     geri kalani (raporlar, KPI'lar, haftalik ozet) onsuz de tam calisir ve o
REM     sayfa erisemedigini kendi icinde acikca bildirir. Bu yuzden Ollama'nin
REM     yoklugu bir HATA degil UYARIDIR - eskiden burada "exit /b 1" vardi ve
REM     kurulum, opsiyonel bir bilesen yuzunden hic tamamlanamiyordu.
echo.
set OLLAMA_HAZIR=0
where ollama >nul 2>&1
if errorlevel 1 (
    echo [UYARI] Ollama bulunamadi - "Akilli Asistan" sayfasi calismayacak.
    echo         Panelin diger tum sayfalari normal sekilde kullanilabilir.
    echo         Istenirse sonradan kurulabilir: https://ollama.com/download
) else (
    set OLLAMA_HAZIR=1
    echo [OK] Ollama bulundu.
)

REM 6) "qwen2.5:3b" modeli cekilmis mi? (sadece Ollama varsa)
if !OLLAMA_HAZIR! EQU 1 (
    echo.
    echo Ollama modelleri kontrol ediliyor...
    ollama list | findstr /c:"qwen2.5:3b" >nul
    if errorlevel 1 (
        echo [BILGI] "qwen2.5:3b" modeli bulunamadi, indiriliyor ^(yaklasik 2 GB, biraz surebilir^)...
        ollama pull qwen2.5:3b
        if errorlevel 1 (
            echo [UYARI] Model indirilemedi - "Akilli Asistan" sayfasi calismayacak.
            echo         Ollama servisinin calisir durumda oldugundan emin olun.
        ) else (
            echo [OK] "qwen2.5:3b" modeli indirildi.
        )
    ) else (
        echo [OK] "qwen2.5:3b" modeli zaten mevcut.
    )
)

echo.
echo ============================================
echo   Kurulum tamamlandi!
echo   Artik Uygulamayi_Baslat.bat dosyasina cift
echo   tiklayarak uygulamayi acabilirsiniz.
echo ============================================
echo.
pause
exit /b 0


REM --------------------------------------------------------------------------
REM :SURUM_DENE <komut>
REM   Verilen Python komutunu calistirip surumune bakar; 3.10+ ise PYEXE/PYVER
REM   degiskenlerine yazar, degilse hicbir sey yapmaz (bir sonraki aday denenir).
REM   Komut hic yoksa da sessizce gecer - "where" ile ayrica kontrol gerekmez.
REM --------------------------------------------------------------------------
:SURUM_DENE
set "ADAY=%~1"
set "ADAYVER="
for /f "tokens=2" %%v in ('%ADAY% --version 2^>^&1') do set "ADAYVER=%%v"
if "!ADAYVER!"=="" goto :eof
set "PYBULUNAN=!ADAYVER!"
for /f "tokens=1,2 delims=." %%a in ("!ADAYVER!") do (
    set "ADAYMAJOR=%%a"
    set "ADAYMINOR=%%b"
)
REM Surum metni beklenmedik bicimdeyse (orn. "Python" yazisi gelmediyse) atla.
echo !ADAYMAJOR!| findstr /r "^[0-9][0-9]*$" >nul || goto :eof
echo !ADAYMINOR!| findstr /r "^[0-9][0-9]*$" >nul || goto :eof
if !ADAYMAJOR! LSS 3 goto :eof
if !ADAYMAJOR! EQU 3 if !ADAYMINOR! LSS 10 goto :eof
set "PYEXE=!ADAY!"
set "PYVER=!ADAYVER!"
goto :eof
