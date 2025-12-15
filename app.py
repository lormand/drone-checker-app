import streamlit as st
import requests
from datetime import datetime
import pytz
from streamlit_geolocation import streamlit_geolocation # New package for location

# --- CONFIGURATION (Move the Mavic 3 Pro limits here) ---
LIMITS = {
    'MAX_WIND_SPEED_MPH': 27.0,
    'MAX_GUST_SPEED_MPH': 30.0,
    'MIN_TEMP_F': 14.0,
    'MAX_TEMP_F': 104.0,
    'MIN_VISIBILITY_MILES': 3.0,
    'MAX_PRECIP_PROB': 0,
    'MAX_KP_INDEX': 5.0,
    'WIND_SAFETY_BUFFER': 1.25 # Adjust reported wind for altitude
}

# --- API Endpoints ---
# NOTE: Aviation Edge requires an API Key, but is reliable for airport lookup.
# You will need to sign up for a key (AviationEdge.com, often free for testing).
AVIATION_EDGE_KEY = 'YOUR_AVIATION_EDGE_KEY' 

# Aviation Edge API endpoint for finding the nearest METAR station
# This is a two-step process: 1. Find the nearest airport, 2. Get its METAR
# --- FREE API FUNCTIONS (AWC/NWS) ---

def get_nearest_station_id(lat, lon):
    """Uses NWS /points endpoint to find the closest observation station ID."""
    try:
        # Step 1: Find all nearby stations
        points_url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}/stations"
        
        # NOTE: NWS requires a custom User-Agent header (use your name/email)
        headers = {'User-Agent': 'MavicProCheckerApp (randylormand@example.com)'}
        response = requests.get(points_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        
        # The first feature in the list is typically the closest station
        if 'features' in data and data['features']:
            # The station ID is typically the last segment of the @id URL
            station_url = data['features'][0]['@id']
            # Split the URL and take the last part (the ICAO/ID)
            icao_code = station_url.split('/')[-1]
            return icao_code
        return None
    except Exception as e:
        st.error(f"Error finding nearest station from NWS: {e}")
        return None

def fetch_metar_data(icao_code):
    """Fetches the latest observation (METAR) from the NWS using the station ID."""
    try:
        # Step 2: Fetch the latest observation
        metar_url = f"https://api.weather.gov/stations/{icao_code}/observations/latest"
        headers = {'User-Agent': 'MavicProCheckerApp (randylormand@example.com)'}
        response = requests.get(metar_url, headers=headers)
        response.raise_for_status()
        data = response.json()['properties']
        
        # Data Extraction and Conversion from NWS
        # The NWS data is already in a clean format, but uses Metric/Scientific units
        
        # 1. Temperature: Celsius to Fahrenheit
        temp_c = data['temperature']['value']
        temp_f = (temp_c * 9/5) + 32 if temp_c is not None else 60.0 # Default if null

        # 2. Wind: Knots to MPH (1 knot = 1.15078 MPH)
        wind_speed_knots = data['windSpeed']['value'] / 1.852 if data['windSpeed']['value'] is not None else 0 # m/s to knots
        wind_speed_mph = wind_speed_knots * 1.15078

        wind_gust_knots = data['windGust']['value'] / 1.852 if data['windGust']['value'] is not None else wind_speed_knots # m/s to knots
        wind_gust_mph = wind_gust_knots * 1.15078

        # 3. Visibility: Meters to Miles (1 mile = 1609.34 meters)
        visibility_meters = data['visibility']['value']
        visibility_miles = visibility_meters / 1609.34 if visibility_meters is not None else 10.0
        
        # 4. Weather: Check if any "present weather" is reported (rain, snow, etc.)
        # This will be simplified to a simple boolean check
        present_weather = data['textDescription'] if data.get('textDescription') else ""
        precip_risk = 100 if any(word in present_weather.lower() for word in ['rain', 'snow', 'shower', 'drizzle', 'thunder']) else 0
        
        return {
            'icao_code': icao_code,
            'wind_speed': wind_speed_mph,
            'wind_gust': wind_gust_mph,
            'temp_f': temp_f,
            'visibility_miles': visibility_miles,
            'precip_prob': precip_risk,
            'source_text': present_weather 
        }

    except Exception as e:
        st.error(f"Error fetching latest observation for {icao_code}: {e}")
        return None

# NOTE: You will also need to re-integrate the Kp index fetch (which is also free).

# --- CORE MAVIC 3 PRO LOGIC (Simplified from previous response) ---
def fetch_kp_index():
    """Fetches the latest estimated Kp Index from the GFZ (free, no key)."""
    print("--- Fetching Geomagnetic Data (Kp Index) ---")
    # GFZ provides real-time Kp estimates in an accessible JSON format
    kp_url = "https://kp.gfz.de/app/json/nowcast-kp-index"
    
    try:
        response = requests.get(kp_url)
        response.raise_for_status()
        data = response.json()
        
        # The data is an array of [time, Kp_value]. We want the last, most recent value.
        if data and 'data' in data and data['data']:
            # Kp values are often represented in thirds (e.g., 5- = 4.7, 5 = 5.0, 5+ = 5.3).
            # The JSON array returns the raw number (e.g., 47 for 4 2/3).
            # We divide by 10 to get the standard decimal value.
            latest_kp_raw = data['data'][-1][1] 
            latest_kp = latest_kp_raw / 10.0
            print(f"   -> Latest Kp Index Found: {latest_kp}")
            return latest_kp

        return 0.0 # Default to calm if data is missing
    except Exception as e:
        st.warning(f"Error fetching Kp Index: {e}. Defaulting to Kp 0.0.")
        return 0.0

def check_flight_status(weather_data):
    reasons_to_ground = []
    
    # 1. Wind Check (Applying the safety buffer)
    actual_wind = weather_data['wind_speed'] * LIMITS['WIND_SAFETY_BUFFER']
    actual_gust = weather_data['wind_gust'] * LIMITS['WIND_SAFETY_BUFFER']
    
    if actual_wind > LIMITS['MAX_WIND_SPEED_MPH']:
        reasons_to_ground.append(f"Wind exceeds limit: {actual_wind:.1f} MPH (Max: {LIMITS['MAX_WIND_SPEED_MPH']} MPH)")
    if actual_gust > LIMITS['MAX_GUST_SPEED_MPH']:
        reasons_to_ground.append(f"Gusts too dangerous: {actual_gust:.1f} MPH (Max: {LIMITS['MAX_GUST_SPEED_MPH']} MPH)")

    # 2. Temperature Check
    if not (LIMITS['MIN_TEMP_F'] <= weather_data['temp_f'] <= LIMITS['MAX_TEMP_F']):
        reasons_to_ground.append(f"Temperature is unsafe: {weather_data['temp_f']:.1f}°F")

    # 3. Visibility Check
    if weather_data['visibility_miles'] < LIMITS['MIN_VISIBILITY_MILES']:
        reasons_to_ground.append(f"Visibility too low: {weather_data['visibility_miles']:.1f} miles")

    # 4. Precipitation/Moisture Check (METAR reports conditions like R-A for Rain, not probability)
    # You need to parse the raw METAR text for codes like RA, SN, TS, FZRA, etc.
    # We will skip the full parsing for this example and focus on wind/temp/vis.
    
    # 5. Kp Index (Simulated - must be fetched separately in a real app)
    # Since you need to query a second API (like the one from the previous response)
    # we will skip it here, but it should be integrated if the check is critical.

    # Satellite/GPS Check (Kp Index)
    if weather_data.get('kp_index', 0) >= LIMITS['MAX_KP_INDEX']:
        reasons_to_ground.append(f"High Solar Storm activity (Kp {weather_data['kp_index']:.1f}). GPS instability possible.")
    
    if reasons_to_ground:
        return "DON'T FLY", reasons_to_ground
    else:
        return "READY TO LAUNCH", ["Conditions are optimal for Mavic 3 Pro."]


# --- STREAMLIT UI ---

st.set_page_config(
    page_title="Mavic 3 Pro Flight Checker",
    page_icon="🚁",
    layout="wide"
)

st.title("🚁 Mavic 3 Pro Flight Checker")
st.subheader("Automated Go/No-Go Decision Based on Real-Time Aviation Weather")

# Use the custom component to get the location from the browser/phone
location = streamlit_geolocation()

if location is not None and location.get('latitude') is not None:
    lat = location['latitude']
    lon = location['longitude']
    
    st.info(f"📍 Location Found: Latitude {lat:.4f}, Longitude {lon:.4f}")
    
    # --- Decision Button ---
# --- Decision Button ---
if st.button("Run Flight Check", type="primary"):
    # This line executes if the button is clicked.
    with st.spinner('Fetching nearest airport METAR and running checks...'):
        
        # All lines below here are inside the spinner block.
        
        # 1. Fetch Kp Index (New call)
        kp_index = fetch_kp_index()
        
        # 2. Fetch Weather
        icao_code = get_nearest_station_id(lat, lon)
        
        if icao_code:
            # Fetch the detailed weather data
            weather_data = fetch_metar_data(icao_code)
            
            # 3. Add Kp to the data structure
            if weather_data:
                weather_data['kp_index'] = kp_index # Add the Kp value here
                
                # 4. Update the call to check_flight_status to accept Kp
                status, reasons = check_flight_status(weather_data)
                
                # ... All lines below here MUST be inside the weather_data 'if' block ...
                
                st.header(status)
                if status == "READY TO LAUNCH":
                    st.success(f"✅ {status} - Weather from **{icao_code}**")
                    st.balloons()
                else:
                    st.error(f"❌ {status} - Weather from **{icao_code}**")
                
                st.markdown("### Detailed Conditions")
                col1, col2 = st.columns(2)
                col1.metric("Wind Speed (Adjusted)", f"{weather_data['wind_speed'] * LIMITS['WIND_SAFETY_BUFFER']:.1f} MPH", f"({weather_data['wind_speed']:.1f} Ground)")
                col1.metric("Wind Gust (Adjusted)", f"{weather_data['wind_gust'] * LIMITS['WIND_SAFETY_BUFFER']:.1f} MPH", f"(Max Safe: {LIMITS['MAX_GUST_SPEED_MPH']} MPH)")
                col2.metric("Temperature", f"{weather_data['temp_f']:.1f} °F", f"({LIMITS['MIN_TEMP_F']} - {LIMITS['MAX_TEMP_F']} Range)")
                col2.metric("Visibility", f"{weather_data['visibility_miles']:.1f} Miles", f"(Min Safe: {LIMITS['MIN_VISIBILITY_MILES']} Miles)")
                
                if reasons:
                    st.markdown("### 🛑 Reasons to Ground:")
                    for reason in reasons:
                        st.warning(f"- {reason}")
            else:
                st.error(f"Could not retrieve detailed METAR data for {icao_code}.")
        else:
            st.warning("Could not find a nearby weather-reporting airport.")
            
# The following lines are outside the button/spinner block (which is correct)
else:
    st.info("Click the button below to allow the app to access your location.")
    # Streamlit geolocation button is hidden until the user clicks an interactive element
    # which allows the browser to request location permission. The button above handles this.

st.markdown("---")
st.caption("Disclaimer: This is a flight planning tool and does not replace the pilot's final safety check.")
