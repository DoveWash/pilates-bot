import requests
import json
from dotenv import load_dotenv
import os
from securitytoken import get_security_token

def login_request(securityToken):
  # Load .env file into environment variables
  load_dotenv()

  USERNAME = os.getenv("API_USERNAME")
  PASSWORD = os.getenv("API_PASSWORD")


  url = "https://widgetapi.hapana.com/v2/wAPI/auth/login/"

  payload = json.dumps({
    "email": USERNAME,
    "password": PASSWORD
    })

  headers = {
    'securityToken': securityToken,
    'Content-Type': 'application/json',
    }

  #  POST to API
  response = requests.post(url=url, headers=headers, data=payload)

  # Save response to JSON
  json_response = response.json()

  # Save Access token from json_response
  access_token = json_response['data']['accessToken']

  # Save ClientID from json_response
  client_id = json_response['data']['clientID']

  # save cookies
  cookie = response.cookies
  cookie_formatted = cookie.get("PHPSESSID")


  # print(f"The access token is: {access_token}")
  # print(f"The clientId is: {client_id}")
  # print(f"The securityToken is: {securityToken}")
  # print(f"The cookie ID is: {response.cookies}")
  print("Running login_request...")
  return access_token, client_id, cookie_formatted

