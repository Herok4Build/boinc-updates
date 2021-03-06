#!/usr/bin/env python3

"""
BASICS

Provides the framework to assign tokens based on a 2 factor authorization
"""

import redis
import random
import json
import os
from flask import Flask, request
from werkzeug.datastructures import ImmutableMultiDict
import preprocessing as pp


r_alloc = redis.Redis(host = '0.0.0.0', port = 6389, db =2)
r_org = redis.Redis(host = '0.0.0.0', port = 6389, db=3)
r_temp = redis.Redis(host = '0.0.0.0', port = 6389, db = 4)
app = Flask(__name__)


# Verifies if 2-factr token APIs are active
@app.route("/boincserver/v2/api/2factor_active")
def factor2_operational():
    return '2-Factor token generation is active'


# Verifies if an user's organization is allowed
@app.route("/boincserver/v2/api/verify_org/<orgtok>")
def verify_org(orgtok):
    all_org_keys = [r_org.hmget(x.decode('UTF-8'), 'Organization Token')[0].decode('UTF-8') for x in r_org.keys()]
    if orgtok in all_org_keys:
       return 'Organization key is valid'

    return 'Organization token is invalid, access denied'



# Authentication to request a token for an user
@app.route("/boincserver/v2/api/request_user_token", methods = ['GET', 'POST'])
def request_user_token():


    if request.method != 'POST':
       return 'INVALID, no data provided'

    all_orgs = [x.decode('UTF-8') for x in r_org.keys()]
    all_org_emails = [r_org.hmget(x, 'Allowed Email')[0].decode('UTF-8') for x in all_orgs]        

    dictdata = ImmutableMultiDict(request.form).to_dict(flat='')
    NAME = str(dictdata['name'][0])
    LAST_NAME = str(dictdata['last_name'][0])
    EMAIL = str(dictdata['email'][0])
    EMLAST = '@'+EMAIL.split('@')[-1]

    ALLOCATION = str(dictdata['allocation'][0])
    if '' in [NAME, LAST_NAME, EMAIL, ALLOCATION]:
       return 'Invalid request, not all information has been provided'

    # Obtains the actual emails
    # Finds if the user's email is valid
    invalid_email = True

    for hh in range(0, len(all_orgs)):
        if EMLAST in all_org_emails[hh].split(';'):
           user_org = all_orgs[hh]
           invalid_email = False
           break

    if invalid_email:
        return "INVALID, user email does not correspond to an allowed organization"


    # Creates a temporary token, sent to the user's email
    SEQ = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890'
    temptok = ''

    for qq in range(0, 24):
        temptok += random.choice(SEQ)

    Text = "Your token request has been approved.\nClick on the following link to activate your account as a BOINC researcher: \n\n"
    Text += os.environ["SERVER_IP"]+":5054/boincserver/v2/api/authenticated_request_token/"+temptok
    Text += "\nThe link will be active for a period of 24 hours and will be deactivated upon clicking on it.\n\n"
    Text += "Note: This is an automated message, all messages sent to this address will be ignored."
    r_temp.setex(temptok, ';'.join([user_org, NAME, LAST_NAME, EMAIL, ALLOCATION]), 24*3600) # Redis documentation is faulty, time goes at the end
    pp.send_mail(EMAIL, 'Researcher account requested', Text)
    return 'An automatic email has been sent to the provided email address, this link will remain valid for 24 hours.'


# Checks the company's maximum allocation
@app.route("/boincserver/v2/api/check_company_allocation/<orgtok>")
def check_company_allocation(orgtok):

    all_org_keys = [r_org.hmget(x.decode('UTF-8'), 'Organization Token')[0].decode('UTF-8') for x in r_org.keys()]
    if orgtok not in all_org_keys:
       return 'Organization key invalid, access denied'

    all_orgs = [x.decode('UTF-8') for x in r_org.keys()]
    for otok, org in zip(all_org_keys, all_orgs):
        if otok == orgtok:
           user_org = org

    return r_org.hget(user_org, 'Data Plan').decode('UTF-8')


# Verifies the user's token and creates a new user for BOINC
# Establishes user's allocation
# Assigns the user to an organization
# Deletes the temporary tokens
@app.route("/boincserver/v2/api/authenticated_request_token/<temptok>")
def authenticated_request_token(temptok):


    if r_temp.get(temptok) is None:
       return 'INVALID, temporary token not valid, already used, or expired, access denied'

    DATA_PROV = r_temp.get(temptok).decode('UTF-8').split(';')
    user_org = DATA_PROV[0]
    NAME = DATA_PROV[1]
    LAST_NAME = DATA_PROV[2]
    EMAIL = DATA_PROV[3]
    ALLOCATION = DATA_PROV[4]
    
    maxalloc = r_org.hget(user_org, 'Data Plan').decode('UTF-8')
    if float(ALLOCATION) > float(maxalloc):
       r_temp.delete(temptok)
       return 'INVALID, User requested allocation is larger than organization allows'

    # Creates a final token for the user
    SEQ = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890'
    user_tok = ''
    for qq in range(0, 14):
        user_tok += random.choice(SEQ)
    
    # Replaces single quotes by double quotes
    Org_Users_Data = json.loads(r_org.hget(user_org, 'Users').decode('UTF-8').replace('\"', 'UHJKNM').replace('\'', '\"').replace('UHJKLM', '\''))
    Org_Users_Data[user_tok]={'name':NAME, 'last name':LAST_NAME, 'email':EMAIL, 'allocation':ALLOCATION}
    r_org.hset(user_org, 'Users', Org_Users_Data)
    r_temp.delete(temptok)
    r_org.hincrby(user_org, 'No. Users', 1)

    Text = "Your researcher account has been approved.\nUser token: "+user_tok+"\n\nSincerely,\nthe TACC BOINC development team"

    pp.send_mail(EMAIL, 'Approved researcher account', Text)

    # Adds the token to the token file
    with open("/root/project/html/user/token_data/Tokens.txt", "a") as tokfile:
         tokfile.write(NAME+" "+LAST_NAME+", "+user_tok+", "+EMAIL+'\n')

    # Also creates a Reef directory
    os.makedirs('/root/project/api/sandbox_files/DIR_'+str(user_tok))
    os.makedirs('/root/project/api/sandbox_files/DIR_'+str(user_tok)+'/___RESULTS')

    # Adds the allocation details
    r_alloc.set(user_tok, ALLOCATION)

    return 'Your request has been approved, you may now submit BOINC jobs. Check your email for a token if you need terminal access.'



# Returns the user's token after providing an email
@app.route("/boincserver/v2/api/token_from_email", methods = ['GET', 'POST'])
def token_from_email():

    if request.method != "POST":
        return "INVALID, no data provided"

    EMAIL = request.form["email"]

    # Searches the redis database for the token
    all_orgs = [x.decode('UTF-8') for x in r_org.keys()]
    Org_Users_Data = [json.loads(r_org.hget(x, 'Users').decode('UTF-8').replace('\"', 'UHJKNM').replace('\'', '\"').replace('UHJKLM', '\'')) for x in all_orgs]

    # Searches all of them until it finds the user
    for anorg in Org_Users_Data:

        for user_token in anorg.keys():
            if EMAIL == anorg[user_token]['email']:
                return user_token

    return 'INVALID, user is not registered as a researcher'



if __name__ == '__main__':
   app.run(host = '0.0.0.0', port = 5054)
