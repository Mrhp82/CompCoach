import streamlit as st
import pandas as pd

st.title("🏃‍♂️ CompCoach")

raw_input = st.text_area("Incolla qui la lista:", height=200)

if st.button("Elabora Lista"):
    if raw_input:
        # Dividiamo per righe
        lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
        df = pd.DataFrame(lines)
        
        # Pulizia: teniamo solo le colonne che ci servono e rinominiamo
        # Assumiamo che le colonne siano quelle viste nello screenshot
        df = df[[0, 2, 3]]
        df.columns = ['Atleta', 'Orario', 'Pedana']
        
        # Ordinamento
        df['Orario_dt'] = pd.to_datetime(df['Orario'], format='%I:%M %p')
        df = df.sort_values(by=['Orario_dt', 'Pedana'])
        
        st.session_state['df'] = df

# Se abbiamo i dati in memoria, mostriamo l'interfaccia coach
if 'df' in st.session_state:
    df = st.session_state['df']
    coach_name = st.text_input("Nome Coach:")
    selected_athletes = st.multiselect("Seleziona atleti:", df['Atleta'].tolist())
    
    if coach_name and selected_athletes:
        st.subheader("📋 Anteprima per WhatsApp")
        # Generiamo il testo compatto
        output = f"Coach {coach_name}:\n"
        subset = df[df['Atleta'].isin(selected_athletes)]
        for _, row in subset.iterrows():
            output += f"P{row['Pedana']} - {row['Atleta']} ({row['Orario']})\n"
        
        st.code(output)
        st.info("Copia e invia su WhatsApp!")
