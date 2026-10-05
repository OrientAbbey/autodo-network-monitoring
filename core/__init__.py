"""
CORE MODULE - Centralized data collection, processing, and alert management
All background tasks and data processing logic is centralized here
"""

from .database_manager import DataCollector
from .ticket_manager import TicketManager

__all__ = ["DataCollector", "TicketManager"]
