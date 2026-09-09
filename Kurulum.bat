@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   Jira Sprint ve KPI Panosu - Kurulum
echo ============================================
echo.

REM 1) Python kurulu mu?
set PYEXE=
where python >nul 2>&1
if not errorlevel 1 (
    set PYEXE=python
) else (
    where py >nul 2>&1
    if not errorlevel 1 (
        set PYEXE=py
    )
)
if "%PYEXE%"=="" (
    echo [HATA] Python bulunamadi.
    echo.
    echo Once Python'i su adresten indirip kurun:
    echo   https://python.org/downloads
    echo Kurulum ekraninda "Add python.exe to PATH" kutusunu isaretlemeyi
    echo unutmayin. Kurulumdan sonra bu dosyayi tekrar calistirin.
    echo.
    pause
    exit /b 1
)
echo [OK] Python bulundu.

REM 2) Python surumu 3.10+ mi?
for /f "tokens=2" %%v in ('%PYEXE% --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set PYMAJOR=%%a
    set PYMINOR=%%b
)
set PYTOOOLD=0
if %PYMAJOR% LSS 3 set PYTOOOLD=1
if %PYMAJOR% EQU 3 if %PYMINOR% LSS 10 set PYTOOOLD=1
if !PYTOOOLD! EQU 1 (
    echo [HATA] Python surumunuz cok eski ^(%PYVER%^). En az Python 3.10 gerekli.
    echo.
    echo Guncel bir surumu su adresten indirip kurun:
    echo   https://python.org/downloads
    echo.
    pause
    exit /b 1
)
echo [OK] Python surumu uygun ^(%PYVER%^).

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

REM 5) Ollama kurulu mu?
echo.
where ollama >nul 2>&1
if errorlevel 1 (
    echo [HATA] Ollama bulunamadi.
    echo.
    echo Once Ollama'yi su adresten indirip kurun:
    echo   https://ollama.com/download
    echo Kurulumdan sonra bu dosyayi tekrar calistirin.
    echo.
    pause
    exit /b 1
)
echo [OK] Ollama bulundu.

REM 6) "qwen2.5:3b" modeli cekilmis mi?
echo.
echo Ollama modelleri kontrol ediliyor...
ollama list | findstr /c:"qwen2.5:3b" >nul
if errorlevel 1 (
    echo [BILGI] "qwen2.5:3b" modeli bulunamadi, indiriliyor ^(yaklasik 2 GB, biraz surebilir^)...
    ollama pull qwen2.5:3b
    if errorlevel 1 (
        echo.
        echo [HATA] Model indirilemedi. Ollama servisinin calisir durumda oldugundan emin olun.
        pause
        exit /b 1
    )
    echo [OK] "qwen2.5:3b" modeli indirildi.
) else (
    echo [OK] "qwen2.5:3b" modeli zaten mevcut.
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
