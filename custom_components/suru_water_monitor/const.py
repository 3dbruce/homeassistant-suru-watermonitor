"""Constants for the SURU WaterMonitor integration."""

DOMAIN = "suru_water_monitor"

# AWS Cognito (extracted from the app's res/raw/amplifyconfiguration.json)
COGNITO_REGION = "eu-central-1"
COGNITO_POOL_ID = "eu-central-1_8hkX7Bvjh"
COGNITO_CLIENT_ID = "1p3lj6d18fehl88ugahglf6kc8"
COGNITO_IDP_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"

# SURU backend (AWS API Gateway, eu-central-1)
API_BASE = "https://api.freeze.suru-cloud.com/v2"

# Polling interval (configurable via the options flow), in minutes.
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL_MINUTES = 60
MIN_SCAN_INTERVAL_MINUTES = 5
MAX_SCAN_INTERVAL_MINUTES = 1440

# Hourly consumption -> long-term statistics. The utility delivers its smart-meter
# data with a delay (often once a day), so every update re-imports the last few
# days to pick up late hours. On the very first run, history is backfilled.
STATS_REFRESH_DAYS = 3
STATS_BACKFILL_DAYS = 30

# Action `import_history`: rebuild the statistic from a start date (one API
# request per day, so the period is capped).
SERVICE_IMPORT_HISTORY = "import_history"
ATTR_START_DATE = "start_date"
MAX_HISTORY_DAYS = 5 * 366
