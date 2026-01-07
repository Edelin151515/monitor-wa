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

# FUNGSI PENTING: Ubah 08 jadi 62
def normalize_phone(phone):
    if not phone: return ""
    phone = str(phone).strip().replace('-', '').replace(' ', '')
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
    # Hitung Read lebih fleksibel
    read_count = sum(1 for c in chats if c.get('status') and 'read' in c.get('status').lower())
    reply_count = sum(1 for c in chats if c.get('direction') == 'inbound')
            
    stats = {'sent': sent_count, 'read': read_count, 'replied': reply_count}
    replies = [c for c in chats if c.get('direction') == 'inbound']
    
    return render_template('dashboard.html', stats=stats, replies=replies)

@app.route('/send', methods=['POST'])
def send_message():
    raw_phone = request.form.get('phone')
    message = request.form.get('message')
    
    # 1. FORMAT NOMOR DULU (Supaya cocok dengan laporan Fonnte nanti)
    phone = normalize_phone(raw_phone)
    
    headers = {'Authorization': FONNTE_TOKEN}
    data = {'target': phone, 'message': message}
    
    try:
        requests.post('https://api.fonnte.com/send', headers=headers, data=data)
    except:
        pass
    
    try:
        # Simpan dengan nomor yang sudah diformat (62...)
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
    data = request.json or request.form
    if not data: return "No Data", 200

    print(f"WEBHOOK RAW: {data}")

    # Ambil nomor dari berbagai kemungkinan field Fonnte
    sender = data.get('sender')
    remote_jid = data.get('remoteJid')
    
    nomor_masuk = None
    if sender:
        nomor_masuk = sender
    elif remote_jid:
        nomor_masuk = remote_jid.split('@')[0] # Ambil angka depan dari 628xx@s.whatsapp.net
        
    # Pastikan formatnya bersih
    nomor_masuk = normalize_phone(nomor_masuk)
    
    message = data.get('message')
    status = data.get('status') or data.get('state')

    # KASUS 1: UPDATE STATUS BACA (READ)
    if status and 'read' in status.lower() and nomor_masuk:
        print(f"Mencocokkan laporan READ dari: {nomor_masuk}")
        try:
            # Cari pesan TERAKHIR ke nomor (62...) ini
            last_msg = supabase.table('chats').select('id')\
                .eq('customer_phone', nomor_masuk)\
                .eq('direction', 'outbound')\
                .neq('status', 'read')\
                .order('created_at', desc=True)\
                .limit(1).execute()
            
            if last_msg.data:
                msg_id = last_msg.data[0]['id']
                supabase.table('chats').update({'status': 'read'}).eq('id', msg_id).execute()
                print(f"BERHASIL: Pesan ID {msg_id} status jadi READ")
            else:
                print("GAGAL: Tidak menemukan pesan outbound untuk nomor ini di DB.")
        except Exception as e:
            print(f"Error DB Update: {e}")

    # KASUS 2: PESAN BALASAN MASUK
    if nomor_masuk and message and (not status or status == 'received'):
        try:
            existing = supabase.table('chats').select('id').eq('message', message).eq('customer_phone', nomor_masuk).limit(1).execute()
            if not existing.data:
                supabase.table('chats').insert({
                    "customer_phone": nomor_masuk,
                    "message": message,
                    "direction": "inbound",
                    "status": "received"
                }).execute()
        except:
            pass

    return "OK", 200
