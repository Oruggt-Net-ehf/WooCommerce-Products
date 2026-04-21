'''
Script that analyzes product description in WooCommerce 
and turns a spec list into attributes

Author Siggi Bjarnason 21 April 2026
Copyright 2026 Siggi Bjarnason

Following packages need to be installed
pip install requests
pip install sentry_sdk
pip install argparse
pip install onepassword
pip install asyncio

'''
# Import libraries
import os
import time
import sys
import requests
import sentry_sdk
import argparse
import configparser
from onepassword import Client, DesktopAuth
import asyncio

if sys.version_info[0] > 2:
    import urllib.parse as urlparse
    # The following line surpresses a warning that we aren't validating the HTTPS certificate
    requests.urllib3.disable_warnings()
else:
   print("This script is only supported on python 3")
   sys.exit(9)

# End imports

# Few globals
tLastCall = 0
iTotalSleep = 0
iLogLevel = 4  # How much logging should be done. Level 10 is debug level, 0 is none
iTimeOut = 180  # Max time in seconds to wait for network response
iMinQuiet = 2  # Minimum time in seconds between API calls
strSentryURL = "https://prxVN17LbuNbxB4Tg2vK8g4x@s2386117.eu-fsn-3.betterstackdata.com/2386117"

sentry_sdk.init(
    dsn=strSentryURL,
    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for performance monitoring.
    traces_sample_rate=1.0,
)

# sub defs

async def get1PasswordItems(dictItemCollection, strAccountName=None, strToken=None):
    """
    Handles fetching items from 1Password based on the provided collection of item specifications. 
    It supports both token-based authentication and desktop app authentication.
    Parameters:
    dictItemCollection: A dictionary of dictionaries, containing the specifications for the items to fetch.
                        Inner dictionaries should have the keys "vault_id" and "item_id" to specify 
                        the vault and item to fetch. The outer dictionary's keys are used as identifiers 
                        for the fetched items in the returned dictionary.
    strAccountName: The name of the 1Password account to use for authentication 
                    in case of desktop app authentication.
    strToken: The token to use for token-based authentication.
    Returns: A dictionary of dictionaries containing the fetched items or an error message in case of failure. 
                The structure of the returned dictionary is as follows:
                {
                    "item_identifier": {
                        "urls": [list of URLs associated with the item],
                        "tags": [list of tags associated with the item],
                        "notes": notes associated with the item,
                        "totp": TOTP field value if present,
                        "field_title_1": field_value_1,
                        "field_title_2": field_value_2,
                        ...
                    },
                    ...
    """
        
    strScriptName = os.path.basename(sys.argv[0])
    strVersion = "{0}.{1}.{2}".format(sys.version_info[0],sys.version_info[1],sys.version_info[2])
    try:
        if strToken is not None:
            LogEntry("Using token-based authentication. Make sure the token is valid and has the necessary permissions.",2)
            objClient = await Client.authenticate(auth=strToken,
            integration_name=strScriptName,
            integration_version=strVersion,)
        else:
            # Connects to the 1Password desktop app.
            LogEntry("No token provided. Using DesktopAuth for authentication. "
                     "Make sure the 1Password desktop app is running and you are signed in.",2)
            if strAccountName is None:
                return {"fatal error": 
                        {"error message":"neither token nor 1Password account name provided. Unable to authenticate."}}
            objClient = await Client.authenticate(
                auth=DesktopAuth(account_name=strAccountName),
            integration_name=strScriptName,
            integration_version=strVersion,)
    except Exception as e:
        return {"fatal error": {"error message": f"Authentication failed. {e}"}}

    LogEntry("Connected to 1Password",1)

    dictCollection = {}
    for key, item_spec in dictItemCollection.items():
        strVaultID = item_spec["vault_id"]
        strItemID = item_spec["item_id"]
        try:
            objItem = await objClient.items.get(strVaultID, strItemID)
        except Exception as e:
            return {"fatal error": {"error message": f"Failed to retrieve item {strItemID} from vault {strVaultID}. {e}"}}
        dictItem = {}

        if hasattr(objItem, 'websites') and objItem.websites:
            dictItem["urls"] = [website.url for website in objItem.websites]
        dictItem["tags"] = objItem.tags
        dictItem["notes"] = objItem.notes
        for objItemField in objItem.fields:
            if objItemField.field_type == "Totp":
                dictItem["totp"] = objItemField
            else:
                dictItem[objItemField.title] = objItemField.value
        dictCollection[key] = dictItem

    return dictCollection

def CleanExit(strCause,bLog=True):
  """
  Handles cleaning things up before unexpected exit in case of an error.
  Things such as closing down open file handles, open database connections, etc.
  Logs any cause given, closes everything down then terminates the script.
  Parameters:
    Cause: simple string indicating cause of the termination, can be blank
    bLog: Optional, defaults to true. Boolean indicating if the cause should be logged before exiting.
  Returns:
    nothing as it terminates the script
  """
  if bLog:
    LogEntry("{} is exiting abnormally on {}: {}".format(
        strScriptName, strScriptHost, strCause), 0)

  objLogOut.close()
  print("objLogOut closed")

  sentry_sdk.capture_exception(Exception(strCause))
  sys.exit(9)

def LogEntry(strMsg, iMsgLevel=0, bAbort=False):
  """
  This handles writing all event logs into the appropriate log facilities
  This could be a simple text log file, a database connection, etc.
  Needs to be customized as needed
  Parameters:
    strMsg: Simple string with the event to be logged
    iMsgLevel: How detailed is this message, debug level or general. Will be matched against Loglevel
    Abort: Optional, defaults to false. A boolean to indicate if CleanExit should be called.
  Returns:
    Nothing
  """
  strTimeStamp = time.strftime("%m-%d-%Y %H:%M:%S")
  #print("Loggin {}. Log level of this message is {}, current log level is {}".format(strMsg, iMsgLevel, iVerbose))

  if iVerbose > iMsgLevel:
    objLogOut.write("{0} : {1}\n".format(strTimeStamp, strMsg))
    if not bQuiet:
      print(strMsg)
  else:
    if bAbort:
      objLogOut.write("{0} : {1}\n".format(strTimeStamp, strMsg))

  if bAbort:
    CleanExit(strMsg,bLog=False)

def isInt(CheckValue):
    """
    function to safely check if a value can be interpreded as an int
    Parameter:
      Value: A object to be evaluated
    Returns:
      Boolean indicating if the object is an integer or not.
    """
    if isinstance(CheckValue, (float, int, str)):
        try:
            fTemp = int(CheckValue)
        except ValueError:
            fTemp = "NULL"
    else:
        fTemp = "NULL"
    return fTemp != "NULL"

def FetchEnv(strVarName):
  """
  Function that fetches the specified content of specified environment variable,
  converting nonetype to empty string.
  Parameters:
    strVarName: The name of the environment variable to be fetched
  Returns:
    The content of the environment or empty string
  """

  if os.getenv(strVarName) != "" and os.getenv(strVarName) is not None:
    return os.getenv(strVarName)
  else:
    return ""

def MakeAPICall(strURL, dictHeader, strMethod, dictPayload="", objFiles=[], objData=None, strUser="", strPWD=""):
  """
  Handles the actual communication with the API, has a backoff mechanism
  MinQuiet defines how many seconds must elapse between each API call.
  Sets a global variable iStatusCode, with the HTTP code returned by the API (200, 404, etc)
  Parameters:
    strURL: Simple String. API EndPoint to call
    dictHeader: Dictionary object with the header to pass along with the call
    strMethod: Simple string. Call method such as GET, PUT, POST, etc
    dictPayload: Optional. Any payload to send along in the appropriate structure and format
    objFiles: Optional. List of files (full absolute paths) or multipart object to be uploaded, if any
    User: Optional. Simple string. Username to use in basic Auth
    Password: Simple string. Password to use in basic auth
  Return:
    Returns a tupple of single element dictionary with key of Success,
    plus a list with either error messages or list with either error messages
    or result of the query, list of dictionaries..
    ({"Success":True/False}, [dictReturn])
  """
  global tLastCall
  global iTotalSleep
  global iStatusCode
  global strScriptHost

  fTemp = time.time()
  fDelta = fTemp - tLastCall
  LogEntry("It's been {} seconds since last API call".format(fDelta), 4)
  if fDelta > iMinQuiet:
    tLastCall = time.time()
  else:
    iDelta = int(fDelta)
    iAddWait = iMinQuiet - iDelta
    LogEntry("It has been less than {} seconds since last API call, "
              "waiting {} seconds".format(iMinQuiet, iAddWait), 4)
    iTotalSleep += iAddWait
    time.sleep(iAddWait)

  strErrCode = ""
  strErrText = ""
  dictReturn = {}

  LogEntry("Doing a {} to URL: {}".format(strMethod, strURL), 1)
  try:
    if strMethod.lower() == "head":
      WebRequest = requests.request("HEAD", strURL, timeout=iTimeOut, verify=False, proxies=dictProxies, 
                                    headers=dictHeader)
    if strMethod.lower() == "put":
      WebRequest = requests.request("PUT", strURL, timeout=iTimeOut, verify=False, proxies=dictProxies, 
                                    headers=dictHeader, data=objData)

    if strMethod.lower() == "get":
      if strUser != "":
        LogEntry(
            "I have none blank credentials so I'm doing basic auth", 3)
        WebRequest = requests.get(strURL, timeout=iTimeOut, headers=dictHeader,
                                  auth=(strUser, strPWD), verify=False, proxies=dictProxies)
      else:
        LogEntry("credentials are blank, proceeding without auth", 3)
        WebRequest = requests.get(
            strURL, timeout=iTimeOut, headers=dictHeader, verify=False, proxies=dictProxies)
      LogEntry("get executed", 4)
    if strMethod.lower() == "post":
      if dictPayload:
        dictTmp = dictPayload.copy()
        if "password" in dictTmp:
            dictTmp["password"] = dictTmp["password"][:2]+"*********"
        if "clientSecret" in dictTmp:
            dictTmp["clientSecret"] = dictTmp["clientSecret"][:2]+"*********"
        if strUser != "":
          LogEntry("I have none blank credentials so I'm doing basic auth", 3)
          LogEntry("with user auth, payload of: {} and files object of {}".format(dictTmp,objFiles), 4)
          WebRequest = requests.post(strURL, json=dictPayload, timeout=iTimeOut,
                                      headers=dictHeader, auth=(strUser, strPWD),
                                      verify=False, proxies=dictProxies,files=objFiles)
        else:
          LogEntry("credentials are blank, proceeding without auth", 3)
          LogEntry("with payload of: {} and files object of {}".format(dictTmp,objFiles), 4)
          WebRequest = requests.post(
              strURL, json=dictPayload, timeout=iTimeOut, headers=dictHeader,
              files=objFiles, verify=False, proxies=dictProxies)
      else:
        LogEntry("No payload, doing a simple post", 3)
        LogEntry("with files object of: {}".format(objFiles), 4)
        WebRequest = requests.post(
            strURL, headers=dictHeader, verify=False, proxies=dictProxies, files=objFiles)
      LogEntry("post executed", 4)
    if strMethod.lower() == "delete":
      WebRequest = requests.delete(strURL, headers=dictHeader, verify=False, proxies=dictProxies)

  except Exception as err:
    dictReturn["url"] = strURL
    dictReturn["condition"] = "Issue with API call"
    dictReturn["errormsg"] = err
    sentry_sdk.capture_exception(err)
    return ({"Success": False}, [dictReturn])

  if isinstance(WebRequest, requests.models.Response) == False:
    LogEntry("response is unknown type", 1)
    strErrCode = "ResponseErr"
    strErrText = "response is unknown type"

  LogEntry("call resulted in status code {}".format(
    WebRequest.status_code), 3)
  iStatusCode = int(WebRequest.status_code)

  if not 200 <= iStatusCode <= 299:
    LogEntry("call resulted in status code {}".format(WebRequest.status_code),3)
    strErrCode += str(iStatusCode)
    strErrText += WebRequest.text
    LogEntry("HTTP Error: {}".format(iStatusCode), 3)
    LogEntry("Response: {}".format(WebRequest.content), 4)
  if strErrCode != "":
    dictReturn["url"] = strURL
    dictReturn["condition"] = "problem with your request"
    dictReturn["errcode"] = strErrCode
    dictReturn["errormsg"] = strErrText
    return ({"Success": False}, [dictReturn])
  else:
    if "<html>" in WebRequest.text[:99] or WebRequest.text == "":
      if WebRequest.text == "":
        return ({"Success": True},"")
      else:
        return ({"Success": False}, WebRequest.text[:99])
    try:
      return ({"Success": True}, WebRequest.json())
    except Exception as err:
      dictReturn["condition"] = "failure converting response to jason"
      dictReturn["errormsg"] = err
      dictReturn["errorDetail"] = "Here are the first 199 character of the response: {}".format(
          WebRequest.text[:199])
      sentry_sdk.capture_exception(err)
      return ({"Success": False}, [dictReturn])

def main():
  global strToken
  global bQuiet
  global objLogOut
  global iVerbose
  global dictProxies
  global strScriptName
  global strScriptHost

  iLoc = sys.argv[0].rfind(".")
  strDefConf = sys.argv[0][:iLoc] + ".ini"
  objParser = argparse.ArgumentParser(description="WooCommerce Product description parser and attrib creator")
  objParser.add_argument("--silent", dest="silent",
                      action="store_true", help="only output to file, not to screen")
  objParser.add_argument("-c", "--config",type=str, help="Path to the configuration file", default=strDefConf)
  objParser.add_argument("-v", "--verbosity", action="count", default=1, help="Verbose output, vv level 2 vvvv level 4")
  objParser.add_argument("-x", "--proxy", type=str, help="Proxy to use for API calls")

  objArgs = objParser.parse_args()
  iVerbose = objArgs.verbosity
  strConfile = objArgs.config
  if os.path.isfile(strConfile):
    LogEntry ("Configuration File {} exists".formatstrConfile)
  else:
    LogEntry ("Can't find configuration file {}, defaulting to {}".format(strConfile,strDefConf))
    strConfile = strDefConf

  objConfig = configparser.ConfigParser()
  objConfig.read(strConfile)
  if "Generic" in objConfig:
    if "AccountName" in objConfig["Generic"]:
      strAccountName = objConfig["Generic"]["AccountName"]
    else:
       LogEntry("Account name not found in config")
  else:
     LogEntry("section Generic not found in config")
  if "WPCreds" in objConfig:
    if "VaultID" in objConfig["WPCreds"]:
      strVaultID = objConfig["WPCreds"]["VaultID"]
    else:
       LogEntry("VaultID not found in config")
    if "ItemID" in objConfig["WPCreds"]:
      strItemID = objConfig["WPCreds"]["ItemID"]
    else:
       LogEntry("ItemID not found in config")
  else:
     LogEntry("section WPCreds not found in config")
  
  ISO = time.strftime("-%Y-%m-%d")
  strVersion = "{0}.{1}.{2}".format(sys.version_info[0], sys.version_info[1], sys.version_info[2])
  strRealPath = os.path.realpath(sys.argv[0])
  strBaseDir = os.path.dirname(sys.argv[0])
  if strBaseDir == "":
    iLoc = strRealPath.rfind("/")
    strBaseDir = strRealPath[:iLoc]
  if strBaseDir[-1:] != "/":
    strBaseDir += "/"

  strOutDir  = strBaseDir + "Out/"
  if strOutDir[-1:] != "/":
    strOutDir += "/"

  iLoc = sys.argv[0].rfind(".")

  if not os.path.exists (strOutDir) :
    os.makedirs(strOutDir)
    print("\nPath '{0}' for output files didn't exists, so I create it!\n".format(strOutDir))

  strLogDir  = strBaseDir + "Logs/"
  if strLogDir[-1:] != "/":
    strLogDir += "/"

  iLoc = sys.argv[0].rfind(".")

  if not os.path.exists (strLogDir) :
    os.makedirs(strLogDir)
    print("\nPath '{0}' for log files didn't exists, so I create it!\n".format(strLogDir))

  strScriptName = os.path.basename(sys.argv[0])
  iLoc = strScriptName.rfind(".")

  strLogFile = strLogDir + strScriptName[:iLoc] + ISO + ".log"
  objLogOut = open(strLogFile, "a", 1)
  strScriptHost = sys.platform.node().upper()
  bQuiet = objArgs.silent

  LogEntry("This is a script to parse WooCommerce product description for specifications "
           "and create product attributes from it."
          "This is running under Python Version {}".format(strVersion))
  LogEntry("Running from: {}".format(strRealPath))
  dtNow = time.strftime("%A %d %B %Y %H:%M:%S %Z")
  LogEntry("The script started at {}".format(dtNow))

  if FetchEnv("PROXY") is not None:
    strProxy = os.getenv("PROXY")
  if objArgs.proxy is not None:
    strProxy = objArgs.proxy
  if strProxy is not None:
    dictProxies["http"] = strProxy
    dictProxies["https"] = strProxy
    LogEntry("Proxy has been configured for {}".format(strProxy))
  else:
    LogEntry("No proxy has been configured")

  strToken = FetchEnv("TOKEN")
  if not strToken:
    strToken = None
  dictItemCollection = {}
  dictItemSpecs = {}
  dictItemSpecs["vault_id"] = strVaultID
  dictItemSpecs["item_id"] = strItemID
  dictItemCollection["Creds"] = dictItemSpecs

  returned_dict = asyncio.run(get1PasswordItems(dictItemCollection, strAccountName=strAccountName, strToken=strToken))
  if returned_dict is None:
    LogEntry("Failed to retrieve item.",0,True)
  if "fatal error" in returned_dict:
    LogEntry("Fatal error: {}".format(returned_dict['fatal error']['error message']),0,True)

  # Replace these with your actual WooCommerce store details
  BaseURL = returned_dict["Creds"]["hostname"]
  consumer_key = returned_dict["Creds"]["username"]
  consumer_secret = returned_dict["Creds"]["credential"]

  if not BaseURL or not consumer_key or not consumer_secret:
      LogEntry("Please set the BASEURL, KEY, and SECRET environment variables.",0,True)

  sentry_sdk.capture_message("Hello Better Stack, this is a test message from Python!")

if __name__ == '__main__':
  main()