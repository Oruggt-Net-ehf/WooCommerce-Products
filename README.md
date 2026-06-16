# WooCommerce Products

Author Siggi Bjarnason 21 April 2026\
Copyright 2026 Siggi Bjarnason

## Introductions

Script that analyzes product description in WooCommerce and turns a spec list into attributes. It does this by looking for a two column table treating the first column as the attribute and the second column as the value.

Additionally it can use Anthropic API to rewrite product descriptions or create new products from a CSV import file using Anthropic AI to generate descriptions.

Since I'm a MikroTik master distributor and I have a requirement to sent stock reports back to HQ I also added an function that can find all the MikroTik products, take the stock and sku from WooCommerce and send it to the MikroTik API as well as write a CSV stock report.

Secret management is by default handled by 1Password (either in account mode or key mode) but can optionally be fed in through environment variables which supports any secret managment that injects environment variables such as Doppler. The name of the environment variables is configurable through the configuration file.

If you provide an ingestion host and source token for a metric server like Better Stack telemetry server, the token consumption from each fix and import action will be logged there.

Also sentry reporting is integrated. You provide your DSN in the configuration file or put it in env variable SENTRY_DSN. If you don't provide it at all, Sentry will be disabled

Heartbeat is also supported if you supply a heartbeat URL and failure code in the config file, all exits via error handling will be logged as incidents through the heartbeat function, if fail conditions are supported by it.

If you provide a incident support system API URL and Key, all error handling exits will also generate an incident. The code assumes BetterStack incident system.

There are five main actions this script can take. You can specify the desired action through a command line flag or have the script prompt for it.

## Action directives

### Audit

This action goes through all the products that match the filter condition specified in the configuration file capturing few stats like how many attributes can be found in the description, how many characters the description is and how many attributes the product already has and writes it to a csv file along with name, sku and product id.

During audit no analysis is done if there is overlap between attributes found in description and actual attributes on the product, nor if if the attributes on the product are local or global. This is intended as quick indication of the status.

### Export

This action will generate a product catalog in csv, pdf or both as specified in configuration file. There are various configuration items in the report section to specify how the pdf report should look like.

### Update

This action goes through all the products that match the filter condition specified in the configuration file. Here though, the script actually compares the attributes found in the description and compares it to the attributes on the product. If it is already on the product as a local attributes, it gets upgraded to global. If it is not on the product at all it gets added. If it is already on the product as a global attributed, it is left alone. Any attribute on the product but not in the description are left alone.

### Fix

This action filters products based on configuration items FixStatus, FixTag and FixCategory, that is it pulls all products in specified status, with specified tag and in specified categories and uses Claude AI model specified in config to generate a new product name, product description and short discription based on the current name, and if the current product description is short enough it is added to the prompt as well. The allowable length of description is specified in number of characters which is configured with MaxCharIn. This allows for putting additional details about the product in the description field so the prompt is more detailed, yet avoids sending a fully formed 1000 words description to the prompt.

Recommendation is to only update products in draft state, with a specific tag and uncategorized. The fix tag specified gets removed from the product once successfully processed.

### Convert

This action will loop through the attribute collection in each product looking for a local attributes, then convert them to global attribute, either using existing ones or creating new ones.

### MikroTik

This action will loop through all products looking for MikroTik devices, captures stock level and reports it MikroTik Corporate.

### Import

Here you can import new product based on a CSV file and have Claude AI generate the description. Here is a sample content (from SampleImport.csv)

``` CSV
Brand,sku,EAN/GTIN,Name,Descr,Price,Stock,Allow Backorder,Enable Reviews
MikroTik,MA53UG+HbeH,4.75222E+12,hAP be³ Media,Latest Wifi 7 router from MikroTik,28461.6,10,notify,FALSE
Teltonika,RUT241000000,4.77905E+12,RUT241 LTE Cat 4 Router,Popular router,37831,15,notify,FALSE
Anker,A1263 ,8.48061E+11,PowerCore 10000,the purple one,5795,8,notify,FALSE
```

"Allow Backorder" and "Enable Reviews" allowed values are per the WooCommerce REST API specifications. Backorders allowed values are "yes", "no" and "notify"; reviews_allowed (Enable Reviews) is a simple boolean.

Brand and sku is also known as make and model. EAN/GTIN is the global product number often found on barcodes, UPC is a form of a GTIN. Name is the product name and descr is additional details about the product, keep it short (less than 100 char). Price is a float and stock is an integer.

Brand, sku, Name and descr is then combined (space deliminated) to form the AI prompt.

## Operational details

Following packages need to be installed

pip install requests\
pip install sentry_sdk\
pip install onepassword-sdk\
pip install beautifulsoup4\
pip install anthropic\
pip install reportlab

### CLI Explained

`usage: python ProductUpdateAttr.py [-h] [--silent] [--audit] [--update] [--import] [--fix] [--mikrotik] [--convert] [--export] [-c CONFIG] [-v] [-x PROXY] [-o OUTDIR]`

WooCommerce Product description parser and attrib creator. If no config file is specified, it will look for ProductUpdateAttr.ini in the same directory as the script. Requires one and only one Action directive. If omitted the script prompts for it.

`options:`\
  `-h, --help`           show this help message and exit\
  `--silent`             only output to file, not to screen\
  `--audit`              Action directive. Only audit products and attributes, no updates. Prints out the product name & sku along with how many attributes it has and how many attributes are in text. No overlap information available.\
  `--update`             Action directive. Update all products with attributes parsed from description. Required unless you specify another action, only one action can be specified.\
  `--import`             Action directive. Create new products based on import file. Required unless you specify another action, only one action can be specified.\
  `--fix`                Action directive. Fix product descriptions by passing existing product name to Claude and asking for new descriptions. Required unless you specify another action, only one action can be specified.\
  `--mikrotik`           Action directive. Update stock level with Mikrotik.Required unless you specify another action, only one action can be specified.
  `--convert`            Action directive. Convert local attributes to global ones.Required unless you specify another action, only one action can be specified.
  `--export`             Action directive. Export all products to a CSV file and/or PDF based on config, no updates will be made. Required unless you specify another action, only one action can be specified.
  `-c, --config CONFIG`  Path to the configuration file. Optional. Defaults to ProductUpdateAttr.ini in the same directory as the script.\
  `-v, --verbosity`      Verbose output, vv level 2 vvvv level 4\
  `-x, --proxy PROXY`    Proxy to use for API calls. Optional\
  `-o, --outdir OUTDIR`  Output directory for generated files. Optional. Defaults to folder named output in the script directory\
  `-i, --input INPUT`    Input file for product import action, overrides config file setting for import file

### Configuration file explained

`[Generic]`\
`AuthMethod = 1Password` or `Env` *(only first three characters are relevant, not case sensitive)*\
`1PassAccount = my account name` *(Required when 1Password is the method, unless you are using token. Ignored on env. The name of your 1Password account as shown in Manage accounts)*\
`1PassTokenEnvVar = 1PASSTOKEN` *(If you are using 1Password in token mode rather than account mode, this is the name of the environment variable for the token. Defaults to "1PASSTOKEN" if not provided )*\
`FileTimeStampFormat = %%Y-%%m-%%d-%%H-%%M-%%S` *(standard python time stamp format, double percent for escape purposes)*\
`TimeStampAudit = false` or `true` *(Do you want the audit filename to have a timestamp in it.)*\
`PerPage = 25` *(When doing API operations, how many items should be fetch per API call. Max 100, 25 seems to be very effective)*\
`FixStatus = draft` *(For operations FIX, what product status should be filtered on. Recommend only fixing products in draft state)*\
`FixTag = NeedsFixing` (*Additional filter safety net for FIX operation, flag products needing fixing with specified tag. Blank disables this filter*)\
`FixCategory = Uncategorized` *(Additional filter safety net for FIX operation, only fix products in this category.)*\
`IngestionHost = xxxx.yyyy.betterstackdata.com` *(Provide your Better Stack or other OpenMetric ingesting host here.)*\
`MetricEndpoint = metrics` *(The endpoint to post the metrics to. For Better stack the pattern is schema+igestionhost+"metrics" or `https://xxxx.yyyy.betterstackdata.com/metrics` defaults to "metrics" if not supplied)*\
`FailureCode = fail` *(Default exit code used by heartbeat function. String "fail" and numbers greater than 0 will cause heartbeat to create an incident on error exit. Anything else will disable this)*\
`SentryDSN = https://asdfjælasjdflæajsdlfjlaj@xxxxyyyxxx.eu-fsn-3.betterstackdata.com/123564` *(Your sentry DSN, leave this blank to disable Sentry)*\
`HeartBeatURL = https://uptime.betterstack.com/api/v1/heartbeat/q49e2LCdamHxyzabcRRozhALb` *(Heartbeat URL, optional)*\
`IncidentURL = https://uptime.betterstack.com/api/v3/incidents` *(URL for the incident creation URL, optional)*\
`OutDir = c:\temp` *(The directory where all write operations should take place)*\
`ImportFile = c:\temp\myimportfile.csv` *(Full path of the import file needed for import operations)*\
`AIBackgroundFile = system.txt` *(File name for the AI system prompt)*\
`AIModel = claude-sonnet-4-6` *(What AI model should we use?)*\
`MaxTokens = 2048`  *(Safety net for runaway output, call will terminated if it uses more than this many tokens)*\
`MaxCharIn = 500` *(If the long description has fewer characters than this, include it with the name in the prompt for update operations. Defaults to 0)*\
`AttrEqFile = AttrEq.csv` *(Attributes Substitution file)*\
`Filter = sku:Q208` *(Filter specification per WooCommerce REST API specifications. Use : to seperate attribute and value. Specify multiple filter specification by using | as the seperator. For example:`status:draft|type:simple|min_price:18000` filters for simple products in draft status with price over 18000)*\

[Report Export]
ExportPriceAdjust = 1.8
ExportTypes = csv,pdf
Units = mm
PDFPageSize = A4
PDFMargins = 20,20,25,25
AfterHeader = 2
AfterParagraph = 4
AfterSection = 6
CompanyName = Öruggt Net ehf
Address = Kristínargata 1
 102 Reykjavik
 Iceland
CompanyLogo = E:\OneDrive - Öruggt Net ehf\Documents - Sölu og Markaðsteymi\content\Logo\Öruggt Net.jpg
LogoSize = 50

`[WPCreds]`\
`VaultID = xxxxxxxx` *(1Password vault ID where item is kept, leave off for env auth)*\
`ItemID = yyyyyyy` *(The item of the item holding the WooCommerce API credentials, leave off for env auth)*\
`ConsumerKeyField = username` *(name of the field in 1Password or env variable name holding the consumer key)*\
`ConsumerSecretField = credential` *(name of the field in 1Password or env variable name holding the consumer secret)*\
`BaseURLField = hostname` *(Name of the field or env variable name holding the base URL)*

`[AICreds]`\
`VaultID = xxxxx` *(1Password vault ID where item is kept, leave off for env auth)*\
`ItemID = yyyyy` *(The item of the item holding the Anthropic API credentials, leave off for env auth)*\
`APIKeyField = credential` *(The name of the field or env variable with the AI API key)*\
`MetricTokenField = MetricToken` *(The name of the field or env variable with Better Stack Metrics Source Token)*

`[MikrotikCreds]` *(This section is only needed if you intend to use the MikroTik function)*\
`VaultID = xxxxx` *(1Password vault ID where item is kept, leave off for env auth)*\
`ItemID = yyyyy` *(The item of the item holding the Anthropic API credentials, leave off for env auth)*\
`TokenField = credential` *(The name of the field or env variable with the MikroTik API key)*\
`HostField = hostname` *(The name of the field or env variable holding the URL to post the stock update to)*\

`[UptimeCreds]`\
`VaultID = xxxxx` *(1Password vault ID where item is kept, leave off for env auth)*\
`ItemID = yyyyy` *(The item of the item holding the Anthropic API credentials, leave off for env auth)*\
`TokenField = credential` *(The name of the field or env variable with the Uptime API key)*\

### Attribute Substitution

Certain attributes in the text are needlessly long, here you can replace them with something shorter and nicer. It's just a semicolon separate line with the original name first then followed by the new line. Put as many lines as you need into a single csv file and put the name in the AttrEqFile configuration item above.

***For example:***\
Number of 25G SFP28 ports;25G SFP28 ports
