from flask import Flask, request, render_template, redirect, url_for
from supabase import create_client, Client
import requests
import os
from datetime import datetime
import json

app = Flask(__name__, template_folder='../templates')

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
FONNTE_TOKEN = os.environ.get("FONNTE_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

@app.route('/')
def dashboard():
    # Ambil data hari ini
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        response = supabase.table('chats').select("*").gte('created_at', today).order('created_at', desc=True).execute()
        chats = response.data
    except Exception as e:
        print(f"Error Dashboard: {e}")
        chats = []

    # Statistik Cerdas (Case Insensitive)
    sent_count = sum(1 for c in chats if c.get('direction') == 'outbound')
    # Hitung 'read' jika status mengandung kata 'read' (misal: 'READ', 'read', 'Auto Read')
    read_count = sum(1 for c in chats if c.get('status') and 'read' in c.get('status').lower())
    reply_count = sum(1 for c in chats if c.get('direction') == 'inbound')
            
    stats = {'sent': sent_count, 'read': read_count, 'replied': reply_count}
    replies = [c for c in chats if c.get('direction') == 'inbound']
    
    return render_template('dashboard.html', stats=stats, replies=replies)

@app.route('/send', methods=['POST'])
def send_message():
    phone = request.form.get('phone')
    message = request.form.get('message')
    
    headers = {'Authorization': FONNTE_TOKEN}
    data = {'target': phone, 'message': message}
    
    msg_id = None
    status_awal = "sent"

    # 1. Kirim ke Fonnte & Tangkap ID-nya
    try:
        req = requests.post('https://api.fonnte.com/send', headers=headers, data=data)
        response_json = req.json()
        # Ambil ID pesan dari balasan Fonnte (supaya bisa dilacak status bacanya nanti)
        if response_json.get('id'):
            msg_id = response_json.get('id')[0] # Fonnte kasih ID dalam bentuk list
        print(f"Pesan Terkirim. ID: {msg_id}")
    except Exception as e:
        print(f"Error Kirim Fonnte: {e}")
        status_awal = "failed"
    
    # 2. Simpan ke Database lengkap dengan ID
    try:
        supabase.table('chats').insert({
            "customer_phone": phone,
            "message": message,
            "direction": "outbound",
            "status": status_awal,
            "fonnte_id": msg_id  # <--- INI KUNCINYA
        }).execute()
    except Exception as e:
        print(f"Error Simpan DB: {e}")
    
    return redirect(url_for('dashboard'))

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    data = request.json or request.form
    if not data: return "No Data", 200

    print(f"WEBHOOK: {data}")

    # Ambil data penting
    sender = data.get('sender')
    message = data.get('message')
    status = data.get('status') or data.get('state') # Bisa 'read', 'delivered', dll
    id_pesan = data.get('id') # ID pesan yang update statusnya

    # KASUS 1: UPDATE STATUS BACA (READ)
    if status and id_pesan:
        print(f"Update Status ID {id_pesan} menjadi {status}")
        try:
            # Cari pesan di DB berdasarkan ID Fonnte, lalu update statusnya
            supabase.table('chats').update({'status': status}).eq('fonnte_id', id_pesan).execute()
        except Exception as e:
            print(f"Gagal Update Status: {e}")

    # KASUS 2: PESAN BALASAN MASUK
    # Pastikan ini pesan chat (ada sender & message), bukan cuma laporan status
    if sender and message and (not status or status == 'received'):
        try:
            # Cek duplikasi sederhana
            existing = supabase.table('chats').select('id').eq('message', message).eq('customer_phone', sender).limit(1).execute()
            if not existing.data:
                supabase.table('chats').insert({
                    "customer_phone": sender,
                    "message": message,
                    "direction": "inbound",
                    "status": "received"
                }).execute()
        except Exception as e:
            print(f"Gagal Simpan Balasan: {e}")

    return "OK", 200
