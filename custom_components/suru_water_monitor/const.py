"""Constants for the SURU WaterMonitor integration."""
from datetime import timedelta

DOMAIN = "suru_water_monitor"

# AWS Cognito (extracted from the app's res/raw/amplifyconfiguration.json)
COGNITO_REGION = "eu-central-1"
COGNITO_POOL_ID = "eu-central-1_8hkX7Bvjh"
COGNITO_CLIENT_ID = "1p3lj6d18fehl88ugahglf6kc8"
COGNITO_IDP_URL = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/"

# SURU backend (AWS API Gateway, eu-central-1)
API_BASE = "https://api.freeze.suru-cloud.com/v2"

DEFAULT_SCAN_INTERVAL = timedelta(hours=1)
