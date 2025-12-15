import streamlit as st
import requests
import json
from datetime import datetime
import pytz
import pandas as pd
from streamlit_geolocation import streamlit_geolocation 

# --- DRONE CONFIGURATION ---

# Max Wind Resistance values converted to MPH and used as the initial MAX_WIND_SPEED_MPH.
# MAX_GUST_SPEED_MPH is set slightly higher for a buffer.
DRONE_MODELS = {
    "DJI Mavic 3 Pro (Selected)": {
        'MAX_WIND_SPEED_MPH': 26.8,     
        'MAX_GUST_SPEED_MPH': 30.0,
        'MIN_TEMP_F': 14.0,           
        'MAX_TEMP_F': 104.0,           
        'DRONE_DISPLAY_NAME': "DJI Mavic 3 Pro"
    },
    "DJI Air 3": {
        'MAX_WIND_SPEED_MPH': 26.8,     
        'MAX_GUST_SPEED_MPH': 30.0,
        'MIN_TEMP_F': 14.0,           
        'MAX_TEMP_F': 104.0,           
        'DRONE_DISPLAY_NAME': "DJI Air 3"
    },
    "DJI Mini 4 Pro (Lightweight)": {
        'MAX_WIND_SPEED_MPH': 24.0,     # Max wind resistance is 10.7 m/s (24.0 mph)
        'MAX_GUST_SPEED_MPH': 26.0,
        'MIN_TEMP_F': 14.0,           
        'MAX_TEMP_F': 104.0,           
        'DRONE_DISPLAY_NAME': "DJI Mini 4 Pro"
    },
    # Add a generic, strict profile for unknown drones
    "General Part 107 (Strict)": {
        'MAX_WIND_SPEED_MPH': 15.0,     # Very safe, conservative value
        'MAX_GUST_SPEED_MPH': 17.0,
        'MIN_TEMP_F': 20.0,           
        'MAX_TEMP_F': 100.0,          
        'DRONE_DISPLAY_NAME': "General Drone (Strict)"
    }
}

# --- STATIC LEGAL/SAFETY LIMITS (Applied to all drones) ---
STATIC_LIMITS = {
    'MIN_VISIBILITY_MILES': 3.0,    # FAA minimum visibility for Part 107
    'MAX_PRECIP_PROB': 100,         # Handled by Warning, not Fail Status
    'MAX_KP_INDEX': 5.0,            
    'WIND_SAFETY_BUFFER': 1.25,     
    'MIN_CLOUD_BASE_FT': 900,       
    'DRONE_DISPLAY_NAME': "N/A" # Placeholder, overwritten by selected model
}

# NOTE: Set your local timezone for accurate daylight calculations!
LOCAL_TIMEZONE = 'America/Chicago' 

# --- Utility Functions (Unchanged) ---
# (degrees_to_cardinal is omitted for brevity but remains in app.py)

# --- API Fetching Functions (Unchanged) ---
# (fetch_sunrise_sunset, get_nws_forecast_url, fetch_hourly_forecast, fetch_kp_index are omitted for brevity but remain in app.py)


# --- CORE FLIGHT CHECK LOGIC (Now accepts a dynamic 'limits' dictionary) ---

def check_flight_status(weather_data, limits, kp_index, is_daylight):
    """Applies all hard weather limits (dynamic and static) for the selected drone and Part 107 rules."""
    
    reasons_to_ground = []
    
    # Safely extract values for logic, ensuring calculation against 0.0 if missing
    wind_speed_raw = weather_data.get('wind_speed', 0.0) 
    wind_gust_raw = weather_data.get('wind_gust', 0.0)
    temp_f = weather_data.get('temp_f', 60.0)
    visibility_miles = weather_data.get('visibility_miles', 10.0)
    cloud_base_ft = weather_data.get('cloud_base_ft', 5000) 
    
    # 1. Wind Check (Applying the safety buffer for altitude)
    actual_wind = wind_speed_raw * limits['WIND_SAFETY_BUFFER']
    actual_gust = wind_gust_raw * limits['WIND_SAFETY_BUFFER']
    
    if actual_wind > limits['MAX_WIND_SPEED_MPH']:
        reasons_to_ground.append(f"🌬️ Wind exceeds limit: {actual_wind:.1f} MPH (Max: {limits['MAX_WIND_SPEED_MPH']} MPH)")
    if actual_gust > limits['MAX_GUST_SPEED_MPH']:
        reasons_to_ground.append(f"💨 Gusts too dangerous: {actual_gust:.1f} MPH (Max: {limits['MAX_GUST_SPEED_MPH']} MPH)")

    # 2. Temperature Check
    if temp_f < limits['MIN_TEMP_F'] or temp_f > limits['MAX_TEMP_F']:
        reasons_to_ground.append(f"🌡️ Temperature is unsafe: {temp_f:.1f}°F")

    # 3. Visibility Check (Static Limit)
    if visibility_miles < limits['MIN_VISIBILITY_MILES']:
        reasons_to_ground.append(f"🌫️ Visibility too low: {visibility_miles:.1f} miles (FAA Minimum: {limits['MIN_VISIBILITY_MILES']} mi)")
    
    # 4. Cloud Check (Static Limit - Part 107)
    if cloud_base_ft < limits['MIN_CLOUD_BASE_FT']:
        max_safe_alt = max(0, int(cloud_base_ft - 500))
        reasons_to_ground.append(f"☁️ Cloud Base Altitude ({cloud_base_ft} ft) is too low. Max safe flight altitude is {max_safe_alt} ft AGL (500ft clearance required by Part 107).")
    
    # 5. Night Flight Check (Static Limit - Part 107)
    if not is_daylight:
        reasons_to_ground.append("🌙 Flying outside of daylight hours (requires proper certification & lights)")

    # 6. Satellite/GPS Check (Static Limit)
    if kp_index >= limits['MAX_KP_INDEX']:
        reasons_to_ground.append(f"🛰️ High Solar Storm activity (Kp {kp_index:.1f}). GPS instability possible.")

    if len(reasons_to_ground) > 0:
        return "DON'T FLY", reasons_to_ground
    else:
        return "READY TO LAUNCH", ["All weather and space weather conditions are optimal."]


# --- PANDAS/STYLING FUNCTIONS (Now accepts a dynamic 'limits' dictionary) ---

def create_styled_dataframe(data, limits, is_daylight, kp_index):
    """Creates a mobile-friendly, color-coded Pandas DataFrame with a 'Pass/Fail' column."""
    
    # Extract adjusted values
    wind_speed_adjusted = data.get('wind_speed', 0.0) * limits['WIND_SAFETY_BUFFER']
    wind_gust_adjusted = data.get('wind_gust', 0.0) * limits['WIND_SAFETY_BUFFER']
    temp_f = data.get('temp_f', 60.0)
    visibility_miles = data.get('visibility_miles', 10.0)
    precip_prob = data.get('precip_prob', 0)
    cloud_base_ft = data.get('cloud_base_ft', 5000)
    short_forecast = data.get('text_description', 'N/A')
    cloud_cover = data.get('cloud_cover', 0) 

    # Helper function to return 'FAIL' or 'PASS'
    def get_status(condition):
        return 'FAIL' if condition else 'PASS'
    
    # Precipitation Status Logic: NEVER FAIL, only PASS or Info
    precip_status = 'Info' if precip_prob > 0 else 'PASS'

    # 1. Define the DataFrame structure (New 'Status' column)
    df_data = [
        # Wind Checks (Dynamic Limits)
        ['Wind Speed (Adjusted)', f"{wind_speed_adjusted:.1f} MPH", f"≤ {limits['MAX_WIND_SPEED_MPH']} MPH", get_status(wind_speed_adjusted > limits['MAX_WIND_SPEED_MPH'])],
        ['Wind Gust (Adjusted)', f"{wind_gust_adjusted:.1f} MPH", f"≤ {limits['MAX_GUST_SPEED_MPH']} MPH", get_status(wind_gust_adjusted > limits['MAX_GUST_SPEED_MPH'])],
        
        # Detailed Weather Info
        ['Current Conditions', short_forecast, "Info (Variable)", 'Info'],
        
        # Precipitation (Info only)
        ['Precipitation Probability', f"{precip_prob:.0f}%", "Hardware Limit (Non-Waterproof)", precip_status],
        
        # Temperature/Visibility (Dynamic Temp, Static Vis)
        ['Temperature', f"{temp_f:.1f} °F", f"{limits['MIN_TEMP_F']}-{limits['MAX_TEMP_F']} °F", get_status(temp_f < limits['MIN_TEMP_F'] or temp_f > limits['MAX_TEMP_F'])],
        ['Visibility (Estimated)', f"{visibility_miles:.1f} miles", f"≥ {limits['MIN_VISIBILITY_MILES']} miles", get_status(visibility_miles < limits['MIN_VISIBILITY_MILES'])],
        
        # Cloud Checks (Static Limit)
        ['Cloud Base Altitude (AGL)', f"{cloud_base_ft:.0f} ft", f"≥ {limits['MIN_CLOUD_BASE_FT']} ft (500ft clearance)", get_status(cloud_base_ft < limits['MIN_CLOUD_BASE_FT'])],
        ['Cloud Cover %', f"{cloud_cover:.0f}%", "Info (Overhead)", 'Info'], 
        
        # GPS/Daylight (Static Limits)
        ['Kp Index (GPS Risk)', f"{kp_index:.1f}", f"≤ {limits['MAX_KP_INDEX']} Kp", get_status(kp_index >= limits['MAX_KP_INDEX'])],
        ['Daylight Status', "Daytime" if is_daylight else "Nighttime", "Daylight Only", get_status(not is_daylight)],
    ]

    # Create the DataFrame and apply styling (Styling functions are omitted for brevity but remain in app.py)
    df = pd.DataFrame(df_data, columns=['Parameter', 'Current Value', 'Safe Limit', 'Status']).rename(
        columns={'Status': 'Pass/Fail'}
    )
    
    # ... rest of styling code (unchanged)
    
    return df.style.apply(lambda s: ['background-color: #ffcccc; color: #31333F'] * len(s) if s['Pass/Fail'] == 'FAIL' else ['background-color: #ccffcc; color: #31333F'] * len(s), axis=1).hide(axis="index").to_html()

# --- STREAMLIT UI (Modified to include drone selection) ---

st.set_page_config(
    page_title="Multi-Drone Flight Safety Checker",
    page_icon="🚁",
    layout="wide"
)

st.title("🚁 Multi-Drone Flight Safety Checker")
st.subheader("Zero-Cost Pre-Flight Check for Popular DJI Models")
st.markdown("---")

# 1. Drone Selection
st.sidebar.markdown("## ⚙️ Drone Selection")
drone_options = list(DRONE_MODELS.keys())
selected_drone_key = st.sidebar.selectbox(
    "Select Your Drone Model:",
    drone_options,
    index=0 # Default to Mavic 3 Pro
)

# 2. Dynamic Limit Assembly
DRONE_LIMITS = DRONE_MODELS[selected_drone_key]
FINAL_LIMITS = {**DRONE_LIMITS, **STATIC_LIMITS}
DRONE_DISPLAY_NAME = DRONE_LIMITS['DRONE_DISPLAY_NAME']

# Update the main heading to reflect the selected drone
st.markdown(f"### Current Model: **{DRONE_DISPLAY_NAME}**")
st.markdown("---")


location = streamlit_geolocation()

if location is not None and location.get('latitude') is not None:
    lat = location['latitude']
    lon = location['longitude']
    
    st.info(f"📍 Current Location: Latitude {lat:.4f}, Longitude {lon:.4f}")
    
    if st.button(f"Run Comprehensive Flight Check for {DRONE_DISPLAY_NAME}", type="primary"):
        with st.spinner('Fetching NWS Hourly Forecast and Kp Index...'):
            
            # 1. Fetch Kp Index
            kp_index = fetch_kp_index()
            
            # 2. Fetch Accurate Sunrise/Sunset Times
            sunrise_local, sunset_local, is_daylight = fetch_sunrise_sunset(lat, lon) 
            
            # 3. Get the Forecast URL
            forecast_url = get_nws_forecast_url(lat, lon)

            # --- Aggregated Logic ---
            if forecast_url:
                # 4. Fetch Hourly Forecast Data
                weather_data = fetch_hourly_forecast(forecast_url) or {} 
                precip_prob = weather_data.get('precip_prob', 0)
                
                # Check weather and Kp limits using the FINAL_LIMITS dictionary
                status, weather_reasons = check_flight_status(weather_data, FINAL_LIMITS, kp_index, is_daylight)
                
                # ... (rest of the display logic is similar to before)
                
                # --- Binary Decision Logic (GO/NO-GO) ---
                if status == "DON'T FLY":
                    final_status = "DON'T FLY"
                    banner_color = "error" # RED
                else:
                    final_status = "READY TO LAUNCH"
                    banner_color = "success" # GREEN
                    
                # --- Display Final Result ---
                st.header(final_status)
                
                # --- Display Status ---
                if banner_color == "success":
                    st.success(f"✅ GO! Conditions are favorable for the **{DRONE_DISPLAY_NAME}**.")
                    st.balloons()
                else:
                    st.error(f"❌ NO GO. Check reasons below for the **{DRONE_DISPLAY_NAME}**.")
                
                # --- CRITICAL PRECIPITATION WARNING ---
                if precip_prob > 0:
                    st.warning(f"⚠️ **HARDWARE RISK:** Precipitation Probability is **{precip_prob:.0f}%**. The {DRONE_DISPLAY_NAME} is NOT waterproof and flight is highly discouraged.")
                
                # --- Persistent Airspace Warning (Revised for Accuracy) ---
                st.warning("⚠️ CRITICAL REMINDER: Airspace requirements MUST be verified before flight. Authorization is mandatory in all Controlled Airspace (Class B, C, D, and surface E). You MUST check the official **Air Control** app or a LAANC provider (Aloft, Airspace Link, etc.) to confirm your local airspace status.")

                # --- Mobile-Optimized Dataframe Display ---
                st.markdown("### 📊 Detailed Conditions")
                
                # Generate and display the styled HTML table 
                styled_html_table = create_styled_dataframe(weather_data, FINAL_LIMITS, is_daylight, kp_index)
                
                # RENDER THE HTML TABLE
                st.markdown(styled_html_table, unsafe_allow_html=True)

                st.markdown("---")
                # Sunlight window reminder:
                st.markdown(f"**☀️ Sunlight Window:** {sunrise_local.strftime('%I:%M %p')} to {sunset_local.strftime('%I:%M %p')} ({LOCAL_TIMEZONE} Time)")

                if all_reasons:
                    st.markdown("### 🛑 Reasons for Grounding:")
                    for reason in all_reasons:
                        st.warning(f"{reason}")
            else:
                st.error("Could not retrieve NWS hourly forecast data for your location.")
else:
    st.info("Click the button above to allow the app to access your location and run the check.")

st.markdown("---")
st.caption("Disclaimer: This tool is for flight planning only. Always confirm safety, battery, and LAANC authorization manually.")

The application now features a sidebar drone selector and dynamically adjusts the wind and temperature checks based on the model chosen.

Would you like me to look up any other drone models to add to the selection list?

You can watch [I LOST CONTROL OF DJI AIR 3 DRONE IN HIGH WIND](https://www.youtube.com/watch?v=uKYzQy5v004) to see the practical implications of flying near or above a drone's maximum wind resistance limits, which are now dynamically applied in the safety checker.


http://googleusercontent.com/youtube_content/3
