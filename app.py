import streamlit as st
import pandas as pd

st.set_page_config(layout="wide")
st.title("🏃‍♂️ CompCoach Pro")

COACH_LIST = ["Igor", "Carmine", "JM", "Vivien", "Ruperto", "Sam", "Yilu", "Daniel"]

raw_input = st.text_area("Paste list here:", height=100)

if st.button("Load List"):
    if raw_input:
        try:
            lines = []
            for line in raw_input.split('\n'):
                if line.strip():
                    parts = [str(p).strip() for p in line.split('\t')]
                    lines.append(parts)
            
            df_temp = pd.DataFrame(lines)
            df = df_temp.iloc[:, 0:4].copy()
            df.columns = ['Athlete', 'Strip', 'Time', 'Strip_Num']
            
            df['Athlete'] = df['Athlete'].astype(str)
            df['Strip'] = df['Strip'].astype(str)
            df['Time'] = df['Time'].astype(str)
            
            df['Pod'] = [str(x).upper() if x and x != "None" else "" for x in df['Strip']]
            
            # TRUCCO INFALLIBILE: Convertiamo in orario 24h ("14:00") come puro testo per il sorting
            temp_time = pd.to_datetime(df['Time'], format='%I:%M %p', errors='coerce')
            df['Time_Sort'] = temp_time.dt.strftime('%H:%M').fillna('99:99')
            
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
        
        # Nessun oggetto Pandas qui dentro, solo testo puro.
        records = []
        for _, r in df.iterrows():
            records.append({
                'Athlete': str(r['Athlete']),
                'Strip': str(r['Strip']),
                'Time': str(r['Time']),
                'Pod': str(r['Pod']),
                'Coach': str(r['Coach']),
                'Side_Coach': str(r['Side_Coach']),
                'Time_Sort': str(r['Time_Sort']) # Stringa garantita (es. "14:00")
            })
            
        coach_order_list = []
        for coach in COACH_LIST:
            coach_str = str(coach)
            coach_records = [r for r in records if r['Coach'] == coach_str or r['Side_Coach'] == coach_str]
            
            if coach_records:
                # Ordinamento puramente alfabetico
                coach_records.sort(key=lambda x: (x['Time_Sort'], x['Pod'], x['Strip']))
                first_rec = coach_records
                
                coach_order_list.append({
                    'coach': coach_str,
                    'first_time': first_rec['Time_Sort'],
                    'first_pod': first_rec['Pod'],
                    'first_strip': first_rec['Strip'],
                    'records': coach_records
                })
        
        coach_order_list.sort(key=lambda x: (x['first_time'], x['first_pod'], x['first_strip']))
        
        for data in coach_order_list:
            c_name = data['coach']
            output += f"\n--- {c_name.upper()} ---\n"
            
            for r in data['records']:
                t = r['Time']
                s = r['Strip']
                a = r['Athlete']
                
                if r['Coach'] == c_name:
                    side_str = f" (Side: {r['Side_Coach']})" if r['Side_Coach'] != "None" else ""
                    output += f"{t} | Strip {s} | {a}{side_str}\n"
                elif r['Side_Coach'] == c_name:
                    main_str = f" (Main: {r['Coach']})" if r['Coach'] != "None" else ""
                    output += f"{t} | Strip {s} | {a} [YOU ARE SIDE]{main_str}\n"
        
        st.code(output.strip())
