import requests
import json

def discord_webhook(target_sessionDate,target_sessionName,target_instructorName,target_siteID,target_startTime, response):

  # if response == []:
  #   exit
  data = response.json()
  error_message = (
      data.get("message")
      or data.get("data", {}).get("message")
      or "No message provided"
  )
  url = "https://discord.com/api/webhooks/1417019179772674119/cbkEFRS1FrfDCiqOIJnJcGmJvAbgqEsf74UJC6w6nt_T7fwKhNoM7OjNhI2BlJKZZ_HP"
  
  headers = {
  'Content-Type': 'application/json',
  }

  location = ""
  if target_siteID == "amhmQmdIdldDL3lRdHFpZ3BuOW40Zz09":
    location = "Yarralumla"
  else:
    location = "Belconnen"

  # if booking is sucessful then
  if data.get("success"):
    payload = json.dumps({
      "content": "🤖 Pilates bot was able to book the following classes for you:",
      "embeds": [
          {
              "title": target_sessionName,
              "color": 5763719,  # nice teal (use any decimal RGB)
              "fields": [
                  {"name": "📅 Date", "value": target_sessionDate, "inline": True},
                  {"name": "🕒 Start Time", "value": target_startTime, "inline": True},
                  {"name": "👩 Instructor", "value": target_instructorName, "inline": True},
                  {"name": "📍 Site", "value": location, "inline": False},
              ],
              "footer": {
                  "text": "Pilates Bot Booking Confirmation"
              }
          }
      ]
    })
  else:
    payload = json.dumps({
        "content": "⚠️ **Pilates Bot** ran into a problem while booking your class!",
        "embeds": [
            {
                "title": f"❌ Booking Error — {target_sessionName}",
                "color": 15158332,  # red
                "fields": [
                    {
                        "name": "📄 Reason",
                        "value": (error_message or "No reason provided")[:1024],
                        "inline": False
                    },
                    {"name": "📅 Date",       "value": str(target_sessionDate),    "inline": True},
                    {"name": "🕒 Start Time", "value": str(target_startTime),      "inline": True},
                    {"name": "👩 Instructor", "value": str(target_instructorName), "inline": True},
                    {"name": "📍 Site",       "value": str(location),         "inline": False},
                ],
                "footer": { "text": "🔧 Pilates Bot Error Handler" }
            }
        ]
    })
  response = requests.request("POST", url, headers=headers, data=payload)
#   print(response.text)

  # target_sessionDate = "15/09/2025"
  # target_sessionName = "Reformer Pilates"
  # target_instructorName = "Martina M"
  # target_siteID = "Belconnen"
  # target_startTime = "19:20:00"

  # discord_webhook(target_sessionDate,target_sessionName,target_instructorName,target_siteID,target_startTime)

def discord_webhook_noclasses():
  url = "https://discord.com/api/webhooks/1417019179772674119/cbkEFRS1FrfDCiqOIJnJcGmJvAbgqEsf74UJC6w6nt_T7fwKhNoM7OjNhI2BlJKZZ_HP"

  headers = {
  'Content-Type': 'application/json',
  }

  payload = json.dumps({
      "content": "🤖 There are no classes for Pilates Bot to book:",
      "embeds": [
          {
              "title": "No available classes",
              "color": 3447003,  # nice blue
              "fields": [
                  {"name": "📅 Bookings", "value": "Nothing to book", "inline": True}
              ]
          }
      ]
  })
  response = requests.request("POST", url, headers=headers, data=payload)