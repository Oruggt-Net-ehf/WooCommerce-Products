# WooCommerce Products

Author Siggi Bjarnason 21 April 2026\
Copyright 2026 Siggi Bjarnason

## Introductions

Script that analyzes the product description in WooCommerce and turns a spec list into attributes. It does this by looking for a two-column table, treating the first column as the attribute and the second column as the value.

Additionally, it can use the Anthropic API to rewrite product descriptions of specified products or create new products from a CSV import file using Anthropic AI to generate descriptions.

Since I'm a MikroTik master distributor and frequently need to send stock reports back to MikroTik, I also added a function that finds all MikroTik products, pulls the stock and SKU from WooCommerce, sends them to the MikroTik API, and writes a CSV stock report.

Another feature I added regarding MikroTik products is a function that adds basic details for any new product that hasn't been added yet. This is based on a product API from MikroTik. This API provides name, SKU, Price, product attribute collection, and a collection of product image URLs. This function checks whether the SKU is in WooCommerce and, if not, creates a new product in draft state, ready for the fix function to update the name and description. See more about this function below.

Secret management is handled by 1Password by default (either in account mode or key mode), but can also be provided via environment variables, which supports any secret management that injects environment variables, such as Doppler. The names of the environment variables are configurable through the configuration file.

If you provide an ingestion host and a source token for a metric server such as the Better Stack telemetry server, the token consumption for each fix and import action will be logged there.

Also, Sentry reporting is integrated. You can either provide your DSN in the configuration file or set the SENTRY_DSN environment variable. If you don't provide it at all, Sentry will be disabled

Heartbeat is also supported if you supply a heartbeat URL and a failure code in the config file; all exits via error handling will be logged as incidents via the heartbeat function, if it supports it. The basics of the heartbeat function, though, is to track that the script ran at the expected time; whether it was successful or not is an extra function with some providers.

If you provide an incident support system API URL and Key, all error handling exits will also generate an incident. The code assumes the BetterStack incident system.

There are eight main actions this script can take. You can specify the desired action through a command-line flag or have the script prompt for it.

## Action directives

### Audit

This action goes through all the products that match the filter condition specified in the configuration file, capturing a few stats like how many attributes can be found in the description, how many characters the description is, and how many attributes the product already has, and writes it to a csv file along with name, SKU, and product ID.

During the audit, no analysis is performed if there is an overlap between the attributes in the description and the product's actual attributes, or if the attributes on the product are local or global. This is intended as a quick indication of the status.

### Export

This action will generate a product catalog in CSV, PDF, or both as specified in the configuration file. There are various configuration items in the report section to specify how the PDF report should look.

### Update

This action traverses all products that match the filter condition specified in the configuration file. Here, though, the script actually compares the attributes in the description with those on the product. If it is already on the product as a local attribute, it gets upgraded to global. If it is not on the product at all, it gets added. If it is already on the product as a global attribute, it is left alone. Any attribute on the product but not in the description is left alone.

### Fix

This action filters products based on configuration items FixStatus, FixTag and FixCategory, that is it pulls all products in specified status, with specified tag and in specified categories and uses Claude AI model specified in config to generate a new product name, product description and short description based on the current name, and if the current product description is short enough it is added to the prompt as well. The allowable length of the description is specified in characters, configured by MaxCharIn. This allows adding additional product details to the description field, keeping the prompt more detailed while avoiding sending a fully formed 1000-word description to the prompt.

Recommendation is to update only products in the draft state, with a specific tag, and uncategorized. The fix tag specified is removed from the product once it is successfully processed.

### Convert

This action will loop through each product's attribute collection, looking for local attributes, then convert them to global attributes, either using existing ones or creating new ones.

### MikroTik

This action will loop through all products, look for MikroTik products, capture their stock levels, and report them to MikroTik Corporate. Details on the MikroTik API can be found in your master distributor's [user account](https://mikrotik.com/client/userinfo) in the Account API key section, when logged in with your master distributor's account. The API key from your user account won't work, even though you get a 200 ok response.

### Sync

This action is intended only for MikroTik master distributors and ensures that all MikroTik products are listed in your WooCommerce store, and that the stock indicator shows customers whether you actually stock this item. If it finds anything missing (like a newly released product or something that was never added), it will create a new draft product with basic details. You can then re-run the script with the FIX action to add a description and update the name.

As mentioned above, this is based on a product API from MikroTik. This API provides name, SKU, Price, product attribute collection, and a collection of product image URLs. The attribute collection is converted into a WooCommerce attribute collection and attached to the new draft product.

The function will look up the currency exchange rate for your local currency (as specified in WooCommerce settings) using one of three services, then convert the price to your local currency, add the markup specified in the configuration file, and use that as the price for the new product. The function detects the service to use based on the URL configured in the [Currency API] section of the configuration file. Here are the services it can use:

- [Currency API](https://currencyapi.com)
- [The Frankfurter](https://frankfurter.dev)
- [Currency Layer](https://currencylayer.com)

The Frankfurter is free and unlimited, but it is refreshed only once a day. The other two charge for real-time feeds. Currently, Currency API is the least expensive at 10 USD per month for 15,000 requests. They are also the most generous with their free tier at 300 requests per month, which I burned through during my dev testing.

Additionally, the function takes the image URL collection and downloads all product images into a single directory specified in the configuration file, skipping images that are already in that folder.

Details on the MikroTik API can be found in your master distributor's [user account](https://mikrotik.com/client/userinfo) in the Account API key section, when logged in with your master distributor's account.

### Import

Here you can import a new product based on a CSV file and have Claude AI generate the description. Here is a sample content (from SampleImport.csv)

``` CSV
Brand,sku,EAN/GTIN,Name,Descr,Price,Stock,Allow Backorder,Enable Reviews
MikroTik,MA53UG+HbeH,4.75222E+12,hAP be³ Media,Latest Wifi 7 router from MikroTik,28461.6,10,notify,FALSE
Teltonika,RUT241000000,4.77905E+12,RUT241 LTE Cat 4 Router,Popular router,37831,15,notify,FALSE
Anker,A1263 ,8.48061E+11,PowerCore 10000,the purple one,5795,8,notify,FALSE
```

"Allow Backorder" and "Enable Reviews" allowed values are per the WooCommerce REST API specifications. Backorders allowed values are "yes", "no" and "notify"; reviews_allowed (Enable Reviews) is a simple boolean.

Brand and SKU are also known as make and model. EAN/GTIN is the global product number often found on barcodes; UPC is a form of GTIN. Name is the product name, and descr is additional details about the product. Keep it short (less than 100 characters). Price is a float, and stock is an integer.

Brand, SKU, Name and descr is then combined (space deliminated) to form the AI prompt.

## Operational details

The following packages need to be installed

pip install requests\
pip install sentry_sdk\
pip install onepassword-sdk\
pip install beautifulsoup4\
pip install anthropic\
pip install reportlab

### CLI Explained

`usage: python ProductUpdateAttr.py [-h] [--silent] [--audit] [--update] [--import] [--fix] [--mikrotik] [--convert] [--export] [--sync] [--production] [-c CONFIG] [-v] [-x PROXY] [-o OUTDIR]`

WooCommerce Product description parser and attrib creator. Also creates new products and updates product descriptions. If no config file is specified, it will look for ProductUpdateAttr.ini in the same directory as the script. Requires one and only one Action directive. If omitted the script prompts for it.

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
  `--sync`               Action directive. Sync between WooCommerce and MikroTik Product feed. Required unless you specify another action, only one action can be specified.
  `--production`         flag to consent that you know you are running production config. If configuration file has environment variable set to production, the script will not run without this flag. Conversely setting this flag if configuration environment is not set to production will stop the script cold.
  `-c, --config CONFIG`  Path to the configuration file. Optional. Defaults to ProductUpdateAttr.ini in the same directory as the script.\
  `-v, --verbosity`      Verbose output, vv level 2 vvvv level 4\
  `-x, --proxy PROXY`    Proxy to use for API calls. Optional\
  `-o, --outdir OUTDIR`  Output directory for generated files. Optional. Defaults to folder named output in the script directory\
  `-i, --input INPUT`    Input file for product import action, overrides config file setting for import file

### Configuration file explained

`[Generic]`\
`Environment = production` *(If the value starts with prod, case insensitive this configuration file will be considered a production configuration, and the script won't run without the production flag. Any other value will be considered non-prod, where the production flag is not allowed. This is to safeguard using production configuration file, thinking it is non-prod. Depends of course on setting this correctly. Therefor it is important to set this to prod if this configuration file points to production API keys)*\
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
`Filter = sku:Q208` *(Filter specification per WooCommerce REST API specifications. Use : to seperate attribute and value. Specify multiple filter specification by using | as the seperator. For example:`status:draft|type:simple|min_price:18000` filters for simple products in draft status with price over 18000. For category or tag you can filter either by ID number or name. For example `Filter = category:! Featured Products !` filters for any product that has a category name "! Featured Products !"*)\

`[MikroTik Details]`\
`ProductImgPath = c:\img\ProductImg`*(Only used by the sync operation as the directory to store the downloaded product images)*\
`BrandName = MikroTik`*(How did you setup MikroTik in your WooCommerce Brands)*\
`Category = Mikrotik Products`*(What is the name of the category, if any, that you want all new MikroTik products created with)*\
`Markup = 20`*(When calculating the price in the store, how much markup do you want, 20 means 20%)*\

`[Report Export]`\
`ReportFileName = ProductCatalog` *(What should the export file be called, this will go in the outdir already defined and get appropriate extension)*\
`ContactEmail = sales@oruggtnet.is` *(Contact info for the cover page)*\
`PreambleFilePath = Preamble.txt` *(This text will be read in made into page 2 introduction text)*\
`ExportPriceAdjust = 0.8` *(Adjust the price based on purpose. This example gives 20% discount across the board, for example for wholesale. 1.5 puts 50% surcharge to encourage using the webstore instead of the catalog)*\
`ExportTypes = csv,pdf` *(Do you want both csv and pdf export, or just one or the other.)*\
`Units = mm` *(valid options are mm, cm, and inch)*\
`ProdImgSize = 35` *(The height of each product image in the units specified, maintaining aspect ratio)*\
`PDFPageSize = A4` *(valid options are A4 or letter)*\
`PDFMargins = 20,20,25,25` *(right, left, top, bottom, using the units you specified above)*\
`AfterHeader = 2` *(using units specified, how much space after a header. In this example 2 mm)*\
`AfterParagraph = 4` *(same, just after a paragraph)*\
`AfterSection = 6` *(same just space between sections)*\
`CompanyName = Öruggt Net ehf` *(Title of the report)*\
`Address = Kristínargata 1
  102 Reykjavik
  Iceland` *(Address, can be put on multiple lines if subsequent lines are indented)*\
`CompanyLogo = E:\Logo\Öruggt Net.jpg` *(Full path to the logo for the front of the report)*\
`LogoSize = 50` *(Size of the logo on the report in units specified above)*\

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
`StockReport = StockReport` *(The name of the field or env variable holding the URL to post the stock update to)*\
`ProductList = ProductList` *(The name of the field or env variable holding the URL to pull product information from)*\

`[UptimeCreds]`\
`VaultID = xxxxx` *(1Password vault ID where item is kept, leave off for env auth)*\
`ItemID = yyyyy` *(The item of the item holding the Anthropic API credentials, leave off for env auth)*\
`TokenField = credential` *(The name of the field or env variable with the Uptime API key)*\

`[Currency API]`\
`VaultID = xxxxx` *(1Password vault ID where item is kept, leave off for env auth)*\
`ItemID = yyyyy` *(The item of the item holding the Anthropic API credentials, leave off for env auth)*\
`TokenField = credential` *(The name of the field or env variable with the Currency Service API key)*\
`BaseURLField = hostname` *(Name of the field or env variable name holding the URL for the choicen Currency service, for example https://api.frankfurter.dev/v2/rates)*

### Attribute Substitution

Certain attributes in the text are needlessly long, here you can replace them with something shorter and nicer. It's just a semicolon separate line with the original name first then followed by the new line. Put as many lines as you need into a single csv file and put the name in the AttrEqFile configuration item above.

***For example:***\
Number of 25G SFP28 ports;25G SFP28 ports
