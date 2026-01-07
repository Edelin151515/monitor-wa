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

    # Statistik (Hitung yang statusnya mengandung kata 'read')
    sent_count = sum(1 for c in chats if c.get('direction') == 'outbound')
    read_count = sum(1 for c in chats if c.get('status') and 'read' in c.get('status').lower())
    reply_count = sum(1 for c in chats if c.get('direction') == 'inbound')
            
    stats = {'sent': sent_count, 'read': read_count, 'replied': reply_count}
    replies = [c for c in chats if c.get('direction') == 'inbound']
    return render_template('dashboard.html', stats=stats, replies=replies)

@app.route('/send', methods=['POST'])
def send_message():
    phone = request.form.get('phone')
    message = request.form.get('message')
    
    headers = {'Authorization': FONNTE_TOKEN}
    data = {'target': phone, 'message': message}
    
    # Kirim ke Fonnte
    try:
        requests.post('https://api.fonnte.com/send', headers=headers, data=data)
    except:
        pass
    
    # Simpan ke Database
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
    data = request.json or request.form
    if not data: return "No Data", 200

    print(f"WEBHOOK: {data}")

    sender = data.get('sender')
    message = data.get('message')
    status = data.get('status') or data.get('state') # Fonnte pakai 'state' untuk status kirim

    # KASUS 1: UPDATE STATUS BACA (READ)
    # Jika statusnya READ, kita cari pesan TERAKHIR ke nomor tersebut
    if status and 'read' in status.lower():
        print(f"Status READ diterima dari {sender}")
        try:
            # Cari pesan terakhir yang dikirim ke nomor ini (outbound) yg belum dibaca
            last_msg = supabase.table('chats').select('id')\
                .eq('customer_phone', sender)\
                .eq('direction', 'outbound')\
                .neq('status', 'read')\
                .order('created_at', desc=True)\
                .limit(1).execute()
            
            if last_msg.data:
                msg_id = last_msg.data[0]['id']
                # Update jadi READ
                supabase.table('chats').update({'status': 'read'}).eq('id', msg_id).execute()
                print(f"Sukses update ID {msg_id} jadi READ")
        except Exception as e:
            print(f"Gagal Update Read: {e}")

    # KASUS 2: PESAN BALASAN (INBOUND)
    if sender and message and (not status or status == 'received'):
        try:
            existing = supabase.table('chats').select('id').eq('message', message).eq('customer_phone', sender).limit(1).execute()
            if not existing.data:
                supabase.table('chats').insert({
                    "customer_phone": sender,
                    "message": message,
                    "direction": "inbound",
                    "status": "received"
                }).execute()
        except:
            pass

    return "OK", 200
