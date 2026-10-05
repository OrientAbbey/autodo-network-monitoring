"""
TICKETS ROUTER - Handles all ticket monitoring, creation, and management
Integrated Orange API integration for external ticketing
"""

import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

import core.constants as constants
from core.database_manager import get_local_db_connection
from core.logger import get_logger
from core.ticket_manager import load_tickets
from core.utils import ttr_to_human_readable

logger = get_logger(__name__)

# ============================================================================
# ROUTER
# ============================================================================
tickets_router = APIRouter()


@tickets_router.get('/')
def tickets_view(request: Request):
    """Serve the tickets HTML view with ticket data"""
    from main import templates
    try:
        tickets = load_tickets(open_only=False)
        for i, ticket in enumerate(tickets):
            tickets[i]["human_readable"] = ttr_to_human_readable(ticket.get("ttr_seconds"))

        # Extract unique values for filters
        sites = list(set(ticket.get('site', '') for ticket in tickets if ticket.get('site')))
        sites = [s for s in sites if s]
        sites.sort()

        databases = list(set(ticket.get('database_name', '') for ticket in tickets if ticket.get('database_name')))
        databases = [d for d in databases if d]
        databases.sort()

        filter_options = {
            'types': [
                {'value': 'traffic', 'label': 'Traffic'},
                {'value': 'pertes', 'label': 'Pertes'},
                {'value': 'db_down', 'label': 'DB_DOWN'}
            ],
            'sites': [{'value': site, 'label': site} for site in sites],
            'databases': [{'value': db, 'label': db} for db in databases],
            'statuses': [
                {'value': 'open', 'label': 'Open'},
                {'value': 'closed', 'label': 'Closed'}
            ],
            'time_periods': [
                {'value': 'all', 'label': 'All Time'},
                {'value': 'today', 'label': 'Today'},
                {'value': 'yesterday', 'label': 'Yesterday'},
                {'value': 'week', 'label': 'Last 7 Days'},
                {'value': 'month', 'label': 'Last 30 Days'},
                {'value': 'current_month', 'label': 'Current Month'}
            ]
        }

        return templates.TemplateResponse('tickets.html', {
            'request': request,
            'tickets': tickets,
            'filter_options': filter_options
        })
    except Exception as e:
        logger.error(f"Error in tickets_view: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@tickets_router.get('/status')
def get_system_status():
    """Get comprehensive system status"""
    try:
        # Check database connection
        db_connected = False
        db_error = None
        try:
            with get_local_db_connection() as connection:
                db_connected = True
        except Exception as e:
            db_error = str(e)
        
        # Get ticket counts
        json_tickets = load_tickets(open_only=False)
        open_tickets = [t for t in json_tickets if t['status'] == 'open']
        closed_tickets = [t for t in json_tickets if t['status'] == 'closed']
        
        # Get database ticket count
        db_ticket_count = 0
        try:
            with get_local_db_connection() as connection:
                with connection.cursor(dictionary=True) as cursor:
                    cursor.execute("SELECT COUNT(*) as count FROM tickets_tb")
                    result = cursor.fetchone()
            db_ticket_count = result['count'] if result else 0
        except:
            pass
        
        # Get scheduler info
        scheduler_info = {}
        if constants.scheduler:
            scheduler_info = {
                "running": constants.scheduler.running,
                "next_run": constants.scheduler.get_jobs()[0].next_run_time.strftime('%Y-%m-%d %H:%M:%S') if constants.scheduler.get_jobs() else None
            }
        
        status = {
            "system": {
                "status": "running",
                "last_monitoring_update": constants.monitoring_data.get('last_update'),
                "timezone": str(constants.CAMEROON_TZ)
            },
            "database": {
                "connected": db_connected,
                "error": db_error,
                "host": f"{os.getenv('LOCAL_DB_HOST')}:{os.getenv('LOCAL_DB_PORT')}",
                "database": os.getenv('LOCAL_DB_NAME')
            },
            "tickets": {
                "json_total": len(json_tickets),
                "database_total": db_ticket_count,
                "open_tickets": len(open_tickets),
                "closed_tickets": len(closed_tickets),
                "sync_status": "synced" if len(json_tickets) == db_ticket_count else "out_of_sync"
            },
            "monitoring": {
                "databases_monitored": len(constants.DB_CONFIGS),
                "scheduler_running": constants.scheduler.running if constants.scheduler else False,
                **scheduler_info
            }
        }
        
        return JSONResponse(status)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@tickets_router.get('/api')
def get_tickets():
    """Get all tickets as JSON"""
    tickets = load_tickets(open_only=False)
    for i, ticket in enumerate(tickets):
        tickets[i]["human_readable"] = ttr_to_human_readable(ticket.get("ttr_seconds"))
    return JSONResponse(tickets)


@tickets_router.get('/test/orange-api')
def test_orange_api():
    """Endpoint to test Orange API connection and authentication"""
    try:
        if not constants.orange_api_client:
            raise HTTPException(status_code=500, detail="Orange API client not initialized")

        logger.debug(f"""
        === Testing Orange API Connection ===
        Base URL: {constants.orange_api_client.server}
        Token Endpoint: {constants.orange_api_client.token_endpoint}
        Trouble Ticket Endpoint: {constants.orange_api_client.trouble_ticket_endpoint}
        ====================================
        """)

        # Test connection and token acquisition
        success = constants.orange_api_client.test_connection()

        if success:
            message = "Orange API connection test successful!"
            status = "success"

            # Get token info if available
            token_info = {
                "token_obtained": bool(constants.orange_api_client.access_token),
                "token_type": constants.orange_api_client.token_type,
                "expires_at": constants.orange_api_client.token_expires_at.isoformat() if constants.orange_api_client.token_expires_at else None
            }
        else:
            message = "Orange API connection test failed. Check network connectivity and credentials."
            status = "failed"
            token_info = {
                "token_obtained": False,
                "token_type": None,
                "expires_at": None
            }

        return JSONResponse({
            "status": status,
            "message": message,
            "orange_api": {
                "base_url": constants.orange_api_client.server,
                "client_id": constants.orange_api_client.client_id,
                "token_endpoint": constants.orange_api_client.token_endpoint,
                "trouble_ticket_endpoint": constants.orange_api_client.trouble_ticket_endpoint,
                **token_info
            }
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Orange API test error: {str(e)}")

