from django.shortcuts import render
from django.contrib.auth.decorators import login_required

@login_required
def settings_page(request):
    """Display the user settings page."""
    return render(request, 'main/settings.html')
