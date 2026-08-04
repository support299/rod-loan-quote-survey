
from django.urls import path
from accounts.views import auth_connect,tokens,callback


urlpatterns = [
    path("auth/connect/", auth_connect, name="oauth_connect"),
    path("auth/tokens/", tokens, name="oauth_tokens"),
    path("auth/callback/", callback, name="oauth_callback"),
]
