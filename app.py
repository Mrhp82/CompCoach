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
            # Initialize empty set for pure-touch selections
            st.session_state['selected_athletes'] = set()
        except Exception as e:
            st.error(f"Formatting error: {e}")

if 'df' in st.session_state:
    df = st.session_state['df']
    
    # --- 1. STRIP MAP ---
    st.subheader("📍 Strip Map")
    display_df = df[['Time', 'Strip', 'Athlete', 'Coach', 'Side_Coach']]
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    
    st.divider()
    
    # --- 2. SELECT ATHLETES (PURE TOUCH, NO KEYBOARD) ---
    st.subheader("👆 1. Select Athletes")
    
    # Pod Filter buttons
    available_pods = ["All"] + sorted(df['Pod'].unique().tolist())
    pod_filter = st.radio("Filter by Pod:", available_pods, horizontal=True)
    
    if pod_filter == "All":
        athletes_to_show = df['Display'].tolist()
    else:
        athletes_to_show = df[df['Pod'] == pod_filter]['Display'].tolist()
        
    st.caption("Tap to select/deselect (keyboard won't open):")
    
    # Scrollable container for checkboxes
    with st.container(height=250):
        for athlete in athletes_to_show:
            is_checked = athlete in st.session_state['selected_athletes']
            
            # If user taps the checkbox, update the set in session_state
            if st.checkbox(athlete, value=is_checked, key=f"chk_{athlete}"):
                st.session_state['selected_athletes'].add(athlete)
            else:
                st.session_state['selected_athletes'].discard(athlete)
                
    st.write(f"**Selected:** {len(st.session_state['selected_athletes'])} athletes")
    
    st.divider()

    # --- 3. ASSIGN COACHES ---
    st.subheader("✍️ 2. Assign Coaches")
    
    main_coach = st.radio("Main Coach:", ["No Change", "None"] + COACH_LIST, horizontal=True)
    side_coach = st.radio("Side Coach:", ["No Change", "None"] + COACH_LIST, horizontal=True)
    
    if st.button("✅ Confirm Assignment"):
        if not st.session_state['selected_athletes']:
            st.warning("Please select at least one athlete first!")
        else:
            selected_list = list(st.session_state['selected_athletes'])
            
            if main_coach != "No Change":
                df.loc[df['Display'].isin(selected_list), 'Coach'] = main_coach
            
            if side_coach != "No Change":
                df.loc[df['Display'].isin(selected_list), 'Side_Coach'] = side_coach
                
            st.session_state['df'] = df
            # Clear selection after successful assignment
            st.session_state['selected_athletes'] = set()
            st.rerun()
            
    st.divider()
    
    # --- 4. WHATSAPP OUTPUT ---
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
