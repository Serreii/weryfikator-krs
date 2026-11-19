@echo off
chcp 65001 >nul
title Weryfikator KRS - B2B Lead Scorer

echo ===============================================================================
echo                WERYFIKATOR KRS ^& B2B LEAD SCORER (PROSPECTING)
echo ===============================================================================
echo.

:: Sprawdzenie czy Python jest zainstalowany
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [BLAD] Python nie zostal odnaleziony w systemie!
    echo Zainstaluj Python ze strony https://www.python.org/ i zaznacz opcje "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

:: Sprawdzenie lub utworzenie srodowiska wirtualnego venv
if not exist "venv\Scripts\activate.bat" (
    echo [*] Tworzenie srodowiska wirtualnego venv...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [BLAD] Nie udalo sie utworzyc srodowiska wirtualnego venv.
        pause
        exit /b 1
    )
    echo [*] Srodowisko venv utworzone pomyslnie.
)

:: Aktywacja srodowiska wirtualnego
echo [*] Aktywacja srodowiska wirtualnego venv...
call venv\Scripts\activate.bat

:: Instalacja lub aktualizacja zaleznosci
echo [*] Sprawdzanie i instalacja wymaganych bibliotek z requirements.txt...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [OSTRZEZENIE] Wystapily ostrzezenia podczas instalacji bibliotek, proba uruchomienia...
)

:: Uruchomienie aplikacji Streamlit w przegladarce
echo.
echo ===============================================================================
echo [*] Uruchamianie aplikacji w przegladarce...
echo [*] Adres lokalny: http://localhost:8501
echo [*] Aby zakonczyc dzialanie serwera, wcisnij Ctrl + C w tym oknie.
echo ===============================================================================
echo.

streamlit run app.py

pause
