import streamlit as st
import pandas as pd

st.set_page_config(layout="centered")
st.title("🏃‍♂️ CompCoach Fast")

COACH_LIST = ["Igor", "Carmine", "JM", "Vivien", "Ruperto", "Sam", "Yilu", "Daniel"]

raw_input = st.text_area("Incolla lista:", height=100)

if st.button("Carica Lista"):
    lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
    df = pd.DataFrame(lines).iloc[:, 0:3]
    df.columns = ['Atleta', 'Orario', 'Pedana']
    df['Coach'] = "Nessuno"
    st.session_state['df'] = df

if 'df' in st.session_state:
    df = st.session_state['df']
    
    # 1. Seleziona Coach
    target_coach = st.selectbox("Assegna a:", COACH_LIST)
    
    # 2. Selezione multipla rapida (checkbox o multiselect)
    # Multiselect è il più veloce su iPhone per grandi liste
    selected = st.multiselect("Seleziona atleti per " + target_coach, df['Atleta'].tolist())
    
    if st.button("Assegna"):
        df.loc[df['Atleta'].isin(selected), 'Coach'] = target_coach
        st.session_state['df'] = df
        st.success(f"Assegnati {len(selected)} atleti a {target_coach}")

    # 3. Output compatto
    if st.button("Genera Testo WhatsApp"):
        output = ""
        for coach in COACH_LIST:
            subset = df[df['Coach'] == coach]
            if not subset.empty:
                output += f"\n--- {coach.upper()} ---\n"
                for _, row in subset.iterrows():
                    output += f"P{row['Pedana']} | {row['Atleta']} | {row['Orario']}\n"
        st.code(output)
