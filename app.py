import sqlite3
import time
import secrets
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

# --- 1. Database Setup ---
def init_db():
    conn = sqlite3.connect('smartattend.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS qr_sessions 
                 (session_id INTEGER PRIMARY KEY AUTOINCREMENT, subject_code TEXT, 
                  current_token TEXT, expires_at REAL, session_end_time REAL, is_active INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS attendance_logs 
                 (log_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, 
                  student_id TEXT, scan_time REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS students (student_id TEXT PRIMARY KEY, name TEXT)''')
    c.execute("INSERT OR IGNORE INTO students (student_id, name) VALUES ('BCA001', 'Krupa')")
    conn.commit()
    conn.close()

init_db()

# --- 2. Projector Screen (Faculty View) ---
PROJECTOR_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>SmartAttend Projector</title>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
  <style>
    body { text-align: center; font-family: sans-serif; background: #0f172a; color: white; padding-top: 50px; } 
    #qrcode { display: inline-block; padding: 20px; background: white; border-radius: 12px; margin-top: 20px; }
    .timer { font-size: 1.5rem; margin-top: 15px; color: #38bdf8; }
  </style>
</head>
<body>
  <h1>SmartAttend: BCA-CORE</h1>
  <p>Scan to mark attendance. Proxies will be rejected.</p>
  <div id="qrcode"></div>
  <div class="timer" id="status">Starting session...</div>
  <p style="margin-top: 30px; font-size: 0.9rem; color: #94a3b8;">
    Testing on your laptop? Open <a href="/scan" target="_blank" style="color: #38bdf8;">Scanner Here</a>
  </p>

  <script>
    const qrContainer = document.getElementById("qrcode");
    const statusText = document.getElementById("status");
    let qr = new QRCode(qrContainer, { width: 250, height: 250 });

    async function refreshToken() {
      try {
        const res = await fetch('/api/token');
        const data = await res.json();
        
        if (data.status === 'expired') {
          qrContainer.style.display = 'none';
          statusText.innerText = '5-Minute Window Closed';
          statusText.style.color = '#ef4444';
          return;
        }
        
        qr.clear();
        qr.makeCode(data.token);
        statusText.innerText = 'Session locks in: ' + Math.floor(data.remaining) + 's';
      } catch (err) { console.error(err); }
    }
    
    refreshToken();
    setInterval(refreshToken, 5000);
  </script>
</body>
</html>
"""

# --- 3. Mobile Scanner (Student View) ---
SCANNER_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>Student Scanner</title>
  <script src="https://unpkg.com/html5-qrcode" type="text/javascript"></script>
  <style>
    body { font-family: sans-serif; text-align: center; padding: 20px; background: #f8fafc; }
    #reader { width: 100%; max-width: 400px; margin: 0 auto; border-radius: 8px; overflow: hidden; }
    #result { margin-top: 20px; padding: 15px; border-radius: 8px; font-weight: bold; display: none; }
  </style>
</head>
<body>
  <h2>Scan Attendance QR</h2>
  <div id="reader"></div>
  <div id="result"></div>

  <script>
    const html5QrcodeScanner = new Html5QrcodeScanner("reader", { fps: 10, qrbox: {width: 250, height: 250} }, false);
    
    async function onScanSuccess(decodedText, decodedResult) {
      html5QrcodeScanner.pause();
      
      const response = await fetch('/api/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: decodedText, student_id: 'BCA001' })
      });
      
      const result = await response.json();
      const resDiv = document.getElementById('result');
      resDiv.style.display = 'block';
      
      if(result.success) {
        resDiv.style.background = '#dcfce7';
        resDiv.style.color = '#166534';
        resDiv.innerText = result.message;
      } else {
        resDiv.style.background = '#fee2e2';
        resDiv.style.color = '#991b1b';
        resDiv.innerText = result.message;
        setTimeout(() => html5QrcodeScanner.resume(), 3000);
      }
    }
    
    html5QrcodeScanner.render(onScanSuccess);
  </script>
</body>
</html>
"""

# --- Routes ---
@app.route('/')
def index():
    return render_template_string(PROJECTOR_HTML)

@app.route('/scan')
def scan():
    return render_template_string(SCANNER_HTML)

@app.route('/api/token')
def get_token():
    conn = sqlite3.connect('smartattend.db')
    c = conn.cursor()
    now = time.time()
    
    c.execute("SELECT * FROM qr_sessions WHERE is_active = 1 ORDER BY session_id DESC LIMIT 1")
    session = c.fetchone()
    
    if not session:
        token = secrets.token_hex(8)
        c.execute("INSERT INTO qr_sessions (subject_code, current_token, expires_at, session_end_time, is_active) VALUES (?, ?, ?, ?, 1)", 
                  ('BCA-CORE', token, now + 15, now + 300))
        conn.commit()
        return jsonify({'token': token, 'status': 'active', 'remaining': 300})
        
    session_id, subj, current_token, expires_at, end_time, is_active = session
    
    if now >= end_time:
        c.execute("UPDATE qr_sessions SET is_active = 0 WHERE session_id = ?", (session_id,))
        conn.commit()
        return jsonify({'status': 'expired'})
        
    if now >= expires_at:
        current_token = secrets.token_hex(8)
        c.execute("UPDATE qr_sessions SET current_token = ?, expires_at = ? WHERE session_id = ?", (current_token, now + 15, session_id))
        conn.commit()
        
    return jsonify({'token': current_token, 'status': 'active', 'remaining': end_time - now})

@app.route('/api/verify', methods=['POST'])
def verify():
    data = request.get_json()
    token = data.get('token')
    student_id = data.get('student_id')
    now = time.time()
    
    conn = sqlite3.connect('smartattend.db')
    c = conn.cursor()
    
    c.execute("SELECT session_id FROM qr_sessions WHERE current_token = ? AND is_active = 1 AND expires_at >= ?", (token, now))
    session = c.fetchone()
    
    if not session:
        return jsonify({'success': False, 'message': 'Scan failed: QR Code expired (Proxy Attempt)!'})
        
    session_id = session[0]
    
    c.execute("SELECT * FROM attendance_logs WHERE session_id = ? AND student_id = ?", (session_id, student_id))
    if c.fetchone():
        return jsonify({'success': False, 'message': 'You are already marked present!'})
        
    c.execute("INSERT INTO attendance_logs (session_id, student_id, scan_time) VALUES (?, ?, ?)", (session_id, student_id, now))
    conn.commit()
    
    return jsonify({'success': True, 'message': f'Attendance marked for {student_id}!'})

# Changed port to 8000 and host to 0.0.0.0 to fix Mac issues
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888, debug=True)