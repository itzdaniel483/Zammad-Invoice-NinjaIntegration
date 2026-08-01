import os
import logging
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

INVOICE_NINJA_URL = os.environ.get('INVOICE_NINJA_URL', '').rstrip('/')
INVOICE_NINJA_API_TOKEN = os.environ.get('INVOICE_NINJA_API_TOKEN', '')

def get_or_create_client(email, name):
    headers = {
        'X-Api-Token': INVOICE_NINJA_API_TOKEN,
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/json'
    }
    
    # 1. Search for existing client
    search_url = f"{INVOICE_NINJA_URL}/api/v1/clients?email={email}"
    resp = requests.get(search_url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    
    if data.get('data'):
        return data['data'][0]['id']
        
    # 2. Create client if not found
    create_url = f"{INVOICE_NINJA_URL}/api/v1/clients"
    payload = {
        "name": name,
        "contacts": [{"email": email}]
    }
    resp = requests.post(create_url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json().get('data', {}).get('id')

def create_invoice(client_id, ticket_number, ticket_title, time_amount):
    headers = {
        'X-Api-Token': INVOICE_NINJA_API_TOKEN,
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/json'
    }
    create_url = f"{INVOICE_NINJA_URL}/api/v1/invoices"
    payload = {
        "client_id": client_id,
        "line_items": [
            {
                "product_key": f"Zammad Ticket #{ticket_number}",
                "notes": ticket_title,
                "cost": 1, # Rate can be adjusted here or passed via Zammad tags
                "quantity": time_amount
            }
        ]
    }
    resp = requests.post(create_url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json().get('data', {}).get('id')

@app.route('/webhook', methods=['POST'])
def zammad_webhook():
    if not INVOICE_NINJA_URL or not INVOICE_NINJA_API_TOKEN:
        logging.error("Invoice Ninja credentials not configured.")
        return jsonify({"error": "Configuration missing"}), 500

    data = request.json
    if not data or 'ticket' not in data:
        return jsonify({"message": "No ticket data"}), 400
        
    ticket = data['ticket']
    
    # Check if closed
    if ticket.get('state') != 'closed':
        return jsonify({"message": "Ticket not closed"}), 200
        
    # Check time accounting
    # In Zammad webhooks, ticket data usually contains "time_unit" if time accounting is used.
    time_amount = ticket.get('time_unit', 0)
    if not time_amount or float(time_amount) <= 0:
        return jsonify({"message": "No time accounted to bill"}), 200

    # Retrieve customer data from the webhook payload payload structure
    # 'customer' usually exists in the root of the payload or under 'ticket'
    customer = data.get('customer') or ticket.get('customer', {})
    if not customer:
        customer_name = "Unknown Customer"
        customer_email = "unknown@example.com"
    else:
        customer_name = f"{customer.get('firstname', '')} {customer.get('lastname', '')}".strip()
        customer_email = customer.get('email', 'unknown@example.com')
        
    if not customer_name:
        customer_name = customer_email

    try:
        client_id = get_or_create_client(customer_email, customer_name)
        if not client_id:
            raise Exception("Failed to get or create client.")
            
        invoice_id = create_invoice(client_id, ticket.get('number'), ticket.get('title'), float(time_amount))
        logging.info(f"Created invoice {invoice_id} for ticket {ticket.get('number')}")
        return jsonify({"message": "Invoice created successfully", "invoice_id": invoice_id}), 200
    except Exception as e:
        logging.error(f"Failed to process billing: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
