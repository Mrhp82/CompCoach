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
        
        export_df = df[(df['Coach'] != "None") | (df['Side_Coach'] != "None")].copy()
        
        if export_df.empty:
            st.warning("No athletes assigned yet!")
        else:
            unique_times = []
            for _, row in export_df.iterrows():
                t_str = str(row['Time'])
                t_sort = row['Time_Sort']
                if not any(t['str'] == t_str for t in unique_times):
                    unique_times.append({'str': t_str, 'sort': t_sort})
                    
            unique_times.sort(key=lambda x: x['sort'] if pd.notna(x['sort']) else pd.Timestamp.max)
            
            for time_data in unique_times:
                current_time = time_data['str']
                output += f"🕒 *{current_time}*\n"
                
                time_subset = export_df[export_df['Time'] == current_time]
                
                active_coaches = []
                for coach in COACH_LIST:
                    if coach in time_subset['Coach'].values or coach in time_subset['Side_Coach'].values:
                        coach_records = time_subset[(time_subset['Coach'] == coach) | (time_subset['Side_Coach'] == coach)]
                        first_pod = str(coach_records['Pod'].values)
                        first_strip = str(coach_records['Strip'].values)
                        active_coaches.append({'name': coach, 'pod': first_pod, 'strip': first_strip})
                
                active_coaches.sort(key=lambda x: (x['pod'], x['strip']))
                
                for c_data in active_coaches:
                    coach_name = c_data['name']
                    output += f"\n🤺 *{coach_name.upper()}*\n"
                    
                    coach_athletes = time_subset[(time_subset['Coach'] == coach_name) | (time_subset['Side_Coach'] == coach_name)]
                    coach_athletes = coach_athletes.sort_values(by=['Pod', 'Strip'])
                    
                    for _, row in coach_athletes.iterrows():
                        s = str(row['Strip'])
                        a = str(row['Athlete'])
                        
                        if str(row['Coach']) == coach_name:
                            side_val = str(row['Side_Coach'])
                            # Etichetta ultra-compatta per il Side Coach [S: NOME]
                            side_str = f" [S: {side_val[:3].upper()}]" if side_val != "None" else ""
                            output += f"🔹 {s}: {a}{side_str}\n"
                        elif str(row['Side_Coach']) == coach_name:
                            main_val = str(row['Coach'])
                            # L'icona arancione indica già che sei Side. Aggiungiamo solo [M: NOME]
                            main_str = f" [M: {main_val[:3].upper()}]" if main_val != "None" else ""
                            output += f"🔸 {s}: {a}{main_str}\n"
                
                output += "\n" + "—"*15 + "\n\n"
                
            st.code(output.strip())
