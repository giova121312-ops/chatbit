import os
import json
from flask import Flask, send_file, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit, join_room, leave_room
from werkzeug.utils import secure_filename

app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = 'chiave-segreta-chatbit'
app.config['UPLOAD_FOLDER'] = 'uploads'
UTENTI_FOLDER = 'utenti'

# Creazione cartelle base
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(UTENTI_FOLDER, exist_ok=True)

socketio = SocketIO(app, cors_allowed_origins="*")

# Database in memoria
users_db = {} # { username: password }
friends_db = {} # { username: set(amici) }
groups_db = {} # { group_id: { name: str, members: [username] } }
online_users = {} # { username: socket_id }
sid_to_user = {} # { socket_id: username }
messages_db = [] # Storico messaggi temporaneo (in memoria)

# ==================== SALVATAGGIO / CARICAMENTO ====================

def get_user_dir(username):
    user_dir = os.path.join(UTENTI_FOLDER, username)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def salva_dati_utente(username):
    """Salva dati.json e gruppi.json dentro la cartella dell'utente."""
    if username not in users_db:
        return

    user_dir = get_user_dir(username)

    # 1. SALVA dati.json (Nome, Password, Amicizie, Lista Nomi Gruppi)
    nomi_gruppi = [
        gdata['name']
        for gdata in groups_db.values()
        if username in gdata.get('members', [])
    ]

    dati = {
        'nome': username,
        'password': users_db[username],
        'amicizie': list(friends_db.get(username, set())),
        'gruppi': nomi_gruppi
    }

    with open(os.path.join(user_dir, 'dati.json'), 'w', encoding='utf-8') as f:
        json.dump(dati, f, ensure_ascii=False, indent=4)

    # 2. SALVA gruppi.json (Tutti i gruppi di cui fa parte con nome e lista membri)
    user_groups_detail = {
        gid: gdata
        for gid, gdata in groups_db.items()
        if username in gdata.get('members', [])
    }

    with open(os.path.join(user_dir, 'gruppi.json'), 'w', encoding='utf-8') as f:
        json.dump(user_groups_detail, f, ensure_ascii=False, indent=4)

def carica_tutti_i_dati():
    """All'avvio legge le cartelle utenti, ripristina profili, amici e gruppi."""
    global users_db, friends_db, groups_db

    if not os.path.exists(UTENTI_FOLDER):
        return

    for username in os.listdir(UTENTI_FOLDER):
        user_dir = os.path.join(UTENTI_FOLDER, username)
        dati_file = os.path.join(user_dir, 'dati.json')
        gruppi_file = os.path.join(user_dir, 'gruppi.json')

        if os.path.isdir(user_dir):
            # Carica dati.json
            if os.path.isfile(dati_file):
                with open(dati_file, 'r', encoding='utf-8') as f:
                    dati = json.load(f)
                    nome = dati.get('nome', username)
                    users_db[nome] = dati.get('password', '')
                    friends_db[nome] = set(dati.get('amicizie', []))

            # Carica gruppi.json se presente
            if os.path.isfile(gruppi_file):
                with open(gruppi_file, 'r', encoding='utf-8') as f:
                    user_groups = json.load(f)
                    for gid, gdata in user_groups.items():
                        if gid not in groups_db:
                            groups_db[gid] = gdata
                        else:
                            # Unisce i membri per evitare che si perdano
                            for m in gdata.get('members', []):
                                if m not in groups_db[gid]['members']:
                                    groups_db[gid]['members'].append(m)

    print(f"-> Dati caricati! Utenti trovati: {len(users_db)}, Gruppi caricati: {len(groups_db)}")

# Carica i dati salvati all'avvio
carica_tutti_i_dati()

# ==================== ROUTE PWA & FILE STATICI ====================

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/manifest.json')
def serve_manifest():
    return send_file('manifest.json', mimetype='application/json')

@app.route('/sw.js')
def serve_sw():
    return send_file('sw.js', mimetype='application/javascript')

@app.route('/<path:filename>')
def serve_static_files(filename):
    """Serve immagini, icone e altri file statici dalla cartella principale."""
    return send_from_directory('.', filename)

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ==================== ROUTE API ====================

@app.route('/api/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': 'Nessun file caricato'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'success': False, 'message': 'Nessun file selezionato'}), 400

    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(file_path)

    file_url = f"/uploads/{filename}"
    return jsonify({'success': True, 'fileUrl': file_url, 'fileName': filename})

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if not username or not password:
        return jsonify({'success': False, 'message': 'Compila tutti i campi'}), 400

    if username in users_db:
        return jsonify({'success': False, 'message': 'Nome utente già esistente'}), 400

    users_db[username] = password
    friends_db[username] = set()
    salva_dati_utente(username)

    return jsonify({'success': True})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if users_db.get(username) == password:
        return jsonify({'success': True, 'username': username})
    
    return jsonify({'success': False, 'message': 'Credenziali errate'}), 401

@app.route('/api/verify', methods=['POST'])
def verify_session():
    """Verifica l'auto-login inviato dal localStorage dell'HTML."""
    data = request.json or {}
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()

    if username in users_db and users_db[username] == password:
        return jsonify({'success': True, 'username': username})
    
    return jsonify({'success': False, 'message': 'Sessione scaduta o errata'}), 401

@app.route('/api/contacts', methods=['GET'])
def get_contacts():
    current_user = request.args.get('username')
    user_friends = list(friends_db.get(current_user, set()))
    
    user_groups = [
        {'id': gid, 'name': gdata['name'], 'members': gdata['members']}
        for gid, gdata in groups_db.items()
        if current_user in gdata.get('members', [])
    ]
    
    return jsonify({'contacts': user_friends, 'groups': user_groups})

@app.route('/api/messages', methods=['GET'])
def get_messages():
    u1 = request.args.get('user1')
    target = request.args.get('target')
    is_group = request.args.get('isGroup') == 'true'

    if is_group:
        history = [m for m in messages_db if m.get('isGroup') and m['receiver'] == target]
    else:
        history = [m for m in messages_db if not m.get('isGroup') and ((m['sender'] == u1 and m['receiver'] == target) or (m['sender'] == target and m['receiver'] == u1))]
        
    return jsonify({'messages': history})

@app.route('/api/user_status', methods=['GET'])
def user_status():
    target_user = request.args.get('username')
    is_online = target_user in online_users
    return jsonify({'username': target_user, 'online': is_online})

# ==================== WEBSOCKET EVENTS ====================

@socketio.on('register_user')
def handle_register(data):
    username = data.get('username')
    if username:
        online_users[username] = request.sid
        sid_to_user[request.sid] = username
        join_room(username)
        for gid, gdata in groups_db.items():
            if username in gdata.get('members', []):
                join_room(gid)
        socketio.emit('user_status_changed', {'username': username, 'online': True})

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in sid_to_user:
        username = sid_to_user.pop(sid)
        if online_users.get(username) == sid:
            online_users.pop(username, None)
            socketio.emit('user_status_changed', {'username': username, 'online': False})

@socketio.on('send_invite')
def handle_send_invite(data):
    sender = data.get('sender')
    receiver = data.get('receiver')

    if receiver not in users_db:
        emit('invite_response', {'success': False, 'message': 'Utente non trovato.'}, room=request.sid)
        return

    if receiver == sender:
        emit('invite_response', {'success': False, 'message': 'Non puoi invitare te stesso!'}, room=request.sid)
        return

    if receiver in friends_db.get(sender, set()):
        emit('invite_response', {'success': False, 'message': f'{receiver} è già tra i tuoi amici.'}, room=request.sid)
        return

    emit('receive_invite_modal', {'from': sender}, room=receiver)
    emit('invite_response', {'success': True, 'message': f'Invito inviato a {receiver}!'}, room=request.sid)

@socketio.on('respond_invite')
def handle_respond_invite(data):
    sender = data.get('sender')
    receiver = data.get('receiver')
    accepted = data.get('accepted')

    if accepted:
        friends_db.setdefault(sender, set()).add(receiver)
        friends_db.setdefault(receiver, set()).add(sender)
        
        salva_dati_utente(sender)
        salva_dati_utente(receiver)

        emit('update_contacts', room=sender)
        emit('update_contacts', room=receiver)

@socketio.on('remove_friend')
def handle_remove_friend(data):
    user = data.get('user')
    friend = data.get('friend')

    if friend in friends_db.get(user, set()):
        friends_db[user].remove(friend)
    if user in friends_db.get(friend, set()):
        friends_db[friend].remove(user)

    salva_dati_utente(user)
    salva_dati_utente(friend)

    emit('update_contacts', room=user)
    emit('update_contacts', room=friend)

@socketio.on('create_group')
def handle_create_group(data):
    creator = data.get('creator')
    group_name = data.get('groupName')
    members = data.get('members', [])

    if creator not in members:
        members.append(creator)

    group_id = f"group_{len(groups_db) + 1}"
    groups_db[group_id] = {
        'name': group_name,
        'members': members
    }

    # Salva dati.json e gruppi.json per tutti i membri
    for m in members:
        salva_dati_utente(m)
        if m in online_users:
            socketio.server.enter_room(online_users[m], group_id)
        emit('update_contacts', room=m)

@socketio.on('add_members_to_group')
def handle_add_members(data):
    group_id = data.get('groupId')
    new_members = data.get('newMembers', [])

    if group_id in groups_db:
        for m in new_members:
            if m not in groups_db[group_id]['members']:
                groups_db[group_id]['members'].append(m)
                if m in online_users:
                    socketio.server.enter_room(online_users[m], group_id)
                emit('update_contacts', room=m)

        # Aggiorna tutti i membri del gruppo
        for m in groups_db[group_id]['members']:
            salva_dati_utente(m)

@socketio.on('leave_group')
def handle_leave_group(data):
    user = data.get('user')
    group_id = data.get('groupId')

    if group_id in groups_db and user in groups_db[group_id]['members']:
        groups_db[group_id]['members'].remove(user)
        salva_dati_utente(user)
        
        # Aggiorna anche i restanti membri del gruppo
        for m in groups_db[group_id]['members']:
            salva_dati_utente(m)

        if user in online_users:
            leave_room(group_id, sid=online_users[user])
        emit('update_contacts', room=user)

@socketio.on('send_message')
def handle_send_message(data):
    msg_data = {
        'sender': data.get('sender'),
        'receiver': data.get('receiver'),
        'isGroup': data.get('isGroup', False),
        'text': data.get('text', ''),
        'audioUrl': data.get('audioUrl', None),
        'isAudio': data.get('isAudio', False),
        'fileUrl': data.get('fileUrl', None),
        'fileName': data.get('fileName', None),
        'isFile': data.get('isFile', False),
        'time': data.get('time')
    }
    messages_db.append(msg_data)

    if data.get('isGroup'):
        emit('receive_message', msg_data, room=data.get('receiver'))
    else:
        emit('receive_message', msg_data, room=data.get('receiver'))
        emit('receive_message', msg_data, room=data.get('sender'))

# ==================== WEBRTC ====================
@socketio.on('call_user')
def handle_call_user(data):
    target = data.get('target')
    if target in online_users:
        emit('incoming_call', {
            'from': data.get('from'),
            'offer': data.get('offer'),
            'isVideo': data.get('isVideo')
        }, room=target)
    else:
        emit('call_rejected', {'reason': 'Utente non online'}, room=request.sid)

@socketio.on('answer_call')
def handle_answer_call(data):
    target = data.get('target')
    emit('call_accepted', {'answer': data.get('answer')}, room=target)

@socketio.on('ice_candidate')
def handle_ice_candidate(data):
    target = data.get('target')
    emit('ice_candidate', {'candidate': data.get('candidate')}, room=target)

@socketio.on('end_call')
def handle_end_call(data):
    target = data.get('target')
    emit('call_ended', room=target)

if __name__ == '__main__':
    print("Server Chatbit avviato! Apri http://127.0.0.1:5000 nel browser.")
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
