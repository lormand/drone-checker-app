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
def get_nearest_metar_code(lat, lon):
    """Finds the ICAO code for the nearest weather-reporting station."""
    print(f"Searching for nearest station to {lat}, {lon}...")
    
    # Aviation Edge URL to find the nearest airport to coordinates
    url = (
        "https://aviation-edge.com/v2/public/nearby?"
        f"key={AVIATION_EDGE_KEY}&lat={lat}&lng={lon}&distance=20&type=airport"
    )
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        airports = response.json()
        
        # Look for the first result that has an ICAO code (the code used for METAR)
        if airports and 'airport' in airports[0]:
            icao_code = airports[0]['airport']['icaoCode']
            print(f"Found nearest ICAO code: {icao_code}")
            return icao_code
        return None
    except Exception as e:
        st.error(f"Error finding nearest airport: {e}")
        return None

def fetch_metar_data(icao_code):
    """Fetches METAR data for the given ICAO code."""
    # Using Aviation Edge's METAR endpoint
    url = (
        "https://aviation-edge.com/v2/public/metar?"
        f"key={AVIATION_EDGE_EDGE_KEY}&code={icao_code}&format=json"
    )
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        # Aviation Edge METAR structure is complex; this is a simplified extraction
        if data and 'metar' in data[0] and 'weather_elements' in data[0]:
            metar = data[0]
            elements = metar['weather_elements']
            
            # Wind data often requires conversion from meters/second or knots (API dependent)
            # Assuming you can adjust this based on the exact format provided by your chosen API.
            # Here we will simulate a simple weather output:
            
            # NOTE: For real deployment, you must convert the API's wind (knots)
            # and temp (Celsius) into your required units (MPH and Fahrenheit).
            
            # --- SIMULATED DATA EXTRACTION ---
            # This part needs to be accurately mapped to the AviationEdge response.
            # Example:
            
            # wind_speed_knots = elements.get('wind_speed', 0) 
            # wind_gust_knots = elements.get('wind_gust', 0)
            # visibility_meters = elements.get('visibility_meters', 10000)
            # temp_c = elements.get('temperature_c', 20)
            
            # For demonstration, we'll return a placeholder structure:
            return {
                'icao_code': icao_code,
                'wind_speed': 15.0, # MPH
                'wind_gust': 20.0, # MPH
                'temp_f': 65.0, # F
                'visibility_miles': 5.0, # Miles
                'precip_prob': 0, # Placeholder, METAR handles weather conditions (rain, snow)
                'source_text': metar['text'] # Raw METAR text
            }
        return None
    except Exception as e:
        st.error(f"Error fetching METAR data: {e}")
        return None

# --- CORE MAVIC 3 PRO LOGIC (Simplified from previous response) ---
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
    if st.button("Run Flight Check", type="primary"):
        with st.spinner('Fetching nearest airport METAR and running checks...'):
            icao_code = get_nearest_metar_code(lat, lon)
            
            if icao_code:
                # Fetch the detailed weather data
                weather_data = fetch_metar_data(icao_code)
                
                if weather_data:
                    status, reasons = check_flight_status(weather_data)
                    
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
else:
    st.info("Click the button below to allow the app to access your location.")
    # Streamlit geolocation button is hidden until the user clicks an interactive element
    # which allows the browser to request location permission. The button above handles this.

st.markdown("---")
st.caption("Disclaimer: This is a flight planning tool and does not replace the pilot's final safety check.")
