import os
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

CONFIG_FILE = 'data/config.json'

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        "invoice_ninja_url": "",
        "invoice_ninja_api_token": "",
        "hourly_rate": 50.0,
        "due_date_days": 14,
        "auto_send": False
    }

def save_config(config):
    os.makedirs('data', exist_ok=True)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(load_config())

@app.route('/api/settings', methods=['POST'])
def update_settings():
    config = request.json
    save_config(config)
    return jsonify({"message": "Settings saved successfully"})

def get_or_create_client(config, email, name):
    headers = {
        'X-Api-Token': config['invoice_ninja_api_token'],
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/json'
    }
    base_url = config['invoice_ninja_url'].rstrip('/')
    
    # 1. Search for existing client
    search_url = f"{base_url}/api/v1/clients?email={email}"
    resp = requests.get(search_url, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    
    if data.get('data'):
        return data['data'][0]['id']
        
    # 2. Create client if not found
    create_url = f"{base_url}/api/v1/clients"
    payload = {
        "name": name,
        "contacts": [{"email": email, "send_email": True}]
    }
    resp = requests.post(create_url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json().get('data', {}).get('id')

def create_and_send_invoice(config, client_id, ticket_number, ticket_title, time_amount):
    headers = {
        'X-Api-Token': config['invoice_ninja_api_token'],
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/json'
    }
    base_url = config['invoice_ninja_url'].rstrip('/')
    
    # Calculate Due Date
    due_date = (datetime.now() + timedelta(days=int(config.get('due_date_days', 14)))).strftime('%Y-%m-%d')
    
    # Calculate cost (assuming time_amount is in minutes)
    hourly_rate = float(config.get('hourly_rate', 50.0))
    cost = hourly_rate / 60.0
    
    create_url = f"{base_url}/api/v1/invoices"
    payload = {
        "client_id": client_id,
        "due_date": due_date,
        "line_items": [
            {
                "product_key": f"Zammad Ticket #{ticket_number}",
                "notes": ticket_title,
                "cost": cost,
                "quantity": time_amount
            }
        ]
    }
    
    resp = requests.post(create_url, headers=headers, json=payload)
    resp.raise_for_status()
    invoice_data = resp.json().get('data', {})
    invoice_id = invoice_data.get('id')
    
    # Auto-send if configured
    if invoice_id and config.get('auto_send', False):
        email_url = f"{base_url}/api/v1/emails"
        email_payload = {
            "entity": "invoice",
            "entity_id": invoice_id,
            "template": "invoice"
        }
        email_resp = requests.post(email_url, headers=headers, json=email_payload)
        email_resp.raise_for_status()
        logging.info(f"Auto-sent invoice {invoice_id}")
        
    return invoice_id

@app.route('/webhook', methods=['POST'])
def zammad_webhook():
    config = load_config()
    if not config.get('invoice_ninja_url') or not config.get('invoice_ninja_api_token'):
        logging.error("Invoice Ninja credentials not configured.")
        return jsonify({"error": "Configuration missing"}), 500

    data = request.json
    if not data or 'ticket' not in data:
        return jsonify({"message": "No ticket data"}), 400
        
    ticket = data['ticket']
    
    if ticket.get('state') != 'closed':
        return jsonify({"message": "Ticket not closed"}), 200
        
    time_amount = ticket.get('time_unit', 0)
    if not time_amount or float(time_amount) <= 0:
        return jsonify({"message": "No time accounted to bill"}), 200

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
        client_id = get_or_create_client(config, customer_email, customer_name)
        if not client_id:
            raise Exception("Failed to get or create client.")
            
        invoice_id = create_and_send_invoice(config, client_id, ticket.get('number'), ticket.get('title'), float(time_amount))
        logging.info(f"Processed invoice {invoice_id} for ticket {ticket.get('number')}")
        return jsonify({"message": "Invoice created successfully", "invoice_id": invoice_id}), 200
    except Exception as e:
        logging.error(f"Failed to process billing: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    os.makedirs('data', exist_ok=True)
    app.run(host='0.0.0.0', port=5000)
