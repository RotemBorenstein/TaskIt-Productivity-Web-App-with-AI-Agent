from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def settings_page(request):
    """Display the user settings page."""
    return render(request, 'main/settings.html')


@login_required
def email_suggestions_page(request):
    """Display the dedicated email suggestions review page."""
    return render(request, "main/email_suggestions.html")
