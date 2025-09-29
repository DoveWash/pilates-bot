import requests
import json
from webhook import discord_webhook
from dotenv import load_dotenv
import os
from webhook import discord_webhook





def book_class(client_id,authorization_header,securitytoken,classes_to_book):
    # Load .env file into environment variables
    load_dotenv()
    package_SubscriptionID = os.getenv("package_SubscriptionID")

  #   "checkoutID": "[insert sessionID here]",
  #  "SiteID" = amhmQmdIdldDL3lRdHFpZ3BuOW40Zz09 (This looks like its for yarra)
  #  "siteID": aTE4MEpReDA1bFdkN3kvaHkyNFludz09 (This looks like its for belco)

  # LOGIC FOR SENDING A BOOKING REQUEST TO BELCONNEN
    target_sessionDate = ""
    target_siteID = ""
    target_checkoutID = ""
    target_sessionName = ""
    target_instructorName = ""
    target_startTime =""

    url = (f"https://widgetapi.hapana.com/v2/wAPI/payments/checkout?clientID={client_id}")
    # checkoutID = SessionID in the get_class_list JSON response (This is the class identifier)

    headers = {
    'authorization': authorization_header,
    'content-type': 'application/json',
    'securitytoken': securitytoken,
    }

    for x in classes_to_book:
        # Handle which location to book at
        if x["address"] == "18 Benjamin Way, Belconnen ACT, Australia":
            target_siteID = "aTE4MEpReDA1bFdkN3kvaHkyNFludz09"
        else:
            target_siteID = "amhmQmdIdldDL3lRdHFpZ3BuOW40Zz09"
        # Save variables from classes_to_book to use in the booking payload
        target_sessionDate = x["sessionDate"]
        target_checkoutID = x["sessionID"]
        target_sessionName = x["sessionName"]
        target_instructorName =x["instructorData"][0]["instructorName"]
        target_startTime =x["startTime"]

        # Building payload
        payload = json.dumps({
        "paymentType": "sessions",
        "applyAccountCredits": False,
        "sessionDate": target_sessionDate,
        "paymentMethod": "validcredits",
        "checkoutID": target_checkoutID,
        "packageSubscriptionID": package_SubscriptionID,
        "siteID": target_siteID,
        "clientID": client_id
        })

        # print(payload)
        response = requests.request("POST", url, headers=headers, data=payload)
        # print(response.text)
        # print(type(response))
        discord_webhook(target_sessionDate,target_sessionName,target_instructorName,target_siteID,target_startTime, response)
        # return target_sessionDate,target_sessionName,target_instructorName,target_siteID,target_startTime, response


