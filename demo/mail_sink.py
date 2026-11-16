#!/usr/bin/env python3
"""Puits SMTP local pour la démo : conserve chaque message en .eml dans DEMO_MAIL_DIR.
Lancement : python mail_sink.py   (écoute 127.0.0.1:1025)"""
import os
import time
import itertools
from pathlib import Path
from aiosmtpd.controller import Controller

OUT = Path(os.getenv("DEMO_MAIL_DIR", "/tmp/autodo_demo_mail")); OUT.mkdir(parents=True, exist_ok=True)
_n = itertools.count(1)


class Sink:
    async def handle_DATA(self, server, session, envelope):
        (OUT / f"{next(_n):04d}_{int(time.time())}.eml").write_bytes(envelope.original_content)
        return "250 OK"


if __name__ == "__main__":
    c = Controller(Sink(), hostname="127.0.0.1", port=int(os.getenv("DEMO_SMTP_PORT", "1025")))
    c.start(); print("mail sink on", c.hostname, c.port, "->", OUT)
    try:
        while True: time.sleep(3600)
    except KeyboardInterrupt:
        c.stop()
