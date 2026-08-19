"""Save drafts to Gmail. Never sends.

Deliberately built on plain REST rather than google-api-python-client: the two
calls we need are simple, and the client libraries drag in a large dependency
tree for no benefit here.

The scope is gmail.compose, which permits creating drafts and nothing else.
gmail.send is never requested, so even a compromised token cannot mail anyone
from the account.
"""

import base64
import time
from email.message import EmailMessage
from email.utils import formataddr

from . import config, web

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"


def configured():
    return bool(config.GOOGLE_OAUTH_CLIENT_ID and config.GOOGLE_OAUTH_CLIENT_SECRET)


def authorize_url(redirect_uri, state):
    """Where to send the browser to start consent."""
    from urllib.parse import urlencode

    params = {
        "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": config.GMAIL_SCOPE,
        "state": state,
        "access_type": "offline",   # we want a refresh token
        "prompt": "consent",        # ...and Google only sends one when asked
        "include_granted_scopes": "true",
    }
    return "%s?%s" % (AUTH_URL, urlencode(params))


def exchange_code(code, redirect_uri):
    """Authorization code -> token bundle. Returns (tokens, error)."""
    payload, error = web.api(
        "POST",
        TOKEN_URL,
        data={
            "code": code,
            "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    if error:
        return None, error

    payload = payload or {}
    if not payload.get("access_token"):
        return None, "no access token in the response"
    payload["expires_at"] = time.time() + int(payload.get("expires_in") or 3600)
    return payload, None


def refresh(tokens):
    """Trade the refresh token for a new access token. Returns (tokens, error)."""
    if not tokens.get("refresh_token"):
        return None, "no refresh token stored -- reconnect Gmail"

    payload, error = web.api(
        "POST",
        TOKEN_URL,
        data={
            "refresh_token": tokens["refresh_token"],
            "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
    )
    if error:
        return None, error

    merged = dict(tokens)
    merged.update(payload or {})
    merged["expires_at"] = time.time() + int((payload or {}).get("expires_in") or 3600)
    return merged, None


def ensure_fresh(tokens):
    """Refresh if the access token is expired or about to be."""
    if not tokens:
        return None, "not connected"
    if tokens.get("expires_at", 0) - 60 > time.time():
        return tokens, None
    return refresh(tokens)


def _headers(tokens):
    return {"Authorization": "Bearer %s" % tokens["access_token"]}


def account_email(tokens):
    payload, error = web.api("GET", PROFILE_URL, headers=_headers(tokens))
    if error:
        return None, error
    return (payload or {}).get("emailAddress"), None


def build_mime(to_address, subject, body, from_name=None, to_name=None):
    """A minimal RFC 5322 message, base64url encoded the way Gmail wants it."""
    message = EmailMessage()
    message["To"] = formataddr((to_name, to_address)) if to_name else to_address
    message["Subject"] = subject
    if from_name:
        message["From"] = from_name
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def create_draft(tokens, to_address, subject, body, to_name=None):
    """Save a draft in the signed-in account. Returns (draft, error).

    This creates a draft and stops. Sending is a separate Gmail scope we never
    ask for, so the user opens Gmail, reads it, and decides.
    """
    if not to_address:
        return None, "no recipient address"

    fresh, error = ensure_fresh(tokens)
    if error:
        return None, error

    raw = build_mime(to_address, subject, body, to_name=to_name)
    payload, error = web.api(
        "POST",
        DRAFTS_URL,
        json={"message": {"raw": raw}},
        headers=dict(_headers(fresh), **{"Content-Type": "application/json"}),
    )
    if error:
        return None, error

    draft_id = (payload or {}).get("id")
    return {
        "draft_id": draft_id,
        "url": "https://mail.google.com/mail/u/0/#drafts",
        "tokens": fresh,  # caller persists these; they may have been refreshed
    }, None
