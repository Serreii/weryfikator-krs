"""
Moduł analizy ryzyka, profilera energochłonności, estymacji taryf energetycznych
oraz silnik punktacji Lead Score (0–100 pkt) dedykowany dla sprzedaży energii B2B.
"""

import re
import unicodedata
from typing import Dict, List, Tuple, Any

# ==============================================================================
# SŁOWNIK BRANŻ ENERGOCHŁONNYCH (PROFILE ENERGETYCZNE)
# ==============================================================================

# Kody PKD – Bardzo wysoka energochłonność (Przemysł / przetwórstwo / chłodnictwo)
PKD_BARDZO_WYSOKI = [
    # Tworzywa sztuczne i guma (22.2)
    "22.21", "22.22", "22.23", "22.29", "22.11", "22.19", "22.2", "22.",
    # Obróbka metali / CNC / konstrukcje stalowe / kucie (25)
    "25.11", "25.12", "25.21", "25.50", "25.61", "25.62", "25.71", "25.72", "25.73", "25.91", "25.92", "25.93", "25.94", "25.99", "25.",
    # Hutnictwo, produkcja metali i odlewnictwo (24)
    "24.10", "24.20", "24.31", "24.32", "24.33", "24.34", "24.41", "24.42", "24.43", "24.44", "24.45", "24.51", "24.52", "24.53", "24.54", "24.",
    # Przemysł spożywczy / mięsny / mleczarski / piekarnie (10)
    "10.11", "10.12", "10.13", "10.20", "10.31", "10.32", "10.39", "10.41", "10.42", "10.51", "10.52", "10.61", "10.62", "10.71", "10.72", "10.73", "10.81", "10.82", "10.83", "10.84", "10.85", "10.86", "10.89", "10.91", "10.92", "10.",
    # Produkcja napojów (11)
    "11.01", "11.02", "11.03", "11.04", "11.05", "11.06", "11.07", "11.",
    # Przemysł drzewny / tartaki (16)
    "16.10", "16.21", "16.22", "16.23", "16.24", "16.29", "16.",
    # Przemysł mineralny / materiały budowlane / ceramika / szkło / beton (23)
    "23.11", "23.12", "23.13", "23.14", "23.19", "23.20", "23.31", "23.32", "23.41", "23.42", "23.43", "23.44", "23.49", "23.51", "23.52", "23.61", "23.62", "23.63", "23.64", "23.65", "23.69", "23.70", "23.91", "23.99", "23.",
    # Magazynowanie i przechowywanie (chłodnie) (52.10)
    "52.10", "52.10.A", "52.10.B",
    # Chemia i farmacja (20, 21)
    "20.11", "20.12", "20.13", "20.14", "20.15", "20.16", "20.20", "20.30", "20.41", "20.42", "20.51", "20.52", "20.53", "20.59", "20.",
    "21.10", "21.20", "21.",
    # Produkcja maszyn i urządzeń (28)
    "28.11", "28.12", "28.13", "28.14", "28.15", "28.21", "28.22", "28.23", "28.24", "28.25", "28.29", "28.30", "28.41", "28.49", "28.91", "28.92", "28.93", "28.94", "28.95", "28.96", "28.99", "28.",
    # Automotive / pojazdy (29)
    "29.10", "29.20", "29.31", "29.32", "29."
]

# Kody PKD – Średnia / Wysoka energochłonność (Hotele, szpitale/przychodnie, sport, szklarnie, handel wielkopowierzchniowy)
PKD_WYSOKI_SREDNI = [
    # Hotele i zakwaterowanie (55.10)
    "55.10", "55.10.Z", "55.",
    # Opieka zdrowotna / szpitale prywatne / przychodnie (86)
    "86.10", "86.10.Z", "86.21", "86.22", "86.90", "86.",
    # Obiekty sportowe, baseny, siłownie (93.11, 93.13)
    "93.11", "93.11.Z", "93.13", "93.13.Z",
    # Rolnictwo szklarniowe / uprawy (01)
    "01.11", "01.12", "01.13", "01.19", "01.21", "01.24", "01.25", "01.28", "01.30", "01.41", "01.42", "01.46", "01.47", "01.",
    # Handel hurtowy i wielkopowierzchniowy (46, 47.11)
    "46.21", "46.31", "46.32", "46.33", "46.34", "46.35", "46.36", "46.37", "46.38", "46.39", "46.71", "46.72", "46.73", "46.74", "46.75", "46.76", "46.77", "46.90", "46.",
    "47.11", "47.11.Z"
]

def classify_energy_profile(pkd_codes: List[str]) -> Tuple[str, List[str]]:
    """
    Przypisuje firmie profil energochłonności na podstawie pełnej listy kodów PKD z KRS:
    - BARDZO WYSOKI (Przemysł / przetwórstwo / chłodnictwo)
    - WYSOKI / ŚREDNI (Hotele, handel wielkopowierzchniowy, obiekty całodobowe)
    - NISKI (Biura / usługi / handel bez produkcji)
    Zwraca (profil_energetyczny, dopasowane_kody_pkd).
    """
    if not pkd_codes:
        return "NISKI (Biura / usługi / handel bez produkcji)", []

    matched_very_high = []
    matched_high_mid = []

    for raw_code in pkd_codes:
        code = str(raw_code).strip().upper()
        # Sprawdzamy bardzo wysoki
        for b_pkd in PKD_BARDZO_WYSOKI:
            if code.startswith(b_pkd.replace(".", "")) or code.startswith(b_pkd):
                if code not in matched_very_high:
                    matched_very_high.append(code)
                break

        # Sprawdzamy wysoki / średni
        for s_pkd in PKD_WYSOKI_SREDNI:
            if code.startswith(s_pkd.replace(".", "")) or code.startswith(s_pkd):
                if code not in matched_high_mid:
                    matched_high_mid.append(code)
                break

    if matched_very_high:
        return "BARDZO WYSOKI (Przemysł / przetwórstwo / chłodnictwo)", matched_very_high
    elif matched_high_mid:
        return "WYSOKI / ŚREDNI (Hotele, handel wielkopowierzchniowy, obiekty całodobowe)", matched_high_mid
    else:
        return "NISKI (Biura / usługi / handel bez produkcji)", []


def estimate_energy_tariff(
    profil_energetyczny: str,
    wykryte_technologie: List[str],
    sygnaly_rekrutacyjne: str,
    kapital_wartosc: float,
    bdo_status: str,
    extra_text: str = ""
) -> str:
    """
    Estymuje grupę taryfową energii elektrycznej:
    - B2x (Średnie napięcie / Duży wolumen) – trafo, odlewnia, ruch ciągły, 3-zmianowa lub kapitał > 500k zł przy przemyśle
    - C2x (Niskie napięcie >40 kW / Przemysł MŚP) – profil bardzo wysoki, BDO lub maszyny produkcyjne
    - C1x (Mały obiekt / Biuro / Usługi) – pozostałe podmioty
    """
    combined_signals = (
        " ".join(wykryte_technologie) + " " +
        str(sygnaly_rekrutacyjne) + " " +
        str(extra_text)
    ).lower()

    b2x_heavy_triggers = [
        "stacja trafo", "transformator", "odlewn", "odlewni", "ruch ciągły",
        "ruchu ciągł", "3-zmianow", "trzyzmianow"
    ]

    is_very_high_profile = "BARDZO WYSOKI" in profil_energetyczny

    # 1. Warunek B2x (Średnie Napięcie)
    if any(trig in combined_signals for trig in b2x_heavy_triggers):
        return "B2x (Średnie napięcie / Duży wolumen)"

    if is_very_high_profile and kapital_wartosc >= 500000:
        return "B2x (Średnie napięcie / Duży wolumen)"

    # 2. Warunek C2x (Niskie Napięcie >40 kW)
    if is_very_high_profile or bdo_status == "TAK" or len(wykryte_technologie) > 0:
        return "C2x (Niskie napięcie >40 kW / Przemysł MŚP)"

    # 3. Domyślnie C1x
    return "C1x (Mały obiekt / Biuro / Usługi)"


# ==============================================================================
# BAZA ZNANYCH ADRESÓW WIRTUALNYCH BIUR W POLSCE
# ==============================================================================

KNOWN_VIRTUAL_OFFICES = [
    # Warszawa
    {"miasto": "WARSZAWA", "ulica": "ALEJE JEROZOLIMSKIE", "nr": "85"},
    {"miasto": "WARSZAWA", "ulica": "ALEJE JEROZOLIMSKIE", "nr": "81"},
    {"miasto": "WARSZAWA", "ulica": "ALEJE JEROZOLIMSKIE", "nr": "65"},
    {"miasto": "WARSZAWA", "ulica": "ALEJE JEROZOLIMSKIE", "nr": "96"},
    {"miasto": "WARSZAWA", "ulica": "ALEJE JEROZOLIMSKIE", "nr": "123"},
    {"miasto": "WARSZAWA", "ulica": "ZŁOTA", "nr": "75A"},
    {"miasto": "WARSZAWA", "ulica": "ZŁOTA", "nr": "59"},
    {"miasto": "WARSZAWA", "ulica": "ZŁOTA", "nr": "61"},
    {"miasto": "WARSZAWA", "ulica": "HOŻA", "nr": "86"},
    {"miasto": "WARSZAWA", "ulica": "HOŻA", "nr": "50"},
    {"miasto": "WARSZAWA", "ulica": "TWARDA", "nr": "18"},
    {"miasto": "WARSZAWA", "ulica": "TWARDA", "nr": "4"},
    {"miasto": "WARSZAWA", "ulica": "CHMIELNA", "nr": "2"},
    {"miasto": "WARSZAWA", "ulica": "CHMIELNA", "nr": "73"},
    {"miasto": "WARSZAWA", "ulica": "MARSZAŁKOWSKA", "nr": "58"},
    {"miasto": "WARSZAWA", "ulica": "MARSZAŁKOWSKA", "nr": "84"},
    {"miasto": "WARSZAWA", "ulica": "MARSZAŁKOWSKA", "nr": "107"},
    {"miasto": "WARSZAWA", "ulica": "MARSZAŁKOWSKA", "nr": "111"},
    {"miasto": "WARSZAWA", "ulica": "MARSZAŁKOWSKA", "nr": "115"},
    {"miasto": "WARSZAWA", "ulica": "NOWOGRODZKA", "nr": "31"},
    {"miasto": "WARSZAWA", "ulica": "NOWOGRODZKA", "nr": "50"},
    {"miasto": "WARSZAWA", "ulica": "NOWOGRODZKA", "nr": "56"},
    {"miasto": "WARSZAWA", "ulica": "GRZYBOWSKA", "nr": "87"},
    {"miasto": "WARSZAWA", "ulica": "GRZYBOWSKA", "nr": "80"},
    {"miasto": "WARSZAWA", "ulica": "GRZYBOWSKA", "nr": "2"},
    {"miasto": "WARSZAWA", "ulica": "EMILII PLATER", "nr": "53"},
    {"miasto": "WARSZAWA", "ulica": "JANA PAWŁA II", "nr": "22"},
    {"miasto": "WARSZAWA", "ulica": "JANA PAWŁA II", "nr": "27"},
    {"miasto": "WARSZAWA", "ulica": "JANA PAWŁA II", "nr": "80"},
    {"miasto": "WARSZAWA", "ulica": "KOSZYKOWA", "nr": "61"},
    {"miasto": "WARSZAWA", "ulica": "KOSZYKOWA", "nr": "54"},
    {"miasto": "WARSZAWA", "ulica": "WSPÓLNA", "nr": "41"},
    {"miasto": "WARSZAWA", "ulica": "WSPÓLNA", "nr": "35"},
    {"miasto": "WARSZAWA", "ulica": "ŻURAWIA", "nr": "6"},
    {"miasto": "WARSZAWA", "ulica": "ŻURAWIA", "nr": "45"},
    {"miasto": "WARSZAWA", "ulica": "SIENNA", "nr": "75"},
    {"miasto": "WARSZAWA", "ulica": "SIENNA", "nr": "72"},
    {"miasto": "WARSZAWA", "ulica": "DOMANIEWSKA", "nr": "37"},
    {"miasto": "WARSZAWA", "ulica": "DOMANIEWSKA", "nr": "47"},
    {"miasto": "WARSZAWA", "ulica": "POSTĘPU", "nr": "14"},
    {"miasto": "WARSZAWA", "ulica": "SOLEC", "nr": "18"},
    {"miasto": "WARSZAWA", "ulica": "PUŁAWSKA", "nr": "12"},
    {"miasto": "WARSZAWA", "ulica": "PUŁAWSKA", "nr": "111"},
    {"miasto": "WARSZAWA", "ulica": "PUŁAWSKA", "nr": "2"},
    {"miasto": "WARSZAWA", "ulica": "MAZOWIECKA", "nr": "9"},
    {"miasto": "WARSZAWA", "ulica": "JASNA", "nr": "1"},
    {"miasto": "WARSZAWA", "ulica": "PIĘKNA", "nr": "24"},
    {"miasto": "WARSZAWA", "ulica": "WILCZA", "nr": "50"},
    {"miasto": "WARSZAWA", "ulica": "BRACKA", "nr": "20"},
    {"miasto": "WARSZAWA", "ulica": "ŚWIĘTOKRZYSKA", "nr": "30"},
    {"miasto": "WARSZAWA", "ulica": "PROSTA", "nr": "68"},
    {"miasto": "WARSZAWA", "ulica": "PROSTA", "nr": "70"},
    {"miasto": "WARSZAWA", "ulica": "MANGALIA", "nr": "4"},
    {"miasto": "WARSZAWA", "ulica": "CYBERNETYKI", "nr": "19B"},

    # Kraków
    {"miasto": "KRAKÓW", "ulica": "FLORIAŃSKA", "nr": "15"},
    {"miasto": "KRAKÓW", "ulica": "FLORIAŃSKA", "nr": "28"},
    {"miasto": "KRAKÓW", "ulica": "WIELOPOLE", "nr": "18"},
    {"miasto": "KRAKÓW", "ulica": "LUBICZ", "nr": "3"},
    {"miasto": "KRAKÓW", "ulica": "SZLAK", "nr": "77"},
    {"miasto": "KRAKÓW", "ulica": "SZLAK", "nr": "65"},
    {"miasto": "KRAKÓW", "ulica": "RYNEK GŁÓWNY", "nr": "28"},
    {"miasto": "KRAKÓW", "ulica": "MOGILSKA", "nr": "43"},
    {"miasto": "KRAKÓW", "ulica": "GRZEGÓRZECKA", "nr": "67"},
    {"miasto": "KRAKÓW", "ulica": "KARMELICKA", "nr": "9"},
    {"miasto": "KRAKÓW", "ulica": "DIETLA", "nr": "64"},

    # Wrocław
    {"miasto": "WROCŁAW", "ulica": "RYNEK", "nr": "7"},
    {"miasto": "WROCŁAW", "ulica": "PIŁSUDSKIEGO", "nr": "74"},
    {"miasto": "WROCŁAW", "ulica": "RUSKA", "nr": "51"},
    {"miasto": "WROCŁAW", "ulica": "ŚWIDNICKA", "nr": "39"},
    {"miasto": "WROCŁAW", "ulica": "LEGNICKA", "nr": "52"},
    {"miasto": "WROCŁAW", "ulica": "PLAC SOLNY", "nr": "14"},
    {"miasto": "WROCŁAW", "ulica": "KAZIMIERZA WIELKIEGO", "nr": "27"},

    # Poznań
    {"miasto": "POZNAŃ", "ulica": "ŚWIĘTY MARCIN", "nr": "29"},
    {"miasto": "POZNAŃ", "ulica": "ŚW. MARCIN", "nr": "29"},
    {"miasto": "POZNAŃ", "ulica": "PÓŁWIEJSKA", "nr": "17"},
    {"miasto": "POZNAŃ", "ulica": "ROOSEVELTA", "nr": "22"},
    {"miasto": "POZNAŃ", "ulica": "TOWAROWA", "nr": "35"},
    {"miasto": "POZNAŃ", "ulica": "GARBARY", "nr": "95"},

    # Trójmiasto (Gdańsk / Gdynia / Sopot)
    {"miasto": "GDAŃSK", "ulica": "GRUNWALDZKA", "nr": "472"},
    {"miasto": "GDAŃSK", "ulica": "NORWIDA", "nr": "4"},
    {"miasto": "GDAŃSK", "ulica": "WAŁY PIASTOWSKIE", "nr": "1"},
    {"miasto": "GDYNIA", "ulica": "ŚWIĘTOJAŃSKA", "nr": "43"},

    # Katowice, Łódź
    {"miasto": "KATOWICE", "ulica": "SOKOLSKA", "nr": "30"},
    {"miasto": "KATOWICE", "ulica": "KORFANTEGO", "nr": "2"},
    {"miasto": "ŁÓDŹ", "ulica": "PIOTRKOWSKA", "nr": "173"},
    {"miasto": "ŁÓDŹ", "ulica": "PIOTRKOWSKA", "nr": "60"},
]

VIRTUAL_OFFICE_KEYWORDS = [
    r"\bbiuro\s+wirtualne\b",
    r"\bwirtualne\s+biuro\b",
    r"\bcoworking\b",
    r"\bcowork\b",
    r"\bwirtualny\s+adres\b",
    r"\badres\s+dla\s+firm\b",
    r"\badres\s+rejestrowy\b",
    r"\badres\s+korespondencyjny\b",
    r"\bserviced\s+office\b",
    r"\bready\s+offices\b",
    r"\bsmart\s+office\b",
    r"\bbusiness\s+cent(?:er|re)\b",
    r"\bvirtual\s+office\b",
    r"\bsecretariat\b",
    r"\bsekretariat\b",
    r"\bskrytka\s+pocztowa\b",
    r"\bskr\.\s*poczt\b",
    r"\bbox\s+\d+\b",
    r"\blok\.\s*u(?:\b|\d)",
    r"\blokal\s+u(?:\b|\d)",
]

def normalize_address(address_str: str) -> str:
    """Normalizuje ciąg adresowy do celów porównywania i wyszukiwania duplikatów."""
    if not address_str:
        return ""
    s = unicodedata.normalize("NFKD", address_str)
    s = s.encode("ASCII", "ignore").decode("ASCII").upper()
    s = re.sub(r"\b(ULICA|UL\.|AL\.?|ALEJA|ALEJE|PLAC|PL\.)\b", "", s)
    s = re.sub(r"[^\w\s/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def check_virtual_office(
    address_str: str,
    city: str = "",
    street: str = "",
    house_num: str = "",
    flat_num: str = "",
    is_batch_duplicate: bool = False,
    duplicate_count: int = 0
) -> Tuple[bool, str]:
    """Sprawdza podejrzenie wirtualnego biura."""
    reasons = []

    if is_batch_duplicate and duplicate_count > 2:
        reasons.append(f"DUPLIKAT_ADRESU_W_PLIKU ({duplicate_count} firm pod identycznym adresem)")

    full_addr = f"{address_str} {city} {street} {house_num} {flat_num}".upper()
    norm_city = normalize_address(city)
    norm_street = normalize_address(street)

    for kw in VIRTUAL_OFFICE_KEYWORDS:
        if re.search(kw, full_addr, flags=re.IGNORECASE):
            reasons.append(f"Słowo kluczowe w adresie: '{kw.replace('\\b', '').replace('\\s+', ' ')}'")
            break

    for vo in KNOWN_VIRTUAL_OFFICES:
        vo_city = vo["miasto"]
        vo_street = vo["ulica"]
        vo_nr = vo["nr"]

        city_match = (vo_city in norm_city or vo_city in full_addr)
        if not city_match:
            continue

        norm_vo_street = normalize_address(vo_street)
        if norm_vo_street in norm_street or norm_vo_street in full_addr:
            pattern_nr = rf"\b{re.escape(vo_nr)}(?:[A-Z]|/\d+)?\b"
            if re.search(pattern_nr, house_num.upper()) or re.search(pattern_nr, full_addr):
                reasons.append(f"Znany adres wirtualnego biura: {vo_street} {vo_nr} ({vo_city})")
                break

    is_virtual = len(reasons) > 0
    reason_str = "; ".join(reasons) if reasons else "Brak podejrzeń (adres fizyczny / unikalny)"
    return is_virtual, reason_str

def find_batch_address_duplicates(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Zlicza wystąpienia adresu w całym wsadzie danych."""
    counts = {}
    for r in records:
        ulica = r.get("ulica") or ""
        nr_domu = r.get("nr_domu") or ""
        nr_lokalu = r.get("nr_lokalu") or ""
        miejscowosc = r.get("miejscowosc") or ""

        if not ulica and not nr_domu:
            addr_key = normalize_address(r.get("adres_pelny") or "")
        else:
            addr_key = normalize_address(f"{miejscowosc} {ulica} {nr_domu} {nr_lokalu}")

        if addr_key and len(addr_key) > 4:
            counts[addr_key] = counts.get(addr_key, 0) + 1

    return counts


# ==============================================================================
# SYSTEM PUNKTACJI LEAD SCORE B2B ENERGIA (0–100 PKT)
# ==============================================================================

DEFAULT_ENERGY_WEIGHTS = {
    "start_score": 35,
    "energy_very_high_bonus": 25,
    "energy_high_mid_bonus": 10,
    "tech_many_bonus": 15,          # >=2 trafienia maszyn
    "tech_single_bonus": 8,          # 1 trafienie
    "recruitment_bonus": 15,        # 3-zmianowa / ruch ciągły / utrzymanie ruchu
    "eu_subsidies_bonus": 15,       # dotacje UE w linie / energię
    "direct_contact_bonus": 10,     # telefon lub email ze strony WWW
    "bdo_bonus": 10,
    "sf_recent_bonus": 10,
    "sf_missing_penalty": -20,
    "zaleglosci_penalty": -30,      # Dział 4 KRS
    "virtual_office_penalty": -20
}

def calculate_lead_score(data: Dict[str, Any], weights: Dict[str, int] = None) -> Dict[str, Any]:
    """
    Oblicza punktację Lead Score (0–100 pkt) pod kątem prospectingu sprzedaży energii elektrycznej.
    Jeżeli podmiot podlega twardemu wykluczeniu (Moduł 1) -> automatycznie 0 pkt i Rekomendacja ODRZUĆ.
    """
    w = DEFAULT_ENERGY_WEIGHTS.copy()
    if weights:
        w.update(weights)

    # 1. TWARDE WYKLUCZENIE (Moduł 1)
    powod_odrzucenia = data.get("powod_odrzucenia") or ""
    if powod_odrzucenia:
        return {
            "score": 0,
            "rekomendacja": f"ODRZUĆ ({powod_odrzucenia})",
            "kategoria": "ODRZUĆ",
            "breakdown": [f"0 pkt: Twarde wykluczenie – {powod_odrzucenia}."]
        }

    score = w.get("start_score", 35)
    breakdown = [f"+{score} pkt: Wynik bazowy (kwalifikacja wstępna)"]

    # 2. Profil Energetyczny (PKD)
    profil_e = str(data.get("profil_energetyczny", ""))
    if "BARDZO WYSOKI" in profil_e:
        val = w.get("energy_very_high_bonus", 25)
        score += val
        breakdown.append(f"+{val} pkt: Profil BARDZO WYSOKI (przemysł / przetwórstwo)")
    elif "WYSOKI" in profil_e or "ŚREDNI" in profil_e:
        val = w.get("energy_high_mid_bonus", 10)
        score += val
        breakdown.append(f"+{val} pkt: Profil WYSOKI / ŚREDNI (hotele / obiekty całodobowe)")
    else:
        breakdown.append("+0 pkt: Profil NISKI (usługi / biura)")

    # 3. Technologie / Maszyny na WWW
    tech_list = data.get("wykryte_technologie_lista") or []
    if len(tech_list) >= 2:
        val = w.get("tech_many_bonus", 15)
        score += val
        breakdown.append(f"+{val} pkt: Wykryte maszyny/technologie na WWW ({len(tech_list)} szt.: {', '.join(tech_list[:4])})")
    elif len(tech_list) == 1:
        val = w.get("tech_single_bonus", 8)
        score += val
        breakdown.append(f"+{val} pkt: Wykryta technologia na WWW ({tech_list[0]})")
    else:
        breakdown.append("+0 pkt: Brak wzmianek o parku maszynowym na WWW")

    # 4. Ślad rekrutacyjny (praca zmianowa / utrzymanie ruchu)
    sygnaly_rekr = str(data.get("sygnaly_rekrutacyjne", "")).strip()
    if sygnaly_rekr and sygnaly_rekr not in ("Brak", "Brak danych", "Brak wykrytych sygnałów", "Brak danych (limit wyszukiwarki)"):
        val = w.get("recruitment_bonus", 15)
        score += val
        breakdown.append(f"+{val} pkt: Ślad rekrutacyjny – praca zmianowa / ruch ciągły ({sygnaly_rekr})")

    # 5. Dotacje UE / Inwestycje technologiczne
    dotacje = str(data.get("dotacje_inwestycje_ue", "")).strip()
    if dotacje.startswith("TAK"):
        val = w.get("eu_subsidies_bonus", 15)
        score += val
        breakdown.append(f"+{val} pkt: Potwierdzone inwestycje / dotacje UE ({dotacje})")

    # 6. Bezpośrednie dane kontaktowe ze strony WWW (telefon lub mail)
    telefony = str(data.get("telefony_www", "")).strip()
    maile = str(data.get("maile_www", "")).strip()
    if telefony or maile:
        val = w.get("direct_contact_bonus", 10)
        score += val
        contact_hints = []
        if telefony:
            contact_hints.append("telefon")
        if maile:
            contact_hints.append("e-mail")
        breakdown.append(f"+{val} pkt: Pozyskano bezpośrednie dane kontaktowe ze strony ({', '.join(contact_hints)})")

    # 7. Rejestr BDO
    bdo_status = str(data.get("bdo_status", "")).upper()
    if bdo_status == "TAK":
        val = w.get("bdo_bonus", 10)
        score += val
        breakdown.append(f"+{val} pkt: Potwierdzony wpis w rejestrze BDO ({data.get('bdo_info', '')})")

    # 8. Sprawozdanie finansowe (SF) z KRS
    brak_sf = data.get("brak_aktualnych_sf", True)
    ostatnie_sf = data.get("ostatnie_sf_okres", "Brak")
    if not brak_sf:
        val = w.get("sf_recent_bonus", 10)
        score += val
        breakdown.append(f"+{val} pkt: Złożone aktualne sprawozdanie finansowe ({ostatnie_sf})")
    else:
        val = w.get("sf_missing_penalty", -20)
        score += val
        breakdown.append(f"{val} pkt: Brak aktualnych sprawozdań finansowych w KRS (>2 lata)")

    # 9. Zaległości w Dziale 4 KRS
    zaleglosci = str(data.get("zaleglosci_krs", "")).upper()
    if zaleglosci == "TAK":
        val = w.get("zaleglosci_penalty", -30)
        score += val
        breakdown.append(f"{val} pkt: Aktywne wpisy o zaległościach/egzekucjach w Dziale 4 KRS!")

    # 10. Podejrzenie wirtualnego biura
    is_virtual = bool(data.get("podejrzenie_wirtualnego_biura", False))
    if is_virtual:
        val = w.get("virtual_office_penalty", -20)
        score += val
        reason = data.get("powod_wirtualnego_biura", "Adres wirtualny")
        breakdown.append(f"{val} pkt: Podejrzenie wirtualnego biura ({reason})")

    # Ograniczenie punktacji 0–100 pkt
    score = max(0, min(100, score))

    # Kategoryzacja końcowa
    if score >= 80:
        rekomendacja = "HOT LEAD (Wysoki potencjał)"
        kategoria = "HOT"
    elif score >= 50:
        rekomendacja = "WARM LEAD (Do weryfikacji)"
        kategoria = "WARM"
    elif score >= 1:
        rekomendacja = "LOW / WYDMUSZKA (Niskie zapotrzebowanie / Ryzyko)"
        kategoria = "LOW"
    else:
        rekomendacja = "ODRZUĆ"
        kategoria = "ODRZUĆ"

    return {
        "score": score,
        "rekomendacja": rekomendacja,
        "kategoria": kategoria,
        "breakdown": breakdown
    }
