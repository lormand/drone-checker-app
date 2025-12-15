import streamlit as st
import requests
import json
from datetime import datetime
import pytz
import pandas as pd
from streamlit_geolocation import streamlit_geolocation 

# --- CONFIGURATION (Mavic 3 Pro Limits) ---

LIMITS = {
    'MAX_WIND_SPEED_MPH': 27.0,     # Max continuous wind speed.
    'MAX_GUST_SPEED_MPH': 30.0,
    'MIN_TEMP_F': 14.0,             # -10°C
    'MAX_TEMP_F': 104.0,            # 40°C
    'MIN_VISIBILITY_MILES': 3.0,    # FAA minimum visibility for Part 107
    'MAX_PRECIP_PROB': 0,           # 0% (No water resistance)
    'MAX_KP_INDEX': 5.0,            # Geomagnetic storm threshold (affects GPS lock)
    'WIND_SAFETY_BUFFER': 1.25      # Safety factor for ground wind at altitude
}

# NOTE: Set your local timezone for accurate daylight calculations!
LOCAL_TIMEZONE = 'America/Chicago' 

# --- Utility Functions ---

def degrees_to_cardinal(deg):
    """Converts wind degrees (0-360) to a cardinal direction (N, NE, SW, etc.)."""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNN']
    ix = round(deg / (360. / len(dirs)))
    return dirs[ix % len(dirs)]

# --- API Fetching Functions (100% Free) ---

@st.cache_data(ttl=3600)
def fetch_sunrise_sunset(lat, lon):
    """
    Fetches accurate sunrise and sunset times for the given coordinates (Keyless API).
    Caches result for 1 hour (3600 seconds).
    """
    try:
        ss_url = f"https://api.sunrise-sunset.org/json?lat={lat:.4f}&lng={lon:.4f}&formatted=0"
        response = requests.get(ss_url)
        response.raise_for_status()
        data = response.json()['results']
        
        utc_tz = pytz.utc
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        
        # Convert and localize the sunrise time
        sunrise_utc = datetime.strptime(data['sunrise'], '%Y-%m-%dT%H:%M:%S+00:00').replace(tzinfo=utc_tz)
        sunrise_local = sunrise_utc.astimezone(local_tz).time()
        
        # Convert and localize the sunset time
        sunset_utc = datetime.strptime(data['sunset'], '%Y-%m-%dT%H:%M:%S+00:00').replace(tzinfo=utc_tz)
        sunset_local = sunset_utc.astimezone(local_tz).time()
        
        now_time = datetime.now(local_tz).time()
        is_daylight = (now_time >= sunrise_local and now_time <= sunset_local)
        
        return sunrise_local, sunset_local, is_daylight
        
    except:
        # Fallback to a safe, fixed placeholder if the API fails
        st.warning(f"Sunrise/Sunset API failed. Using fixed 06:00-19:00 window.")
        sunrise_ph = datetime(2000, 1, 1, 6, 0).time() 
        sunset_ph = datetime(2000, 1, 1, 19, 0).time() 
        now_time = datetime.now().time()
        is_daylight = (now_time >= sunrise_ph and now_time <= sunset_ph)
        return sunrise_ph, sunset_ph, is_daylight

@st.cache_data(ttl=300)
def get_nearest_station_id(lat, lon):
    """Uses NWS /points endpoint to find the closest observation station ID (ICAO)."""
    try:
        points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}/stations"
        headers = {'User-Agent': 'MavicProCheckerApp (dronepilot@example.com)'}
        response = requests.get(points_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if 'features' in data and data['features']:
            station_url = data['features'][0]['id']
            icao_code = station_url.split('/')[-1]
            return icao_code
        return None
    except:
        return None

@st.cache_data(ttl=3600)
def fetch_station_name(icao_code):
    """Fetches the human-readable name of the weather station using its ICAO code."""
    try:
        station_url = f"https://api.weather.gov/stations/{icao_code}"
        headers = {'User-Agent': 'MavicProCheckerApp (dronepilot@example.com)'}
        response = requests.get(station_url, headers=headers)
        response.raise_for_status()
        data = response.json()['properties']
        
        # The 'name' field is usually the full airport/station name
        return data.get('name', 'N/A')
    except:
        return 'Unknown Station'

@st.cache_data(ttl=300)
def fetch_metar_data(icao_code):
    """Fetches the latest observation (METAR) from the NWS using the station ID."""
    try:
        metar_url = f"https://api.weather.gov/stations/{icao_code}/observations/latest"
        headers = {'User-Agent': 'MavicProCheckerApp (dronepilot@example.com)'}
        response = requests.get(metar_url, headers=headers)
        response.raise_for_status()
        data = response.json()['properties']
        
        # 1. Temperature: Celsius to Fahrenheit
        temp_c = data['temperature']['value']
        temp_f = (temp_c * 9/5) + 32 if temp_c is not None else 60.0

        # 2. Wind Speed and Gust: m/s to MPH (CORRECTION APPLIED)
        WIND_CONV_FACTOR = 2.23694 # 1 m/s = 2.23694 MPH
        
        wind_speed_ms = data['windSpeed']['value'] if data['windSpeed']['value'] is not None else 0
        wind_gust_ms = data['windGust']['value'] if data['windGust']['value'] is not None else wind_speed_ms
        
        # *** CRITICAL FIX FOR 10X ERROR ***
        wind_speed_ms_corrected = wind_speed_ms / 10.0
        wind_gust_ms_corrected = wind_gust_ms / 10.0
        
        wind_speed_mph = wind_speed_ms_corrected * WIND_CONV_FACTOR 
        wind_gust_mph = wind_gust_ms_corrected * WIND_CONV_FACTOR
        
        wind_dir_deg = data['windDirection']['value'] if data['windDirection']['value'] is not None else 0

        # 3. Visibility: Meters to Miles
        visibility_meters = data['visibility']['value']
        visibility_miles = visibility_meters / 1609.34 if visibility_meters is not None else 10.0

        # 4. Precipitation Check
        present_weather = data['textDescription'] if data.get('textDescription') else ""
        precip_risk = 100 if any(word in present_weather.lower() for word in ['rain', 'snow', 'drizzle', 'thunder', 'fog']) else 0
        
        return {
            'icao_code': icao_code,
            'wind_speed': wind_speed_mph,
            'wind_gust': wind_gust_mph,
            'wind_direction_deg': wind_dir_deg,
            'temp_f': temp_f,
            'visibility_miles': visibility_miles,
            'precip_prob': precip_risk,
            'text_description': present_weather,
        }

    except:
        # Return a dictionary with safe defaults on API failure
        return None

@st.cache_data(ttl=3600)
def fetch_kp_index():
    """Fetches the latest OBSERVED Kp Index from NOAA SWPC (free, no key, JSON format)."""
    kp_url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
    
    try:
        response = requests.get(kp_url)
        response.raise_for_status()
        data = response.json()
        
        for row in reversed(data[1:]): 
            time_tag, kp_value_str, status, noaa_scale = row
            
            if status == "observed":
                latest_kp = float(kp_value_str)
                return latest_kp
        return 0.0
        
    except:
        return 0.0

# --- CORE MAVIC 3 PRO LOGIC ---

def check_flight_status(weather_data, kp_index, is_daylight):
    """Applies all hard weather limits for the Mavic 3 Pro."""
    
    reasons_to_ground = []
    
    # Safely extract values for logic, ensuring calculation against 0.0 if missing
    wind_speed_raw = weather_data.get('wind_speed', 0.0) 
    wind_gust_raw = weather_data.get('wind_gust', 0.0)
    temp_f = weather_data.get('temp_f', 60.0)
    visibility_miles = weather_data.get('visibility_miles', 10.0)
    precip_prob = weather_data.get('precip_prob', 0)
    
    # 1. Wind Check (Applying the safety buffer for altitude)
    actual_wind = wind_speed_raw * LIMITS['WIND_SAFETY_BUFFER']
    actual_gust = wind_gust_raw * LIMITS['WIND_SAFETY_BUFFER']
    
    if actual_wind > LIMITS['MAX_WIND_SPEED_MPH']:
        reasons_to_ground.append(f"🌬️ Wind exceeds limit: {actual_wind:.1f} MPH (Max: {LIMITS['MAX_WIND_SPEED_MPH']} MPH)")
    if actual_gust > LIMITS['MAX_GUST_SPEED_MPH']:
        reasons_to_ground.append(f"💨 Gusts too dangerous: {actual_gust:.1f} MPH (Max: {LIMITS['MAX_GUST_SPEED_MPH']} MPH)")

    # 2. Temperature Check
    if temp_f < LIMITS['MIN_TEMP_F'] or temp_f > LIMITS['MAX_TEMP_F']:
        reasons_to_ground.append(f"🌡️ Temperature is unsafe: {temp_f:.1f}°F")

    # 3. Moisture and Visibility Check
    if precip_prob > LIMITS['MAX_PRECIP_PROB']:
        reasons_to_ground.append(f"💧 Precipitation risk ({weather_data.get('text_description', 'N/A')}). Mavic 3 is not waterproof!")

    if visibility_miles < LIMITS['MIN_VISIBILITY_MILES']:
        reasons_to_ground.append(f"🌫️ Visibility too low: {visibility_miles:.1f} miles")
    
    # 4. Night Flight Check (based on accurate sunrise/sunset)
    if not is_daylight:
        reasons_to_ground.append("🌙 Flying outside of daylight hours (requires proper certification & lights)")

    # 5. Satellite/GPS Check (Kp Index)
    if kp_index >= LIMITS['MAX_KP_INDEX']:
        reasons_to_ground.append(f"🛰️ High Solar Storm activity (Kp {kp_index:.1f}). GPS instability possible.")

    if len(reasons_to_ground) > 0:
        return "DON'T FLY", reasons_to_ground
    else:
        return "READY TO LAUNCH", ["All weather and space weather conditions are optimal."]


# --- PANDAS/STYLING FUNCTIONS FOR MOBILE TABLE ---

def create_styled_dataframe(data, limits, is_daylight, kp_index, station_name, icao_code):
    """Creates a mobile-friendly, color-coded Pandas DataFrame with a 'Pass/Fail' column."""
    
    # Extract adjusted values
    wind_speed_adjusted = data.get('wind_speed', 0.0) * limits['WIND_SAFETY_BUFFER']
    wind_gust_adjusted = data.get('wind_gust', 0.0) * limits['WIND_SAFETY_BUFFER']
    temp_f = data.get('temp_f', 60.0)
    visibility_miles = data.get('visibility_miles', 10.0)
    precip_prob = data.get('precip_prob', 0)
    wind_dir_deg = data.get('wind_direction_deg', 0)
    wind_dir_cardinal = degrees_to_cardinal(wind_dir_deg)

    # Helper function to return 'FAIL' or 'PASS'
    def get_status(condition):
        return 'FAIL' if condition else 'PASS'

    # 1. Define the DataFrame structure (New 'Status' column)
    df_data = [
        # Parameter | Current Value | Safe Limit | Status ('PASS'/'FAIL'/'Info')
        
        ['Wind Speed (Adjusted)', f"{wind_speed_adjusted:.1f} MPH", f"≤ {limits['MAX_WIND_SPEED_MPH']} MPH", get_status(wind_speed_adjusted > limits['MAX_WIND_SPEED_MPH'])],
        
        ['Wind Gust (Adjusted)', f"{wind_gust_adjusted:.1f} MPH", f"≤ {limits['MAX_GUST_SPEED_MPH']} MPH", get_status(wind_gust_adjusted > limits['MAX_GUST_SPEED_MPH'])],
        
        # Info rows: Set to 'Info' status
        ['Wind Direction', f"{wind_dir_cardinal} ({wind_dir_deg:.0f}°)", "Info (Variable)", 'Info'],
        
        ['Temperature', f"{temp_f:.1f} °F", f"{limits['MIN_TEMP_F']}-{limits['MAX_TEMP_F']} °F", get_status(temp_f < limits['MIN_TEMP_F'] or temp_f > limits['MAX_TEMP_F'])],
        
        ['Visibility', f"{visibility_miles:.1f} miles", f"≥ {limits['MIN_VISIBILITY_MILES']} miles", get_status(visibility_miles < limits['MIN_VISIBILITY_MILES'])],
        
        ['Precipitation Risk', f"{precip_prob:.0f}%", f"≤ {limits['MAX_PRECIP_PROB']}% (No water)", get_status(precip_prob > limits['MAX_PRECIP_PROB'])],
        
        ['Kp Index (GPS Risk)', f"{kp_index:.1f}", f"≤ {limits['MAX_KP_INDEX']} Kp", get_status(kp_index >= limits['MAX_KP_INDEX'])],
        
        ['Daylight Status', "Daytime" if is_daylight else "Nighttime", "Daylight Only", get_status(not is_daylight)],
        
        # Info rows: Set to 'Info' status
        ['Weather Station', f"{station_name} [{icao_code}]", "NWS Data Source", 'Info']
    ]

    # Create the DataFrame and rename the 'Status' column for display
    df = pd.DataFrame(df_data, columns=['Parameter', 'Current Value', 'Safe Limit', 'Status']).rename(
        columns={'Status': 'Pass/Fail'}
    )
    
    # 2. Define the styling function
    def color_status(s):
        """Applies red or green background based on the 'Pass/Fail' column."""
        DARK_TEXT_COLOR = 'color: #31333F' 
        
        status = s['Pass/Fail']
        
        if status == 'FAIL':
            # FAIL (Red background, dark text)
            return [f'background-color: #ffcccc; {DARK_TEXT_COLOR}'] * len(s) 
        elif status == 'Info':
            # Info rows: No color for uniformity as non-Go/No-Go items
            return [''] * len(s) 
        else:
            # PASS (Green background, dark text)
            return [f'background-color: #ccffcc; {DARK_TEXT_COLOR}'] * len(s) 

    # 3. Apply the styling
    # Define a style for the table header (thead)
    header_styles = [
        {'selector': 'thead', 
         'props': [('background-color', '#555555'), 
                   ('color', '#FFFFFF'),
                   ('font-weight', 'bold')]}
    ]
    
    # Apply all styling: hide index, set row properties, apply header style, and convert to HTML
    styled_df = df.style.apply(color_status, axis=1).hide(axis="index").set_properties(
        **{'font-size': '14pt', 'padding': '8px'} 
    ).set_table_styles(header_styles).to_html() 
    
    # 4. Manually insert the tooltip (<abbr> tag) into the HTML
    ADJUSTED_TITLE_HTML = "title='The raw wind speed is increased by 25% (x1.25) to account for wind shear and increased turbulence at altitude (400ft AGL). This provides a critical safety buffer.'"
    
    styled_df = styled_df.replace(
        'Wind Speed (Adjusted)', 
        f"<abbr {ADJUSTED_TITLE_HTML}>Wind Speed (Adjusted)</abbr>"
    )
    styled_df = styled_df.replace(
        'Wind Gust (Adjusted)', 
        f"<abbr {ADJUSTED_TITLE_HTML}>Wind Gust (Adjusted)</abbr>"
    )

    return styled_df


# --- STREAMLIT UI ---

st.set_page_config(
    page_title="Mavic 3 Pro Flight Checker",
    page_icon="🚁",
    layout="wide"
)

st.title("🚁 Mavic 3 Pro Flight Checker")
st.subheader("Zero-Cost Pre-Flight Safety Check")
st.markdown("---")

location = streamlit_geolocation()

if location is not None and location.get('latitude') is not None:
    lat = location['latitude']
    lon = location['longitude']
    
    st.info(f"📍 Current Location: Latitude {lat:.4f}, Longitude {lon:.4f}")
    
    if st.button("Run Comprehensive Flight Check", type="primary"):
        with st.spinner('Fetching NWS Weather and Kp Index...'):
            
            # 1. Fetch Kp Index
            kp_index = fetch_kp_index()
            
            # 2. Fetch Accurate Sunrise/Sunset Times
            sunrise_local, sunset_local, is_daylight = fetch_sunrise_sunset(lat, lon) 
            
            # 3. Fetch Weather Data (Code only)
            icao_code = get_nearest_station_id(lat, lon)
            
            # --- Aggregated Logic ---
            if icao_code:
                # 4. Fetch Station Name
                station_name = fetch_station_name(icao_code)
                
                weather_data = fetch_metar_data(icao_code) or {} 
                
                # Check weather and Kp limits
                status, weather_reasons = check_flight_status(weather_data, kp_index, is_daylight)
                
                all_reasons = weather_reasons
                
                # --- Binary Decision Logic (GO/NO-GO) ---
                if status == "DON'T FLY":
                    final_status = "DON'T FLY"
                    banner_color = "error" # RED
                else:
                    final_status = "READY TO LAUNCH"
                    banner_color = "success" # GREEN
                    
                # --- Display Final Result ---
                st.header(final_status)
                
                # --- Display Status with Station Name ---
                if banner_color == "success":
                    st.success(f"✅ GO! Conditions are favorable. Weather from **{station_name} [{icao_code}]**.")
                    st.balloons()
                else:
                    st.error(f"❌ NO GO. Check reasons below. Weather from **{station_name} [{icao_code}]**.")
                
                # --- Persistent Airspace Warning (Revised for Accuracy) ---
                st.warning("⚠️ CRITICAL REMINDER: Airspace requirements MUST be verified before flight. Authorization is mandatory in all Controlled Airspace (Class B, C, D, and surface E). You MUST check the official **Air Control** app or a LAANC provider (Aloft, Airspace Link, etc.) to confirm your local airspace status.")

                # --- Mobile-Optimized Dataframe Display ---
                st.markdown("### 📊 Detailed Conditions")
                
                # Generate and display the styled HTML table 
                styled_html_table = create_styled_dataframe(weather_data, LIMITS, is_daylight, kp_index, station_name, icao_code)
                
                # RENDER THE HTML TABLE (Streamlit's fastest and most consistent table method for mobile)
                st.markdown(styled_html_table, unsafe_allow_html=True)

                st.markdown("---")
                # Sunlight window reminder:
                st.markdown(f"**☀️ Sunlight Window:** {sunrise_local.strftime('%I:%M %p')} to {sunset_local.strftime('%I:%M %p')} ({LOCAL_TIMEZONE} Time)")

                if all_reasons:
                    st.markdown("### 🛑 Reasons for Grounding:")
                    for reason in all_reasons:
                        st.warning(f"{reason}")
            else:
                st.warning("Could not find a nearby weather-reporting airport.")
else:
    st.info("Click the button above to allow the app to access your location and run the check.")

st.markdown("---")
st.caption("Disclaimer: This tool is for flight planning only. Always confirm safety, battery, and LAANC authorization manually.")
