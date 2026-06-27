import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import io

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
    # Forzatura del formato stringa per evitare errori PyArrow
    ui_df['Coach'] = ui_df['Coach'].astype(str)
    ui_df['Side_Coach'] = ui_df['Side_Coach'].astype(str)
    ui_df['Is_Assigned'] = (ui_df['Coach'] != "None") | (ui_df['Side_Coach'] != "None")
    ui_df = ui_df.sort_values(by=['Is_Assigned', 'Time_Sort', 'Pod', 'Strip'])
    
    reset_key = st.session_state.get('reset_counter', 0)
    selected_list = []
    
    with st.container(height=350):
        for _, row in ui_df.iterrows():
            athlete_display = row['Display']
            
            if row['Is_Assigned']:
                main_init = row['Coach'][:3].upper() if row['Coach'] != "None" else "-"
                side_init = row['Side_Coach'][:3].upper() if row['Side_Coach'] != "None" else "-"
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
    
    # --- 3. EXPORT IMAGE FOR WHATSAPP ---
    st.subheader("📱 Export Schedule")
    if st.button("📸 Generate Image"):
        output = ""
        export_df = df.sort_values(by=['Time_Sort', 'Pod', 'Strip']).copy()
        
        # Forzatura del formato stringa prima del confronto per aggirare il TypeError
        export_df['Coach'] = export_df['Coach'].astype(str)
        export_df['Side_Coach'] = export_df['Side_Coach'].astype(str)
        
        coach_order_list = []
        for coach in COACH_LIST:
            is_main = export_df['Coach'] == str(coach)
            is_side = export_df['Side_Coach'] == str(coach)
            subset = export_df[is_main | is_side]
            
            if not subset.empty:
                first_time = subset['Time_Sort'].values
                first_pod = subset['Pod'].values
                first_strip = subset['Strip'].values
                coach_order_list.append((coach, first_time, first_pod, first_strip))
        
        coach_order_list.sort(key=lambda x: (x, x, x))
        
        for coach_data in coach_order_list:
            coach = coach_data
            is_main = export_df['Coach'] == str(coach)
            is_side = export_df['Side_Coach'] == str(coach)
            subset = export_df[is_main | is_side]
            
            output += f"\n--- {coach.upper()} ---\n"
            for _, row in subset.iterrows():
                if row['Coach'] == coach:
                    side_str = f" (Side: {row['Side_Coach']})" if row['Side_Coach'] != "None" else ""
                    output += f"{row['Time']} | Strip {row['Strip']} | {row['Athlete']}{side_str}\n"
                elif row['Side_Coach'] == coach:
                    main_str = f" (Main: {row['Coach']})" if row['Coach'] != "None" else ""
                    output += f"{row['Time']} | Strip {row['Strip']} | {row['Athlete']} [YOU ARE SIDE]{main_str}\n"
        
        with st.spinner("Generating high quality image..."):
            lines = output.strip().split('\n')
            fig_height = max(4.0, len(lines) * 0.28) 
            
            fig, ax = plt.subplots(figsize=(9, fig_height), facecolor='#0E1117')
            ax.axis('off')
            
            ax.text(0.01, 0.99, output.strip(), 
                    fontsize=13, 
                    color='#FAFAFA', 
                    family='monospace', 
                    verticalalignment='top', 
                    transform=ax.transAxes)
            
            buf = io.BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight', dpi=200, facecolor='#0E1117')
            buf.seek(0)
            
            st.success("Image generated! 👇 Long-press on the image below and tap 'Share' to send via WhatsApp.")
            st.image(buf)
            
            st.download_button("💾 Save Image to Device", buf, file_name="AFM_Schedule.png", mime="image/png")
            
            with st.expander("Show Text Version"):
                st.code(output.strip())
