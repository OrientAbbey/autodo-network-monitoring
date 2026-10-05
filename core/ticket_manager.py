"""
TICKET MANAGER - Manages ticket creation, updates, and closure
Handles all ticket operations including Orange API integration
"""

import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from . import constants
from .database_manager import get_local_db_connection
from .logger import get_logger
from .utils import (calculate_ttr, get_next_ticket_id, load_config,
                    ttr_to_human_readable)

logger = get_logger(__name__)

# Orange API Client
class OrangeAPIClient:
    """
    Orange Developer CALYPSO API OCEANE ODI Client
    Handles OAuth 2.0 authentication and trouble ticket creation
    Uses the same configuration as test_external_api.py
    """

    def __init__(self, scheduler=None):
        self.scheduler = scheduler
        
        """Initialize with Orange Calypso API credentials - updated configuration"""
        # Orange Developer CALYSPSO API OCEANE ODI Credentials (updated)
        self.client_id = os.getenv('ORANGE_API_CLIENT_ID', "")
        self.client_secret = os.getenv('ORANGE_API_CLIENT_SECRET', "")
        
        # API Endpoints
        self.server = os.getenv('ORANGE_API_SERVER_INTRA', '')
        self.token_endpoint = f"{self.server}{os.getenv('ORANGE_API_TOKEN_ENDPOINT', '')}"
        self.trouble_ticket_endpoint = f"{self.server}{os.getenv('ORANGE_API_BASE_ENDPOINT', '')}/troubleTicket"
        
        self.cuid = os.getenv('CLIENT_USER_ID', 'RJDF7613')
        
        # Token management
        self.access_token = None

        self.token_type = "Bearer"
        self.token_expires_at = None
        
        # Calculate Basic Auth Header properly
        credentials_string = f"{self.client_id}:{self.client_secret}"
        credentials_bytes = credentials_string.encode('utf-8')
        self.basic_auth_header = base64.b64encode(credentials_bytes).decode('utf-8')
        
        logger.debug(
            f"Orange Calypso API Client initialized\n"
            f"Client ID: {self.client_id[:5]}\n"
            f"Client Secret: {self.client_secret[:10]}...\n"
            f"Token Endpoint: {self.token_endpoint}\n"
            f"Trouble Ticket Endpoint: {self.trouble_ticket_endpoint}\n"
            f"CUID: {self.cuid}\n"
            f"Calculated Basic Auth Header: Basic {self.basic_auth_header[:30]}..."
        )

    def _handle_retry_logic(self, ticket_data: dict, error_msg: str, retry_count: int, max_retries: int) -> dict:
        """
        Unified retry logic for all exception types (ConnectTimeout, ConnectionError, etc.)
        Consolidates redundant retry scheduling code.
        """
        if retry_count < max_retries:
            logger.debug(f"Will retry in 15 minutes (attempt {retry_count + 2}/{max_retries + 1})")
            retry_time = datetime.now() + timedelta(minutes=15)
            self.scheduler.add_job(
                func=self.retry_ticket_creation,
                trigger='date',
                run_date=retry_time,
                args=[ticket_data, retry_count + 1, max_retries],
                id=f"retry_orange_ticket_{ticket_data.get('id')}_{retry_count + 1}",
                replace_existing=True
            )
            
            return {
                "success": False, 
                "error": "Request error - Retry scheduled", 
                "message": f"{error_msg} - Will retry in 15 minutes",
                "retry_count": retry_count,
                "retry_scheduled": True,
                "next_retry": retry_time.isoformat()
            }
        else:
            return {
                "success": False, 
                "error": "Request error - All retries exhausted", 
                "message": f"{error_msg} - Failed after {max_retries + 1} attempts",
                "retry_count": retry_count,
                "retry_scheduled": False
            }

    def get_access_token(self):
        """
        Get OAuth 2.0 access token using proper Basic Auth header
        Saves token data to file to avoid excessive requests
        Returns: access token string or None if failed
        """
        # Try to load token from file first
        token_file = Path(__file__).parent / "orange_token.json"
        current_time = datetime.now(timezone.utc)

        if token_file.exists():
            try:
                with open(token_file, 'r') as f:
                    token_data = json.load(f)
                    saved_token = token_data.get('access_token')
                    expires_at_str = token_data.get('expires_at')
                    if saved_token and expires_at_str:
                        expires_at = datetime.fromisoformat(expires_at_str)
                        # Add 60 second buffer before expiration
                        if current_time + timedelta(seconds=60) < expires_at:
                            self.access_token = saved_token
                            self.token_expires_at = expires_at
                            self.token_type = token_data.get('token_type', 'Bearer')
                            logger.debug("Using saved Orange API access token")
                            return self.access_token
            except Exception as e:
                logger.warning(f"Error loading saved token: {e}")

        logger.debug("Requesting new Orange API access token...")

        try:
            # Use properly calculated Basic Auth header
            headers = {
                'Authorization': f'Basic {self.basic_auth_header}',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }

            data = {
                'grant_type': 'client_credentials'
            }

            logger.debug(
                f"Using calculated Authorization header: Basic {self.basic_auth_header[:20]}...\n"
                f"Client ID: {self.client_id[:5]}\n"
                f"Client Secret: {self.client_secret[:10]}..."
            )

            # Make token request
            response = requests.post(
                self.token_endpoint,
                headers=headers,
                data=data,
                timeout=30,
                verify=False
            )

            logger.debug(f"Orange API token response status: {response.status_code}")

            if response.status_code == 200:
                token_data = response.json()
                self.access_token = token_data.get('access_token')
                self.token_type = token_data.get('token_type', 'Bearer')
                expires_in = token_data.get('expires_in', 3600)

                # Calculate expiration time
                self.token_expires_at = current_time + timedelta(seconds=expires_in)

                # Save token data to file
                try:
                    save_data = {
                        'access_token': self.access_token,
                        'token_type': self.token_type,
                        'expires_at': self.token_expires_at.isoformat(),
                        'saved_at': current_time.isoformat()
                    }
                    with open(token_file, 'w') as f:
                        json.dump(save_data, f)
                    logger.debug("Orange API token saved to file")
                except Exception as e:
                    logger.warning(f"Error saving token to file: {e}")

                logger.debug(
                    f"✓ Orange API access token obtained successfully\n"
                    f"Token expires at: {self.token_expires_at}"
                )
                return self.access_token
            else:
                logger.error(f"❌ Orange API token request failed: {response.status_code}")
                try:
                    error_data = response.json()
                    logger.error(f"Error details: {error_data}")
                except:
                    logger.error(f"Error response: {response.text}")
                return None

        except requests.exceptions.ConnectTimeout:
            logger.error("❌ Orange API connection timeout - ensure connection to Orange internal network")
            return None
        except requests.exceptions.ConnectionError as e:
            logger.error(f"❌ Orange API connection error: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ Orange API token request error: {e}")
            return None
        

    def create_trouble_ticket(self, ticket_data, retry_count=0, max_retries=3):
        """
        Create a trouble ticket using Orange API - FIXED STRUCTURE (only description changes)
        Args:
            ticket_data: Dictionary containing ticket information
            retry_count: Current retry attempt (0-based)
            max_retries: Maximum number of retry attempts
        Returns:
            Dictionary with success status and response data
        """
        # Get valid access token
        access_token = self.get_access_token()
        if not access_token:
            return {
                "success": False,
                "error": "Failed to obtain Orange API access token",
                "message": "Could not authenticate with Orange API",
                "retry_count": retry_count
            }
        
        logger.debug(f"Creating Orange API trouble ticket for ticket {ticket_data.get('id', 'Unknown')} (attempt {retry_count + 1}/{max_retries + 1})")
        
        try:
            # FIXED PAYLOAD STRUCTURE - Only description changes, everything else stays the same
            orange_payload = {
                "description": self._format_ticket_description(ticket_data),  # ONLY THIS FIELD CHANGES
                "ticketType": {
                    "id": "1",
                    "label": "Incident"
                },
                "origin": {
                    "id": "J",
                    "label": "Incident"
                },
                "relatedResource": {
                    "referredResource": False,
                    "id": "PRD124245259",
                    "resourceSpecCharacteristic": [
                        {
                            "index": "1",
                            "value": "ResourceId1"
                        }
                    ]
                }
            }
            
            # Prepare headers (updated to use new configuration)
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json;charset=utf-8',
                'Accept-Language': 'en',
                'X-Client-User-Id': self.cuid
            }
            
            logger.debug(
                f"Sending trouble ticket to Orange API using FIXED structure...\n"
                f"Description length: {len(orange_payload['description'])} characters"
            )
            
            # Send request to Orange API
            response = requests.post(
                self.trouble_ticket_endpoint,
                headers=headers,
                json=orange_payload,
                timeout=30,
                verify=False
            )
            
            logger.debug(f"Orange API response status: {response.status_code}")
            
            if response.status_code in [200, 201]:
                try:
                    response_data = response.json()
                    orange_ticket_id = response_data.get('id', response_data.get('ticketId', 'Unknown'))
                    
                    logger.debug(
                        f"✓ Orange API trouble ticket created successfully!\n"
                        f"Orange Ticket ID: {orange_ticket_id}\n"
                        f"AUTODO Ticket ID: {ticket_data.get('id')}")
                    
                    return {
                        "success": True,
                        "orange_ticket_id": orange_ticket_id,
                        "response_data": response_data,
                        "message": f"Orange ticket {orange_ticket_id} created successfully",
                        "retry_count": retry_count
                    }
                except:
                    logger.debug(f"✓ Orange API trouble ticket created (response not JSON)")
                    return {
                        "success": True,
                        "orange_ticket_id": "Created",
                        "response_data": response.text,
                        "message": "Orange ticket created successfully",
                        "retry_count": retry_count
                    }
            else:
                logger.error(f"❌ Orange API trouble ticket creation failed")
                try:
                    error_data = response.json()
                    error_message = error_data.get('message', 'Unknown error')
                    logger.error(f"Error details: {error_data}")
                except:
                    error_message = response.text
                    logger.error(f"Error response: {response.text}")
                
                # If this was not the last retry, schedule another attempt
                if retry_count < max_retries:
                    logger.debug(f"Will retry in 15 minutes (attempt {retry_count + 2}/{max_retries + 1})")
                    # Schedule retry using the scheduler
                    retry_time = datetime.now() + timedelta(minutes=15)
                    self.scheduler.add_job(
                        func=self.retry_ticket_creation,
                        trigger='date',
                        run_date=retry_time,
                        args=[ticket_data, retry_count + 1, max_retries],
                        id=f"retry_orange_ticket_{ticket_data.get('id')}_{retry_count + 1}",
                        replace_existing=True
                    )
                    
                    return {
                        "success": False,
                        "error": f"Orange API error {response.status_code} - Retry scheduled",
                        "message": f"{error_message} - Will retry in 15 minutes",
                        "status_code": response.status_code,
                        "retry_count": retry_count,
                        "retry_scheduled": True,
                        "next_retry": retry_time.isoformat()
                    }
                else:
                    return {
                        "success": False,
                        "error": f"Orange API error {response.status_code} - All retries exhausted",
                        "message": f"{error_message} - Failed after {max_retries + 1} attempts",
                        "status_code": response.status_code,
                        "retry_count": retry_count,
                        "retry_scheduled": False
                    }
                
        except (requests.exceptions.ConnectTimeout, requests.exceptions.ConnectionError, Exception) as e:
            error_msg = (
                "Connection timeout to Orange API - ensure connection to Orange internal network"
                if isinstance(e, requests.exceptions.ConnectTimeout)
                else f"Connection error to Orange API: {e}"
                if isinstance(e, requests.exceptions.ConnectionError)
                else f"Orange API request error: {e}"
            )
            logger.error(f"❌ {error_msg}")
            return self._handle_retry_logic(ticket_data, error_msg, retry_count, max_retries)

    def retry_ticket_creation(self, ticket_data, retry_count, max_retries):
        """
        Retry method for failed Orange API ticket creation
        Called by scheduler after 15-minute delays
        """
        logger.debug(
            f"\n=== Retrying Orange API Ticket Creation ===\n"
            f"Ticket ID: {ticket_data.get('id')}\n"
            f"Attempt: {retry_count + 1}/{max_retries + 1}\n"
            f"=========================================")
        
        result = self.create_trouble_ticket(ticket_data, retry_count, max_retries)
        
        if result.get("success"):
            # Success! Update the ticket in JSON and database
            orange_ticket_id = result.get("orange_ticket_id")
            
            # Update database
            ticket_data['external_api_ticket_id'] = orange_ticket_id
            
            if save_tickets([ticket_data]):
                logger.debug(f"✓ Updated ticket {ticket_data.get('id')} with Orange ID {orange_ticket_id}")

        else:
            if not result.get("retry_scheduled"):
                logger.error(f"❌ All retry attempts exhausted for ticket {ticket_data.get('id')}")
            else:
                logger.debug(f"⏰ Next retry scheduled for ticket {ticket_data.get('id')}")
                

    def get_orange_ticket_status(self, orange_ticket_id):
        """
        Get Orange API trouble ticket status to check if it's still InProgress
        
        Args:
            orange_ticket_id: The Orange API ticket ID
            
        Returns:
            dict: {"success": bool, "is_inprogress": bool, "status_code": str, "error": str}
        """
        try:
            # Get access token
            access_token = self.get_access_token()
            if not access_token:
                return {"success": False, "error": "Failed to get access token", "is_inprogress": False}
            
            # Prepare GET URL and headers
            get_url = f"{self.trouble_ticket_endpoint}/{orange_ticket_id}"
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
                'X-Client-User-Id': self.cuid
            }
            
            logger.debug(f"Checking Orange ticket {orange_ticket_id} status...")
            
            # Make the GET request
            response = requests.get(
                get_url,
                headers=headers,
                timeout=30,
                verify=False
            )

            if response.status_code == 200 or response.status_code == 201:
                ticket_data = response.json()
                
                # Check status array for current status
                status_array = ticket_data.get('status', [])
                current_inprogress_status = None
                
                # Find the InProgress status entry
                for status_entry in status_array:
                    if status_entry.get('code') == 'InProgress':
                        current_inprogress_status = status_entry
                        break
                
                if current_inprogress_status:
                    is_current = current_inprogress_status.get('isCurrentStatus', False)
                    logger.debug(f"Orange ticket {orange_ticket_id}: InProgress isCurrentStatus = {is_current}")
                    
                    return {
                        "success": True,
                        "is_inprogress": is_current,
                        "status_code": "InProgress" if is_current else "NotInProgress",
                        "full_status": status_array
                    }
                else:
                    # No InProgress status found, ticket is not in progress
                    logger.debug(f"Orange ticket {orange_ticket_id}: No InProgress status found")
                    return {
                        "success": True,
                        "is_inprogress": False,
                        "status_code": "NoInProgress",
                        "full_status": status_array
                    }
                    
            else:
                error_msg = f"HTTP {response.status_code}: {response.text}"
                logger.error(f"❌ Failed to get Orange ticket {orange_ticket_id} status: {error_msg}")
                return {"success": False, "error": error_msg, "is_inprogress": False}
                
        except Exception as e:
            logger.error(f"Exception getting Orange ticket {orange_ticket_id} status: {e}")
            return {"success": False, "error": str(e), "is_inprogress": False}
        

    def update_orange_ticket_status(self, orange_ticket_id):
        """
        Update Orange API trouble ticket status from InProgress to Restored
        Uses exact same logic as successful update_tickets.py script
        
        Args:
            orange_ticket_id: The Orange API ticket ID
        """
        try:
            # Get access token
            access_token = self.get_access_token()
            if not access_token:
                return {"success": False, "error": "Failed to get access token"}
            
            # Prepare update URL and headers (exact same as update_tickets.py)
            update_url = f"{self.trouble_ticket_endpoint}/{orange_ticket_id}"
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
                'X-HTTP-Method-Override': 'PATCH',
                'X-Client-User-Id': self.cuid
            }
            
            # Use current datetime in the exact format Orange API expects (same as update_tickets.py)
            start_date = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            
            # Prepare the payload with exact structure from update_tickets.py
            update_payload = {
                "status": {
                    "code": "Restored",
                    "isCurrentStatus": "true",
                    "startDate": start_date
                },
                "troubleCause": [
                    {
                        "problemDetail": {
                            "id": "315677",
                            "label": "Alarm defect"
                        },
                        "problemCategory": {
                            "id": "107",
                            "label": "Degraded Service"
                        }
                    }
                ]
            }
            
            logger.debug(
                f"Updating Orange ticket {orange_ticket_id} to 'Restored' status\n"
                f"Start date: {start_date}")
            
            # Make the PUT request (exact same as update_tickets.py)
            response = requests.put(
                update_url,
                headers=headers,
                json=update_payload,
                timeout=30,
                verify=False
            )
            
            if response.status_code in [200, 204]:
                logger.debug(f"✅ Successfully updated Orange ticket {orange_ticket_id} to 'Restored'")
                return {"success": True, "message": f"Ticket {orange_ticket_id} status updated to Restored"}
            else:
                try:
                    error_data = response.json()
                    error_msg = f"HTTP {response.status_code}: {error_data}"
                except:
                    error_msg = f"HTTP {response.status_code}: {response.text}"
                
                logger.error(f"❌ Failed to update Orange ticket {orange_ticket_id}: {error_msg}")
                return {"success": False, "error": error_msg}
                
        except Exception as e:
            logger.error(f"Exception updating Orange ticket {orange_ticket_id}: {e}")
            return {"success": False, "error": str(e)}
        

    def _format_ticket_description(self, ticket_data):
        """Format ticket description for Orange API - Use enhanced or auto-generated format"""
        # Convert timestamp to UTC for Orange API
        timestamp_utc = None
        if ticket_data.get('timestamp'):
            try:
                ticket_time = datetime.strptime(ticket_data['timestamp'], "%Y-%m-%d %H:%M:%S")
                if ticket_time.tzinfo is None:
                    ticket_time = ticket_time.replace(tzinfo=constants.CAMEROON_TZ)
                timestamp_utc = ticket_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                pass

        # Use enhanced description if available
        if ticket_data.get('description'):
            return (f"AUTODO Network Monitoring Alert\n"
                   f"=" * 50 + "\n"
                   f"{ticket_data['description']}\n"
                   f"=" * 50 + "\n"
                   f"Generated by AUTODO Monitoring System\n"
                   f"Timestamp: {timestamp_utc or ticket_data.get('timestamp', 'Unknown')}\n")[:2500]

        # Auto-generate description from ticket data
        ticket_type = ticket_data.get('type', 'unknown')
        site = ticket_data.get('site', 'Unknown')
        db = ticket_data.get('database_name', 'Unknown')
        value = ticket_data.get('value', 0)
        
        desc_parts = [
            f"AUTODO Network Monitoring Alert - {site}",
            "=" * 50,
            "TICKET DETAILS:",
            f"• Ticket ID: #{ticket_data.get('id', 'Unknown')}",
            f"• Type: {ticket_type.upper()}",
            f"• Site: {site}",
            f"• Database: {db}",
            f"• Status: {ticket_data.get('status', 'open').upper()}",
            f"• Agent: {ticket_data.get('agent', 'DIAGNET')}",
            f"• Severity: {ticket_data.get('severity', 'critical').upper()}",
            f"• Timestamp: {timestamp_utc or ticket_data.get('timestamp', 'Unknown')}",
            f"• Value: {value}{'%' if ticket_type == 'pertes' else ' Gb/s'}",
        ]

        # Add optional fields
        if ticket_data.get('autin_id'):
            desc_parts.append(f"• AUTIN ID: {ticket_data['autin_id']}")

        # Type-specific details
        if ticket_type == 'traffic' and ticket_data.get('traffic_values'):
            desc_parts.extend([
                "", "TRAFFIC DETAILS:",
                f"• Pattern: {' -> '.join(map(str, ticket_data['traffic_values']))} Gb/s"
            ])
        elif ticket_type == 'pertes':
            desc_parts.extend([
                "", "PACKET LOSS DETAILS:",
                f"• Loss Percentage: {value}%",
                f"• Threshold: {load_config().get('alert_pertes_threshold', 2.0)}%"
            ])

        # Closure info
        if ticket_data.get('closure_timestamp'):
            desc_parts.extend([
                "", "CLOSURE INFO:",
                f"• Closed At: {ticket_data['closure_timestamp']}"
            ])
            if ticket_data.get('ttr_seconds'):
                desc_parts.append(f"• TTR (Time To Repair): {ttr_to_human_readable(ticket_data['ttr_seconds'])}")

        if ticket_data.get('external_api_ticket_id'):
            desc_parts.append(f"• Orange Ticket ID: {ticket_data['external_api_ticket_id']}")

        # Alert details
        desc_parts.extend(["", "=" * 50, "ALERT DETAILS:"])
        if ticket_type == 'pertes':
            desc_parts.extend([
                "• Packet loss detected above acceptable threshold",
                f"• Current loss rate: {value}%",
                "• Network connectivity investigation required"
            ])
        elif ticket_type == 'traffic':
            if value == 0:
                desc_parts.extend(["• CRITICAL: Zero traffic detected!", "• Complete service interruption"])
            else:
                desc_parts.extend([
                    "• Traffic anomaly detected",
                    f"• Current traffic: {value} Gb/s"
                ])
            desc_parts.append("• Traffic monitoring and investigation required")
        else:
            desc_parts.append("• Network monitoring alert requires attention")

        desc_parts.extend([
            "", "ACTION REQUIRED:",
            f"• Investigate {ticket_type} issue on {site} - {db}",
            "• Take necessary corrective actions immediately",
            "", "SYSTEM INFO:",
            "• Generated by: AUTODO Network Monitoring System",
            f"• Agent: {ticket_data.get('agent', 'DIAGNET')}",
            f"• Auto-generated at: {timestamp_utc or ticket_data.get('timestamp', 'Unknown')}",
            f"• Site Location: {site}, Cameroon",
            f"• Database Instance: {db}"
        ])

        description = "\n".join(desc_parts)
        return description[:2500] if len(description) > 2500 else description

    def _map_field(self, field_type: str, value: str) -> str:
        """Unified mapping for severity, priority, category, and resolution date"""
        severity_val = value.lower() if isinstance(value, str) else str(value).lower()
        
        if field_type in ('severity', 'priority'):
            return {'critical': '1', 'high': '2', 'medium': '3', 'low': '4'}.get(severity_val, '1')
        elif field_type == 'category':
            return {'pertes': 'Connectivity', 'traffic': 'Performance', 'network': 'Network'}.get(value, 'Technical')
        elif field_type == 'resolution_date':
            hours = {'critical': 4, 'high': 8, 'medium': 24, 'low': 48}.get(severity_val, 4)
            return (datetime.now() + timedelta(hours=hours)).isoformat()
        return value

    def test_connection(self):
        """Test Orange API connection and authentication"""
        logger.debug("Testing Orange API connection...")
        
        access_token = self.get_access_token()
        if access_token:
            logger.debug("✓ Orange API connection test successful")
            return True
        else:
            logger.error("❌ Orange API connection test failed")
            return False


def load_tickets(open_only: bool = True) -> List[Dict]:
    """
    Charger les tickets depuis la table tickets_tb.
    Si open_only=True, ne charger que les tickets ouverts.
    """
    try:
        tickets = []
        with get_local_db_connection() as conn:
            with conn.cursor(dictionary=True) as cursor:
                query = "SELECT * FROM tickets_tb"
                if open_only:
                    query += " WHERE status = 'open'"
                cursor.execute(query)
                rows = cursor.fetchall()
        
        for r in rows:
            # r is expected to be a dict-like row
            ticket = {
                "id": int(r["id"]),
                "type": r.get("type") or "unknown",
                "site": r.get("site"),
                "database_name": r.get("database_name"),
                "timestamp": r["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "value": float(r["value"]) if r.get("value") is not None else None,
                "status": r.get("status") or "open",
                "agent": r.get("agent") or "DIAGNET",
                "severity": r.get("severity") or "critical",
                "closure_timestamp": r["closure_timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                    if r.get("closure_timestamp") else None,
                "closure_pending_timestamp": r["closure_pending_timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                    if r.get("closure_pending_timestamp") else None,
                "closure_reason": r.get("closure_reason"),
                "autin_id": r.get("autin_id"),
                "external_api_ticket_id": r.get("external_api_ticket_id"),
                "ttr_seconds": int(r["ttr_seconds"]) if r.get("ttr_seconds") is not None else None,
                "category": int(r["category"]) if r.get("category") is not None else None,
                "pertes": r.get("pertes") or "No",
                "description": r.get("description"),
                "reason": r.get("reason")
            }
            
            # Parse last_values if possible (stored as longtext JSON)
            def _parse_json_value(raw):
                try:
                    return json.loads(raw)
                except Exception:
                    return raw

            ticket["last_values"] = _parse_json_value(r.get("last_values")) if r.get("last_values") else []
            ticket["pertes_warnings"] = _parse_json_value(r.get("pertes_warnings")) if r.get("pertes_warnings") else []

            tickets.append(ticket)

        logger.debug(f"Loaded {len(tickets)} ticket(s) ({'open only' if open_only else 'all'}) from tickets_tb")
        return tickets

    except Exception as e:
        logger.error(f"Error loading tickets: {e}")
        return []


def save_tickets(tickets: List[Dict]) -> int:
    """Save tickets to database"""
    if not tickets:
        return 0
    
    def _dump_json(v):
        if v is None:
            return None
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False) if v else None
        return v
    
    cols = [
        "id", "type", "site", "database_name", "timestamp", "value", "status", "agent", "severity",
        "closure_timestamp", "closure_pending_timestamp", "closure_reason", "autin_id", "external_api_ticket_id",
        "ttr_seconds", "category", "pertes", "last_values", "description", "reason", "pertes_warnings"
    ]
    
    # build placeholders and ON DUPLICATE part
    placeholders = ", ".join(["%s"] * len(cols))
    col_list = ", ".join(f"`{c}`" for c in cols)
    update_clause = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c != "id")
    
    sql = f"INSERT INTO `tickets_tb` ({col_list}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"
    
    params = []
    for t in tickets:
        row = [
            t.get("id"), t.get("type"), t.get("site"), t.get("database_name"),
            t.get("timestamp"), t.get("value"), t.get("status", "open"),
            t.get("agent", "DIAGNET"), t.get("severity", "critical"),
            t.get("closure_timestamp"), t.get("closure_pending_timestamp"),
            t.get("closure_reason"), t.get("autin_id"), t.get("external_api_ticket_id"),
            t.get("ttr_seconds"), t.get("category"), t.get("pertes", "No"),
            _dump_json(t.get("last_values")), t.get("description"),
            t.get("reason"), _dump_json(t.get("pertes_warnings"))
        ]
        params.append(tuple(row))
    
    try:
        with get_local_db_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, params)
        return len(params)
    except Exception as e:
        logger.error(f"Error saving tickets: {e}")
        return 0


def has_open_ticket(db_name: str, ticket_type: str, category: int|str=None, tickets: list[dict]=None) -> dict | None:
    """Check if there's already an open ticket for this database and type"""
    if not tickets:
        tickets = load_tickets()
    return next(
        (
            ticket for ticket in tickets
            if ticket['database_name'] == db_name
            and ticket['type'] == ticket_type
            and ticket['status'] == 'open'
            and (ticket.get('category') == category if category is not None else 1)
        ),
        None  # valeur par défaut si aucun ticket trouvé
    )


# Helper functions
def add_in_tickets(tickets: List[Dict], ticket: Dict) -> List[Dict]:
    """Add or update ticket in list"""
    for i, t in enumerate(tickets):
        if t["id"] == ticket["id"]:
            tickets[i] = ticket
            break
    else:
        # Si aucun ticket avec le même id n’a été trouvé
        tickets.append(ticket)
    return tickets


class TicketManager:
    """Manages ticket operations"""
    
    def __init__(self, orange_api_client=None):
        self.orange_api_client = orange_api_client


    def create_ticket(self, ticket_type: str, db_name: str, timestamp: str, 
                     last_values: List[Dict], description: str, reason: str, 
                     category: int = None, connection_failed: bool = False, 
                     failure_reason: str = "") -> Optional[Dict]:
        """Create a new ticket with detailed description for external APIs"""
        new_ticket = None
        try:
            # Get original value based on ticket type
            original_value, _ = self._get_original_value(ticket_type, last_values, category)
            
            logger.debug(
            f"""=== Creating Enhanced Ticket ===
                Type: {ticket_type}
                Database: {db_name}
                Value: {original_value}
                Description: {description}
                Timestamp: {timestamp}
                "================================"""
            )
            new_id = get_next_ticket_id()
            site = constants.DB_CONFIGS.get(db_name, {}).get("site", "Unknown")
            
            new_ticket = {
                'id': new_id,
                'type': ticket_type,
                'site': site,
                'database_name': db_name,
                'timestamp': timestamp,
                'value': original_value,
                'status': 'open',
                'agent': 'DIAGNET',
                'severity': 'critical',
                'description': description,  # Enhanced description
                'reason': reason,  # Store the detailed reason for closure logic
                'autin_id': None,
                'pertes': 'No',  # Default to No, will be updated if pertes detected
                'external_api_ticket_id': None,
                'closure_timestamp': None,
                'ttr': None,
                'last_values': last_values, # Most recent values (consecutive_count + 1)
                'category': category, # Following the condition use to generate this alert
                'current_value': last_values[-1]['value'] # will be updated each interval
            }
            logger.debug(f"Generated ticket ID: {new_id}")
            
            # Send to Orange API if available
            logger.debug("Step 2: Sending to Orange API...")
            if self.orange_api_client:
                try:
                    # Add description to ticket data for Orange API
                    ticket_with_description = new_ticket.copy()
                    ticket_with_description['description'] = description
                    orange_result = self.orange_api_client.create_trouble_ticket(ticket_with_description)
                    if orange_result.get('success'):
                        logger.debug(f"✓ Successfully submitted to Orange API with ticket ID: {orange_result['orange_ticket_id']}")
                        new_ticket['external_api_ticket_id'] = orange_result.get('orange_ticket_id')
                    else:
                        logger.warning(f"⚠ Orange API request failed: {orange_result.get('message', 'Unknown error')}")
                
                except Exception as orange_error:
                    logger.warning(f"⚠ Orange API integration error: {str(orange_error)}")
                except Exception as e:
                    logger.warning(f"Orange API error: {e}")
            
            # Save to database
            if save_tickets([new_ticket]):
                logger.info(f"Created ticket {new_id} for {db_name} ({ticket_type})")
                
            return new_ticket
        
        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            return None
    
    
    def _check_closure_criterion_met(self, ticket_type: str, current_value: float, original_value: float, 
                                       category: int, mode_7days_gbps: float = None) -> bool:
        """Check if closure criterion is met for a given ticket type"""
        config = load_config()
        
        if ticket_type == 'pertes':
            return current_value is not None and current_value < config.get("alert_pertes_threshold", 1.0)
        
        elif ticket_type == 'traffic':
            if category == 1:
                return current_value is not None and current_value > 0
            else:
                traffic_recovery_ratio = config.get("traffic_recovery_ratio", 0.9)
                mode7days_recovery_ratio = config.get("mode7days_recovery_ratio", 0.85)
                if current_value is not None and original_value > 0:
                    return (current_value >= (original_value * traffic_recovery_ratio) or
                            (mode_7days_gbps is not None and current_value >= (mode_7days_gbps * mode7days_recovery_ratio)))
                return False
        
        elif ticket_type == 'db_down':
            db_recovery_threshold = config.get("db_recovery_threshold_minutes", 70)
            return current_value is not None and current_value <= db_recovery_threshold
        
        return False

    def _format_closure_reason(self, ticket_type: str, current_value: float, original_value: float, alert_persistence: int) -> str:
        """Format closure reason based on ticket type"""
        if ticket_type == 'pertes':
            return f"Pertes resolved: stable for {alert_persistence} minutes"
        elif ticket_type == 'traffic':
            recovery_percentage = (current_value / original_value) * 100 if original_value > 0 else 100
            return f"Traffic recovered to {recovery_percentage:.1f}% of original value and stable for {alert_persistence} minutes."
        elif ticket_type == 'db_down':
            return f"Database back online: {current_value:.1f} minutes behind. Stable for {alert_persistence} minutes."
        else:
            return f"Resolved and stable for {alert_persistence} minutes."

    def update_ticket_status(self, ticket: dict, current_timestamp: str, try_to_close: bool = True, values_to_updated: dict = None):
        """Update ticket status based on current values with proper database synchronization"""
        should_close = False
        current_time_dt = datetime.strptime(current_timestamp, "%Y-%m-%d %H:%M:%S")

        last_values = ticket.get('last_values', []) if values_to_updated is None else values_to_updated.get("last_values", [])
        category = int(ticket.get('category', 0)) if ticket.get('category') is not None else 0
        current_value = last_values[-1]['value'] if last_values else 0.0
        original_value = float(ticket.get('value', 0.0))
        ticket_type = ticket.get("type")
        db_name = ticket.get("database_name", "")
        mode_7days_gbps = values_to_updated.get("mode_7days_gbps") if values_to_updated else None

        config = load_config()
        alert_persistence = config.get("alert_persistence_duration_minutes", 25)

        if not try_to_close:
            # Mise à jour des valeurs sans tentative de fermeture
            if ticket_type in ['pertes', 'traffic']:
                ticket['last_values'] = last_values if last_values is not None else ticket.get('last_values', [])
                if values_to_updated and values_to_updated.get("category") not in (category, 0):
                    if ticket_type == 'pertes':
                        ticket["value"] = self._get_original_value(ticket_type, last_values, values_to_updated["category"])[0]
                if values_to_updated and values_to_updated.get("category") != 0:
                    ticket["reason"] = values_to_updated.get("reason", ticket.get("reason", ""))
                    ticket['category'] = values_to_updated.get("category", ticket.get('category'))
            # IMPORTANT: Reset closure_pending_timestamp car le ticket est toujours actif à la suite d'une mise à jour
            ticket['closure_pending_timestamp'] = None
            logger.debug(f"Updated last_values for {ticket_type} ticket {ticket.get('id')}: {ticket.get('last_values')}")
        else:
            # try_to_close == True -> appliquer la logique unifiée de persistance
            criterion_met = self._check_closure_criterion_met(ticket_type, current_value, original_value, category, mode_7days_gbps)

            # Gestion du closure_pending_timestamp
            pending_ts_str = ticket.get('closure_pending_timestamp')
            if criterion_met:
                if not pending_ts_str:
                    # Démarrer le compte à rebours
                    pending_time = current_time_dt + timedelta(minutes=alert_persistence)
                    ticket['closure_pending_timestamp'] = pending_time.strftime("%Y-%m-%d %H:%M:%S")
                    ticket['closure_reason'] = f"Pending closure: criterium met. Will close if maintained for {alert_persistence} minutes."
                    logger.debug(f"Ticket {ticket.get('id')} - {ticket_type}: criterium met, pending closure started")
                else:
                    # Vérifier si la deadline est atteinte
                    try:
                        pending_time = datetime.strptime(pending_ts_str, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pending_time = current_time_dt + timedelta(minutes=alert_persistence)
                        ticket['closure_pending_timestamp'] = pending_time.strftime("%Y-%m-%d %H:%M:%S")
                        logger.warning(f"Ticket {ticket.get('id')}: invalid closure_pending_timestamp, restarted")

                    if current_time_dt >= pending_time:
                        should_close = True
                        ticket['closure_reason'] = self._format_closure_reason(ticket_type, current_value, original_value, alert_persistence)
                        logger.info(f"Ticket {ticket.get('id')} ({ticket_type}) will be closed: criterion maintained for {alert_persistence} minutes.")
            else:
                # Critère non rempli -> annuler tout pending
                if pending_ts_str:
                    logger.debug(f"Ticket {ticket.get('id')} ({ticket_type}) closure pending canceled. Current: {current_value}")
                ticket['closure_pending_timestamp'] = None

        # Si on doit fermer maintenant -> appliquer les changements de fermeture
        if should_close:
            logger.debug(
                f"\n=== Closing Ticket {ticket.get('id')} ===\n"
                f"Type: {ticket_type}\n"
                f"Database: {db_name}\n"
                f"Original Value: {ticket.get('value')}\n"
                f"Current Value: {current_value}\n"
                f"Closure Time: {current_timestamp}\n"
                f"Reason: {ticket.get('closure_reason', 'Unknown')}"
            )

            ticket["closure_timestamp"] = current_timestamp
            ticket["status"] = "closed"

            creation_timestamp = ticket.get("timestamp")
            ttr_seconds = calculate_ttr(creation_timestamp, current_timestamp)
            ticket["ttr_seconds"] = ttr_seconds

            # Step 2: Send email notification for ticket closure
            logger.debug("Step 3: Sending closure email notification...")

            # Step 3: Update Orange API ticket status to 'Restored'
            logger.debug("Step 4: Updating Orange API ticket status to 'Restored'...")
            try:
                orange_ticket_id = ticket.get('external_api_ticket_id')
                if orange_ticket_id and orange_ticket_id != 'Unknown' and orange_ticket_id != '':
                    logger.debug(f"Updating Orange ticket {orange_ticket_id} status to 'Restored'...")
                    orange_result = self.orange_api_client.update_orange_ticket_status(orange_ticket_id)
                    if orange_result.get('success'):
                        logger.debug(f"✅ Orange ticket {orange_ticket_id} status updated to 'Restored' successfully")
                    else:
                        logger.warning(f"⚠ Failed to update Orange ticket {orange_ticket_id} status: {orange_result.get('error', 'Unknown error')}")
                else:
                    logger.warning(f"⚠ No valid Orange ticket ID found for ticket {ticket.get('id')} - skipping Orange API status update")
            except Exception as orange_error:
                logger.error(f"⚠ Orange API status update error: {str(orange_error)}")
                # Ne pas échouer la fermeture pour une erreur API

        # Sauvegarder et retourner
        if save_tickets([ticket]):
            logger.debug(f"Ticket {ticket.get('id')} updated and saved in DB")
            return True, ticket

        return False, ticket

    
    def _get_original_value(self, ticket_type: str, last_values: List[Dict], category: int = None) -> Tuple[float, str]:
        """Get original value based on ticket type and category"""
        config = load_config()
        
        if ticket_type == "traffic":
            if category in (1, 2):  # Critical drop
                original_value, timestamp = last_values[-2]['value'], last_values[-2]['timestamp']
            elif category == 3:  # Cumulative drop
                original_value, timestamp = last_values[1]['value'], last_values[1]['timestamp']
            elif category == 4:  # Daily drop
                original_value, timestamp = last_values[0]['value'], last_values[-1]['timestamp']
            else:
                original_value, timestamp = last_values[-1]['value'], last_values[-1]['timestamp']
        elif ticket_type == "pertes":
            pertes_consecutive_count = config.get('pertes_consecutive_count', 1)
            original_value, timestamp = last_values[-pertes_consecutive_count]['value'], last_values[-pertes_consecutive_count]['timestamp']
        else:
            original_value, timestamp = last_values[-1]['value'], last_values[-1]['timestamp']
        return original_value, timestamp
    
    
    def create_ticket_with_description(self, ticket_type: str, db_name: str, timestamp: str, 
                                       last_values: List[Dict], detailed_reason: str, 
                                       connection_failed: bool = False, failure_reason: str = "", 
                                       custom_description: str = None, category: int = None) -> Optional[Dict]:
        """Create a ticket with detailed description for external API"""
        value, timestamp = self._get_original_value(ticket_type, last_values, category)
        
        if custom_description:
            description = custom_description
            reason = detailed_reason
        else:
            # Generate comprehensive description based on ticket type and reason
            site_name = constants.DB_CONFIGS.get(db_name, {}).get("site", "Unknown")
            
            if connection_failed:
                reason = failure_reason
                if ticket_type == 'traffic':
                    description = f"[DATABASE CONNECTION FAILURE - TRAFFIC CRITIQUE] Perte de connexion à la base de données {site_name}. " \
                                 f"Raison: {failure_reason}. Trafic traité comme zéro - Vérification infrastructure urgente requise. " \
                                 f"Timestamp: {timestamp}, Valeur: {value} Gb/s"
                elif ticket_type == 'pertes':
                    description = f"[DATABASE CONNECTION FAILURE - PERTES CRITIQUES] Perte de connexion à la base de données {site_name}. " \
                                 f"Raison: {failure_reason}. Pertes traitées comme zéro - Diagnostic infrastructure requis. " \
                                 f"Timestamp: {timestamp}, Taux de perte: {value:.3f}%"
                else:
                    description = f"[DATABASE CONNECTION FAILURE - {ticket_type.upper()}] Perte de connexion à la base de données {site_name}. " \
                                 f"Raison: {failure_reason}. Timestamp: {timestamp}, Valeur: {value}"
            else:
                reason = detailed_reason
                if ticket_type == 'traffic':
                    if "Perte de données" in detailed_reason:
                        description = f"[TRAFFIC CRITIQUE] Perte totale de données détectée sur {site_name}. " \
                                     f"Valeurs de trafic nulles consécutives. Vérification urgente requise. " \
                                     f"Timestamp: {timestamp}, Valeur actuelle: {value} Gb/s"
                    elif "Diminution critique instantanée" in detailed_reason:
                        description = f"[TRAFFIC CRITIQUE] Chute instantanée critique du trafic sur {site_name}. " \
                                     f"{detailed_reason}. Investigation immédiate nécessaire. " \
                                     f"Timestamp: {timestamp}, Valeur actuelle: {value} Gb/s"
                    elif "diminutions consécutives" in detailed_reason:
                        description = f"[TRAFFIC ALERTE] Pattern de diminutions consécutives détecté sur {site_name}. " \
                                     f"{detailed_reason}. Surveillance renforcée recommandée. " \
                                     f"Timestamp: {timestamp}, Valeur actuelle: {value} Gb/s"
                    else:
                        description = f"[TRAFFIC] Anomalie de trafic détectée sur {site_name}. " \
                                     f"{detailed_reason}. Timestamp: {timestamp}, Valeur: {value} Gb/s"
                elif ticket_type == 'pertes':
                    description = f"[PERTES CRITIQUES] Taux de perte de paquets élevé sur {site_name}. " \
                                 f"{detailed_reason}. Diagnostic réseau requis. " \
                                 f"Timestamp: {timestamp}, Taux de perte: {value:.3f}%"
                elif ticket_type == 'db_down':
                    description = f"[BASE DE DONNÉES HORS LIGNE] Base de données {site_name} non mise à jour depuis {value:.1f} minutes. " \
                                 f"{detailed_reason}. Vérification infrastructure critique requise. " \
                                 f"Timestamp: {timestamp}"
                else:
                    description = f"[{ticket_type.upper()}] Incident détecté sur {site_name}. " \
                                 f"{detailed_reason}. Timestamp: {timestamp}, Valeur: {value}"
        
        return self.create_ticket(ticket_type, db_name, timestamp, last_values, description, reason, category, connection_failed, failure_reason)
    
    
    def add_pertes_message_to_traffic_ticket(ticket: dict, loss_percentage: float, timestamp: str) -> dict[str, dict] :
        """
        Ajoute un message de pertes ('pertes warning') à un ticket de type 'traffic'.
        - Ne recharge ni ne sauvegarde la liste complète des tickets.
        - Met à jour uniquement le ticket fourni et le renvoie.
        """
        # Initialiser le champ pertes_warnings si manquant
        if 'pertes_warnings' not in ticket:
            ticket['pertes_warnings'] = []

        # Vérifie si un message récent (< 30 min) a déjà été ajouté
        try:
            if ticket['pertes_warnings']:
                last_warning_time = datetime.strptime(ticket['pertes_warnings'][-1]['timestamp'], "%Y-%m-%d %H:%M:%S")
                current_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                time_diff = (current_time - last_warning_time).total_seconds() / 60  # en minutes
                if time_diff < 30:
                    logger.info(f"Recent pertes warning already exists for ticket {ticket['id']}")
                    return ticket
        except Exception as e:
            logger.warning(f"Timestamp parsing failed: {e}")

        # Ajout du nouveau warning
        ticket['pertes_warnings'].append({
            'timestamp': timestamp,
            'loss_percentage': loss_percentage,
            'message': f"High packet loss ({loss_percentage}%) detected during traffic issue"
        })

        # Marquer que des pertes sont détectées
        ticket['pertes'] = 'Yes'

        # Ajouter une mention dans la description si absente
        if 'description' not in ticket:
            ticket['description'] = f"Traffic issue detected on {ticket.get('database_name', 'unknown')}"
        if "PERTES ALERT" not in ticket['description']:
            ticket['description'] += (
                f"\n\n--- PERTES ALERT ---\n"
                f"High packet loss detected during this traffic issue. See pertes_warnings for details."
            )

        logger.info(f"Added pertes warning to traffic ticket {ticket['id']}: {loss_percentage}% at {timestamp}")
        return ticket
 
    
    def process_ticket_logic(self, db_key: str, alerts: Dict, metrics: Dict, 
                            datetime_str: str, tickets: List[Dict], 
                            mode_7days_gbps: float | None) -> List[Dict]:
        """
        Process complete ticket creation/update logic for traffic and pertes alerts.
        Covers all 16 possible cases with clear priority and attachment management.
        """        
        actions = []
        
        # Extract alert data
        traffic_alert = bool(alerts.get('traffic_alert'))
        pertes_alert = bool(alerts.get('pertes_alert'))
        traffic_reason = alerts.get('traffic_reason')
        pertes_reason = alerts.get('pertes_reason')
        traffic_cat = alerts.get('traffic_cat')
        pertes_cat = alerts.get('pertes_cat')
        
        # Prepare values
        traffic_gbps = [{"timestamp": ts, "value": gb} for ts, gb in zip(metrics.get('timestamp'), metrics.get('traffic_gbps'))]
        loss_percentages = [{"timestamp": ts, "value": lp} for ts, lp in zip(metrics.get('timestamp'), metrics.get('loss_percentage'))]
        connection_failed = metrics.get('connection_failed', [False])[-1]
        failure_reason = metrics.get('failure_reason', [''])[-1]
        
        # Valeurs de mise à jour
        traffic_to_update = {"reason": traffic_reason, "category": traffic_cat, "last_values": traffic_gbps, "mode_7days_gbps": mode_7days_gbps}
        perte_to_update = {"reason": pertes_reason, "category": pertes_cat, "last_values": loss_percentages}

        # Vérification de l'existence des tickets
        traffic_ticket = has_open_ticket(db_key, 'traffic', tickets=tickets)
        pertes_ticket = has_open_ticket(db_key, 'pertes', tickets=tickets)
        
        # ========== GROUPE 1: traffic_alert=True, pertes_alert=True ==========
        if traffic_alert and pertes_alert:
            
            if not traffic_ticket and not pertes_ticket:
                # CAS 1: Aucun ticket existant → Créer traffic + attacher pertes
                logger.debug(f"[{db_key}] CAS 1: Création ticket traffic avec pertes attachées")
                new_t = self.create_ticket_with_description(
                    'traffic', db_key, datetime_str, traffic_gbps, traffic_reason,
                    connection_failed=connection_failed, failure_reason=failure_reason, category=traffic_cat
                )
                if new_t:
                    new_t = self.add_pertes_message_to_traffic_ticket(new_t, loss_percentages[-1]['value'], datetime_str)
                    tickets = add_in_tickets(tickets, new_t)
                    actions.append(('create_traffic_with_pertes', new_t.get('id')))
                    logger.info(f"Créé ticket traffic {new_t.get('id')} avec pertes pour {db_key}")
            
            elif traffic_ticket and not pertes_ticket:
                # CAS 2: Traffic existe → Mettre à jour + attacher pertes
                logger.debug(f"[{db_key}] CAS 2: Mise à jour ticket traffic + attachment pertes")
                updated, traffic_ticket = self.update_ticket_status(traffic_ticket, datetime_str, try_to_close=False, values_to_update=traffic_to_update)
                if updated:
                    traffic_ticket = self.add_pertes_message_to_traffic_ticket(traffic_ticket, loss_percentages[-1]['value'], datetime_str)
                    tickets = add_in_tickets(tickets, traffic_ticket)
                    actions.append(('update_traffic_attach_pertes', db_key))
            
            elif not traffic_ticket and pertes_ticket:
                # CAS 3: Pertes existe → Créer traffic + mettre à jour pertes + attacher pertes au traffic
                logger.debug(f"[{db_key}] CAS 3: Création traffic + mise à jour pertes existant")
                new_t = self.create_ticket_with_description(
                    'traffic', db_key, datetime_str, traffic_gbps, traffic_reason,
                    connection_failed=connection_failed, failure_reason=failure_reason, category=traffic_cat
                )
                if new_t:
                    new_t = self.add_pertes_message_to_traffic_ticket(new_t, loss_percentages[-1]['value'], datetime_str)
                    tickets = add_in_tickets(tickets, new_t)
                    actions.append(('create_traffic_with_pertes', new_t.get('id')))
                
                updated, pertes_ticket = self.update_ticket_status(pertes_ticket, datetime_str, try_to_close=False, values_to_update=perte_to_update)
                if updated:
                    tickets = add_in_tickets(tickets, pertes_ticket)
                    actions.append(('update_pertes', db_key))
            
            else:
                # CAS 4: Les deux existent → Mettre à jour les deux
                logger.debug(f"[{db_key}] CAS 4: Mise à jour traffic + pertes")
                updated_t, traffic_ticket = self.update_ticket_status(traffic_ticket, datetime_str, try_to_close=False, values_to_update=traffic_to_update)
                updated_p, pertes_ticket = self.update_ticket_status(pertes_ticket, datetime_str, try_to_close=False, values_to_update=perte_to_update)
                
                if updated_t:
                    tickets = add_in_tickets(tickets, traffic_ticket)
                    actions.append(('update_traffic', db_key))
                if updated_p:
                    tickets = add_in_tickets(tickets, pertes_ticket)
                    actions.append(('update_pertes', db_key))

        # ========== GROUPE 2: traffic_alert=True, pertes_alert=False ==========
        elif traffic_alert and not pertes_alert:
            
            if not traffic_ticket and not pertes_ticket:
                # CAS 5: Aucun ticket → Créer traffic
                logger.debug(f"[{db_key}] CAS 5: Création ticket traffic")
                new_t = self.create_ticket_with_description(
                    'traffic', db_key, datetime_str, traffic_gbps, traffic_reason,
                    connection_failed=connection_failed, failure_reason=failure_reason, category=traffic_cat
                )
                if new_t:
                    tickets = add_in_tickets(tickets, new_t)
                    actions.append(('create_traffic', new_t.get('id')))
            
            elif traffic_ticket and not pertes_ticket:
                # CAS 6: Traffic existe → Mettre à jour
                logger.debug(f"[{db_key}] CAS 6: Mise à jour ticket traffic")
                updated, traffic_ticket = self.update_ticket_status(traffic_ticket, datetime_str, try_to_close=False, values_to_update=traffic_to_update)
                if updated:
                    tickets = add_in_tickets(tickets, traffic_ticket)
                    actions.append(('update_traffic', db_key))
            
            elif not traffic_ticket and pertes_ticket:
                # CAS 7: Pertes existe → Créer traffic + tenter fermeture pertes
                logger.debug(f"[{db_key}] CAS 7: Création traffic + tentative fermeture pertes")
                new_t = self.create_ticket_with_description(
                    'traffic', db_key, datetime_str, traffic_gbps, traffic_reason,
                    connection_failed=connection_failed, failure_reason=failure_reason, category=traffic_cat
                )
                if new_t:
                    tickets = add_in_tickets(tickets, new_t)
                    actions.append(('create_traffic', new_t.get('id')))

                closed, pertes_ticket = self.update_ticket_status(pertes_ticket, datetime_str, try_to_close=True, values_to_update=perte_to_update)
                if closed and pertes_ticket['status'] == 'closed':
                    actions.append(('close_pertes', db_key))
                    logger.info(f"Fermé ticket pertes {pertes_ticket['id']} pour {db_key}")
                tickets = add_in_tickets(tickets, pertes_ticket)
            
            else:
                # CAS 8: Les deux existent → Mettre à jour traffic + tenter fermeture pertes
                logger.debug(f"[{db_key}] CAS 8: Mise à jour traffic + tentative fermeture pertes")
                updated, traffic_ticket = self.update_ticket_status(traffic_ticket, datetime_str, try_to_close=False, values_to_update=traffic_to_update)
                if updated:
                    tickets = add_in_tickets(tickets, traffic_ticket)
                    actions.append(('update_traffic', db_key))
                
                closed, pertes_ticket = self.update_ticket_status(pertes_ticket, datetime_str, try_to_close=True, values_to_update=perte_to_update)
                if closed and pertes_ticket['status'] == 'closed':
                    actions.append(('close_pertes', db_key))
                    logger.info(f"Fermé ticket pertes {pertes_ticket['id']} pour {db_key}")
                tickets = add_in_tickets(tickets, pertes_ticket)

        # ========== GROUPE 3: traffic_alert=False, pertes_alert=True ==========
        elif not traffic_alert and pertes_alert:
            
            if not traffic_ticket and not pertes_ticket:
                # CAS 9: Aucun ticket → Créer pertes
                logger.debug(f"[{db_key}] CAS 9: Création ticket pertes")
                new_t = self.create_ticket_with_description(
                    'pertes', db_key, datetime_str, loss_percentages, pertes_reason,
                    connection_failed=connection_failed, failure_reason=failure_reason, category=pertes_cat
                )
                if new_t:
                    tickets = add_in_tickets(tickets, new_t)
                    actions.append(('create_pertes', new_t.get('id')))
            
            elif traffic_ticket and not pertes_ticket:
                # CAS 10: Traffic existe → Attacher pertes + annuler fermeture pending
                logger.debug(f"[{db_key}] CAS 10: Attachment pertes au ticket traffic existant")
                updated, traffic_ticket = self.update_ticket_status(traffic_ticket, datetime_str, try_to_close=False, values_to_update=traffic_to_update)
                if updated:
                    traffic_ticket = self.add_pertes_message_to_traffic_ticket(traffic_ticket, loss_percentages[-1]['value'], datetime_str)
                    tickets = add_in_tickets(tickets, traffic_ticket)
                    actions.append(('attach_pertes_to_traffic', db_key))
            
            elif not traffic_ticket and pertes_ticket:
                # CAS 11: Pertes existe → Mettre à jour
                logger.debug(f"[{db_key}] CAS 11: Mise à jour ticket pertes")
                updated, pertes_ticket = self.update_ticket_status(pertes_ticket, datetime_str, try_to_close=False, values_to_update=perte_to_update)
                if updated:
                    tickets = add_in_tickets(tickets, pertes_ticket)
                    actions.append(('update_pertes', db_key))
            
            else:
                # CAS 12: Les deux existent → Mettre à jour pertes + tenter fermeture traffic
                logger.debug(f"[{db_key}] CAS 12: Mise à jour pertes + tentative fermeture traffic")
                updated, pertes_ticket = self.update_ticket_status(pertes_ticket, datetime_str, try_to_close=False, values_to_update=perte_to_update)
                if updated:
                    tickets = add_in_tickets(tickets, pertes_ticket)
                    actions.append(('update_pertes', db_key))
                
                closed, traffic_ticket = self.update_ticket_status(traffic_ticket, datetime_str, try_to_close=True, values_to_update=traffic_to_update)
                if closed and traffic_ticket['status'] == 'closed':
                    actions.append(('close_traffic', db_key))
                    logger.info(f"Fermé ticket traffic {traffic_ticket['id']} pour {db_key}")
                tickets = add_in_tickets(tickets, traffic_ticket)

        # ========== GROUPE 4: traffic_alert=False, pertes_alert=False ==========
        else:
            
            if not traffic_ticket and not pertes_ticket:
                # CAS 13: Aucun ticket → Ne rien faire
                logger.debug(f"[{db_key}] CAS 13: Pas d'alerte, pas de ticket - RAS")
                pass
            
            elif traffic_ticket and not pertes_ticket:
                # CAS 14: Traffic existe → Tenter fermeture
                logger.debug(f"[{db_key}] CAS 14: Tentative fermeture ticket traffic")
                closed, traffic_ticket = self.update_ticket_status(traffic_ticket, datetime_str, try_to_close=True, values_to_update=traffic_to_update)
                if closed and traffic_ticket['status'] == 'closed':
                    actions.append(('close_traffic', db_key))
                    logger.info(f"Fermé ticket traffic {traffic_ticket['id']} pour {db_key}")
                tickets = add_in_tickets(tickets, traffic_ticket)
            
            elif not traffic_ticket and pertes_ticket:
                # CAS 15: Pertes existe → Tenter fermeture
                logger.debug(f"[{db_key}] CAS 15: Tentative fermeture ticket pertes")
                closed, pertes_ticket = self.update_ticket_status(pertes_ticket, datetime_str, try_to_close=True, values_to_update=perte_to_update)
                if closed and pertes_ticket['status'] == 'closed':
                    actions.append(('close_pertes', db_key))
                    logger.info(f"Fermé ticket pertes {pertes_ticket['id']} pour {db_key}")
                tickets = add_in_tickets(tickets, pertes_ticket)
            
            else:
                # CAS 16: Les deux existent → Tenter fermeture des deux
                logger.debug(f"[{db_key}] CAS 16: Tentative fermeture traffic + pertes")
                closed_t, traffic_ticket = self.update_ticket_status(traffic_ticket, datetime_str, try_to_close=True, values_to_update=traffic_to_update)
                closed_p, pertes_ticket = self.update_ticket_status(pertes_ticket, datetime_str, try_to_close=True, values_to_update=perte_to_update)
                
                if closed_t and traffic_ticket['status'] == 'closed':
                    actions.append(('close_traffic', db_key))
                    logger.info(f"Fermé ticket traffic {traffic_ticket['id']} pour {db_key}")
                if closed_p and pertes_ticket['status'] == 'closed':
                    actions.append(('close_pertes', db_key))
                    logger.info(f"Fermé ticket pertes {pertes_ticket['id']} pour {db_key}")
                
                tickets = add_in_tickets(tickets, traffic_ticket)
                tickets = add_in_tickets(tickets, pertes_ticket)

        # Logging final
        if actions:
            logger.info(f"[{db_key}] Actions effectuées: {actions}")
        
        return tickets
  
    
    def process_db_down_logic(self, db_key: str, db_timestamp: Optional[str], 
                              datetime_str: str, tickets: List[Dict]) -> List[Dict]:
        """Process DB down ticket logic"""
        from .background_task import check_database_freshness
        
        if not db_timestamp:
            return tickets
        
        # Use check_database_freshness function
        is_db_down, minutes_behind = check_database_freshness(db_key, db_timestamp, datetime_str)
        db_down_reason = f"Base de données {db_key} non mise à jour depuis {int(minutes_behind // 60)}h {int(minutes_behind % 60)}m"
        if is_db_down:
            logger.debug(f"🚨 DB DOWN issue detected for {db_key}: {db_down_reason}")
            db_ticket = has_open_ticket(db_key, 'db_down', tickets=tickets)
            if not db_ticket:
                logger.debug(f"✅ CREATING DB DOWN TICKET for {db_key}")
                try:
                    description = self._create_db_down_ticket_description(db_key, minutes_behind, db_timestamp, datetime_str)
                    ticket_result = self.create_ticket_with_description(
                        'db_down', db_key, db_timestamp, 
                        [{"timestamp": db_timestamp, "value": minutes_behind}], 
                        db_down_reason, custom_description=description, category=1
                    )
                    if ticket_result:
                        tickets = add_in_tickets(tickets, ticket_result)
                        logger.debug(f"✅ Successfully created DB Down ticket for {db_key}")
                except Exception as e:
                    logger.error(f"❌ Error creating DB Down ticket for {db_key}: {e}")
            else:
                db_ticket["value"] = minutes_behind
                db_ticket["reason"] = db_down_reason
                db_ticket["category"] = 1
                db_ticket["last_values"] = [{"timestamp": db_timestamp, "value": minutes_behind}]
                db_ticket["description"] = self._create_db_down_ticket_description(db_key, minutes_behind, db_timestamp, datetime_str)
                save_tickets([db_ticket])
                tickets = add_in_tickets(tickets, db_ticket)
        else:
            # Close existing DB down tickets
            existing_db_down_ticket = has_open_ticket(db_key, 'db_down', tickets=tickets)
            if existing_db_down_ticket:
                existing_db_down_ticket["category"] = 1
                logger.debug(f"🔄 Closing {existing_db_down_ticket['id']} open DB Down tickets for {db_key}")
                values_to_update = {
                    "reason": f"Database {db_key} is up to date", 
                    "last_values": [{"timestamp": db_timestamp, "value": minutes_behind}]
                }
                closed, existing_db_down_ticket = self.update_ticket_status(
                    existing_db_down_ticket, datetime_str, try_to_close=True, values_to_update=values_to_update
                )
                tickets = add_in_tickets(tickets, existing_db_down_ticket)
        
        return tickets
  
    
    def _create_db_down_ticket_description(self, db_name: str, minutes_behind: float, 
                                          db_timestamp: str, current_timestamp: str) -> str:
        """Create a detailed description for DB Down ticket"""
        config = load_config()
        db_recovery_threshold_minutes = config.get("db_recovery_threshold_minutes", 70)
        site_info = constants.DB_CONFIGS.get(db_name, {}).get('site', 'Site inconnu')
        
        description = f"""[BASE DE DONNÉES HORS LIGNE - CRITIQUE] Base de données {site_info} non mise à jour depuis {minutes_behind:.1f} minutes.

🚨 ALERTE CRITIQUE: Absence de données récentes
📍 Localisation: {site_info}
⏰ Dernière mise à jour: {db_timestamp}
🕐 Heure actuelle: {current_timestamp}
⏱️ Retard: {minutes_behind:.1f} minutes (Seuil critique: {db_recovery_threshold_minutes} minutes)

🔧 ACTIONS REQUISES:
- Vérifier la connectivité de la base de données
- Contrôler les processus de collecte de données
- Examiner l'infrastructure réseau locale
- Redémarrer les services si nécessaire

📊 Impact: Perte de visibilité sur le trafic et les performances du site {site_info}"""
        
        return description

