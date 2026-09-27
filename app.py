# -*- coding: utf-8 -*-
"""
سامانه اتوماسیون اداری و مالی — شرکت عمران زیست
فقط با پایتون ۳.۹ به بالا اجرا می‌شود و به هیچ کتابخانه بیرونی یا اینترنت نیاز ندارد.
اجرا:  python app.py      سپس در مرورگر:  http://<IP سرور>:8080
"""
import os, sys, json, sqlite3, hashlib, secrets, re, shutil, threading, csv, io, time
import mimetypes, urllib.parse, datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http import cookies

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
FILES = os.path.join(DATA, 'files')
BACK = os.path.join(DATA, 'backups')
DB = os.path.join(DATA, 'oa.db')
PORT_FILE = os.path.join(BASE, 'port.txt')


def read_port():
    try:
        return int(open(PORT_FILE).read().strip())
    except Exception:
        return int(os.environ.get('OA_PORT', '8080'))


PORT = read_port()
CANDIDATE_PORTS = [8080, 8090, 8888, 9090, 5080, 7080, 18080]
MAX_UPLOAD = 60 * 1024 * 1024
VERSION = '1.1'

FA2EN = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
OPEN = ('new', 'seen', 'doing')
ROLES = {'admin': 'مدیر سیستم', 'manager': 'عضو هیات مدیره', 'secretariat': 'دبیرخانه',
         'finance': 'مالی', 'staff': 'کارمند'}
LETTER_KINDS = {'in': 'وارده', 'out': 'صادره', 'internal': 'داخلی'}
LETTER_PREFIX = {'in': 'و', 'out': 'ص', 'internal': 'د'}
ACTIONS = ['اقدام', 'بررسی و اظهارنظر', 'تهیه پاسخ', 'امضا', 'پیگیری', 'جهت اطلاع', 'بایگانی']
SOURCES = ['پست', 'تحویل حضوری', 'ایمیل', 'فکس', 'تلگرام/واتساپ کارگاه', 'سامانه الکترونیکی', 'سایر']
REQUEST_KINDS = ['خرید کالا و مصالح', 'خرید تجهیزات', 'خرید خدمات', 'تنخواه', 'پرداخت به پیمانکار',
                 'پرداخت به تامین‌کننده', 'هزینه جاری دفتر', 'سایر']
WAREHOUSE_KINDS = ('خرید کالا و مصالح', 'خرید تجهیزات')

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
  title TEXT DEFAULT '', role TEXT NOT NULL DEFAULT 'staff', pw_hash TEXT, salt TEXT, must_change INTEGER DEFAULT 1,
  active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, code TEXT DEFAULT '',
  manager_id INTEGER, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS letters(id INTEGER PRIMARY KEY, kind TEXT NOT NULL, year INTEGER, seq INTEGER, number TEXT,
  subject TEXT NOT NULL, counterpart TEXT DEFAULT '', their_number TEXT DEFAULT '', their_date TEXT DEFAULT '',
  letter_date TEXT DEFAULT '', project_id INTEGER, priority TEXT DEFAULT 'normal', confidential INTEGER DEFAULT 0,
  summary TEXT DEFAULT '', source TEXT DEFAULT '', status TEXT DEFAULT 'open', archive_code TEXT DEFAULT '',
  created_by INTEGER, created_at TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, year INTEGER, seq INTEGER, number TEXT, kind TEXT NOT NULL,
  project_id INTEGER, requester_id INTEGER, title TEXT NOT NULL, amount INTEGER DEFAULT 0, payee TEXT DEFAULT '',
  description TEXT DEFAULT '', status TEXT DEFAULT 'pending', paid_amount INTEGER, paid_at TEXT, pay_note TEXT DEFAULT '',
  sepidar_no TEXT DEFAULT '', created_at TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS steps(id INTEGER PRIMARY KEY, request_id INTEGER, step_no INTEGER, approver_id INTEGER,
  label TEXT, status TEXT DEFAULT 'waiting', note TEXT DEFAULT '', acted_at TEXT);
CREATE TABLE IF NOT EXISTS referrals(id INTEGER PRIMARY KEY, doc_type TEXT, doc_id INTEGER, parent_id INTEGER,
  from_id INTEGER, to_id INTEGER, action TEXT, instruction TEXT DEFAULT '', due_date TEXT DEFAULT '',
  status TEXT DEFAULT 'new', reply TEXT DEFAULT '', created_at TEXT, seen_at TEXT, done_at TEXT);
CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY, doc_type TEXT, doc_id INTEGER, name TEXT, path TEXT,
  size INTEGER, uploaded_by INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, doc_type TEXT, doc_id INTEGER, user_id INTEGER, event TEXT,
  detail TEXT DEFAULT '', at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS ix_ref_to ON referrals(to_id, status);
CREATE INDEX IF NOT EXISTS ix_ref_doc ON referrals(doc_type, doc_id);
CREATE INDEX IF NOT EXISTS ix_steps ON steps(request_id);
CREATE INDEX IF NOT EXISTS ix_att ON attachments(doc_type, doc_id);
CREATE INDEX IF NOT EXISTS ix_log ON log(doc_type, doc_id);
"""

DEFAULT_SETTINGS = {'company': 'شرکت گسترش فناوری عمران زیست', 'ceo_threshold': '1000000000',
                    'ceo_user': '', 'office_approver': '', 'warehouse_user': '', 'default_due_days': '3'}

SEED_USERS = [  # (username, full_name, title, role)
    ('admin', 'مدیر سیستم', 'راهبر سامانه', 'admin'),
    ('ceo', 'مدیرعامل', 'مدیرعامل', 'manager'),
    ('kasaeian', 'مهندس کساییان', 'نایب رییس هیات مدیره و معاون فنی', 'manager'),
    ('aliasghari', 'مهندس علی‌اصغری', 'رییس هیات مدیره، معاون اجرایی و مدیر انبار و تجهیزات', 'manager'),
    ('secretary', 'منشی', 'دبیرخانه', 'secretariat'),
    ('support', 'پشتیبانی', 'پشتیبانی', 'staff'),
    ('office', 'اداری', 'امور اداری', 'staff'),
    ('finance', 'مالی', 'امور مالی', 'finance'),
]


# ------------------------------------------------------------------ تاریخ شمسی
def g2j(gy, gm, gd):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    jy = -1595 + 33 * (days // 12053); days %= 12053
    jy += 4 * (days // 1461); days %= 1461
    if days > 365:
        jy += (days - 1) // 365; days = (days - 1) % 365
    jm = 1 + days // 31 if days < 186 else 7 + (days - 186) // 30
    jd = 1 + (days % 31 if days < 186 else (days - 186) % 30)
    return jy, jm, jd


def jstr(iso):
    if not iso:
        return ''
    try:
        y, m, d = int(iso[0:4]), int(iso[5:7]), int(iso[8:10])
        jy, jm, jd = g2j(y, m, d)
        return '%04d/%02d/%02d' % (jy, jm, jd) + (iso[10:16] if len(iso) > 10 else '')
    except Exception:
        return iso


def now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def today():
    return datetime.date.today().isoformat()


def jyear():
    d = datetime.date.today()
    return g2j(d.year, d.month, d.day)[0]


# ------------------------------------------------------------------ پایگاه داده
def db():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    return hashlib.pbkdf2_hmac('sha256', pw.encode('utf-8'), salt.encode(), 120000).hex(), salt


def init_db():
    os.makedirs(FILES, exist_ok=True)
    os.makedirs(BACK, exist_ok=True)
    c = db()
    c.execute('PRAGMA journal_mode=WAL')
    c.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (k, v))
    if not c.execute('SELECT 1 FROM users').fetchone():
        for un, fn, tt, rl in SEED_USERS:
            h, s = hash_pw('1234')
            c.execute('INSERT INTO users(username,full_name,title,role,pw_hash,salt) VALUES(?,?,?,?,?,?)',
                      (un, fn, tt, rl, h, s))
        ids = {r['username']: r['id'] for r in c.execute('SELECT id,username FROM users')}
        c.execute("UPDATE settings SET value=? WHERE key='ceo_user'", (str(ids['ceo']),))
        c.execute("UPDATE settings SET value=? WHERE key='office_approver'", (str(ids['aliasghari']),))
        c.execute("UPDATE settings SET value=? WHERE key='warehouse_user'", (str(ids['aliasghari']),))
        c.execute("INSERT INTO projects(name,code,manager_id) VALUES('دفتر مرکزی','HQ',NULL)")
    c.commit()
    c.close()


def settings(c):
    return {r['key']: r['value'] for r in c.execute('SELECT key,value FROM settings')}


def log(c, dt, did, uid, ev, detail=''):
    c.execute('INSERT INTO log(doc_type,doc_id,user_id,event,detail,at) VALUES(?,?,?,?,?,?)',
              (dt, did, uid, ev, detail, now()))


def rows(cur):
    return [dict(r) for r in cur]


def one(cur):
    r = cur.fetchone()
    return dict(r) if r else None


def backup_loop():
    while True:
        try:
            fn = os.path.join(BACK, 'oa-%s.db' % today())
            if not os.path.exists(fn):
                src = db(); dst = sqlite3.connect(fn)
                src.backup(dst); dst.close(); src.close()
                olds = sorted(f for f in os.listdir(BACK) if f.startswith('oa-') and f.endswith('.db'))
                for f in olds[:-60]:
                    os.remove(os.path.join(BACK, f))
        except Exception as e:
            print('backup error:', e)
        time.sleep(3600)


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg); self.msg = msg; self.code = code


def need(cond, msg='دسترسی مجاز نیست', code=403):
    if not cond:
        raise ApiError(msg, code)


def is_mgr(u):
    return u['role'] in ('admin', 'manager')


def sees_all(u):
    return u['role'] in ('admin', 'manager', 'secretariat')


# ------------------------------------------------------------------ دسترسی به سند
def participant(c, u, dt, did):
    return c.execute('SELECT 1 FROM referrals WHERE doc_type=? AND doc_id=? AND (to_id=? OR from_id=?) LIMIT 1',
                     (dt, did, u['id'], u['id'])).fetchone() is not None


def can_view_letter(c, u, L):
    part = L['created_by'] == u['id'] or participant(c, u, 'letter', L['id'])
    if L['confidential'] and not is_mgr(u):
        return part
    return sees_all(u) or part


def can_view_request(c, u, R):
    if u['role'] in ('admin', 'manager', 'finance') or R['requester_id'] == u['id']:
        return True
    if c.execute('SELECT 1 FROM steps WHERE request_id=? AND approver_id=?', (R['id'], u['id'])).fetchone():
        return True
    return participant(c, u, 'request', R['id'])


def get_doc(c, u, dt, did):
    if dt == 'letter':
        d = one(c.execute('SELECT * FROM letters WHERE id=?', (did,)))
        need(d, 'نامه پیدا نشد', 404); need(can_view_letter(c, u, d))
    elif dt == 'request':
        d = one(c.execute('SELECT * FROM requests WHERE id=?', (did,)))
        need(d, 'درخواست پیدا نشد', 404); need(can_view_request(c, u, d))
    else:
        raise ApiError('نوع سند نامعتبر')
    return d


def doc_extras(c, dt, did):
    att = rows(c.execute('SELECT a.*, u.full_name uploader FROM attachments a LEFT JOIN users u ON u.id=a.uploaded_by '
                         'WHERE doc_type=? AND doc_id=? ORDER BY a.id', (dt, did)))
    refs = rows(c.execute(
        'SELECT r.*, f.full_name from_name, t.full_name to_name FROM referrals r LEFT JOIN users f ON f.id=r.from_id '
        'LEFT JOIN users t ON t.id=r.to_id WHERE doc_type=? AND doc_id=? ORDER BY r.id', (dt, did)))
    lg = rows(c.execute('SELECT l.*, u.full_name user_name FROM log l LEFT JOIN users u ON u.id=l.user_id '
                        'WHERE doc_type=? AND doc_id=? ORDER BY l.id', (dt, did)))
    return att, refs, lg


# ------------------------------------------------------------------ ارجاع
def make_referrals(c, u, dt, did, to_ids, action, instruction, due, parent_id=None):
    to_ids = [int(x) for x in (to_ids or []) if str(x).strip()]
    need(to_ids, 'گیرنده ارجاع انتخاب نشده', 400)
    need(action in ACTIONS, 'نوع اقدام نامعتبر', 400)
    names = []
    for t in to_ids:
        tu = one(c.execute('SELECT id,full_name FROM users WHERE id=? AND active=1', (t,)))
        need(tu, 'کاربر گیرنده نامعتبر', 400)
        c.execute('INSERT INTO referrals(doc_type,doc_id,parent_id,from_id,to_id,action,instruction,due_date,created_at)'
                  ' VALUES(?,?,?,?,?,?,?,?,?)', (dt, did, parent_id, u['id'], t, action, instruction or '', due or '', now()))
        names.append(tu['full_name'])
    log(c, dt, did, u['id'], 'ارجاع', '%s ← %s%s' % (action, '، '.join(names), (' : ' + instruction) if instruction else ''))
    if dt == 'letter':
        c.execute("UPDATE letters SET status='open', closed_at=NULL WHERE id=? AND status!='archived'", (did,))


# ------------------------------------------------------------------ درخواست مالی
def build_steps(c, R, uid):
    S = settings(c)
    chain = []
    proj = one(c.execute('SELECT * FROM projects WHERE id=?', (R['project_id'],))) if R['project_id'] else None
    first = (proj or {}).get('manager_id') or int(S.get('office_approver') or 0)
    if first:
        chain.append((first, 'تأیید مدیر پروژه' if (proj or {}).get('manager_id') else 'تأیید مدیر اجرایی'))
    if R['kind'] in WAREHOUSE_KINDS and S.get('warehouse_user'):
        chain.append((int(S['warehouse_user']), 'بررسی انبار و تجهیزات'))
    ceo = int(S.get('ceo_user') or 0)
    if ceo and int(R['amount'] or 0) >= int(S.get('ceo_threshold') or 0):
        chain.append((ceo, 'تأیید مدیرعامل'))
    seen, final = set(), []
    for a, l in chain:
        if a not in seen:
            seen.add(a); final.append((a, l))
    c.execute('DELETE FROM steps WHERE request_id=?', (R['id'],))
    active_set = False
    for i, (a, l) in enumerate(final, 1):
        if a == uid:
            st, note, at = 'approved', 'ثبت‌کننده درخواست', now()
        elif not active_set:
            st, note, at = 'pending', '', None; active_set = True
        else:
            st, note, at = 'waiting', '', None
        c.execute('INSERT INTO steps(request_id,step_no,approver_id,label,status,note,acted_at) VALUES(?,?,?,?,?,?,?)',
                  (R['id'], i, a, l, st, note, at))
    c.execute('UPDATE requests SET status=? WHERE id=?', ('pending' if active_set else 'approved', R['id']))


def req_fields(b):
    kind = b.get('kind')
    need(kind in REQUEST_KINDS, 'نوع درخواست نامعتبر', 400)
    title = (b.get('title') or '').strip(); need(title, 'عنوان درخواست خالی است', 400)
    try:
        amount = int(str(b.get('amount') or '0').replace(',', ''))
    except ValueError:
        raise ApiError('مبلغ نامعتبر')
    need(amount >= 0, 'مبلغ نامعتبر', 400)
    return dict(kind=kind, title=title, amount=amount, project_id=int(b['project_id']) if b.get('project_id') else None,
                payee=b.get('payee') or '', description=b.get('description') or '')


# ------------------------------------------------------------------ مسیرها
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile('^' + pattern + '$'), fn)); return fn
    return deco


@route('POST', '/api/login')
def api_login(h, c, u, b, q):
    r = one(c.execute('SELECT * FROM users WHERE username=? AND active=1', ((b.get('username') or '').strip(),)))
    if not r or hash_pw(b.get('password') or '', r['salt'])[0] != r['pw_hash']:
        raise ApiError('نام کاربری یا رمز عبور نادرست است', 401)
    tok = secrets.token_hex(24)
    c.execute('INSERT INTO sessions VALUES(?,?,?)', (tok, r['id'], now()))
    h.set_cookie = 'sid=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000' % tok
    return {'ok': True}


@route('POST', '/api/logout')
def api_logout(h, c, u, b, q):
    c.execute('DELETE FROM sessions WHERE token=?', (h.token,))
    h.set_cookie = 'sid=; Path=/; Max-Age=0'
    return {'ok': True}


@route('POST', '/api/password')
def api_password(h, c, u, b, q):
    r = one(c.execute('SELECT * FROM users WHERE id=?', (u['id'],)))
    need(hash_pw(b.get('old') or '', r['salt'])[0] == r['pw_hash'], 'رمز فعلی نادرست است', 400)
    new = b.get('new') or ''
    need(len(new) >= 6, 'رمز جدید باید حداقل ۶ کاراکتر باشد', 400)
    hh, s = hash_pw(new)
    c.execute('UPDATE users SET pw_hash=?, salt=?, must_change=0 WHERE id=?', (hh, s, u['id']))
    return {'ok': True}


@route('GET', '/api/meta')
def api_meta(h, c, u, b, q):
    S = settings(c)
    return {'me': u, 'version': VERSION, 'today': today(), 'company': S.get('company'),
            'users': rows(c.execute('SELECT id,full_name,title,role FROM users WHERE active=1 ORDER BY id')),
            'projects': rows(c.execute('SELECT id,name,code,manager_id FROM projects WHERE active=1 ORDER BY id')),
            'roles': ROLES, 'letter_kinds': LETTER_KINDS, 'actions': ACTIONS, 'sources': SOURCES,
            'request_kinds': REQUEST_KINDS, 'default_due_days': int(S.get('default_due_days') or 3),
            'ceo_threshold': int(S.get('ceo_threshold') or 0)}


DOC_LABEL_SQL = """CASE r.doc_type WHEN 'letter' THEN (SELECT number||' — '||subject FROM letters WHERE id=r.doc_id)
 ELSE (SELECT number||' — '||title FROM requests WHERE id=r.doc_id) END"""


@route('GET', '/api/cartable')
def api_cartable(h, c, u, b, q):
    inbox = rows(c.execute(
        'SELECT r.*, f.full_name from_name, %s doc_label, '
        "(SELECT priority FROM letters WHERE r.doc_type='letter' AND id=r.doc_id) priority "
        'FROM referrals r LEFT JOIN users f ON f.id=r.from_id WHERE r.to_id=? AND r.status IN %s '
        "ORDER BY (r.due_date!='' AND r.due_date<?) DESC, r.id DESC" % (DOC_LABEL_SQL, str(OPEN)), (u['id'], today())))
    sent = rows(c.execute(
        'SELECT r.*, t.full_name to_name, %s doc_label FROM referrals r LEFT JOIN users t ON t.id=r.to_id '
        'WHERE r.from_id=? AND r.status IN %s ORDER BY r.id DESC LIMIT 300' % (DOC_LABEL_SQL, str(OPEN)), (u['id'],)))
    approvals = rows(c.execute(
        'SELECT s.*, q.number, q.title, q.amount, q.kind, q.created_at, ru.full_name requester, p.name project '
        'FROM steps s JOIN requests q ON q.id=s.request_id LEFT JOIN users ru ON ru.id=q.requester_id '
        "LEFT JOIN projects p ON p.id=q.project_id WHERE s.approver_id=? AND s.status='pending' AND q.status='pending' "
        'ORDER BY s.id', (u['id'],)))
    mine = rows(c.execute(
        "SELECT q.*, p.name project FROM requests q LEFT JOIN projects p ON p.id=q.project_id "
        "WHERE q.requester_id=? AND q.status IN ('pending','returned','approved') ORDER BY q.id DESC", (u['id'],)))
    pay = []
    if u['role'] in ('finance', 'admin'):
        pay = rows(c.execute(
            "SELECT q.*, p.name project, ru.full_name requester FROM requests q LEFT JOIN projects p ON p.id=q.project_id "
            "LEFT JOIN users ru ON ru.id=q.requester_id WHERE q.status='approved' OR (q.status='paid' AND q.sepidar_no='') "
            "ORDER BY q.id"))
    desk = []
    if u['role'] in ('secretariat', 'admin'):
        desk = rows(c.execute(
            "SELECT l.* FROM letters l WHERE l.status='open' AND NOT EXISTS (SELECT 1 FROM referrals r WHERE "
            "r.doc_type='letter' AND r.doc_id=l.id AND r.status IN %s) ORDER BY l.id DESC LIMIT 200" % str(OPEN)))
    return {'inbox': inbox, 'sent': sent, 'approvals': approvals, 'mine': mine, 'pay': pay, 'desk': desk,
            'today': today()}


@route('GET', '/api/counts')
def api_counts(h, c, u, b, q):
    n = c.execute('SELECT COUNT(*) FROM referrals WHERE to_id=? AND status IN %s' % str(OPEN), (u['id'],)).fetchone()[0]
    n += c.execute("SELECT COUNT(*) FROM steps s JOIN requests q ON q.id=s.request_id WHERE s.approver_id=? "
                   "AND s.status='pending' AND q.status='pending'", (u['id'],)).fetchone()[0]
    if u['role'] in ('finance',):
        n += c.execute("SELECT COUNT(*) FROM requests WHERE status='approved'").fetchone()[0]
    return {'n': n}


# ---------- نامه‌ها
HOLDERS_SQL = ("(SELECT group_concat(u2.full_name, '، ') FROM referrals r2 JOIN users u2 ON u2.id=r2.to_id "
               "WHERE r2.doc_type='%s' AND r2.doc_id=%s.id AND r2.status IN ('new','seen','doing'))")


def letter_filter(u, q):
    w, p = ['1=1'], []
    part = ("(l.created_by=? OR EXISTS(SELECT 1 FROM referrals r WHERE r.doc_type='letter' AND r.doc_id=l.id "
            "AND (r.to_id=? OR r.from_id=?)))")
    if not sees_all(u):
        w.append(part); p += [u['id']] * 3
    elif not is_mgr(u):
        w.append('(l.confidential=0 OR ' + part + ')'); p += [u['id']] * 3
    if q.get('q'):
        w.append('(l.subject LIKE ? OR l.number LIKE ? OR l.counterpart LIKE ? OR l.summary LIKE ? OR l.their_number LIKE ?'
                 ' OR l.archive_code LIKE ?)')
        p += ['%' + q['q'] + '%'] * 6
    for k in ('kind', 'status'):
        if q.get(k):
            w.append('l.%s=?' % k); p.append(q[k])
    if q.get('project_id'):
        w.append('l.project_id=?'); p.append(int(q['project_id']))
    if q.get('from'):
        w.append('substr(l.created_at,1,10)>=?'); p.append(q['from'])
    if q.get('to'):
        w.append('substr(l.created_at,1,10)<=?'); p.append(q['to'])
    if q.get('holder'):
        w.append("EXISTS(SELECT 1 FROM referrals r WHERE r.doc_type='letter' AND r.doc_id=l.id AND r.to_id=? "
                 "AND r.status IN ('new','seen','doing'))"); p.append(int(q['holder']))
    if q.get('overdue'):
        w.append("EXISTS(SELECT 1 FROM referrals r WHERE r.doc_type='letter' AND r.doc_id=l.id AND "
                 "r.status IN ('new','seen','doing') AND r.due_date!='' AND r.due_date<?)"); p.append(today())
    return ' AND '.join(w), p


@route('GET', '/api/letters')
def api_letters(h, c, u, b, q):
    w, p = letter_filter(u, q)
    return rows(c.execute(
        'SELECT l.*, pr.name project, cu.full_name creator, %s holders FROM letters l '
        'LEFT JOIN projects pr ON pr.id=l.project_id LEFT JOIN users cu ON cu.id=l.created_by WHERE %s '
        'ORDER BY l.id DESC LIMIT %d' % (HOLDERS_SQL % ('letter', 'l'), w, int(q.get('limit') or 500)), p))


@route('GET', '/api/letters.csv')
def api_letters_csv(h, c, u, b, q):
    q['limit'] = 100000
    data = api_letters(h, c, u, b, q)
    out = io.StringIO(); wr = csv.writer(out)
    wr.writerow(['شماره', 'نوع', 'تاریخ ثبت', 'موضوع', 'طرف مکاتبه', 'شماره طرف', 'پروژه', 'وضعیت', 'در دست', 'کد بایگانی'])
    for r in data:
        wr.writerow([r['number'], LETTER_KINDS.get(r['kind']), jstr(r['created_at'])[:10], r['subject'], r['counterpart'],
                     r['their_number'], r['project'] or '', r['status'], r['holders'] or '', r['archive_code']])
    return ('csv', 'letters.csv', out.getvalue())


def letter_fields(b):
    kind = b.get('kind'); need(kind in LETTER_KINDS, 'نوع نامه نامعتبر', 400)
    subject = (b.get('subject') or '').strip(); need(subject, 'موضوع نامه خالی است', 400)
    return dict(kind=kind, subject=subject, counterpart=b.get('counterpart') or '', their_number=b.get('their_number') or '',
                their_date=b.get('their_date') or '', letter_date=b.get('letter_date') or '',
                project_id=int(b['project_id']) if b.get('project_id') else None,
                priority=b.get('priority') if b.get('priority') in ('normal', 'urgent', 'very_urgent') else 'normal',
                confidential=1 if b.get('confidential') else 0, summary=b.get('summary') or '', source=b.get('source') or '')


@route('POST', '/api/letters')
def api_letter_new(h, c, u, b, q):
    f = letter_fields(b)
    if f['kind'] in ('in', 'out'):
        need(u['role'] in ('admin', 'secretariat', 'manager'), 'ثبت نامه وارده/صادره فقط توسط دبیرخانه انجام می‌شود')
    y = jyear()
    seq = c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM letters WHERE year=? AND kind=?', (y, f['kind'])).fetchone()[0]
    number = '%s %d/%04d' % (LETTER_PREFIX[f['kind']], y, seq)
    cur = c.execute('INSERT INTO letters(kind,year,seq,number,subject,counterpart,their_number,their_date,letter_date,'
                    'project_id,priority,confidential,summary,source,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (f['kind'], y, seq, number, f['subject'], f['counterpart'], f['their_number'], f['their_date'],
                     f['letter_date'], f['project_id'], f['priority'], f['confidential'], f['summary'], f['source'],
                     u['id'], now()))
    lid = cur.lastrowid
    log(c, 'letter', lid, u['id'], 'ثبت', number)
    if b.get('to_ids'):
        make_referrals(c, u, 'letter', lid, b['to_ids'], b.get('action') or 'اقدام', b.get('instruction'), b.get('due'))
    return {'id': lid, 'number': number}


@route('GET', r'/api/letters/(\d+)')
def api_letter_get(h, c, u, b, q, lid):
    L = get_doc(c, u, 'letter', int(lid))
    L['project'] = (one(c.execute('SELECT name FROM projects WHERE id=?', (L['project_id'],))) or {}).get('name')
    L['creator'] = (one(c.execute('SELECT full_name FROM users WHERE id=?', (L['created_by'],))) or {}).get('full_name')
    att, refs, lg = doc_extras(c, 'letter', L['id'])
    # «جهت اطلاع» با دیدن، خودکار بسته می‌شود؛ بقیه به «دیده شد» تغییر می‌کنند
    for r in refs:
        if r['to_id'] == u['id'] and r['status'] == 'new':
            if r['action'] == 'جهت اطلاع':
                c.execute("UPDATE referrals SET status='done', seen_at=?, done_at=? WHERE id=?", (now(), now(), r['id']))
                r['status'] = 'done'
            else:
                c.execute("UPDATE referrals SET status='seen', seen_at=? WHERE id=?", (now(), r['id']))
                r['status'] = 'seen'
    return {'doc': L, 'attachments': att, 'referrals': refs, 'log': lg}


@route('POST', r'/api/letters/(\d+)/update')
def api_letter_update(h, c, u, b, q, lid):
    L = get_doc(c, u, 'letter', int(lid))
    need(u['role'] in ('admin', 'secretariat') or L['created_by'] == u['id'])
    f = letter_fields(dict(b, kind=L['kind']))
    c.execute('UPDATE letters SET subject=?,counterpart=?,their_number=?,their_date=?,letter_date=?,project_id=?,priority=?,'
              'confidential=?,summary=?,source=? WHERE id=?', (f['subject'], f['counterpart'], f['their_number'],
              f['their_date'], f['letter_date'], f['project_id'], f['priority'], f['confidential'], f['summary'],
              f['source'], L['id']))
    log(c, 'letter', L['id'], u['id'], 'ویرایش مشخصات')
    return {'ok': True}


@route('POST', r'/api/letters/(\d+)/archive')
def api_letter_archive(h, c, u, b, q, lid):
    L = get_doc(c, u, 'letter', int(lid))
    need(u['role'] in ('admin', 'secretariat', 'manager'), 'بایگانی توسط دبیرخانه یا هیات مدیره انجام می‌شود')
    open_n = c.execute("SELECT COUNT(*) FROM referrals WHERE doc_type='letter' AND doc_id=? AND status IN %s" % str(OPEN),
                       (L['id'],)).fetchone()[0]
    if open_n:
        need(is_mgr(u) and b.get('force'), 'این نامه هنوز %d ارجاع باز دارد؛ ابتدا باید انجام یا بسته شوند' % open_n, 400)
        c.execute("UPDATE referrals SET status='closed', done_at=?, reply=reply||' [بسته شد با بایگانی]' "
                  "WHERE doc_type='letter' AND doc_id=? AND status IN %s" % str(OPEN), (now(), L['id']))
    c.execute("UPDATE letters SET status='archived', archive_code=?, closed_at=? WHERE id=?",
              (b.get('archive_code') or '', now(), L['id']))
    log(c, 'letter', L['id'], u['id'], 'بایگانی', b.get('archive_code') or '')
    return {'ok': True}


@route('POST', r'/api/letters/(\d+)/reopen')
def api_letter_reopen(h, c, u, b, q, lid):
    L = get_doc(c, u, 'letter', int(lid))
    need(u['role'] in ('admin', 'secretariat', 'manager'))
    c.execute("UPDATE letters SET status='open', closed_at=NULL WHERE id=?", (L['id'],))
    log(c, 'letter', L['id'], u['id'], 'بازگشایی از بایگانی')
    return {'ok': True}


# ---------- ارجاع‌ها
@route('POST', '/api/refer')
def api_refer(h, c, u, b, q):
    dt, did = b.get('doc_type'), int(b.get('doc_id') or 0)
    D = get_doc(c, u, dt, did)
    pid = b.get('parent_id')
    if pid:
        P = one(c.execute('SELECT * FROM referrals WHERE id=?', (int(pid),)))
        need(P and P['to_id'] == u['id'] and P['status'] in OPEN, 'این ارجاع در دست شما نیست')
        c.execute("UPDATE referrals SET status='forwarded', done_at=?, reply=? WHERE id=?",
                  (now(), b.get('reply') or '', P['id']))
    else:
        need(sees_all(u) or u['role'] == 'finance' or participant(c, u, dt, did) or
             (dt == 'letter' and D['created_by'] == u['id']) or (dt == 'request' and D['requester_id'] == u['id']))
    make_referrals(c, u, dt, did, b.get('to_ids'), b.get('action'), b.get('instruction'), b.get('due'),
                   int(pid) if pid else None)
    return {'ok': True}


@route('POST', r'/api/referrals/(\d+)/status')
def api_ref_status(h, c, u, b, q, rid):
    R = one(c.execute('SELECT * FROM referrals WHERE id=?', (int(rid),)))
    need(R, 'ارجاع پیدا نشد', 404)
    st = b.get('status')
    if st == 'cancel':  # ارجاع‌دهنده ارجاع باز خود را پس می‌گیرد
        need(R['from_id'] == u['id'] and R['status'] in OPEN, 'فقط ارجاع‌دهنده می‌تواند ارجاع باز را لغو کند')
        c.execute("UPDATE referrals SET status='closed', done_at=?, reply='لغو توسط ارجاع‌دهنده' WHERE id=?", (now(), R['id']))
        log(c, R['doc_type'], R['doc_id'], u['id'], 'لغو ارجاع')
        return {'ok': True}
    need(R['to_id'] == u['id'] and R['status'] in OPEN, 'این ارجاع در دست شما نیست')
    need(st in ('doing', 'done', 'returned'), 'وضعیت نامعتبر', 400)
    if st in ('done', 'returned'):
        need(st == 'done' or (b.get('reply') or '').strip(), 'برای برگشت، علت را بنویسید', 400)
        c.execute('UPDATE referrals SET status=?, reply=?, done_at=? WHERE id=?', (st, b.get('reply') or '', now(), R['id']))
    else:
        c.execute("UPDATE referrals SET status='doing' WHERE id=?", (R['id'],))
    lbl = {'doing': 'در حال اقدام', 'done': 'انجام شد', 'returned': 'برگشت داده شد'}[st]
    log(c, R['doc_type'], R['doc_id'], u['id'], lbl, b.get('reply') or '')
    return {'ok': True}


# ---------- پیوست‌ها
@route('POST', '/api/attach')
def api_attach(h, c, u, b, q):
    dt, did = q.get('doc_type'), int(q.get('doc_id') or 0)
    get_doc(c, u, dt, did)
    name = urllib.parse.unquote(h.headers.get('X-Filename') or 'file')
    name = re.sub(r'[\\/:*?"<>|]', '_', os.path.basename(name))[:150] or 'file'
    raw = h.raw_body
    need(raw, 'فایل خالی است', 400)
    sub = datetime.date.today().strftime('%Y-%m')
    os.makedirs(os.path.join(FILES, sub), exist_ok=True)
    rel = os.path.join(sub, '%s_%s' % (secrets.token_hex(6), name))
    with open(os.path.join(FILES, rel), 'wb') as f:
        f.write(raw)
    c.execute('INSERT INTO attachments(doc_type,doc_id,name,path,size,uploaded_by,created_at) VALUES(?,?,?,?,?,?,?)',
              (dt, did, name, rel, len(raw), u['id'], now()))
    log(c, dt, did, u['id'], 'پیوست', name)
    return {'ok': True}


# ---------- درخواست‌های مالی
def req_filter(u, q):
    w, p = ['1=1'], []
    if u['role'] not in ('admin', 'manager', 'finance'):
        w.append("(q.requester_id=? OR EXISTS(SELECT 1 FROM steps s WHERE s.request_id=q.id AND s.approver_id=?) OR "
                 "EXISTS(SELECT 1 FROM referrals r WHERE r.doc_type='request' AND r.doc_id=q.id AND (r.to_id=? OR r.from_id=?)))")
        p += [u['id']] * 4
    if q.get('q'):
        w.append('(q.title LIKE ? OR q.number LIKE ? OR q.payee LIKE ? OR q.description LIKE ? OR q.sepidar_no LIKE ?)')
        p += ['%' + q['q'] + '%'] * 5
    for k in ('kind', 'status'):
        if q.get(k):
            w.append('q.%s=?' % k); p.append(q[k])
    if q.get('project_id'):
        w.append('q.project_id=?'); p.append(int(q['project_id']))
    if q.get('from'):
        w.append('substr(q.created_at,1,10)>=?'); p.append(q['from'])
    if q.get('to'):
        w.append('substr(q.created_at,1,10)<=?'); p.append(q['to'])
    return ' AND '.join(w), p


@route('GET', '/api/requests')
def api_requests(h, c, u, b, q):
    w, p = req_filter(u, q)
    return rows(c.execute(
        "SELECT q.*, pr.name project, ru.full_name requester, (SELECT au.full_name FROM steps s JOIN users au ON "
        "au.id=s.approver_id WHERE s.request_id=q.id AND s.status='pending') waiting_for FROM requests q "
        "LEFT JOIN projects pr ON pr.id=q.project_id LEFT JOIN users ru ON ru.id=q.requester_id WHERE %s "
        "ORDER BY q.id DESC LIMIT %d" % (w, int(q.get('limit') or 500)), p))


@route('GET', '/api/requests.csv')
def api_requests_csv(h, c, u, b, q):
    q['limit'] = 100000
    data = api_requests(h, c, u, b, q)
    out = io.StringIO(); wr = csv.writer(out)
    wr.writerow(['شماره', 'تاریخ ثبت', 'نوع', 'پروژه', 'درخواست‌کننده', 'عنوان', 'ذینفع', 'مبلغ درخواستی (ریال)',
                 'مبلغ پرداختی (ریال)', 'تاریخ پرداخت', 'شماره سند سپیدار', 'وضعیت', 'شرح'])
    for r in data:
        wr.writerow([r['number'], jstr(r['created_at'])[:10], r['kind'], r['project'] or '', r['requester'], r['title'],
                     r['payee'], r['amount'], r['paid_amount'] or '', r['paid_at'] or '', r['sepidar_no'], r['status'],
                     r['description']])
    return ('csv', 'requests.csv', out.getvalue())


@route('POST', '/api/requests')
def api_request_new(h, c, u, b, q):
    f = req_fields(b)
    y = jyear()
    seq = c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM requests WHERE year=?', (y,)).fetchone()[0]
    number = 'م %d/%04d' % (y, seq)
    cur = c.execute('INSERT INTO requests(year,seq,number,kind,project_id,requester_id,title,amount,payee,description,'
                    'created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (y, seq, number, f['kind'], f['project_id'], u['id'],
                    f['title'], f['amount'], f['payee'], f['description'], now()))
    R = one(c.execute('SELECT * FROM requests WHERE id=?', (cur.lastrowid,)))
    build_steps(c, R, u['id'])
    log(c, 'request', R['id'], u['id'], 'ثبت درخواست', number)
    return {'id': R['id'], 'number': number}


@route('GET', r'/api/requests/(\d+)')
def api_request_get(h, c, u, b, q, rid):
    R = get_doc(c, u, 'request', int(rid))
    R['project'] = (one(c.execute('SELECT name FROM projects WHERE id=?', (R['project_id'],))) or {}).get('name')
    R['requester'] = (one(c.execute('SELECT full_name FROM users WHERE id=?', (R['requester_id'],))) or {}).get('full_name')
    steps = rows(c.execute('SELECT s.*, u.full_name approver FROM steps s LEFT JOIN users u ON u.id=s.approver_id '
                           'WHERE request_id=? ORDER BY step_no', (R['id'],)))
    att, refs, lg = doc_extras(c, 'request', R['id'])
    for r in refs:
        if r['to_id'] == u['id'] and r['status'] == 'new':
            ns = 'done' if r['action'] == 'جهت اطلاع' else 'seen'
            c.execute('UPDATE referrals SET status=?, seen_at=?, done_at=? WHERE id=?',
                      (ns, now(), now() if ns == 'done' else None, r['id'])); r['status'] = ns
    return {'doc': R, 'steps': steps, 'attachments': att, 'referrals': refs, 'log': lg}


@route('POST', r'/api/requests/(\d+)/act')
def api_request_act(h, c, u, b, q, rid):
    R = get_doc(c, u, 'request', int(rid))
    need(R['status'] == 'pending', 'این درخواست در مرحله تأیید نیست', 400)
    S = one(c.execute("SELECT * FROM steps WHERE request_id=? AND status='pending'", (R['id'],)))
    need(S and S['approver_id'] == u['id'], 'تأیید این مرحله با شما نیست')
    d, note = b.get('decision'), (b.get('note') or '').strip()
    need(d in ('approve', 'reject', 'return'), 'تصمیم نامعتبر', 400)
    if d != 'approve':
        need(note, 'علت رد یا برگشت را بنویسید', 400)
    st = {'approve': 'approved', 'reject': 'rejected', 'return': 'returned'}[d]
    c.execute('UPDATE steps SET status=?, note=?, acted_at=? WHERE id=?', (st, note, now(), S['id']))
    if d == 'approve':
        nx = one(c.execute("SELECT * FROM steps WHERE request_id=? AND status='waiting' ORDER BY step_no LIMIT 1", (R['id'],)))
        if nx:
            c.execute("UPDATE steps SET status='pending' WHERE id=?", (nx['id'],))
        else:
            c.execute("UPDATE requests SET status='approved' WHERE id=?", (R['id'],))
    else:
        c.execute("UPDATE steps SET status='skipped' WHERE request_id=? AND status='waiting'", (R['id'],))
        c.execute('UPDATE requests SET status=?, closed_at=? WHERE id=?', (st, now() if d == 'reject' else None, R['id']))
    log(c, 'request', R['id'], u['id'], {'approve': 'تأیید', 'reject': 'رد', 'return': 'برگشت برای اصلاح'}[d],
        S['label'] + (' : ' + note if note else ''))
    return {'ok': True}


@route('POST', r'/api/requests/(\d+)/resubmit')
def api_request_resubmit(h, c, u, b, q, rid):
    R = get_doc(c, u, 'request', int(rid))
    need(R['requester_id'] == u['id'] and R['status'] in ('returned', 'pending'), 'فقط درخواست‌کننده، پیش از تأیید نهایی')
    f = req_fields(b)
    c.execute('UPDATE requests SET kind=?,project_id=?,title=?,amount=?,payee=?,description=?,status=? WHERE id=?',
              (f['kind'], f['project_id'], f['title'], f['amount'], f['payee'], f['description'], 'pending', R['id']))
    build_steps(c, one(c.execute('SELECT * FROM requests WHERE id=?', (R['id'],))), u['id'])
    log(c, 'request', R['id'], u['id'], 'اصلاح و ارسال مجدد')
    return {'ok': True}


@route('POST', r'/api/requests/(\d+)/cancel')
def api_request_cancel(h, c, u, b, q, rid):
    R = get_doc(c, u, 'request', int(rid))
    need((R['requester_id'] == u['id'] or u['role'] == 'admin') and R['status'] in ('pending', 'returned'),
         'لغو فقط پیش از تأیید نهایی و توسط درخواست‌کننده ممکن است')
    c.execute("UPDATE requests SET status='cancelled', closed_at=? WHERE id=?", (now(), R['id']))
    c.execute("UPDATE steps SET status='skipped' WHERE request_id=? AND status IN ('waiting','pending')", (R['id'],))
    log(c, 'request', R['id'], u['id'], 'لغو درخواست', b.get('note') or '')
    return {'ok': True}


@route('POST', r'/api/requests/(\d+)/pay')
def api_request_pay(h, c, u, b, q, rid):
    R = get_doc(c, u, 'request', int(rid))
    need(u['role'] in ('finance', 'admin'), 'ثبت پرداخت فقط توسط مالی')
    if R['status'] == 'approved':
        try:
            amt = int(str(b.get('paid_amount') or R['amount']).replace(',', ''))
        except ValueError:
            raise ApiError('مبلغ نامعتبر')
        sep = (b.get('sepidar_no') or '').strip()
        c.execute('UPDATE requests SET status=?, paid_amount=?, paid_at=?, pay_note=?, sepidar_no=?, closed_at=? WHERE id=?',
                  ('closed' if sep else 'paid', amt, b.get('paid_at') or jstr(today()), b.get('pay_note') or '', sep,
                   now() if sep else None, R['id']))
        log(c, 'request', R['id'], u['id'], 'پرداخت', '%s ریال%s' % (format(amt, ','), ' — سند سپیدار ' + sep if sep else ''))
    else:
        need(R['status'] in ('paid', 'closed'), 'درخواست هنوز تأیید نهایی نشده', 400)
        sep = (b.get('sepidar_no') or '').strip(); need(sep, 'شماره سند سپیدار را وارد کنید', 400)
        c.execute("UPDATE requests SET sepidar_no=?, status='closed', closed_at=? WHERE id=?", (sep, now(), R['id']))
        log(c, 'request', R['id'], u['id'], 'ثبت سند سپیدار', sep)
    return {'ok': True}


# ---------- گزارش‌ها
@route('GET', '/api/reports')
def api_reports(h, c, u, b, q):
    need(u['role'] in ('admin', 'manager', 'secretariat', 'finance'), 'گزارش‌ها برای مدیران، دبیرخانه و مالی است')
    t = today()
    people = rows(c.execute(
        "SELECT u.id, u.full_name, u.title,"
        " (SELECT COUNT(*) FROM referrals r WHERE r.to_id=u.id AND r.status IN ('new','seen','doing')) open_n,"
        " (SELECT COUNT(*) FROM referrals r WHERE r.to_id=u.id AND r.status IN ('new','seen','doing') AND r.due_date!='' AND r.due_date<?) overdue_n,"
        " (SELECT COUNT(*) FROM referrals r WHERE r.to_id=u.id AND r.status='new') unseen_n,"
        " (SELECT COUNT(*) FROM referrals r WHERE r.to_id=u.id AND r.status IN ('done','forwarded') AND r.done_at>=date('now','-30 day')) done30,"
        " (SELECT ROUND(AVG((julianday(r.done_at)-julianday(r.created_at))*24),1) FROM referrals r WHERE r.to_id=u.id AND r.status IN ('done','forwarded') AND r.done_at>=date('now','-90 day') AND r.action!='جهت اطلاع') avg_hours,"
        " (SELECT MIN(r.created_at) FROM referrals r WHERE r.to_id=u.id AND r.status IN ('new','seen','doing')) oldest,"
        " (SELECT COUNT(*) FROM steps s JOIN requests q ON q.id=s.request_id WHERE s.approver_id=u.id AND s.status='pending' AND q.status='pending') approvals_n"
        " FROM users u WHERE u.active=1 ORDER BY overdue_n DESC, open_n DESC", (t,)))
    overdue = rows(c.execute(
        "SELECT r.*, t.full_name to_name, f.full_name from_name, %s doc_label FROM referrals r "
        "LEFT JOIN users t ON t.id=r.to_id LEFT JOIN users f ON f.id=r.from_id WHERE r.status IN ('new','seen','doing') "
        "AND r.due_date!='' AND r.due_date<? ORDER BY r.due_date" % DOC_LABEL_SQL, (t,)))
    y = jyear()
    letters = rows(c.execute("SELECT kind, status, COUNT(*) n FROM letters WHERE year=? GROUP BY kind, status", (y,)))
    reqs = rows(c.execute("SELECT status, COUNT(*) n, SUM(amount) amount, SUM(paid_amount) paid FROM requests WHERE year=? "
                          "GROUP BY status", (y,)))
    by_project = rows(c.execute(
        "SELECT COALESCE(p.name,'بدون پروژه') project, COUNT(*) n, SUM(CASE WHEN q.status IN ('approved','paid','closed') "
        "THEN q.amount ELSE 0 END) approved, SUM(COALESCE(q.paid_amount,0)) paid FROM requests q LEFT JOIN projects p "
        "ON p.id=q.project_id WHERE q.year=? AND q.status NOT IN ('cancelled','rejected') GROUP BY p.name ORDER BY paid DESC", (y,)))
    no_sep = c.execute("SELECT COUNT(*) FROM requests WHERE status='paid' AND sepidar_no=''").fetchone()[0]
    return {'people': people, 'overdue': overdue, 'letters': letters, 'requests': reqs, 'by_project': by_project,
            'no_sepidar': no_sep, 'year': y}


@route('POST', '/api/projects')
def api_project_new(h, c, u, b, q):
    need(u['role'] in ('admin', 'manager', 'secretariat', 'finance'), 'تعریف پروژه برای مدیران، دبیرخانه و مالی است')
    name = (b.get('name') or '').strip(); need(name, 'نام پروژه الزامی است', 400)
    need(not c.execute('SELECT 1 FROM projects WHERE name=? AND active=1', (name,)).fetchone(), 'این پروژه قبلاً تعریف شده', 400)
    mid = int(b['manager_id']) if b.get('manager_id') else None
    cur = c.execute('INSERT INTO projects(name,code,manager_id) VALUES(?,?,?)', (name, (b.get('code') or '').strip(), mid))
    return one(c.execute('SELECT id,name,code,manager_id FROM projects WHERE id=?', (cur.lastrowid,)))


# ---------- مدیریت
@route('GET', '/api/admin')
def api_admin(h, c, u, b, q):
    need(u['role'] == 'admin')
    return {'users': rows(c.execute('SELECT id,username,full_name,title,role,active,must_change FROM users ORDER BY id')),
            'projects': rows(c.execute('SELECT * FROM projects ORDER BY id')), 'settings': settings(c),
            'backups': sorted(os.listdir(BACK))[-10:]}


@route('POST', '/api/admin/user')
def api_admin_user(h, c, u, b, q):
    need(u['role'] == 'admin')
    need(b.get('role') in ROLES and (b.get('full_name') or '').strip() and (b.get('username') or '').strip(),
         'نام، نام کاربری و نقش الزامی است', 400)
    if b.get('id'):
        c.execute('UPDATE users SET username=?,full_name=?,title=?,role=?,active=? WHERE id=?',
                  (b['username'].strip(), b['full_name'].strip(), b.get('title') or '', b['role'],
                   1 if b.get('active', True) else 0, int(b['id'])))
    else:
        hh, s = hash_pw('1234')
        try:
            c.execute('INSERT INTO users(username,full_name,title,role,pw_hash,salt) VALUES(?,?,?,?,?,?)',
                      (b['username'].strip(), b['full_name'].strip(), b.get('title') or '', b['role'], hh, s))
        except sqlite3.IntegrityError:
            raise ApiError('این نام کاربری تکراری است')
    return {'ok': True}


@route('POST', '/api/admin/reset')
def api_admin_reset(h, c, u, b, q):
    need(u['role'] == 'admin')
    hh, s = hash_pw('1234')
    c.execute('UPDATE users SET pw_hash=?, salt=?, must_change=1 WHERE id=?', (hh, s, int(b['id'])))
    c.execute('DELETE FROM sessions WHERE user_id=?', (int(b['id']),))
    return {'ok': True}


@route('POST', '/api/admin/project')
def api_admin_project(h, c, u, b, q):
    need(u['role'] == 'admin')
    need((b.get('name') or '').strip(), 'نام پروژه الزامی است', 400)
    mid = int(b['manager_id']) if b.get('manager_id') else None
    if b.get('id'):
        c.execute('UPDATE projects SET name=?,code=?,manager_id=?,active=? WHERE id=?',
                  (b['name'].strip(), b.get('code') or '', mid, 1 if b.get('active', True) else 0, int(b['id'])))
    else:
        c.execute('INSERT INTO projects(name,code,manager_id) VALUES(?,?,?)', (b['name'].strip(), b.get('code') or '', mid))
    return {'ok': True}


@route('POST', '/api/admin/settings')
def api_admin_settings(h, c, u, b, q):
    need(u['role'] == 'admin')
    for k in DEFAULT_SETTINGS:
        if k in b:
            c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (k, str(b[k]).replace(',', '')
                      if k == 'ceo_threshold' else str(b[k])))
    return {'ok': True}


# ------------------------------------------------------------------ سرور HTTP
class H(BaseHTTPRequestHandler):
    server_version = 'OmranZistOA/' + VERSION

    def log_message(self, fmt, *a):
        pass

    def send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        if getattr(self, 'set_cookie', None):
            self.send_header('Set-Cookie', self.set_cookie)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def user(self, c):
        ck = cookies.SimpleCookie(self.headers.get('Cookie') or '')
        self.token = ck['sid'].value if 'sid' in ck else ''
        if not self.token:
            return None
        return one(c.execute('SELECT u.id,u.username,u.full_name,u.title,u.role,u.must_change FROM sessions s JOIN users u '
                             'ON u.id=s.user_id WHERE s.token=? AND u.active=1', (self.token,)))

    def do_GET(self):
        self.handle_req('GET')

    def do_POST(self):
        self.handle_req('POST')

    def handle_req(self, method):
        self.set_cookie = None
        url = urllib.parse.urlparse(self.path)
        path = url.path
        q = {k: v[0].translate(FA2EN) for k, v in urllib.parse.parse_qs(url.query).items()}
        if method == 'GET' and path in ('/', '/index.html'):
            with open(os.path.join(BASE, 'index.html'), 'rb') as f:
                return self.send(200, f.read(), 'text/html; charset=utf-8')
        c = db()
        try:
            u = self.user(c)
            m = re.match(r'^/files/(\d+)$', path)
            if m and method == 'GET':
                need(u, 'ابتدا وارد شوید', 401)
                a = one(c.execute('SELECT * FROM attachments WHERE id=?', (int(m.group(1)),)))
                need(a, 'فایل پیدا نشد', 404)
                get_doc(c, u, a['doc_type'], a['doc_id'])
                with open(os.path.join(FILES, a['path']), 'rb') as f:
                    data = f.read()
                ct = mimetypes.guess_type(a['name'])[0] or 'application/octet-stream'
                disp = 'inline' if ('dl' not in q) else 'attachment'
                return self.send(200, data, ct, {'Content-Disposition': "%s; filename*=UTF-8''%s" %
                                                 (disp, urllib.parse.quote(a['name']))})
            n = int(self.headers.get('Content-Length') or 0)
            need(n <= MAX_UPLOAD, 'حجم فایل بیش از حد مجاز است', 413)
            raw = self.rfile.read(n) if n else b''
            self.raw_body = raw
            body = {}
            if raw and (self.headers.get('Content-Type') or '').startswith('application/json'):
                body = json.loads(raw.decode('utf-8'))
            for meth, rx, fn in ROUTES:
                mm = rx.match(path)
                if meth == method and mm:
                    if path != '/api/login':
                        need(u, 'ابتدا وارد شوید', 401)
                    res = fn(self, c, u, body, q, *mm.groups())
                    c.commit()
                    if isinstance(res, tuple) and res[0] == 'csv':
                        return self.send(200, '﻿' + res[2], 'text/csv; charset=utf-8',
                                         {'Content-Disposition': 'attachment; filename=%s' % res[1]})
                    return self.send(200, json.dumps(res, ensure_ascii=False))
            raise ApiError('مسیر پیدا نشد', 404)
        except ApiError as e:
            c.rollback()
            self.send(e.code, json.dumps({'error': e.msg}, ensure_ascii=False))
        except Exception as e:
            c.rollback()
            import traceback; traceback.print_exc()
            self.send(500, json.dumps({'error': 'خطای داخلی: %s' % e}, ensure_ascii=False))
        finally:
            c.close()


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    print('سامانه اتوماسیون عمران زیست — نسخه %s (با انتخاب خودکار پورت)' % VERSION)
    init_db()
    if '--reset-admin' in sys.argv:
        c = db(); hh, ss = hash_pw('1234')
        c.execute("UPDATE users SET pw_hash=?, salt=?, must_change=1, active=1 WHERE username='admin'", (hh, ss))
        c.commit(); c.close(); print('رمز admin به 1234 برگشت.'); return
    threading.Thread(target=backup_loop, daemon=True).start()
    srv, port = None, None
    for p in [PORT] + [x for x in CANDIDATE_PORTS if x != PORT]:
        try:
            srv = ThreadingHTTPServer(('0.0.0.0', p), H); port = p; break
        except OSError as e:
            print('پورت %d قابل استفاده نیست (%s)؛ پورت بعدی امتحان می‌شود...' % (p, e.strerror or e))
    if not srv:
        print('هیچ‌کدام از پورت‌های %s آزاد نبود. یک عدد پورت آزاد را در فایل port.txt بنویسید.' % CANDIDATE_PORTS)
        return
    if port != PORT or not os.path.exists(PORT_FILE):
        with open(PORT_FILE, 'w') as f:
            f.write(str(port))
    print('سامانه اتوماسیون اداری عمران زیست — نسخه %s' % VERSION)
    print('در حال اجرا روی پورت %d  —  آدرس در مرورگر: http://<IP این کامپیوتر>:%d' % (port, port))
    print('این پورت در فایل port.txt ذخیره شد و دفعات بعد هم همین استفاده می‌شود.')
    print('برای توقف، این پنجره را ببندید.')
    srv.serve_forever()


if __name__ == '__main__':
    main()
