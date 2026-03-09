"""
Serveur Flask — Suivi de Chantier Cuves
========================================
Version production (déploiement Render, SQLite)

Variables d'environnement sur Render :
  - CLIENT_PASSWORD : mot de passe pour le client
  - ADMIN_TOKEN     : token secret pour télécharger le CSV
  - SECRET_KEY      : clé secrète Flask (chaîne aléatoire)
"""

from flask import Flask, jsonify, request, send_from_directory, Response, session, redirect
from functools import wraps
import json
import os
import sqlite3
import csv
import io
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='.')
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

CLIENT_PASSWORD = os.environ.get('CLIENT_PASSWORD', 'chantier2024')
ADMIN_TOKEN     = os.environ.get('ADMIN_TOKEN', 'mon-token-secret')

# Sur Render, le dossier projet est en lecture seule → on écrit dans /tmp
# En local, on utilise le dossier du script
IS_RENDER   = bool(os.environ.get('RENDER', ''))
DATA_DIR    = '/tmp' if IS_RENDER else os.path.dirname(os.path.abspath(__file__))
DB_FILE     = os.path.join(DATA_DIR, 'chantier.db')
ACTIVE_FILE = os.path.join(DATA_DIR, 'active.json')

# ══════════════════════════════════════════════════════════════════════════════
# AUTHENTIFICATION
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


@app.route('/healthz')
def healthz():
    """Health check pour Render — initialise aussi la DB au premier appel."""
    try:
        init_db()
        return 'OK', 200
    except Exception as e:
        return str(e), 500


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
<html lang="fr">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Suivi Chantier — Connexion</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0d0f14;
      color: #e2e8f0;
      font-family: 'IBM Plex Mono', monospace;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }}
    .box {{
      background: #1a1d26;
      border: 1px solid #2a2d3a;
      border-radius: 12px;
      padding: 40px;
      width: 100%;
      max-width: 380px;
      text-align: center;
    }}
    h1 {{ font-size: 28px; letter-spacing: 4px; color: #f0a500; margin-bottom: 8px; }}
    p  {{ font-size: 11px; color: #64748b; margin-bottom: 32px; letter-spacing: 1px; }}
    input {{
      width: 100%;
      background: #0d0f14;
      border: 1px solid #2a2d3a;
      border-radius: 6px;
      padding: 14px 16px;
      color: #e2e8f0;
      font-size: 16px;
      font-family: inherit;
      outline: none;
      margin-bottom: 16px;
    }}
    input:focus {{ border-color: #f0a500; }}
    button {{
      width: 100%;
      background: #f0a500;
      color: #0d0f14;
      border: none;
      border-radius: 6px;
      padding: 14px;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 2px;
      cursor: pointer;
    }}
    button:hover {{ background: #fbbf24; }}
    .error {{ color: #ef4444; font-size: 12px; margin-top: 12px; }}
  </style>
</head>
<body>
  <div class="box">
    <h1>SUIVI</h1>
    <p>CHANTIER CUVES — ACCÈS SÉCURISÉ</p>
    <form method="POST">
      <input type="password" name="password" placeholder="Mot de passe" autofocus>
      <button type="submit">CONNEXION</button>
    </form>
    {"" if not error else f'<p class="error">{error}</p>'}
  </div>
</body>
</html>'''


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ══════════════════════════════════════════════════════════════════════════════
# INITIALISATION BASE SQLite
# ══════════════════════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS cycles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id    TEXT    NOT NULL UNIQUE,
            date        TEXT    NOT NULL,
            nb_cuves    INTEGER NOT NULL,
            cuves_nums  TEXT    NOT NULL,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS phases (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id     TEXT    NOT NULL,
            phase_key    TEXT    NOT NULL,
            phase_label  TEXT    NOT NULL,
            duration_ms  INTEGER NOT NULL,
            duration_hms TEXT    NOT NULL,
            notes        TEXT    NOT NULL DEFAULT '',
            FOREIGN KEY (cycle_id) REFERENCES cycles(cycle_id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS cycles_raw (
            cycle_id  TEXT PRIMARY KEY,
            data_json TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

# init_db() est appelé au premier accès, pas au chargement du module
# (évite les erreurs de permissions au démarrage gunicorn --preload)

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def ms_to_hms(ms):
    s      = int(ms / 1000)
    h, r   = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def build_phases(cycle):
    cuves   = cycle.get('cuves', [])
    chronos = cycle.get('chronos', {})
    labels  = {
        'TRAJET_ALLER':  'Récupération Cuve',
        'TRAJET_RETOUR': 'Livraison Cuve',
    }
    for i, cv in enumerate(cuves):
        labels[f'repair_{i}'] = f'Réparation Cuve #{cv["num"]}'

    order  = ['TRAJET_ALLER'] + [f'repair_{i}' for i in range(len(cuves))] + ['TRAJET_RETOUR']
    phases = []
    for key in order:
        if key in chronos:
            ms    = chronos[key].get('elapsed', 0)
            notes = ''
            if key.startswith('repair_'):
                idx   = int(key.split('_')[1])
                notes = cuves[idx].get('notes', '') if idx < len(cuves) else ''
            phases.append({'key': key, 'label': labels.get(key, key),
                           'ms': ms, 'hms': ms_to_hms(ms), 'notes': notes})
    return phases


def db_save_cycle(cycle):
    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()

    cycle_id   = str(cycle['id'])
    date       = cycle.get('date', datetime.now().strftime('%d/%m/%Y %H:%M'))
    cuves      = cycle.get('cuves', [])
    nb_cuves   = len(cuves)
    cuves_nums = json.dumps([cv['num'] for cv in cuves], ensure_ascii=False)

    c.execute('''INSERT OR REPLACE INTO cycles (cycle_id, date, nb_cuves, cuves_nums, created_at)
                 VALUES (?, ?, ?, ?, datetime('now','localtime'))''',
              (cycle_id, date, nb_cuves, cuves_nums))

    c.execute('DELETE FROM phases WHERE cycle_id = ?', (cycle_id,))
    phases = build_phases(cycle)
    for p in phases:
        c.execute('''INSERT INTO phases (cycle_id, phase_key, phase_label, duration_ms, duration_hms, notes)
                     VALUES (?, ?, ?, ?, ?, ?)''',
                  (cycle_id, p['key'], p['label'], p['ms'], p['hms'], p.get('notes', '')))

    c.execute('INSERT OR REPLACE INTO cycles_raw (cycle_id, data_json) VALUES (?, ?)',
              (cycle_id, json.dumps(cycle, ensure_ascii=False)))

    conn.commit()
    conn.close()


def db_load_all_cycles():
    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()
    c.execute('SELECT data_json FROM cycles_raw ORDER BY rowid ASC')
    rows = c.fetchall()
    conn.close()
    return [json.loads(r[0]) for r in rows]


def db_export_csv():
    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()
    c.execute('''
        SELECT cy.date, cy.cuves_nums, cy.cycle_id, cy.nb_cuves,
               p.phase_label, p.duration_hms, p.duration_ms, p.notes
        FROM cycles cy
        JOIN phases p ON cy.cycle_id = p.cycle_id
        ORDER BY cy.created_at ASC, p.id ASC
    ''')
    rows = c.fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Date', 'Cuves', 'ID Cycle', 'Nb cuves', 'Phase',
                     'Durée (HH:MM:SS)', 'Durée (ms)', 'Notes'])
    prev_id = None
    for date, cuves_json, cycle_id, nb, phase, hms, ms, notes in rows:
        cuves = ', '.join(json.loads(cuves_json))
        if prev_id and prev_id != cycle_id:
            writer.writerow([])
        writer.writerow([date, cuves, cycle_id, nb, phase, hms, ms, notes or ''])
        prev_id = cycle_id

    output.seek(0)
    return output.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# GESTION CYCLE ACTIF
# ══════════════════════════════════════════════════════════════════════════════

def load_active():
    if not os.path.exists(ACTIVE_FILE):
        return None
    try:
        with open(ACTIVE_FILE, 'r', encoding='utf-8') as f:
            active = json.load(f)
        if active is None:
            return None

        now_ms = int(datetime.now().timestamp() * 1000)
        for key, ch in active.get('chronos', {}).items():
            was_paused = ch.get('paused', True)
            start      = ch.get('start', 0)
            if was_paused or start == 0:
                ch['paused'] = True
                ch['start']  = 0
            else:
                elapsed_since = now_ms - start
                ch['elapsed'] = ch.get('elapsed', 0) + max(0, elapsed_since)
                ch['start']   = now_ms

        active['_interrupted'] = False
        return active
    except Exception as e:
        print(f"[WARN] Impossible de lire active.json : {e}")
        return None


def save_active(active):
    with open(ACTIVE_FILE, 'w', encoding='utf-8') as f:
        json.dump(active, f, ensure_ascii=False, indent=2)


def delete_active():
    if os.path.exists(ACTIVE_FILE):
        os.remove(ACTIVE_FILE)


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
@login_required
def index():
    init_db()
    return send_from_directory('.', 'index.html')


@app.route('/api/data', methods=['GET'])
@login_required
def get_data():
    cycles = db_load_all_cycles()
    active = load_active()
    return jsonify({'cycles': cycles, 'active_cycle': active})


@app.route('/api/save_active', methods=['POST'])
@login_required
def save_active_route():
    data   = request.json
    active = data.get('active_cycle')
    if active is None:
        delete_active()
    else:
        save_active(active)
    return jsonify({'ok': True})


@app.route('/api/archive_cycle', methods=['POST'])
@login_required
def archive_cycle():
    cycle = request.json.get('cycle')
    if not cycle:
        return jsonify({'ok': False, 'error': 'Données manquantes'}), 400
    db_save_cycle(cycle)
    delete_active()
    return jsonify({'ok': True})


@app.route('/api/delete_cycle', methods=['POST'])
@login_required
def delete_cycle():
    cycle_id = request.json.get('cycle_id')
    if not cycle_id:
        return jsonify({'ok': False, 'error': 'cycle_id manquant'}), 400
    db_delete_cycle(str(cycle_id))
    return jsonify({'ok': True})


def db_delete_cycle(cycle_id):
    conn = sqlite3.connect(DB_FILE)
    c    = conn.cursor()
    c.execute('DELETE FROM cycles     WHERE cycle_id = ?', (cycle_id,))
    c.execute('DELETE FROM phases     WHERE cycle_id = ?', (cycle_id,))
    c.execute('DELETE FROM cycles_raw WHERE cycle_id = ?', (cycle_id,))
    conn.commit()
    conn.close()


@app.route('/admin/export_csv')
@admin_required
def export_csv():
    content  = db_export_csv()
    filename = f"chantier_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content.encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# ══════════════════════════════════════════════════════════════════════════════
# DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port  = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    print("=" * 60)
    print("  SUIVI CHANTIER — Serveur démarré")
    print(f"  URL : http://localhost:{port}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=port, debug=debug)