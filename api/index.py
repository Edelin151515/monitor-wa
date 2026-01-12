from flask import Flask, request, render_template, redirect, url_for
from supabase import create_client, Client
import requests
import os
from datetime import datetime, timedelta
import traceback

app = Flask(__name__, template_folder='../templates')

# --- KONFIGURASI ---
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
    
    chats_daily = []
    chats_all_history = []

    try:
        # --- QUERY A: Data Statistik Harian (Volume Kerja Hari Ini) ---
        start_stats = f"{selected_date_str}T00:00:00"
        end_stats = f"{selected_date_str}T23:59:59"
        
        resp_stats = supabase.table('chats').select("*")\
            .gte('created_at', start_stats)\
            .lte('created_at', end_stats)\
            .order('created_at', desc=True)\
            .execute()
        chats_daily = resp_stats.data

        # --- QUERY B: History (Untuk Cek "Siapa yang belum balas?") ---
        # Kita ambil 2000 data terakhir untuk cek status akhir setiap nomor
        resp_leads = supabase.table('chats').select("*")\
            .order('created_at', desc=True)\
            .limit(2000)\
            .execute() 
        chats_all_history = resp_leads.data

    except Exception as e:
        print(f"Error Dashboard Data: {e}")

    # --- HITUNG VOLUME HARIAN (Total Traffic) ---
    sent_count = 0
    reply_count = 0
    
    for c in chats_daily:
        if c.get('direction') == 'inbound':
            reply_count += 1
        elif c.get('direction') == 'outbound':
            sent_count += 1

    # --- LOGIKA SELISIH (TARGET FOLLOW-UP) ---
    # Mencari orang yang interaksi TERAKHIR-nya adalah "Kita Kirim" (Outbound)
    # dan belum ada balasan sesudahnya.
    
    read_leads = []
    latest_per_phone = {}
    
    # 1. Cari pesan paling ujung (terakhir) untuk setiap nomor HP
    for c in chats_all_history:
        phone = c.get('customer_phone')
        if phone and phone not in latest_per_phone:
            latest_per_phone[phone] = c

    # 2. Filter: Ambil yang statusnya Outbound (Belum dibalas) & Tanggalnya sesuai Filter
    for phone, last_msg in latest_per_phone.items():
        # Syarat 1: Arahnya Outbound (Artinya pesan terakhir dari KITA, bukan dari nasabah)
        if last_msg.get('direction') == 'outbound':
            status = str(last_msg.get('status')).lower()
            
            # Ambil tanggal pesan terakhir (YYYY-MM-DD)
            created_at = last_msg.get('created_at', '') 
            msg_date = created_at.split('T')[0] if 'T' in created_at else created_at
            
            # Syarat 2: Pesan terakhirnya dibuat pada TANGGAL YANG DIPILIH
            # (Artinya: Hari itu kita chat dia, dan sampai sekarang dia belum balas)
            is_date_match = (msg_date == selected_date_str)
            
            # Syarat 3: Status bukan failed
            is_not_failed = 'failed' not in status

            if is_date_match and is_not_failed:
                # Label status biar cantik
                label_status = 'Menunggu Respon'
                if 'read' in status or '3' in status: label_status = 'Sudah Baca (Ghosting)'
                elif 'delivered' in status or '2' in status: label_status = 'Terkirim (Sampai)'
                
                read_leads.append({
                    'phone': phone,
                    'msg': last_msg.get('message'),
                    'status': label_status, # Pakai label yang sudah dipercantik
                    'raw_status': status,
                    'time': last_msg.get('created_at')
                })

    # --- UPDATE STATISTIK KOTAK TENGAH ---
    stats = {
        'sent': sent_count,      # Total kita kirim hari ini
        'valid': len(read_leads), # <--- INI MODIFIKASINYA: Menampilkan SELISIH (Target Follow-Up)
        'replied': reply_count   # Total balasan masuk hari ini
    }
    
    # Kirim data ke HTML
    # Note: Variable 'potential' di HTML diisi dengan 'read_leads'
    replies = [c for c in chats_daily if c.get('direction') == 'inbound']
    
    return render_template('dashboard.html', 
                           stats=stats, 
                           replies=replies, 
                           potential=read_leads, 
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
        if not data: return "OK", 200

        msg_id = data.get('stateid') or data.get('id')
        raw_status = data.get('state') or data.get('status')
        target_phone = data.get('target')

        # 1. UPDATE STATUS (Read/Delivered)
        if raw_status is not None:
            final_status = str(raw_status).lower()
            if final_status == '2': final_status = 'delivered'
            elif final_status == '3': final_status = 'read'
            elif final_status == '1': final_status = 'sent'

            updated = False
            # Cara A: Update pakai ID Fonnte (Paling Akurat)
            if msg_id:
                try:
                    res = supabase.table('chats').update({'status': final_status})\
                        .eq('fonnte_id', str(msg_id).strip()).execute()
                    if res.data and len(res.data) > 0: updated = True
                except: pass

            # Cara B: Update pakai Nomor HP (Fallback jika ID gagal/tidak ada)
            if not updated and target_phone:
                try:
                    target_normalized = normalize_phone(target_phone)
                    supabase.table('chats').update({'status': final_status})\
                        .eq('customer_phone', target_normalized)\
                        .eq('direction', 'outbound')\
                        .order('created_at', desc=True)\
                        .limit(1).execute()
                except: pass

        # 2. SIMPAN PESAN MASUK (Inbound)
        sender = data.get('sender')
        message = data.get('message')
        
        if sender and message:
            sender = normalize_phone(sender)
            try:
                # Cek duplikasi biar gak double
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
        print(f"Webhook Error: {e}")
        traceback.print_exc()

    return "OK", 200
