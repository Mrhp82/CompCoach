import streamlit as st
import pandas as pd
import json
import os
import requests
from bs4 import BeautifulSoup

st.set_page_config(layout="wide")
st.title("🏃‍♂️ CompCoach Pro")

# --- DATABASE SETUP ---
DB_FILE = "database_gare.json"

def load_db():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

db = load_db()

# --- SETUP COMPETIZIONE E COACH ---
st.subheader("⚙️ Setup Competizione")
col1, col2 = st.columns([1, 2])
with col1:
    comp_name = st.text_input("Nome Competizione", "Gara AFM")
with col2:
    ALL_COACHES = ["Igor", "Carmine", "JM", "Vivien", "Ruperto", "Sam", "Yilu", "Daniel"]
    active_coaches = st.multiselect("Seleziona i Coach presenti oggi:", ALL_COACHES, default=ALL_COACHES)

st.divider()

# --- CARICAMENTO DATI (TABS) ---
tab1, tab2, tab3 = st.tabs(["📝 Incolla Lista (Consigliato)", "📂 Carica Gara Salvata", "🌐 Estrai da FencingTimeLive (Sperimentale)"])

with tab1:
    raw_input = st.text_area("Incolla la lista (Atleta, Pedana, Orario, Numero):", height=100)
    if st.button("Carica Lista Incollata"):
        if raw_input:
            try:
                lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
                df_temp = pd.DataFrame(lines)
                
                # Assicuriamoci di avere 4 colonne
                while df_temp.shape[1] < 4:
                    df_temp[df_temp.shape[1]] = ""
                    
                df = df_temp.iloc[:, 0:4].copy()
                df.columns = ['Athlete', 'Strip', 'Time', 'Strip_Num']
                
                df['Athlete'] = df['Athlete'].astype(str).str.strip()
                df['Strip'] = df['Strip'].astype(str).str.strip()
                df['Time'] = df['Time'].astype(str).str.strip()
                
                df['Pod'] = [str(x)[0].upper() if x and x != "None" else "" for x in df['Strip']]
                
                df['Time_Sort'] = pd.to_datetime(df['Time'], format='%I:%M %p', errors='coerce')
                df = df.sort_values(by=['Time_Sort', 'Pod', 'Strip'])
                
                df['Coach'] = "None"
                df['Side_Coach'] = "None"
                df['Display'] = "Strip " + df['Strip'] + " | " + df['Athlete']
                
                st.session_state['df'] = df
                st.session_state['reset_counter'] = 0
            except Exception as e:
                st.error(f"Errore di formattazione: {e}")

with tab2:
    if db:
        saved_games = list(db.keys())
        selected_game = st.selectbox("Scegli una gara salvata:", saved_games)
        if st.button("Carica Gara Selezionata"):
            # Ripristina il dataframe dal database JSON
            loaded_data = db[selected_game]
            df = pd.DataFrame(loaded_data)
            df['Time_Sort'] = pd.to_datetime(df['Time_Sort'], errors='coerce')
            st.session_state['df'] = df
            st.session_state['reset_counter'] = 0
            st.success(f"Gara '{selected_game}' caricata con successo!")
    else:
        st.info("Nessuna gara salvata in archivio al momento.")

with tab3:
    ftl_url = st.text_input("Inserisci il link di FencingTimeLive (es. pagina dei Gironi):")
    if st.button("Estrai Atleti AFM"):
        if ftl_url:
            with st.spinner("Tentativo di estrazione in corso..."):
                try:
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                    response = requests.get(ftl_url, headers=headers, timeout=10)
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.text, 'html.parser')
                        # NOTA: FTL usa molto Javascript. Se i dati sono nel DOM HTML, proviamo a cercarli:
                        # Qui cerchiamo banalmente righe o celle che contengono "Academy Of Fencing Masters"
                        if "Academy of Fencing Masters" in response.text or "AFM" in response.text:
                            st.warning("Dati trovati, ma a causa della struttura complessa di FTL serve una configurazione API per estrarre Orari e Pedane in modo affidabile. Continua a usare il copia-incolla per ora!")
                        else:
                            st.error("Nessun atleta AFM trovato o la pagina è bloccata da Cloudflare (Bot Protection).")
                    else:
                        st.error(f"Impossibile accedere al sito. Codice errore: {response.status_code}")
                except Exception as e:
                    st.error(f"Errore durante l'estrazione: {e}")


# --- FLUSSO PRINCIPALE (Se ci sono dati caricati) ---
if 'df' in st.session_state:
    df = st.session_state['df']
    
    # 1. CONTATORE E SALVATAGGIO
    col_stat, col_save = st.columns([2, 1])
    with col_stat:
        st.success(f"🎯 **Atleti totali in lista:** {len(df)}")
    with col_save:
        if st.button("💾 Salva/Aggiorna questa Gara"):
            # Converte il df in dizionario per salvarlo in JSON in modo sicuro (evita i Timestamp error)
            save_df = df.copy()
            save_df['Time_Sort'] = save_df['Time_Sort'].astype(str)
            db[comp_name] = save_df.to_dict('records')
            save_db(db)
            st.toast("Gara salvata nel database!")

    # 2. MAPPA GARA
    st.subheader("📍 Strip Map")
    display_df = df[['Time', 'Strip', 'Athlete', 'Coach', 'Side_Coach']]
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    
    st.divider()
    
    # 3. ASSEGNAZIONE COACH
    st.subheader("✍️ Assignment")
    
    # Usiamo active_coaches invece di COACH_LIST per mostrare solo chi c'è oggi
    main_coach = st.radio("1. Select Main Coach:", ["No Change", "None"] + active_coaches, horizontal=True)
    side_coach = st.radio("2. Select Side Coach:", ["No Change", "None"] + active_coaches, horizontal=True)
    
    st.write("---")
    st.write("**3. Select Athletes:**")
    
    ui_df = df.copy()
    ui_df['Is_Assigned'] = (ui_df['Coach'] != "None") | (ui_df['Side_Coach'] != "None")
    ui_df = ui_df.sort_values(by=['Is_Assigned', 'Time_Sort', 'Pod', 'Strip'])
    
    reset_key = st.session_state.get('reset_counter', 0)
    selected_list = []
    
    with st.container(height=350):
        for _, row in ui_df.iterrows():
            athlete_display = str(row['Display'])
            
            if row['Is_Assigned']:
                main_init = str(row['Coach'])[:3].upper() if row['Coach'] != "None" else "-"
                side_init = str(row['Side_Coach'])[:3].upper() if row['Side_Coach'] != "None" else "-"
                ui_label = f"✅ {athlete_display} [M: {main_init} | S: {side_init}]"
            else:
                ui_label = athlete_display
                
            chk_key = f"chk_{athlete_display}_{reset_key}"
            st.checkbox(ui_label, key=chk_key)
            
            if st.session_state.get(chk_key, False):
                selected_list.append(athlete_display)
                
    st.caption(f"Selected: {len(selected_list)} athletes")
    
    if st.button("✅ 4. Confirm Assignment"):
        if not selected_list:
            st.warning("Please select at least one athlete first!")
        else:
            if main_coach != "No Change":
                df.loc[df['Display'].isin(selected_list), 'Coach'] = main_coach
            
            if side_coach != "No Change":
                df.loc[df['Display'].isin(selected_list), 'Side_Coach'] = side_coach
                
            st.session_state['df'] = df
            st.session_state['reset_counter'] += 1
            
            # Autosave silente dopo ogni conferma
            save_df = df.copy()
            save_df['Time_Sort'] = save_df['Time_Sort'].astype(str)
            db[comp_name] = save_df.to_dict('records')
            save_db(db)
            
            st.rerun()
            
    st.divider()
    
    # 4. EXPORT TEXT FOR WHATSAPP
    st.subheader("📱 Export Schedule")
    if st.button("📝 Generate WhatsApp Text"):
        output = ""
        
        export_df = df[(df['Coach'] != "None") | (df['Side_Coach'] != "None")].copy()
        
        if export_df.empty:
            st.warning("No athletes assigned yet!")
        else:
            unique_times = []
            for _, row in export_df.iterrows():
                t_str = str(row['Time'])
                t_sort = row['Time_Sort']
                if not any(t['str'] == t_str for t in unique_times):
                    unique_times.append({'str': t_str, 'sort': t_sort})
                    
            unique_times.sort(key=lambda x: x['sort'] if pd.notna(x['sort']) else pd.Timestamp.max)
            
            # Intestazione della gara
            output += f"🏆 *{comp_name.upper()}*\n\n"
            
            for time_data in unique_times:
                current_time = time_data['str']
                output += f"🕒 *{current_time}*\n"
                
                time_subset = export_df[export_df['Time'] == current_time]
                
                active_coaches_time = []
                for coach in active_coaches:
                    if coach in time_subset['Coach'].values or coach in time_subset['Side_Coach'].values:
                        coach_records = time_subset[(time_subset['Coach'] == coach) | (time_subset['Side_Coach'] == coach)]
                        first_pod = str(coach_records['Pod'].values[0])
                        first_strip = str(coach_records['Strip'].values[0])
                        active_coaches_time.append({'name': coach, 'pod': first_pod, 'strip': first_strip})
                
                active_coaches_time.sort(key=lambda x: (x['pod'], x['strip']))
                
                for c_data in active_coaches_time:
                    coach_name = c_data['name']
                    output += f"\n🤺 *{coach_name.upper()}*\n"
                    
                    coach_athletes = time_subset[(time_subset['Coach'] == coach_name) | (time_subset['Side_Coach'] == coach_name)]
                    coach_athletes = coach_athletes.sort_values(by=['Pod', 'Strip'])
                    
                    for _, row in coach_athletes.iterrows():
                        s = str(row['Strip'])
                        a = str(row['Athlete'])
                        
                        if str(row['Coach']) == coach_name:
                            side_val = str(row['Side_Coach'])
                            side_str = f" [S: {side_val[:3].upper()}]" if side_val != "None" else ""
                            output += f"🔹 {s}: {a}{side_str}\n"
                        elif str(row['Side_Coach']) == coach_name:
                            main_val = str(row['Coach'])
                            main_str = f" [M: {main_val[:3].upper()}]" if main_val != "None" else ""
                            output += f"🔸 {s}: {a}{main_str}\n"
                
                output += "\n" + "—"*15 + "\n\n"
                
            st.code(output.strip())