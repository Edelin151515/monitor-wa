from flask import Flask, request, render_template, redirect, url_for
from supabase import create_client, Client
import requests
import os
from datetime import datetime
import json
import traceback # Import untuk melacak error

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

    sent_count = sum(1 for c in chats if c.get('direction') == 'outbound')
    read_count = sum(1 for c in chats if c.get('status') and 'read' in c.get('status').lower())
    reply_count = sum(1 for c in chats if c.get('direction') == 'inbound')
            
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
    except Exception as e:
        print(f"Error Simpan: {e}")
    
    return redirect(url_for('dashboard'))

@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    # WRAP SEMUA DALAM TRY AGAR TIDAK ERROR 500
    try:
        data = request.json
        if not data:
            data = request.form
        
        if not data: return "No Data", 200

        print(f"DEBUG DATA MASUK: {data}") # Cek log ini nanti

        remote_jid = data.get('remoteJid')
        sender = data.get('sender')
        message = data.get('message')
        status = data.get('status') or data.get('state')
        
        # Logika Penentuan Nomor
        nomor_masuk = None
        if remote_jid:
            nomor_masuk = str(remote_jid).split('@')[0]
        elif sender:
            nomor_masuk = sender
            
        nomor_masuk = normalize_phone(nomor_masuk)
        
        # KASUS 1: UPDATE STATUS BACA (READ)
        if status and 'read' in str(status).lower() and nomor_masuk:
            print(f"Mencoba update READ untuk nomor: {nomor_masuk}")
            
            # Cari pesan TERAKHIR ke nomor ini
            last_msg = supabase.table('chats').select('id')\
                .eq('customer_phone', nomor_masuk)\
                .eq('direction', 'outbound')\
                .neq('status', 'read')\
                .order('created_at', desc=True)\
                .limit(1).execute()
            
            if last_msg.data:
                msg_id = last_msg.data[0]['id']
                supabase.table('chats').update({'status': 'read'}).eq('id', msg_id).execute()
                print(f"BERHASIL UPDATE READ ID: {msg_id}")
            else:
                print(f"SKIP: Tidak ada pesan pending untuk {nomor_masuk}")

        # KASUS 2: PESAN BALASAN MASUK
        if nomor_masuk and message and (not status or status == 'received'):
            # Cek duplikasi
            existing = supabase.table('chats').select('id').eq('message', message).eq('customer_phone', nomor_masuk).limit(1).execute()
            if not existing.data:
                supabase.table('chats').insert({
                    "customer_phone": nomor_masuk,
                    "message": message,
                    "direction": "inbound",
                    "status": "received"
                }).execute()
                print("Balasan tersimpan.")

    except Exception as e:
        # INI PENTING: Kalau error, print errornya tapi JANGAN bikin server mati (tetap return 200)
        print(f"CRASH DI WEBHOOK: {e}")
        traceback.print_exc() # Print detail error
        return "Error Handled", 200

    return "OK", 200
