'''
Script that analyzes product description in WooCommerce
and turns a spec list into attributes

Author Siggi Bjarnason 21 April 2026
Copyright 2026 Siggi Bjarnason

Following packages need to be installed
pip install requests
pip install sentry_sdk
pip install onepassword-sdk
pip install beautifulsoup4
pip install anthropic
pip install reportlab

'''
# Import libraries
import os
import io
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
import traceback
import platform
import urllib.parse as urlLib
from onepassword import Client, DesktopAuth
from bs4 import BeautifulSoup, Tag
from anthropic import Anthropic
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle, SimpleDocTemplate, HRFlowable, PageBreak, Image, KeepTogether
from PIL import Image as PILImage
from reportlab.lib.units import inch, cm, mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER


# End imports

# Few globals
requests.urllib3.disable_warnings()
tLastCall = 0
iTotalSleep = 0
iTimeOut = 180  # Max time in seconds to wait for network response
iMinQuiet = 10  # Minimum time in seconds between API calls
strDefAIenvName = "ANTHROPIC_API_KEY" # Name of the environment variable where the AI key is stored, if using env variable for auth
strDefAImodel = "claude-sonnet-4-6"
strDefMetricEndPoint = "metrics" # Endpoint to submit metrics to, appended to the ingestion host
strDef1PassTokenEnvVar = "1PASSTOKEN" # Name of the environment variable where the 1Password token is stored, if using token for auth
iDefMaxToken = 2048 # Max tokens to use for the AI calls, can be adjusted based on needs and model limits
iDefPerPage = 25 # Number of items to fetch per page for API calls

dictCurrencySymbols = {
    "EUR": "€",
    "ISK": "kr",
    "SEK": "kr",
    "DKK": "kr",
    "NOK": "kr",
    "GBP": "£",
    "CHF": "CHF",
    "PLN": "zł",
    "CZK": "Kč",
    "HUF": "Ft",
    "RON": "lei",
}

def CustomExcepthook(clsType, objValue, objTraceback):
  strLocation = GetExceptionLocation(objTraceback)
  CleanExit("unhandled exception: {} at {}. Details: {}".format(clsType, strLocation, objValue), bLog=True)
  sys.__excepthook__(clsType, objValue, objTraceback)

sys.excepthook = CustomExcepthook

# sub defs

def GetExceptionLocation(objTraceback:traceback.StackSummary)->str:
  objTB = traceback.extract_tb(objTraceback)

  objMyFrame = None
  for objFrame in reversed(objTB):
    if os.path.basename(objFrame.filename) == strScriptName:
      objMyFrame = objFrame
      break

  if objMyFrame:
    return "line {} in {}".format(objMyFrame.lineno, objMyFrame.name)
  else:
    return "unknown location"

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

def CreateWooCommerceProduct(dictProduct:dict, strBaseURL:str, strWCKey:str, strWCSecret:str):
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
                                     strAIsystem:str, objAIClient:any,strAIModel:str,
                                     iMaxTokens:int, strDelim:str=",")->list:
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
        LogEntry("Processing SKU: {}".format(strSKU),1)
        strProdName = (objRow.get("Name") or "").strip()
        strDescr = (objRow.get("Descr") or "").strip()
        strBackorders = objRow.get("Allow Backorder", "")
        if not strBackorders:
          strBackorders = "no"
        strAllowReviews = objRow.get("Enable Reviews", "")
        if not strAllowReviews:
          strAllowReviews = "false"
        bAllowReviews = strAllowReviews.lower() == "true" or strAllowReviews.lower() == "yes"
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

        LogEntry("Done with basics for SKU {}. Generating product details using AI.".format(strSKU),1)

        strProdDetails = "{} {} {} {}".format(strProdName,strDescr, lstBrandID, strSKU)
        LogEntry("Generated product description for SKU {} with details: {}".format(strSKU, strProdDetails),1)
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

        LogEntry("Creating product",1)

        dictResult = CreateWooCommerceProduct(dictProduct, strBaseURL, strWCKey, strWCSecret)
        lstResults.append((strSKU, dictResult))

    return lstResults

def ExtractTwoColumnTables(strHTML:str)->dict:
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

def ConvertLocalAttributes(lstAttributes:list, strBaseURL:str, strWCKey:str, strWCSecret:str)->tuple:
    """
    Converts local attributes in a list of WooCommerce attributes to global attributes.
    Local attributes are identified by having an "id" of 0 and a "name" that matches a global attribute.
    Parameters:
        lstAttributes (list): A list of attribute dictionaries from WooCommerce
        strBaseURL (str): The base URL for the WooCommerce API
        strWCKey (str): The WooCommerce API key
        strWCSecret (str): The WooCommerce API secret
    Returns:
        tuple: A tuple containing:
          int: The number of local attributes converted
          bool: True if any attributes were changed, False otherwise
          list: A new list of attribute dictionaries where local attributes have been replaced
          with their global counterparts if a match was found, otherwise they are left unchanged.
    """
    lstConverted = []
    iAttrID = None
    bChanged = False
    iCount = 0
    if not isinstance(lstAttributes, list):
      LogEntry("Expected list of attributes, got {} instead".format(type(lstAttributes)), 0, False)
      return 0, False, []
    for dictAttribute in lstAttributes:
      bVariation = dictAttribute.get("variation", False)
      if dictAttribute.get("id") == 0:
        if bVariation:
          LogEntry("Attribute {} is used for variation, skipping conversion.".format(dictAttribute.get("name", "")), 0)
          continue
        strAttrName = dictAttribute.get("name", "").strip().lower()
        if strAttrName == "type" or strAttrName == "category":
          continue
        if strAttrName in dictGlobalAttributes:
          iAttrID = dictGlobalAttributes[strAttrName]
        else:
          LogEntry("Attribute {} not found in global attributes, creating it.".format(strAttrName),0)
          iAttrID = CreateGlobalAttribute(strAttrName, strBaseURL, strWCKey, strWCSecret)
          dictGlobalAttributes[strAttrName] = iAttrID
        if iAttrID is None:
          LogEntry("Failed to create global attribute for local attribute {}. Skipping conversion.".format(strAttrName), 0, False)
          lstConverted.append(dictAttribute)
        else:
          bChanged = True
          iCount += 1
          dictGlobalAttr = {}
          dictGlobalAttr["id"] = iAttrID
          dictGlobalAttr["name"] = dictAttribute.get("name", "")
          dictGlobalAttr["options"] = dictAttribute.get("options", [])
          dictGlobalAttr["variation"] = bVariation
          dictGlobalAttr["visible"] = dictAttribute.get("visible", True)
          lstConverted.append(dictGlobalAttr)
          if bVariation:
            LogEntry("WARNING!! Converted attribute {} used for variation from local to global with ID {}.".format(strAttrName, dictGlobalAttributes[strAttrName]), 0, False)
          else:
            LogEntry("Converted local attribute '{}' to global with ID {}".format(strAttrName, dictGlobalAttributes[strAttrName]), 0, False)
      else:
        lstConverted.append(dictAttribute)


    return iCount, bChanged, lstConverted

def countLocalAttributes(lstAttributes:list)->int:
    """
    Counts how many local attributes are in a list of WooCommerce attributes.
    Local attributes are identified by having an "id" of 0.
    Parameters:
        lstAttributes (list): A list of attribute dictionaries from WooCommerce
    Returns:
        int: The number of local attributes in the list
    """
    intCount = 0
    if not isinstance(lstAttributes, list):
      LogEntry("Expected list of attributes, got {} instead".format(type(lstAttributes)), 0, False)
      return -1
    for dictAttribute in lstAttributes:
        if dictAttribute.get("id") == 0:
            intCount += 1
    return intCount

def AttributeExists(listAttributeCollection:list, strSearchName:str)->str|bool:
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

def CreateGlobalAttribute(strAttributeName:str, strBaseURL:str, strWCKey:str, strWCSecret:str):
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

def CreateBrand(strBrandName:str, strBaseURL:str, strWCKey:str, strWCSecret:str)->int|None:
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

def UpdateWooCommerceProduct(dictProduct:dict, iProductID:int, strBaseURL:str, strWCKey:str, strWCSecret:str)->tuple:
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

def LoadTaxDetails(strBaseURL:str, strWCKey:str, strWCSecret:str)->tuple:
  LogEntry("Loading tax details",1)
  dictHeader = {}
  strMethod = "get"
  strEndPoint = "/wp-json/wc/v3/taxes"
  strURL = strBaseURL + strEndPoint
  dictResponse = MakeAPICall(strURL,dictHeader,strMethod,strUser=strWCKey,strPWD=strWCSecret)
  if dictResponse[0]["Success"]==False:
    LogEntry("API call to WooCommerce endpoint {} failed. {}".format(strEndPoint, dictResponse[1]),0,False)
    return{}
  lstTaxes = dictResponse[1]
  strEndPoint = "/wp-json/wc/v3/settings/general"
  strURL = strBaseURL + strEndPoint
  dictResponse = MakeAPICall(strURL,dictHeader,strMethod,strUser=strWCKey,strPWD=strWCSecret)
  if dictResponse[0]["Success"]==False:
    LogEntry("API call to WooCommerce endpoint {} failed. {}".format(strEndPoint, dictResponse[1]),0,False)
    return{}
  lstSettings = dictResponse[1]
  for dictSetting in lstSettings:
    if dictSetting.get("id") == "woocommerce_default_country":
      strBaseCountry = dictSetting.get("value").split(":")[0]
    elif dictSetting.get("id") == "woocommerce_currency":
      strCurrency = dictSetting.get("value")
    elif dictSetting.get("id") == "woocommerce_currency_pos":
      strCurrencyPos = dictSetting.get("value")
    elif dictSetting.get("id") == "woocommerce_price_num_decimals":
      strPriceNumDecimals = int(dictSetting.get("value"))

  strEndPoint = "/wp-json/wc/v3/settings/tax"
  strURL = strBaseURL + strEndPoint
  dictResponse = MakeAPICall(strURL,dictHeader,strMethod,strUser=strWCKey,strPWD=strWCSecret)
  if dictResponse[0]["Success"]==False:
    LogEntry("API call to WooCommerce endpoint {} failed. {}".format(strEndPoint, dictResponse[1]),0,False)
    return{}
  lstSettings = dictResponse[1]
  for dictSetting in lstSettings:
    if dictSetting.get("id") == "woocommerce_prices_include_tax":
      strPricesIncludeTax = dictSetting.get("value")
      break
  return lstTaxes, strBaseCountry, strPricesIncludeTax, strCurrency, strCurrencyPos, strPriceNumDecimals

def LoadDictionaries(strEndPoint:str, strBaseURL:str, strWCKey:str, strWCSecret:str)->dict:
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

def CreateIncident(strName:str, strSummary:str, strDetails:str)->dict:
  """
  Create an incident in Incident system with the provided summary and details.
  Parameters:
    strName (str): The name of the incident to be created
    strSummary (str): The summary of the incident to be created
    strDetails (str): The detailed description of the incident

  Returns:
    dict: A dictionary containing the response from the incident API, or error details in case of failure
  """
  dictHeader = {}
  dictHeader["Authorization"] = "Bearer {}".format(strBSKey)
  dictHeader["Content-Type"] = "application/json"
  dictPayload = {}
  dictPayload["name"] = strName
  dictPayload["summary"] = strSummary
  dictPayload["requester_email"] = "{}@{}".format(strScriptName, strScriptHost)
  dictPayload["description"] = strDetails
  strMethod = "post"

  LogEntry("Creating incident with title: {}".format(strSummary), 1)
  WebResponse = MakeAPICall(strIncidentURL, dictHeader, strMethod, dictPayload)
  return WebResponse

def GetEnvCreds(dictCollectionIn:dict)->dict:
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

async def get1PasswordItems(dictItemCollection:dict, strAccountName:str|None=None, strToken:str|None=None)->dict:
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
  if not strAccountName and not strToken:
    return {"fatal error":
            {"error message":"neither token nor 1Password account name provided. Unable to authenticate via 1Password."}}
  if strToken is not None:
    LogEntry("Using token-based authentication.",1)
    try:
      objClient = await Client.authenticate(auth=strToken,
      integration_name=strScriptName,
      integration_version=strVersion,)
    except Exception as e:
      LogEntry("Failed to authenticate with 1Password using the provided token. Error: {}".format(e),0,False)
      LogEntry("Attempting Desktop Authentication as fallback. ",0,False)
      try:
        objClient = await Client.authenticate(
              auth=DesktopAuth(account_name=strAccountName),
              integration_name=strScriptName,
              integration_version=strVersion,)
      except Exception as e:
        return {"fatal error": {"error message": "Failed to authenticate with 1Password using both token and DesktopAuth. "
                                  "Error: {}".format(e)}}
  else:
    # Connects to the 1Password desktop app.
    LogEntry("No token provided. Using DesktopAuth for authentication. ",1)
    try:
      objClient = await Client.authenticate(
            auth=DesktopAuth(account_name=strAccountName),
            integration_name=strScriptName,
            integration_version=strVersion,)
    except Exception as e:
      return {"fatal error": {"error message": "Failed to authenticate with 1Password using both token and DesktopAuth. Error: {}".format(e)}}

  LogEntry("Connected to 1Password",1)

  dictCollection = {}
  for key, item_spec in dictItemCollection.items():
    strVaultID = item_spec["vault_id"]
    strItemID = item_spec["item_id"]
    try:
      objItem = await objClient.items.get(strVaultID, strItemID)
    except Exception as e:
      return {"fatal error": {"error message": "Failed to retrieve item {0} from vault {1}. {2}".format(strItemID, strVaultID, e)}}
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

  del objItem
  del objClient

  return dictCollection

def CleanExit(strCause:str,bLog=True):
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

  if strHeartBeatURL:
    WebResponse = MakeAPICall(strHeartBeatURL+"/"+strExitCode,{},"HEAD",objData=strCause)
    LogEntry("Heartbeat posted. Response was: {}".format(WebResponse))

  if strBSKey and strIncidentURL:
    WebResponse = CreateIncident("Script Failure", "Error in {} on {}".format(strScriptName, strScriptHost), "Script {} on host {} is exiting abnormally due to: {}".format(strScriptName, strScriptHost, strCause))
    LogEntry("BetterStack incident creation response: {}".format(WebResponse))

  if objFileOut is not None:
    objFileOut.close()
    LogEntry("objFileOut closed", 1)

  sentry_sdk.flush()
  sentry_sdk.init(dsn=None) # This is to ensure that the Sentry SDK is properly closed and all events are flushed before exiting.

  objLogOut.close()
  print("Log file {} closed".format(strLogFile))

  sys.exit(9)

def LogEntry(strMsg:str, iMsgLevel:int=0, bAbort:bool=False):
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

def isNum(CheckValue:any)->bool:
  """
  function to safely check if a value can be interpreded as a number
  (integer or float) without throwing an error.
  Parameter:
    Value: A object to be evaluated
  Returns:
    Boolean indicating if the object is a valid number or not.
  """
  if isinstance(CheckValue,bool):
    return False
  if not isinstance(CheckValue, (float, int, str)):
    return False
  try:
      float(CheckValue)
      return True
  except ValueError:
      return False

def StripHTML(strHTML:str)->str:
  """
  This function takes a string containing HTML content
  and returns a plain text version of it by removing all HTML tags.
  Also strips out all script, style and other such non-text sections.
  Parameters:
      strHTML (str): The input string containing HTML content.
  Returns:
      str: The plain text version of the input string.
  """
  objSoup = BeautifulSoup(strHTML, "html.parser")

  for objTag in objSoup(["script", "style","noscript", "iframe", "template", "iframe", "object", "embed"]):
      objTag.decompose()

  strText = objSoup.get_text(separator=" ", strip=True)
  return strText

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

def GetFileHandle(strFileName:str, strperm:str)->object:
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

def FetchEnv(strVarName:str)->str|None:
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

def Convert2OpenMetricGauge(dictPayloads:dict)->list:
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

def MakeAPICall(strURL:str, dictHeader:dict, strMethod:str, dictPayload:dict="",
                objFiles:list=[], objData=None, strUser:str="", strPWD:str="")->tuple:
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
                                    headers=dictHeader, data=objData)
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

def ParseHtmlToFlowables(objParent):
  lstFlowables = []
  if isinstance(objParent, str):
    objParent = BeautifulSoup(objParent, features="html.parser")

  for objTag in objParent.children:
    if not isinstance(objTag, Tag):
      continue

    strTag = objTag.name.lower()

    if strTag == "div":
      lstFlowables.extend(ParseHtmlToFlowables(objTag))
      continue

    if strTag in ("h1", "h2", "h3", "h4", "h5", "h6"):
      strStyleName = "Heading{}".format(strTag[1])
      lstFlowables.append(Paragraph(objTag.get_text(), objStyles[strStyleName]))
      lstFlowables.append(Spacer(1, fSpaceAfterHeader * fUnit))

    elif strTag == "p":
      for objChild in objTag.find_all(True):
        if objChild.name == "img":
          objChild.decompose()
        else:
          objChild.attrs = {}
      lstFlowables.append(Paragraph(objTag.decode_contents(), objStyles["Normal"]))
      lstFlowables.append(Spacer(1, fSpaceAfterParagraph * fUnit))

    elif strTag in ("ul", "ol"):
      iCount = 1
      for objItem in objTag.find_all("li", recursive=False):
        if strTag == "ol":
          strBullet = "{}. {}".format(iCount, objItem.get_text())
          iCount = iCount + 1
        else:
          strBullet = "• {}".format(objItem.get_text())
        lstFlowables.append(Paragraph(strBullet, objStyles["Normal"]))
      lstFlowables.append(Spacer(1, fSpaceAfterParagraph * fUnit))

    elif strTag == "table":
      lstRows = []
      for objRow in objTag.find_all("tr"):
        lstCells = []
        for objCell in objRow.find_all(["td", "th"]):
          lstCells.append(Paragraph(objCell.get_text(strip=True), objStyles["Normal"]))
        lstRows.append(lstCells)

      if isinstance(lstRows, list):
        if len(lstRows) > 2:
          iNumCols = len(lstRows[2])
        elif len(lstRows) > 1:
          iNumCols = len(lstRows[1])
        else:
          iNumCols = len(lstRows[0])
        fColWidth = fPageWidth / iNumCols
        LogEntry("debug: iNumCols={} fColWidth={} fAvailWidth={}".format(iNumCols, fColWidth, fPageWidth),4)
        lstColWidths = []
        for i in range(iNumCols):
          lstColWidths.append(fColWidth)
        objTable = Table(lstRows, colWidths=lstColWidths)
        objTable.setStyle(TableStyle([
          ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
          ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
          ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        lstFlowables.append(objTable)
        lstFlowables.append(Spacer(1, fSpaceAfterParagraph * fUnit))

  return lstFlowables

def GetProductTaxRate(lstTaxes, strBaseCountry, strTaxClass):
  if strTaxClass == "":
    strTaxClass = "standard"

  for dctTax in lstTaxes:
    if dctTax.get("country") == strBaseCountry and dctTax.get("class") == strTaxClass:
      return float(dctTax.get("rate"))

  return None

def DrawFooter(objCanvas, objDoc):
  objCanvas.saveState()
  objCanvas.setFont("Helvetica", 10)
  strFooter = "{}  {} | All prices are in {} and include Icelandic VAT".format(strCompanyName, strContactEmail, strCurrency)
  objCanvas.drawCentredString(tPageSize[0] / 2, 15 * fUnit, strFooter)
  objCanvas.drawCentredString(tPageSize[0] / 2, 10 * fUnit, "Page {}".format(objDoc.page))
  objCanvas.restoreState()

def FetchImageBuffer(strUrl):
  objResponse = requests.get(strUrl, timeout=10)
  if objResponse.status_code != 200:
    return None
  return io.BytesIO(objResponse.content)

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
  global strVersion
  global strLogFile
  global strHeartBeatURL
  global strBSKey
  global strIncidentURL
  global strExitCode
  global fUnit
  global tPageSize
  global fSpaceAfterHeader
  global fSpaceAfterParagraph
  global fSpaceAfterSection
  global objStyles
  global objCenteredNormal
  global objCenteredH1
  global objCenteredH2
  global strCompanyName
  global strContactEmail
  global fPageWidth
  global strCurrency

  objStyles = getSampleStyleSheet()
  objStyles["Title"].fontSize = 48
  objStyles["Title"].leading = objStyles["Title"].fontSize * 1.5
  objStyles["Title"].spaceAfter = objStyles["Title"].fontSize * 0.5
  objStyles["Title"].spaceBefore = objStyles["Title"].fontSize * 0.8
  objCenteredNormal = ParagraphStyle(name="Centered", parent=objStyles["Normal"], alignment=TA_CENTER)
  objCenteredH1 = ParagraphStyle(name="CenteredH1", parent=objStyles["Heading1"], alignment=TA_CENTER)
  objCenteredH2 = ParagraphStyle(name="CenteredH2", parent=objStyles["Heading2"], alignment=TA_CENTER)
  objCenteredH3 = ParagraphStyle(name="CenteredH3", parent=objStyles["Heading3"], alignment=TA_CENTER)

  fUnit = mm
  tPageSize = A4
  fSpaceAfterHeader = 2.0
  fSpaceAfterParagraph = 3.0
  fSpaceAfterSection = 6.0
  strCompanyName = ""
  strContactEmail = ""
  strCurrency = ""
  strPreamble = "This will be introductory text, such as instructions, contact info, etc. It can be left blank if not needed."

  dictProxies = {}
  strOutDir = None
  objFileOut = None
  strAccountName = None
  strMikrotikToken = None
  strHeartBeatURL = None
  strBSKey = None
  strIncidentURL = None
  strExitCode = "fail"

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
  objParser.add_argument("--mikrotik", dest="mikrotik",
                      action="store_true", help="Action directive. Update stock level with Mikrotik."
                      "Required unless you specify another action, only one action can be specified.")
  objParser.add_argument("--convert", dest="convert",
                      action="store_true", help="Action directive. Convert local attributes to global ones."
                      "Required unless you specify another action, only one action can be specified.")
  objParser.add_argument("--export", dest="export", action="store_true",
                         help="Action directive. Export all products to a CSV file and/or PDF based on config, "
                         "no updates will be made. Required unless you specify another action, only one action can be specified.")
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
  print ("Logging to file: {}".format(strLogFile))
  objLogOut = GetFileHandle(strLogFile, "w")
  if isinstance(objLogOut, str):
    print("Unable to open log file {}, error was: {}, aborting".format(strLogFile, objLogOut))
    sys.exit(9)
  strScriptHost = platform.node().upper()
  bQuiet = objArgs.silent
  bExport = objArgs.export
  bAudit = objArgs.audit
  bUpdate = objArgs.update
  bImport = objArgs.prodimport
  bFix = objArgs.fix
  bMikrotik = objArgs.mikrotik
  bConvert = objArgs.convert
  LogEntry("This is a script to parse WooCommerce product description for specifications "
           "and create product attributes from it. Can also import new products "
           "and rewrite product descriptions.\n"
          "This is running under Python Version {}".format(strVersion),0)
  LogEntry("Running from: {}".format(strRealPath),0)
  dtNow = time.strftime("%A %d %B %Y %H:%M:%S %Z")
  LogEntry("The script started at {}".format(dtNow),0)
  LogEntry("Verbosity is set to {}".format(iVerbose),1)

  # Validate that only one action is specified
  iActionCount = sum([bAudit, bUpdate, bImport, bFix, bMikrotik, bExport, bConvert])
  if iActionCount > 1:
    LogEntry("Error: More than one action directive specified. "
             "Only one of --audit, --update, --import, --fix, --mikrotik, --export or --convert can be used.",0)
    iActionCount = 0
  if iActionCount == 0:
    strAction = input("Please specify action, one of AUDIT, UPDATE, IMPORT, FIX, CONVERT, EXPORT or MIKROTIK: ")
    strAction = strAction.upper()
    if strAction not in ["AUDIT", "UPDATE", "IMPORT", "FIX", "CONVERT", "EXPORT", "MIKROTIK"]:
      LogEntry("Invalid action directive '{}', aborting".format(strAction),0,True)
  if iActionCount == 1:
    # Determine and set the action string
    if bAudit:
      strAction = "AUDIT"
    elif bUpdate:
      strAction = "UPDATE"
    elif bImport:
      strAction = "IMPORT"
    elif bFix:
      strAction = "FIX"
    elif bMikrotik:
      strAction = "MIKROTIK"
    elif bConvert:
      strAction = "CONVERT"
    elif bExport:
      strAction = "EXPORT"
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
  if not os.path.isfile(strConfile):
    LogEntry ("Can't find configuration file {}, aborting".format(strConfile),0,True)

  try:
    objConFileHndl = GetFileHandle(strConfile, "r")
    if isinstance(objConFileHndl, str):
      LogEntry("Unable to open configuration file {}, error was: {}, aborting".format(strConfile, objConFileHndl),0,True)
    objConfig = configparser.ConfigParser()
    objConfig.read_file(objConFileHndl)
    objConFileHndl.close()
  except Exception as e:
    LogEntry("Error occurred while reading configuration file: {}".format(str(e)),0,True)

  strSentryDSN = objConfig.get("Generic", "SentryDSN", fallback="") or FetchEnv("SENTRY_DSN")
  LogEntry("Sentry DSN is set: {}".format(bool(strSentryDSN)), 0)

  sentry_sdk.init(
      dsn=strSentryDSN,
      # Set traces_sample_rate to 1.0 to capture 100%
      # of transactions for performance monitoring.
      traces_sample_rate=1.0,
  )

  #sentry_sdk.capture_message("Test message from {}".format(strScriptName))
  strExitCode = objConfig.get("Generic", "FailureCode", fallback="")
  strExportFile = objConfig.get("Report Export", "ReportFileName", fallback="ProductCatalog").strip()
  strContactEmail = objConfig.get("Report Export", "ContactEmail", fallback="").strip()
  strImgSize = objConfig.get("Report Export", "ProdImgSize", fallback="0.8")
  if isNum(strImgSize):
    fImgSize = float(strImgSize)
  else:
    LogEntry("ProdImgSize value in config ({}) is not a number, defaulting to 0.8".format(strImgSize),0)
    fImgSize = 0.8
  strPriceAdjust = objConfig.get("Report Export", "ExportPriceAdjust", fallback="0")
  if isNum(strPriceAdjust):
    fPriceAdjust = float(strPriceAdjust)
  else:
    LogEntry("ExportPriceAdjust value in config ({}) is not a number, defaulting to 0".format(strPriceAdjust),0)
    fPriceAdjust = 0.0
  strExportTypes = objConfig.get("Report Export", "ExportTypes", fallback="csv,pdf").lower()
  strExportTypes = strExportTypes.replace(" ","")
  lstExportTypes = strExportTypes.split(",")
  strUnits = objConfig.get("Report Export", "Units", fallback="mm").lower()
  if strUnits not in ["mm", "cm", "in"]:
    LogEntry("Invalid Units specified in config. Must be 'mm', 'cm', or 'in', case insensitive. Defaulting to 'mm'.",0)
    strUnits = "mm"
  if strUnits == "mm":
    fUnit=mm
  elif strUnits == "cm":
    fUnit=cm
  else:
    fUnit=inch
  strPDFPageSize = objConfig.get("Report Export", "PDFPageSize", fallback="A4").upper()
  if strPDFPageSize not in ["A4", "LETTER"]:
    LogEntry("Invalid PDFPageSize specified in config. Must be 'A4' or 'letter', case insensitive. Defaulting to 'A4'.",0)
    strPDFPageSize = "A4"
  if strPDFPageSize == "A4":
    tPageSize = A4
  else:
    tPageSize = letter
  strPDFMargins = objConfig.get("Report Export", "PDFMargins", fallback="10,10,10,10")
  strPDFMargins = strPDFMargins.replace(" ","")
  lstPDFMargins = strPDFMargins.split(",")
  if len(lstPDFMargins) != 4 or not all(isNum(margin) for margin in lstPDFMargins):
    LogEntry("Invalid PDFMargins specified in config. Must be four numbers separated by commas, case insensitive. Defaulting to 10,10,10,10.",0)
    lstPDFMargins = [10,10,10,10]
  else:
    lstPDFMargins = [float(margin) for margin in lstPDFMargins]
  strSpaceAfterHeader = objConfig.get("Report Export", "AfterHeader", fallback="2")
  if isNum(strSpaceAfterHeader):
    fSpaceAfterHeader = float(strSpaceAfterHeader)
  else:
    LogEntry("AfterHeader value in config ({}) is not a number, defaulting to 2".format(strSpaceAfterHeader),0)
    fSpaceAfterHeader = 2.0
  strSpaceAfterParagraph = objConfig.get("Report Export", "AfterParagraph", fallback="3")
  if isNum(strSpaceAfterParagraph):
    fSpaceAfterParagraph = float(strSpaceAfterParagraph)
  else:
    LogEntry("AfterParagraph value in config ({}) is not a number, defaulting to 3".format(strSpaceAfterParagraph),0)
    fSpaceAfterParagraph = 3.0
  strSpaceAfterSection = objConfig.get("Report Export", "AfterSection", fallback="6")
  if isNum(strSpaceAfterSection):
    fSpaceAfterSection = float(strSpaceAfterSection)
  else:
    LogEntry("AfterSection value in config ({}) is not a number, defaulting to 6".format(strSpaceAfterSection),0)
    fSpaceAfterSection = 6.0

  strLogoFilePath = objConfig.get("Report Export", "CompanyLogo", fallback="")
  LogEntry("LogoPath is set to: {}".format(strLogoFilePath), 0)
  if strLogoFilePath != "" and not os.path.isfile(strLogoFilePath):
    LogEntry("LogoPath specified in config does not exist, ignoring it.",0)
    strLogoFilePath = ""
  strCompanyName = objConfig.get("Report Export", "CompanyName", fallback="Nameless Company")
  if strCompanyName == "":
    LogEntry("CompanyName not specified in config, so report will be nameless",0)
  strAddress = objConfig.get("Report Export", "Address", fallback="")
  strAddress = strAddress.replace("\n", "<br/>")
  strLogoSize = objConfig.get("Report Export", "LogoSize", fallback="50")
  if isNum(strLogoSize):
    fLogoSize = float(strLogoSize)
  else:
    LogEntry("LogoSize value in config ({}) is not a number, defaulting to 50".format(strLogoSize),0)
    fLogoSize = 50.0
  fPageWidth = tPageSize[0] - ((lstPDFMargins[0] + lstPDFMargins[1]) * fUnit)

  strPreambleFile = objConfig.get("Report Export", "PreambleFilePath", fallback="")
  if strPreambleFile != "" and os.path.isfile(strPreambleFile):
    try:
      with open(strPreambleFile, "r", encoding="utf-8") as f:
        strPreamble = f.read()
    except Exception as e:
      LogEntry("Error reading PreambleFile '{}': {}, using default preamble.".format(strPreambleFile, e),0,True)
  strPreamble = strPreamble.replace("\n", "<br/>")

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
    if "IncidentURL" in objConfig["Generic"]:
      strIncidentURL = objConfig["Generic"]["IncidentURL"]
    else:
      strIncidentURL = None
    if "HeartBeatURL" in objConfig["Generic"]:
      strHeartBeatURL = objConfig["Generic"]["HeartBeatURL"]
    else:
      strHeartBeatURL = None
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
      if isNum(strMaxCharIn):
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
      if isNum(objConfig["Generic"]["PerPage"]):
        iPerPage = int(objConfig["Generic"]["PerPage"])
      else:
        LogEntry("PerPage value in config is not an integer, defaulting to {}".format(iDefPerPage),0)
        iPerPage = iDefPerPage
  else:
    LogEntry("section Generic not found in config",0)

  if "UptimeCreds" in objConfig:
    if strAuthMethod == "1pa":
      if "VaultID" in objConfig["UptimeCreds"]:
        strBSVaultID = objConfig["UptimeCreds"]["VaultID"]
      else:
        LogEntry("UptimeCreds VaultID not found in config",0)
        strBSVaultID = None
      if "ItemID" in objConfig["UptimeCreds"]:
        strBSItemID = objConfig["UptimeCreds"]["ItemID"]
      else:
        LogEntry("UptimeCreds ItemID not found in config",0)
        strBSItemID = None
    if "TokenField" in objConfig["UptimeCreds"]:
      strBSKeyField = objConfig["UptimeCreds"]["TokenField"]
    else:
      LogEntry("UptimeCreds TokenField not found in config",0)
      strBSKeyField = None
  else:
    LogEntry("section UptimeCreds not found in config",0)

  if "MikrotikCreds" in objConfig:
    if strAuthMethod == "1pa":
      if "VaultID" in objConfig["MikrotikCreds"]:
        strMTVaultID = objConfig["MikrotikCreds"]["VaultID"]
      else:
        LogEntry("MikroTik VaultID not found in config",0)
        strMTVaultID = None
      if "ItemID" in objConfig["MikrotikCreds"]:
        strMTItemID = objConfig["MikrotikCreds"]["ItemID"]
      else:
        LogEntry("MikroTik ItemID not found in config",0)
        strMTItemID = None
    if "TokenField" in objConfig["MikrotikCreds"]:
      strMTAPIKeyField = objConfig["MikrotikCreds"]["TokenField"]
    else:
      LogEntry("MikroTik TokenField not found in config",0)
      strMTAPIKeyField = None
    if "HostField" in objConfig["MikrotikCreds"]:
      strMTURLField = objConfig["MikrotikCreds"]["HostField"]
    else:
      LogEntry("MikroTik HostField not found in config",0)
      strMTURLField = None
  else:
    LogEntry("section MikrotikCreds not found in config",0)

  if "AICreds" in objConfig:
    if strAuthMethod == "1pa":
      if "VaultID" in objConfig["AICreds"]:
        strAIVaultID = objConfig["AICreds"]["VaultID"]
      else:
        LogEntry("AI VaultID not found in config",0)
        strAIVaultID = None
      if "ItemID" in objConfig["AICreds"]:
        strAIItemID = objConfig["AICreds"]["ItemID"]
      else:
        LogEntry("AI ItemID not found in config",0)
        strAIItemID = None
    if "APIKeyField" in objConfig["AICreds"]:
      strAIAPIKeyField = objConfig["AICreds"]["APIKeyField"]
    else:
      LogEntry("AI APIKeyField not found in config, setting default to {}".format(strDefAIenvName),0)
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
        LogEntry("WP VaultID not found in config",0)
      if "ItemID" in objConfig["WPCreds"]:
        strItemID = objConfig["WPCreds"]["ItemID"]
      else:
        LogEntry("WP ItemID not found in config",0)
    if "ConsumerKeyField" in objConfig["WPCreds"]:
      strConsumerKeyField = objConfig["WPCreds"]["ConsumerKeyField"]
    else:
      LogEntry("WP ConsumerKeyField not found in config",0)
    if "ConsumerSecretField" in objConfig["WPCreds"]:
      strConsumerSecretField = objConfig["WPCreds"]["ConsumerSecretField"]
    else:
      LogEntry("WP ConsumerSecretField not found in config",0)
      strConsumerSecretField = None
    if "BaseURLField" in objConfig["WPCreds"]:
      strBaseURLField = objConfig["WPCreds"]["BaseURLField"]
    else:
      LogEntry("WP BaseURLField not found in config",0)
      strBaseURLField = None
  else:
    LogEntry("section WPCreds not found in config",0)

  if objArgs.input is not None:
    strImportFile = objArgs.input

  if strAIsystemFile is None:
    LogEntry("Please provide a path to a text file providing context for AI Calls, the file can be blank if you want. "
              "Put it in the general section of the config file as 'AIBackgroundFile = system.txt' "
              "assuming the file is called system.txt and is in the script directory.",0,True)
  else:
    if os.path.isfile(strAIsystemFile):
      LogEntry("AI System file appears good",1)
      objAISystem = GetFileHandle(strAIsystemFile,"r")
      if isinstance(objAISystem, str):
        LogEntry("Unable to open AI system file {}, error was: {}, aborting".format(strAIsystemFile, objAISystem),0,True)
      strAIsystem = objAISystem.read()
      objAISystem.close()
    else:
      LogEntry("AI system file {} specified but not found, please correct before proceeding.".format(strAttrEqFile),0,True)

  if strAttrEqFile is not None:
    if os.path.isfile(strAttrEqFile):
      LogEntry("Attribute equivalence file {} found, processing.".format(strAttrEqFile),1)
      objAttrEqFileHndl = GetFileHandle(strAttrEqFile, "r")
      if isinstance(objAttrEqFileHndl, str):
        LogEntry("Unable to open attribute equivalence file {}, error was: {}, aborting".format(strAttrEqFile, objAttrEqFileHndl),0,True)
      dictAttrEq = {}
      for strLine in objAttrEqFileHndl:
        if ";" in strLine:
          strKey, strValue = strLine.split(";", 1)
          dictAttrEq[strKey.strip()] = strValue.strip()
      objAttrEqFileHndl.close()
    else:
      LogEntry("Attribute equivalence file {} specified but not found, ignoring.".format(strAttrEqFile),0)
  if not isNum(iMaxTokens):
    LogEntry("MaxToken value of '{}' is not valid. Setting it to the default of {}".format(iMaxTokens,iDefMaxToken),0)
    iMaxTokens = iDefMaxToken
  else:
     iMaxTokens = int(iMaxTokens)
  if strMetricURL and not strMetricTokenField:
     LogEntry("You provided Metric URL but didn't specify where to find the token, disabling Metric posting",0)
     strMetricURL = None

  if not strAccountName and strAuthMethod == "1pa":
     LogEntry("Auth method is 1Password but 1Password account name not specified, can't proceed",0,True)

  if strHeartBeatURL.endswith("/"):
    strHeartBeatURL = strHeartBeatURL[:-1]
  if strIncidentURL.endswith("/"):
    strIncidentURL = strIncidentURL[:-1]

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
    dictItemSpecs["vault_id"] = strBSVaultID
    dictItemSpecs["item_id"] = strBSItemID
    dictItemCollection["UptimeCreds"] = dictItemSpecs
    dictItemSpecs = {}
    dictItemSpecs["vault_id"] = strAIVaultID
    dictItemSpecs["item_id"] = strAIItemID
    dictItemSpecs["metric_key"] = strMetricTokenField
    dictItemCollection["AICreds"] = dictItemSpecs
    dictItemSpecs = {}
    dictItemSpecs["vault_id"] = strMTVaultID
    dictItemSpecs["item_id"] = strMTItemID
    dictItemSpecs["metric_key"] = strMetricTokenField
    dictItemSpecs["HostField"] = strMTURLField
    dictItemCollection["MikrotikCreds"] = dictItemSpecs

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
    dictItemSpecs = {}
    dictItemSpecs["MTSecret"] = strMTAPIKeyField
    dictItemSpecs["HostField"] = strMTURLField
    dictItemCollection["MikrotikCreds"] = dictItemSpecs
    dictItemSpecs = {}
    dictItemSpecs["BSkey"] = strBSKeyField
    dictItemCollection["UptimeCreds"] = dictItemSpecs

    dictReturn = GetEnvCreds(dictItemCollection)

  if not dictReturn or "WCreds" not in dictReturn or "AICreds" not in dictReturn:
    LogEntry("Failed to retrieve credentials from environment variables.",0,True)

  strBaseURL = dictReturn["WCreds"].get(strBaseURLField)
  strWCKey = dictReturn["WCreds"].get(strConsumerKeyField)
  strWCSecret = dictReturn["WCreds"].get(strConsumerSecretField)
  strAIAPIKey = dictReturn["AICreds"].get(strAIAPIKeyField)
  strMetricToken = dictReturn["AICreds"].get(strMetricTokenField)
  strMikrotikToken = dictReturn["MikrotikCreds"].get(strMTAPIKeyField)
  strMikroTikURL = dictReturn["MikrotikCreds"].get(strMTURLField)
  strBSKey = dictReturn["UptimeCreds"].get(strBSKeyField)

  LogEntry("Credentials retrieved. Validating critical credentials and normalizing URLs.",1)

  if not strBaseURL or not strWCKey or not strWCSecret:
    LogEntry("No URL Consumer Key or Secret, unable to proceed.",0,True)
  if not strAIAPIKey:
    LogEntry("No AI API key provided, won't be able to use AI",0)
  if strMetricURL and not strMetricToken:
    LogEntry("You provided Metric URL but token is blank, disabling Metric posting",0)
    strMetricURL = None
  LogEntry("URLs before normalization.\nBaseURL: {}\nMetricURL: {}\nMikroTikURL: {}".format(strBaseURL,strMetricURL,strMikroTikURL),1)
  strMetricURL = NormalizeToHttps(strMetricURL)
  strBaseURL = NormalizeToHttps(strBaseURL)
  strMikroTikURL = NormalizeToHttps(strMikroTikURL)
  strIncidentURL = NormalizeToHttps(strIncidentURL)
  LogEntry("URLs after normalization.\nBaseURL: '{}'\nMetricURL: '{}'\nMikroTikURL: '{}'".format(strBaseURL,strMetricURL,strMikroTikURL),1)
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
  lstTaxes, strBaseCountry, strPricesIncludeTax, strCurrency, strCurrencyPos, strPriceNumDecimals = LoadTaxDetails(strBaseURL, strWCKey, strWCSecret)
  LogEntry("Tax details loaded. Base country: {}, prices include tax: {}, total tax classes: {}, currency: {}, currency position: {}, "
           "price decimal places: {}".format(strBaseCountry, strPricesIncludeTax, len(lstTaxes), strCurrency, strCurrencyPos, strPriceNumDecimals),0)
  strCurrencySymbol = dictCurrencySymbols.get(strCurrency, strCurrency)
  if strAction == "IMPORT":
    # The Import action takes place here
    LogEntry("Now starting import action...",0)
    if strImportFile is not None:
      if os.path.isfile(strImportFile):
        lstResults = CreateWooCommerceProductsFromCSV(strImportFile,strBaseURL,strWCKey,strWCSecret,strAIsystem,objAIClient,strAIModel,iMaxTokens,",")
        LogEntry("Finished import, here are the results:",0)
        strFilter = "sku:"
        for strSKU, dictResult in lstResults:
          if dictResult:
            if dictResult[0].get("Success"):
              LogEntry("SKU {} Successful".format(strSKU),0)
              strFilter += "{},".format(strSKU)
            else:
              LogEntry("SKU {} had an issue. Code: {}, error: {}".format(strSKU,dictResult[1][0].get("errcode"),dictResult[1][0].get("errormsg")),0)
          else:
             LogEntry("Something odd is going on. SKU {} does not have a valid result tuple".format(strSKU),0)
      else:
         LogEntry("Import File {} not found, can't do anything".format(strImportFile),0,True)
    else:
       LogEntry("Import File not defined, nothing to import",0,True)
    if strFilter.endswith(","):
      strFilter = strFilter[:-1]
    if strFilter == "":
      LogEntry("No products were successfully imported, exiting abnormally.",0,True)
    LogEntry("Next up, applying attributes to the products we just imported...",0)

  if strAction == "MIKROTIK":
    #Initializing MikroTik action
    LogEntry("Making sure no filter is applied for MikroTik action",0)
    strFilter = None
  if strAction == "FIX":
    # here is the fix function initialized
    strFilter = ""
    if strFixStatus is not None:
      strFilter += "status:{}|".format(strFixStatus)
    if strFixTag is not None:
      strFilter += "tag:{}|".format(dictGlobalTags.get(strFixTag.lower(), strFixTag))
    if strFixCategory is not None:
      strFilter += "category:{}|".format(dictGlobalCategories.get(strFixCategory.lower(), strFixCategory))

  if strAction == "EXPORT":
    # Here is the export function initialized
    if "csv" in lstExportTypes:
      strCSVOutFileName = strOutDir + "{}.csv".format(strExportFile)
      LogEntry("Starting export of product descriptions in CSV format. Output file is {}".format(strCSVOutFileName),0)
      objCSVFileOut = GetFileHandle(strCSVOutFileName, "w")
      if objCSVFileOut is None or isinstance(objCSVFileOut, str):
        objCSVFileOut = None
        LogEntry("Unable to open output file {}, error: {}".format(strCSVOutFileName, objCSVFileOut),0,True)
      objCSVFileOut.write("Brand,SKU,Name,Price,Description\n")
    if "pdf" in lstExportTypes:
      strPDFOutFileName = strOutDir + "{}.pdf".format(strExportFile)
      LogEntry("Starting export of product descriptions in PDF format. Output file is {}".format(strPDFOutFileName),0)
      objPDFDoc = SimpleDocTemplate(strPDFOutFileName, pagesize=tPageSize,
                                    rightMargin=fUnit*float(lstPDFMargins[0]),
                                    leftMargin=fUnit*float(lstPDFMargins[1]),
                                    topMargin=fUnit*float(lstPDFMargins[2]),
                                    bottomMargin=fUnit*float(lstPDFMargins[3]))
      lstStory = []
      lstStory.append(Paragraph(strCompanyName, objStyles["Title"]))
      lstStory.append(Paragraph("Product Catalog", objCenteredH1))
      lstStory.append(Paragraph("Generated {}".format(time.strftime("%A %d %B %Y")), objCenteredH2))
      lstStory.append(Spacer(1, fUnit*fSpaceAfterParagraph))

      if strLogoFilePath != "":
        objPILImg = PILImage.open(strLogoFilePath)
        iWidth, iHeight = objPILImg.size
        fAspect = iHeight / iWidth
        fImgWidth = fLogoSize * fUnit
        fImgHeight = fImgWidth * fAspect
        lstStory.append(Image(strLogoFilePath, width=fImgWidth, height=fImgHeight))
        lstStory.append(Spacer(1, fUnit*fSpaceAfterParagraph))
      if strAddress != "":
        lstStory.append(Paragraph(strCompanyName, objCenteredH3))
        lstStory.append(Paragraph(strAddress, objCenteredH3))
        lstStory.append(Paragraph(strContactEmail, objCenteredH3))
        lstStory.append(Spacer(1, fUnit*fSpaceAfterParagraph))
      lstStory.append(Paragraph(strBaseURL+"/shop/", objCenteredH2))
      lstStory.append(PageBreak())
      if strPreamble:
        lstStory.append(Paragraph("Introduction", objStyles["Heading1"]))
        lstStory.append(Spacer(1, fUnit*fSpaceAfterHeader))
        lstStory.append(Paragraph(strPreamble, objStyles["Normal"]))
        lstStory.append(Spacer(1, fUnit*fSpaceAfterParagraph))
        lstStory.append(PageBreak())


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
    objFileOut.write("Brand,SKU,Name,Type,Status,Descr len,Content len,Existing Attribute Count,Local Attribute Count,Description Attributes Count\n")

  # Here is basic prep work for all actions
  iPage = 1
  iProdCount = 5
  iTotalProducts = 0
  strEndPoint = "/wp-json/wc/v3/products"
  dictHeader = {}
  strMethod = "get"
  dictParams = {}
  dictParams["per_page"] = iPerPage
  if strFilter is None:
    if strAction == "AUDIT":
      LogEntry("No filter specified, processing all products. This may take a while if you have a lot of products, so be patient...",0)
  else:
    if strFilter.endswith("|"):
      strFilter = strFilter[:-1]
    lstFilters = strFilter.split("|")
    for lstFilter in lstFilters:
      if ":" in lstFilter:
        strFilterKey, strFilterValue = lstFilter.split(":", 1)
        if strFilterKey in ["category", "tag"] and not isNum(strFilterValue):
          LogEntry("Filter value for {}:{} is not a number, attempting to convert to ID using global dictionaries.".format(strFilterKey, strFilterValue),0)
          if strFilterKey == "category":
            strFilterValue = dictGlobalCategories.get(strFilterValue.lower(), strFilterValue)
          elif strFilterKey == "tag":
            strFilterValue = dictGlobalTags.get(strFilterValue.lower(), strFilterValue)
        LogEntry("Filtering products with {} of {}".format(strFilterKey, strFilterValue),0)
        dictParams[strFilterKey] = strFilterValue
  if strAction == "UPDATE": # Only update published products
    dictParams["status"] = "publish"
    LogEntry("For update action, only fetching published products in addition to any other filters specified",0)

  lstProductFailure = []
  lstReport = []
  while iProdCount > 0:
    LogEntry("Fetching products, page {} of {}".format(iPage, iTotalPages),1)
    dictParams["page"] = iPage
    strParams = urlLib.urlencode(dictParams)
    strURL = strBaseURL + strEndPoint + "?" + strParams
    dictResponse = MakeAPICall(strURL,dictHeader,strMethod,strUser=strWCKey,strPWD=strWCSecret)
    if dictResponse[0]["Success"]==False:
      LogEntry("API call to WooCommerce failed. {}".format(dictResponse[1]),0,True)
    LogEntry("API call successful, processing response. "
             "{} total products in response, {} total pages".format(iTotal, iTotalPages),1)
    dictProducts = dictResponse[1]
    iProdCount = len(dictProducts)
    iTotalProducts += iProdCount
    LogEntry("Received {} products in page {}. Total products fetched: {}".format(iProdCount, iPage, iTotalProducts),1)
    iPage += 1
    for dictProduct in dictProducts:
      strType = dictProduct.get("type","")
      if strType == "pw-gift-card":
        LogEntry("Product {} name {} is a gift card, skipping.".format(dictProduct["id"], dictProduct["name"]),0)
        continue
      if "description" not in dictProduct or dictProduct["description"] is None:
        LogEntry("Product {} with SKU {} and name {} has no description, skipping.".format(dictProduct["id"],
                                                                dictProduct["sku"], dictProduct["name"]),0)
        continue
      lstCategoryIDs = dictProduct.get("categories", [])
      lstCategoryNames = []
      for dictCategory in lstCategoryIDs:
        strCatName = dictCategory.get("name", "")
        lstCategoryNames.append(strCatName)
      dictAttributes = ExtractTwoColumnTables(dictProduct["description"])
      lstProdAttribs = dictProduct["attributes"] if "attributes" in dictProduct and dictProduct["attributes"] is not None else []
      iLocalCount = countLocalAttributes(lstProdAttribs)
      if strAction == "CONVERT":
        iCount, bUpdate, lstAttrib = ConvertLocalAttributes(lstProdAttribs, strBaseURL, strWCKey, strWCSecret)
        if bUpdate:
          dictResult = UpdateWooCommerceProduct({"attributes": lstAttrib}, dictProduct["id"], strBaseURL, strWCKey, strWCSecret)
          if dictResult[0]["Success"]:
            LogEntry("Successfully converted {} local attributes to global for product {} with SKU {} and name {}".format(
              iCount, dictProduct["id"], dictProduct["sku"], dictProduct["name"]),0)
          else:
            LogEntry("Failed to convert local attributes for product {} with SKU {} and name {}. Error: {}".format(
              dictProduct["id"], dictProduct["sku"], dictProduct["name"], dictResult[1]),0)
      if strAction != "MIKROTIK":
        LogEntry("Working on product {} with SKU {} and name {}. "
               "It has {} existing attributes and {} attributes in the description.".format(
                  dictProduct["id"], dictProduct["sku"], dictProduct["name"],
                  len(lstProdAttribs), len(dictAttributes)),0)
      if strPricesIncludeTax == "no":
        fTaxRate = GetProductTaxRate(lstTaxes, strBaseCountry, dictProduct.get("tax_class",""))
        strRegPrice = dictProduct.get("regular_price", "0")
        if isNum(strRegPrice):
          fRegPrice = float(strRegPrice)
        else:
          LogEntry("Regular price for product {} with SKU {} is not a number: {}".format(dictProduct["id"], dictProduct["sku"], strRegPrice),0)
          fRegPrice = 0.0
        if fTaxRate:
          fTaxMultiplier = 1 + (fTaxRate/100)
          fPriceIncTax = fRegPrice * fTaxMultiplier
        else:
          LogEntry("Couldn't find tax rate for product {}, the response for tax class {} for country {} was ({}) "
                   "so can't calculate price including tax. Setting tax multiplier to 0".format(dictProduct["name"],
                                                  dictProduct.get("tax_class", ""), strBaseCountry, fTaxRate),0)
          fPriceIncTax = fRegPrice
      else:
          fPriceIncTax = fRegPrice

      if strCurrencyPos == "left":
          strFormattedPrice = "{}{:,.{}f}".format(strCurrencySymbol, fPriceIncTax, strPriceNumDecimals)
      elif strCurrencyPos == "right":
          strFormattedPrice = "{:,.{}f}{}".format(fPriceIncTax, strPriceNumDecimals, strCurrencySymbol)
      elif strCurrencyPos == "left_space":
          strFormattedPrice = "{} {:,.{}f}".format(strCurrencySymbol, fPriceIncTax, strPriceNumDecimals)
      elif strCurrencyPos == "right_space":
          strFormattedPrice = "{:,.{}f} {}".format(fPriceIncTax, strPriceNumDecimals, strCurrencySymbol)
      strBrand = "No Brand"
      lstBrands = dictProduct["brands"]
      if isinstance(lstBrands, list):
         if len(lstBrands) > 0:
            strBrand = lstBrands[0]["name"]
      lstImages = dictProduct.get("images", [])
      if lstImages:
          strMainImageUrl = lstImages[0].get("src")

      if strAction == "MIKROTIK":
        # Build the report list for MikroTik stock report, sku and stock quantity only for products with brand MikroTik and stock quantity above 0
        if strBrand == "MikroTik" and dictProduct["stock_quantity"] > 0:
          LogEntry("Product {} - {} is a MikroTik product, stock level: {}.".format(dictProduct["sku"], dictProduct["name"], dictProduct["stock_quantity"]),0)
          dictReportItem = {}
          dictReportItem["code"] = dictProduct["sku"]
          dictReportItem["count"] = dictProduct["stock_quantity"]
          lstReport.append(dictReportItem)
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
          strPrompt = dictProduct["name"] + " " + dictProduct["description"]
        else:
          strPrompt = dictProduct["name"]
        dictNewDesc = GenerateProductDescription(strPrompt,strAIsystem,objAIClient,strAIModel,iMaxTokens)
        if not isinstance(dictNewDesc,dict):
           LogEntry("New Description is not a dict, something went wrong with AI generation, "
                    "it returned a {} containing {}".format(type(dictNewDesc),dictNewDesc),0,True)
        strNewDesc = dictNewDesc["description"] if "description" in dictNewDesc else dictProduct["description"]
        strNewName = dictNewDesc["Product_Name"] if "Product_Name" in dictNewDesc else dictProduct["name"]
        strShortDesc = dictNewDesc["short_description"] if "short_description" in dictNewDesc else dictProduct["short_description"]
        dictResult = UpdateWooCommerceProduct({"description": strNewDesc, "status": "pending", "name": strNewName,
          "short_description": strShortDesc, "tags": lstCleanTags}, dictProduct["id"], strBaseURL, strWCKey, strWCSecret)
        LogEntry("Finished updating description for product {}. Now extracting attributes from new description to update attributes if needed.".format(dictProduct["id"]),0)
        dictAttributes = ExtractTwoColumnTables(strNewDesc)
        LogEntry("Extracted {} attributes from the new description".format(len(dictAttributes)),0)

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
                LogEntry("attribute {} used for variation skipping. "
                           "ID:{} SKU:{} Name:{}".format(strKey, dictProduct["id"], dictProduct["sku"], dictProduct["name"]),0)
                continue
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
            # TODO: Think about if this should be a fatal failure or not.
            LogEntry("Failed to update product {} with new attributes. "
                      "Error: {}".format(dictProduct["id"], dictResult[1]),0,False)

      if strAction == "EXPORT":
        # write out the description to the export file(s)
        fCatalogPrice = fPriceIncTax * fPriceAdjust

        if strCurrencyPos == "left":
            strFormattedPrice = "{}{:,.{}f}".format(strCurrencySymbol, fCatalogPrice, strPriceNumDecimals)
        elif strCurrencyPos == "right":
            strFormattedPrice = "{:,.{}f}{}".format(fCatalogPrice, strPriceNumDecimals, strCurrencySymbol)
        elif strCurrencyPos == "left_space":
            strFormattedPrice = "{} {:,.{}f}".format(strCurrencySymbol, fCatalogPrice, strPriceNumDecimals)
        elif strCurrencyPos == "right_space":
            strFormattedPrice = "{:,.{}f} {}".format(fCatalogPrice, strPriceNumDecimals, strCurrencySymbol)
        if fCatalogPrice == 0:
            strFormattedPrice = "Contact us for price"
        if "csv" in lstExportTypes and objCSVFileOut is not None:
          objCSVFileOut.write("\"{}\",\"{}\",\"{}\",\"{}\",\"{}\"\n".format(strBrand.strip(), dictProduct["sku"],
            dictProduct["name"].replace(","," ").strip(), strFormattedPrice, dictProduct["short_description"].replace("\"","\"\"").strip()))
          objCSVFileOut.flush()
        if "pdf" in lstExportTypes and objPDFDoc is not None:
          if strBrand.strip() !="No Brand":
            strName = "{} {}".format(strBrand.strip(), dictProduct["name"].replace(","," ").strip())
          else:
            strName = dictProduct["name"].replace(","," ").strip()
          lstKeep = []
          lstKeep.append(Paragraph(strName, objStyles["Heading1"]))
          lstKeep.append(Spacer(1, fSpaceAfterHeader * fUnit))
          objBuffer = FetchImageBuffer(strMainImageUrl)
          if objBuffer:
            objPILImg = PILImage.open(objBuffer)
            iWidth, iHeight = objPILImg.size
            fAspect = iHeight / iWidth
            fImgHeight = fImgSize * fUnit
            fImgWidth = fImgHeight / fAspect
            objBuffer.seek(0)  # Reset buffer position after PILImage reads it
            objImage = Image(objBuffer, width=fImgWidth * fUnit, height=fImgHeight * fUnit)
            lstKeep.append(objImage)
            lstKeep.append(Spacer(1, fSpaceAfterHeader * fUnit))
          lstKeep.append(Paragraph("<b>SKU:</b> {}".format(dictProduct["sku"]), objStyles["Normal"]))
          lstKeep.append(Paragraph("<b>Price:</b> {}".format(strFormattedPrice), objStyles["Normal"]))
          lstKeep.append(Paragraph("<b>Categories:</b> {}".format(", ".join(lstCategoryNames)), objStyles["Normal"]))
          lstKeep.append(Spacer(1, fSpaceAfterParagraph * fUnit))
          lstStory.append(KeepTogether(lstKeep))
          lstDescFlowables = ParseHtmlToFlowables(dictProduct["description"])
          lstKeep = []
          for objFlowable in lstDescFlowables:
            bIsHeading = isinstance(objFlowable, Paragraph) and objFlowable.style.name.startswith("Heading")
            if bIsHeading:
              if lstKeep:
                lstStory.append(KeepTogether(lstKeep))
                lstKeep = []
              lstKeep = [objFlowable]
            elif len(lstKeep) > 0 and len(lstKeep) < 3:
              lstKeep.append(objFlowable)
            elif len(lstKeep) == 3:
              lstStory.append(KeepTogether(lstKeep))
              lstKeep = []
              lstStory.append(objFlowable)
            else:
              lstStory.append(objFlowable)
          if lstKeep:
            lstStory.append(KeepTogether(lstKeep))

          lstStory.append(Spacer(1, fSpaceAfterSection * fUnit))
          lstStory.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
          lstStory.append(Spacer(1, fSpaceAfterSection * fUnit))

      if strAction == "AUDIT":
        # write out the audit file
        objFileOut.write("{},{},{},{},{},{},{},{},{},{}\n".format(strBrand.strip(), dictProduct["sku"],
            dictProduct["name"].replace(","," ").strip(), dictProduct["type"], dictProduct["status"], len(dictProduct["description"]),
            len(StripHTML(dictProduct["description"])), len(lstProdAttribs), iLocalCount, len(dictAttributes)))
        objFileOut.flush()

  if lstReport:
    strMTOutFileName = strOutDir + "Mikrotik.csv"
    LogEntry("Writing out the MikroTik report. Output file is {}".format(strMTOutFileName),0)
    objMTFileOut = GetFileHandle(strMTOutFileName, "w")
    if objMTFileOut is None or isinstance(objMTFileOut, str):
      objMTFileOut = None
      LogEntry("Unable to open output file {}, error: {}".format(strMTOutFileName, objMTFileOut),0,True)
    for dictItem in lstReport:
      objMTFileOut.write("{},{}\n".format(dictItem["code"], dictItem["count"]))
    objMTFileOut.close()

  if strAction == "MIKROTIK" and strMikrotikToken and strMikroTikURL and lstReport:
    # For MikroTik action, post the stock levels to the MikroTik API. The API expects a list of items with code and count, and the API key for authentication.
    LogEntry("Posting stock levels to MikroTik API at {} for {} products.".format(strMikroTikURL, len(lstReport)),0)
    dictHeader = {}
    dictHeader["Content-Type"] = "application/json"
    dictUpdate = {}
    dictUpdate["apiKey"] = strMikrotikToken
    dictUpdate["report"] = lstReport
    dictResponse = MakeAPICall(strMikroTikURL, dictHeader, "post",dictPayload=dictUpdate)
    LogEntry("MikroTik API response: {}".format(dictResponse),0)
    if not dictResponse[0]["Success"]:
      LogEntry("Failed to post stock levels to MikroTik API.[{}]".format(dictResponse[1][0]["errormsg"]),0,True)

  if objFileOut is not None:
    objFileOut.close()
    LogEntry("Audit file {} closed".format(strOutFileName),0)
  if objCSVFileOut is not None:
    objCSVFileOut.close()
    LogEntry("CSV export file {} closed".format(strCSVOutFileName),0)
  if objPDFDoc is not None:
    objPDFDoc.build(lstStory, onLaterPages=DrawFooter)
    LogEntry("PDF export written to file {}".format(strPDFOutFileName),0)

  if strHeartBeatURL:
    WebResponse = MakeAPICall(strHeartBeatURL,{},"HEAD")
    LogEntry("Heartbeat posted. Response was: {}".format(WebResponse))

  LogEntry("Finished processing products. Total products fetched: {}".format(iTotalProducts),0)

  objLogOut.close()
  del objParser
  del strSentryDSN
  del objAIClient
  print("Log file {} closed, objects deleted".format(strLogFile))




if __name__ == '__main__':
  main()