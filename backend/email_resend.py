"""Minimal Resend adapter for MotionLab transactional authentication email.

Uses the standard library so the backend gains no runtime dependency. The API
key and sender identity are read at send time from server-side environment
variables. No email is sent unless a request reaches this adapter.
"""
from __future__ import annotations

import json
import os
from html import escape
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


RESEND_ENDPOINT = "https://api.resend.com/emails"
REQUEST_TIMEOUT_SECONDS = 10


def build_resend_sender():
    """Return the callable expected by the auth router.

    Required environment:
      RESEND_API_KEY: Resend API key with email-send permission.
      MOTIONLAB_EMAIL_FROM: verified sender, e.g. MotionLab <auth@auth.example.com>.
    """

    def send_magic_link(recipient: str, link: str) -> None:
        api_key = os.getenv("RESEND_API_KEY", "").strip()
        sender = os.getenv("MOTIONLAB_EMAIL_FROM", "").strip()
        if not api_key or not sender:
            raise RuntimeError("Resend email delivery is not configured.")
        if "\r" in sender or "\n" in sender:
            raise RuntimeError("MOTIONLAB_EMAIL_FROM contains invalid header characters.")

        safe_link = escape(link, quote=True)
        safe_recipient = escape(recipient)
        payload = {
            "from": sender,
            "to": [recipient],
            "subject": "Your MotionLab sign-in link",
            "text": (
                "Use this one-time link to sign in to MotionLab. "
                "It expires in 15 minutes and can only be used once.\n\n"
                f"{link}\n\n"
                "If you did not request this email, you can ignore it."
            ),
            "html": (
                '<div style="font-family:Arial,sans-serif;max-width:560px;margin:auto">'
                '<h2>Sign in to MotionLab</h2>'
                '<p>Use the button below to securely access your MotionLab workspace.</p>'
                f'<p><a href="{safe_link}" style="display:inline-block;padding:12px 18px;'
                'background:#202b36;color:#fff;text-decoration:none;border-radius:6px">'
                "Sign in to MotionLab</a></p>"
                '<p>This one-time link expires in 15 minutes.</p>'
                f'<p style="color:#667085;font-size:12px">Requested for {safe_recipient}. '
                "If you did not request this email, ignore it.</p>"
                "</div>"
            ),
        }
        request = Request(
            RESEND_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                if not 200 <= response.status < 300:
                    raise RuntimeError("Resend rejected the email request.")
                # Consume the response without logging provider data or recipient details.
                response.read()
        except HTTPError as exc:
            # Do not include the provider response body, API key, or email address in logs.
            raise RuntimeError(f"Resend returned HTTP {exc.code}.") from None
        except (URLError, TimeoutError, OSError):
            raise RuntimeError("Resend could not be reached.") from None

    return send_magic_link
