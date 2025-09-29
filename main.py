import requests
import json
from securitytoken import get_security_token
from login_probe import login_request
from get_class_list import get_class_list
from book_class import book_class
from webhook import discord_webhook
from webhook import discord_webhook_noclasses

def main_function():
    # get security token
    securitytoken = get_security_token()

    # login to groundup, save login variables
    access_token, client_id, cookie_formatted = login_request(securitytoken)

    # logging to console
    # print(f"security token is: {securitytoken}")
    # print(f"access token is: {access_token}")
    # print(f"cliend ID is: {client_id}")
    # print(f"cookie formatted is: {cookie_formatted}")

    # get list of classes to book, save the list in classes_to_book variable
    classes_to_book, authorization_header = get_class_list(access_token, client_id, securitytoken, cookie_formatted)

    # print(f"The classes to book are: ")
    # This if statement is required because if classes_to_book is an empty array, it causes nothing to be returned for book_class and discord_webhook.
    # This checks to see if there is any data in classes_to_book, if not -> it will run discord_webhook_noclasses (generic discord message saying no classes to book)
    if len(classes_to_book) > 0:
        # logging to console
        # print(f"Authorisation header is: {authorization_header}")
        # # logging to console
        # print("--- adding a new line to break up console ----")
        # book classes from classes_to_book

        # old way below
        # target_sessionDate,target_sessionName,target_instructorName,target_siteID,target_startTime, response = book_class(client_id,authorization_header,securitytoken,classes_to_book)
        book_class(client_id,authorization_header,securitytoken,classes_to_book)
        # discord_webhook(target_sessionDate,target_sessionName,target_instructorName,target_siteID,target_startTime, response)
    else:
        discord_webhook_noclasses()

# main_function()

#if things go wrong here is how to fix:

# # Flip the "enabled" flag back on


# enables the app again

# RG=pilates-bot
# APP=pilates-bot

# az functionapp show -g $RG -n $APP --query "{name:name,state:state,enabled:enabled,defaultHostName:defaultHostName}" -o table
# # Flip the "enabled" flag back on
# az functionapp update -g $RG -n $APP --set enabled=true






