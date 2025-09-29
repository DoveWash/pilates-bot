import requests
import json

# Gets the security token that is used for the login request
def get_security_token():
    url = "https://widgetapi.hapana.com/v2/wAPI/site/settings"

    payload = {}
    headers = {
        # Random string that is assigned to the yarra classes. I've hard coded this in for now as its a required parameter to get a security token
    'wid': 'amhmQmdIdldDL3lRdHFpZ3BuOW40Zz09',
    }

    response = requests.request("GET", url, headers=headers, data=payload)
    http_response = response.json()
    security_token = http_response["securityToken"]

    print(f"Running get_security_token...")
    return security_token

