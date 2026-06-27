import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("🏃‍♂️ CompCoach Pro")

# Lista coach aggiornata
COACH_LIST = ["Igor", "Carmine", "JM", "Vivien", "Ruperto", "Sam", "Yilu", "Daniel"]

raw_input = st.text_area("Incolla qui la lista:", height=150)

if st.button("Elabora Lista"):
    if raw_input:
        try:
            lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
            df_temp = pd.DataFrame(lines)
            
            # Selezione dinamica delle prime 3 colonne (Nome, Orario, Pedana)
            df = df_temp.iloc[:, 0:3].copy()
            df.columns = ['Atleta', 'Orario', 'Pedana']
            
            # Formattazione per sorting
            df['Orario_Sort'] = pd.to_datetime(df['Orario'], format='%I:%M %p', errors='coerce')
            df = df.sort_values(by=['Orario_Sort', 'Pedana'])
            
            df['Coach'] = "Nessuno"
            df['Side Coach'] = "Nessuno"
            
            st.session_state['df'] = df
            st.success("Lista caricata! Assegna i coach.")
        except Exception as e:
            st.error(f"Errore: {e}")

if 'df' in st.session_state:
    df = st.session_state['df']
    
    st.write("### Assegnazioni")
    # Editor interattivo
    edited_df = st.data_editor(df.drop(columns=['Orario_Sort']), column_config={
        "Coach": st.column_config.SelectboxColumn(options=COACH_LIST, required=True),
        "Side Coach": st.column_config.SelectboxColumn(options=["Nessuno"] + COACH_LIST)
    }, hide_index=True)
    
    if st.button("Genera Output WhatsApp"):
        st.subheader("📋 Copia questo testo:")
        output = ""
        # Raggruppiamo per coach principale
        for coach in COACH_LIST:
            subset = edited_df[edited_df['Coach'] == coach]
            if not subset.empty:
                output += f"\n--- {coach.upper()} ---\n"
                for _, row in subset.iterrows():
                    side = f" (Side: {row['Side Coach']})" if row['Side Coach'] != "Nessuno" else ""
                    output += f"P{row['Pedana']} | {row['Atleta']} | {row['Orario']}{side}\n"
        st.code(output)
