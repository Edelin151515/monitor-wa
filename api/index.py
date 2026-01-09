from flask import Flask, request, render_template, redirect, url_for
from supabase import create_client, Client
import requests
import os
from datetime import datetime
import traceback

app = Flask(__name__, template_folder='../templates')

# Konfigurasi ENV
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
FONNTE_TOKEN = os.environ.get("FONNTE_TOKEN")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Normalize nomor HP Indonesia
def normalize_phone(phone):
    if not phone:
        return ""
    phone = str(phone).strip().replace('-', '').replace(' ', '').replace('+', '')
    if phone.startswith('0'):
        return '62' + phone[1:]
    return phone

@app.route('/')
def dashboard():
    selected_date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    start = f"{selected_date}T00:00:00"
    end = f"{selected_date}T23:59:59"

    try:
        # Ambil chat sesuai tanggal filter
        res = supabase.table('chats').select("*")\
            .gte('created_at', start)\
            .lte('created_at', end)\
            .order('created_at', desc=True)\
            .execute()
        chats = res.data

        # Ambil history panjang untuk cek status terakhir per nomor
        hist = supabase.table('chats').select("*")\
            .order('created_at', desc=True)\
            .limit(2000)\
            .execute()
        chats_history = hist.data

    except Exception as e:
        print("Dashboard error:", e)
        chats = []
        chats_history = []

    # --- METRIK ---
    total_kirim = 0
    terkirim = 0
    dibalas_nomor = set()
    followup = {}
    latest_outbound = {}

    # Hitung metrik harian
    for c in chats:
        phone = c.get('customer_phone')
        direction = c.get('direction')
        status = str(c.get('status')).lower()

        if phone and direction == 'outbound' and phone not in latest_outbound:
            latest_outbound[phone] = c

        if direction == 'inbound' and phone:
            dibalas_nomor.add(phone)

        # Total Kirim hanya jika sudah dapat event (exclude pending)
        if direction == 'outbound' and status in ['sent', 'delivered', 'read', '1', '2', '3']:
            total_kirim += 1

        # Terkirim hanya delivered + read
        if direction == 'outbound' and status in ['delivered', 'read', '2', '3']:
            terkirim += 1

    # Cari outbound terakhir per nomor dari history
    for c in chats_history:
        phone = c.get('customer_phone')
        if phone and c.get('direction') == 'outbound' and phone not in latest_outbound:
            latest_outbound[phone] = c

    # Siapkan follow-up dari outbound terakhir
    for phone, last in latest_outbound.items():
        st = str(last.get('status')).lower()
        if st in ['delivered', 'read', '2', '3']:
            followup[phone] = {
                'phone': phone,
                'msg': last.get('message'),
                'time': last.get('created_at'),
                'status': st
            }

    # Hapus nomor dari follow-up jika sudah balas setelah outbound terakhir
    for c in chats_history:
        phone = c.get('customer_phone')
        if phone in followup and c.get('direction') == 'inbound':
            if c.get('created_at') > followup[phone]['time']:
                del followup[phone]

    stats = {
        'total_kirim': total_kirim,
        'terkirim': terkirim,
        'dibalas': list(dibalas_nomor),
        'followup': list(followup.values())
    }

    return render_template('dashboard.html',
                           stats=stats,
                           selected_date=selected_date)

@app.route('/send', methods=['POST'])
def send_message():
    raw_phone = request.form.get('phone')
    message = request.form.get('message')
    phone = normalize_phone(raw_phone)

    # Jangan set langsung "sent", tunggu webhook
    status_awal = "pending"

    headers = {'Authorization': FONNTE_TOKEN}
    data = {'target': phone, 'message': message}

    fonnte_id = None

    try:
        req = requests.post('https://api.fonnte.com/send', headers=headers, data=data)
        res_json = req.json()
        if 'id' in res_json and isinstance(res_json['id'], list) and len(res_json['id']) > 0:
            fonnte_id = str(res_json['id'][0]).strip()
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
        print("DB Save error:", e)

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

        final_status = str(raw_status).lower() if raw_status else None
        if final_status == '2': final_status = 'delivered'
        elif final_status == '3': final_status = 'read'
        elif final_status == '1': final_status = 'sent'
        elif final_status == '0': final_status = 'pending'

        updated = False

        # Update berdasarkan fonnte_id dulu
        if msg_id:
            try:
                res = supabase.table('chats').update({'status': final_status})\
                    .eq('fonnte_id', str(msg_id).strip()).execute()
                if res.data:
                    updated = True
            except:
                pass

        # Fallback update berdasarkan nomor outbound terakhir
        if not updated and target_phone:
            try:
                supabase.table('chats').update({'status': final_status})\
                    .eq('customer_phone', normalize_phone(target_phone))\
                    .eq('direction', 'outbound')\
                    .order('created_at', desc=True)\
                    .limit(1).execute()
            except:
                pass

        # Simpan inbound message (balasan nasabah)
        sender = normalize_phone(data.get('sender'))
        message = data.get('message')

        if sender and message:
            try:
                exist = supabase.table('chats').select("id")\
                    .eq("customer_phone", sender)\
                    .eq("message", message)\
                    .eq("direction", "inbound")\
                    .limit(1).execute()

                if not exist.data:
                    supabase.table('chats').insert({
                        "customer_phone": sender,
                        "message": message,
                        "direction": "inbound",
                        "status": "received"
                    }).execute()
            except:
                pass

    except Exception as e:
        print("Webhook error:", e)
        traceback.print_exc()

    return "OK", 200

if __name__ == "__main__":
    app.run(debug=True, port=5000)
