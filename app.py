import streamlit as st
import pandas as pd

st.title("🏃‍♂️ Coach Tool")

raw_input = st.text_area("Incolla qui la lista:", height=200)

# Aggiungiamo un bottone esplicito per elaborare
if st.button("Elabora Lista"):
    if raw_input:
        try:
            # Dividiamo per righe e poi per spazi (o tab)
            lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
            # Creiamo il dataframe
            df = pd.DataFrame(lines)
            
            # ATTENZIONE: Se il formato non è tabulato ma separato da spazi, 
            # dovremmo usare uno split diverso. 
            # Per ora proviamo a prendere le colonne che ci interessano.
            # Se la lista è incollata male, il df avrà tante colonne.
            
            st.write("Lista elaborata con successo!")
            # Qui poi aggiungeremo la logica di visualizzazione
            st.dataframe(df)
            
        except Exception as e:
            st.error(f"Errore: {e}")
    else:
        st.warning("Incolla qualcosa prima di premere Elabora!")
