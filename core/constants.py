"""
CONSTANTS MODULE - Shared constants for the unified application
"""

import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

# Global variables for database configurations (will be updated by reload function)
DB_CONFIGS: Dict[str, Dict[str, str]] = {}
LABELS: Dict[str, Dict[str, str]] = {"international": {}, "utilisateur": {}, "roaming": {}}
DB_KEY_TO_CPARAM: Dict[str, str] = {}
LOCATION_MAPPING: Dict[str, str] = {}

# Frontend metadata (generated from db_config.json)
FRONTEND_METADATA: Dict[str, any] = {
    "international_names": {},
    "gi_names": {},
    "roaming_cities": [],
    "roaming_types": []
}

# Thread lock for configuration reloading
_config_lock = threading.RLock()



def load_database_configurations() -> Tuple[Dict[str, Dict[str, str]], Dict[str, Dict[str, str]], Dict[str, str], Dict[str, str], Dict[str, str], Dict[str, str], List[str], List[str]]:
    """
    Load database configurations from db_config.json file.
    Returns DB_CONFIGS, LABELS, db_key_to_cparam, location_mapping, international_names, gi_names, roaming_cities, roaming_types.
    """
    config_file = Path(__file__).parent.parent / "db_config.json"

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)

        # Initialize DB_CONFIGS and LABELS
        db_configs = {}
        labels = {
            "international": {},
            "utilisateur": {},
            "roaming": {}
        }

        # Initialize additional mappings for compatibility
        db_key_to_cparam = {}
        location_mapping = {}

        # Initialize frontend metadata
        international_names = {}
        gi_names = {}
        roaming_cities = set()
        roaming_types = set()

        # Process each category
        for category, databases in config_data.items():
            for db_info in databases:
                key = db_info["key"]
                host = db_info["host"]
                db_name = db_info["db_name"]
                display_name = db_info["display_name"]
                site = db_info["site"]
                c_param = db_info["c_param"]

                # Add to DB_CONFIGS
                db_configs[key] = {
                    "host": host,
                    "db_name": db_name,
                    "site": site
                }

                # Add to LABELS based on category
                if category == "international":
                    labels["international"][key] = display_name
                    # Add cparam mapping
                    db_key_to_cparam[key] = c_param
                    # Add location mapping for international
                    location_mapping[key] = f"[Sortie Internationale] {display_name}"
                    # Add to frontend metadata: use display_name.split()[0]
                    international_names[key] = display_name.split()[0]

                elif category == "gi":
                    labels["utilisateur"][key] = display_name
                    # Add cparam mapping
                    db_key_to_cparam[key] = c_param
                    # Add location mapping for GI
                    location_mapping[key] = f"[GI] {display_name}"
                    # Add to frontend metadata: use display_name.split()[0]
                    gi_names[key] = display_name.split()[0]

                elif category == "roaming":
                    labels["roaming"][key] = display_name
                    # Add cparam mapping
                    db_key_to_cparam[key] = c_param
                    # Roaming databases don't have location mapping in the original code
                    # Add to frontend metadata: extract from key
                    roaming_cities.add(key.split('_')[0])  # First part is city
                    if '_roaming_' in key:
                        roaming_types.add(key.split('_roaming_')[1])  # Part after _roaming_

        # Convert sets to sorted lists for frontend metadata
        roaming_cities = sorted(list(roaming_cities))
        roaming_types = sorted(list(roaming_types))

        return db_configs, labels, db_key_to_cparam, location_mapping, international_names, gi_names, roaming_cities, roaming_types

    except FileNotFoundError:
        raise FileNotFoundError(f"Configuration file {config_file} not found")
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in configuration file: {e}")
    except KeyError as e:
        raise KeyError(f"Missing required key in configuration: {e}")


def reload_database_configurations() -> bool:
    """
    Reload database configurations from file and update global variables.
    Returns True if successful, False otherwise.
    """
    global DB_CONFIGS, LABELS, DB_KEY_TO_CPARAM, LOCATION_MAPPING, FRONTEND_METADATA

    with _config_lock:
        try:
            # Load new configurations
            new_db_configs, new_labels, new_db_key_to_cparam, new_location_mapping, new_international_names, new_gi_names, new_roaming_cities, new_roaming_types = load_database_configurations()

            # Update global variables
            DB_CONFIGS = new_db_configs
            LABELS = new_labels
            DB_KEY_TO_CPARAM = new_db_key_to_cparam
            LOCATION_MAPPING = new_location_mapping
            FRONTEND_METADATA = {
                "international_names": new_international_names,
                "gi_names": new_gi_names,
                "roaming_cities": new_roaming_cities,
                "roaming_types": new_roaming_types
            }

            return True
        except Exception as e:
            print(f"Error reloading database configurations: {e}")
            return False


# Initial load of configurations
reload_database_configurations()

# ============================================================================
# GLOBAL CONFIGURATION
# ============================================================================
APP_PORT = int(os.getenv("APP_PORT_UNIFIED", "501"))
CAMEROON_TZ = ZoneInfo("Africa/Douala")
TOKEN_TTL = int(os.getenv("TOKEN_TTL", 180))  # 3 minutes
TOKENS = {}  # token -> expiration timestamp

# Scheduler and Orange API client
scheduler = None
orange_api_client = None

# Monitoring state
monitoring_data = {'last_update': None, 'data': {}}
monitoring_lock = threading.Lock()

# Global cache for dashboard data
DASHBOARD_CACHE = {}
DASHBOARD_CACHE_LOCK = threading.Lock()

# ============================================================================
# DATABASE CONFIGURATION
# ============================================================================
# Ticket database (local) - moved here to avoid circular imports
LOCAL_DB_HOST = os.getenv("LOCAL_DB_HOST", "localhost")
LOCAL_DB_USER = os.getenv("LOCAL_DB_USER", "root")
LOCAL_DB_PASS = os.getenv("LOCAL_DB_PASS", "")
LOCAL_DB_NAME = os.getenv("LOCAL_DB_NAME", "tickets_db")
LOCAL_DB_PORT = int(os.getenv("LOCAL_DB_PORT", "3306"))

# Database configuration for analytics (uses centralized vars)
LOGS_DB_CONFIG = {
    "host": LOCAL_DB_HOST,
    "user": LOCAL_DB_USER,
    "password": LOCAL_DB_PASS,
    "db": LOCAL_DB_NAME,
    "port": LOCAL_DB_PORT,
}


# ============================================================================
# DASHBOARD CONSTANTS
# ============================================================================
DEBIT_FACTOR = 8 / (300 * 1_000_000)  # Conversion factor for traffic calculation

EXNET_TRAFFIC_LINK = os.getenv(
    "EXNET_TRAFFIC_LINK",
    "https://{db_host}/exnet/diagnet/user_plane/traffic_vol.php?c={c_param}&ipset=*&protocole=1&end_date=__END_TIMESTAMP__&display=7200",
)
EXNET_LOSS_LINK = os.getenv(
    "EXNET_LOSS_LINK",
    "https://{db_host}/exnet/diagnet/user_plane/tcp_qos_internet.php?c={c_param}&selected=&ipset=*&end_date=__END_TIMESTAMP__&display=7200",
)
EXNET_TCP_SERVICE_ACCESS_LINK = os.getenv(
    "EXNET_TCP_SERVICE_ACCESS_LINK",
    "https://{db_host}/exnet/diagnet/user_plane/tcp_service_access.php?c={c_param}&selected=&ipset=*&end_date=__END_TIMESTAMP__&display=7200&type=%",
)
EXNET_TCP_TTS_LINK = os.getenv(
    "EXNET_TCP_TTS_LINK",
    "https://{db_host}/exnet/diagnet/user_plane/tcp_tts.php?c={c_param}&selected=&ipset=*&end_date=__END_TIMESTAMP__&display=7200",
)
EXNET_DNS_LINK = os.getenv(
    "EXNET_DNS_LINK",
    "https://{db_host}/exnet/diagnet/user_plane/dns_summary.php?c={c_param}&selected=&ipset=*&end_date=__END_TIMESTAMP__&display=7200&type=%",
)

CONFIG_FILE = Path(__file__).parent.parent / "config.json"