"""
Serveur Flask — Suivi de Chantier Cuves
========================================
Version production avec Neon (PostgreSQL)
Le cycle actif est stocké dans Neon — survit aux redémarrages et changements d'appareil.

Variables d'environnement sur Render :
  - DATABASE_URL    : connection string Neon (postgresql://...)
  - CLIENT_PASSWORD : mot de passe pour le client
  - ADMIN_TOKEN     : token secret pour télécharger le CSV
  - SECRET_KEY      : clé secrète Flask (chaîne aléatoire)
"""

from flask import Flask, jsonify, request, send_from_directory, Response, session, redirect
from functools import wraps
import json, os, csv, io
from datetime import datetime, timedelta
import psycopg2

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

CLIENT_PASSWORD = os.environ.get('CLIENT_PASSWORD', 'chantier2024')
ADMIN_TOKEN     = os.environ.get('ADMIN_TOKEN', 'mon-token-secret')
DATABASE_URL    = os.environ.get('DATABASE_URL', '')

# ══════════════════════════════════════════════════════════════════════════════
# POSTGRESQL
# ══════════════════════════════════════════════════════════════════════════════

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_conn()
    c = conn.cursor()
    # Cycles archivés
    c.execute('''CREATE TABLE IF NOT EXISTS cycles (
        id SERIAL PRIMARY KEY, cycle_id TEXT NOT NULL UNIQUE,
        date TEXT NOT NULL, nb_cuves INT NOT NULL,
        cuves_nums TEXT NOT NULL, created_at TIMESTAMP DEFAULT NOW())''')
    c.execute('''CREATE TABLE IF NOT EXISTS phases (
        id SERIAL PRIMARY KEY, cycle_id TEXT NOT NULL,
        phase_key TEXT NOT NULL, phase_label TEXT NOT NULL,
        duration_ms BIGINT NOT NULL, duration_hms TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '')''')
    c.execute('''CREATE TABLE IF NOT EXISTS cycles_raw (
        cycle_id TEXT PRIMARY KEY, data_json TEXT NOT NULL)''')
    # Cycle actif — une seule ligne, clé fixe 'current'
    c.execute('''CREATE TABLE IF NOT EXISTS active_cycle (
        key TEXT PRIMARY KEY,
        data_json TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT NOW())''')
    conn.commit()
    conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# AUTH
# ══════════════════════════════════════════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Non autorisé'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.args.get('token') or request.headers.get('X-Admin-Token', '')
        if token != ADMIN_TOKEN:
            return Response('Token invalide', status=403)
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = ''
    if request.method == 'POST':
        pwd = request.form.get('password', '')
        if pwd == CLIENT_PASSWORD:
            session['logged_in'] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=30)
            return redirect('/')
        error = 'Mot de passe incorrect'
    return f'''<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Suivi Chantier</title>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ background:#0d0f14; color:#e2e8f0; font-family:monospace;
       display:flex; align-items:center; justify-content:center; min-height:100vh; }}
.box {{ background:#1a1d26; border:1px solid #2a2d3a; border-radius:12px;
        padding:40px; width:100%; max-width:380px; text-align:center; }}
h1 {{ font-size:28px; letter-spacing:4px; color:#f0a500; margin-bottom:8px; }}
p  {{ font-size:11px; color:#64748b; margin-bottom:32px; letter-spacing:1px; }}
input {{ width:100%; background:#0d0f14; border:1px solid #2a2d3a; border-radius:6px;
         padding:14px 16px; color:#e2e8f0; font-size:16px; outline:none; margin-bottom:16px; }}
input:focus {{ border-color:#f0a500; }}
button {{ width:100%; background:#f0a500; color:#0d0f14; border:none; border-radius:6px;
          padding:14px; font-size:13px; font-weight:700; letter-spacing:2px; cursor:pointer; }}
button:hover {{ background:#fbbf24; }}
.error {{ color:#ef4444; font-size:12px; margin-top:12px; }}
</style></head><body>
<div class="box"><h1>SUIVI</h1><p>CHANTIER CUVES — ACCÈS SÉCURISÉ</p>
<form method="POST">
<input type="password" name="password" placeholder="Mot de passe" autofocus>
<button type="submit">CONNEXION</button></form>
{"" if not error else f'<p class="error">{error}</p>'}
</div></body></html>'''

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def ms_to_hms(ms):
    s = int(ms/1000); h,r = divmod(s,3600); m,sec = divmod(r,60)
    return f"{h:02d}:{m:02d}:{sec:02d}"

def build_phases(cycle):
    cuves = cycle.get('cuves', [])
    chronos = cycle.get('chronos', {})
    labels = {'TRAJET_ALLER': 'Récupération Cuve', 'TRAJET_RETOUR': 'Livraison Cuve'}
    for i, cv in enumerate(cuves):
        labels[f'repair_{i}'] = f'Réparation Cuve #{cv["num"]}'
    order = ['TRAJET_ALLER'] + [f'repair_{i}' for i in range(len(cuves))] + ['TRAJET_RETOUR']
    phases = []
    for key in order:
        if key in chronos:
            ms = chronos[key].get('elapsed', 0)
            notes = ''
            if key.startswith('repair_'):
                idx = int(key.split('_')[1])
                notes = cuves[idx].get('notes', '') if idx < len(cuves) else ''
            phases.append({'key': key, 'label': labels.get(key, key),
                           'ms': ms, 'hms': ms_to_hms(ms), 'notes': notes})
    return phases

# ══════════════════════════════════════════════════════════════════════════════
# CYCLE ACTIF — stocké dans Neon
# ══════════════════════════════════════════════════════════════════════════════

def load_active():
    """
    Charge le cycle actif depuis Neon.
    Si un chrono était en cours au moment de la sauvegarde, on calcule
    le temps écoulé depuis le timestamp 'start' — le chrono continue
    même si le serveur s'est endormi.
    """
    try:
        init_db()
        conn = get_conn(); c = conn.cursor()
        c.execute('SELECT data_json FROM active_cycle WHERE key=%s', ('current',))
        row = c.fetchone(); conn.close()
        if not row:
            return None
        active = json.loads(row[0])
        if not active:
            return None

        # Recalculer les chronos en cours
        now_ms = int(datetime.now().timestamp() * 1000)
        for key, ch in active.get('chronos', {}).items():
            if not ch.get('paused', True) and ch.get('start', 0) > 0:
                # Chrono en cours : ajouter le temps écoulé depuis le dernier save
                elapsed_since = now_ms - ch['start']
                ch['elapsed'] = ch.get('elapsed', 0) + max(0, elapsed_since)
                ch['start']   = now_ms  # remettre à now pour le client
            else:
                ch['paused'] = True
                ch['start']  = 0

        active['_interrupted'] = False
        return active
    except Exception as e:
        print(f"[WARN] load_active : {e}")
        return None

def save_active(active):
    """Sauvegarde le cycle actif dans Neon avec timestamp."""
    try:
        init_db()
        # Avant de sauvegarder, mettre à jour le timestamp 'start'
        # des chronos en cours pour avoir la référence la plus récente
        now_ms = int(datetime.now().timestamp() * 1000)
        for key, ch in active.get('chronos', {}).items():
            if not ch.get('paused', True) and ch.get('start', 0) > 0:
                # Capitaliser le temps écoulé et remettre start à now
                elapsed_since = now_ms - ch['start']
                ch['elapsed'] = ch.get('elapsed', 0) + max(0, elapsed_since)
                ch['start']   = now_ms

        conn = get_conn(); c = conn.cursor()
        data = json.dumps(active, ensure_ascii=False)
        c.execute('''INSERT INTO active_cycle (key, data_json, updated_at)
                     VALUES (%s, %s, NOW())
                     ON CONFLICT (key) DO UPDATE
                     SET data_json=%s, updated_at=NOW()''',
                  ('current', data, data))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[WARN] save_active : {e}")

def delete_active():
    """Supprime le cycle actif de Neon."""
    try:
        init_db()
        conn = get_conn(); c = conn.cursor()
        c.execute('DELETE FROM active_cycle WHERE key=%s', ('current',))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[WARN] delete_active : {e}")

# ══════════════════════════════════════════════════════════════════════════════
# CYCLES ARCHIVÉS
# ══════════════════════════════════════════════════════════════════════════════

def db_save_cycle(cycle):
    init_db()
    conn = get_conn(); c = conn.cursor()
    cid  = str(cycle['id'])
    date = cycle.get('date', datetime.now().strftime('%d/%m/%Y %H:%M'))
    cuves = cycle.get('cuves', [])
    nums  = json.dumps([cv['num'] for cv in cuves], ensure_ascii=False)
    c.execute('''INSERT INTO cycles (cycle_id,date,nb_cuves,cuves_nums)
                 VALUES (%s,%s,%s,%s)
                 ON CONFLICT (cycle_id) DO UPDATE
                 SET date=%s, nb_cuves=%s, cuves_nums=%s, created_at=NOW()''',
              (cid, date, len(cuves), nums, date, len(cuves), nums))
    c.execute('DELETE FROM phases WHERE cycle_id=%s', (cid,))
    for p in build_phases(cycle):
        c.execute('''INSERT INTO phases (cycle_id,phase_key,phase_label,duration_ms,duration_hms,notes)
                     VALUES (%s,%s,%s,%s,%s,%s)''',
                  (cid, p['key'], p['label'], p['ms'], p['hms'], p.get('notes','')))
    raw = json.dumps(cycle, ensure_ascii=False)
    c.execute('''INSERT INTO cycles_raw (cycle_id,data_json) VALUES (%s,%s)
                 ON CONFLICT (cycle_id) DO UPDATE SET data_json=%s''', (cid, raw, raw))
    conn.commit(); conn.close()

def db_load_all_cycles():
    init_db()
    conn = get_conn(); c = conn.cursor()
    c.execute('SELECT data_json FROM cycles_raw ORDER BY cycle_id ASC')
    rows = c.fetchall(); conn.close()
    return [json.loads(r[0]) for r in rows]

def db_delete_cycle(cycle_id):
    init_db()
    conn = get_conn(); c = conn.cursor()
    c.execute('DELETE FROM cycles     WHERE cycle_id=%s', (cycle_id,))
    c.execute('DELETE FROM phases     WHERE cycle_id=%s', (cycle_id,))
    c.execute('DELETE FROM cycles_raw WHERE cycle_id=%s', (cycle_id,))
    conn.commit(); conn.close()

def db_export_csv():
    init_db()
    conn = get_conn(); c = conn.cursor()
    c.execute('''SELECT cy.date, cy.cuves_nums, cy.cycle_id, cy.nb_cuves,
                        p.phase_label, p.duration_hms, p.duration_ms, p.notes
                 FROM cycles cy JOIN phases p ON cy.cycle_id=p.cycle_id
                 ORDER BY cy.created_at ASC, p.id ASC''')
    rows = c.fetchall(); conn.close()
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['Date','Cuves','ID Cycle','Nb cuves','Phase','Durée (HH:MM:SS)','Durée (ms)','Notes'])
    prev = None
    for date, nums_json, cid, nb, phase, hms, ms, notes in rows:
        cuves = ', '.join(json.loads(nums_json))
        if prev and prev != cid:
            w.writerow([])
        w.writerow([date, cuves, cid, nb, phase, hms, ms, notes or ''])
        prev = cid
    out.seek(0)
    return out.getvalue()

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/healthz')
def healthz():
    try: init_db(); return 'OK', 200
    except Exception as e: return str(e), 500

@app.route('/')
@login_required
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), 'index.html')

@app.route('/api/data')
@login_required
def get_data():
    return jsonify({'cycles': db_load_all_cycles(), 'active_cycle': load_active()})

@app.route('/api/save_active', methods=['POST'])
@login_required
def save_active_route():
    active = request.json.get('active_cycle')
    if active is None: delete_active()
    else: save_active(active)
    return jsonify({'ok': True})

@app.route('/api/archive_cycle', methods=['POST'])
@login_required
def archive_cycle():
    cycle = request.json.get('cycle')
    if not cycle: return jsonify({'ok': False, 'error': 'Données manquantes'}), 400
    db_save_cycle(cycle)
    delete_active()
    return jsonify({'ok': True})

@app.route('/api/delete_cycle', methods=['POST'])
@login_required
def delete_cycle():
    cycle_id = request.json.get('cycle_id')
    if not cycle_id: return jsonify({'ok': False, 'error': 'cycle_id manquant'}), 400
    db_delete_cycle(str(cycle_id))
    return jsonify({'ok': True})

@app.route('/admin/export_csv')
@admin_required
def export_csv():
    content = db_export_csv()
    fname = f"chantier_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(content.encode('utf-8-sig'), mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment; filename={fname}'})

# ══════════════════════════════════════════════════════════════════════════════
# DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=os.environ.get('FLASK_ENV') != 'production')