"""
Moduł weryfikacji danych z darmowych rejestrów publicznych:
1. Rejestr Przedsiębiorców KRS (API Ministerstwa Sprawiedliwości)
2. Biała Lista Podatników VAT (API Ministerstwa Finansów)
3. Rejestr BDO (Wyszukiwarka Rejestru BDO)
4. Weryfikacja obecności w sieci, scrapowanie WWW i wywiad głęboki (DuckDuckGo search bez klucza API)
"""

import re
import time
import random
import logging
import unicodedata
from datetime import datetime, date
from urllib.parse import urlparse, quote_plus
from typing import List, Dict, Tuple, Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verifiers")

# Konfiguracja sesji HTTP z obsługą ponawiania prób (retry) i timeoutami
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
}

def create_resilient_session(retries: int = 3, backoff_factor: float = 0.8) -> requests.Session:
    """Tworzy sesję HTTP z automatycznym ponawianiem prób dla typowych błędów sieciowych i 5xx/429."""
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST", "HEAD"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

GLOBAL_SESSION = create_resilient_session()

def rate_limit_delay(min_s: float = 0.6, max_s: float = 1.2):
    """Losowe opóźnienie chroniące przed blokadą IP przez serwery rządowe i wyszukiwarki."""
    time.sleep(random.uniform(min_s, max_s))

def clean_krs(krs_raw) -> str:
    """Czyści numer KRS i uzupełnia zera wiodące do 10 cyfr (np. '12345' -> '0000012345')."""
    if krs_raw is None:
        return ""
    val_str = str(krs_raw).strip()
    if val_str.endswith(".0"):
        val_str = val_str[:-2]
    digits = re.sub(r"\D", "", val_str)
    if digits:
        return digits.zfill(10)
    return ""

def clean_nip(nip_raw) -> str:
    """Czyści numer NIP usuwając myślniki, spacje i litery."""
    if nip_raw is None:
        return ""
    digits = re.sub(r"\D", "", str(nip_raw).strip())
    return digits

def transliterate_pl(text: str) -> str:
    """Usuwa polskie znaki diakrytyczne i normalizuje tekst do prostego ASCII."""
    if not text:
        return ""
    pl_map = {
        'ą': 'a', 'ć': 'c', 'ę': 'e', 'ł': 'l', 'ń': 'n', 'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
        'Ą': 'A', 'Ć': 'C', 'Ę': 'E', 'Ł': 'L', 'Ń': 'N', 'Ó': 'O', 'Ś': 'S', 'Ź': 'Z', 'Ż': 'Z'
    }
    res = ''.join(pl_map.get(ch, ch) for ch in text)
    res = unicodedata.normalize('NFKD', res).encode('ascii', 'ignore').decode('utf-8')
    return res

def shorten_company_name(name: str) -> str:
    """Skraca pełną nazwę firmy do zwięzłej nazwy handlowej usuwając formy prawne (ułatwia wyszukiwanie w Google/DDG)."""
    if not name:
        return ""
    patterns_to_remove = [
        r"\bSPÓŁKA\s+Z\s+OGRANICZONĄ\s+ODPOWIEDZIALNOŚCIĄ\b",
        r"\bSPÓŁKA\s+AKCYJNA\b",
        r"\bSPÓŁKA\s+KOMANDYTOWA\b",
        r"\bSPÓŁKA\s+JAWNA\b",
        r"\bSPÓŁKA\s+PARTNERSKA\b",
        r"\bSPÓŁKA\s+KOMANDYTOWO-AKCYJNA\b",
        r"\bPROSTA\s+SPÓŁKA\s+AKCYJNA\b",
        r"\bSP\.\s*Z\s*O\.O\.\b",
        r"\bSP\.\s*K\.\b",
        r"\bSP\.\s*J\.\b",
        r"\bS\.A\.\b",
        r"\bP\.S\.A\.\b",
        r"\bW\s+LIKWIDACJI\b",
        r"\bW\s+UPADŁOŚCI\b",
        r'["“”„”\']'
    ]
    cleaned = name
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if len(cleaned) > 2 else name.strip()

def extract_val(obj, key=None):
    """Pobiera wartość pola zarówno ze słownika (OdpisAktualny), jak i z listy historii zmian (OdpisPelny)."""
    if obj is None:
        return None
    if isinstance(obj, list):
        if not obj:
            return None
        item = obj[-1]
        if isinstance(item, dict):
            if key and key in item:
                return extract_val(item[key])
            return item
        return item
    if isinstance(obj, dict):
        if key and key in obj:
            return extract_val(obj[key])
        return obj
    return obj


# ==============================================================================
# MODUŁ A: OFICJALNE API REJESTRU PRZEDSIĘBIORCÓW KRS
# ==============================================================================

def verify_krs(krs_number: str) -> dict:
    """
    Pobiera i analizuje dane podmiotu z oficjalnego API Ministerstwa Sprawiedliwości KRS.
    Wyciąga dane podstawowe, pełną listę kodów PKD, oddziały terenowe, sposób reprezentacji,
    zaległości z Działu 4, kapitał zakładowy, zarząd, wspólników, sprawozdania finansowe i daty.
    """
    krs = clean_krs(krs_number)
    result = {
        "krs": krs,
        "nazwa": "",
        "nazwa_skrocona": "",
        "nip": "",
        "regon": "",
        "forma_prawna": "",
        "data_rejestracji": "",
        "wiek_firmy_lat": 0.0,
        "data_ostatniego_wpisu": "",
        "swieza_zmiana_krs": "NIE",
        "adres_pelny": "",
        "ulica": "",
        "nr_domu": "",
        "nr_lokalu": "",
        "ulica_nr": "",
        "kod_pocztowy": "",
        "miejscowosc": "",
        "wojewodztwo": "",
        "kapital_wartosc": 0.0,
        "kapital_waluta": "PLN",
        "kapital_str": "Brak danych",
        "status_krs": "BŁĄD POBIERANIA",
        "oddzialy": [],
        "oddzialy_str": "Brak oddziałów",
        "reprezentacja_opis": "",
        "reprezentacja": "Brak danych",
        "zaleglosci_krs": "NIE",
        "pkd_przewazajace": [],
        "pkd_pozostale": [],
        "pkd_kody": [],
        "pkd_pelne_lista": [],
        "zarzad": [],
        "zarzad_str": "Brak danych",
        "prokurenci": [],
        "prokurenci_str": "Brak",
        "wspolnicy": [],
        "wspolnicy_str": "Brak danych",
        "ostatnie_sf_data": "Brak",
        "ostatnie_sf_okres": "Brak",
        "ostatnie_sf_rok": None,
        "brak_aktualnych_sf": True,
        "strona_www_krs": "",
        "email_krs": "",
        "krs_error": None
    }

    if not krs or len(krs) != 10:
        result["krs_error"] = f"Nieprawidłowy numer KRS: {krs_number}"
        result["status_krs"] = "BŁĄD POBIERANIA"
        return result

    url_aktualny = f"https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs}?rejestr=P&format=json"
    url_pelny = f"https://api-krs.ms.gov.pl/api/krs/OdpisPelny/{krs}?rejestr=P&format=json"

    try:
        resp = GLOBAL_SESSION.get(url_aktualny, timeout=12)
        odpis = None
        is_pelny = False

        if resp.status_code == 200:
            data = resp.json()
            odpis = data.get("odpis", {})
        elif resp.status_code in (204, 404):
            resp_pelny = GLOBAL_SESSION.get(url_pelny, timeout=12)
            if resp_pelny.status_code == 200:
                data_pelny = resp_pelny.json()
                odpis = data_pelny.get("odpis", {})
                is_pelny = True
            else:
                result["status_krs"] = "BŁĄD POBIERANIA"
                result["krs_error"] = f"Nie odnaleziono podmiotu w KRS (status HTTP: {resp.status_code})"
                return result
        else:
            result["status_krs"] = "BŁĄD POBIERANIA"
            result["krs_error"] = f"Błąd API KRS (status HTTP: {resp.status_code})"
            return result

        if not odpis:
            result["status_krs"] = "BŁĄD POBIERANIA"
            result["krs_error"] = "Pusta odpowiedź z API KRS"
            return result

        dane = odpis.get("dane", {})
        naglowek = odpis.get("naglowekA" if not is_pelny else "naglowekP", {})

        # 1. Nagłówek i rejestracja oraz daty
        data_rej = str(naglowek.get("dataRejestracjiWKRS") or "").strip()
        result["data_rejestracji"] = data_rej
        if data_rej:
            try:
                reg_dt = datetime.strptime(data_rej, "%d.%m.%Y")
                result["wiek_firmy_lat"] = round((datetime.now() - reg_dt).days / 365.25, 1)
            except Exception:
                result["wiek_firmy_lat"] = 0.0

        data_ost_wpisu = str(naglowek.get("dataOstatniegoWpisu") or "").strip()
        result["data_ostatniego_wpisu"] = data_ost_wpisu
        if data_ost_wpisu:
            try:
                wpis_dt = datetime.strptime(data_ost_wpisu, "%d.%m.%Y")
                days_diff = (datetime.now() - wpis_dt).days
                if days_diff <= 180:
                    result["swieza_zmiana_krs"] = "TAK"
            except Exception:
                result["swieza_zmiana_krs"] = "NIE"

        # 2. Dział 1 – Dane podstawowe, identyfikatory, siedziba i kapitał
        d1 = dane.get("dzial1", {})
        dane_podmiotu = d1.get("danePodmiotu", {})

        raw_nazwa = extract_val(dane_podmiotu.get("nazwa"), "nazwa") or ""
        result["nazwa"] = str(raw_nazwa).strip()
        result["nazwa_skrocona"] = shorten_company_name(result["nazwa"])

        raw_forma = extract_val(dane_podmiotu.get("formaPrawna"), "formaPrawna") or ""
        result["forma_prawna"] = str(raw_forma).strip()

        raw_ids = extract_val(dane_podmiotu.get("identyfikatory"), "identyfikatory")
        if isinstance(raw_ids, dict):
            result["nip"] = clean_nip(raw_ids.get("nip", ""))
            result["regon"] = str(raw_ids.get("regon", "")).strip()

        # Adres i siedziba
        siedziba_i_adres = d1.get("siedzibaIAdres", {})
        adres_obj = extract_val(siedziba_i_adres.get("adres"))
        if not isinstance(adres_obj, dict):
            adres_obj = {}

        ulica = str(adres_obj.get("ulica") or "").strip()
        nr_domu = str(adres_obj.get("nrDomu") or "").strip()
        nr_lokalu = str(adres_obj.get("nrLokalu") or "").strip()
        kod = str(adres_obj.get("kodPocztowy") or "").strip()
        miejscowosc = str(adres_obj.get("miejscowosc") or "").strip()

        siedziba_obj = extract_val(siedziba_i_adres.get("siedziba"))
        wojewodztwo = ""
        if isinstance(siedziba_obj, dict):
            wojewodztwo = str(siedziba_obj.get("wojewodztwo") or "").strip()

        result["ulica"] = ulica
        result["nr_domu"] = nr_domu
        result["nr_lokalu"] = nr_lokalu
        result["kod_pocztowy"] = kod
        result["miejscowosc"] = miejscowosc
        result["wojewodztwo"] = wojewodztwo

        # Ulica + Nr (dla czystych kolumn)
        u_nr_parts = []
        if ulica:
            u_nr_parts.append(f"ul. {ulica} {nr_domu}".strip())
        elif nr_domu:
            u_nr_parts.append(f"nr {nr_domu}")
        if nr_lokalu:
            u_nr_parts.append(f"lok. {nr_lokalu}")
        result["ulica_nr"] = ", ".join(u_nr_parts) if u_nr_parts else ""

        adres_czesci = []
        if result["ulica_nr"]:
            adres_czesci.append(result["ulica_nr"])
        if kod or miejscowosc:
            adres_czesci.append(f"{kod} {miejscowosc}".strip())
        result["adres_pelny"] = ", ".join(adres_czesci) if adres_czesci else miejscowosc

        # Kontakt z KRS
        raw_www = extract_val(siedziba_i_adres.get("adresStronyInternetowej"), "adresStronyInternetowej")
        raw_email = extract_val(siedziba_i_adres.get("adresPocztyElektronicznej"), "adresPocztyElektronicznej")
        result["strona_www_krs"] = str(raw_www or "").strip().lower()
        result["email_krs"] = str(raw_email or "").strip().lower()

        # Kapitał zakładowy
        kapital_section = d1.get("kapital", {})
        kapital_obj = extract_val(kapital_section.get("wysokoscKapitaluZakladowego"))
        if isinstance(kapital_obj, dict):
            wart_str = str(kapital_obj.get("wartosc", "0")).replace(" ", "").replace(",", ".")
            waluta = str(kapital_obj.get("waluta", "PLN")).strip()
            try:
                result["kapital_wartosc"] = float(wart_str)
            except ValueError:
                result["kapital_wartosc"] = 0.0
            result["kapital_waluta"] = waluta
            result["kapital_str"] = f"{result['kapital_wartosc']:,.2f} {waluta}".replace(",", " ").replace(".", ",")

        # Oddziały terenowe z Działu 1
        oddzialy_raw = d1.get("jednostkiTerenoweOddzialy") or d1.get("oddzialy") or []
        oddzialy_list = []
        if isinstance(oddzialy_raw, list):
            for oddz in oddzialy_raw:
                if not isinstance(oddz, dict):
                    continue
                o_nazwa = oddz.get("nazwa", "")
                o_adres = oddz.get("adres", {})
                o_miejsc = o_adres.get("miejscowosc") or oddz.get("siedziba", {}).get("miejscowosc") or ""
                o_ulica = o_adres.get("ulica", "")
                o_dom = o_adres.get("nrDomu", "")
                oddz_text = f"{o_nazwa} ({o_miejsc}, ul. {o_ulica} {o_dom})".strip()
                oddzialy_list.append(oddz_text)
        result["oddzialy"] = oddzialy_list
        result["oddzialy_str"] = "; ".join(oddzialy_list) if oddzialy_list else "Brak oddziałów"

        # Wspólnicy (np. w sp. z o.o.)
        wspolnicy_raw = d1.get("wspolnicySpzoo") or d1.get("wspolnicy") or []
        wspolnicy_list = []
        if isinstance(wspolnicy_raw, list):
            for wsp in wspolnicy_raw:
                if not isinstance(wsp, dict):
                    continue
                nazwisko_dict = wsp.get("nazwisko", {})
                nazwisko = ""
                if isinstance(nazwisko_dict, dict):
                    nazwisko = nazwisko_dict.get("nazwiskoICzlon") or ""
                elif isinstance(nazwisko_dict, str):
                    nazwisko = nazwisko_dict
                if not nazwisko:
                    nazwisko = wsp.get("nazwa") or ""

                imiona_dict = wsp.get("imiona", {})
                imie = ""
                if isinstance(imiona_dict, dict):
                    imie = imiona_dict.get("imie") or ""
                elif isinstance(imiona_dict, str):
                    imie = imiona_dict

                udzialy = wsp.get("posiadaneUdzialy", "")
                czy_calosc = wsp.get("czyPosiadaCaloscUdzialow", False)
                calosc_txt = " (100% udziałów)" if czy_calosc else ""
                osoba_str = f"{imie} {nazwisko}".strip() if imie or nazwisko else "Podmiot prawny"
                if udzialy:
                    osoba_str += f" [{udzialy}]"
                osoba_str += calosc_txt
                wspolnicy_list.append(osoba_str.strip())
        result["wspolnicy"] = wspolnicy_list
        result["wspolnicy_str"] = "; ".join(wspolnicy_list[:5]) if wspolnicy_list else "Brak ujawnionych w odpisie"

        # 3. Dział 2 – Zarząd, reprezentacja i prokurenci
        d2 = dane.get("dzial2", {})
        rep = d2.get("reprezentacja", {})

        sposob_rep = ""
        if isinstance(rep, dict):
            sposob_rep = str(rep.get("sposobReprezentacji") or "").strip()
            result["reprezentacja_opis"] = sposob_rep
            sposob_lower = sposob_rep.lower()
            if "samodzielnie" in sposob_lower or "jednoosobowo" in sposob_lower:
                result["reprezentacja"] = "SAMODZIELNA"
            elif sposob_rep:
                result["reprezentacja"] = "ŁĄCZNA / WIELOOSOBOWA"
            else:
                result["reprezentacja"] = "Brak danych"

            sklad = rep.get("sklad", [])
            czlonkowie_zarzadu = []
            if isinstance(sklad, list):
                for czl in sklad:
                    if not isinstance(czl, dict):
                        continue
                    nazwisko_d = czl.get("nazwisko", {})
                    nazwisko = nazwisko_d.get("nazwiskoICzlon", "") if isinstance(nazwisko_d, dict) else str(nazwisko_d)
                    imiona_d = czl.get("imiona", {})
                    imie = imiona_d.get("imie", "") if isinstance(imiona_d, dict) else str(imiona_d)
                    funkcja = czl.get("funkcjaWOrganie", "Członek organu")
                    imie_nazwisko = f"{imie} {nazwisko}".strip()
                    if imie_nazwisko:
                        czlonkowie_zarzadu.append(f"{imie_nazwisko} ({funkcja})")
            result["zarzad"] = czlonkowie_zarzadu
            result["zarzad_str"] = "; ".join(czlonkowie_zarzadu) if czlonkowie_zarzadu else "Brak danych zarządu"

        # Prokurenci
        prokurenci_raw = d2.get("prokurenci", [])
        prokurenci_list = []
        if isinstance(prokurenci_raw, list):
            for pro in prokurenci_raw:
                if not isinstance(pro, dict):
                    continue
                nazwisko_d = pro.get("nazwisko", {})
                nazwisko = nazwisko_d.get("nazwiskoICzlon", "") if isinstance(nazwisko_d, dict) else str(nazwisko_d)
                imiona_d = pro.get("imiona", {})
                imie = imiona_d.get("imie", "") if isinstance(imiona_d, dict) else str(imiona_d)
                rodzaj = pro.get("rodzajProkury", "Prokurent")
                p_str = f"{imie} {nazwisko}".strip()
                if p_str:
                    prokurenci_list.append(f"{p_str} ({rodzaj})")
        result["prokurenci"] = prokurenci_list
        result["prokurenci_str"] = "; ".join(prokurenci_list) if prokurenci_list else "Brak"

        # 4. Dział 3 – Kody PKD i Sprawozdania finansowe
        d3 = dane.get("dzial3", {})

        # Przedmiot działalności (PKD)
        przedmiot_dzial = d3.get("przedmiotDzialalnosci", {})
        pkd_przew = przedmiot_dzial.get("przedmiotPrzewazajacejDzialalnosci", [])
        pkd_pozost = przedmiot_dzial.get("przedmiotPozostalejDzialalnosci", [])

        pkd_codes = []
        pkd_full_list = []

        def parse_pkd_entry(entry):
            if not isinstance(entry, dict):
                return None
            kd = str(entry.get("kodDzial") or "").strip()
            kk = str(entry.get("kodKlasa") or "").strip()
            kp = str(entry.get("kodPodklasa") or "").strip()
            opis = str(entry.get("opis") or "").strip()
            code_parts = [p for p in [kd, kk, kp] if p]
            code = ".".join(code_parts) if code_parts else kd
            return code, opis

        if isinstance(pkd_przew, list):
            for item in pkd_przew:
                res_pkd = parse_pkd_entry(item)
                if res_pkd and res_pkd[0]:
                    c, o = res_pkd
                    pkd_codes.append(c)
                    pkd_full_list.append({"kod": c, "opis": o, "typ": "przeważająca"})
                    result["pkd_przewazajace"].append(f"{c} - {o}")

        if isinstance(pkd_pozost, list):
            for item in pkd_pozost:
                res_pkd = parse_pkd_entry(item)
                if res_pkd and res_pkd[0]:
                    c, o = res_pkd
                    pkd_codes.append(c)
                    pkd_full_list.append({"kod": c, "opis": o, "typ": "pozostała"})
                    result["pkd_pozostale"].append(f"{c} - {o}")

        result["pkd_kody"] = pkd_codes
        result["pkd_pelne_lista"] = pkd_full_list

        # Sprawozdania finansowe (SF)
        wzmianki = d3.get("wzmiankiOZlozonychDokumentach", {})
        sf_list = wzmianki.get("wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego", [])

        if isinstance(sf_list, list) and sf_list:
            ostatnie_sf = sf_list[-1]
            if isinstance(ostatnie_sf, dict):
                data_zlozenia = ostatnie_sf.get("dataZlozenia", "Brak")
                okres_od_do = ostatnie_sf.get("zaOkresOdDo", "Brak")
                result["ostatnie_sf_data"] = str(data_zlozenia)
                result["ostatnie_sf_okres"] = str(okres_od_do)

                years_found = [int(y) for y in re.findall(r"\b(20[12]\d)\b", str(okres_od_do) + " " + str(data_zlozenia))]
                if years_found:
                    latest_year = max(years_found)
                    result["ostatnie_sf_rok"] = latest_year
                    current_year = datetime.now().year
                    if latest_year >= current_year - 2:
                        result["brak_aktualnych_sf"] = False
                    else:
                        result["brak_aktualnych_sf"] = True
                else:
                    result["brak_aktualnych_sf"] = False
        else:
            try:
                reg_dt = datetime.strptime(result["data_rejestracji"], "%d.%m.%Y")
                days_active = (datetime.now() - reg_dt).days
                if days_active < 540:
                    result["brak_aktualnych_sf"] = False
                    result["ostatnie_sf_okres"] = "Firma nowa (< 18 mies., brak wymogu SF)"
                else:
                    result["brak_aktualnych_sf"] = True
            except Exception:
                result["brak_aktualnych_sf"] = True

        # 5. Dział 4 – Zaległości, wierzytelności, egzekucje
        d4 = dane.get("dzial4", {})
        has_zaleglosci = False
        if isinstance(d4, dict) and d4:
            for k, v in d4.items():
                if v and (isinstance(v, list) and len(v) > 0 or isinstance(v, dict) and len(v) > 0):
                    has_zaleglosci = True
                    break
        result["zaleglosci_krs"] = "TAK" if has_zaleglosci else "NIE"

        # 6. Dział 6 – Status prawny (Likwidacja, Upadłość, Restrukturyzacja, Zawieszenie, Wykreślenie)
        d6 = dane.get("dzial6", {})
        status_krs = "AKTYWNA"

        if is_pelny:
            wpisy = naglowek.get("wpis", [])
            for wp in wpisy:
                opis = str(wp.get("opis", "")).upper()
                if "WYKREŚLENIE" in opis or "WYKRESLENIE" in opis:
                    status_krs = "WYKREŚLONA"
                    break
            if status_krs != "WYKREŚLONA" and naglowek.get("stanPozycji") == 2:
                status_krs = "WYKREŚLONA"

        if status_krs == "AKTYWNA" and d6.get("likwidacja"):
            status_krs = "W LIKWIDACJI"

        if status_krs == "AKTYWNA" and (d6.get("postepowanieUpadlosciowe") or d6.get("upadlosc")):
            status_krs = "UPADŁOŚĆ"

        if status_krs == "AKTYWNA" and (d6.get("postepowanieRestrukturyzacyjne") or d6.get("postepowanieUkladowe")):
            status_krs = "RESTRUKTURYZACJA"

        if status_krs == "AKTYWNA" and d6.get("zawieszenieWznowienieDzialalnosci"):
            zaw_list = d6.get("zawieszenieWznowienieDzialalnosci", [])
            if isinstance(zaw_list, list) and zaw_list:
                ostatni_wpis = zaw_list[-1]
                if isinstance(ostatni_wpis, dict) and ostatni_wpis.get("dataZawieszenia") and not ostatni_wpis.get("dataWznowienia"):
                    status_krs = "ZAWIESZONA"

        if status_krs == "AKTYWNA" and d6.get("rozwiazanieUniewaznienie"):
            status_krs = "W LIKWIDACJI"

        result["status_krs"] = status_krs

    except Exception as e:
        logger.error(f"Wyjątek podczas weryfikacji KRS {krs}: {e}", exc_info=True)
        result["status_krs"] = "BŁĄD POBIERANIA"
        result["krs_error"] = str(e)

    return result


# ==============================================================================
# MODUŁ B: BIAŁA LISTA PODATNIKÓW VAT (API MINISTERSTWA FINANSÓW)
# ==============================================================================

def verify_vat_whitelist(nip: str) -> dict:
    """
    Sprawdza status VAT oraz rachunki bankowe na Białej Liście Podatników VAT (API MF).
    Endpoint: https://wl-api.mf.gov.pl/api/search/nip/{nip}?date={dzisiejsza_data}
    """
    clean_n = clean_nip(nip)
    result = {
        "nip": clean_n,
        "status_vat": "Brak danych",
        "liczba_rachunkow": 0,
        "rachunki": [],
        "rachunki_str": "Brak rachunków",
        "brak_rachunkow": True,
        "adres_vat": "",
        "vat_error": None
    }

    if not clean_n or len(clean_n) != 10:
        result["vat_error"] = f"Nieprawidłowy numer NIP: {nip}"
        result["status_vat"] = "Niepoprawny NIP"
        return result

    today_str = date.today().strftime("%Y-%m-%d")
    url = f"https://wl-api.mf.gov.pl/api/search/nip/{clean_n}?date={today_str}"

    try:
        resp = GLOBAL_SESSION.get(url, timeout=10)
        if resp.status_code == 200:
            payload = resp.json().get("result", {})
            subject = payload.get("subject")
            if subject:
                status_vat = subject.get("statusVat") or "Brak w wykazie"
                result["status_vat"] = status_vat
                result["adres_vat"] = subject.get("residenceAddress") or subject.get("workingAddress") or ""
                accounts = subject.get("accountNumbers") or []
                result["rachunki"] = accounts
                result["liczba_rachunkow"] = len(accounts)
                result["brak_rachunkow"] = len(accounts) == 0

                if len(accounts) > 0:
                    sample = accounts[0]
                    if len(accounts) > 1:
                        result["rachunki_str"] = f"{sample} (i {len(accounts) - 1} innych)"
                    else:
                        result["rachunki_str"] = sample
                else:
                    result["rachunki_str"] = "Brak rachunków na Białej Liście"
            else:
                result["status_vat"] = "Niezarejestrowany / Brak podmiotu"
        elif resp.status_code == 404:
            result["status_vat"] = "Brak w wykazie VAT"
        else:
            result["status_vat"] = "BŁĄD SPRAWDZENIA VAT"
            result["vat_error"] = f"Kod odpowiedzi API VAT: {resp.status_code}"

    except Exception as e:
        logger.error(f"Wyjątek podczas sprawdzania Białej Listy VAT NIP {clean_n}: {e}")
        result["status_vat"] = "BŁĄD POŁĄCZENIA"
        result["vat_error"] = str(e)

    return result


# ==============================================================================
# MODUŁ 1: TWARDE WYKLUCZENIA (B2B ENERGIA ELEKTRYCZNA)
# ==============================================================================

PUBLIC_KEYWORDS = [
    "skarb państwa", "gmina", "miasto", "powiat", "województwo", "komunaln",
    "wodociągi", "zakład gospodarki", "szpital", "uniwersytet", "instytut",
    "samorząd", "miejskie przedsiębiorstwo", "zarząd dróg"
]

ENERGY_GROUPS = [
    "tauron", "pge", "enea", "energa", "orlen", "polenergia", "innogy", "e.on", "veolia", "fortum"
]

def check_hard_exclusions(krs_data: dict, vat_data: dict) -> Tuple[bool, str]:
    """
    Sprawdza kryteria twardego odrzucenia (0 pkt) dedykowane sprzedaży energii B2B:
    1. Status KRS inny niż AKTYWNA lub brak czynnego VAT
    2. Spółki Akcyjne (S.A.)
    3. Podmioty państwowe / komunalne / PZP
    4. Branża energetyczna (PKD 35.1) lub powiązania ze znanymi grupami energetycznymi
    Zwraca (is_rejected: bool, reason: str).
    """
    status_krs = str(krs_data.get("status_krs", "")).upper()
    status_vat = str(vat_data.get("status_vat", "")).strip()

    # 1. Status prawny i VAT
    if status_krs in ("W LIKWIDACJI", "UPADŁOŚĆ", "ZAWIESZONA", "WYKREŚLONA"):
        return True, f"Status KRS: {status_krs}"

    if status_krs == "BŁĄD POBIERANIA":
        return True, "Błąd pobrania danych KRS / brak podmiotu"

    if status_vat.lower() != "czynny":
        return True, f"Brak czynnego statusu VAT ({status_vat})"

    # 2. Spółki Akcyjne (S.A.)
    forma = str(krs_data.get("forma_prawna", "")).upper()
    nazwa = str(krs_data.get("nazwa", "")).upper()

    if "SPÓŁKA AKCYJNA" in forma or "SPOLKA AKCYJNA" in forma:
        return True, "Spółka Akcyjna (S.A.)"
    if re.search(r"\bS\.A\.\b", nazwa) or re.search(r"\bSPÓŁKA\s+AKCYJNA\b", nazwa):
        return True, "Spółka Akcyjna (S.A.)"

    # 3. Podmioty państwowe / komunalne / PZP
    full_text_to_check = f"{nazwa} {krs_data.get('wspolnicy_str', '')}".lower()
    for kw in PUBLIC_KEYWORDS:
        if kw in full_text_to_check:
            return True, f"Podmiot publiczny / PZP (wykryto: {kw})"

    # 4. Branża energetyczna i powiązania z grupami energetycznymi
    pkd_codes = krs_data.get("pkd_kody", [])
    for code in pkd_codes:
        c_clean = str(code).strip()
        if c_clean.startswith("35.1"):
            return True, f"Konkurencja / Branża energetyczna (PKD {c_clean})"

    wspolnicy_lower = str(krs_data.get("wspolnicy_str", "")).lower()
    for eg in ENERGY_GROUPS:
        if re.search(rf"\b{eg}\b", wspolnicy_lower) or eg in wspolnicy_lower:
            return True, f"Powiązanie z grupą energetyczną (wykryto: {eg.upper()})"

    return False, ""


# ==============================================================================
# MODUŁ C: REJESTR BDO
# ==============================================================================

def verify_bdo(nip: str) -> dict:
    """Weryfikuje obecność podmiotu w rejestrze BDO (https://rejestr-bdo.mos.gov.pl/)."""
    clean_n = clean_nip(nip)
    result = {
        "nip": clean_n,
        "bdo_status": "NIE",
        "bdo_numer": "",
        "bdo_zakres": "",
        "bdo_info": "Brak wpisu lub brak danych",
        "bdo_error": None
    }

    if not clean_n or len(clean_n) != 10:
        result["bdo_info"] = "Niepoprawny NIP"
        return result

    url_search = "https://rejestr-bdo.mos.gov.pl/Dictionary/Application/Search"
    payload = {"queryString": clean_n, "placeType": "residenceOrBusinessAddress"}
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://rejestr-bdo.mos.gov.pl/Registry/Index",
        "Origin": "https://rejestr-bdo.mos.gov.pl",
        "X-Requested-With": "XMLHttpRequest"
    }

    try:
        resp = GLOBAL_SESSION.post(url_search, json=payload, headers=headers, timeout=6)
        if resp.status_code == 200:
            try:
                data = resp.json()
                items = data.get("items") or []
                if items:
                    first = items[0]
                    reg_num = first.get("registrationNumber") or first.get("bdoNumber") or ""
                    scope = first.get("activityScope") or first.get("activityScopeDescription") or ""
                    result["bdo_status"] = "TAK"
                    result["bdo_numer"] = str(reg_num).strip()
                    result["bdo_zakres"] = str(scope).strip()
                    result["bdo_info"] = f"Numer BDO: {reg_num}" if reg_num else "Zarejestrowany w BDO"
                    return result
                elif data.get("totalResultNumber", 0) > 0:
                    result["bdo_status"] = "TAK"
                    result["bdo_info"] = "Zarejestrowany w BDO"
                    return result
                else:
                    result["bdo_status"] = "NIE"
                    result["bdo_info"] = "Brak wpisu w rejestrze BDO"
            except Exception:
                if clean_n in resp.text:
                    result["bdo_status"] = "TAK"
                    result["bdo_info"] = "Obecny w rejestrze BDO"
                else:
                    result["bdo_status"] = "NIE"
                    result["bdo_info"] = "Brak wpisu"
        else:
            result["bdo_status"] = "NIE"
            result["bdo_info"] = f"Rejestr BDO niedostępny (status: {resp.status_code})"
    except Exception as e:
        logger.warning(f"BDO check failed gracefully for NIP {clean_n}: {e}")
        result["bdo_status"] = "NIE"
        result["bdo_info"] = "Błąd połączenia z BDO"
        result["bdo_error"] = str(e)

    return result


# ==============================================================================
# MODUŁ D: WERYFIKACJA WWW, PARK MASZYNOWY, OZE, KONTAKT I DEEP RESEARCH
# ==============================================================================

AGGREGATOR_DOMAINS = {
    "aleo.com", "rejestr.io", "krs-online.com.pl", "wyszukiwarkakrs.pl", "gowork.pl",
    "panoramafirm.pl", "bison.pl", "owler.com", "infoveriti.pl", "pkt.pl",
    "cylex-polska.pl", "money.pl", "biznes.gov.pl", "ceidg.gov.pl", "dnb.com",
    "linkedin.com", "facebook.com", "instagram.com", "youtube.com", "wikipedia.org",
    "twitter.com", "x.com", "zoominfo.com", "goldenline.pl", "bankier.pl",
    "msp.money.pl", "finanse.wp.pl", "wprost.pl", "wyborcza.pl", "onet.pl",
    "interia.pl", "pracuj.pl", "olx.pl", "allegro.pl", "google.com", "google.pl",
    "maps.google.com", "gov.pl", "sejm.gov.pl", "bip.gov.pl"
}

def is_aggregator(url: str) -> bool:
    """Sprawdza, czy podany URL należy do katalogu firm, rejestru lub mediów społecznościowych."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        for agg in AGGREGATOR_DOMAINS:
            if netloc == agg or netloc.endswith("." + agg):
                return True
        return False
    except Exception:
        return False

def clean_company_url(raw_url: str) -> str:
    """Czyści i normalizuje adres strony firmowej."""
    if not raw_url:
        return ""
    url = raw_url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return raw_url

def extract_base_domain(url: str) -> str:
    """Wyciąga czystą domenę (np. 'firma.pl' z 'https://www.firma.pl/kontakt')."""
    if not url:
        return ""
    try:
        parsed = urlparse(clean_company_url(url))
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""

def verify_web_presence(nazwa: str, miasto: str = "", nip: str = "", krs_www: str = "") -> dict:
    """Szuka domeny firmowej oraz sprawdza wizytówkę Google Maps."""
    result = {
        "strona_www": "",
        "wizytowka_google": "NIE",
        "zrodlo_www": "Brak",
        "search_query": ""
    }

    if krs_www and not is_aggregator(krs_www):
        result["strona_www"] = clean_company_url(krs_www)
        result["zrodlo_www"] = "Oficjalny KRS"

    nazwa_short = shorten_company_name(nazwa)
    query_parts = []
    if nazwa_short:
        query_parts.append(f'"{nazwa_short}"')
    if miasto:
        query_parts.append(miasto)
    if nip:
        query_parts.append(nip)

    query = " ".join(query_parts) if query_parts else nazwa
    result["search_query"] = query

    try:
        time.sleep(random.uniform(0.6, 1.0))
        from duckduckgo_search import DDGS
        ddgs = DDGS()
        search_results = list(ddgs.text(query, max_results=6))

        if not search_results and nip and nazwa_short:
            fallback_query = f'"{nazwa_short}" {miasto}'.strip()
            result["search_query"] = fallback_query
            search_results = list(ddgs.text(fallback_query, max_results=6))

        for item in search_results:
            href = item.get("href", "")
            title = item.get("title", "").lower()
            body = item.get("body", "").lower()

            if any(m in href.lower() for m in ["google.com/maps", "maps.google", "goo.gl/maps"]):
                result["wizytowka_google"] = "TAK"
            elif "google maps" in title or "wizytówka google" in body:
                result["wizytowka_google"] = "TAK"

            if not result["strona_www"]:
                if href and not is_aggregator(href):
                    result["strona_www"] = clean_company_url(href)
                    result["zrodlo_www"] = "Wyszukiwarka"

        if result["wizytowka_google"] == "NIE" and nazwa_short:
            maps_query = f'"{nazwa_short}" {miasto} google maps'
            maps_results = list(ddgs.text(maps_query, max_results=3))
            for item in maps_results:
                href = item.get("href", "").lower()
                if any(m in href for m in ["google.com/maps", "maps.google", "goo.gl/maps"]):
                    result["wizytowka_google"] = "TAK"
                    break

    except Exception as e:
        logger.warning(f"DuckDuckGo search error for '{query}': {e}")

    return result


# Słowa kluczowe parku maszynowego i OZE
MACHINERY_KEYWORDS = [
    "wtryskark", "laser", "cnc", "frezark", "tokark", "piec", "chłodni", "mroźni",
    "kompresor", "sprężark", "linia produkcyjna", "hala produkcyjna", "stacja trafo",
    "transformator", "moc umowna", "zmianow", "tłoczn", "odlewn", "spawaln", "suszarni", "kocioł"
]

OZE_KEYWORDS = [
    "fotowoltaik", "instalacja pv", "farmy fotowoltaiczne", "oze",
    "zielona energia", "pompy ciepła", "kogeneracj"
]

PLANT_KEYWORDS = ["zakład", "produkcja", "magazyn", "baza", "oddział", "fabryka"]

CONTACT_SUBPAGE_PATTERNS = [
    "kontakt", "contact", "o-nas", "about", "oferta", "technologia",
    "park-maszynowy", "produkcja", "maszyny", "park_maszynowy"
]

def scrape_website_tech_and_contacts(base_url: str) -> dict:
    """
    Wykonuje lekki scraping strony głównej oraz podstron kontaktowych/o-nas/technologia:
    - wyodrębnia telefony i adresy e-mail (z wyróżnieniem maili imiennych)
    - wykrywa technologie i maszyny energochłonne oraz ocenia skalę
    - wykrywa posiadanie instalacji OZE / fotowoltaiki
    - wyłapuje fizyczny adres zakładu produkcyjnego ze strony kontaktu
    """
    res = {
        "telefony_www": "",
        "maile_www": "",
        "wykryte_technologie": "",
        "wykryte_technologie_lista": [],
        "skala_technologiczna": "BRAK DANYCH",
        "posiada_oze": "NIE",
        "adres_zakladu_www": ""
    }

    if not base_url or is_aggregator(base_url):
        return res

    clean_url = clean_company_url(base_url)
    pages_to_visit = [clean_url]
    visited_urls = set()
    collected_text = []

    try:
        # 1. Pobieramy stronę główną
        resp = GLOBAL_SESSION.get(clean_url, timeout=7)
        if resp.status_code == 200:
            visited_urls.add(clean_url)
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "noscript", "svg"]):
                tag.decompose()
            collected_text.append(soup.get_text(separator=" ", strip=True))

            # Szukamy podstron kontaktu, oferty, technologii
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                    continue
                if href.startswith("/"):
                    full_link = clean_url.rstrip("/") + href
                elif href.startswith("http"):
                    full_link = href
                else:
                    full_link = clean_url.rstrip("/") + "/" + href

                link_lower = full_link.lower()
                if urlparse(clean_url).netloc.lower() in link_lower:
                    if any(pat in link_lower for pat in CONTACT_SUBPAGE_PATTERNS):
                        if full_link not in visited_urls and len(pages_to_visit) < 5:
                            pages_to_visit.append(full_link)
                            visited_urls.add(full_link)

        # 2. Pobieramy do 3-4 wybranych podstron
        for page_url in pages_to_visit[1:5]:
            try:
                time.sleep(0.3)
                sub_resp = GLOBAL_SESSION.get(page_url, timeout=5)
                if sub_resp.status_code == 200:
                    sub_soup = BeautifulSoup(sub_resp.text, "html.parser")
                    for tag in sub_soup(["script", "style", "noscript", "svg"]):
                        tag.decompose()
                    text_sub = sub_soup.get_text(separator=" ", strip=True)
                    collected_text.append(text_sub)

                    # Sprawdźmy czy na stronie kontaktu nie ma fizycznego adresu zakładu
                    if "kontakt" in page_url.lower() or "contact" in page_url.lower():
                        for line in sub_soup.stripped_strings:
                            line_lower = line.lower()
                            if any(pk in line_lower for pk in PLANT_KEYWORDS) and re.search(r"\b\d{2}-\d{3}\b", line):
                                if not res["adres_zakladu_www"] and len(line) < 160:
                                    res["adres_zakladu_www"] = line.strip()
            except Exception:
                continue

    except Exception as e:
        logger.warning(f"Błąd scrapingu strony {base_url}: {e}")

    full_corpus = " ".join(collected_text)
    full_corpus_lower = full_corpus.lower()

    if not full_corpus:
        return res

    # 3. Wyodrębnianie telefonów (Polskie formaty)
    raw_phones = re.findall(
        r"(?:(?:\+|00)48[\s-]?)?(?:[1-9]\d{1}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}|[1-9]\d{2}[\s-]?\d{3}[\s-]?\d{3}|[1-9]\d{1}[\s-]?\d{3}[\s-]?\d{4})",
        full_corpus
    )
    cleaned_phones = []
    for p in raw_phones:
        clean_digits = re.sub(r"\D", "", p)
        if clean_digits.startswith("48") and len(clean_digits) == 11:
            clean_digits = clean_digits[2:]
        if len(clean_digits) == 9 and clean_digits not in [re.sub(r"\D", "", cp) for cp in cleaned_phones]:
            if clean_digits[:2] in ("22", "12", "61", "71", "58", "32", "42", "81", "85", "52", "91"):
                fmt = f"+48 {clean_digits[:2]} {clean_digits[2:5]} {clean_digits[5:7]} {clean_digits[7:]}"
            else:
                fmt = f"+48 {clean_digits[:3]} {clean_digits[3:6]} {clean_digits[6:]}"
            cleaned_phones.append(fmt)
    res["telefony_www"] = "; ".join(cleaned_phones[:4])

    # 4. Wyodrębnianie e-maili
    raw_emails = re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", full_corpus)
    filtered_emails = []
    ignored_prefixes = ("rodo@", "iod@", "prywatnosc@", "dpo@", "noreply@", "no-reply@", "newsletter@")
    ignored_ext = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".pdf", ".css", ".js")

    for em in raw_emails:
        em_lower = em.lower()
        if any(em_lower.startswith(pfx) for pfx in ignored_prefixes):
            continue
        if any(em_lower.endswith(ext) for ext in ignored_ext):
            continue
        if em_lower not in filtered_emails:
            filtered_emails.append(em_lower)

    filtered_emails.sort(key=lambda x: (0 if "." in x.split("@")[0] and not x.split("@")[0] in ("biuro", "kontakt", "info", "bok") else 1))
    res["maile_www"] = "; ".join(filtered_emails[:4])

    # 5. Wykrywanie technologii i maszyn
    detected_tech = []
    for kw in MACHINERY_KEYWORDS:
        if kw in full_corpus_lower:
            detected_tech.append(kw)
    res["wykryte_technologie_lista"] = detected_tech
    res["wykryte_technologie"] = ", ".join(detected_tech) if detected_tech else ""

    heavy_indicators = ["stacja trafo", "transformator", "linia produkcyjna", "hala produkcyjna", "odlewn"]
    if len(detected_tech) >= 3 or any(hi in detected_tech for hi in heavy_indicators):
        res["skala_technologiczna"] = "DUŻA / ZŁOŻONA"
    elif len(detected_tech) >= 1:
        res["skala_technologiczna"] = "ŚREDNIA"
    else:
        res["skala_technologiczna"] = "BRAK DANYCH"

    # 6. Wykrywanie OZE
    for oze_kw in OZE_KEYWORDS:
        if oze_kw in full_corpus_lower:
            res["posiada_oze"] = "TAK"
            break

    # 7. Fallback dla adresu zakładu
    if not res["adres_zakladu_www"]:
        match_addr = re.search(r"(\b(?:ul\.|al\.|plac|pl\.)?\s*[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż\s\.\d/-]+,\s*\d{2}-\d{3}\s+[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźż]+)", full_corpus)
        if match_addr and any(pk in full_corpus_lower for pk in PLANT_KEYWORDS):
            res["adres_zakladu_www"] = match_addr.group(1).strip()

    return res


def generate_suggested_ceo_email(ceo_str: str, website_url: str) -> str:
    """
    Generuje sugerowany adres e-mail decydenta (Prezesa/Zarządu) na podstawie domeny firmowej.
    np. 'j.kowalski@firma.pl / jan.kowalski@firma.pl'
    """
    domain = extract_base_domain(website_url)
    if not domain or not ceo_str:
        return ""

    first_person = ceo_str.split(";")[0].strip()
    match = re.match(r"^([A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż]+)\s+([A-ZĄĆĘŁŃÓŚŹŻa-ząćęłńóśźż-]+)", first_person)
    if not match:
        return ""

    imie = transliterate_pl(match.group(1)).lower()
    nazwisko = transliterate_pl(match.group(2)).lower()

    if not imie or not nazwisko:
        return ""

    first_letter = imie[0]
    return f"{first_letter}.{nazwisko}@{domain} / {imie}.{nazwisko}@{domain}"


# ==============================================================================
# MODUŁ 3: GŁĘBOKI WYWIAD W SIECI (LINKEDIN, REKRUTACJA, DOTACJE UE)
# Wyłącznie dla nieodrzuconych firm o profilu BARDZO WYSOKI!
# ==============================================================================

def search_linkedin_decision_makers(nazwa_skrocona: str) -> str:
    """Wyszukuje profile LinkedIn kluczowych decydentów i personelu technicznego."""
    if not nazwa_skrocona:
        return ""
    try:
        time.sleep(random.uniform(1.0, 1.8))
        from duckduckgo_search import DDGS
        ddgs = DDGS()
        query = f'"{nazwa_skrocona}" ("dyrektor" OR "kierownik" OR "energetyk" OR "utrzymania ruchu" OR "prezes" OR "prokurent") site:linkedin.com/in'
        results = list(ddgs.text(query, max_results=3))

        if not results:
            fallback = f'{nazwa_skrocona} dyrektor kierownik prezes linkedin.com/in'
            results = list(ddgs.text(fallback, max_results=3))

        links = []
        for r in results:
            href = r.get("href", "")
            title = r.get("title", "")
            if "linkedin.com/in" in href and href not in links:
                links.append(f"{title} ({href})")
        return "; ".join(links[:2]) if links else "Brak bezpośrednich profili"
    except Exception as e:
        logger.warning(f"Błąd wyszukiwania LinkedIn dla '{nazwa_skrocona}': {e}")
        return "Brak danych (limit wyszukiwarki)"


def search_recruitment_signals(nazwa_skrocona: str) -> str:
    """Wyszukuje ślad rekrutacyjny wskazujący na pracę zmianową, utrzymanie ruchu lub energetyka."""
    if not nazwa_skrocona:
        return ""
    try:
        time.sleep(random.uniform(1.0, 1.8))
        from duckduckgo_search import DDGS
        ddgs = DDGS()
        query = f'"{nazwa_skrocona}" ("3-zmianowa" OR "trzyzmianowa" OR "ruch ciągły" OR "utrzymania ruchu" OR "energetyk" OR "operator" OR "produkcja") (site:olx.pl OR site:pracuj.pl OR site:indeed.com)'
        results = list(ddgs.text(query, max_results=4))

        if not results:
            fallback = f'{nazwa_skrocona} praca produkcja zmiana operator'
            results = list(ddgs.text(fallback, max_results=3))

        matched_signals = []
        target_words = ["3-zmianow", "trzyzmianow", "ruch ciągły", "ruchu ciągł", "utrzymania ruchu", "energetyk", "operator maszyn", "praca na produkcji"]
        for r in results:
            content = (r.get("title", "") + " " + r.get("body", "")).lower()
            for tw in target_words:
                if tw in content and tw not in matched_signals:
                    matched_signals.append(tw)

        return ", ".join(matched_signals) if matched_signals else "Brak wykrytych sygnałów"
    except Exception as e:
        logger.warning(f"Błąd wyszukiwania rekrutacji dla '{nazwa_skrocona}': {e}")
        return "Brak danych (limit wyszukiwarki)"


def search_eu_subsidies(nip: str, nazwa_skrocona: str) -> str:
    """Weryfikuje realizację dotacji UE na linie produkcyjne, hale lub efektywność energetyczną."""
    if not nazwa_skrocona and not nip:
        return "NIE"
    try:
        time.sleep(random.uniform(1.0, 1.8))
        from duckduckgo_search import DDGS
        ddgs = DDGS()
        query = f'"{nip or nazwa_skrocona}" ("dotacja" OR "linia produkcyjna" OR "efektywność energetyczna" OR "fotowoltaiczna" OR "termomodernizacja" OR "hala") (site:mapadotacji.gov.pl OR site:bazakonkurencyjnosci.funduszeeuropejskie.gov.pl)'
        results = list(ddgs.text(query, max_results=3))

        if not results and nazwa_skrocona:
            fallback = f'{nazwa_skrocona} dotacja unijna fundusze europejskie'
            results = list(ddgs.text(fallback, max_results=3))

        for r in results:
            title = r.get("title", "").strip()
            href = r.get("href", "")
            if any(dom in href for dom in ["mapadotacji.gov.pl", "funduszeeuropejskie.gov.pl", "bazakonkurencyjnosci"]):
                clean_t = re.sub(r"\s*-\s*Mapa Dotacji.*", "", title)
                clean_t = re.sub(r"\s*-\s*Baza Konkurencyjności.*", "", clean_t)
                return f"TAK ({clean_t[:70]}...)" if len(clean_t) > 70 else f"TAK ({clean_t})"

        return "NIE"
    except Exception as e:
        logger.warning(f"Błąd wyszukiwania dotacji dla '{nazwa_skrocona}': {e}")
        return "NIE"
