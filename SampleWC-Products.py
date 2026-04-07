import requests
from requests.auth import HTTPBasicAuth

# Replace these with your actual WooCommerce store details
url = "https://your-store.com/wp-json/wc/v3/products"
consumer_key = "your_consumer_key"
consumer_secret = "your_consumer_secret"

# Set up authentication
auth = HTTPBasicAuth(consumer_key, consumer_secret)

# Make a GET request to fetch products
response = requests.get(url, auth=auth)

# Check if the request was successful
if response.status_code == 200:
    products = response.json()
    for product in products:
        print(f"Product ID: {product['id']}, Name: {product['name']}")
else:
    print(f"Failed to fetch products. Status code: {response.status_code}, Response: {response.text}")