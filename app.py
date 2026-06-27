import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("🏃‍♂️ CompCoach Pro")

COACH_LIST = ["Carmine", "Daniel", "Coach A", "Coach B", "Altro..."]

raw_input = st.text_area("Incolla qui la lista:", height=150)

if st.button("Elabora Lista"):
    if raw_input:
        try:
            # Suddividiamo per riga e poi per tabulazione
            lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
            df_temp = pd.DataFrame(lines)
            
            # PRENDIAMO DINAMICAMENTE LE PRIME 3 COLONNE, non importa quante ce ne siano
            df = df_temp.iloc[:, 0:3].copy()
            df.columns = ['Atleta', 'Orario', 'Pedana']
            
            # Sorting
            df['Orario_Sort'] = pd.to_datetime(df['Orario'], format='%I:%M %p', errors='coerce')
            df = df.sort_values(by=['Orario_Sort', 'Pedana'])
            
            df['Coach'] = "Nessuno"
            df['Side Coach'] = "Nessuno"
            
            st.session_state['df'] = df
            st.success("Lista caricata correttamente!")
        except Exception as e:
            st.error(f"Errore: {e}")

if 'df' in st.session_state:
    df = st.session_state['df']
    edited_df = st.data_editor(df.drop(columns=['Orario_Sort']), column_config={
        "Coach": st.column_config.SelectboxColumn(options=COACH_LIST),
        "Side Coach": st.column_config.SelectboxColumn(options=["Nessuno"] + COACH_LIST)
    }, hide_index=True)
    
    if st.button("Genera Output WhatsApp"):
        st.subheader("📋 Copia questo testo:")
        output = ""
        for coach in COACH_LIST:
            subset = edited_df[edited_df['Coach'] == coach]
            if not subset.empty:
                output += f"--- {coach.upper()} ---\n"
                for _, row in subset.iterrows():
                    side = f" (Side: {row['Side Coach']})" if row['Side Coach'] != "Nessuno" else ""
                    output += f"P{row['Pedana']} | {row['Atleta']} | {row['Orario']}{side}\n"
        st.code(output)
