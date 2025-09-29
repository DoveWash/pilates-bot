import requests
import json
from datetime import date, timedelta, datetime
from login_probe import login_request
import pytz


def get_class_list(access_token, client_id, securitytoken, cookie_formatted):

   print("running get_class_list....")
   
   # make timezone AUS
   aus_timezone = pytz.timezone('Australia/Sydney')
   now_aus = datetime.now(aus_timezone)
   # get todays date / future date
   todays_date = now_aus.date()
   days_to_add = 6
   future_date = todays_date + timedelta(days=days_to_add)

   print(f"Get Class list starting from {todays_date} and ending on {future_date}")
 
   # In the URL, SiteID = "" will target which location the pilates studio will pull classes from
   location_yarra = "amhmQmdIdldDL3lRdHFpZ3BuOW40Zz09"
   location_belco = "aTE4MEpReDA1bFdkN3kvaHkyNFludz09"

   # day_of_the_week = todays_date.weekday()
   # future_day_of_the_week = future_date.weekday()
   # print(day_of_the_week)
   # print(future_day_of_the_week)

   # formatted API with todays date / future date
   get_class_API_todays_date = (f"https://widgetapi.hapana.com/v2/wAPI/site/sessions?startDate={todays_date}&endDate={future_date}&sessionCategory=classes&siteID={location_belco}&sessionType=Classes&pageIndex=1&pageSize=200&clientID={client_id}")


   # formatting the authorisation header and cookie header to be passed into API GET
   authorization_header = "Bearer " + access_token
   cookie = "PHPSESSID=" + cookie_formatted

   payload = {}
   headers = {
   'content-type': 'application/json',
   'authorization': authorization_header,
   'bypasstoken': 'false',
   'securitytoken': securitytoken,
   'Cookie': cookie
   }
   #
   # print(headers)
   response = requests.request("GET", url=get_class_API_todays_date, headers=headers, data=payload)

   json_response = response.json()

 

   # the sessionIDs for the belconnen class that emaly books on a regular basis - these have the attribute "recurringSeries": true
   belconnen_sessionIDs = ["Y2M2VWtLbkdIRWZwaVJMenVLbUJXQT09","V1ZXYkVvWTRIS3FxM0lyUTVCOUtSQT09","em95dGhTa0RiYytRWU56M2xod2gxZz09","Q2tJL2JKQS9wYVlBaktzYjVsMGFPdz09"]

   # list of all the classes to book- This is an empty array, but classes will get appended to it
   classes_to_book = []

   # search the response from the API post for belconnen sessionIds and sessionStatus. Ensures bookingstatus is an emptry string so it only returns classes that emaly has not booked yet
   for x in (json_response["data"]):
      if x["sessionID"] in belconnen_sessionIDs and x["sessionStatus"] == "open" and x["bookingStatus"] == "":
         classes_to_book.append(x)


   # 
   # 
   # POST request to yarra to get the required classes
   # 
   # 

   # formatted API with todays date / future date
   get_class_API_todays_date = (f"https://widgetapi.hapana.com/v2/wAPI/site/sessions?startDate={todays_date}&endDate={future_date}&sessionCategory=classes&siteID={location_yarra}&sessionType=Classes&pageIndex=1&pageSize=200")

   response = requests.request("GET", url=get_class_API_todays_date, headers=headers, data=payload)

   json_response = response.json()

   print("This is the get class list request to yarra: ")
   print(json.dumps(json_response))
   
   # the sessionIDs for the yarra class that emaly books on a regular basis - these have the attribute "recurringSeries": true
   yarra_sessionIDs = ["cmVCYjZQMnpKMHREUVFwbWY0RU5jQT09","cEU4ZmU4QURicUpWQmtmM2IvQ2pEZz09","ZGFLWmh6c0dDTmJ2TUZNZU56K0RzZz09"]

   # search the response from the API post for yarra sessionIds and sessionStatus and append them to classes_to_book
   for y in (json_response["data"]):
      if y["sessionID"] in yarra_sessionIDs and y["sessionStatus"] == "open" and y["bookingStatus"] == "":
         classes_to_book.append(y)


# prints the list of classes that it will book
   print(json.dumps([classes_to_book]))
   return classes_to_book, authorization_header

   # --------------------------
   # 
   # Daryls random notes
   # 
   # --------------------------
   # 
   # The sessionID for the sunday pilates classes are reused. not sure if this will change, but I should add some logic about this

   #  cEU4ZmU4QURicUpWQmtmM2IvQ2pEZz09 = sessionID for the 16:50 sunday class - yarra
   #  ZGFLWmh6c0dDTmJ2TUZNZU56K0RzZz09 = sessionID for the 17:40 sunday class - yarra

   # Monday - heated yoga at 6:20 belco - Y2M2VWtLbkdIRWZwaVJMenVLbUJXQT09
   # Monday - yin yoga 7:20 belco - V1ZXYkVvWTRIS3FxM0lyUTVCOUtSQT09
   # Wed - reformer Pilates 6:00 belco - em95dGhTa0RiYytRWU56M2xod2gxZz09
   # Wed - stretch and release 6:50 belco - Q2tJL2JKQS9wYVlBaktzYjVsMGFPdz09
   # Sat - reformer 9:30 Yarra - cmVCYjZQMnpKMHREUVFwbWY0RU5jQT09
   # Sun - reformer 4:50 Yarra - cEU4ZmU4QURicUpWQmtmM2IvQ2pEZz09
   # Sun - stretch and release 5:40 Yarra - ZGFLWmh6c0dDTmJ2TUZNZU56K0RzZz09
   # 
   # 
   # 
   # 





