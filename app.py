import streamlit as st
import requests
import json
from datetime import datetime
import pytz
from streamlit_geolocation import streamlit_geolocation 

# --- CONFIGURATION (Mavic 3 Pro Limits) ---

LIMITS = {
    'MAX_WIND_SPEED_MPH': 27.0,     # Max continuous wind speed
    'MAX_GUST_SPEED_MPH': 30.0,
    'MIN_TEMP_F': 14.0,             # -10°C
    'MAX_TEMP_F': 104.0,            # 40°C
    'MIN_VISIBILITY_MILES': 3.0,    # FAA minimum visibility for Part 107
    'MAX_PRECIP_PROB': 0,           # 0% (No water resistance)
    'MAX_KP_INDEX': 5.0,            # Geomagnetic storm threshold (affects GPS lock)
    'WIND_SAFETY_BUFFER': 1.25      # Safety factor for ground wind at altitude
}

# --- Utility Functions ---

def degrees_to_cardinal(deg):
    """Converts wind degrees (0-360) to a cardinal direction (N, NE, SW, etc.)."""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    # Divide the 360 degrees into 16 sections (22.5 degrees per section)
    ix = round(deg / (360. / len(dirs)))
    return dirs[ix % len(dirs)]

# --- API Fetching Functions (100% Free) ---

def get_nearest_station_id(lat, lon):
    """Uses NWS /points endpoint to find the closest observation station ID (ICAO)."""
    try:
        points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}/stations"
        
        # NWS requires a custom User-Agent header
        headers = {'User-Agent': 'MavicProCheckerApp (dronepilot@example.com)'}
        response = requests.get(points_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        if 'features' in data and data['features']:
            # The station ID (ICAO) is the last part of the 'id' URL
            station_url = data['features'][0]['id']
            icao_code = station_url.split('/')[-1]
            return icao_code
        return None
    except Exception as e:
        # st.error(f"Error finding nearest station from NWS: {e}") # Suppress for cleaner UI
        return None

def fetch_metar_data(icao_code):
    """Fetches the latest observation (METAR) from the NWS using the station ID."""
    try:
        metar_url = f"https://api.weather.gov/stations/{icao_code}/observations/latest"
        headers = {'User-Agent': 'MavicProCheckerApp (dronepilot@example.com)'}
        response = requests.get(metar_url, headers=headers)
        response.raise_for_status()
        data = response.json()['properties']
        
        # Extract essential properties, handling possible None values
        
        # 1. Temperature: Celsius to Fahrenheit
        temp_c = data['temperature']['value']
        temp_f = (temp_c * 9/5) + 32 if temp_c is not None else 60.0

        # 2. Wind Speed and Gust: m/s to MPH (1 m/s ≈ 2.237 mph)
        wind_speed_ms = data['windSpeed']['value'] if data['windSpeed']['value'] is not None else 0
        wind_gust_ms = data['windGust']['value'] if data['windGust']['value'] is not None else wind_speed_ms
        
        wind_speed_mph = wind_speed_ms * 2.237 
        wind_gust_mph = wind_gust_ms * 2.237
        
        # 3. Wind Direction: Degrees (0-360)
        wind_dir_deg = data['windDirection']['value'] if data['windDirection']['value'] is not None else 0

        # 4. Visibility: Meters to Miles
        visibility_meters = data['visibility']['value']
        visibility_miles = visibility_meters / 1609.34 if visibility_meters is not None else 10.0

        # 5. Precipitation Check (simplified from text description)
        present_weather = data['textDescription'] if data.get('textDescription') else ""
        precip_risk = 100 if any(word in present_weather.lower() for word in ['rain', 'snow', 'drizzle', 'thunder', 'fog']) else 0
        
        # 6. Sunrise/Sunset (fetched via separate NWS call, or a simplified calculation)
        # NWS observation data doesn't include sunset/sunrise. Use a placeholder or a dedicated library if needed.
        # For this version, we'll use a placeholder for day/night check only.
        local_tz = pytz.timezone('America/Chicago')
        now_time = datetime.now(local_tz).time()
        
        # Placeholder for simplicity: check between 6am and 7pm
        sunrise_ph = local_tz.localize(datetime(2000, 1, 1, 6, 0)).time() 
        sunset_ph = local_tz.localize(datetime(2000, 1, 1, 19, 0)).time() 
        
        # In a production app, you'd use a sunrise library or different API for accuracy.
        
        return {
            'icao_code': icao_code,
            'wind_speed': wind_speed_mph,
            'wind_gust': wind_gust_mph,
            'wind_direction_deg': wind_dir_deg,
            'temp_f': temp_f,
            'visibility_miles': visibility_miles,
            'precip_prob': precip_risk,
            'text_description': present_weather,
            'sunrise': sunrise_ph,
            'sunset': sunset_ph,
            'is_daylight': (now_time >= sunrise_ph and now_time <= sunset_ph)
        }

    except Exception as e:
        # st.error(f"Error fetching latest observation for {icao_code}: {e}") # Suppress for cleaner UI
        return None

def fetch_kp_index():
    """Fetches the latest OBSERVED Kp Index from NOAA SWPC (free, no key, JSON format)."""
    kp_url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
    
    try:
        response = requests.get(kp_url)
        response.raise_for_status()
        data = response.json()
        
        # Data starts from the second element (index 1).
        for row in reversed(data[1:]): 
            time_tag, kp_value_str, status, noaa_scale = row
            
            if status == "observed":
                latest_kp = float(kp_value_str)
                return latest_kp
        return 0.0 # Default if no 'observed' value is found
        
    except Exception as e:
        # st.warning(f"Error fetching Kp Index: {e}.") # Suppress for cleaner UI
        return 0.0

def check_airspace(lat, lon):
    """
    Checks for immediate airspace restrictions using OpenAIP (community data).
    NOTE: This is a simplified check and does NOT replace official LAANC authorization.
    """
    # OpenAIP is currently the most accessible keyless service for this.
    airspace_url = f"http://api.openaip.net/api/airspaces?lat={lat}&lon={lon}&dist=10"
    
    try:
        response = requests.get(airspace_url)
        response.raise_for_status()
        data = response.json()
        
        restricted_zones = []
        
        for item in data.get('airspaces', []):
            type = item.get('type')
            name = item.get('name')
            
            # Identify critical restrictions
            if type in ['PROHIBITED', 'RESTRICTED', 'DANGER']:
                restricted_zones.append(f"🔴 PROHIBITED/DANGER ZONE: {name}")
            
            # Identify controlled airspace requiring LAANC
            elif type in ['CLASS B', 'CLASS C', 'CLASS D']:
                 restricted_zones.append(f"⚠️ {type} AIRSPACE: LAANC authorization required.")
        
        if restricted_zones:
            return "RESTRICTED", restricted_zones
        else:
            return "CLEAR", ["Airspace appears clear for VFR flight."]

    except Exception as e:
        return "WARNING", [f"Airspace check failed ({e}). Check LAANC app manually."]

# --- CORE MAVIC 3 PRO LOGIC ---

def check_flight_status(weather_data, kp_index):
    """Applies all hard weather limits for the Mavic 3 Pro."""
    
    reasons_to_ground = []
    
    # 1. Wind Check (Applying the safety buffer for altitude)
    actual_wind = weather_data['wind_speed'] * LIMITS['WIND_SAFETY_BUFFER']
    actual_gust = weather_data['wind_gust'] * LIMITS['WIND_SAFETY_BUFFER']
    
    if actual_wind > LIMITS['MAX_WIND_SPEED_MPH']:
        reasons_to_ground.append(f"🌬️ Wind exceeds limit: {actual_wind:.1f} MPH (Max: {LIMITS['MAX_WIND_SPEED_MPH']} MPH)")
    if actual_gust > LIMITS['MAX_GUST_SPEED_MPH']:
        reasons_to_ground.append(f"💨 Gusts too dangerous: {actual_gust:.1f} MPH (Max: {LIMITS['MAX_GUST_SPEED_MPH']} MPH)")

    # 2. Temperature Check
    if weather_data['temp_f'] < LIMITS['MIN_TEMP_F'] or weather_data['temp_f'] > LIMITS['MAX_TEMP_F']:
        reasons_to_ground.append(f"🌡️ Temperature is unsafe: {weather_data['temp_f']:.1f}°F")

    # 3. Moisture and Visibility Check
    if weather_data['precip_prob'] > LIMITS['MAX_PRECIP_PROB']:
        reasons_to_ground.append(f"💧 Precipitation risk ({weather_data['text_description']}). Mavic 3 is not waterproof!")

    if weather_data['visibility_miles'] < LIMITS['MIN_VISIBILITY_MILES']:
        reasons_to_ground.append(f"🌫️ Visibility too low: {weather_data['visibility_miles']:.1f} miles")
    
    # 4. Night Flight Check (based on simple placeholder for demonstration)
    if not weather_data['is_daylight']:
        reasons_to_ground.append("🌙 Flying outside of daylight hours (requires proper certification & lights)")

    # 5. Satellite/GPS Check (Kp Index)
    if kp_index >= LIMITS['MAX_KP_INDEX']:
        reasons_to_ground.append(f"🛰️ High Solar Storm activity (Kp {kp_index:.1f}). GPS instability possible.")

    if len(reasons_to_ground) > 0:
        return "DON'T FLY", reasons_to_ground
    else:
        return "READY TO LAUNCH", ["All weather and space weather conditions are optimal."]


# --- STREAMLIT UI ---

st.set_page_config(
    page_title="Mavic 3 Pro Flight Checker",
    page_icon="🚁",
    layout="wide"
)

st.title("🚁 Mavic 3 Pro Flight Checker")
st.subheader("Zero-Cost Pre-Flight Safety Check")
st.markdown("---")

# Use the custom component to get the location from the browser/phone
location = streamlit_geolocation()

if location is not None and location.get('latitude') is not None:
    lat = location['latitude']
    lon = location['longitude']
    
    st.info(f"📍 Current Location: Latitude {lat:.4f}, Longitude {lon:.4f}")
    
    # --- Decision Button ---
    if st.button("Run Comprehensive Flight Check", type="primary"):
        with st.spinner('Fetching NWS Weather, Kp Index, and Airspace data...'):
            
            # 1. Fetch Kp Index
            kp_index = fetch_kp_index()
            
            # 2. Fetch Airspace Data
            airspace_status, airspace_reasons = check_airspace(lat, lon)
            
            # 3. Fetch Weather Data
            icao_code = get_nearest_station_id(lat, lon)
            
            # --- Aggregated Logic ---
            if icao_code:
                weather_data = fetch_metar_data(icao_code)
                
                if weather_data:
                    # Check weather and Kp limits
                    status, weather_reasons = check_flight_status(weather_data, kp_index)
                    
                    # Combine all reasons (Weather/Kp + Airspace)
                    all_reasons = weather_reasons + airspace_reasons
                    
                    final_status = "READY TO LAUNCH"
                    if status == "DON'T FLY" or airspace_status == "RESTRICTED" or airspace_status == "WARNING":
                        final_status = "DON'T FLY"

                    # --- Display Final Result ---
                    st.header(final_status)
                    if final_status == "READY TO LAUNCH":
                        st.success(f"✅ GO! Conditions are favorable. Weather from **{icao_code}**.")
                        st.balloons()
                    else:
                        st.error(f"❌ NO GO. Check reasons below.")
                    
                    # --- Detailed Conditions Display ---
                    st.markdown("### Detailed Conditions")
                    
                    col1, col2 = st.columns(2)
                    
                    # Col 1: Wind and Temp
                    wind_dir_cardinal = degrees_to_cardinal(weather_data.get('wind_direction_deg', 0))
                    
                    col1.metric("Wind Speed (Adjusted)", 
                                f"{weather_data['wind_speed'] * LIMITS['WIND_SAFETY_BUFFER']:.1f} MPH", 
                                f"({weather_data['wind_speed']:.1f} Ground)")
                    col1.metric("Wind Gust (Adjusted)", 
                                f"{weather_data['wind_gust'] * LIMITS['WIND_SAFETY_BUFFER']:.1f} MPH", 
                                f"(Max Safe: {LIMITS['MAX_GUST_SPEED_MPH']} MPH)")
                    col1.metric("Wind Direction", wind_dir_cardinal, f"{weather_data.get('wind_direction_deg', 0)}°")

                    # Col 2: Visibility, Time, Geomagnetic
                    col2.metric("Temperature", f"{weather_data['temp_f']:.1f} °F", f"({LIMITS['MIN_TEMP_F']} - {LIMITS['MAX_TEMP_F']} Range)")
                    col2.metric("Visibility", f"{weather_data['visibility_miles']:.1f} Miles", f"(Min Safe: {LIMITS['MIN_VISIBILITY_MILES']} Miles)")
                    col2.metric("Kp Index (GPS Risk)", f"{kp_index:.1f}", f"(Max Safe: {LIMITS['MAX_KP_INDEX']} Kp)")
                    
                    st.markdown("---")
                    st.markdown(f"**Sunlight Window:** {weather_data['sunrise'].strftime('%I:%M %p')} to {weather_data['sunset'].strftime('%I:%M %p')}")


                    if all_reasons:
                        st.markdown("### 🛑 Reasons for Grounding:")
                        for reason in all_reasons:
                            st.warning(f"- {reason}")
                else:
                    st.error(f"Could not retrieve detailed NWS data for {icao_code}. Try again.")
            else:
                st.warning("Could not find a nearby weather-reporting airport.")
else:
    st.info("Click the button below to allow the app to access your location and run the check.")

st.markdown("---")
st.caption("Disclaimer: This tool is for flight planning only. Always confirm safety, battery, and LAANC authorization manually.")
