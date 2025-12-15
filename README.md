# Drone Checker App (RC)

A small Streamlit app that evaluates whether a DJI Mavic 3 Pro can safely fly based on nearby METAR weather, solar Kp index, and daylight.

## Quick Start

1. Create and activate a virtual environment (recommended):

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the app:

```bash
streamlit run app.py
```

4. Open the local preview URL shown by Streamlit. Allow location access when the browser prompts.

## Notes for RC1
- The `streamlit-geolocation` component had a client-side permission issue which has been resolved.
- Version: `0.1.0-rc1` (see `VERSION`).

## Files
- `app.py`: Main Streamlit app.
- `requirements.txt`: Python dependencies.
- `CHANGELOG.md`: Release notes.
- `VERSION`: Release version string.

