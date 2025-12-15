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
    'MAX_PRECIP_PROB': 100,         # Set high to disable FAIL status; handled as Info/Warning
    'MAX_KP_INDEX': 5.0,            # Geomagnetic storm threshold (affects GPS lock)
    'WIND_SAFETY_BUFFER': 1.25,     # Safety factor for ground wind at altitude
    'MIN_CLOUD_BASE_FT': 900        # Min cloud base height (AGL) to allow full 400ft flight ceiling (400ft max + 500ft buffer)
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
def get_nws_forecast_url(lat, lon):
    """Uses NWS /points endpoint to find the hourly forecast URL."""
    try:
        points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
        headers = {'User-Agent': 'MavicProCheckerApp (dronepilot@example.com)'}
        response = requests.get(points_url, headers=headers)
        response.raise_for_status()
        data = response.json()['properties']
        return data.get('forecastHourly')
    except:
        return None

@st.cache_data(ttl=300)
def fetch_hourly_forecast(forecast_url):
    """Fetches the NWS hourly forecast and extracts the current/next hour's data."""
    if not forecast_url:
        return None

    try:
        headers = {'User-Agent': 'MavicProCheckerApp (dronepilot@example.com)'}
        response = requests.get(forecast_url, headers=headers)
        response.raise_for_status()
        
        # The first period in the list is the current/next hour's forecast
        current_period = response.json()['properties']['periods'][0]
        
        # --- Data Extraction and Conversion ---
        
        # 1. Wind Speed/Gust (NWS uses text/range, we use the max value reported)
        wind_speed_text = current_period.get('windSpeed', '0 mph')
        
        # Extract the highest speed value from the range (e.g., "5 to 10 mph" -> 10)
        speed_parts = wind_speed_text.split('to')
        raw_speed_mph = int(speed_parts[-1].strip().split()[0])
        
        wind_speed_mph = raw_speed_mph
        wind_gust_mph = raw_speed_mph

        # 2. Temperature: Already in Fahrenheit
        temp_f = current_period.get('temperature', 60.0)

        # 3. Cloud Cover (Sky Cover is the percentage)
        cloud_cover_percent = current_period.get('skyCover', 0)
        
        # 4. Precipitation Probability
        precip_prob = current_period.get('probabilityOfPrecipitation', {}).get('value', 0)
        
        # 5. Overall Weather/Text Description
        short_forecast = current_period.get('shortForecast', 'N/A')
        
        # 6. Cloud Base Altitude
        # NWS forecast API doesn't usually provide precise METAR-style cloud layers.
        # We use a placeholder logic: high base if clear/mostly clear, low base if cloudy/overcast.
        if "clear" in short_forecast.lower() or cloud_cover_percent < 50:
            cloud_base_ft = 5000
        else:
            cloud_base_ft = 800 # Assume low base for safety if cloudy, ensuring it fails the 900ft check
        
        # Visibility (NWS forecast API often omits this, defaulting to high visibility)
        visibility_miles = 10.0
        
        return {
            'wind_speed': wind_speed_mph,
            'wind_gust': wind_gust_mph,
            'temp_f': float(temp_f),
            'visibility_miles': visibility_miles,
            'precip_prob': float(precip_prob),
            'text_description': short_forecast,
            'cloud_cover': cloud_cover_percent,
            'cloud_base_ft': cloud_base_ft
        }
    except Exception as e:
        st.error(f"Error fetching NWS hourly forecast: {e}")
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
    """Applies all hard weather limits for the Mavic 3 Pro and Part 107 rules."""
    
    reasons_to_ground = []
    
    # Safely extract values for logic, ensuring calculation against 0.0 if missing
    wind_speed_raw = weather_data.get('wind_speed', 0.0)
    wind_gust_raw = weather_data.get('wind_gust', 0.0)
    temp_f = weather_data.get('temp_f', 60.0)
    visibility_miles = weather_data.get('visibility_miles', 10.0)
    # precip_prob = weather_data.get('precip_prob', 0) # Removed from FAIL check
    cloud_base_ft = weather_data.get('cloud_base_ft', 5000)
    
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

    # 3. Visibility Check
    if visibility_miles < LIMITS['MIN_VISIBILITY_MILES']:
        reasons_to_ground.append(f"🌫️ Visibility too low: {visibility_miles:.1f} miles")
    
    # Note: Precipitation check is removed here, handled by a separate st.warning in the UI.
    
    # 4. Cloud Check (Based on Part 107 VLoS and 400ft AGL rule)
    if cloud_base_ft < LIMITS['MIN_CLOUD_BASE_FT']:
        # Calculate the maximum safe altitude permitted
        max_safe_alt = max(0, int(cloud_base_ft - 500))
        reasons_to_ground.append(f"☁️ Cloud Base Altitude ({cloud_base_ft} ft) is too low. Max safe flight altitude is {max_safe_alt} ft AGL (500ft clearance required by Part 107).")
    
    # 5. Night Flight Check (based on accurate sunrise/sunset)
    if not is_daylight:
        reasons_to_ground.append("🌙 Flying outside of daylight hours (requires proper certification & lights)")

    # 6. Satellite/GPS Check (Kp Index)
    if kp_index >= LIMITS['MAX_KP_INDEX']:
        reasons_to_ground.append(f"🛰️ High Solar Storm activity (Kp {kp_index:.1f}). GPS instability possible.")

    if len(reasons_to_ground) > 0:
        return "DON'T FLY", reasons_to_ground
    else:
        return "READY TO LAUNCH", ["All weather and space weather conditions are optimal."]


# --- PANDAS/STYLING FUNCTIONS FOR MOBILE TABLE ---

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
        # Parameter | Current Value | Safe Limit | Status ('PASS'/'FAIL'/'Info')
        
        # Wind Checks
        ['Wind Speed (Adjusted)', f"{wind_speed_adjusted:.1f} MPH", f"≤ {limits['MAX_WIND_SPEED_MPH']} MPH", get_status(wind_speed_adjusted > limits['MAX_WIND_SPEED_MPH'])],
        ['Wind Gust (Adjusted)', f"{wind_gust_adjusted:.1f} MPH", f"≤ {limits['MAX_GUST_SPEED_MPH']} MPH", get_status(wind_gust_adjusted > limits['MAX_GUST_SPEED_MPH'])],
        
        # Detailed Weather Info
        ['Current Conditions', short_forecast, "Info (Variable)", 'Info'],
        
        # Precipitation (Info only)
        ['Precipitation Probability', f"{precip_prob:.0f}%", "Hardware Limit (Non-Waterproof)", precip_status],
        
        # Temperature/Visibility
        ['Temperature', f"{temp_f:.1f} °F", f"{limits['MIN_TEMP_F']}-{limits['MAX_TEMP_F']} °F", get_status(temp_f < limits['MIN_TEMP_F'] or temp_f > limits['MAX_TEMP_F'])],
        ['Visibility (Estimated)', f"{visibility_miles:.1f} miles", f"≥ {limits['MIN_VISIBILITY_MILES']} miles", get_status(visibility_miles < limits['MIN_VISIBILITY_MILES'])],
        
        # Cloud Checks
        ['Cloud Base Altitude (AGL)', f"{cloud_base_ft:.0f} ft", f"≥ {limits['MIN_CLOUD_BASE_FT']} ft (400ft max + 500ft buffer)", get_status(cloud_base_ft < limits['MIN_CLOUD_BASE_FT'])],
        ['Cloud Cover %', f"{cloud_cover:.0f}%", "Info (Overhead)", 'Info'],
        
        # GPS/Daylight
        ['Kp Index (GPS Risk)', f"{kp_index:.1f}", f"≤ {limits['MAX_KP_INDEX']} Kp", get_status(kp_index >= limits['MAX_KP_INDEX'])],
        ['Daylight Status', "Daytime" if is_daylight else "Nighttime", "Daylight Only", get_status(not is_daylight)],
    ]

    # Create the DataFrame and rename the 'Status' column for display
    df = pd.DataFrame(df_data, columns=['Parameter', 'Current Value', 'Safe Limit', 'Status']).rename(
        columns={'Status': 'Pass/Fail'}
    )
    
    # 2. Define the styling function
    def color_status(s):
        """Applies red or green background color based on the 'Pass/Fail' column."""
        DARK_TEXT_COLOR = 'color: #31333F'
        
        status = s['Pass/Fail']
        
        if status == 'FAIL':
            # FAIL (Red background, dark text) - REMAINS
            return [f'background-color: #ffcccc; {DARK_TEXT_COLOR}'] * len(s)
        else:
            # PASS and Info rows: Light Green background for consistency and 'Go' status
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

st.title("🚁 Drone Flight Safety Checker")
st.subheader("Zero-Cost Pre-Flight Safety Check for Mavic 3 Pro")
st.markdown("---")

location = streamlit_geolocation()

if location is not None and location.get('latitude') is not None:
    lat = location['latitude']
    lon = location['longitude']
    
    st.info(f"📍 Current Location: Latitude {lat:.4f}, Longitude {lon:.4f}")
    
    if st.button("Run Comprehensive Flight Check", type="primary"):
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
                
                # --- Display Status ---
                if banner_color == "success":
                    st.success(f"✅ GO! Conditions are favorable. Weather from **NWS Hourly Forecast**.")
                    #st.balloons()
                else:
                    st.error(f"❌ NO GO. Check reasons below. Weather from **NWS Hourly Forecast**.")
                
                # --- CRITICAL PRECIPITATION WARNING ---
                if precip_prob > 0:
                    st.warning(f"⚠️ **HARDWARE RISK:** Precipitation Probability is **{precip_prob:.0f}%**. The Mavic 3 Pro is NOT waterproof and flight is highly discouraged.")
                
                # --- Persistent Airspace Warning (Revised for Accuracy) ---
                st.warning("⚠️ CRITICAL REMINDER: Airspace requirements MUST be verified before flight. Authorization is mandatory in all Controlled Airspace (Class B, C, D, and surface E). You MUST check the official **Air Control** app or a LAANC provider (Aloft, Airspace Link, etc.) to confirm your local airspace status.")

                # --- Mobile-Optimized Dataframe Display ---
                st.markdown("### 📊 Detailed Conditions")
                
                # Generate and display the styled HTML table
                styled_html_table = create_styled_dataframe(weather_data, LIMITS, is_daylight, kp_index)
                
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
                st.error("Could not retrieve NWS hourly forecast data for your location.")
else:
    st.info("Click the button above to allow the app to access your location and run the check.")

st.markdown("---")
st.caption("Disclaimer: This tool is for flight planning only. Always confirm safety, battery, and LAANC authorization manually.")
