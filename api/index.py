from flask import Flask, request, render_template, redirect, url_for
from supabase import create_client, Client
import requests
import os
from datetime import datetime

app = Flask(__name__, template_folder='../templates')

# Ambil Kunci Rahasia
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
FONNTE_TOKEN = os.environ.get("FONNTE_TOKEN")

# Koneksi Database
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.route('/')
def dashboard():
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        # Ambil data hari ini
        response = supabase.table('chats').select("*").gte('created_at', today).order('created_at', desc=True).execute()
        chats = response.data
    except:
        chats = []

    stats = {
        'sent': sum(1 for c in chats if c.get('direction') == 'outbound'),
        'read': sum(1 for c in chats if c.get('status') == 'READ'),
        'replied': sum(1 for c in chats if c.get('direction') == 'inbound')
    }

    replies = [c for c in chats if c.get('direction') == 'inbound']
    return render_template('dashboard.html', stats=stats, replies=replies)

@app.route('/send', methods=['POST'])
def send_message():
    phone = request.form.get('phone')
    message = request.form.get('message')

    # Kirim Fonnte
    headers = {'Authorization': FONNTE_TOKEN}
    data = {'target': phone, 'message': message}
    try:
        requests.post('https://api.fonnte.com/send', headers=headers, data=data)
    except:
        pass

    # Simpan Database
    try:
        supabase.table('chats').insert({
            "customer_phone": phone,
            "message": message,
            "direction": "outbound",
            "status": "sent"
        }).execute()
    except:
        pass

    return redirect(url_for('dashboard'))

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if data:
        sender = data.get('sender')
        message = data.get('message')
        status = data.get('status')

        if status == 'READ':
            try:
                last = supabase.table('chats').select('id').eq('customer_phone', sender).eq('direction', 'outbound').order('created_at', desc=True).limit(1).execute()
                if last.data:
                    supabase.table('chats').update({'status': 'READ'}).eq('id', last.data[0]['id']).execute()
            except:
                pass
        elif message and not status:
            try:
                supabase.table('chats').insert({
                    "customer_phone": sender,
                    "message": message,
                    "direction": "inbound",
                    "status": "received"
                }).execute()
            except:
                pass
    return "OK", 200