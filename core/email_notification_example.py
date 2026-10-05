#!/usr/bin/env python3
"""
Exemple d'utilisation du système de notification d'emails pour les nouveaux tickets
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.email_config import email_sender

def example_new_ticket_notification():
    """Exemple complet d'envoi de notification pour un nouveau ticket avec tous les tickets ouverts"""

    # Exemple de données de ticket
    new_ticket = {
        'id': 123,
        'type': 'traffic',
        'status': 'open',
        'Agent': 'DIAGNET',
        'Severity': 'critical',
        'site': 'Paris-DC1',
        'database_name': 'PROD_DB',
        'timestamp': '2024-01-09 10:30:00',
        'value': 'High latency detected',
        'description': 'Traffic anomaly detected on main link',
        'orange_ticket_id': 'ORANGE-45678',
        'pertes_warnings': True
    }

    # Exemple de tickets ouverts existants
    existing_open_tickets = [
        {
            'id': 121,
            'type': 'connectivity',
            'status': 'open',
            'Agent': 'DIAGNET',
            'Severity': 'high',
            'site': 'Paris-DC1',
            'database_name': 'PROD_DB',
            'timestamp': '2024-01-09 08:15:00',
            'value': 'Connection timeout',
            'description': 'Database connection issues',
            'orange_ticket_id': 'ORANGE-45676'
        },
        {
            'id': 122,
            'type': 'performance',
            'status': 'open',
            'Agent': 'DIAGNET',
            'Severity': 'medium',
            'site': 'Paris-DC2',
            'database_name': 'TEST_DB',
            'timestamp': '2024-01-09 09:45:00',
            'value': 'Slow query performance',
            'description': 'Query execution time above threshold',
            'orange_ticket_id': 'ORANGE-45677'
        }
    ]

    print("Envoi de notifications pour nouveau ticket (via send_new_ticket_notification)...")
    print(f"Nouveau ticket ID: {new_ticket['id']}")
    print(f"Total tickets ouverts: {len(existing_open_tickets) + 1}")

    # Préparer les données (nouveau ticket + tickets existants)
    all_tickets = existing_open_tickets + [new_ticket]

    # Charger les destinataires depuis le fichier
    recipients = email_sender._load_recipients()

    # Utiliser la fonction unifiée pour envoyer des notifications personnalisées
    results = email_sender.send_ticket_notification(
        ticket_data=all_tickets,
        recipients=recipients,
        highlight_ticket_id=str(new_ticket['id']),
        personalize=True
    )

    print("\nRésultats de l'envoi:")
    print(f"Emails envoyés avec succès: {results['success_count']}")
    if results['failed_recipients']:
        print("Échecs:")
        for failed in results['failed_recipients']:
            print(f"  - {failed}")

    return results

def example_manual_ticket_notification():
    """Exemple d'envoi manuel de notification pour des tickets existants"""

    tickets = [
        {
            'id': 124,
            'type': 'traffic',
            'status': 'open',
            'Agent': 'DIAGNET',
            'Severity': 'critical',
            'site': 'Paris-DC1',
            'database_name': 'PROD_DB',
            'timestamp': '2024-01-09 11:00:00',
            'value': 'Packet loss detected',
            'description': 'Network packet loss on backup link',
            'orange_ticket_id': 'ORANGE-45679'
        }
    ]

    print("\nEnvoi de notification manuelle pour tickets existants...")

    # Envoyer à tous les destinataires configurés
    recipients = email_sender._load_recipients()
    print(f"Destinataires configurés: {len(recipients)}")
    for recipient in recipients:
        print(f"  - {recipient['name']} <{recipient['email']}>")

    # Envoyer la notification
    success = email_sender.send_ticket_notification(
        ticket_data=tickets,
        recipients=[r['email'] for r in recipients]
    )

    print(f"Notification envoyée: {'Oui' if success else 'Non'}")

def example_simple_new_ticket():
    """Exemple simple d'utilisation de la fonction unifiée"""

    new_ticket = {
        'id': 999,
        'type': 'traffic',
        'status': 'open',
        'Agent': 'DIAGNET',
        'Severity': 'high',
        'site': 'Test-Site',
        'database_name': 'TEST_DB',
        'timestamp': '2024-01-09 12:00:00',
        'value': 'Test alert',
        'description': 'Test ticket for demonstration',
        'orange_ticket_id': 'TEST-999'
    }

    print("\n=== Exemple simple : Nouveau ticket personnalisé ===")

    # Charger les destinataires depuis le fichier
    recipients = email_sender._load_recipients()

    # Utiliser la fonction unifiée pour envoyer des notifications personnalisées
    results = email_sender.send_ticket_notification(
        ticket_data=new_ticket,
        recipients=recipients,
        # app_url="http://localhost:8000/dashboard",
        highlight_ticket_id=str(new_ticket['id']),
        personalize=True
    )

    print(f"Résultat: {results['success_count']} email(s) envoyé(s)")
    if results['failed_recipients']:
        print(f"Échecs: {results['failed_recipients']}")

    # Exemple classique : notification manuelle à des destinataires spécifiques
    print("\n=== Exemple : Notification classique ===")
    success = email_sender.send_ticket_notification(
        ticket_data=new_ticket,
        recipients=["test@example.com", "admin@example.com"]
    )
    print(f"Notification classique: {'Réussie' if success else 'Échouée'}")


if __name__ == "__main__":
    print("=== Exemple du système de notification d'emails ===\n")

    # Vérifier que les destinataires sont configurés
    recipients = email_sender._load_recipients()
    if not recipients:
        print("[WARNING] Aucun destinataire configuré dans email_recipients.txt")
        print("Veuillez ajouter des destinataires dans le fichier core/email_recipients.txt")
        sys.exit(1)

    print(f"[OK] {len(recipients)} destinataire(s) configuré(s)")

    # Exemple 1: Notification de nouveau ticket (nouvelle approche)
    example_simple_new_ticket()

    # Exemple 2: Notification complète de nouveau ticket
    example_new_ticket_notification()

    # Exemple 3: Notification manuelle
    example_manual_ticket_notification()

    print("\n=== Fin de l'exemple ===")
