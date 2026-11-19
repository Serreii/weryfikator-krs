"""
Weryfikator KRS & B2B Lead Scorer (Edycja: Sprzedaż Energii Elektrycznej B2B)
Kompletna lokalna aplikacja Streamlit do automatycznej weryfikacji, profilowania energochłonności
oraz inteligentnego scoringu firm bez płatnych kluczy API.
"""

import io
import os
import time
import random
import logging
from datetime import datetime
from urllib.parse import quote_plus
from typing import List, Dict, Any

import streamlit as st
import pandas as pd
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

import verifiers
import scoring

# Konfiguracja strony Streamlit
st.set_page_config(
    page_title="Weryfikator KRS - B2B Energia & Prospecting",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Niestandardowy styl CSS
st.markdown("""
<style>
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        color: #1E3A8A;
        margin-bottom: 0.2rem;
    }
    .sub-title {
        font-size: 1.05rem;
        color: #4B5563;
        margin-bottom: 1.2rem;
    }
    .badge-energy {
        background-color: #FEF3C7;
        color: #92400E;
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 600;
        display: inline-block;
        margin-right: 6px;
    }
    .badge-free {
        background-color: #D1FAE5;
        color: #065F46;
        padding: 4px 10px;
        border-radius: 9999px;
        font-size: 0.85rem;
        font-weight: 600;
        display: inline-block;
    }
    .kpi-card {
        padding: 14px;
        border-radius: 10px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
        text-align: center;
        margin-bottom: 10px;
    }
    .kpi-passed {
        background-color: #ECFDF5;
        border: 1px solid #A7F3D0;
        color: #065F46;
    }
    .kpi-rejected {
        background-color: #FEF2F2;
        border: 1px solid #FECACA;
        color: #991B1B;
    }
    .kpi-low {
        background-color: #FFF7ED;
        border: 1px solid #FED7AA;
        color: #9A3412;
    }
    .kpi-total {
        background-color: #EFF6FF;
        border: 1px solid #BFDBFE;
        color: #1E40AF;
    }
    .kpi-number {
        font-size: 1.8rem;
        font-weight: 700;
        margin: 2px 0;
    }
    .kpi-label {
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .sub-breakdown {
        font-size: 0.8rem;
        margin-top: 4px;
        line-height: 1.3;
    }
</style>
""", unsafe_allow_html=True)


# ==============================================================================
# FUNKCJE POMOCNICZE OBSŁUGI PLIKÓW I EKSPORTU DO EXCELA (3 ZAKŁADKI)
# ==============================================================================

def load_uploaded_file(uploaded_file) -> pd.DataFrame:
    """Odczytuje plik CSV (obsługując różne separatory i kodowania) lub Excel (.xlsx)."""
    filename = uploaded_file.name.lower()
    if filename.endswith(".xlsx") or filename.endswith(".xls"):
        return pd.read_excel(uploaded_file)
    else:
        raw_bytes = uploaded_file.getvalue()
        encodings = ["utf-8-sig", "utf-8", "windows-1250", "cp1250", "iso-8859-2"]
        separators = [";", ",", "\t"]

        for enc in encodings:
            for sep in separators:
                try:
                    df = pd.read_csv(io.BytesIO(raw_bytes), encoding=enc, sep=sep, dtype=str)
                    if len(df.columns) > 1 or (len(df.columns) == 1 and "krs" in str(df.columns[0]).lower()):
                        return df
                except Exception:
                    continue

        return pd.read_csv(io.BytesIO(raw_bytes), encoding="utf-8", sep=None, engine="python", dtype=str)


# Docelowy układ kolumn biznesowych w generowanym arkuszu Excel
TARGET_COLUMN_ORDER = [
    "Lead_Score",
    "Rekomendacja",
    "Nazwa_Firmy",
    "Miasto_KRS",
    "Ulica_Nr_KRS",
    "Adres_Zakladu_WWW",
    "Link_Google_Maps",
    "Profil_Energetyczny",
    "Szacowana_Taryfa",
    "Kapital_Zakladowy",
    "Wykryte_Technologie",
    "Sygnaly_Rekrutacyjne",
    "Dotacje_Inwestycje_UE",
    "Posiada_OZE",
    "Telefony_WWW",
    "Maile_WWW",
    "Decydenci_KRS",
    "Reprezentacja",
    "Sugerowany_Mail_Prezesa",
    "LinkedIn_Decydenci",
    "Wiek_Firmy_Lat",
    "Swieza_Zmiana_KRS",
    "Strona_WWW",
    "NIP",
    "KRS",
    "Oddzialy_KRS",
    "Zaleglosci_KRS",
    "Dopasowane_PKD_Energochlonne",
    "Powod_Odrzucenia",
    "Weryfikacja_Status_KRS",
    "Weryfikacja_Status_VAT",
    "Weryfikacja_Liczba_Rachunkow_VAT",
    "Weryfikacja_BDO_Status",
    "Weryfikacja_BDO_Numer",
    "Weryfikacja_Wirtualne_Biuro",
    "Weryfikacja_Powod_Wirtualnego_Biura",
    "Weryfikacja_Ostatnie_SF",
    "Uzasadnienie_Punktacji"
]


def organize_dataframe_columns(df: pd.DataFrame, drop_kategoria: bool = True) -> pd.DataFrame:
    """Układa kolumny w logicznym porządku biznesowym, doklejając pozostałe kolumny na końcu."""
    existing_cols = list(df.columns)
    ordered_cols = [c for c in TARGET_COLUMN_ORDER if c in existing_cols]
    if drop_kategoria:
        remaining_cols = [c for c in existing_cols if c not in ordered_cols and c != "Kategoria"]
    else:
        remaining_cols = [c for c in existing_cols if c not in ordered_cols]
    return df[ordered_cols + remaining_cols]


def style_excel_sheet(ws, df_subset: pd.DataFrame):
    """Aplikuje profesjonalne formatowanie do wskazanego arkusza openpyxl."""
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Calibri", size=10)

    fill_hot = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    font_hot = Font(name="Calibri", size=10, color="006100", bold=True)

    fill_warm = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    font_warm = Font(name="Calibri", size=10, color="9C6500", bold=True)

    fill_low = PatternFill(start_color="FFDEB5", end_color="FFDEB5", fill_type="solid")
    font_low = Font(name="Calibri", size=10, color="8C3B00", bold=True)

    fill_dead = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    font_dead = Font(name="Calibri", size=10, color="9C0006", bold=True)

    thin_border = Border(
        left=Side(style='thin', color='E5E7EB'),
        right=Side(style='thin', color='E5E7EB'),
        top=Side(style='thin', color='E5E7EB'),
        bottom=Side(style='thin', color='E5E7EB')
    )

    headers = list(df_subset.columns)
    ws.append(headers)

    # Stylizacja nagłówków
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Indeksy kolumn specjalnych
    rec_col_idx = None
    score_col_idx = None
    multiline_cols = set()

    for idx, col_name in enumerate(headers, start=1):
        c_low = col_name.lower()
        if c_low in ("rekomendacja",):
            rec_col_idx = idx
        elif c_low in ("lead_score", "score"):
            score_col_idx = idx
        elif any(k in c_low for k in ["decyden", "technolog", "dotacj", "uzasadn", "oddzial"]):
            multiline_cols.add(idx)

    # Zapis danych
    for _, row in df_subset.iterrows():
        row_vals = ["" if pd.isna(v) else str(v) for v in row]
        ws.append(row_vals)
        curr_row = ws.max_row

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=curr_row, column=col_idx)
            cell.font = data_font
            cell.border = thin_border
            wrap = col_idx in multiline_cols
            cell.alignment = Alignment(vertical="center", wrap_text=wrap)

        if rec_col_idx:
            rc = ws.cell(row=curr_row, column=rec_col_idx)
            r_val = str(rc.value).upper()
            if "HOT" in r_val:
                rc.fill = fill_hot
                rc.font = font_hot
            elif "WARM" in r_val:
                rc.fill = fill_warm
                rc.font = font_warm
            elif "LOW" in r_val or "WYDMUSZKA" in r_val:
                rc.fill = fill_low
                rc.font = font_low
            elif "ODRZUĆ" in r_val or "ODRZUC" in r_val:
                rc.fill = fill_dead
                rc.font = font_dead

        if score_col_idx:
            sc = ws.cell(row=curr_row, column=score_col_idx)
            sc.alignment = Alignment(horizontal="center", vertical="center")
            sc.font = Font(name="Calibri", size=10, bold=True)

    # Włączamy AutoFilter na całym arkuszu
    ws.auto_filter.ref = ws.dimensions

    # Zamrażamy pierwszy wiersz
    ws.freeze_panes = "A2"

    # Dopasowanie szerokości kolumn (max 45 znaków)
    for col in ws.columns:
        max_l = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col[:80]:
            val_s = str(cell.value or "")
            if len(val_s) > max_l:
                max_l = len(val_s)
        ws.column_dimensions[col_letter].width = min(max(max_l + 3, 13), 45)


def generate_styled_excel_3sheets(df: pd.DataFrame) -> bytes:
    """
    Tworzy profesjonalny plik Excel (.xlsx) z podziałem na 3 dedykowane zakładki:
    1. DO_KONTAKTU (HOT & WARM) – Lead Score >= 50 pkt, posortowane malejąco
    2. NISKI_PRIORYTET (LOW) – Lead Score 1–49 pkt
    3. ODRZUCONE – 0 pkt wraz z kolumną Powod_Odrzucenia
    """
    output = io.BytesIO()
    wb = openpyxl.Workbook()

    # Zapewnij obecność kolumny pomocniczej Kategoria
    df_work = df.copy()
    if "Kategoria" not in df_work.columns:
        df_work["Kategoria"] = "LOW"

    # Podział danych na 3 segmenty
    df_contact = df_work[df_work["Kategoria"].isin(["HOT", "WARM"])].sort_values(by="Lead_Score", ascending=False)
    df_low = df_work[df_work["Kategoria"] == "LOW"].sort_values(by="Lead_Score", ascending=False)
    df_rejected = df_work[df_work["Kategoria"] == "ODRZUĆ"]

    # Porządkujemy kolumny dla każdego segmentu (usuwając pomocniczą kolumnę Kategoria)
    df_contact_sorted = organize_dataframe_columns(df_contact, drop_kategoria=True)
    df_low_sorted = organize_dataframe_columns(df_low, drop_kategoria=True)
    df_rejected_sorted = organize_dataframe_columns(df_rejected, drop_kategoria=True)

    dummy_empty = organize_dataframe_columns(df_work.head(0), drop_kategoria=True)

    # Zakładka 1: DO_KONTAKTU
    ws1 = wb.active
    ws1.title = "DO_KONTAKTU (HOT & WARM)"
    style_excel_sheet(ws1, df_contact_sorted if len(df_contact_sorted) > 0 else dummy_empty)

    # Zakładka 2: NISKI_PRIORYTET
    ws2 = wb.create_sheet("NISKI_PRIORYTET (LOW)")
    style_excel_sheet(ws2, df_low_sorted if len(df_low_sorted) > 0 else dummy_empty)

    # Zakładka 3: ODRZUCONE
    ws3 = wb.create_sheet("ODRZUCONE")
    style_excel_sheet(ws3, df_rejected_sorted if len(df_rejected_sorted) > 0 else dummy_empty)

    wb.save(output)
    output.seek(0)
    return output.getvalue()


# ==============================================================================
# GŁÓWNY INTERFEJS STREAMLIT
# ==============================================================================

st.markdown('<div class="main-title">⚡ Weryfikator KRS – Kwalifikacja B2B Energia</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="sub-title">System automatycznej selekcji i prospectingu pod kątem sprzedaży energii elektrycznej, '
    'analizy parków maszynowych, OZE i optymalizacji taryf przemysłowych. '
    '<span class="badge-energy">⚡ B2B Energy Edition</span> '
    '<span class="badge-free">100% Darmowe Źródła (0 zł za API)</span></div>',
    unsafe_allow_html=True
)

# Pasek boczny – Konfiguracja wag Lead Score i opcji
with st.sidebar:
    st.header("⚙️ Wagi Scoringu Energetycznego")
    st.caption("Dostosuj punkty przyznawane za wskaźniki energochłonności i wiarygodności:")

    w_start = st.slider("Wynik bazowy (Start po przejściu sita)", 0, 50, scoring.DEFAULT_ENERGY_WEIGHTS["start_score"], 5)
    w_e_very_high = st.slider("Bonus: Profil BARDZO WYSOKI (przemysł / CNC / tworzywa)", 0, 40, scoring.DEFAULT_ENERGY_WEIGHTS["energy_very_high_bonus"], 5)
    w_e_high_mid = st.slider("Bonus: Profil WYSOKI / ŚREDNI (hotele / całodobowe)", 0, 25, scoring.DEFAULT_ENERGY_WEIGHTS["energy_high_mid_bonus"], 5)
    w_tech = st.slider("Bonus: Maszyny na WWW (>=2 trafienia)", 0, 30, scoring.DEFAULT_ENERGY_WEIGHTS["tech_many_bonus"], 5)
    w_recruitment = st.slider("Bonus: Ślad rekrutacyjny (3 zmiany / ruch ciągły)", 0, 25, scoring.DEFAULT_ENERGY_WEIGHTS["recruitment_bonus"], 5)
    w_subsidies = st.slider("Bonus: Potwierdzone dotacje UE w linie/energię", 0, 25, scoring.DEFAULT_ENERGY_WEIGHTS["eu_subsidies_bonus"], 5)
    w_contact = st.slider("Bonus: Telefon / mail pozyskany z WWW", 0, 20, scoring.DEFAULT_ENERGY_WEIGHTS["direct_contact_bonus"], 5)
    w_bdo = st.slider("Bonus: Potwierdzony wpis w BDO", 0, 20, scoring.DEFAULT_ENERGY_WEIGHTS["bdo_bonus"], 5)
    w_sf = st.slider("Bonus: Złożone aktualne SF w KRS", 0, 20, scoring.DEFAULT_ENERGY_WEIGHTS["sf_recent_bonus"], 5)
    w_sf_miss = st.slider("Kara: Brak SF w KRS (>2 lata)", -40, 0, scoring.DEFAULT_ENERGY_WEIGHTS["sf_missing_penalty"], 5)
    w_zaleglosci = st.slider("Kara: Aktywne zaległości w Dziale 4 KRS", -50, 0, scoring.DEFAULT_ENERGY_WEIGHTS["zaleglosci_penalty"], 5)
    w_virtual = st.slider("Kara: Podejrzenie wirtualnego biura", -40, 0, scoring.DEFAULT_ENERGY_WEIGHTS["virtual_office_penalty"], 5)

    custom_weights = {
        "start_score": w_start,
        "energy_very_high_bonus": w_e_very_high,
        "energy_high_mid_bonus": w_e_high_mid,
        "tech_many_bonus": w_tech,
        "tech_single_bonus": int(w_tech * 0.5),
        "recruitment_bonus": w_recruitment,
        "eu_subsidies_bonus": w_subsidies,
        "direct_contact_bonus": w_contact,
        "bdo_bonus": w_bdo,
        "sf_recent_bonus": w_sf,
        "sf_missing_penalty": w_sf_miss,
        "zaleglosci_penalty": w_zaleglosci,
        "virtual_office_penalty": w_virtual
    }

    st.markdown("---")
    st.header("⚡ Opcje wywiadu i limitów")
    opt_check_web = st.checkbox("Wyszukiwanie WWW & scrapowanie maszyn", value=True, help="Wyszukuje własną domenę WWW i analizuje park maszynowy / OZE / kontakt")
    opt_check_deep = st.checkbox("Wywiad głęboki (LinkedIn, rekrutacja, dotacje UE)", value=True, help="Wykonywany wyłącznie dla nieodrzuconych firm o profilu BARDZO WYSOKI")
    opt_check_bdo = st.checkbox("Weryfikacja w rejestrze BDO", value=True, help="Sprawdza wpis odpadowy BDO po NIP")
    delay_s = st.slider("Opóźnienie zapytań (sekundy)", 0.4, 2.5, 0.8, 0.1, help="Chroni przed blokadą IP przez serwery")
    backup_freq = st.number_input("Auto-save backup co X rekordów", min_value=1, max_value=50, value=10)

# Inicjalizacja stanu sesji
if "df_input" not in st.session_state:
    st.session_state.df_input = None
if "df_results" not in st.session_state:
    st.session_state.df_results = None
if "stop_requested" not in st.session_state:
    st.session_state.stop_requested = False


# ==============================================================================
# SEKCJA 1: WCZYTYWANIE DANYCH WEJŚCIOWYCH
# ==============================================================================

col_up, col_smp = st.columns([3, 1])

with col_up:
    uploaded_file = st.file_uploader(
        "Wybierz plik CSV lub Excel (.xlsx) z bazą firm (np. export z Aleo / scrapera):",
        type=["csv", "xlsx", "xls"],
        help="Wymagana kolumna: KRS. Pozostałe dane aplikacja pobierze z rejestrów."
    )

with col_smp:
    st.write("")
    st.write("")
    if st.button("🧪 Załaduj bazę testową", help="Wczytuje zróżnicowaną bazę firm (przemysł, S.A., energetyka, wykreślona)"):
        if os.path.exists("przykladowe_firmy.xlsx"):
            st.session_state.df_input = pd.read_excel("przykladowe_firmy.xlsx")
            st.session_state.df_results = None
            st.success("Załadowano bazę testową!")
        else:
            st.error("Brak pliku przykladowe_firmy.xlsx.")

if uploaded_file is not None:
    try:
        st.session_state.df_input = load_uploaded_file(uploaded_file)
        st.session_state.df_results = None
    except Exception as e:
        st.error(f"Błąd podczas wczytywania pliku: {e}")

if st.session_state.df_input is not None:
    df_raw = st.session_state.df_input

    st.markdown("### 📋 Podgląd danych wejściowych")
    st.dataframe(df_raw.head(5), use_container_width=True)

    cols_avail = ["(Brak)"] + list(df_raw.columns)

    default_krs_idx = 0
    for idx, c in enumerate(df_raw.columns):
        if "krs" in c.lower():
            default_krs_idx = idx + 1
            break

    def find_idx(names):
        for idx, c in enumerate(df_raw.columns):
            if any(n in c.lower() for n in names):
                return idx + 1
        return 0

    c_m1, c_m2, c_m3, c_m4 = st.columns(4)
    with c_m1:
        col_krs = st.selectbox("Kolumna KRS * (Wymagana):", options=cols_avail[1:], index=max(0, default_krs_idx - 1))
    with c_m2:
        col_nip = st.selectbox("Kolumna NIP (Opcjonalna):", options=cols_avail, index=find_idx(["nip"]))
    with c_m3:
        col_nazwa = st.selectbox("Kolumna Nazwa (Opcjonalna):", options=cols_avail, index=find_idx(["nazwa", "firma", "company"]))
    with c_m4:
        col_miasto = st.selectbox("Kolumna Miasto (Opcjonalna):", options=cols_avail, index=find_idx(["miasto", "city", "miejscowosc"]))

    st.info(
        f"💡 Załadowano **{len(df_raw)}** rekordów. "
        "Aplikacja w pierwszej kolejności sprawdzi warunki twardego odrzucenia (S.A., podmioty publiczne, energetyka, brak VAT, upadłość/likwidacja). "
        "Dla odrzuconych firm pomijany jest scraping i wyszukiwarka, co wielokrotnie przyspiesza analizę."
    )

    c_btn1, c_btn2, c_btn_sp = st.columns([2, 1, 3])
    with c_btn1:
        start_btn = st.button("🚀 Rozpocznij Kwalifikację Energetyczną", type="primary", use_container_width=True)
    with c_btn2:
        stop_btn = st.button("⏹️ Zatrzymaj proces", use_container_width=True)

    if stop_btn:
        st.session_state.stop_requested = True
        st.warning("Zażądano zatrzymania! Zostaną zachowane już przetworzone firmy.")

    # ==============================================================================
    # SEKCJA 2: PĘTLA PRZETWARZANIA Z OPTYMALIZACJĄ KOLEJNOŚCI (MODUŁ 1 -> 2 -> 3 -> 4)
    # ==============================================================================
    if start_btn:
        st.session_state.stop_requested = False
        total_rows = len(df_raw)
        p_bar = st.progress(0.0)
        status_box = st.empty()
        live_table_box = st.empty()

        verified_records = []
        address_counts = scoring.find_batch_address_duplicates(df_raw.to_dict(orient="records"))
        start_time = time.time()

        for idx, row in df_raw.iterrows():
            if st.session_state.stop_requested:
                status_box.warning("Proces przerwany na żądanie użytkownika.")
                break

            curr_num = idx + 1
            raw_krs = row[col_krs]
            clean_krs_val = verifiers.clean_krs(raw_krs)

            name_hint = str(row[col_nazwa]) if col_nazwa != "(Brak)" and pd.notna(row[col_nazwa]) else ""
            city_hint = str(row[col_miasto]) if col_miasto != "(Brak)" and pd.notna(row[col_miasto]) else ""

            status_box.markdown(f"**[{curr_num}/{total_rows}]** Sprawdzanie: **{name_hint or clean_krs_val}** (KRS: `{clean_krs_val}`)...")

            # KROK 1: POBRANIE DANYCH Z KRS (Dział 1, 2, 3, 4, 6, PKD, Kapitał, Zarząd)
            krs_data = verifiers.verify_krs(clean_krs_val)
            verifiers.rate_limit_delay(delay_s * 0.6, delay_s * 1.0)

            company_name = krs_data.get("nazwa") or name_hint or ""
            nip_val = krs_data.get("nip") or (clean_nip(str(row[col_nip])) if col_nip != "(Brak)" and pd.notna(row[col_nip]) else "")
            city_val = krs_data.get("miejscowosc") or city_hint or ""
            street_val = krs_data.get("ulica") or ""
            house_val = krs_data.get("nr_domu") or ""
            flat_val = krs_data.get("nr_lokalu") or ""
            ulica_nr_val = krs_data.get("ulica_nr") or ""
            full_addr = krs_data.get("adres_pelny") or f"{city_val}, {ulica_nr_val}"

            # KROK 2: BIAŁA LISTA VAT
            vat_data = {}
            if nip_val:
                vat_data = verifiers.verify_vat_whitelist(nip_val)
                verifiers.rate_limit_delay(delay_s * 0.5, delay_s * 0.9)
            else:
                vat_data = {"status_vat": "Brak NIP", "liczba_rachunkow": 0, "rachunki_str": "Brak NIP"}

            # KROK 3: SPRAWDZENIE TWARDYCH WYKLUCZEŃ (MODUŁ 1)
            is_rejected, rejection_reason = verifiers.check_hard_exclusions(krs_data, vat_data)

            # Inicjalizacja pól operacyjnych
            profil_energetyczny, dopasowane_pkd = scoring.classify_energy_profile(krs_data.get("pkd_kody", []))
            bdo_data = {"bdo_status": "NIE", "bdo_numer": "", "bdo_info": "Pominięto"}
            web_data = {"strona_www": krs_data.get("strona_www_krs", ""), "wizytowka_google": "NIE"}
            tech_data = {
                "telefony_www": "", "maile_www": "", "wykryte_technologie": "",
                "wykryte_technologie_lista": [], "skala_technologiczna": "BRAK DANYCH",
                "posiada_oze": "NIE", "adres_zakladu_www": ""
            }
            sugerowany_mail_prezesa = ""
            linkedin_links = "Pominięto (odrzucona lub profil niski)"
            rekrutacja_sygnaly = "Pominięto"
            dotacje_ue = "NIE"
            szacowana_taryfa = "Nie dotyczy (odrzucona)"

            # Sprawdzenie wirtualnego biura
            norm_addr_key = scoring.normalize_address(f"{city_val} {street_val} {house_val} {flat_val}")
            dup_count = address_counts.get(norm_addr_key, 0)
            is_virtual, virtual_reason = scoring.check_virtual_office(
                address_str=full_addr, city=city_val, street=street_val,
                house_num=house_val, flat_num=flat_val,
                is_batch_duplicate=(dup_count > 2), duplicate_count=dup_count
            )

            # JEŚLI FIRMA NIE JEST ODRZUCONA -> WYKONAJ DALSZĄ ANALIZĘ (BDO, WWW, DEEP RESEARCH)
            if not is_rejected:
                # 3a. Rejestr BDO
                if opt_check_bdo and nip_val:
                    bdo_data = verifiers.verify_bdo(nip_val)
                    verifiers.rate_limit_delay(delay_s * 0.5, delay_s * 0.8)

                # 3b. Weryfikacja obecności w sieci (DDG)
                if opt_check_web and (company_name or nip_val):
                    web_data = verifiers.verify_web_presence(company_name, city_val, nip_val, krs_data.get("strona_www_krs", ""))

                # 3c. Scraping technologii i kontaktów ze strony WWW
                if web_data.get("strona_www"):
                    tech_data = verifiers.scrape_website_tech_and_contacts(web_data["strona_www"])
                    sugerowany_mail_prezesa = verifiers.generate_suggested_ceo_email(
                        krs_data.get("zarzad_str", ""), web_data["strona_www"]
                    )

                # 3d. Wywiad głęboki (LinkedIn, Rekrutacja, Dotacje UE) WYŁĄCZNIE dla BARDZO WYSOKIEGO profilu!
                if opt_check_deep and "BARDZO WYSOKI" in profil_energetyczny:
                    status_box.markdown(f"**[{curr_num}/{total_rows}]** Głęboki wywiad energetyczny: **{company_name}**...")
                    linkedin_links = verifiers.search_linkedin_decision_makers(krs_data.get("nazwa_skrocona", company_name))
                    rekrutacja_sygnaly = verifiers.search_recruitment_signals(krs_data.get("nazwa_skrocona", company_name))
                    dotacje_ue = verifiers.search_eu_subsidies(nip_val, krs_data.get("nazwa_skrocona", company_name))
                else:
                    linkedin_links = "Brak (profil inny niż BARDZO WYSOKI)"
                    rekrutacja_sygnaly = "Brak"
                    dotacje_ue = "NIE"

                # 3e. Estymacja taryfy energetycznej
                szacowana_taryfa = scoring.estimate_energy_tariff(
                    profil_energetyczny=profil_energetyczny,
                    wykryte_technologie=tech_data.get("wykryte_technologie_lista", []),
                    sygnaly_rekrutacyjne=rekrutacja_sygnaly,
                    kapital_wartosc=krs_data.get("kapital_wartosc", 0.0),
                    bdo_status=bdo_data.get("bdo_status", "NIE")
                )

            # KROK 4: KALKULACJA LEAD SCORE
            scoring_payload = {
                "powod_odrzucenia": rejection_reason if is_rejected else "",
                "profil_energetyczny": profil_energetyczny,
                "wykryte_technologie_lista": tech_data.get("wykryte_technologie_lista", []),
                "sygnaly_rekrutacyjne": rekrutacja_sygnaly,
                "dotacje_inwestycje_ue": dotacje_ue,
                "telefony_www": tech_data.get("telefony_www", ""),
                "maile_www": tech_data.get("maile_www", ""),
                "bdo_status": bdo_data.get("bdo_status", "NIE"),
                "bdo_info": bdo_data.get("bdo_info", ""),
                "brak_aktualnych_sf": krs_data.get("brak_aktualnych_sf", True),
                "ostatnie_sf_okres": krs_data.get("ostatnie_sf_okres", ""),
                "zaleglosci_krs": krs_data.get("zaleglosci_krs", "NIE"),
                "podejrzenie_wirtualnego_biura": is_virtual,
                "powod_wirtualnego_biura": virtual_reason
            }
            score_res = scoring.calculate_lead_score(scoring_payload, custom_weights)

            # Link Google Maps
            target_map_address = tech_data.get("adres_zakladu_www") or full_addr
            maps_url = f"https://www.google.com/maps/search/?api=1&query={quote_plus(target_map_address)}" if target_map_address else ""

            # Scalenie wszystkich kolumn
            rec = row.to_dict()
            rec.update({
                "Lead_Score": score_res["score"],
                "Rekomendacja": score_res["rekomendacja"],
                "Kategoria": score_res["kategoria"],
                "Nazwa_Firmy": company_name,
                "Miasto_KRS": city_val,
                "Ulica_Nr_KRS": ulica_nr_val,
                "Adres_Zakladu_WWW": tech_data.get("adres_zakladu_www", ""),
                "Link_Google_Maps": maps_url,
                "Profil_Energetyczny": profil_energetyczny,
                "Szacowana_Taryfa": szacowana_taryfa,
                "Kapital_Zakladowy": krs_data.get("kapital_wartosc", 0.0),
                "Wykryte_Technologie": tech_data.get("wykryte_technologie", ""),
                "Sygnaly_Rekrutacyjne": rekrutacja_sygnaly,
                "Dotacje_Inwestycje_UE": dotacje_ue,
                "Posiada_OZE": tech_data.get("posiada_oze", "NIE"),
                "Telefony_WWW": tech_data.get("telefony_www", ""),
                "Maile_WWW": tech_data.get("maile_www", ""),
                "Decydenci_KRS": krs_data.get("zarzad_str", ""),
                "Reprezentacja": krs_data.get("reprezentacja", "Brak danych"),
                "Sugerowany_Mail_Prezesa": sugerowany_mail_prezesa,
                "LinkedIn_Decydenci": linkedin_links,
                "Wiek_Firmy_Lat": krs_data.get("wiek_firmy_lat", 0.0),
                "Swieza_Zmiana_KRS": krs_data.get("swieza_zmiana_krs", "NIE"),
                "Strona_WWW": web_data.get("strona_www", ""),
                "NIP": nip_val,
                "KRS": clean_krs_val,
                "Oddzialy_KRS": krs_data.get("oddzialy_str", ""),
                "Zaleglosci_KRS": krs_data.get("zaleglosci_krs", "NIE"),
                "Dopasowane_PKD_Energochlonne": ", ".join(dopasowane_pkd) if dopasowane_pkd else "Brak",
                "Powod_Odrzucenia": rejection_reason if is_rejected else "",
                "Weryfikacja_Status_KRS": krs_data.get("status_krs", ""),
                "Weryfikacja_Status_VAT": vat_data.get("status_vat", ""),
                "Weryfikacja_Liczba_Rachunkow_VAT": vat_data.get("liczba_rachunkow", 0),
                "Weryfikacja_BDO_Status": bdo_data.get("bdo_status", "NIE"),
                "Weryfikacja_BDO_Numer": bdo_data.get("bdo_numer", ""),
                "Weryfikacja_Wirtualne_Biuro": "TAK" if is_virtual else "NIE",
                "Weryfikacja_Powod_Wirtualnego_Biura": virtual_reason,
                "Weryfikacja_Ostatnie_SF": krs_data.get("ostatnie_sf_okres", ""),
                "Uzasadnienie_Punktacji": " | ".join(score_res["breakdown"])
            })

            verified_records.append(rec)
            p_bar.progress(curr_num / total_rows)

            # Auto-save backup co X rekordów
            if curr_num % backup_freq == 0 or curr_num == total_rows:
                try:
                    pd.DataFrame(verified_records).to_csv("backup_wyniki.csv", index=False, encoding="utf-8-sig", sep=";")
                except Exception as ex:
                    logger.warning(f"Błąd auto-zapisu: {ex}")

            # Podgląd na żywo
            df_live = pd.DataFrame(verified_records)
            live_cols = ["KRS", "Nazwa_Firmy", "Profil_Energetyczny", "Szacowana_Taryfa", "Lead_Score", "Rekomendacja", "Powod_Odrzucenia"]
            v_live_cols = [c for c in live_cols if c in df_live.columns]
            live_table_box.dataframe(df_live[v_live_cols].tail(4), use_container_width=True)

        elapsed = round(time.time() - start_time, 1)
        st.session_state.df_results = pd.DataFrame(verified_records)
        status_box.success(f"✅ Ukończono kwalifikację **{len(verified_records)}** firm w czasie **{elapsed}s**! Dane są gotowe do pobrania.")


# ==============================================================================
# SEKCJA 3: PODSUMOWANIE WYNIKÓW, METRYKI ENERGETYCZNE I EKSPORT
# ==============================================================================

if st.session_state.df_results is not None and len(st.session_state.df_results) > 0:
    df_res = st.session_state.df_results

    st.markdown("---")
    st.markdown("## 📊 Kwalifikacja B2B Energia – Wyniki i Podsumowanie Sita")

    total_n = len(df_res)
    passed_mask = df_res["Kategoria"].isin(["HOT", "WARM"])
    passed_n = len(df_res[passed_mask])
    low_n = len(df_res[df_res["Kategoria"] == "LOW"])
    rejected_n = len(df_res[df_res["Kategoria"] == "ODRZUĆ"])

    # Rozbicie odrzuconych na podkategorie
    rej_dead_vat = 0
    rej_sa_public = 0
    rej_energy = 0

    for _, r in df_res[df_res["Kategoria"] == "ODRZUĆ"].iterrows():
        p = str(r.get("Powod_Odrzucenia", "")).lower()
        if "status krs" in p or "vat" in p or "błąd" in p:
            rej_dead_vat += 1
        elif "spółka akcyjna" in p or "publiczn" in p or "pzp" in p or "wodociąg" in p:
            rej_sa_public += 1
        elif "energetyczn" in p or "konkurencja" in p:
            rej_energy += 1
        else:
            rej_dead_vat += 1

    # Podsumowanie KPI na górze strony
    k1, k2, k3, k4 = st.columns(4)

    with k1:
        st.markdown(f"""
        <div class="kpi-card kpi-total">
            <div class="kpi-label">Wszystkie firmy w pliku</div>
            <div class="kpi-number">{total_n}</div>
            <div>100% wsadu</div>
        </div>
        """, unsafe_allow_html=True)

    with k2:
        pct_p = round(passed_n / total_n * 100, 1) if total_n else 0
        st.markdown(f"""
        <div class="kpi-card kpi-passed">
            <div class="kpi-label">🔥 PRZESZŁY SITO (DO KONTAKTU)</div>
            <div class="kpi-number">{passed_n}</div>
            <div><b>{pct_p}%</b> (HOT & WARM Leads)</div>
        </div>
        """, unsafe_allow_html=True)

    with k3:
        pct_l = round(low_n / total_n * 100, 1) if total_n else 0
        st.markdown(f"""
        <div class="kpi-card kpi-low">
            <div class="kpi-label">⚠️ NISKI PRIORYTET (LOW)</div>
            <div class="kpi-number">{low_n}</div>
            <div>{pct_l}% (Aktywne, mały pobór)</div>
        </div>
        """, unsafe_allow_html=True)

    with k4:
        pct_r = round(rejected_n / total_n * 100, 1) if total_n else 0
        st.markdown(f"""
        <div class="kpi-card kpi-rejected">
            <div class="kpi-label">❌ ODRZUCONE AUTOMATYCZNIE</div>
            <div class="kpi-number">{rejected_n}</div>
            <div class="sub-breakdown">
                💀 Martwe / brak VAT: <b>{rej_dead_vat}</b><br>
                🏛️ S.A. / Publiczne: <b>{rej_sa_public}</b><br>
                ⚡ Branża energetyczna: <b>{rej_energy}</b>
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.write("")

    # Przyciski pobierania plików
    c_dl1, c_dl2, c_sp = st.columns([2.5, 2, 2])

    with c_dl1:
        excel_bytes = generate_styled_excel_3sheets(df_res)
        st.download_button(
            label="📥 Pobierz Excel 3-zakładkowy (.xlsx)",
            data=excel_bytes,
            file_name=f"Kwalifikacja_Energia_B2B_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary"
        )

    with c_dl2:
        csv_bytes = df_res.to_csv(index=False, encoding="utf-8-sig", sep=";").encode("utf-8-sig")
        st.download_button(
            label="📥 Pobierz plik CSV (.csv)",
            data=csv_bytes,
            file_name=f"Kwalifikacja_Energia_B2B_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True
        )

    st.markdown("---")

    # Szybkie filtry nad tabelą
    st.markdown("### 🔍 Przeglądaj i filtruj bazę prospektów")
    cf1, cf2, cf3 = st.columns(3)
    with cf1:
        f_high_energy = st.checkbox("⚡ Pokaż tylko profil BARDZO WYSOKI / WYSOKI", value=False)
    with cf2:
        f_has_contact = st.checkbox("📞 Tylko z wykrytym telefonem lub mailem", value=False)
    with cf3:
        f_hide_rejected = st.checkbox("🚫 Ukryj odrzucone (0 pkt)", value=True)

    search_txt = st.text_input("Szukaj w wynikach (Nazwa, Miasto, NIP, KRS, Maszyny):", "")

    # Filtrowanie
    df_view = df_res.copy()

    if f_high_energy:
        df_view = df_view[df_view["Profil_Energetyczny"].str.contains("WYSOKI", na=False)]

    if f_has_contact:
        df_view = df_view[(df_view["Telefony_WWW"] != "") | (df_view["Maile_WWW"] != "")]

    if f_hide_rejected:
        df_view = df_view[df_view["Kategoria"] != "ODRZUĆ"]

    if search_txt:
        q = search_txt.lower()
        mask = (
            df_view["Nazwa_Firmy"].astype(str).str.lower().str.contains(q) |
            df_view["Miasto_KRS"].astype(str).str.lower().str.contains(q) |
            df_view["NIP"].astype(str).str.contains(q) |
            df_view["KRS"].astype(str).str.contains(q) |
            df_view["Wykryte_Technologie"].astype(str).str.lower().str.contains(q)
        )
        df_view = df_view[mask]

    # Prezentacja tabeli z najważniejszymi kolumnami handlowymi
    preview_cols = [
        "Lead_Score", "Rekomendacja", "Nazwa_Firmy", "Miasto_KRS", "Profil_Energetyczny",
        "Szacowana_Taryfa", "Wykryte_Technologie", "Telefony_WWW", "Maile_WWW",
        "Decydenci_KRS", "Powod_Odrzucenia"
    ]
    disp_cols = [c for c in preview_cols if c in df_view.columns]

    st.dataframe(
        df_view[disp_cols],
        use_container_width=True,
        height=min(450, 45 + len(df_view) * 38)
    )

    # Szczegółowa karta wybranej firmy
    st.markdown("### 🔎 Karta Wywiadu Gospodarczego & Energetycznego")
    comp_choices = [f"{row['Nazwa_Firmy']} (KRS: {row['KRS']}) | Score: {row['Lead_Score']} pkt" for _, row in df_view.iterrows()]

    if comp_choices:
        sel_comp_label = st.selectbox("Wybierz podmiot do pełnego podglądu:", options=comp_choices)
        sel_idx = comp_choices.index(sel_comp_label)
        c_data = df_view.iloc[sel_idx]

        with st.expander(f"⚡ Raport Energetyczny: {c_data['Nazwa_Firmy']}", expanded=True):
            col_r1, col_r2, col_r3 = st.columns(3)

            with col_r1:
                st.markdown("#### ⚡ Profil Energetyczny & Taryfa")
                st.markdown(f"**Profil:** `{c_data.get('Profil_Energetyczny')}`")
                st.markdown(f"**Szacowana taryfa:** `{c_data.get('Szacowana_Taryfa')}`")
                st.markdown(f"**Wykryte technologie:** `{c_data.get('Wykryte_Technologie') or 'Brak wzmianek'}`")
                st.markdown(f"**Posiada OZE (PV/pompy):** `{c_data.get('Posiada_OZE')}`")
                st.markdown(f"**Adres zakładu z WWW:** {c_data.get('Adres_Zakladu_WWW') or 'Brak (użyto siedziby)'}")
                if c_data.get("Link_Google_Maps"):
                    st.markdown(f"[📍 Zobacz zakład w Google Maps]({c_data.get('Link_Google_Maps')})")

            with col_r2:
                st.markdown("#### 📞 Kontakt i Decydenci")
                st.markdown(f"**Telefony z WWW:** `{c_data.get('Telefony_WWW') or 'Brak'}`")
                st.markdown(f"**Maile z WWW:** `{c_data.get('Maile_WWW') or 'Brak'}`")
                st.markdown(f"**Sugerowany mail Prezesa:** `{c_data.get('Sugerowany_Mail_Prezesa') or 'Brak'}`")
                st.markdown(f"**Reprezentacja:** `{c_data.get('Reprezentacja')}`")
                st.markdown(f"**Decydenci KRS:** {c_data.get('Decydenci_KRS')}")
                st.markdown(f"**Profile LinkedIn:** {c_data.get('LinkedIn_Decydenci')}")

            with col_r3:
                st.markdown("#### 🏭 Kondycja & Wywiad")
                st.markdown(f"**Ślad rekrutacyjny (3 zmiany):** `{c_data.get('Sygnaly_Rekrutacyjne')}`")
                st.markdown(f"**Dotacje / Inwestycje UE:** `{c_data.get('Dotacje_Inwestycje_UE')}`")
                st.markdown(f"**Zaległości w Dziale 4 KRS:** `{c_data.get('Zaleglosci_KRS')}`")
                st.markdown(f"**Kapitał zakładowy:** `{c_data.get('Kapital_Zakladowy'):,.2f} PLN`".replace(",", " "))
                st.markdown(f"**Wiek firmy:** `{c_data.get('Wiek_Firmy_Lat')} lat`")
                st.markdown(f"**Świeża zmiana w KRS (<180 dni):** `{c_data.get('Swieza_Zmiana_KRS')}`")
                st.markdown("---")
                score_val = c_data.get("Lead_Score")
                rec_val = c_data.get("Rekomendacja")
                st.metric("Lead Score", f"{score_val} / 100 pkt", delta=rec_val)

            st.markdown("#### 🧮 Uzasadnienie Punktacji")
            b_items = str(c_data.get("Uzasadnienie_Punktacji", "")).split(" | ")
            for b in b_items:
                st.markdown(f"- {b}")
    else:
        st.info("Brak podmiotów spełniających wybrane kryteria filtrów.")
