from django.urls import path

from .views import (
    CliLoginView,
    LogoutView,
    RegisterView,
    ResendVerificationView,
    UserView,
    VerifyEmailView,
    get_csrf_token,
)

urlpatterns = [
    path(
        "register/",
        RegisterView.as_view(),
        name="auth_register",
    ),
    path(
        "verify/<str:uidb64>/<str:token>/",
        VerifyEmailView.as_view(),
        name="auth_verify",
    ),
    path("me/", UserView.as_view(), name="about_me"),
    path("login/", CliLoginView.as_view(), name="cli_login"),
    path("csrf/", get_csrf_token, name="csrf"),
    path(
        "resend-verification/",
        ResendVerificationView.as_view(),
        name="resend_verification",
    ),
    path("logout/", LogoutView.as_view(), name="logout"),
]
