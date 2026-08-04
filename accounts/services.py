import requests
from accounts.models import GHLAuthCredentials
from accounts.models import GHLCustomField
def get_location_name(location_id: str, access_token: str) -> str:
    url = f"https://services.leadconnectorhq.com/locations/{location_id}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Version": "2021-07-28"
    }

    response = requests.get(url, headers=headers)
    response.raise_for_status()  # Raise exception for HTTP errors

    data = response.json()
    return data



def sync_custom_fields_to_db(location_id: str, access_token: str) -> dict:
    """
    Fetch custom fields from GHL API and save them to GHLCustomField model.
    This allows us to look up custom field IDs by name and location_id instead of hardcoding them.
    
    Args:
        location_id (str): The location ID for the subaccount
        access_token (str): Bearer token for authentication
        
    Returns:
        dict: Summary with counts of created and updated custom fields
    """
    try:
        # Get the account (GHLAuthCredentials) for this location
        account = GHLAuthCredentials.objects.filter(location_id=location_id).first()
        if not account:
            print(f"❌ [CUSTOM FIELDS SYNC] No GHLAuthCredentials found for location_id: {location_id}")
            return {"created": 0, "updated": 0, "total": 0}
        
        # Fetch contact + opportunity custom fields from API (merged by id)
        custom_fields_data = {}
        for model in ("contact", "opportunity"):
            try:
                chunk = fetch_location_custom_fields(location_id, access_token, model=model)
                if chunk:
                    custom_fields_data.update(chunk)
            except Exception as ex:
                print(f"⚠️ [CUSTOM FIELDS SYNC] model={model} failed for {location_id}: {ex}")

        if not custom_fields_data:
            print(f"⚠️ [CUSTOM FIELDS SYNC] No custom fields found for location_id: {location_id}")
            return {"created": 0, "updated": 0, "total": 0}
        
        created_count = 0
        updated_count = 0
        
        # Sync each custom field to database
        for field_id, field_info in custom_fields_data.items():
            field_name = field_info.get("name")
            if not field_name:
                continue
            
            # Determine field type from fieldKey or default to text
            field_key = field_info.get("fieldKey", "")
            field_type = "text"  # default
            if "number" in field_key.lower() or "sqft" in field_key.lower() or "floor" in field_key.lower():
                field_type = "number"
            elif "date" in field_key.lower():
                field_type = "date"
            elif "url" in field_key.lower():
                field_type = "url"
            
            # Create or update custom field
            custom_field, created = GHLCustomField.objects.update_or_create(
                account=account,
                ghl_field_id=field_id,
                defaults={
                    "field_name": field_name,
                    "field_type": field_type,
                    "is_active": True,
                }
            )
            
            if created:
                created_count += 1
            else:
                updated_count += 1
        
        print(f"✅ [CUSTOM FIELDS SYNC] Synced {len(custom_fields_data)} custom fields for location_id: {location_id} ({created_count} created, {updated_count} updated)")
        return {
            "created": created_count,
            "updated": updated_count,
            "total": len(custom_fields_data)
        }
        
    except Exception as e:
        print(f"❌ [CUSTOM FIELDS SYNC] Error syncing custom fields: {str(e)}")
        import traceback
        traceback.print_exc()
        return {"created": 0, "updated": 0, "total": 0, "error": str(e)}



def fetch_location_custom_fields(location_id: str, access_token: str, model: str = "contact") -> dict:
    """
    Fetch custom fields for a given location from GoHighLevel API and return a dict with id as key and a dict of name, fieldKey, parentId as value.

    Args:
        location_id (str): The location ID for the subaccount
        access_token (str): Bearer token for authentication
        model (str): GHL model, e.g. "contact" or "opportunity"

    Returns:
        dict: {id: {"name": ..., "fieldKey": ..., "parentId": ...}, ...}
    Raises:
        Exception: If the API request fails
    """
    url = f"https://services.leadconnectorhq.com/locations/{location_id}/customFields?model={model}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
        "Version": "2021-07-28"
    }
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        fields = data.get("customFields", [])
        return {
            f.get("id"): {
                "name": f.get("name"),
                "fieldKey": f.get("fieldKey"),
                "parentId": f.get("parentId")
            }
            for f in fields if f.get("id")
        }
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        raise Exception(f"Failed to fetch custom fields: {e}")
