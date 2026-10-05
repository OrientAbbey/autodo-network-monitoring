import json
import logging
import os
import smtplib
import time
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import dotenv

# Load environment variables
dotenv.load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)


class EmailSender:
    def __init__(self):
        # Email server configuration with defaults
        self.server_mail = os.getenv("SERVER_MAIL", "localhost")
        self.server_port = int(os.getenv("SERVER_PORT", "25"))
        self.sender_email = os.getenv("SENDER_EMAIL", "noreply@orange.com")
        self.sender_name = os.getenv("SENDER_NAME", "AUTODO System")
        self.app_url = os.getenv("APP_TICKETS_URL", 'http://localhost:8000')
        
        self.notifications_file = Path(__file__).parent / "email_notifications.json"
        self.recipients_file = Path(__file__).parent / "email_recipients.txt"

    def _load_notifications(self):
        """Load email notifications tracking data"""
        if self.notifications_file.exists():
            try:
                with open(self.notifications_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Error loading notifications file: {e}")
        return {}

    def _save_notifications(self, notifications):
        """Save email notifications tracking data"""
        try:
            with open(self.notifications_file, 'w', encoding='utf-8') as f:
                json.dump(notifications, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving notifications file: {e}")

    def _should_send_notification(self, recipient, ticket_ids):
        """Check if we should send notification to this recipient for these tickets"""
        notifications = self._load_notifications()
        recipient_key = recipient.lower().strip()

        if recipient_key not in notifications:
            return True

        recipient_data = notifications[recipient_key]
        last_sent = recipient_data.get('last_sent', '')
        sent_ticket_ids = set(recipient_data.get('ticket_ids', []))

        # If no tickets were sent before, or if there are new tickets, send notification
        current_ticket_ids = set(ticket_ids)
        new_tickets = current_ticket_ids - sent_ticket_ids

        return len(new_tickets) > 0

    def _should_send_new_ticket_notification(self, recipient, current_open_ticket_ids):
        """
        Check if we should send new ticket notification to this recipient
        For new ticket notifications, we send if there are any open tickets the recipient hasn't been notified about
        """
        notifications = self._load_notifications()
        recipient_key = recipient.lower().strip()

        if recipient_key not in notifications:
            return True

        recipient_data = notifications[recipient_key]
        sent_ticket_ids = set(recipient_data.get('ticket_ids', []))
        current_open_ids = set(current_open_ticket_ids)

        # Check if there are any open tickets not yet notified to this recipient
        unnotified_tickets = current_open_ids - sent_ticket_ids

        return len(unnotified_tickets) > 0

    def _update_notification_tracking(self, recipient, ticket_ids):
        """Update notification tracking for recipient"""
        notifications = self._load_notifications()
        recipient_key = recipient.lower().strip()

        notifications[recipient_key] = {
            'last_sent': datetime.now().isoformat(),
            'ticket_ids': list(set(ticket_ids))  # Remove duplicates
        }

        self._save_notifications(notifications)

    def _load_recipients(self):
        """Load email recipients from txt file"""
        recipients = []
        if self.recipients_file.exists():
            try:
                with open(self.recipients_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        # Skip empty lines and comments
                        if not line or line.startswith('#'):
                            continue
                        # Parse format: nom,email
                        if ',' in line:
                            parts = line.split(',', 1)
                            if len(parts) == 2:
                                name = parts[0].strip()
                                email = parts[1].strip()
                                if name and email:
                                    recipients.append({'name': name, 'email': email})
            except Exception as e:
                logger.error(f"Error loading recipients file: {e}")
        else:
            logger.warning(f"Recipients file not found: {self.recipients_file}")

        return recipients

    def _get_ticket_card_data(self, ticket_data):
        """Extract and compute data needed for ticket card generation"""
        # Determine status color for this ticket
        status_color = "#4CAF50" if ticket_data.get("status") == "closed" else "#ff4444"

        # Determine losses indicator
        has_pertes = ticket_data.get("type") == "traffic" and ticket_data.get("pertes_warnings")
        pertes_indicator = (
            '<span style="color: #ff6b6b; font-weight: bold; margin-left: 10px;">Pertes: High</span>'
            if has_pertes
            else ""
        )

        # Orange ticket ID
        orange_id = (
            ticket_data.get("orange_ticket_id")
            or ticket_data.get("external_api_ticket_id")
            or "N/A"
        )

        return {
            'status_color': status_color,
            'pertes_indicator': pertes_indicator,
            'orange_id': orange_id,
            'ticket_data': ticket_data
        }

    def _generate_ticket_card_html(self, ticket_data, status_color=None, is_new_ticket=False):
        """Generate HTML for a single ticket card"""
        card_data = self._get_ticket_card_data(ticket_data)

        # Use provided status_color or computed one
        final_status_color = status_color if status_color is not None else card_data['status_color']

        # Add new ticket highlight class if this is a new ticket
        highlight_class = " new-ticket-highlight" if is_new_ticket else ""
        new_ticket_badge = '<span style="background: #ff9800; color: white; padding: 2px 6px; border-radius: 3px; font-size: 0.8em; margin-left: 8px;">NOUVEAU</span>' if is_new_ticket else ""

        return f"""
        <div class="ticket-card{highlight_class}" style="border-left-color: {final_status_color};">
            <div class="ticket-header">
                <div>
                    <span class="ticket-type">{ticket_data.get('type', 'Unknown')}</span>
                    <span class="ticket-id">#{ticket_data.get('id', 'N/A')}</span>
                    {card_data['pertes_indicator']}
                    {new_ticket_badge}
                </div>
                <span class="ticket-status" style="background-color: {final_status_color};">{ticket_data.get('status', 'open')}</span>
            </div>

            <div class="ticket-info">
                <div class="info-row">
                    <span class="info-label">Agent:</span>
                    <span>{ticket_data.get('Agent', 'DIAGNET')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Severity:</span>
                    <span>{ticket_data.get('Severity', 'critical')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Orange Ticket:</span>
                    <span style="color: #ff9800; font-weight: bold;">{card_data['orange_id']}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Site:</span>
                    <span>{ticket_data.get('site', '-')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Database:</span>
                    <span>{ticket_data.get('database_name', ticket_data.get('database', '-'))}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Timestamp:</span>
                    <span>{ticket_data.get('timestamp', '-')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Value:</span>
                    <span>{ticket_data.get('value', '-')}</span>
                </div>
                <div class="info-row">
                    <span class="info-label">Description:</span>
                    <span>{ticket_data.get('description', ticket_data.get('reason', '-'))}</span>
                </div>
            </div>
        </div>
        <br>
        """

    def _generate_css_styles(self):
        """Generate common CSS styles for emails"""
        return """
                    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f0f2f5; margin: 0; padding: 20px; }
                    .ticket-card {
                        background: white;
                        border-radius: 8px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                        padding: 20px;
                        max-width: 600px;
                        margin: 0 auto 20px auto;
                        border-left: 5px solid #ff4444;
                    }
                    .ticket-header {
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        margin-bottom: 20px;
                        border-bottom: 1px solid #eee;
                        padding-bottom: 10px;
                    }
                    .ticket-type {
                        font-weight: bold;
                        text-transform: uppercase;
                        color: #555;
                        background: #eee;
                        padding: 4px 8px;
                        border-radius: 4px;
                        font-size: 0.9em;
                    }
                    .ticket-id {
                        color: #888;
                        margin-left: 10px;
                        font-size: 1.1em;
                    }
                    .ticket-status {
                        padding: 4px 12px;
                        border-radius: 12px;
                        font-size: 0.9em;
                        font-weight: bold;
                        text-transform: uppercase;
                        color: white;
                        background-color: #ff4444;
                    }
                    .info-row {
                        display: flex;
                        justify-content: space-between;
                        margin-bottom: 8px;
                        padding: 4px 0;
                        border-bottom: 1px solid #f9f9f9;
                    }
                    .info-label {
                        color: #666;
                        font-weight: 500;
                        min-width: 120px;
                    }
                    .app-link {
                        display: inline-block;
                        margin-top: 20px;
                        padding: 10px 20px;
                        background-color: #007bff;
                        color: white;
                        text-decoration: none;
                        border-radius: 5px;
                        font-weight: bold;
                        text-align: center;
                    }
                    .center { text-align: center; }
                    .tickets-summary {
                        text-align: center;
                        font-size: 18px;
                        font-weight: bold;
                        color: #ff4444;
                        margin-bottom: 20px;
                        background: white;
                        padding: 15px;
                        border-radius: 8px;
                        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                        max-width: 600px;
                        margin: 0 auto 20px auto;
                    }
                    .new-ticket-highlight {
                        border-left-color: #ff9800 !important;
                        background: linear-gradient(90deg, rgba(255,152,0,0.1) 0%, rgba(255,255,255,1) 10px);
                    }
                    .greeting {
                        font-size: 16px;
                        color: #555;
                        margin-bottom: 20px;
                        line-height: 1.5;
                    }
        """

    def send_email(self, recipients, subject, body, attachment_path=None):
        """
        Send email to one or multiple recipients with retry logic

        Args:
            recipients (str or list): Recipient email(s)
            subject (str): Email subject
            body (str): Email body content
            attachment_path (str, optional): Path to attachment file

        Returns:
            bool: True if email sent successfully, False otherwise
        """
        recipients_list = [recipients] if isinstance(recipients, str) else recipients
        max_retries = 3
        retry_delay = 15  # seconds

        for attempt in range(1, max_retries + 1):
            try:
                # Create message container
                msg = MIMEMultipart()
                msg["From"] = f"{self.sender_name} <{self.sender_email}>"
                msg["To"] = ", ".join(recipients_list)
                msg["Subject"] = subject

                # Add body to email
                msg.attach(MIMEText(body, "html"))

                # Add attachment if provided
                if attachment_path:
                    try:
                        with open(attachment_path, "rb") as attachment:
                            part = MIMEBase("application", "octet-stream")
                            part.set_payload(attachment.read())

                        encoders.encode_base64(part)
                        part.add_header(
                            "Content-Disposition",
                            f'attachment; filename= {attachment_path.split("/")[-1]}',
                        )
                        msg.attach(part)
                    except FileNotFoundError:
                        logger.warning(f"Attachment file not found: {attachment_path}")

                # Create SMTP session
                server = smtplib.SMTP(self.server_mail, self.server_port)

                # Enable security - try STARTTLS if available
                try:
                    server.starttls()
                except smtplib.SMTPNotSupportedError:
                    # STARTTLS not supported, continue without encryption
                    logger.debug("STARTTLS not supported, continuing without encryption")
                    pass

                # Send email
                text = msg.as_string()
                server.sendmail(self.sender_email, recipients_list, text)
                server.quit()

                logger.info(f"Email sent successfully to {recipients_list}")
                return True

            except Exception as e:
                logger.warning(
                    f"Attempt {attempt}/{max_retries} failed to send email: {str(e)}"
                )
                if attempt < max_retries:
                    time.sleep(retry_delay)
                else:
                    logger.error(
                        f"Failed to send email after {max_retries} attempts to {recipients_list}"
                    )
                    return False

    def send_ticket_notification(self, ticket_data, recipients, app_url=None, highlight_ticket_id=None, personalize=False):
        """
        Send ticket notification email (unified function)

        Args:
            ticket_data (dict or list): Ticket information (single ticket or list of tickets)
            recipients (str, list, or dict): Recipients can be:
                - str: single email address
                - list of str: list of email addresses
                - list of dict: list with {'name': str, 'email': str} for personalization
            app_url (str, optional): URL to view the ticket in the application
            highlight_ticket_id (str, optional): ID of ticket to highlight (shows "NOUVEAU" badge)
            personalize (bool, optional): If True, use personalized greetings with recipient names

        Returns:
            bool or dict: True if email sent successfully (legacy mode), or results dict for personalized mode
        """
        # Always treat tickets as a list
        if isinstance(ticket_data, list):
            tickets = ticket_data
        else:
            tickets = [ticket_data]

        # Filter open tickets only
        open_tickets = [t for t in tickets if t.get('status') == 'open']

        if not open_tickets:
            logger.info("No open tickets to notify about")
            return False

        # Normalize recipients format
        if isinstance(recipients, str):
            # Single email string
            recipients_list = [{'email': recipients, 'name': None}]
        elif isinstance(recipients, list) and recipients and isinstance(recipients[0], str):
            # List of email strings
            recipients_list = [{'email': email, 'name': None} for email in recipients]
        elif isinstance(recipients, list) and recipients and isinstance(recipients[0], dict):
            # List of dicts with name/email
            recipients_list = recipients
        else:
            logger.error(f"Invalid recipients format: {recipients}")
            return False

        # Get ticket IDs for tracking
        ticket_ids = [str(t.get('id', '')) for t in tickets]

        # Determine if we should return detailed results (for personalized mode)
        return_detailed_results = personalize or len(recipients_list) > 1

        if return_detailed_results:
            results = {'success_count': 0, 'failed_recipients': []}

        # Send to each recipient
        successful_sends = []

        for recipient_info in recipients_list:
            recipient_email = recipient_info['email']
            recipient_name = recipient_info.get('name')

            # Check if we should send notification to this recipient
            should_send = (personalize and self._should_send_new_ticket_notification(recipient_email, ticket_ids)) or \
                         (not personalize and self._should_send_notification(recipient_email, ticket_ids))

            if should_send:
                logger.debug(f"Sending ticket notification to {recipient_name or recipient_email} for tickets: {ticket_ids}")

                # Generate email content
                success = self._generate_and_send_ticket_email(
                    open_tickets, recipient_email, recipient_name, app_url,
                    highlight_ticket_id, personalize
                )

                if success:
                    successful_sends.append(recipient_email)
                    # Update tracking for this recipient
                    self._update_notification_tracking(recipient_email, ticket_ids)
                    logger.debug(f"Notification sent and tracked for {recipient_email}")
                    if return_detailed_results:
                        results['success_count'] += 1
                else:
                    logger.warning(f"Failed to send notification to {recipient_email}")
                    if return_detailed_results:
                        results['failed_recipients'].append(recipient_email)
            else:
                logger.debug(f"Skipping notification to {recipient_email} - already notified of these tickets")

        if return_detailed_results:
            return results
        else:
            return len(successful_sends) > 0

    def _generate_and_send_ticket_email(self, open_tickets, recipient_email, recipient_name=None, app_url=None, highlight_ticket_id=None, personalize=False):
        """
        Generate and send ticket email to a specific recipient

        Args:
            open_tickets (list): List of open tickets
            recipient_email (str): Recipient email address
            recipient_name (str, optional): Recipient name for personalization
            app_url (str, optional): URL to view tickets in the application
            highlight_ticket_id (str, optional): ID of ticket to highlight
            personalize (bool): Whether to use personalized content

        Returns:
            bool: True if email sent successfully, False otherwise
        """
        # Determine subject based on number of open tickets
        if len(open_tickets) == 1:
            ticket_type = open_tickets[0].get('type', 'Unknown')
            subject = f"Nouveau Ticket - {ticket_type} Issue" if personalize else f"Ticket Alert - {ticket_type} Issue"
        else:
            ticket_count = len(open_tickets)
            subject = f"Alertes Réseau - {ticket_count} Ticket{'s' if ticket_count > 1 else ''} Ouvert{'s' if ticket_count > 1 else ''}" if personalize else f"Network Monitoring Alert - {ticket_count} Open Ticket{'s' if ticket_count > 1 else ''}"

        # Generate individual ticket cards for all open tickets
        ticket_cards = ""
        for ticket_data in open_tickets:
            is_highlighted = highlight_ticket_id and str(ticket_data.get('id')) == str(highlight_ticket_id)
            ticket_cards += self._generate_ticket_card_html(ticket_data, is_new_ticket=is_highlighted)

        # Determine header and content based on personalization
        if personalize:
            if len(open_tickets) == 1:
                header_text = f"Bonjour {recipient_name},<br><br>Un nouveau ticket a été créé et nécessite votre attention."
                summary_div = ""
            else:
                header_text = f"Bonjour {recipient_name},<br><br>Un nouveau ticket a été créé. Voici tous les tickets actuellement ouverts nécessitant votre attention."
                summary_div = f'<div class="tickets-summary">ALERTE: {len(open_tickets)} Ticket{"s" if len(open_tickets) > 1 else ""} Ouvert{"s" if len(open_tickets) > 1 else ""} - Action Requise</div>'

            footer_text = """
                <p style="text-align: center; color: #666; font-size: 14px;">
                    Ceci est une notification automatique du système AUTODO.<br>
                    Veuillez prendre les mesures appropriées si nécessaire.
                </p>
            """
        else:
            if len(open_tickets) == 1:
                header_text = "AUTODO - Ticket Notification"
                summary_div = ""
            else:
                header_text = "AUTODO - Network Monitoring Alert"
                summary_div = f'<div class="tickets-summary">ALERTE: {len(open_tickets)} Open Ticket{"s" if len(open_tickets) > 1 else ""} Require Attention</div>'

            footer_text = """
                <p>This is an automated notification from AUTODO system.</p>
                <p>Please take appropriate action if required.</p>
            """

        body = f"""
        <html>
        <head>
            <style>
                {self._generate_css_styles()}
            </style>
        </head>
        <body>
            {"<div style=\"font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 600px; margin: 0 auto;\">" if personalize else ""}
                <div class="header">{header_text}</div>
                <br>
                {summary_div}
                {ticket_cards}
                <div class="center">
                    <a href="{app_url if app_url else self.app_url}" class="app-link">{"Voir Tous les Tickets dans l'Application" if personalize else "View All Tickets in Application"}</a>
                </div>
                <br>
                {footer_text}
            {"" if personalize else ""}
        </body>
        </html>
        """

        # Send email to this specific recipient
        return self.send_email(recipient_email, subject, body)

    def send_new_ticket_notification(self, new_ticket_data, all_open_tickets=None, app_url=None):
        """
        Send notification for new ticket creation to all configured recipients

        Args:
            new_ticket_data (dict): The newly created ticket data
            all_open_tickets (list, optional): All currently open tickets. If None, will only include new ticket
            app_url (str, optional): URL to view tickets in the application

        Returns:
            dict: Results with 'success_count' and 'failed_recipients'
        """
        # Load all recipients from file
        recipients = self._load_recipients()

        if not recipients:
            logger.warning("No recipients configured for new ticket notifications")
            return {'success_count': 0, 'failed_recipients': []}

        # Prepare ticket data - include all open tickets or just the new one
        if all_open_tickets:
            # Remove new ticket from all_open_tickets if it's already there to avoid duplicates
            filtered_open_tickets = [t for t in all_open_tickets if str(t.get('id')) != str(new_ticket_data.get('id'))]
            ticket_data = filtered_open_tickets + [new_ticket_data]
        else:
            ticket_data = [new_ticket_data]

        # Send using the unified function
        return self.send_ticket_notification(
            ticket_data=ticket_data,
            recipients=recipients,
            app_url=app_url,
            highlight_ticket_id=str(new_ticket_data.get('id')),
            personalize=True
        )


# Global email sender instance
email_sender = EmailSender()
