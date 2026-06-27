import streamlit as st
import pandas as pd

st.title("🏃‍♂️ CompCoach Pro")

# Lista coach predefiniti
COACH_LIST = ["Carmine", "Daniel", "Coach A", "Coach B", "Altro..."]

raw_input = st.text_area("Incolla qui la lista:", height=150)

if st.button("Elabora Lista"):
    lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
    df = pd.DataFrame(lines)[]
    df.columns = ['Atleta', 'Orario', 'Pedana']
    df['Coach'] = "Nessuno"
    df['Side Coach'] = "Nessuno"
    st.session_state['df'] = df

if 'df' in st.session_state:
    df = st.session_state['df']
    
    # Editor interattivo
    st.write("### Assegnazioni")
    edited_df = st.data_editor(df, column_config={
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
                    output += f"P{row['Pedana']} | {row['Atleta']} | {row['Orario']}\n"
        st.code(output)
