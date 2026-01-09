from flask import Flask, request, render_template, redirect, url_for
from supabase import create_client, Client
import requests
import os
from datetime import datetime, timedelta
import traceback

app = Flask(__name__, template_folder='../templates')

# Konfigurasi Environment Variable
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
    
    try:
        # --- QUERY A: Data Statistik (Hanya Hari Ini / Tanggal Terpilih) ---
        start_stats = f"{selected_date_str}T00:00:00"
        end_stats = f"{selected_date_str}T23:59:59"
        
        resp_stats = supabase.table('chats').select("*")\
            .gte('created_at', start_stats)\
            .lte('created_at', end_stats)\
            .order('created_at', desc=True)\
            .execute()
        chats_daily = resp_stats.data

        # --- QUERY B: Data History (Untuk Follow Up) ---
        resp_leads = supabase.table('chats').select("*")\
            .order('created_at', desc=True)\
            .limit(2000)\
            .execute() 
        chats_all_history = resp_leads.data

        # --- QUERY C: Pesan Masuk (Realtime/Global) ---
        resp_inbound = supabase.table('chats').select("*")\
            .eq('direction', 'inbound')\
            .order('created_at', desc=True)\
            .limit(20)\
            .execute()
        latest_replies = resp_inbound.data

    except Exception as e:
        print(f"Error Dashboard Data: {e}")
        chats_daily = []
        chats_all_history = []
        latest_replies = []

    # --- HITUNG STATISTIK (Hanya untuk tanggal yang dipilih) ---
    sent_count = 0      # Total Kirim
    delivered_count = 0 # Terkirim (Valid)
    reply_count = 0     # Dibalas
    
    # Hitung dulu siapa saja yang membalas hari ini (untuk validasi silang)
    nomor_yang_balas_hari_ini = set()
    for c in chats_daily:
        if c.get('direction') == 'inbound':
            reply_count += 1 # ✅ Dibalas = Hitung pesan masuk
            if c.get('customer_phone'):
                nomor_yang_balas_hari_ini.add(c.get('customer_phone'))

    for c in chats_daily:
        if c.get('direction') == 'outbound':
            sent_count += 1 # ✅ Total Kirim = Semua pesan keluar
            
            status = str(c.get('status')).lower()
            nomor = c.get('customer_phone')
            
            # ✅ Terkirim = Delivered (2) + Read (3). 
            # EXCLUDE: Sent (1) / Pending (0)
            # Logika tambahan: Jika orangnya sudah balas, otomatis dianggap valid/terkirim
            if any(s in status for s in ['delivered', '2', 'read', '3']) or nomor in nomor_yang_balas_hari_ini:
                delivered_count += 1

    # --- LOGIKA TARGET FOLLOW-UP (FILTER BY DATE) ---
    read_leads = []
    latest_per_phone = {}
    
    # Ambil chat terakhir per nomor
    for c in chats_all_history:
        phone = c.get('customer_phone')
        if phone and phone not in latest_per_phone:
            latest_per_phone[phone] = c

    for phone, last_msg in latest_per_phone.items():
        if last_msg.get('direction') == 'outbound':
            status = str(last_msg.get('status')).lower()
            
            created_at = last_msg.get('created_at', '') 
            msg_date = created_at.split('T')[0] if 'T' in created_at else created_at
            
            # ✅ Follow-Up Logic:
            # 1. Status HARUS 'read' (3) ATAU 'delivered' (2).
            # 2. Status 'sent' (1) DIBUANG dari list ini (karena wa mungkin offline).
            is_status_ok = any(s in status for s in ['read', '3', 'delivered', '2'])
            
            # 3. Tanggal chat terakhir harus sama dengan filter tanggal dashboard
            is_date_match = (msg_date == selected_date_str)

            if is_status_ok and is_date_match:
                read_leads.append({
                    'phone': phone,
                    'msg': last_msg.get('message'),
                    'status': status,
                    'time': last_msg.get('created_at')
                })

    stats = {
        'sent': sent_count, 
        'valid': delivered_count, 
        'replied': reply_count
    }
    
    return render_template('dashboard.html', 
                           stats=stats, 
                           replies=latest_replies, 
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
            fonnte_id = str(res_json['id'][0]).strip()
        if not fonnte_id and 'data' in res_json and isinstance(res_json['data'], list) and len(res_json['data']) > 0:
             fonnte_id = str(res_json['data'][0].get('id', '')).strip()
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
        data = request.get_json(silent=True) or request.form or request.args
        if not data: 
            return "OK", 200

        msg_id = data.get('stateid') or data.get('id')
        raw_status = data.get('state') or data.get('status')
        target_phone = data.get('target')

        if raw_status is not None:
            final_status = str(raw_status).lower()
            if final_status == '2': final_status = 'delivered'
            elif final_status == '3': final_status = 'read'
            elif final_status == '0': final_status = 'pending'
            elif final_status == '1': final_status = 'sent'

            updated = False
            
            # 1. Update by ID
            if msg_id:
                try:
                    msg_id_str = str(msg_id).strip()
                    res = supabase.table('chats').update({'status': final_status})\
                        .eq('fonnte_id', msg_id_str).execute()
                    if res.data and len(res.data) > 0:
                        updated = True
                except Exception as e:
                    print(f"Update ID Error: {e}")

            # 2. Update Fallback
            if not updated and target_phone:
                try:
                    target_normalized = normalize_phone(target_phone)
                    supabase.table('chats').update({'status': final_status})\
                        .eq('customer_phone', target_normalized)\
                        .eq('direction', 'outbound')\
                        .in_('status', ['sent', 'pending', 'unknown'])\
                        .order('created_at', desc=True)\
                        .limit(1).execute()
                except Exception as e:
                    print(f"Update Fallback Error: {e}")

        sender = data.get('sender')
        message = data.get('message')
        
        if sender and message:
            sender = normalize_phone(sender)
            try
