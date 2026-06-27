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
            
            # Pulizia stringhe
            df['Athlete'] = df['Athlete'].astype(str).str.strip()
            df['Strip'] = df['Strip'].astype(str).str.strip()
            df['Time'] = df['Time'].astype(str).str.strip()
            
            df['Pod'] = [str(x).upper() if x and x != "None" else "" for x in df['Strip']]
            
            df['Time_Sort'] = pd.to_datetime(df['Time'], format='%I:%M %p', errors='coerce')
            df = df.sort_values(by=['Time_Sort', 'Pod', 'Strip'])
            
            df['Coach'] = "None"
            df['Side_Coach'] = "None"
            
            df['Display'] = "Strip " + df['Strip'] + " | " + df['Athlete']
            
            st.session_state['df'] = df
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
    
    # --- 2. ASSIGNMENT SECTION ---
    st.subheader("✍️ Assignment")
    
    main_coach = st.radio("1. Select Main Coach:", ["No Change", "None"] + COACH_LIST, horizontal=True)
    side_coach = st.radio("2. Select Side Coach:", ["No Change", "None"] + COACH_LIST, horizontal=True)
    
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
            if st.checkbox(ui_label, key=chk_key):
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
            st.rerun()
            
    st.divider()
    
    # --- 3. EXPORT TEXT FOR WHATSAPP ---
    st.subheader("📱 Export Schedule")
    if st.button("📝 Generate WhatsApp Text"):
        output = ""
        
        export_df = df.sort_values(by=['Time_Sort', 'Pod', 'Strip']).copy()
        
        # TRUCCO GENIALE: Troviamo l'ordine dei coach leggendo la tabella dall'alto verso il basso!
        coach_order = []
        for _, row in export_df.iterrows():
            c = str(row['Coach'])
            sc = str(row['Side_Coach'])
            if c in COACH_LIST and c not in coach_order:
                coach_order.append(c)
            if sc in COACH_LIST and sc not in coach_order:
                coach_order.append(sc)
                
        # Generiamo il testo seguendo questo ordine naturale
        for coach in coach_order:
            is_main = export_df['Coach'] == coach
            is_side = export_df['Side_Coach'] == coach
            subset = export_df[is_main | is_side]
            
            output += f"\n--- {coach.upper()} ---\n"
            
            for _, row in subset.iterrows():
                t = str(row['Time'])
                s = str(row['Strip'])
                a = str(row['Athlete'])
                
                if str(row['Coach']) == coach:
                    side_str = f" (Side: {row['Side_Coach']})" if str(row['Side_Coach']) != "None" else ""
                    output += f"{t} | Strip {s} | {a}{side_str}\n"
                elif str(row['Side_Coach']) == coach:
                    main_str = f" (Main: {row['Coach']})" if str(row['Coach']) != "None" else ""
                    output += f"{t} | Strip {s} | {a} [YOU ARE SIDE]{main_str}\n"
                    
        st.code(output.strip())
