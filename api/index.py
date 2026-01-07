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
        response = supabase.table('chats').select("*").gte('created_at', today).order('created_at', desc=True).execute()
        chats = response.data
    except Exception as e:
        print(f"Error Dashboard: {e}")
        chats = []

    # Hitung Statistik (Support huruf besar/kecil)
    sent_count = 0
    read_count = 0
    reply_count = 0
    
    for c in chats:
        direction = c.get('direction')
        status = c.get('status', '').lower() # Ubah ke huruf kecil semua biar aman
        
        if direction == 'outbound':
            sent_count += 1
            if 'read' in status: # Cek jika ada kata 'read'
                read_count += 1
        elif direction == 'inbound':
            reply_count += 1
            
    stats = {'sent': sent_count, 'read': read_count, 'replied': reply_count}
    replies = [c for c in chats if c.get('direction') == 'inbound']
    
    return render_template('dashboard.html', stats=stats, replies=replies)

@app.route('/send', methods=['POST'])
def send_message():
    phone = request.form.get('phone')
    message = request.form.get('message')
    
    headers = {'Authorization': FONNTE_TOKEN}
    data = {'target': phone, 'message': message}
    try:
        requests.post('https://api.fonnte.com/send', headers=headers, data=data)
    except:
        pass
    
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

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    data = request.json or request.form
    if not data: return "No Data", 200

    print(f"WEBHOOK MASUK: {data}") # Cek Log Vercel kalau penasaran

    # 1. Ambil Data Penting (Fonnte kadang kirim 'status', kadang 'state')
    sender = data.get('sender')
    message = data.get('message')
    status = data.get('status') or data.get('state') # <- INI KUNCINYA
    
    # 2. Logika Update Status (READ/DELIVERED)
    if status:
        # Cari pesan terakhir ke nomor ini
        device_phone = data.get('device') # Nomor HP kita
        # Kita asumsikan update status untuk pesan terakhir yg kita kirim
        # (Fitur ini terbatas karena Fonnte versi gratis tidak kirim ID pesan spesifik)
        if status.lower() == 'read':
             # Opsional: Update database jika mau (agak tricky tanpa ID)
             pass 

    # 3. Logika Pesan Masuk (BALASAN)
    # Pastikan ini bukan status update (biasanya status update gak ada 'message' user)
    if sender and message and (not status or status == 'received'):
        try:
            # Cek dulu supaya tidak double input
            existing = supabase.table('chats').select('id').eq('message', message).eq('customer_phone', sender).limit(1).execute()
            if not existing.data:
                supabase.table('chats').insert({
                    "customer_phone": sender,
                    "message": message,
                    "direction": "inbound",
                    "status": "received"
                }).execute()
                print("Balasan tersimpan!")
        except Exception as e:
            print(f"Gagal simpan balasan: {e}")

    return "OK", 200
