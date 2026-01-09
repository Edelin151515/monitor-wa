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
    
    try:
        # 2. Ambil data Statistik (Hari Ini)
        date_obj = datetime.strptime(selected_date_str, '%Y-%m-%d')
        start_stats = f"{selected_date_str}T00:00:00"
        end_stats = f"{selected_date_str}T23:59:59"
        
        resp_stats = supabase.table('chats').select("*")\
            .gte('created_at', start_stats)\
            .lte('created_at', end_stats)\
            .order('created_at', desc=True)\
            .execute()
        chats_daily = resp_stats.data

        # 3. Ambil data Leads (Seluruh data 3 hari terakhir agar sequence chat terbaca utuh)
        three_days_ago = (date_obj - timedelta(days=3)).strftime('%Y-%m-%d')
        start_leads = f"{three_days_ago}T00:00:00"
        
        resp_leads = supabase.table('chats').select("*")\
            .gte('created_at', start_leads)\
            .lte('created_at', end_stats)\
            .order('created_at', desc=True)\
            .execute()
        chats_all = resp_leads.data
    except Exception as e:
        print(f"Error Dashboard Data: {e}")
        chats_daily = []
        chats_all = []

    # --- LOGIKA POTENSIAL BARU ---
    sent_count = 0
    delivered_count = 0 # Menggantikan "Dibaca" yg error
    reply_count = 0
    
    nomor_yang_balas = set()
    for c in chats_daily:
        if c.get('direction') == 'inbound':
            reply_count += 1
            if c.get('customer_phone'):
                nomor_yang_balas.add(c.get('customer_phone'))

    sent_count = 0
    delivered_count = 0
    for c in chats_daily:
        if c.get('direction') == 'outbound':
            sent_count += 1
            status = str(c.get('status')).lower()
            nomor = c.get('customer_phone')
            if any(s in status for s in ['delivered', '2', 'read', '3']) or nomor in nomor_yang_balas:
                delivered_count += 1

    # --- LOGIKA FOLLOW-UP (SEQUENCE AWARE) ---
    # Kita cari nomor yang pesan TERAKHIRNYA adalah outbound BERSTATUS READ
    read_leads = []
    latest_per_phone = {}
    
    # chats_all sudah terurut descending (terbaru di atas)
    for c in chats_all:
        phone = c.get('customer_phone')
        if phone and phone not in latest_per_phone:
            latest_per_phone[phone] = c

    for phone, last_msg in latest_per_phone.items():
        if last_msg.get('direction') == 'outbound':
            status = str(last_msg.get('status')).lower()
            if 'read' in status or '3' in status:
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
    
    replies = [c for c in chats_daily if c.get('direction') == 'inbound']
    
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

            target_phone = data.get('target') # Beberapa webhook status juga mengirim target
            
            # Update status di database berdasarkan fonnte_id
            try:
                msg_id_str = str(msg_id).strip()
                # Cara 1: Update berdasarkan fonnte_id saja
                query = supabase.table('chats').update({'status': final_status}).eq('fonnte_id', msg_id_str)
                query.execute()
                
                # Cara 2: Jika ada target, pastikan update ke nomor yang benar (tambahan keamanan)
                if target_phone:
                    target_normalized = normalize_phone(target_phone)
                    supabase.table('chats').update({'status': final_status})\
                        .eq('customer_phone', target_normalized)\
                        .eq('direction', 'outbound')\
                        .order('created_at', desc=True)\
                        .limit(1).execute()
                        
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
