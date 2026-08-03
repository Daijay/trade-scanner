# notify.py
"""Delivery: Telegram + email. PLAN.md SS11.

Both senders are independently try/excepted inside send_digest -- a
Telegram failure must never block email and vice versa, and neither may
crash the scan. Both read credentials from environment variables (loaded
via python-dotenv locally, GitHub Actions Secrets in CI) and log a clear
"not configured, skipping" message when empty rather than raising, so the
rest of the pipeline can be verified before real credentials exist.
"""

import asyncio
import email.mime.text
import logging
import os
import smtplib

import requests
from dotenv import load_dotenv

load_dotenv()

import telegram  # noqa: E402  (import after load_dotenv, matches analyst.py's anthropic import convention)

import config  # noqa: E402
from digest import split_for_telegram  # noqa: E402

logger = logging.getLogger(__name__)

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587


def send_telegram(message: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.info("Telegram not configured, skipping.")
        return False

    try:
        bot = telegram.Bot(token)
        parts = split_for_telegram(message)

        async def _send_all():
            for part in parts:
                await bot.send_message(chat_id=chat_id, text=part)

        asyncio.run(_send_all())
        return True
    except Exception as e:
        logger.warning("Telegram delivery failed: %r", e)
        return False


def send_email(message: str) -> bool:
    address = os.getenv("EMAIL_ADDRESS")
    app_password = os.getenv("EMAIL_APP_PASSWORD")
    if not address or not app_password:
        logger.info("Email not configured, skipping.")
        return False

    try:
        msg = email.mime.text.MIMEText(message, "plain")
        msg["Subject"] = "Trade Scanner Digest"
        msg["From"] = address
        msg["To"] = address

        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.starttls()
            server.login(address, app_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.warning("Email delivery failed: %r", e)
        return False


def send_daily_report(message: str) -> bool:
    address = os.getenv("EMAIL_ADDRESS")
    app_password = os.getenv("EMAIL_APP_PASSWORD")
    if not address or not app_password:
        logger.info("Email not configured, skipping.")
        return False

    try:
        msg = email.mime.text.MIMEText(message, "plain")
        msg["Subject"] = "Trade Scanner — Daily Report"
        msg["From"] = address
        msg["To"] = address

        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.starttls()
            server.login(address, app_password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.warning("Email delivery failed: %r", e)
        return False


def send_discord(message: str) -> bool:
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.info("Discord not configured, skipping.")
        return False

    try:
        parts = split_for_telegram(message, limit=config.DISCORD_MESSAGE_LIMIT)
        for part in parts:
            response = requests.post(webhook_url, json={"content": part}, timeout=15)
            response.raise_for_status()
        return True
    except Exception as e:
        logger.warning("Discord delivery failed: %r", e)
        return False


def send_digest(message: str) -> None:
    try:
        send_telegram(message)
    except Exception as e:
        logger.warning("Unexpected error sending Telegram: %r", e)

    try:
        send_email(message)
    except Exception as e:
        logger.warning("Unexpected error sending email: %r", e)

    try:
        send_discord(message)
    except Exception as e:
        logger.warning("Unexpected error sending Discord: %r", e)
