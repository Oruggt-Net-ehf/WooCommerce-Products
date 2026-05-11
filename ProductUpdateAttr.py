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
pip install anthropic

'''
# Import libraries
import os
import re
import time
import sys
import json
import requests
import sentry_sdk
import argparse
import configparser
import csv
import asyncio
import platform
import urllib.parse as urlLib
from onepassword import Client, DesktopAuth
from bs4 import BeautifulSoup
from anthropic import Anthropic

# End imports

# Few globals
requests.urllib3.disable_warnings()
tLastCall = 0
iTotalSleep = 0
iTimeOut = 180  # Max time in seconds to wait for network response
iMinQuiet = 10  # Minimum time in seconds between API calls
strDefAIenvName = "ANTHROPIC_API_KEY" # Name of the environment variable where the AI key is stored, if using env variable for auth
strDefMetricEndPoint = "metrics" # Endpoint to submit metrics to, appended to the ingestion host
strDef1PassTokenEnvVar = "1PASSTOKEN" # Name of the environment variable where the 1Password token is stored, if using token for auth
iDefMaxToken = 2048 # Max tokens to use for the AI calls, can be adjusted based on needs and model limits
iDefPerPage = 25 # Number of items to fetch per page for API calls

strSentryURL = "https://prxVN17LbuNbxB4Tg2vK8g4x@s2386117.eu-fsn-3.betterstackdata.com/2386117" # Sentry URL for error logging, replace with your own if needed

sentry_sdk.init(
    dsn=strSentryURL,
    # Set traces_sample_rate to 1.0 to capture 100%
    # of transactions for performance monitoring.
    traces_sample_rate=1.0,
)

# sub defs

def GenerateProductDescription(strDetails:str,strSystem:str, objClient:any, strModel:str, iMaxToken:int)->dict:
  """
  Create a WooCommerce compatible product details using the REST API.
  Parameters:
  strDetails (str): A string with details about the product, such make, model and other relevant facts
  strSystem (str): The system prompt text
  objClient: The object from SDK when the client was created.
  strModel: A string representing the model to use
  iMaxToken: Integer for the Max token variable

  Returns: A dictionary object with the respone
  """

  dictMessage = {}
  dictMessage["role"] = "user"
  dictMessage["content"] = "Using the following details please generate product description: {}".format(strDetails)
  dictSystemPrompt = {}
  dictSystemPrompt["type"] = "text"
  dictSystemPrompt["text"] = strSystem
  dictSystemPrompt["cache_control"] = {"type": "ephemeral"}

  objMessage = objClient.messages.create(model=strModel,max_tokens=iMaxToken,system=[dictSystemPrompt],messages=[dictMessage])
  if strMetricURL:
    dictPayload = {}
    dictPayload["input_tokens"] = objMessage.usage.input_tokens
    dictPayload["output_tokens"] = objMessage.usage.output_tokens
    lstMetrics = Convert2OpenMetricGauge(dictPayload)
    WebResponse = SubmitMetric(lstMetrics,strMetricURL,strMetricToken,strEndPoint=strMetricEndpoint)
    LogEntry("Response from metric server: {}".format(WebResponse),1)

  LogEntry("Description creation complete. Token In: {} Token Out: {}".format(objMessage.usage.input_tokens,objMessage.usage.output_tokens),1)
  return ParseJsonResponse(objMessage.content[0].text)

def CreateWooCommerceProduct(dictProduct, strBaseURL, strWCKey, strWCSecret):
    """
    Create a new WooCommerce product using the REST API.
    Parameters:
    dictProduct (dict): A dictionary containing the product data to create.
                        Keys can include name, description, regular_price, sku, etc.
    strBaseURL (str): The base URL of the WooCommerce site (e.g., "https://example.com")
    strWCKey (str): WooCommerce API consumer key
    strWCSecret (str): WooCommerce API consumer secret

    Returns: A tuple of the form ({"Success": True/False}, response_data)
    """
    dictHeader = {}
    strMethod = "post"
    strEndPoint = "/wp-json/wc/v3/products"
    strURL = strBaseURL + strEndPoint

    LogEntry("Creating WooCommerce product SKU: {}".format(dictProduct.get("sku")),2)
    return MakeAPICall(strURL, dictHeader, strMethod, dictProduct, strUser=strWCKey, strPWD=strWCSecret)

def CreateWooCommerceProductsFromCSV(strCSVPath:str, strBaseURL:str, strWCKey:str, strWCSecret:str,
                                     strAIsystem:str, objAIClient:any,strAIModel:str,iMaxTokens:int, strDelim:str=",")->list:
    """
    Read a CSV file with columns:
    Brand, sku, EAN/GTIN, Name, Descr, Price, Cost, Stock, Allow Backorder, Enable Reviews
    and create new WooCommerce simple products for each row.

    Parameters:
    strCSVPath (str): The file path to the CSV file to read
    strBaseURL (str): The base URL of the WooCommerce site (e.g., "https://example.com")
    strWCKey (str): WooCommerce API consumer key
    strWCSecret (str): WooCommerce API consumer secret
    strAISystem (str): Text to pass to the AI with background instructions
    objAIclient: the output from anthropic client create
    strAIModel(str): String representing the model to use
    iMaxtokens(int): Integer limiting how many tokens each call uses
    strDelim (str): The strDelim used in the CSV file, defaults to comma (",")

    Returns:
    list of tuples: Each tuple contains (sku, API response) for each product creation attempt
    API response is a tuple of the form ({"Success": True/False}, response_data)
    """
    lstResults = []

    with open(strCSVPath, mode="r", newline="", encoding="utf-8-sig") as objCSVFile:
        objCSVReader = csv.DictReader(objCSVFile, delimiter=strDelim)
        for objRow in objCSVReader:
            strSKU = (objRow.get("sku") or objRow.get("SKU") or "").strip()
            if not strSKU:
                LogEntry("Skipping objRow with missing SKU",0)
                continue

            strProdName = (objRow.get("Name") or "").strip()
            strDescr = (objRow.get("Descr") or "").strip()
            strBackorders = objRow.get("Allow Backorder", "")
            if not strBackorders:
                strBackorders = "no"
            strAllowReviews = objRow.get("Enable Reviews", "")
            if not strAllowReviews:
                strAllowReviews = "no"
            bAllowReviews = strAllowReviews.lower() == "true"
            strQTY = objRow.get("Stock", "").strip()
            strPrice = objRow.get("Price", "").strip()
            strGTIN = objRow.get("EAN/GTIN", "").strip()
            strBrand_asis = objRow.get("Brand", "").strip()
            strBrand = objRow.get("Brand", "").strip().lower()
            if strBrand in dictGlobalBrands:
              dictBrandID = {}
              dictBrandID["id"] = int(dictGlobalBrands[strBrand])
              lstBrandID = [dictBrandID]
              LogEntry("Brand {} has ID of {}".format(strBrand_asis,lstBrandID),3)
            else:
              LogEntry("Brand {} can't be found, attempting to create it".format(strBrand_asis),3)
              iBrandID = CreateBrand(strBrand_asis, strBaseURL, strWCKey, strWCSecret)
              LogEntry("Brand {} now exists as {}".format(strBrand_asis,iBrandID),3)
              if iBrandID is not None:
                dictGlobalBrands[strBrand] = int(iBrandID)
                dictBrandID = {}
                dictBrandID["id"] = iBrandID
                lstBrandID = [dictBrandID]
              else:
                lstBrandID = []

            strProdDetails = "{} {} {} {}".format(strProdName,strDescr, lstBrandID, strSKU)
            dictResult = GenerateProductDescription(strProdDetails,strAIsystem,objAIClient,strAIModel,iMaxTokens)

            dictProduct = {}
            dictProduct["status"] = "pending"
            dictProduct["name"] = dictResult["Product_Name"]
            dictProduct["type"] = "simple"
            dictProduct["sku"] = strSKU
            dictProduct["description"] = dictResult["description"]
            dictProduct["short_description"] = dictResult["short_description"]
            dictProduct["backorders"] = strBackorders
            dictProduct["regular_price"] = strPrice if strPrice else None
            dictProduct["reviews_allowed"] = bAllowReviews
            dictProduct["manage_stock"] = True
            dictProduct["global_unique_id"] = strGTIN
            dictProduct["brands"] = lstBrandID
            dictProduct["stock_quantity"] = int(strQTY)

            # Remove None values so payload stays clean
            dictCleaned = {}
            for strKey, strValue in dictProduct.items():
                if strValue is not None:
                    dictCleaned[strKey] = strValue
            dictProduct = dictCleaned

            dictResult = CreateWooCommerceProduct(dictProduct, strBaseURL, strWCKey, strWCSecret)
            lstResults.append((strSKU, dictResult))

    return lstResults

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
        # Get all objRows
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
        str: if the attribute name is found in the collection (case-insensitive) returns local or global as appropriate, false otherwise
    """
    if not listAttributeCollection:
        return False

    strSearchLower = strSearchName.strip().lower()

    for dictAttribute in listAttributeCollection:
      if isinstance(dictAttribute, dict) and "name" in dictAttribute:
        if dictAttribute["name"].strip().lower() == strSearchLower:
          if dictAttribute["id"] == 0:
            LogEntry("Attribute {} is local".format(strSearchName),2)
            return dictAttribute
          else:
            return "global"

    return "false"

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

def CreateBrand(strBrandName, strBaseURL, strWCKey, strWCSecret):
    """
    Create a new global brand in WooCommerce and return its ID.

    Parameters:
        strBrandName (str): The name of the brand to create
        strBaseURL (str): The base URL of the WooCommerce site
        strWCKey (str): WooCommerce API consumer key
        strWCSecret (str): WooCommerce API consumer secret

    Returns:
        int: The ID of the newly created brand, or None if creation failed
    """
    dictHeader = {}
    strMethod = "post"
    strEndPoint = "/wp-json/wc/v3/products/brands"
    strURL = strBaseURL + strEndPoint

    # Create the payload with the brand name
    dictPayload = {
        "name": strBrandName.strip()
    }

    LogEntry("Creating new brand: {}".format(strBrandName), 2)

    # Make the API call
    dictResponse = MakeAPICall(strURL, dictHeader, strMethod, dictPayload, strUser=strWCKey, strPWD=strWCSecret)

    # Check if the call was successful
    if dictResponse[0]["Success"] == False:
        LogEntry("Failed to create brand '{}'. Error: {}".format(strBrandName, dictResponse[1]), 0, False)
        return None

    # Extract the ID from the response
    if dictResponse[1] and isinstance(dictResponse[1], dict) and "id" in dictResponse[1]:
        iNewBrandID = dictResponse[1]["id"]
        LogEntry("Successfully created brand '{}' with ID: {}".format(strBrandName, iNewBrandID), 2)
        return iNewBrandID
    else:
        LogEntry("Brand created but could not extract ID from response", 0, False)
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

def LoadDictionaries(strEndPoint, strBaseURL, strWCKey, strWCSecret):
  LogEntry("Loading values from {}".format(strEndPoint),1)
  dictHeader = {}
  strMethod = "get"
  dictGeneric = {}
  dictParams = {}
  dictParams["per_page"] = 10 #iPerPage
  strURL = strBaseURL + strEndPoint
  dictResponse = MakeAPICall(strURL,dictHeader,strMethod,strUser=strWCKey,strPWD=strWCSecret)
  if dictResponse[0]["Success"]==False:
    LogEntry("API call to WooCommerce endpoint {} failed. {}".format(strEndPoint, dictResponse[1]),0,False)
    return{}
  LogEntry("API call successful, processing response. "
             "{} overall total entries per response across {} total pages".format(iTotal, iTotalPages),1)

  for dictEntry in dictResponse[1]:
    dictGeneric[dictEntry["name"].strip().lower()] = dictEntry["id"]

  iPage = 2
  if len(dictResponse[1]) < iTotal:
    while len(dictResponse[1])  > 0:
      LogEntry("Fetching products, page {} of {}".format(iPage, iTotalPages),2)
      dictParams["page"] = iPage
      strParams = urlLib.urlencode(dictParams)
      strURL = strBaseURL + strEndPoint + "?" + strParams
      dictResponse = MakeAPICall(strURL,dictHeader,strMethod,strUser=strWCKey,strPWD=strWCSecret)
      if dictResponse[0]["Success"]==False:
        LogEntry("API call to WooCommerce endpoint {} failed. {}".format(strEndPoint, dictResponse[1]),0,False)
      LogEntry("API call successful, processing response. "
                "{} overall total entries per response across {} total pages".format(iTotal, iTotalPages),1)
      for dictEntry in dictResponse[1]:
        dictGeneric[dictEntry["name"].strip().lower()] = dictEntry["id"]
      iPage += 1


  return dictGeneric

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

def ParseJsonResponse(strText: str) -> dict:
    strCleaned = re.sub(r"^```(?:json)?\n?", "", strText.strip())
    strCleaned = re.sub(r"\n?```$", "", strCleaned).strip()
    return json.loads(strCleaned)

def NormalizeToHttps(strURL: str) -> str | None:
    parsedURL = urlLib.urlparse(strURL)

    # Already HTTPS
    if parsedURL.scheme == "https" and parsedURL.netloc:
        return strURL

    # Upgrade HTTP → HTTPS
    if parsedURL.scheme == "http" and parsedURL.netloc:
        return urlLib.urlunparse(parsedURL._replace(scheme="https"))

    # Bare FQDN → prepend https://
    if not parsedURL.scheme and IsFqdn(strURL):
        return "https://{}".format(strURL)

    return None

def IsFqdn(strHost: str) -> bool:
    strPattern = r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$'
    return bool(re.match(strPattern, strHost))

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
              "permission denied.".format(strFileName, dictModes[cMode]),0)
        return ("Permission denied")
    except FileNotFoundError:
        LogEntry("unable to open output file {} for {}, "
              "Issue with the path".format(strFileName, dictModes[cMode]),0)
        return ("FileNotFound")
    except Exception as err:
      LogEntry("Unknown error: {}".format(err),0)
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

def Convert2OpenMetricGauge(dictPayloads):
    """
    Transform a dictionary of metrics into OpenMetrics format, type Gauge.

    Parameters:
      dictPayloads: Dictionary with metric names as keys and numerical values

    Returns:
      List of dictionaries in OpenMetrics format

    Example:
      >>> BuildMetricsPayload({"temperature": 45.2, "clock_speed": 1800})
      [
          {"name": "temperature", "gauge": {"value": 45.2}},
          {"name": "clock_speed", "gauge": {"value": 1800}}
      ]
    """
    listMetrics = []
    for metric_name, metric_value in dictPayloads.items():
        dictMetric = {
            "name": metric_name,
            "gauge": {
                "value": metric_value
            }
        }
        listMetrics.append(dictMetric)
    return listMetrics

def SubmitMetric(dictPayload:dict,strURL:str,strToken:str,strEndPoint:str="metrics")->dict:
  strMethod = "post"
  strURL = strURL + strEndPoint

  LogEntry("Submitting metric to server:{}".format(json.dumps(dictPayload)),3)
  dictHeader = {}
  dictHeader["Content-type"] = "application/json"
  dictHeader["Authorization"] = "Bearer " + strToken
  WebRequest = MakeAPICall(strURL,dictHeader,strMethod,dictPayload)

  return WebRequest

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
  global str1PassToken
  global bQuiet
  global objLogOut
  global iVerbose
  global dictProxies
  global strScriptName
  global strScriptHost
  global objFileOut
  global dictGlobalAttributes
  global dictGlobalCategories
  global dictGlobalTags
  global dictGlobalBrands
  global iPerPage
  global strMetricURL
  global strMetricToken
  global strMetricEndpoint

  dictProxies = {}
  strOutDir = None
  objFileOut = None
  strAccountName = None

  strDefAImodel = "claude-sonnet-4-6"

  iLoc = sys.argv[0].rfind(".")
  strDefConf = sys.argv[0][:iLoc] + ".ini"
  objParser = argparse.ArgumentParser(description="WooCommerce Product description parser and attrib creator. "
                                      "If no config file is specified, it will look for {} in the same directory as the script. "
                                      "Requires one and only one Action directive. If omitted the script prompts for it.".format(strDefConf))
  objParser.add_argument("--silent", dest="silent",
                      action="store_true", help="only output to file, not to screen")
  objParser.add_argument("--audit", dest="audit",
                      action="store_true", help="Action directive. Only audit products and attributes, no updates. ")
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
  objParser.add_argument("-i", "--input", type=str, help="Input file for product import action, overrides config file setting for import file")

  objArgs = objParser.parse_args()
  iVerbose = objArgs.verbosity

  ISO = time.strftime("-%Y-%m-%d-%H-%M-%S")
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
           "and create product attributes from it. Can also import new products "
           "and rewrite product descriptions.\n"
          "This is running under Python Version {}".format(strVersion),0)
  LogEntry("Running from: {}".format(strRealPath),0)
  dtNow = time.strftime("%A %d %B %Y %H:%M:%S %Z")
  LogEntry("The script started at {}".format(dtNow),0)
  LogEntry("Verbosity is set to {}".format(iVerbose),1)

  # Validate that only one action is specified
  iActionCount = sum([bAudit, bUpdate, bImport, bFix])
  if iActionCount > 1:
    LogEntry("Error: More than one action directive specified. "
             "Only one of --audit, --update, --import, or --fix can be used.",0,True)
  elif iActionCount == 0:
    strAction = input("Please specify action, one of AUDIT, UPDATE, IMPORT or FIX: ")
    strAction = strAction.upper()
    if strAction not in ["AUDIT", "UPDATE", "IMPORT", "FIX"]:
      LogEntry("Invalid action directive '{}', aborting".format(strAction),0,True)
  else:
    # Determine and set the action string
    if bAudit:
      strAction = "AUDIT"
    elif bUpdate:
      strAction = "UPDATE"
    elif bImport:
      strAction = "IMPORT"
    elif bFix:
      strAction = "FIX"

  LogEntry("Selected action: {}".format(strAction),0)

  if FetchEnv("PROXY") is not None:
    strProxy = FetchEnv("PROXY")
  else:
    strProxy = None
  if objArgs.proxy is not None:
    strProxy = objArgs.proxy
  if strProxy is not None:
    dictProxies["http"] = strProxy
    dictProxies["https"] = strProxy
    LogEntry("Proxy has been configured for {}".format(strProxy),0)
  else:
    LogEntry("No proxy has been configured",0)
  strConfile = objArgs.config
  if os.path.isfile(strConfile):
    LogEntry ("Configuration File {} exists".format(strConfile))
  else:
    LogEntry ("Can't find configuration file {}, defaulting to {}".format(strConfile,strDefConf))
    strConfile = strDefConf

  objConFileHndl = GetFileHandle(strConfile, "r")
  objConfig = configparser.ConfigParser()
  objConfig.read_file(objConFileHndl)
  objConFileHndl.close()

  if "Generic" in objConfig:
    if "AuthMethod" in objConfig["Generic"]:
      strAuthMethod = objConfig["Generic"]["AuthMethod"].strip().lower()[:3]
      if strAuthMethod not in ["env", "1pa"]:
        LogEntry("Invalid AuthMethod specified in config. Must be 'env' or '1Password', "
                 "case insensitive and only the first three characters are relevant. Defaulting to '1Password'.",0)
        strAuthMethod = "1pa"
    else:
      LogEntry("AuthMethod not found in config, defaulting to '1Password'.",0)
      strAuthMethod = "1pa"
    if strAuthMethod == "1pa":
      if "1PassAccount" in objConfig["Generic"]:
        strAccountName = objConfig["Generic"]["1PassAccount"]
      else:
        LogEntry("Account name not found in config",1)
      if "1PassToken" in objConfig["Generic"]:
        str1PassToken = objConfig["Generic"]["1PassToken"]
      else:
        str1PassToken = strDef1PassTokenEnvVar
    if "OutDir" in objConfig["Generic"]:
      strDefOutDir = objConfig["Generic"]["OutDir"]
    else:
      strDefOutDir = strBaseDir + "Output/"
    if "MaxTokens" in objConfig["Generic"]:
      iMaxTokens = objConfig["Generic"]["MaxTokens"]
    else:
      iMaxTokens = None
    if "AIModel" in objConfig["Generic"]:
      strAIModel = objConfig["Generic"]["AIModel"]
    else:
      strAIModel = strDefAImodel
    if "IngestionHost" in objConfig["Generic"]:
      strMetricURL = objConfig["Generic"]["IngestionHost"]
    else:
      strMetricURL = None
    if "MetricEndpoint" in objConfig["Generic"]:
      strMetricEndpoint = objConfig["Generic"]["MetricEndpoint"]
    else:
      strMetricEndpoint = strDefMetricEndPoint
    if "ImportFile" in objConfig["Generic"]:
      strImportFile = objConfig["Generic"]["ImportFile"]
    else:
      strImportFile = None
    if "AIBackgroundFile" in objConfig["Generic"]:
      strAIsystemFile = objConfig["Generic"]["AIBackgroundFile"]
    else:
      strAIsystemFile = None
    if "MaxCharIn" in objConfig["Generic"]:
      strMaxCharIn = objConfig["Generic"]["MaxCharIn"]
      if isInt(strMaxCharIn):
        iMaxCharIn = int(strMaxCharIn)
      else:
        LogEntry("MaxCharIn value in config is not an integer, defaulting to 0",0)
        iMaxCharIn = 0
    else:
      iMaxCharIn = 0
    if "FileTimeStampFormat" in objConfig["Generic"]:
      strTimeStampFormat = objConfig["Generic"]["FileTimeStampFormat"]
    else:
      strTimeStampFormat = "%Y-%m-%d-%H-%M-%S"
    if "TimeStampAudit" in objConfig["Generic"]:
      bTimeStampAudit = objConfig["Generic"]["TimeStampAudit"].lower() == "true"
    else:
      bTimeStampAudit = True
    if "Filter" in objConfig["Generic"]:
      strFilter = objConfig["Generic"]["Filter"]
    else:
      strFilter = None
    if "FixStatus" in objConfig["Generic"]:
      strFixStatus = objConfig["Generic"]["FixStatus"]
    else:
      strFixStatus = None
    if "FixTag" in objConfig["Generic"]:
      strFixTag = objConfig["Generic"]["FixTag"]
    else:
      strFixTag = None
    if "FixCategory" in objConfig["Generic"]:
      strFixCategory = objConfig["Generic"]["FixCategory"]
    else:
      strFixCategory = None
    if "AttrEqFile" in objConfig["Generic"]:
      strAttrEqFile = objConfig["Generic"]["AttrEqFile"]
    else:
      strAttrEqFile = None
    if "PerPage" in objConfig["Generic"]:
      if isInt(objConfig["Generic"]["PerPage"]):
        iPerPage = int(objConfig["Generic"]["PerPage"])
      else:
        LogEntry("PerPage value in config is not an integer, defaulting to 25",0)
        iPerPage = iDefPerPage
  else:
    LogEntry("section Generic not found in config",0)

  if "AICreds" in objConfig:
    if strAuthMethod == "1pa":
      if "VaultID" in objConfig["AICreds"]:
        strAIVaultID = objConfig["AICreds"]["VaultID"]
      else:
        LogEntry("VaultID not found in config",0)
      if "ItemID" in objConfig["AICreds"]:
        strAIItemID = objConfig["AICreds"]["ItemID"]
      else:
        LogEntry("ItemID not found in config",0)
    if "APIKeyField" in objConfig["AICreds"]:
      strAIAPIKeyField = objConfig["AICreds"]["APIKeyField"]
    else:
      LogEntry("APIKeyField not found in config, setting default to {}".format(strDefAIenvName),0)
      strAIAPIKeyField = strDefAIenvName
    if "MetricTokenField" in objConfig["AICreds"]:
      strMetricTokenField = objConfig["AICreds"]["MetricTokenField"]
    else:
       strMetricTokenField = None
  else:
    LogEntry("section AICreds not found in config",0)

  if "WPCreds" in objConfig:
    if strAuthMethod == "1pa":
      if "VaultID" in objConfig["WPCreds"]:
        strVaultID = objConfig["WPCreds"]["VaultID"]
      else:
        LogEntry("VaultID not found in config",0)
      if "ItemID" in objConfig["WPCreds"]:
        strItemID = objConfig["WPCreds"]["ItemID"]
      else:
        LogEntry("ItemID not found in config",0)
    if "ConsumerKeyField" in objConfig["WPCreds"]:
      strConsumerKeyField = objConfig["WPCreds"]["ConsumerKeyField"]
    else:
      LogEntry("ConsumerKeyField not found in config",0)
    if "ConsumerSecretField" in objConfig["WPCreds"]:
      strConsumerSecretField = objConfig["WPCreds"]["ConsumerSecretField"]
    else:
      LogEntry("ConsumerSecretField not found in config",0)
    if "BaseURLField" in objConfig["WPCreds"]:
      strBaseURLField = objConfig["WPCreds"]["BaseURLField"]
    else:
      LogEntry("BaseURLField not found in config",0)
  else:
    LogEntry("section WPCreds not found in config",0)

  if objArgs.input is not None:
    strImportFile = objArgs.input

  if strAIsystemFile is None:
    LogEntry("Please provide a path to a text file providing context for AI Calls. "
              "Put it in the general section of the config file as 'AIBackgroundFile = system.txt' "
              "assuming the file is called system.txt and is in the script directory.",0,True)
  else:
    if os.path.isfile(strAIsystemFile):
      LogEntry("AI System file appears good",1)
      objAISystem = GetFileHandle(strAIsystemFile,"r")
      strAIsystem = objAISystem.read()
      objAISystem.close()
    else:
      LogEntry("AI system file {} specified but not found, please correct before proceeding.".format(strAttrEqFile),0,True)

  if strAttrEqFile is not None:
    if os.path.isfile(strAttrEqFile):
      LogEntry("Attribute equivalence file {} found, processing.".format(strAttrEqFile),1)
      objAttrEqFileHndl = GetFileHandle(strAttrEqFile, "r")
      dictAttrEq = {}
      for strLine in objAttrEqFileHndl:
        if ";" in strLine:
          strKey, strValue = strLine.split(";", 1)
          dictAttrEq[strKey.strip()] = strValue.strip()
      objAttrEqFileHndl.close()
    else:
      LogEntry("Attribute equivalence file {} specified but not found, ignoring.".format(strAttrEqFile),0)
  if not isInt(iMaxTokens):
    LogEntry("MaxToken value of '{}' is not valid. Setting it to the default of {}".format(iMaxTokens,iDefMaxToken),0)
    iMaxTokens = iDefMaxToken
  else:
     iMaxTokens = int(iMaxTokens)
  if strMetricURL and not strMetricTokenField:
     LogEntry("You provided Metric URL but didn't specify where to find the token, disabling Metric posting",0)
     strMetricURL = None

  if not strAccountName and strAuthMethod == "1pa":
     LogEntry("Auth method is 1Password but 1Password account name not specified, can't proceed",0,True)

  if not strMetricEndpoint:
      strMetricEndpoint = strDefMetricEndPoint

  str1PassToken = FetchEnv(strDef1PassTokenEnvVar)
  if not str1PassToken:
    str1PassToken = None

  strOutDir = objArgs.outdir if objArgs.outdir else strDefOutDir
  strOutDir = strOutDir.replace("\\", "/")
  if strOutDir[-1:] != "/":
    strOutDir += "/"
  if not os.path.exists(strOutDir):
    os.makedirs(strOutDir)
    LogEntry("Output directory {} didn't exist, so I created it.".format(strOutDir),0)
  else:
    LogEntry("Output directory {} good to go.".format(strOutDir),0)

  if strAuthMethod == "1pa":
    strCredMethod = "1Password"
    dictItemCollection = {}
    dictItemSpecs = {}
    dictItemSpecs["vault_id"] = strVaultID
    dictItemSpecs["item_id"] = strItemID
    dictItemCollection["WCreds"] = dictItemSpecs
    dictItemSpecs = {}
    dictItemSpecs["vault_id"] = strAIVaultID
    dictItemSpecs["item_id"] = strAIItemID
    dictItemSpecs["metric_key"] = strMetricTokenField
    dictItemCollection["AICreds"] = dictItemSpecs

    LogEntry("Attempting to retrieve credentials from 1Password, with account name {} and token {}".format(
      strAccountName, "provided" if str1PassToken else "not provided"),0)

    dictReturn = asyncio.run(get1PasswordItems(dictItemCollection, strAccountName=strAccountName, strToken=str1PassToken))
    if dictReturn is None:
      LogEntry("Failed to retrieve item.",0,True)
    if "fatal error" in dictReturn:
      LogEntry("Fatal 1pass error: {}".format(dictReturn['fatal error']['error message']),0,True)
  elif strAuthMethod == "env":
    strCredMethod = "Environment Variables"
    LogEntry("Using environment variable authentication method. Fetching credentials from environment variables.",0)
    dictItemCollection = {}
    dictItemSpecs = {}
    dictItemSpecs["BaseURLField"] = strBaseURLField
    dictItemSpecs["ConsumerKeyField"] = strConsumerKeyField
    dictItemSpecs["ConsumerSecretField"] = strConsumerSecretField
    dictItemCollection["WCreds"] = dictItemSpecs
    dictItemSpecs = {}
    dictItemSpecs["metric_key"] = strMetricTokenField
    dictItemSpecs["AISecret"] = strAIAPIKeyField
    dictItemCollection["AICreds"] = dictItemSpecs

    dictReturn = GetEnvCreds(dictItemCollection)

  if not dictReturn or "WCreds" not in dictReturn or "AICreds" not in dictReturn:
    LogEntry("Failed to retrieve credentials from environment variables.",0,True)

  strBaseURL = dictReturn["WCreds"].get(strBaseURLField)
  strWCKey = dictReturn["WCreds"].get(strConsumerKeyField)
  strWCSecret = dictReturn["WCreds"].get(strConsumerSecretField)
  strAIAPIKey = dictReturn["AICreds"].get(strAIAPIKeyField)
  strMetricToken = dictReturn["AICreds"].get(strMetricTokenField)

  if not strBaseURL or not strWCKey or not strWCSecret:
    LogEntry("No URL Consumer Key or Secret, unable to proceed.",0,True)
  if not strAIAPIKey:
    LogEntry("No AI API key provided, won't be able to use AI",0)
  if strMetricURL and not strMetricToken:
    LogEntry("You provided Metric URL but token is blank, disabling Metric posting",0)
    strMetricURL = None
  LogEntry("URLs before normalization.\nBaseURL: {}\nMetricURL: {}".format(strBaseURL,strMetricURL),2)
  strMetricURL = NormalizeToHttps(strMetricURL)
  strBaseURL = NormalizeToHttps(strBaseURL)
  LogEntry("URLs after normalization.\nBaseURL: '{}'\nMetricURL: '{}'".format(strBaseURL,strMetricURL),2)
  if not strBaseURL:
     LogEntry("Invalid BaseURL, unable to continue",0,True)
  if strMetricURL[:-1] != "/":
     strMetricURL = strMetricURL + "/"

  LogEntry("Unless otherwise noted above, successfully retrieved credentials from {}. ".format(strCredMethod),1)
  if strAction == "IMPORT" or strAction == "FIX":
    LogEntry("Establish a connection to Anthropic API",1)
    objAIClient = Anthropic(api_key=strAIAPIKey)
  else:
     objAIClient = None

  LogEntry("Now loading various lists from WooCommerce to prepare for product updates.",0)
  dictHeader = {}
  strMethod = "get"
  dictGlobalAttributes = LoadDictionaries("/wp-json/wc/v3/products/attributes", strBaseURL, strWCKey, strWCSecret)
  if not dictGlobalAttributes:
     LogEntry("No attributes, aborting",0,True)
  LogEntry("Global Attributes loaded, total {} attributes".format(len(dictGlobalAttributes)),0)
  dictGlobalCategories = LoadDictionaries("/wp-json/wc/v3/products/categories", strBaseURL, strWCKey, strWCSecret)
  LogEntry("Global categories loaded, total {} categories".format(len(dictGlobalCategories)),0)
  dictGlobalTags = LoadDictionaries("/wp-json/wc/v3/products/tags", strBaseURL, strWCKey, strWCSecret)
  LogEntry("Global tags loaded, total {} tags".format(len(dictGlobalTags)),0)
  dictGlobalBrands = LoadDictionaries("/wp-json/wc/v3/products/brands", strBaseURL, strWCKey, strWCSecret)
  LogEntry("Global brands loaded, total {} brands".format(len(dictGlobalBrands)),0)

  if strAction == "IMPORT":
    # The Import action takes place here
    LogEntry("Now starting import action...",0)
    if strImportFile is not None:
      if os.path.isfile(strImportFile):
        lstResults = CreateWooCommerceProductsFromCSV(strImportFile,strBaseURL,strWCKey,strWCSecret,strAIsystem,objAIClient,strAIModel,iMaxTokens,",")
        LogEntry("Finished import, here are the results:",0)
        for strSKU, dictResult in lstResults:
          if dictResult:
            if dictResult[0].get("Success"):
              LogEntry("SKU {} Successful".format(strSKU),0)
            else:
              LogEntry("SKU {} had an issue. Code: {}, error: {}".format(strSKU,dictResult[1][0].get("errcode"),dictResult[1][0].get("errormsg")),0)
          else:
             LogEntry("Something odd is going on. SKU {} does not have a valid result tupple".format(strSKU),0)
      else:
         LogEntry("Import File {} not found, can't do anything".format(strImportFile),0)
    else:
       LogEntry("Import File not defined, nothing to import",0)
    #objLogOut.close()
    #print("objLogOut closed")
    #return

  if strAction == "FIX":
    # here is the fix function initialized
    strFilter = ""
    if strFixStatus is not None:
      strFilter += "status:{}|".format(strFixStatus)
    if strFixTag is not None:
      strFilter += "tag:{}|".format(dictGlobalTags.get(strFixTag.lower(), strFixTag))
    if strFixCategory is not None:
      strFilter += "category:{}|".format(dictGlobalCategories.get(strFixCategory.lower(), strFixCategory))

  if strAction == "AUDIT":
    # Here is the Audit function initialized
    if bTimeStampAudit:
      strOutFileName = strOutDir + "ProdattrAudit_" + time.strftime(strTimeStampFormat) + ".csv"
    else:
      strOutFileName = strOutDir + "ProdattrAudit.csv"
    LogEntry("Starting audit of product descriptions for attributes. Output file is {}".format(strOutFileName),0)
    objFileOut = GetFileHandle(strOutFileName, "w")
    if objFileOut is None or isinstance(objFileOut, str):
      objFileOut = None
      LogEntry("Unable to open output file {}, error: {}".format(strOutFileName, objFileOut),0,True)
    objFileOut.write("Brand,SKU,Name,Descr len,Existing Attribute Count,Description Attributes Count\n")

  # Here is basic prep work for all actions
  iPage = 1
  iProdCount = 5
  iTotalProducts = 0
  strEndPoint = "/wp-json/wc/v3/products"
  dictHeader = {}
  strMethod = "get"
  dictParams = {}
  dictParams["per_page"] = iPerPage
  if strFilter is not None:
     if strFilter.endswith("|"):
        strFilter = strFilter[:-1]
     lstFilters = strFilter.split("|")
     for lstFilter in lstFilters:
        if ":" in lstFilter:
          strFilterKey, strFilterValue = lstFilter.split(":", 1)
          LogEntry("Filtering products with {} of {}".format(strFilterKey, strFilterValue),0)
          dictParams[strFilterKey] = strFilterValue
  if strAction == "UPDATE": # Only update published products
    dictParams["status"] = "publish"
  if strAction == "IMPORT": # For the products we just imported we want to apply attributes as if it was an update
    dictParams = {}
    dictParams["per_page"] = iPerPage
    dictParams["status"] = "pending"

  lstProductFailure = []
  while iProdCount > 0:
    LogEntry("Fetching products, page {} of {}".format(iPage, iTotalPages),1)
    dictParams["page"] = iPage
    strParams = urlLib.urlencode(dictParams)
    strURL = strBaseURL + strEndPoint + "?" + strParams
    dictResponse = MakeAPICall(strURL,dictHeader,strMethod,strUser=strWCKey,strPWD=strWCSecret)
    if dictResponse[0]["Success"]==False:
      LogEntry("API call to WooCommerce failed. {}".format(dictResponse[1]),0,False)
    LogEntry("API call successful, processing response. "
             "{} total products in response, {} total pages".format(iTotal, iTotalPages),1)
    dictProducts = dictResponse[1]
    iProdCount = len(dictProducts)
    iTotalProducts += iProdCount
    LogEntry("Received {} products in page {}. Total products fetched: {}".format(iProdCount, iPage, iTotalProducts),1)
    iPage += 1
    for dictProduct in dictProducts:
      if "description" not in dictProduct or dictProduct["description"] is None:
        LogEntry("Product {} with SKU {} and name {} has no description, skipping.".format(dictProduct["id"],
                                                                dictProduct["sku"], dictProduct["name"]),0)
        continue
      dictAttributes = ExtractTwoColumnTables(dictProduct["description"])
      lstProdAttribs = dictProduct["attributes"] if "attributes" in dictProduct and dictProduct["attributes"] is not None else []
      LogEntry("Working on product {} with SKU {} and name {}. "
               "It has {} existing attributes and {} attributes in the description.".format(
                  dictProduct["id"], dictProduct["sku"], dictProduct["name"],
                  len(lstProdAttribs), len(dictAttributes)),0)
      if strAction == "FIX":
        # Actual fix action
        lstCleanTags = []
        if strFixTag and isinstance(strFixTag,str):
          lstCurTags = dictProduct["tags"]
          for dictTag in lstCurTags:
            if dictTag["name"].lower() != strFixTag.lower():
                lstCleanTags.append(dictTag)
        else:
          lstCleanTags = dictProduct["tags"]
        if len(dictProduct["description"]) < iMaxCharIn:
          strPrompt = dictProduct["name "] + " " + dictProduct["description"]
        else:
          strPrompt = dictProduct["name"]
        dictNewDesc = GenerateProductDescription(strPrompt,strAIsystem,objAIClient,strAIModel,iMaxTokens)
        if not isinstance(dictNewDesc,dict):
           LogEntry("New Description is not a dict, something went wrong with AI generation, "
                    "it returned a {} containing {}".format(type(dictNewDesc),dictNewDesc),0,True)
        strNewDesc = dictNewDesc["description"] if "description" in dictNewDesc else dictProduct["description"]
        strNewName = dictNewDesc["Product_Name"] if "Product_Name" in dictNewDesc else dictProduct["name"]
        strShortDesc = dictNewDesc["short_description"] if "short_description" in dictNewDesc else dictProduct["short_description"]
        dictResult = UpdateWooCommerceProduct({"description": strNewDesc, "name": strNewName,
          "short_description": strShortDesc, "tags": lstCleanTags}, dictProduct["id"], strBaseURL, strWCKey, strWCSecret)
        dictAttributes = ExtractTwoColumnTables(strNewDesc)
        LogEntry("Extracted {} attributes from the new description".format(len(dictAttributes)),1)

      if strAction == "UPDATE" or strAction == "FIX" or strAction == "IMPORT":
        # Here is the real UPDATE work going on. Finding tech specs in description and apply it as an attribute
        bNeedUpdate = False
        for dictKey in dictAttributes.items(): # Loop through the dictionary of specs found in descriiption
          if dictKey[0].strip() in dictAttrEq:
            strKey = dictAttrEq[dictKey[0].strip()]
            LogEntry("Changing attribute {} to {}".format(dictKey[0], strKey),0)
          else:
            strKey = dictKey[0].strip()
          if strKey == "MTBF" or strKey == "LED lifetime":
            lstValue = [dictKey[1]]
          else:
            lstValue = dictKey[1].split(",")
          if strKey.lower()[:28] in dictGlobalAttributes:
            iAttrID = dictGlobalAttributes[strKey.lower()[:28]]
          else:
            LogEntry("Attribute {} not found in global attributes, creating it.".format(strKey),0)
            iAttrID = CreateGlobalAttribute(strKey.strip(), strBaseURL, strWCKey, strWCSecret)
            dictGlobalAttributes[strKey.lower()[:28]] = iAttrID
          AttrFound = AttributeExists(lstProdAttribs, strKey[:28])
          if isinstance(AttrFound,str) and AttrFound == "global":
            LogEntry("Attribute {} already on product as global.".format(strKey),1)
          else:
            if iAttrID is None:
              LogEntry("Failed to create attribute {} on product {}. Skipping this attribute.".format(strKey, dictProduct["id"]),0,False)
              continue
            LogEntry("Attribute {} is not on product, or is local. Need to add {} to attributeID {} ".format(
              strKey, lstValue, iAttrID),1)

            bVariation = False
            if isinstance(AttrFound,dict):
              if AttrFound["variation"]:
                LogEntry("WARNING!! Converted attribute {} used for variation from local to global. "
                           "ID:{} SKU:{} Name:{}".format(strKey, dictProduct["id"], dictProduct["sku"], dictProduct["name"]),0)
              lstProdAttribs.remove(AttrFound)
              bVariation = AttrFound["variation"]

            lstProdAttribs.append({"id": iAttrID, "visible": True, "variation": bVariation, "options": lstValue})
            bNeedUpdate = True

        if bNeedUpdate:
          dictResult = UpdateWooCommerceProduct({"attributes": lstProdAttribs},dictProduct["id"],
                                              strBaseURL, strWCKey, strWCSecret)
          if dictResult[0]["Success"]:
            LogEntry("Successfully updated product {} with new attributes.".format(dictProduct["id"]),0)
          else:
            lstProductFailure.append(dictProduct["id"])
            LogEntry("Failed to update product {} with new attributes. "
                      "Error: {}".format(dictProduct["id"], dictResult[1]),0,False)

      strBrand = "No Brand"
      dictBrands = dictProduct["brands"]
      if isinstance(dictBrands, list):
         if len(dictBrands) > 0:
            strBrand = dictBrands[0]["name"]
      if strAction == "AUDIT":
        # write out the audit file
        objFileOut.write("{},{},{},{},{},{}\n".format(strBrand.strip(), dictProduct["sku"],
            dictProduct["name"].replace(","," ").strip(), len(dictProduct["description"]), len(lstProdAttribs), len(dictAttributes)))
        objFileOut.flush()

  if objFileOut is not None:
    objFileOut.close()

  LogEntry("Finished fetching products. Total products fetched: {}".format(iTotalProducts),0)
  LogEntry("Products that failed to update: {}".format(lstProductFailure),0)

  objLogOut.close()
  print("objLogOut closed")




if __name__ == '__main__':
  main()