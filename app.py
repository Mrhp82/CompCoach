import streamlit as st
import pandas as pd

st.title("Coach Tool")

raw_input = st.text_area("Incolla qui la lista:")

if raw_input:
    # Parsing riga per riga
    lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
    df = pd.DataFrame(lines, columns=['Atleta', 'Pedana', 'Orario', 'Info1', 'Info2', 'Info3', 'Info4'])
    
    # Pulizia: teniamo solo Atleta, Orario, Pedana
    df = df[['Atleta', 'Orario', 'Pedana']]
    df['Orario'] = pd.to_datetime(df['Orario'], format='%I:%M %p').dt.time
    df = df.sort_values(by=['Orario', 'Pedana'])
    
    # Input Coach
    coach_name = st.text_input("Nome Coach:")
    
    st.write("Seleziona gli atleti per questo coach:")
    selected_athletes = st.multiselect("Atleti:", df['Atleta'].tolist())
    
    if coach_name and selected_athletes:
        st.subheader("Output per WhatsApp:")
        output = f"Coach {coach_name}:\n"
        for athlete in selected_athletes:
            info = df[df['Atleta'] == athlete].iloc
            output += f"{info['Pedana']} {athlete}\n"
        
        st.code(output)
        st.info("Copia il testo sopra e incollalo su WhatsApp.")


