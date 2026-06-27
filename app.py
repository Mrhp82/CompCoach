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
            
            # Extract first 4 columns
            df = df_temp.iloc[:, 0:4].copy()
            df.columns = ['Athlete', 'Strip', 'Time', 'Strip_Num']
            
            # Implicit Pod logic (extracts the letter from the Strip, e.g., 'M' from 'M1')
            df['Pod'] = [str(x).upper() if pd.notna(x) and str(x) else "" for x in df['Strip']]
            
            # Sort by Time, then Pod, then Strip
            df['Time_Sort'] = pd.to_datetime(df['Time'], format='%I:%M %p', errors='coerce')
            df = df.sort_values(by=['Time_Sort', 'Pod', 'Strip'])
            
            df['Coach'] = "None"
            df['Side_Coach'] = "None"
            
            # Display label for the multiselect
            df['Display'] = "Strip " + df['Strip'].astype(str) + " | " + df['Athlete'].astype(str)
            
            st.session_state['df'] = df
        except Exception as e:
            st.error(f"Formatting error. Please check the pasted text. Details: {e}")

if 'df' in st.session_state:
    df = st.session_state['df']
    
    # 1. STRIP MAP
    st.subheader("📍 Strip Map")
    # Clean display for mobile
    display_df = df[['Time', 'Strip', 'Athlete', 'Coach', 'Side_Coach']]
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    
    st.divider()
    
    # 2. ASSIGN COACHES (Simultaneous Assignment)
    st.subheader("✍️ Assign Coaches")
    
    main_coach = st.radio("Main Coach:", COACH_LIST, horizontal=True)
    side_coach = st.radio("Side Coach (Optional):", ["None"] + COACH_LIST, horizontal=True)
        
    st.info("💡 Tip: Tap the box below and type the Pod letter (e.g., 'M') to filter quickly!")
    
    # Dynamic key: automatically clears selections when you change either coach!
    ms_key = f"ms_{main_coach}_{side_coach}"
    
    selected_display = st.multiselect(
        f"Select athletes:", 
        df['Display'].tolist(),
        key=ms_key
    )
    
    if st.button("✅ Confirm Assignment"):
        # Assign Main Coach
        df.loc[df['Display'].isin(selected_display), 'Coach'] = main_coach
        # Assign Side Coach
        df.loc[df['Display'].isin(selected_display), 'Side_Coach'] = side_coach
            
        st.session_state['df'] = df
        st.rerun()
        
    st.divider()
    
    # 3. WHATSAPP OUTPUT
    if st.button("Generate WhatsApp Text"):
        output = ""
        # Re-sort before export to ensure alphabetical Pod order
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
