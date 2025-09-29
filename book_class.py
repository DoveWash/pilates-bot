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


#  using dummy data to test booking functionaility and notifications
# classes_to_book = [
#     {
#         "sessionID": "YUkxWDY3LzJKQUxISFNPbld3UHU0Zz09",
#         "sessionName": "Hatha Yoga",
#         "sessionDate": "2025-09-18",
#         "startTime": "06:50:00",
#         "endTime": "07:40:00",
#         "duration": "50 min",
#         "sessionType": "Classes",
#         "sessionTypeID": "eXp3ZzFIU2l1a1lmNjZudFZEMFpvdz09",
#         "bookingID": "",
#         "casualRate": 0,
#         "paymentRequired": True,
#         "creditsRequired": 1,
#         "instructorData": [
#             {
#                 "instructorID": "MldzV0x1WXJHS3h5cUNjRCtCMTJydz09",
#                 "instructorName": "Bronte M",
#                 "instructorProfile": "https://link.hapana.com/image/no_image.jpg"
#             }
#         ],
#         "instructor": "Bronte M",
#         "capacity": 18,
#         "reserved": 5,
#         "remaining": 13,
#         "waitlistCapacity": 25,
#         "waitlistReserved": 0,
#         "waitlistRemaining": 25,
#         "waitlistNumber": 0,
#         "sessionStatus": "open",
#         "bookingStatus": "",
#         "ineligiblityMessage": "",
#         "checkinStatus": False,
#         "roomID": "UncyOTBCV29tVHRCZmJxdzJlUUw3dz09",
#         "roomName": "Studio 1",
#         "address": "18 Benjamin Way, Belconnen ACT, Australia",
#         "spotID": 0,
#         "spotName": "",
#         "recurringSeries": True,
#         "zoomJoinURL": "",
#         "mediaType": "zoom",
#         "sessionLocationType": "physical",
#         "timezone": "Australia/Sydney",
#         "paymentPending": False,
#         "sessionImage": "",
#         "resourceBooking": False,
#         "sessionTemplateID": "ZU1tdjdzYWpRK2VyZ1lxT3lvWHBGUT09",
#         "sessionTemplate": "Hatha Yoga"
#     },
#     {
#         "sessionID": "RFpDdnpUQ2ZVbm0yQ3dwcExhMzFLQT09",
#         "sessionName": "Reformer",
#         "sessionDate": "2025-09-18",
#         "startTime": "05:15:00",
#         "endTime": "05:55:00",
#         "duration": "40 min",
#         "sessionType": "Classes",
#         "sessionTypeID": "SGRnZDBaOWkrVDZWbFZNWGk0Q3QvUT09",
#         "bookingID": "",
#         "casualRate": 0,
#         "paymentRequired": True,
#         "creditsRequired": 1,
#         "instructorData": [
#             {
#                 "instructorID": "eGRlMUhoWmlzM0RYU0JHL1JiMjVndz09",
#                 "instructorName": "Erin O D",
#                 "instructorProfile": "https://link.hapana.com/image/no_image.jpg"
#             }
#         ],
#         "instructor": "Erin O D",
#         "capacity": 21,
#         "reserved": 10,
#         "remaining": 11,
#         "waitlistCapacity": 25,
#         "waitlistReserved": 0,
#         "waitlistRemaining": 25,
#         "waitlistNumber": 0,
#         "sessionStatus": "open",
#         "bookingStatus": "",
#         "ineligiblityMessage": "",
#         "checkinStatus": False,
#         "roomID": "UHBkSy9FUFdISlpvcExsNFFGMDd0Zz09",
#         "roomName": "Reformer Studio",
#         "address": "43 Alexandrina Dr, Yarralumla ACT 2600",
#         "spotID": 0,
#         "spotName": "",
#         "recurringSeries": True,
#         "zoomJoinURL": "",
#         "mediaType": "zoom",
#         "sessionLocationType": "physical",
#         "timezone": "Australia/Sydney",
#         "paymentPending": False,
#         "sessionImage": "",
#         "resourceBooking": False,
#         "sessionTemplateID": "ZExGNVZ2aGt3UEt1eE9Vb2MyY1Vjdz09",
#         "sessionTemplate": "Reformer"
#     }
# ]


# client_id = "YnZjOWNBU3Q5d1l2VWRTT3V4YVJnQT09"
# authorization_header = "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJpc3MiOiIzMDEwMDU4IiwiYXVkIjoiMjgzIiwiaWF0IjoxNzU3OTE2MDI1LCJleHAiOjE3NjA1MDgwMjV9.xSgcvurmzJdUis6xmcJ2aCltbBCN8lIFt7awiPK0g6I"
# securitytoken = "194d512d6478f1f3d06fefa8b1203728||ca0fc5a79dde648c08896fdc7d5c26505a60c0f8ff7a0d0523d1f2bff4e0e932f798adf81924eed9f3335a356b0542f2c1d0f1711ca0e6a349cae2ab4aee7fda00121fec09e5bf40a3c057744f588649edf4d690b557e1e9aa1cabc59efb36390b7b085404bd18798686ef14dfdfb1a9d291090bb1f6c0e326158d9ceed80c66d9b64209ca8b473bf488f23d70e2c465b8523df41fdaacb1b87cae9207f62ffe1e4f21a28997e83fb8908ad1ddc2cb761003229a12ece51a686137112b62b4690f4c9bde2d978e6932fbac6b01f1aa5b21eb468597bce4f085fa2e5fa8e1f4"

# book_class(client_id,authorization_header,securitytoken,classes_to_book)