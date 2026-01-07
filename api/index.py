from flask import Flask, request, render_template, redirect, url_for
from supabase import create_client, Client
import requests
import os
from datetime import datetime
import traceback

app = Flask(__name__, template_folder='../templates')

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
FONNTE_TOKEN = os.environ.get("FONNTE_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def normalize_phone(phone):
    if not phone: return ""
    phone = str(phone).strip().replace('-', '').replace(' ', '').replace('+', '')
    if phone.startswith('0'):
        return '62' + phone[1:]
    return phone

@app.route('/')
def dashboard():
    today = datetime.now().strftime('%Y-%m-%d')
    try:
        response = supabase.table('chats').select("*").gte('created_at', today).order('created_at', desc=True).execute()
        chats = response.data
    except Exception as e:
        print(f"Error Dashboard: {e}")
        chats = []

    # --- PERHITUNGAN MURNI (STRICT) ---
    sent_count = 0
    read_count = 0
    reply_count = 0
    
    for c in chats:
        direction = c.get('direction')
        status = str(c.get('status')).lower()
        
        # Hitung Pesan Keluar
        if direction == 'outbound':
            sent_count += 1
            # HANYA hitung 'Read' jika statusnya benar-benar 'read' atau '3'
            # Tidak ada asumsi "kalau balas berarti baca"
            if 'read' in status or '3' in status:
                read_count += 1
                
        # Hitung Balasan Masuk
        elif direction == 'inbound':
            reply_count += 1
            
    stats = {'sent': sent_count, 'read': read_count, 'replied': reply_count}
    replies = [c for c in chats if c.get('direction') == 'inbound']
    
    return render_template('dashboard.html', stats=stats, replies=replies)

@app.route('/send', methods=['POST'])
def send_message():
    raw_phone = request.form.get('phone')
    message = request.form.get('message')
    phone = normalize_phone(raw_phone)
    
    headers = {'Authorization': FONNTE_TOKEN}
    data = {'target': phone, 'message': message}
    
    fonnte_id = None
    status_awal = "sent"
    
    # Kirim ke Fonnte
    try:
        req = requests.post('https://api.fonnte.com/send', headers=headers, data=data)
        res_json = req.json()
        # Ambil ID Fonnte (Penting untuk tracking status Read nanti)
        if 'id' in res_json and isinstance(res_json['id'], list) and len(res_json['id']) > 0:
            fonnte_id = res_json['id'][0]
        if not fonnte_id and 'data' in res_json and isinstance(res_json['data'], list) and len(res_json['data']) > 0:
             fonnte_id = res_json['data'][0].get('id')
    except:
        pass
    
    # Simpan ke Database
    try:
        supabase.table('chats').insert({
            "customer_phone": phone, 
            "message": message,
            "direction": "outbound",
            "status": status_awal,
            "fonnte_id": fonnte_id
        }).execute()
    except Exception as e:
        print(f"Error Simpan DB: {e}")
    
    return redirect(url_for('dashboard'))

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    try:
        data = request.json or request.form
        if not data: return "No Data", 200

        print(f"WEBHOOK: {data}")

        msg_id = data.get('stateid') or data.get('id')
        raw_status = data.get('state') or data.get('status')
        
        # Translate kode angka Fonnte
        final_status = str(raw_status)
        if str(raw_status) == '2': final_status = 'delivered' # Terkirim (Abu)
        if str(raw_status) == '3': final_status = 'read'      # Dibaca (Biru)

        # KASUS A: UPDATE STATUS (Jika ada ID)
        if msg_id and raw_status:
            print(f"Update Status ID {msg_id} -> {final_status}")
            supabase.table('chats').update({'status': final_status}).eq('fonnte_id', msg_id).execute()

        # KASUS B: PESAN BALASAN MASUK
        sender = data.get('sender')
        message = data.get('message')
        
        if sender and message:
            sender = normalize_phone(sender)
            existing = supabase.table('chats').select('id').eq('message', message).eq('customer_phone', sender).limit(1).execute()
            if not existing.data:
                supabase.table('chats').insert({
                    "customer_phone": sender,
                    "message": message,
                    "direction": "inbound",
                    "status": "received"
                }).execute()
                
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()

    return "OK", 200
