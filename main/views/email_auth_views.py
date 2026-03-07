from __future__ import annotations
from typing import Optional, List, Literal
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponseRedirect
from ninja import NinjaAPI, Schema
from ninja.errors import HttpError
from datetime import datetime

api = NinjaAPI(title="TaskIt email auth api", urls_namespace="email_auth")

# Schemas
class EmailIntegrationStatusOut(Schema):
    is_connected: bool
    provider: Optional[Literal["gmail", "outlook"]] = None
    email: Optional[str] = None

class ConnectionUrlOut(Schema):
    url: str

# Endpoints

@api.get("/status", response=EmailIntegrationStatusOut)
def get_email_integration_status(request):
    """
    Check if the user has an email account connected.
    """
    # Logic to check existing integration in database
    return {
        "is_connected": False,
        "provider": None,
        "email": None
    }

@api.get("/connect/gmail", response=ConnectionUrlOut)
def connect_gmail(request):
    """
    Step 1: Generate the Gmail OAuth authorization URL.
    """
    # Logic to generate Google OAuth URL with minimal read-only scopes
    return {"url": "https://accounts.google.com/o/oauth2/v2/auth?..."}

@api.get("/connect/outlook", response=ConnectionUrlOut)
def connect_outlook(request):
    """
    Step 1: Generate the Outlook OAuth authorization URL.
    """
    # Logic to generate Microsoft OAuth URL with minimal read-only scopes
    return {"url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?..."}

@api.get("/callback/gmail")
def callback_gmail(request, code: str, state: str = None):
    """
    Step 2: Handle the callback from Google, exchange code for token.
    Redirect back to TaskIt Settings -> Integrations.
    """
    # Logic to exchange code for tokens and save to DB
    return redirect("/settings/")

@api.get("/callback/outlook")
def callback_outlook(request, code: str, state: str = None):
    """
    Step 2: Handle the callback from Microsoft, exchange code for token.
    Redirect back to TaskIt Settings -> Integrations.
    """
    # Logic to exchange code for tokens and save to DB
    return redirect("/settings/")

@api.post("/disconnect")
def disconnect_email(request):
    """
    Disconnect the current email account.
    """
    # Logic to remove integration from DB
    return {"success": True}

@api.delete("/data")
def delete_email_data(request):
    """
    Delete all data derived from the connected email account.
    """
    # Logic to find and delete derived events/tasks
    return {"success": True}
