"""NOTIFICATIONS E-MAIL — nouveaux tickets (et alertes internes d'auto-supervision).

Corrections par rapport à l'ancien ``email_config.py`` :
* SMTP : délai d'expiration, UNE connexion pour tous les destinataires, ``with`` (jamais de fuite), STARTTLS avec
  contexte SSL vérifié quand le serveur l'annonce, plafond d'envois/minute (``SMTP_MAX_PER_MINUTE``) ;
* envoi hors du thread de détection (exécuteur à 1 thread) : un SMTP lent ne retarde plus le monitoring ;
* tous les champs injectés dans le HTML sont échappés ; mise en page par TABLES + styles en ligne (Outlook) ;
* état « qui a été notifié de quoi » en base (table ``notification_state``), plus de fichier JSON dans le code ;
* destinataires : variable ``EMAIL_RECIPIENTS`` ou fichier ``email_recipients.txt`` (non versionné).
"""
from __future__ import annotations

import html
import smtplib
import ssl
from concurrent.futures import ThreadPoolExecutor
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from collections.abc import Iterable

from sqlalchemy import text

from .config import fmt, now_local, settings
from .db import local_conn, local_tx
from .logger import get_logger
from .ratelimit import TokenBucket

logger = get_logger(__name__)
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notify")
_bucket = TokenBucket(max(settings.smtp_max_per_minute, 1) / 60.0, max(settings.smtp_max_per_minute, 1))
e = html.escape


def load_recipients() -> list[dict]:
    """``EMAIL_RECIPIENTS="Nom,mail;Nom2,mail2"`` ou fichier (une ligne ``nom,email``)."""
    lines: list[str] = []
    if settings.email_recipients:
        lines = [x for x in settings.email_recipients.split(";") if x.strip()]
    else:
        p = Path(settings.email_recipients_file)
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "," in line:
            name, mail = (x.strip() for x in line.split(",", 1))
            if name and "@" in mail:
                out.append({"name": name, "email": mail})
    return out


# ----------------------------------------------------------------------------
# État des notifications (base)
# ----------------------------------------------------------------------------
def _already_notified(recipient: str) -> set[str]:
    with local_conn() as c:
        r = c.execute(text("SELECT ticket_ids FROM notification_state WHERE recipient = :r"), {"r": recipient.lower()}).first()
    return {x for x in (r[0] or "").split(",") if x} if r else set()


def _remember(recipient: str, ticket_ids: Iterable) -> None:
    if settings.db_backend == "sqlite":
        sql = ("INSERT INTO notification_state (recipient, ticket_ids, sent_at) VALUES (:r, :t, :s) "
               "ON CONFLICT(recipient) DO UPDATE SET ticket_ids = excluded.ticket_ids, sent_at = excluded.sent_at")
    else:
        sql = ("INSERT INTO notification_state (recipient, ticket_ids, sent_at) VALUES (:r, :t, :s) "
               "ON DUPLICATE KEY UPDATE ticket_ids = :t, sent_at = :s")
    with local_tx() as c:
        c.execute(text(sql),
                  {"r": recipient.lower(), "t": ",".join(str(i) for i in ticket_ids), "s": fmt(now_local().replace(tzinfo=None))})


# ----------------------------------------------------------------------------
# HTML (tables + styles en ligne)
# ----------------------------------------------------------------------------
def _row(label: str, value) -> str:
    return (f'<tr><td style="padding:3px 10px 3px 0;color:#666;font-weight:bold;white-space:nowrap">{e(label)}</td>'
            f'<td style="padding:3px 0">{e(str(value))}</td></tr>')


def ticket_card(t: dict, highlight: bool = False) -> str:
    closed = t.get("status") == "closed"
    color = "#4CAF50" if closed else "#ff4444"
    orange_id = t.get("external_api_ticket_id") or t.get("orange_ticket_id") or "N/A"
    badges = ""
    if t.get("type") == "traffic" and t.get("pertes_warnings"):
        badges += ' <span style="color:#ff6b6b;font-weight:bold">Pertes: High</span>'
    if highlight:
        badges += ' <span style="background:#ff9800;color:#fff;padding:2px 6px;border-radius:3px;font-size:12px">NOUVEAU</span>'
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #ddd;border-left:6px solid {color};margin:0 0 14px 0">
<tr><td style="padding:10px 12px;background:#fafafa">
  <b>{e(str(t.get('type', 'Unknown')).upper())}</b> <span style="color:#666">#{e(str(t.get('id', 'N/A')))}</span>{badges}
  <span style="float:right;background:{color};color:#fff;padding:2px 8px;border-radius:3px;font-size:12px">{e(str(t.get('status', 'open')))}</span>
</td></tr>
<tr><td style="padding:8px 12px"><table role="presentation" cellpadding="0" cellspacing="0" style="font-size:14px">
{_row('Agent', t.get('agent', 'DIAGNET'))}{_row('Sévérité', t.get('severity', 'critical'))}
{_row('Ticket Orange', orange_id)}{_row('Site', t.get('site', '-'))}{_row('Base', t.get('database_name', '-'))}
{_row('Horodatage', t.get('timestamp', '-'))}{_row('Valeur', t.get('value', '-'))}
{_row('Description', t.get('description') or t.get('reason') or '-')}
</table></td></tr></table>"""


def build_email(open_tickets: list[dict], recipient_name: str, highlight_id=None) -> tuple[str, str]:
    n = len(open_tickets)
    subject = (f"Nouveau Ticket - {open_tickets[0].get('type', 'Unknown')} Issue" if n == 1
               else f"Alertes Réseau - {n} Tickets Ouverts")
    intro = ("Un nouveau ticket a été créé et nécessite votre attention." if n == 1 else
             "Un nouveau ticket a été créé. Voici tous les tickets actuellement ouverts nécessitant votre attention.")
    cards = "".join(ticket_card(t, str(t.get("id")) == str(highlight_id)) for t in open_tickets)
    body = f"""<html><body style="font-family:'Segoe UI',Tahoma,Verdana,sans-serif;color:#222">
<table role="presentation" width="600" align="center" cellpadding="0" cellspacing="0"><tr><td>
<p>Bonjour {e(recipient_name)},</p><p>{e(intro)}</p>
{cards}
<p style="text-align:center"><a href="{e(settings.app_tickets_url)}" style="background:#ff7900;color:#fff;padding:10px 18px;text-decoration:none;border-radius:4px">Voir tous les tickets dans l'application</a></p>
<p style="text-align:center;color:#666;font-size:13px">Notification automatique AUTODO — merci de prendre les mesures appropriées si nécessaire.</p>
</td></tr></table></body></html>"""
    return subject, body


# ----------------------------------------------------------------------------
# Envoi
# ----------------------------------------------------------------------------
def _send_batch(messages: list[tuple[str, str, str]]) -> tuple[int, list[str]]:
    """Envoie [(destinataire, sujet, html)] sur UNE connexion SMTP bornée. Retourne (réussites, échecs)."""
    ok, failed = 0, []
    if not settings.smtp_host or not messages:
        return 0, [m[0] for m in messages]
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout) as server:
            server.ehlo()
            if server.has_extn("starttls"):
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            for to, subject, body in messages:
                if not _bucket.acquire(timeout=60):
                    failed.append(to)
                    logger.warning("E-mail à %s différé : plafond d'envois/minute atteint", to)
                    continue
                try:
                    msg = MIMEMultipart("alternative")
                    msg["Subject"], msg["To"] = subject, to
                    msg["From"] = f"{settings.sender_name} <{settings.sender_email}>"
                    msg.attach(MIMEText(body, "html", "utf-8"))
                    server.sendmail(settings.sender_email, [to], msg.as_string())
                    ok += 1
                except smtplib.SMTPException as ex:
                    failed.append(to)
                    logger.warning("E-mail à %s refusé : %s", to, ex)
    except (OSError, smtplib.SMTPException) as ex:
        logger.error("SMTP indisponible (%s:%s) : %s", settings.smtp_host, settings.smtp_port, ex)
        failed = [m[0] for m in messages if m[0] not in failed] + failed
        failed = list(dict.fromkeys(failed))
    return ok, failed


def notify_new_tickets(new_tickets: list[dict], open_tickets: list[dict]) -> None:
    """Planifie (hors thread de détection) l'envoi aux destinataires qui n'ont pas encore été notifiés."""
    if new_tickets:
        _pool.submit(_notify_job, [dict(t) for t in new_tickets], [dict(t) for t in open_tickets])


def _notify_job(new_tickets: list[dict], open_tickets: list[dict]) -> None:
    try:
        recipients = load_recipients()
        if not recipients:
            logger.info("Aucun destinataire e-mail configuré")
            return
        ids = [t["id"] for t in open_tickets]
        highlight = new_tickets[0]["id"]
        messages, who = [], []
        for r in recipients:
            if set(map(str, ids)) - _already_notified(r["email"]):
                subject, body = build_email(open_tickets, r["name"], highlight)
                messages.append((r["email"], subject, body))
                who.append(r["email"])
        ok, failed = _send_batch(messages)
        for to in who:
            if to not in failed:
                _remember(to, ids)
        logger.info("E-mails de nouveaux tickets : %d envoyé(s), %d échec(s)", ok, len(failed))
    except Exception:
        logger.exception("Erreur lors de l'envoi des notifications")


def notify_internal(subject: str, message: str) -> None:
    """Alerte d'exploitation (auto-supervision, abandon Orange…) — texte simple, mêmes destinataires."""
    def job():
        try:
            body = f"<html><body><p>{e(message).replace(chr(10), '<br>')}</p></body></html>"
            _send_batch([(r["email"], f"[AUTODO] {subject}", body) for r in load_recipients()])
        except Exception:
            logger.exception("Alerte interne non envoyée")
    _pool.submit(job)


def shutdown() -> None:
    _pool.shutdown(wait=True, cancel_futures=False)
