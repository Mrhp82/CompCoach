import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("🏃‍♂️ CompCoach Pro")

COACH_LIST = ["Igor", "Carmine", "JM", "Vivien", "Ruperto", "Sam", "Yilu", "Daniel"]

raw_input = st.text_area("Incolla lista:", height=100)

if st.button("Carica Lista"):
    if raw_input:
        try:
            lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
            df_temp = pd.DataFrame(lines)
            
            # Prendiamo esattamente le prime 4 colonne (Nome, Pod+Pedana, Orario, Numero Pedana Assoluto)
            df = df_temp.iloc[:, 0:4].copy()
            df.columns = ['Atleta', 'Pod_Info', 'Orario', 'Pedana_Num']
            
            # MODIFICA QUI: Metodo infallibile per estrarre la lettera del Pod
            df['Pod'] = [str(x).upper() if pd.notna(x) and str(x) else "" for x in df['Pod_Info']]
            
            # Ordiniamo per Orario, poi per Lettera del Pod, poi per Numero Pedana
            df['Orario_Sort'] = pd.to_datetime(df['Orario'], format='%I:%M %p', errors='coerce')
            df = df.sort_values(by=['Orario_Sort', 'Pod', 'Pod_Info'])
            
            df['Coach'] = "Nessuno"
            df['Side_Coach'] = "Nessuno"
            
            # Prepariamo un'etichetta furba per farti selezionare velocemente da iPhone
            df['Display'] = "Pod " + df['Pod'] + " (" + df['Pod_Info'].astype(str) + ") | " + df['Atleta'].astype(str)
            
            st.session_state['df'] = df
        except Exception as e:
            st.error(f"Errore di formattazione. Controlla il testo incollato. Dettagli: {e}")

if 'df' in st.session_state:
    df = st.session_state['df']
    
    # 1. MAPPA GARA (Raggruppata per Pod)
    st.subheader("📍 Mappa Gara")
    display_df = df[['Orario', 'Pod', 'Pod_Info', 'Atleta', 'Coach', 'Side_Coach']]
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    
    st.divider()
    
    # 2. ASSEGNAZIONE VELOCE
    st.subheader("✍️ Assegna Coach")
    
    col1, col2 = st.columns(2)
    with col1:
        target_coach = st.selectbox("Chi vuoi assegnare?", COACH_LIST)
    with col2:
        role = st.radio("Ruolo:", ["Coach", "Side Coach"], horizontal=True)
        
    st.info("💡 Suggerimento: Tocca il box sotto e digita la lettera del Pod (es. 'M') per selezionare tutto il gruppo!")
    selected_display = st.multiselect(
        "Seleziona gli atleti:", 
        df['Display'].tolist()
    )
    
    if st.button("✅ Conferma Assegnazione"):
        if role == "Coach":
            df.loc[df['Display'].isin(selected_display), 'Coach'] = target_coach
        else:
            df.loc[df['Display'].isin(selected_display), 'Side_Coach'] = target_coach
            
        st.session_state['df'] = df
        st.rerun()
        
    st.divider()
    
    # 3. OUTPUT WHATSAPP FORMATTATO PER POD
    if st.button("Genera Testo WhatsApp"):
        output = ""
        for coach in COACH_LIST:
            is_main = df['Coach'] == coach
            is_side = df['Side_Coach'] == coach
            subset = df[is_main | is_side]
            
            if not subset.empty:
                output += f"\n--- {coach.upper()} ---\n"
                for _, row in subset.iterrows():
                    if row['Coach'] == coach:
                        side_str = f" (Side: {row['Side_Coach']})" if row['Side_Coach'] != "Nessuno" else ""
                        output += f"{row['Orario']} | Pod {row['Pod_Info']} | {row['Atleta']}{side_str}\n"
                    elif row['Side_Coach'] == coach:
                        main_str = f" (Main: {row['Coach']})" if row['Coach'] != "Nessuno" else ""
                        output += f"{row['Orario']} | Pod {row['Pod_Info']} | {row['Atleta']} [TU SEI SIDE]{main_str}\n"
        st.code(output)
