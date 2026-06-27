import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("🏃‍♂️ CompCoach Pro")

COACH_LIST = ["Igor", "Carmine", "JM", "Vivien", "Ruperto", "Sam", "Yilu", "Daniel"]

raw_input = st.text_area("Paste list here:", height=100)

if st.button("Load List"):
    if raw_input:
        try:
            lines = [line.split('\t') for line in raw_input.split('\n') if line.strip()]
            df_temp = pd.DataFrame(lines)
            
            df = df_temp.iloc[:, 0:4].copy()
            df.columns = ['Athlete', 'Strip', 'Time', 'Strip_Num']
            
            df['Pod'] = [str(x).upper() if pd.notna(x) and str(x) else "" for x in df['Strip']]
            
            df['Time_Sort'] = pd.to_datetime(df['Time'], format='%I:%M %p', errors='coerce')
            df = df.sort_values(by=['Time_Sort', 'Pod', 'Strip'])
            
            df['Coach'] = "None"
            df['Side_Coach'] = "None"
            
            df['Display'] = "Strip " + df['Strip'].astype(str) + " | " + df['Athlete'].astype(str)
            
            st.session_state['df'] = df
            # Contatore per forzare il reset delle spunte
            st.session_state['reset_counter'] = 0
        except Exception as e:
            st.error(f"Formatting error: {e}")

if 'df' in st.session_state:
    df = st.session_state['df']
    
    # --- 1. STRIP MAP ---
    st.subheader("📍 Strip Map")
    display_df = df[['Time', 'Strip', 'Athlete', 'Coach', 'Side_Coach']]
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    
    st.divider()
    
    # --- 2. ASSIGNMENT SECTION (Top to Bottom flow) ---
    st.subheader("✍️ Assignment")
    
    # A. Scegli i Coach
    main_coach = st.radio("1. Select Main Coach:", ["No Change", "None"] + COACH_LIST, horizontal=True)
    side_coach = st.radio("2. Select Side Coach:", ["No Change", "None"] + COACH_LIST, horizontal=True)
    
    st.write("---")
    
    # B. Seleziona gli Atleti
    st.write("**3. Select Athletes:**")
    
    athletes_to_show = df['Display'].tolist()
    reset_key = st.session_state.get('reset_counter', 0)
    selected_list = []
    
    # Box con scroll per non allungare troppo la pagina
    with st.container(height=300):
        for athlete in athletes_to_show:
            # Il reset_key cambia ad ogni conferma, azzerando le caselle
            chk_key = f"chk_{athlete}_{reset_key}"
            if st.checkbox(athlete, key=chk_key):
                selected_list.append(athlete)
                
    st.caption(f"Selected: {len(selected_list)} athletes")
    
    # C. Conferma
    if st.button("✅ 4. Confirm Assignment"):
        if not selected_list:
            st.warning("Please select at least one athlete first!")
        else:
            if main_coach != "No Change":
                df.loc[df['Display'].isin(selected_list), 'Coach'] = main_coach
            
            if side_coach != "No Change":
                df.loc[df['Display'].isin(selected_list), 'Side_Coach'] = side_coach
                
            st.session_state['df'] = df
            # Incrementiamo il contatore: al prossimo ricaricamento le caselle saranno vuote!
            st.session_state['reset_counter'] += 1
            st.rerun()
            
    st.divider()
    
    # --- 3. WHATSAPP OUTPUT ---
    if st.button("Generate WhatsApp Text"):
        output = ""
        export_df = df.sort_values(by=['Time_Sort', 'Pod', 'Strip'])
        
        for coach in COACH_LIST:
            is_main = export_df['Coach'] == coach
            is_side = export_df['Side_Coach'] == coach
            subset = export_df[is_main | is_side]
            
            if not subset.empty:
                output += f"\n--- {coach.upper()} ---\n"
                for _, row in subset.iterrows():
                    if row['Coach'] == coach:
                        side_str = f" (Side: {row['Side_Coach']})" if row['Side_Coach'] != "None" else ""
                        output += f"{row['Time']} | Strip {row['Strip']} | {row['Athlete']}{side_str}\n"
                    elif row['Side_Coach'] == coach:
                        main_str = f" (Main: {row['Coach']})" if row['Coach'] != "None" else ""
                        output += f"{row['Time']} | Strip {row['Strip']} | {row['Athlete']} [YOU ARE SIDE]{main_str}\n"
        st.code(output)
