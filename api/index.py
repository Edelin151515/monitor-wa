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
    except Exception as e:
        print(f"Error Dashboard: {e}")
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
    
    # 1. Kirim Lewat Fonnte
    headers = {'Authorization': FONNTE_TOKEN}
    data = {'target': phone, 'message': message}
    try:
        requests.post('https://api.fonnte.com/send', headers=headers, data=data)
    except Exception as e:
        print(f"Error Fonnte: {e}")
    
    # 2. Simpan Database
    try:
        supabase.table('chats').insert({
            "customer_phone": phone,
            "message": message,
            "direction": "outbound",
            "status": "sent"
        }).execute()
    except Exception as e:
        print(f"Error Simpan DB: {e}")
    
    return redirect(url_for('dashboard'))

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    # Cek data dari JSON atau Form (Fonnte kadang kirim beda-beda)
    data = request.json
    if not data:
        data = request.form
    
    print(f"WEBHOOK MASUK: {data}") # Ini akan muncul di Logs Vercel

    if not data:
        return "No Data", 200

    sender = data.get('sender')
    message = data.get('message')
    status = data.get('status')
    
    # KASUS: Ada Balasan Pesan (Inbound)
    if sender and message and not status:
        try:
            print(f"Menyimpan pesan dari {sender}...")
            supabase.table('chats').insert({
                "customer_phone": sender,
                "message": message,
                "direction": "inbound",
                "status": "received"
            }).execute()
            print("Berhasil simpan!")
        except Exception as e:
            print(f"GAGAL SIMPAN KE DB: {e}")

    # KASUS: Update Status Baca (READ)
    # (Catatan: Status READ kadang tidak membawa nomor pengirim, jadi ini best-effort)
    if status == 'READ':
        print(f"Status READ diterima untuk ID: {data.get('id')}")
        # Logika update status bisa ditambahkan nanti jika ID disimpan

    return "OK", 200
