# WPWC

## Introductions

Script that analyzes product description in WooCommerce and turns a spec list into attributes
Additionally it can use Anthropic API to rewrite product descriptions
or create new products from a CSV import file using Antropic AI to generate descriptions.

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

## CLI Explained

usage: python ProductUpdateAttr.py [-h] [--silent] [--audit] [--update] [--import] [--fix] [-c CONFIG] [-v] [-x PROXY] [-o OUTDIR]

WooCommerce Product description parser and attrib creator. Must specify one Action directive. If no config file is specified, it will look for ProductUpdateAttr.ini in the same directory as the script.

options:
  -h, --help           show this help message and exit
  --silent             only output to file, not to screen
  --audit              Action directive. Only audit products and attributes, no updates.
  --update             Action directive. Update all products with attributes parsed from description. Required unless you specify another action, only one action can be specified.
  --import             Action directive. Create new products based on import file.Required unless you specify another action, only one action can be specified.
  --fix                Action directive. Fix product descriptions.Required unless you specify another action, only one action can be specified.
  -c, --config CONFIG  Path to the configuration file
  -v, --verbosity      Verbose output, vv level 2 vvvv level 4
  -x, --proxy PROXY    Proxy to use for API calls
  -o, --outdir OUTDIR  Output directory for generated files

## Configuration file explained

[Generic]
AuthMethod = 1Password or Env (only first three characters are relevant, not case sensitive)
1PassAccount = Required when 1Password is the method ignored on env. The name of your 1Password account as shown in Manage accounts
FileTimeStampFormat = %%Y-%%m-%%d-%%H-%%M-%%S (standard python time stamp format, double percent for escape purposes)
TimeStampAudit = false or true. Do you want the audit filename to have a timestamp in it.
PerPage = 25 When doing API operations, how many items should be fetch per time. Max 100, 25 seems to be very effective
FixStatus = draft For operations FIX, what product status should be filtered on. Recommend only fixing products in draft state
FixTag = NeedsFixing Additional filter safety net for FIX operation, flag products needing fixing with specified tag. Blank disables this filter
FixCategory = Uncategorized Additional filter safety net for FIX operation, only fix products in this category.
MetricURL = Provide your Better Stack or other OpenMetric server URL here.
OutDir = c:\temp The directory where all write operations should take place
ImportFile = Full path of the import file needed for import operations
AIBackgroundFile = system.txt File name for the AI system prompt
AIModel = claude-sonnet-4-6 - What AI model should we use?
MaxTokens = 2048  - Safetynet for runaway output, call will terminated if it uses more than this many tokens
AttrEqFile = AttrEq.csv Attributes Substitution file

[WPCreds]
VaultID = o2kycysysfiwki2yqfvwo5w2gm
ItemID = fb4deay4oahrq4cwmbuxy5fafm
ConsumerKeyField = username
ConsumerSecretField = credential
BaseURLField = hostname

[AICreds]
VaultID = o2kycysysfiwki2yqfvwo5w2gm
ItemID = ouezr5lfusvmvengskjg4vse6m
APIKeyField = credential
MetricTokenField = MetricToken

## Attribute Substitution
