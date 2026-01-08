from flask import Flask, request, render_template, redirect, url_for
from supabase import create_client, Client
import requests
import os
from datetime import datetime, timedelta
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
    # 1. AMBIL TANGGAL DARI PILIHAN USER (Default: Hari Ini)
    selected_date_str = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    
    # Hitung batas waktu untuk filter database (Pakai Offset WIB +07:00)
    try:
        date_obj = datetime.strptime(selected_date_str, '%Y-%m-%d')
        next_day_obj = date_obj + timedelta(days=1)
        
        # Format string ISO8601 dengan offset +07:00
        start_filter = f"{selected_date_str}T00:00:00+07:00"
        end_filter = f"{next_day_obj.strftime('%Y-%m-%d')}T00:00:00+07:00"
    except:
        selected_date_str = datetime.now().strftime('%Y-%m-%d')
        start_filter = f"{selected_date_str}T00:00:00+07:00"
        end_filter = f"{(datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')}T00:00:00+07:00"

    try:
        # Query: Ambil data HANYA pada tanggal yang dipilih (sesuai WIB)
        response = supabase.table('chats').select("*")\
            .gte('created_at', start_filter)\
            .lt('created_at', end_filter)\
            .order('created_at', desc=True)\
            .execute()
        chats = response.data
    except Exception as e:
        print(f"Error Dashboard: {e}")
        chats = []

    # --- LOGIKA POTENSIAL BARU ---
    sent_count = 0
    delivered_count = 0 # Menggantikan "Dibaca" yg error
    reply_count = 0
    
    # Set untuk menampung nomor yang membalas
    nomor_yang_balas = set()
    for c in chats:
        if c.get('direction') == 'inbound':
            reply_count += 1
            if c.get('customer_phone'):
                nomor_yang_balas.add(c.get('customer_phone'))

    # List untuk Nasabah Potensial (Hanya yang sudah baca, tapi belum balas)
    read_leads = []

    for c in chats:
        if c.get('direction') == 'outbound':
            sent_count += 1
            status = str(c.get('status')).lower()
            nomor = c.get('customer_phone')
            
            # Cek Status Terkirim (Valid)
            is_read = 'read' in status or '3' in status
            # is_delivered: pesan sudah sampai ke HP (Centang 2)
            is_delivered = 'delivered' in status or '2' in status or is_read or nomor in nomor_yang_balas
            
            if is_delivered:
                delivered_count += 1
                
                # JIKA sudah dibaca DAN belum membalas
                if is_read and nomor not in nomor_yang_balas:
                    lead_data = {'phone': nomor, 'msg': c.get('message'), 'status': status}
                    if not any(d['phone'] == nomor for d in read_leads):
                        read_leads.append(lead_data)

    stats = {
        'sent': sent_count, 
        'valid': delivered_count, 
        'replied': reply_count
    }
    
    replies = [c for c in chats if c.get('direction') == 'inbound']
    
    return render_template('dashboard.html', 
                          stats=stats, 
                          replies=replies, 
                          read_leads=read_leads, 
                          selected_date=selected_date_str)

@app.route('/send', methods=['POST'])
def send_message():
    raw_phone = request.form.get('phone')
    message = request.form.get('message')
    phone = normalize_phone(raw_phone)
    
    headers = {'Authorization': FONNTE_TOKEN}
    data = {'target': phone, 'message': message}
    
    fonnte_id = None
    status_awal = "sent"
    
    try:
        req = requests.post('https://api.fonnte.com/send', headers=headers, data=data)
        res_json = req.json()
        if 'id' in res_json and isinstance(res_json['id'], list) and len(res_json['id']) > 0:
            fonnte_id = res_json['id'][0]
        if not fonnte_id and 'data' in res_json and isinstance(res_json['data'], list) and len(res_json['data']) > 0:
             fonnte_id = res_json['data'][0].get('id')
    except:
        pass
    
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
        # Gunakan get_json(silent=True) agar tidak crash jika payload bukan JSON
        data = request.get_json(silent=True) or request.form or request.args
        if not data: 
            return "OK", 200

        # 1. LOGIKA UPDATE STATUS (webhook status)
        msg_id = data.get('stateid') or data.get('id')
        raw_status = data.get('state') or data.get('status')
        
        if msg_id and raw_status is not None:
            final_status = str(raw_status).lower()
            if final_status == '2': final_status = 'delivered'
            elif final_status == '3': final_status = 'read'
            elif final_status == '0': final_status = 'pending'
            elif final_status == '1': final_status = 'sent'

            # Update status di database berdasarkan fonnte_id
            try:
                # Coba update dengan string ID
                supabase.table('chats').update({'status': final_status}).eq('fonnte_id', str(msg_id)).execute()
                # Jika ID mungkin angka
                if str(msg_id).isdigit():
                    supabase.table('chats').update({'status': final_status}).eq('fonnte_id', int(msg_id)).execute()
            except Exception as e:
                print(f"Error Update Status: {e}")

        # 2. LOGIKA PESAN MASUK (webhook message)
        sender = data.get('sender')
        message = data.get('message')
        
        if sender and message:
            sender = normalize_phone(sender)
            # Cek apakah pesan ini sudah pernah disimpan (hindari duplikat dari webhook)
            try:
                existing = supabase.table('chats').select('id')\
                    .eq('message', message)\
                    .eq('customer_phone', sender)\
                    .eq('direction', 'inbound')\
                    .limit(1).execute()
                
                if not existing.data:
                    supabase.table('chats').insert({
                        "customer_phone": sender,
                        "message": message,
                        "direction": "inbound",
                        "status": "received"
                    }).execute()
            except Exception as e:
                print(f"Error Save Inbound: {e}")
                
    except Exception as e:
        # Cetak error ke log server tapi tetap balas 200 ke Fonnte
        print(f"Webhook Crash: {e}")
        traceback.print_exc()

    # Selalu balas 200 OK agar Fonnte tidak menganggap gagal dan mengirim ulang terus-menerus
    return "OK", 200
