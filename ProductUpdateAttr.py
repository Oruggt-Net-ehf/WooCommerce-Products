'''
Script that analyzes product description in WooCommerce
and turns a spec list into attributes

Author Siggi Bjarnason 21 April 2026
Copyright 2026 Siggi Bjarnason

Following packages need to be installed
pip install requests
pip install sentry_sdk
pip install argparse
pip install onepassword-sdk
pip install asyncio
pip install beautifulsoup4

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
import platform
from bs4 import BeautifulSoup

if sys.version_info[0] > 2:
    import urllib.parse as urlparse
    # The following line surpresses a warning that we aren't validating the HTTPS certificate
    requests.urllib3.disable_warnings()
    from bs4 import BeautifulSoup
else:
   print("This script is only supported on python 3")
   sys.exit(9)

# End imports

# Few globals
tLastCall = 0
iTotalSleep = 0
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

def ExtractTwoColumnTables(strHTML):
    """
    Extract all two-column tables from HTML and return as a dictionary.
    Parameters:
        strHTML (str): The HTML content as a string
    Returns:
        dict: Dictionary where keys are values from the first column
              and values are from the second column
    """
    objSoup = BeautifulSoup(strHTML, 'html.parser')
    dictReturn = {}

    # Find all tables
    objTables = objSoup.find_all('table')

    for objTable in objTables:
        # Get all rows
        objRows = objTable.find_all('tr')

        # Extract data from all objRows
        for objRow in objRows:
            objCells = objRow.find_all(['td'])
            if len(objCells) == 2:
                key = objCells[0].get_text(strip=True)
                value = objCells[1].get_text(strip=True)
                if value != "":  # Only add to dict if value is not empty
                  dictReturn[key] = value

    return dictReturn

def AttributeExists(listAttributeCollection, strSearchName):
    """
    Check if a string can be found in a WooCommerce attribute collection.

    Parameters:
        listAttributeCollection (list): A list of attribute dictionaries from WooCommerce
        strSearchName (str): The attribute name string to search for

    Returns:
        bool: True if the attribute name is found in the collection (case-insensitive), False otherwise
    """
    if not listAttributeCollection:
        return False

    strSearchLower = strSearchName.strip().lower()

    for dictAttribute in listAttributeCollection:
        if isinstance(dictAttribute, dict) and "name" in dictAttribute:
            if dictAttribute["name"].strip().lower() == strSearchLower:
                return True

    return False

def CreateGlobalAttribute(strAttributeName, strBaseURL, strWCKey, strWCSecret):
    """
    Create a new global attribute in WooCommerce and return its ID.

    Parameters:
        strAttributeName (str): The name of the attribute to create
        strBaseURL (str): The base URL of the WooCommerce site
        strWCKey (str): WooCommerce API consumer key
        strWCSecret (str): WooCommerce API consumer secret

    Returns:
        int: The ID of the newly created attribute, or None if creation failed
    """
    dictHeader = {}
    strMethod = "post"
    strEndPoint = "/wp-json/wc/v3/products/attributes"
    strURL = strBaseURL + strEndPoint

    # Create the payload with the attribute name
    dictPayload = {
        "name": strAttributeName.strip()[:28]
    }

    LogEntry("Creating new attribute: {}".format(strAttributeName), 2)

    # Make the API call
    dictResponse = MakeAPICall(strURL, dictHeader, strMethod, dictPayload, strUser=strWCKey, strPWD=strWCSecret)

    # Check if the call was successful
    if dictResponse[0]["Success"] == False:
        LogEntry("Failed to create attribute '{}'. Error: {}".format(strAttributeName, dictResponse[1]), 0, False)
        return None

    # Extract the ID from the response
    if dictResponse[1] and isinstance(dictResponse[1], dict) and "id" in dictResponse[1]:
        iNewAttributeID = dictResponse[1]["id"]
        LogEntry("Successfully created attribute '{}' with ID: {}".format(strAttributeName, iNewAttributeID), 2)
        return iNewAttributeID
    else:
        LogEntry("Attribute created but could not extract ID from response", 0, False)
        return None

def UpdateWooCommerceProduct(dictProduct, iProductID, strBaseURL, strWCKey, strWCSecret):
    """
    Update a specific product in WooCommerce with the provided product data.

    Parameters:
        dictProduct (dict): The product data/collection to send (keys can include name, description, attributes, etc.)
        iProductID (int): The WooCommerce product ID to update
        strBaseURL (str): The base URL of the WooCommerce site
        strWCKey (str): WooCommerce API consumer key
        strWCSecret (str): WooCommerce API consumer secret

    Returns:
        tuple: ({"Success": True/False}, response_data)
               Where response_data is the updated product object on success, or error details on failure
    """
    dictHeader = {}
    strMethod = "post"
    strEndPoint = "/wp-json/wc/v3/products/{}".format(iProductID)
    strURL = strBaseURL + strEndPoint

    LogEntry("Updating WooCommerce product ID: {}".format(iProductID), 2)

    # Make the API call
    dictResponse = MakeAPICall(strURL, dictHeader, strMethod, dictProduct, strUser=strWCKey, strPWD=strWCSecret)

    # Check if the call was successful
    if dictResponse[0]["Success"] == False:
        LogEntry("Failed to update product ID {}. Error: {}".format(iProductID, dictResponse[1]), 0, False)
        return dictResponse

    LogEntry("Successfully updated product ID: {}".format(iProductID), 2)
    return dictResponse

def GetEnvCreds(dictCollectionIn):
    """
    Fetches WooCommerce API credentials from environment variables.
    dictCollection: Dictionary of dictionaries with name of env variables to fetch
    Returns:  Diction of dictionary of the values retrieved from the environment variables, with the same keys as the input dictionary
    """

    dictCollection = {}
    dictItems = {}
    for strCredName, dictItem in dictCollectionIn.items():
        for strKey, strEnvName in dictItem.items():
          dictItems[strEnvName] = FetchEnv(strEnvName)
        dictCollection[strCredName] = dictItems

    return dictCollection

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
  if objFileOut is not None:
    objFileOut.close()
    LogEntry("objFileOut closed", 1)
  objLogOut.close()
  print("objLogOut closed")

  #sentry_sdk.capture_exception(Exception(strCause))
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
  objLogOut.flush()

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

def GetSpecificationsFollower(strHTML):
    """
    Parses an HTML string, finds the heading "Technical Specification" or "Specifications"
    and determines what element immediately follows it, ignoring any div elements.
    Parameters:
      strHTML: A string containing HTML content
    Returns:
      A string indicating what follows the specifications heading:
        - "table" if a <table> element follows
        - "ul" if an unordered list <ul> element follows
        - "neither" if neither a table nor ul follows
    """
    try:
        soup = BeautifulSoup(strHTML, 'html.parser')

        # Find all heading tags (h1-h6)
        for heading_tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
            # Check if the heading text is "specifications" or "technical specification" (case-insensitive)
            heading_text = heading_tag.get_text().strip().lower()
            if heading_text == 'specifications' or heading_text == 'technical specification':
                # Get the next sibling element that is a tag (skip text nodes and div elements)
                next_element = heading_tag.find_next_sibling()

                # Skip any text nodes, whitespace, and div elements
                while next_element:
                    if isinstance(next_element, str):
                        # Skip text nodes
                        if not next_element.strip():
                            next_element = next_element.find_next_sibling() if hasattr(next_element, 'find_next_sibling') else None
                        else:
                            break
                    elif hasattr(next_element, 'name') and next_element.name and next_element.name.lower() == 'div':
                        # Skip div elements
                        next_element = next_element.find_next_sibling()
                    else:
                        break

                if next_element is None:
                    return "none"

                # Check the tag name of the next element
                tag_name = next_element.name.lower() if next_element.name else "neither"

                if tag_name == "table":
                    return "table"
                elif tag_name == "ul":
                    return "ul"
                else:
                    return "neither"

        # If specifications heading not found
        return "not found"

    except Exception as e:
        LogEntry(f"Error parsing HTML in GetSpecificationsFollower: {e}", 3)
        return "error"

def GetFileHandle(strFileName, strperm):
    """
    This wraps error handling around standard file open function
    Parameters:
      strFileName: Simple string with filename to be opened
      strperm: single character string, usually w or r to indicate read vs write.
      other options such as "a" and "x" are valid too.
    Returns:
      File Handle object
    """
    dictModes = {}
    dictModes["w"] = "writing"
    dictModes["r"] = "reading"
    dictModes["a"] = "appending"
    dictModes["x"] = "opening"
    dictModes["wb"] = "binary write"

    cMode = strperm[:2].lower().strip()

    try:
        if len(strperm) > 1:
          objFileHndl = open(strFileName, strperm)
        else:
          objFileHndl = open(strFileName, strperm, encoding='utf8', buffering=1)
        return objFileHndl
    except PermissionError:
        LogEntry("unable to open output file {} for {}, "
              "permission denied.".format(strFileName, dictModes[cMode]))
        return ("Permission denied")
    except FileNotFoundError:
        LogEntry("unable to open output file {} for {}, "
              "Issue with the path".format(strFileName, dictModes[cMode]))
        return ("FileNotFound")
    except Exception as err:
      LogEntry("Unknown error: {}".format(err))
      return ("unknowErr")

def FetchEnv(strVarName):
  """
  Function that fetches the specified content of specified environment variable.
  Parameters:
    strVarName: The name of the environment variable to be fetched
  Returns:
    The content of the environment or none if the variable is not set or is blank
  """

  if os.getenv(strVarName) != "" and os.getenv(strVarName) is not None:
    return os.getenv(strVarName)
  else:
    return None

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
  global iTotal
  global iTotalPages

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
  iTotal = int(WebRequest.headers.get("X-WP-Total", -1))
  iTotalPages = int(WebRequest.headers.get("X-WP-TotalPages", -1))

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
  global objFileOut

  dictProxies = {}
  strOutDir = None
  objFileOut = None
  iLoc = sys.argv[0].rfind(".")
  strDefConf = sys.argv[0][:iLoc] + ".ini"
  objParser = argparse.ArgumentParser(description="WooCommerce Product description parser and attrib creator. "
                                      "Must specify one Action directive, otherwise defaults to audit. "
                                      "If no config file is specified, "
                                      "it will look for {} in the same directory as the script.".format(strDefConf))
  objParser.add_argument("--silent", dest="silent",
                      action="store_true", help="only output to file, not to screen")
  objParser.add_argument("--audit", dest="audit",
                      action="store_true", help="Action directive. Only audit products and attributes, no updates. "
                      "Default action if no other action is specified.")
  objParser.add_argument("--update", dest="update",
                      action="store_true", help="Action directive. Update all products with attributes parsed from description. "
                      "Required unless you specify another action, only one action can be specified.")
  objParser.add_argument("--import", dest="prodimport",
                      action="store_true", help="Action directive. Create new products based on import file."
                      "Required unless you specify another action, only one action can be specified.")
  objParser.add_argument("--fix", dest="fix",
                      action="store_true", help="Action directive. Fix product descriptions."
                      "Required unless you specify another action, only one action can be specified.")
  objParser.add_argument("-c", "--config",type=str, help="Path to the configuration file", default=strDefConf)
  objParser.add_argument("-v", "--verbosity", action="count", default=1, help="Verbose output, vv level 2 vvvv level 4")
  objParser.add_argument("-x", "--proxy", type=str, help="Proxy to use for API calls")
  objParser.add_argument("-o", "--outdir", type=str, help="Output directory for generated files")

  objArgs = objParser.parse_args()
  iVerbose = objArgs.verbosity

  ISO = time.strftime("-%Y-%m-%d")
  strVersion = "{0}.{1}.{2}".format(sys.version_info[0], sys.version_info[1], sys.version_info[2])
  strRealPath = os.path.realpath(sys.argv[0]).replace("\\", "/")

  strBaseDir = os.path.dirname(sys.argv[0])
  if strBaseDir == "":
    iLoc = strRealPath.rfind("/")
    strBaseDir = strRealPath[:iLoc]
  if strBaseDir[-1:] != "/":
    strBaseDir += "/"

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
  objLogOut = GetFileHandle(strLogFile, "w")
  strScriptHost = platform.node().upper()
  bQuiet = objArgs.silent
  bAudit = objArgs.audit
  bUpdate = objArgs.update
  bImport = objArgs.prodimport
  bFix = objArgs.fix

  LogEntry("This is a script to parse WooCommerce product description for specifications "
           "and create product attributes from it."
          "This is running under Python Version {}".format(strVersion))
  LogEntry("Running from: {}".format(strRealPath))
  dtNow = time.strftime("%A %d %B %Y %H:%M:%S %Z")
  LogEntry("The script started at {}".format(dtNow))

  # Validate that only one action is specified
  iActionCount = sum([bAudit, bUpdate, bImport, bFix])
  if iActionCount > 1:
    LogEntry("Error: More than one action directive specified. "
             "Only one of --audit, --update, --import, or --fix can be used.",0,True)
  elif iActionCount == 0:
    bAudit = True
    LogEntry("No action directive specified, defaulting to --audit")

  # Determine and set the action string
  if bAudit:
    strAction = "AUDIT"
  elif bUpdate:
    strAction = "UPDATE"
  elif bImport:
    strAction = "IMPORT"
  elif bFix:
    strAction = "FIX"

  LogEntry("Selected action: {}".format(strAction))

  if FetchEnv("PROXY") is not None:
    strProxy = FetchEnv("PROXY")
  else:
    strProxy = None
  if objArgs.proxy is not None:
    strProxy = objArgs.proxy
  if strProxy is not None:
    dictProxies["http"] = strProxy
    dictProxies["https"] = strProxy
    LogEntry("Proxy has been configured for {}".format(strProxy))
  else:
    LogEntry("No proxy has been configured")
  strConfile = objArgs.config
  if os.path.isfile(strConfile):
    LogEntry ("Configuration File {} exists".format(strConfile))
  else:
    LogEntry ("Can't find configuration file {}, defaulting to {}".format(strConfile,strDefConf))
    strConfile = strDefConf
  if os.path.isfile(strConfile):
    LogEntry ("Configuration File {} exists".format(strConfile))
  else:
    LogEntry ("Can't find configuration file {}, exiting.".format(strConfile),0,True)

  objConFileHndl = GetFileHandle(strConfile, "r")
  objConfig = configparser.ConfigParser()
  objConfig.read_file(objConFileHndl)
  objConFileHndl.close()

  if "Generic" in objConfig:
    if "AuthMethod" in objConfig["Generic"]:
      strAuthMethod = objConfig["Generic"]["AuthMethod"].strip().lower()[:3]
      if strAuthMethod not in ["env", "1pa"]:
        LogEntry("Invalid AuthMethod specified in config. Must be 'env' or '1Password', "
                 "case insensitive and only the first three characters are relevant. Defaulting to '1Password'.")
        strAuthMethod = "1pa"
    else:
      LogEntry("AuthMethod not found in config, defaulting to '1Password'.")
      strAuthMethod = "1pa"
    if "OutDir" in objConfig["Generic"]:
      strDefOutDir = objConfig["Generic"]["OutDir"]
    else:
      strDefOutDir = strBaseDir + "Output/"
    if "FileTimeStampFormat" in objConfig["Generic"]:
      strTimeStampFormat = objConfig["Generic"]["FileTimeStampFormat"]
    else:
      strTimeStampFormat = "%Y-%m-%d %H:%M:%S"
    if "TimeStampAudit" in objConfig["Generic"]:
      bTimeStampAudit = objConfig["Generic"]["TimeStampAudit"].lower() == "true"
    else:
      bTimeStampAudit = True
    if "Filter" in objConfig["Generic"]:
      strFilter = objConfig["Generic"]["Filter"]
    else:
      strFilter = None
    if "AttrEqFile" in objConfig["Generic"]:
      strAttrEqFile = objConfig["Generic"]["AttrEqFile"]
    else:
      strAttrEqFile = None
    if "PerPage" in objConfig["Generic"]:
      if isInt(objConfig["Generic"]["PerPage"]):
        iPerPage = int(objConfig["Generic"]["PerPage"])
      else:
        LogEntry("PerPage value in config is not an integer, defaulting to 25")
        iPerPage = 25
  else:
    LogEntry("section Generic not found in config")
  if "WPCreds" in objConfig:
    if strAuthMethod == "1pa":
      if "AccountName" in objConfig["WPCreds"]:
        strAccountName = objConfig["WPCreds"]["AccountName"]
      else:
        LogEntry("Account name not found in config")
      if "VaultID" in objConfig["WPCreds"]:
        strVaultID = objConfig["WPCreds"]["VaultID"]
      else:
        LogEntry("VaultID not found in config")
      if "ItemID" in objConfig["WPCreds"]:
        strItemID = objConfig["WPCreds"]["ItemID"]
      else:
        LogEntry("ItemID not found in config")
    if "ConsumerKeyField" in objConfig["WPCreds"]:
      strConsumerKeyField = objConfig["WPCreds"]["ConsumerKeyField"]
    else:
      LogEntry("ConsumerKeyField not found in config")
    if "ConsumerSecretField" in objConfig["WPCreds"]:
      strConsumerSecretField = objConfig["WPCreds"]["ConsumerSecretField"]
    else:
      LogEntry("ConsumerSecretField not found in config")
    if "BaseURLField" in objConfig["WPCreds"]:
      strBaseURLField = objConfig["WPCreds"]["BaseURLField"]
    else:
      LogEntry("BaseURLField not found in config")
  else:
    LogEntry("section WPCreds not found in config")

  if strAttrEqFile is not None:
    if os.path.isfile(strAttrEqFile):
      LogEntry("Attribute equivalence file {} found, processing.".format(strAttrEqFile))
      objAttrEqFileHndl = GetFileHandle(strAttrEqFile, "r")
      dictAttrEq = {}
      for strLine in objAttrEqFileHndl:
        if ";" in strLine:
          strKey, strValue = strLine.split(";", 1)
          dictAttrEq[strKey.strip()] = strValue.strip()
      objAttrEqFileHndl.close()
    else:
      LogEntry("Attribute equivalence file {} specified but not found, ignoring.".format(strAttrEqFile))

  strToken = FetchEnv("TOKEN")
  if not strToken:
    strToken = None

  strOutDir = objArgs.outdir if objArgs.outdir else strDefOutDir
  strOutDir = strOutDir.replace("\\", "/")
  if strOutDir[-1:] != "/":
    strOutDir += "/"
  if not os.path.exists(strOutDir):
    os.makedirs(strOutDir)
    LogEntry("Output directory {} didn't exist, so I created it.".format(strOutDir))
  else:
    LogEntry("Output directory {} good to go.".format(strOutDir))

  if strAuthMethod == "1pa":
    strCredMethod = "1Password"
    dictItemCollection = {}
    dictItemSpecs = {}
    dictItemSpecs["vault_id"] = strVaultID
    dictItemSpecs["item_id"] = strItemID
    dictItemCollection["Creds"] = dictItemSpecs

    LogEntry("Attempting to retrieve credentials from 1Password, with account name {} and token {}".format(
      strAccountName, "provided" if strToken else "not provided"))

    returned_dict = asyncio.run(get1PasswordItems(dictItemCollection, strAccountName=strAccountName, strToken=strToken))
    if returned_dict is None:
      LogEntry("Failed to retrieve item.",0,True)
    if "fatal error" in returned_dict:
      LogEntry("Fatal 1pass error: {}".format(returned_dict['fatal error']['error message']),0,True)
  elif strAuthMethod == "env":
    strCredMethod = "Environment Variables"
    LogEntry("Using environment variable authentication method. Fetching credentials from environment variables.")
    dictItemCollection = {}
    dictItemSpecs = {}
    dictItemSpecs["BaseURLField"] = strBaseURLField
    dictItemSpecs["ConsumerKeyField"] = strConsumerKeyField
    dictItemSpecs["ConsumerSecretField"] = strConsumerSecretField
    dictItemCollection["Creds"] = dictItemSpecs

    returned_dict = GetEnvCreds(dictItemCollection)
    if not returned_dict or "Creds" not in returned_dict:
      LogEntry("Failed to retrieve credentials from environment variables.",0,True)

  strBaseURL = returned_dict["Creds"][strBaseURLField]
  strWCKey = returned_dict["Creds"][strConsumerKeyField]
  strWCSecret = returned_dict["Creds"][strConsumerSecretField]

  if not strBaseURL or not strWCKey or not strWCSecret:
      LogEntry("No URL Consumer Key or Secret, unable to proceed.",0,True)

  LogEntry("Successfully retrieved credentials from {}. "
           "Now fetching global attributes from WooCommerce to prepare for product updates.".format(strCredMethod))
  dictHeader = {}
  strMethod = "get"
  strEndPoint = "/wp-json/wc/v3/products/attributes"
  dictGlobalAttributes = {}
  strURL = strBaseURL + strEndPoint
  dictResponse = MakeAPICall(strURL,dictHeader,strMethod,strUser=strWCKey,strPWD=strWCSecret)
  if dictResponse[0]["Success"]==False:
    LogEntry("API call to WooCommerce failed. {}".format(dictResponse[1]),0,False)
  LogEntry("API call successful, processing response. "
             "{} total attributes in response, {} total pages".format(iTotal, iTotalPages),0)
  dictAttrCollection = dictResponse[1]
  if len(dictAttrCollection) != iTotal:
    LogEntry("Warning, total attributes in response does not match total in header. {} vs {}".format(len(dictAttrCollection), iTotal))
  else:
    LogEntry("Total attributes in response matches total in header. {} attributes".format(iTotal))

  for dictAttr in dictAttrCollection:
    dictGlobalAttributes[dictAttr["name"].strip().lower()] = dictAttr["id"]

  if strAction == "IMPORT":
    LogEntry("IMPORT action is not implemented yet, exiting.")
    # TODO: implement the import action, which will read a file with product information
    # and create new products in WooCommerce based on that. Have Claude generate the product description
    # and short description based on the product information in the file, and populate attributes as well.

    objLogOut.close()
    print("objLogOut closed")
    return

  if strAction == "FIX":
    LogEntry("FIX action is not implemented yet, exiting.",0,True)
    # TODO: implement the fix action, which will go through products and
    # flush out the description and put the specifications into attributes,
    # Use Claude to populate both description and short description based on the product information,

    strFilter = "status:draft|tag:needsfixing"

  if strAction == "AUDIT":
    if bTimeStampAudit:
      strOutFileName = strOutDir + "ProdattrAudit_" + time.strftime(strTimeStampFormat) + ".csv"
    else:
      strOutFileName = strOutDir + "ProdattrAudit.csv"
    LogEntry("Starting audit of product descriptions for attributes. Output file is {}".format(strOutFileName))
    objFileOut = GetFileHandle(strOutFileName, "w")
    if objFileOut is None or isinstance(objFileOut, str):
      objFileOut = None
      LogEntry("Unable to open output file {}, error: {}".format(strOutFileName, objFileOut),0,True)
    objFileOut.write("Brand,SKU,Name,Existing Attribute Count,Description Attributes Count\n")
  iPage = 1
  iProdCount = 5
  iTotalProducts = 0
  strEndPoint = "/wp-json/wc/v3/products"
  dictHeader = {}
  strMethod = "get"
  dictParams = {}
  dictParams["per_page"] = iPerPage
  if strFilter is not None:
     lstFilters = strFilter.split("|")
     for lstFilter in lstFilters:
        if ":" in lstFilter:
           strFilterKey, strFilterValue = lstFilter.split(":", 1)
           LogEntry("Filtering products with {} of {}".format(strFilterKey, strFilterValue))
           dictParams[strFilterKey] = strFilterValue
  while iProdCount > 0:
    LogEntry("Fetching products, page {} of {}".format(iPage, iTotalPages))
    dictParams["page"] = iPage
    strParams = urlparse.urlencode(dictParams)
    strURL = strBaseURL + strEndPoint + "?" + strParams
    dictResponse = MakeAPICall(strURL,dictHeader,strMethod,strUser=strWCKey,strPWD=strWCSecret)
    if dictResponse[0]["Success"]==False:
      LogEntry("API call to WooCommerce failed. {}".format(dictResponse[1]),0,False)
    LogEntry("API call successful, processing response. "
             "{} total products in response, {} total pages".format(iTotal, iTotalPages),0)
    dictProducts = dictResponse[1]
    iProdCount = len(dictProducts)
    iTotalProducts += iProdCount
    LogEntry("Received {} products in page {}. Total products fetched: {}".format(iProdCount, iPage, iTotalProducts))
    iPage += 1
    for dictProduct in dictProducts:
      if "description" not in dictProduct or dictProduct["description"] is None:
        LogEntry("Product {} with SKU {} and name {} has no description, skipping.".format(dictProduct["id"],
                                                                dictProduct["sku"], dictProduct["name"]))
        continue
      dictAttributes = ExtractTwoColumnTables(dictProduct["description"])
      lstProdAttribs = dictProduct["attributes"] if "attributes" in dictProduct and dictProduct["attributes"] is not None else []
      LogEntry("Working on product {} with SKU {} and name {}. "
               "It has {} existing attributes and {} attributes in the description.".format(
                  dictProduct["id"], dictProduct["sku"], dictProduct["name"],
                  len(lstProdAttribs), len(dictAttributes)))
      if strAction == "UPDATE":
        for dictKey in dictAttributes.items():
          if dictKey[0] in dictAttrEq:
              strKey = dictAttrEq[dictKey[0]]
              LogEntry("Changing attribute {} to {}".format(dictKey[0], strKey))
          else:
            strKey = dictKey[0].strip()[:28]
          if strKey == "MTBF":
            lstValue = [dictKey[1]]
          else:
            lstValue = dictKey[1].split(",")
          if strKey.lower() in dictGlobalAttributes:
              iAttrID = dictGlobalAttributes[strKey.lower()]
          else:
              LogEntry("Attribute {} not found in global attributes, creating it.".format(strKey))
              iAttrID = CreateGlobalAttribute(strKey.strip(), strBaseURL, strWCKey, strWCSecret)
          if AttributeExists(lstProdAttribs, strKey):
              LogEntry("Attribute {} already on product.".format(strKey))
          else:
              LogEntry("Attribute {} is not on product. Need to add {} to attributeID {} ".format(
                strKey, lstValue, iAttrID))
              lstProdAttribs.append({"id": iAttrID, "visible": True, "variation": False, "options": lstValue})
        LogEntry("Need to post new attributes {}".format(lstProdAttribs))

      strBrand = "No Brand"
      dictBrands = dictProduct["brands"]
      if isinstance(dictBrands, list):
         if len(dictBrands) > 0:
            strBrand = dictBrands[0]["name"]
      if strAction == "AUDIT":
        objFileOut.write("{},{},{},{},{}\n".format(strBrand.strip(), dictProduct["sku"],
            dictProduct["name"].replace(","," ").strip(), len(lstProdAttribs), len(dictAttributes)))
        objFileOut.flush()

  if objFileOut is not None:
    objFileOut.close()

  LogEntry("Finished fetching products. Total products fetched: {}".format(iTotalProducts))

  objLogOut.close()
  print("objLogOut closed")




if __name__ == '__main__':
  main()