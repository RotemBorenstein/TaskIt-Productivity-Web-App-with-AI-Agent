from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import login as auth_login
from django.conf import settings
from django.shortcuts import render, redirect

def home(request):
    if request.user.is_authenticated:
        return redirect("main:tasks")

    return render(
        request,
        "main/home.html",
        {"enable_public_signup": settings.ENABLE_PUBLIC_SIGNUP},
    )

def signup(request):
    if not settings.ENABLE_PUBLIC_SIGNUP:
        return redirect("main:login")

    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Log the user in immediately after signup:
            auth_login(request, user)
            return redirect('main:tasks')
    else:
        form = UserCreationForm()
    return render(request, 'main/signup.html', {'form': form})



