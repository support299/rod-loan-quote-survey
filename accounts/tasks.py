import requests
from celery import shared_task
from accounts.models import GHLAuthCredentials
from decouple import config
@shared_task
def make_api_call():
    credentials = GHLAuthCredentials.objects.all()
    print("credentials token", credentials)
    for credential in credentials:
        refresh_token = credential.refresh_token

        response = requests.post('https://services.leadconnectorhq.com/oauth/token', data={
            'grant_type': 'refresh_token',
            'client_id': config("GHL_CLIENT_ID"),
            'client_secret': config("GHL_CLIENT_SECRET"),
            'refresh_token': refresh_token
        })
    
        obj, created = GHLAuthCredentials.objects.update_or_create(
                location_id= credential.location_id,
                defaults={
                    "access_token": response.json().get("access_token"),
                    "refresh_token": response.json().get("refresh_token"),
                    "expires_in": response.json().get("expires_in"),
                    "scope": response.json().get("scope"),
                }
            )
    