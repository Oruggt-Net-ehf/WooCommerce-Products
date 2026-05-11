# WooCommerce Products

## Introductions

Script that analyzes product description in WooCommerce and turns a spec list into attributes.
Additionally it can use Anthropic API to rewrite product descriptions or create new products from a CSV import file using Anthropic AI to generate descriptions.

Secret management is by default handled by 1Password (either in account mode or key mode) but can optionally be fed in through environment variables which supports any secret managment that injects environment variables such as Doppler. The name of the environment variables is configurable through the configuration file.

If you provide an ingestion host and source token for a metric server like Better Stack telemetry server, the token consumption from each fix and import action will be logged there.

Also sentry reporting is integrated. You provide your DSN below or put it in env variable SENTRY_DSN. If you don't provide it at all, Sentry will be disabled

Author Siggi Bjarnason 21 April 2026\
Copyright 2026 Siggi Bjarnason

Following packages need to be installed

pip install requests\
pip install sentry_sdk\
pip install argparse\
pip install onepassword-sdk\
pip install asyncio\
pip install beautifulsoup4\
pip install anthropic

## CLI Explained

`usage: python ProductUpdateAttr.py [-h] [--silent] [--audit] [--update] [--import] [--fix] [-c CONFIG] [-v] [-x PROXY] [-o OUTDIR]`

WooCommerce Product description parser and attrib creator. If no config file is specified, it will look for ProductUpdateAttr.ini in the same directory as the script. Requires one and only one Action directive. If omitted the script prompts for it.

`options:`\
  `-h, --help`           show this help message and exit\
  `--silent`             only output to file, not to screen\
  `--audit`              Action directive. Only audit products and attributes, no updates. Prints out the product name & sku along with how many attributes it has and how many attributes are in text. No overlap information available.\
  `--update`             Action directive. Update all products with attributes parsed from description. Required unless you specify another action, only one action can be specified.\
  `--import`             Action directive. Create new products based on import file. Required unless you specify another action, only one action can be specified.\
  `--fix`                Action directive. Fix product descriptions by passing existing product name to Claude and asking for new descriptions. Required unless you specify another action, only one action can be specified.\
  `-c, --config CONFIG`  Path to the configuration file. Optional. Defaults to ProductUpdateAttr.ini in the same directory as the script.\
  `-v, --verbosity`      Verbose output, vv level 2 vvvv level 4\
  `-x, --proxy PROXY`    Proxy to use for API calls. Optional\
  `-o, --outdir OUTDIR`  Output directory for generated files. Optional. Defaults to folder named output in the script directory\
  `-i, --input INPUT`    Input file for product import action, overrides config file setting for import file

## Configuration file explained

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
`SentryDSN = https://asdfjælasjdflæajsdlfjlaj@xxxxyyyxxx.eu-fsn-3.betterstackdata.com/123564` *(Your sentry DSN, leave this blank to disable Sentry)*\
`OutDir = c:\temp` *(The directory where all write operations should take place)*\
`ImportFile = c:\temp\myimportfile.csv` *(Full path of the import file needed for import operations)*\
`AIBackgroundFile = system.txt` *(File name for the AI system prompt)*\
`AIModel = claude-sonnet-4-6` *(What AI model should we use?)*\
`MaxTokens = 2048`  *(Safety net for runaway output, call will terminated if it uses more than this many tokens)*\
`MaxCharIn = 500` *(If the long description has fewer characters than this, include it with the name in the prompt for update operations. Defaults to 0)*\
`AttrEqFile = AttrEq.csv` *(Attributes Substitution file)*\
`Filter = sku:Q208` *(Filter specification per WooCommerce REST API specifications. Use : to seperate attribute and value. Specify multiple filter specification by using | as the seperator. For example:`status:draft|type:simple|min_price:18000` filters for simple products in draft status with price over 18000)*\

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
`MetricTokenField = MetricToken` *(The name of the field or env variable with Better Stack Source Token)*

## Attribute Substitution

Certain attributes in the text are needlessly long, here you can replace them with something shorter and nicer. It's just a semicolon separate line with the original name first then followed by the new line. Put as many lines as you need into a single csv file and put the name in the AttrEqFile configuration item above.

***For example:***\
Number of 25G SFP28 ports;25G SFP28 ports
