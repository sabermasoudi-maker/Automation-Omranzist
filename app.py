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
VERSION = '1.4'

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

# ارکان هر پروژه و واحدهای درخواست‌کننده (زیرتب‌های پروژه)
PROJECT_ROLES = [('pm', 'مدیر پروژه'), ('supervisor', 'سرپرست کارگاه'), ('exec', 'معاون اجرایی'),
                 ('tech', 'معاون فنی'), ('support', 'پشتیبانی'), ('warehouse', 'انبار')]
UNITS = [('tech', 'فنی'), ('exec', 'اجرایی'), ('support', 'پشتیبانی'), ('warehouse', 'انبار')]
ROLE_UNIT = {'pm': 'exec', 'supervisor': 'exec', 'exec': 'exec', 'tech': 'tech', 'support': 'support',
             'warehouse': 'warehouse'}
HQ_ROLES = {'support_manager': 'مدیر پشتیبانی دفتر مرکزی', 'finance_manager': 'مدیر مالی دفتر مرکزی',
            'archive_user': 'منشی (بایگانی)'}

# گردش درخواست کالا: مرحله ← {اقدام: (مرحله بعد، شرح)}
P_STAGES = {'draft': 'ثبت درخواست', 'unit_approval': 'تأیید رئیس واحد',
            'supervisor_review': 'بررسی سرپرست کارگاه',  # فقط برای درخواست‌های نسخه‌های قبل
            'warehouse_check': 'استعلام موجودی از انبار کارگاه', 'tech_review': 'بررسی معاون فنی',
            'supervisor_approve': 'تأیید سرپرست کارگاه', 'pm_approve': 'بررسی مدیر پروژه',
            'hq_purchase': 'خرید — پشتیبانی دفتر مرکزی', 'finance_pay': 'پرداخت — مالی دفتر مرکزی',
            'archive': 'بایگانی — دبیرخانه', 'returned': 'برگشت به درخواست‌کننده', 'done': 'پایان'}
_RET = ('returned', 'برگشت به درخواست‌کننده برای اصلاح')
P_FLOW = {
    'unit_approval': {'approve': ('warehouse_check', 'تأیید رئیس واحد و ارسال استعلام به انبار کارگاه'), 'return': _RET},
    'supervisor_review': {'inquire': ('warehouse_check', 'ارسال استعلام به انبار کارگاه'), 'return': _RET},
    'warehouse_check': {'in_stock': ('done', 'موجود است — حواله خروج و تحویل به درخواست‌کننده'),
                        'not_in_stock': ('supervisor_approve', 'موجود نیست')},
    'tech_review': {'approve': ('supervisor_approve', 'تأیید معاون فنی'), 'return': _RET},
    'supervisor_approve': {'approve': ('pm_approve', 'تأیید سرپرست کارگاه و ارسال به مدیر پروژه'), 'return': _RET},
    'pm_approve': {'approve': ('hq_purchase', 'تأیید مدیر پروژه و ارسال به پشتیبانی دفتر مرکزی'), 'return': _RET},
    'hq_purchase': {'purchased': ('finance_pay', 'خرید انجام شد — ارسال به مالی')},
    'finance_pay': {'paid': ('archive', 'پرداخت شد — ارسال به دبیرخانه برای بایگانی')},
    'archive': {'archived': ('done', 'بایگانی شد')},
}
STAGE_HOLDER = {'supervisor_review': ('member', 'supervisor'), 'warehouse_check': ('member', 'warehouse'),
                'tech_review': ('member', 'tech'), 'supervisor_approve': ('member', 'supervisor'),
                'pm_approve': ('member', 'pm'),
                'hq_purchase': ('setting', 'support_manager'), 'finance_pay': ('setting', 'finance_manager'),
                'archive': ('setting', 'archive_user')}
UNIT_HEAD = {'tech': 'tech', 'exec': 'exec', 'support': 'supervisor', 'warehouse': 'supervisor'}  # رئیس هر واحد
HEAD_ROLES = ('tech', 'exec', 'supervisor')  # درخواست این افراد، خودش تأیید رئیس واحد است
# تا پیش از رسیدن به مدیر پروژه ویرایش و لغو ممکن است؛ مدیر پروژه فقط لغو (ابطال) می‌کند
EDIT_STAGES = ('unit_approval', 'supervisor_review', 'warehouse_check', 'tech_review', 'supervisor_approve', 'returned')
CANCEL_STAGES = EDIT_STAGES + ('pm_approve',)
CATEGORIES = [('main', 'مصالح اصلی'), ('general', 'عمومی و مصرفی')]
URGENCIES = [('normal', 'عادی'), ('emergency', 'اضطراری')]
ATT_KINDS = ['پیش‌فاکتور', 'فاکتور', 'مشخصات فنی', 'نقشه / متره', 'رسید', 'صورت‌جلسه', 'عکس', 'سایر']
DEFAULT_CANCEL_REASONS = 'نیاز نیست\nبودجه تأمین نیست\nتکراری است\nزمان‌بندی اجازه نمی‌دهد\nسایر'
P_STATUS = {'open': 'در جریان', 'returned': 'برگشت برای اصلاح', 'delivered': 'تحویل از انبار',
            'closed': 'خریداری، پرداخت و بایگانی شد', 'rejected': 'رد شد', 'cancelled': 'لغو شد (بایگانی)'}
SEED_PROJECTS = ['موادکاران', 'پروژه بدون نام ۱', 'پروژه بدون نام ۲']
# حساب‌های سمت‌های پروژه موادکاران (موقت؛ مدیر سیستم بعداً نام، شخص یا حساب را عوض می‌کند)
SEED_MK_POSTS = [('supervisor', 'mk-sarparast', 'سرپرست کارگاه موادکاران'),
                 ('exec', 'mk-ejraei', 'معاون اجرایی موادکاران'),
                 ('tech', 'mk-fanni', 'معاون فنی موادکاران'),
                 ('support', 'mk-poshtibani', 'پشتیبانی کارگاه موادکاران'),
                 ('warehouse', 'mk-anbar', 'انباردار موادکاران')]

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
CREATE TABLE IF NOT EXISTS project_members(project_id INTEGER NOT NULL, role_key TEXT NOT NULL, user_id INTEGER NOT NULL,
  PRIMARY KEY(project_id, role_key));
CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY, year INTEGER, seq INTEGER, number TEXT, project_id INTEGER NOT NULL,
  unit TEXT DEFAULT '', warehouse TEXT DEFAULT '', purpose TEXT DEFAULT '', requester_id INTEGER, req_date TEXT,
  stage TEXT, status TEXT DEFAULT 'open', holder_id INTEGER, supplier TEXT DEFAULT '', amount INTEGER, paid_amount INTEGER,
  paid_at TEXT DEFAULT '', sepidar_no TEXT DEFAULT '', archive_code TEXT DEFAULT '', created_at TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS purchase_items(id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL, row_no INTEGER, title TEXT,
  qty TEXT DEFAULT '', unit TEXT DEFAULT '', spec TEXT DEFAULT '', note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS purchase_flow(id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL, stage TEXT, action TEXT,
  label TEXT, user_id INTEGER, note TEXT DEFAULT '', at TEXT);
CREATE TABLE IF NOT EXISTS purchase_versions(id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL, version INTEGER,
  data TEXT, user_id INTEGER, note TEXT DEFAULT '', at TEXT);
CREATE INDEX IF NOT EXISTS ix_pur_ver ON purchase_versions(purchase_id);
CREATE INDEX IF NOT EXISTS ix_pur_holder ON purchases(holder_id, status);
CREATE INDEX IF NOT EXISTS ix_pur_proj ON purchases(project_id);
CREATE INDEX IF NOT EXISTS ix_pur_items ON purchase_items(purchase_id);
CREATE INDEX IF NOT EXISTS ix_pur_flow ON purchase_flow(purchase_id);
CREATE INDEX IF NOT EXISTS ix_ref_to ON referrals(to_id, status);
CREATE INDEX IF NOT EXISTS ix_ref_doc ON referrals(doc_type, doc_id);
CREATE INDEX IF NOT EXISTS ix_steps ON steps(request_id);
CREATE INDEX IF NOT EXISTS ix_att ON attachments(doc_type, doc_id);
CREATE INDEX IF NOT EXISTS ix_log ON log(doc_type, doc_id);
"""

DEFAULT_SETTINGS = {'company': 'شرکت گسترش فناوری عمران زیست', 'ceo_threshold': '1000000000',
                    'ceo_user': '', 'office_approver': '', 'warehouse_user': '', 'default_due_days': '3',
                    'support_manager': '', 'finance_manager': '', 'archive_user': '',
                    'cancel_reasons': DEFAULT_CANCEL_REASONS}

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
    add_columns(c)
    migrate(c)
    migrate_v13(c)
    c.commit()
    c.close()


def add_columns(c):
    """ستون‌های اضافه‌شده در نسخه ۱.۴ (افزودنی؛ داده‌ای حذف نمی‌شود)."""
    want = {'purchases': [('category', "TEXT DEFAULT 'general'"), ('urgency', "TEXT DEFAULT 'normal'"),
                          ('need_date', "TEXT DEFAULT ''"), ('version', 'INTEGER DEFAULT 1'),
                          ('cancel_reason', "TEXT DEFAULT ''"), ('cancel_note', "TEXT DEFAULT ''")],
            'attachments': [('kind', "TEXT DEFAULT ''")]}
    for t, cols in want.items():
        have = {r[1] for r in c.execute('PRAGMA table_info(%s)' % t)}
        for name, decl in cols:
            if name not in have:
                c.execute('ALTER TABLE %s ADD COLUMN %s %s' % (t, name, decl))


def migrate(c):
    """ارتقای پایگاه داده نسخه‌های قبلی؛ فقط یک بار اجرا می‌شود."""
    c.execute("INSERT OR IGNORE INTO project_members(project_id,role_key,user_id) "
              "SELECT id,'pm',manager_id FROM projects WHERE manager_id IS NOT NULL")
    if settings(c).get('seed_v12'):
        return
    ids = {r['username']: r['id'] for r in c.execute('SELECT id,username FROM users')}
    for key, un in (('support_manager', 'support'), ('finance_manager', 'finance'), ('archive_user', 'secretary')):
        if un in ids:
            c.execute("UPDATE settings SET value=? WHERE key=? AND value=''", (str(ids[un]), key))
    for name in SEED_PROJECTS:
        if c.execute('SELECT 1 FROM projects WHERE name=?', (name,)).fetchone():
            continue
        mid = ids.get('kasaeian') if name == 'موادکاران' else None
        pid = c.execute('INSERT INTO projects(name,code,manager_id) VALUES(?,?,?)', (name, '', mid)).lastrowid
        sync_pm(c, pid, mid)
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('seed_v12','1')")


def migrate_v13(c):
    """سمت‌های خالی پروژه موادکاران با حساب‌های سمتی پر می‌شود (رمز اولیه 1234)."""
    if settings(c).get('seed_v13'):
        return
    pr = c.execute("SELECT id FROM projects WHERE name='موادکاران'").fetchone()
    if pr:
        for key, un, fn in SEED_MK_POSTS:
            if c.execute('SELECT 1 FROM project_members WHERE project_id=? AND role_key=?', (pr[0], key)).fetchone():
                continue
            r = c.execute('SELECT id FROM users WHERE username=?', (un,)).fetchone()
            if r:
                uid = r[0]
            else:
                hh, ss = hash_pw('1234')
                uid = c.execute('INSERT INTO users(username,full_name,title,role,pw_hash,salt) VALUES(?,?,?,?,?,?)',
                                (un, fn, fn, 'staff', hh, ss)).lastrowid
            c.execute('INSERT INTO project_members(project_id,role_key,user_id) VALUES(?,?,?)', (pr[0], key, uid))
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('seed_v13','1')")


def sync_pm(c, pid, uid):
    """مدیر پروژه هم در ارکان و هم در projects.manager_id (تأییدکننده اول درخواست مالی) نگه داشته می‌شود."""
    if uid:
        c.execute("INSERT OR REPLACE INTO project_members(project_id,role_key,user_id) VALUES(?,'pm',?)", (pid, uid))
    else:
        c.execute("DELETE FROM project_members WHERE project_id=? AND role_key='pm'", (pid,))
    c.execute('UPDATE projects SET manager_id=? WHERE id=?', (uid or None, pid))


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


TOP_ROLES = ('admin', 'manager')  # بالاترین سطح دسترسی: مدیر سیستم و هیات مدیره


def is_mgr(u):
    return u['role'] in TOP_ROLES


def sees_all(u):
    return u['role'] in ('admin', 'manager', 'secretariat')


def is_broad(c, u):
    """کسانی که همه درخواست‌های خرید همه پروژه‌ها را می‌بینند."""
    return is_mgr(u) or u['role'] in ('finance', 'secretariat') or str(u['id']) == settings(c).get('support_manager')


HQ_SETTING_USERS = ('support_manager', 'finance_manager', 'archive_user', 'ceo_user', 'office_approver', 'warehouse_user')


def is_site_only(c, u):
    """کاربر کارگاهی: کارمندی که عضو ارکان پروژه است و سمتی در دفتر مرکزی ندارد؛ بخش‌های دفتر مرکزی را نمی‌بیند."""
    if u['role'] != 'staff':
        return False
    S = settings(c)
    if str(u['id']) in {S.get(k) for k in HQ_SETTING_USERS}:
        return False
    return c.execute('SELECT 1 FROM project_members WHERE user_id=? LIMIT 1', (u['id'],)).fetchone() is not None


def no_site(c, u):
    need(not is_site_only(c, u), 'بخش‌های دفتر مرکزی برای کاربران کارگاه در دسترس نیست')


def is_member(c, u, pid):
    return c.execute('SELECT 1 FROM project_members WHERE project_id=? AND user_id=?', (pid, u['id'])).fetchone() is not None


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


def can_view_purchase(c, u, P):
    if is_broad(c, u) or u['id'] in (P['requester_id'], P['holder_id']) or is_member(c, u, P['project_id']):
        return True
    if c.execute('SELECT 1 FROM purchase_flow WHERE purchase_id=? AND user_id=?', (P['id'], u['id'])).fetchone():
        return True
    return participant(c, u, 'purchase', P['id'])


def get_doc(c, u, dt, did):
    if dt == 'letter':
        d = one(c.execute('SELECT * FROM letters WHERE id=?', (did,)))
        need(d, 'نامه پیدا نشد', 404); need(can_view_letter(c, u, d))
    elif dt == 'request':
        d = one(c.execute('SELECT * FROM requests WHERE id=?', (did,)))
        need(d, 'درخواست پیدا نشد', 404); need(can_view_request(c, u, d))
    elif dt == 'purchase':
        d = one(c.execute('SELECT * FROM purchases WHERE id=?', (did,)))
        need(d, 'درخواست کالا پیدا نشد', 404); need(can_view_purchase(c, u, d))
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
            'ceo_threshold': int(S.get('ceo_threshold') or 0),
            'project_roles': PROJECT_ROLES, 'units': UNITS, 'role_unit': ROLE_UNIT, 'p_stages': P_STAGES,
            'p_status': P_STATUS, 'hq_roles': HQ_ROLES, 'broad': is_broad(c, u), 'site_only': is_site_only(c, u),
            'categories': CATEGORIES, 'urgencies': URGENCIES, 'att_kinds': ATT_KINDS,
            'cancel_reasons': cancel_reasons(c),
            'my_roles': rows(c.execute('SELECT project_id, role_key FROM project_members WHERE user_id=?', (u['id'],)))}


PUR_SEL = ("SELECT x.*, pr.name project, pr.code project_code, ru.full_name requester, hu.full_name holder, "
           "(SELECT group_concat(title, '، ') FROM purchase_items WHERE purchase_id=x.id) items_text FROM purchases x "
           "LEFT JOIN projects pr ON pr.id=x.project_id LEFT JOIN users ru ON ru.id=x.requester_id "
           "LEFT JOIN users hu ON hu.id=x.holder_id ")


DOC_LABEL_SQL = """CASE r.doc_type WHEN 'letter' THEN (SELECT number||' — '||subject FROM letters WHERE id=r.doc_id)
 WHEN 'purchase' THEN (SELECT number||' — درخواست کالا' FROM purchases WHERE id=r.doc_id)
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
    if u['role'] == 'finance' or is_mgr(u):
        pay = rows(c.execute(
            "SELECT q.*, p.name project, ru.full_name requester FROM requests q LEFT JOIN projects p ON p.id=q.project_id "
            "LEFT JOIN users ru ON ru.id=q.requester_id WHERE q.status='approved' OR (q.status='paid' AND q.sepidar_no='') "
            "ORDER BY q.id"))
    desk = []
    if u['role'] == 'secretariat' or is_mgr(u):
        desk = rows(c.execute(
            "SELECT l.* FROM letters l WHERE l.status='open' AND NOT EXISTS (SELECT 1 FROM referrals r WHERE "
            "r.doc_type='letter' AND r.doc_id=l.id AND r.status IN %s) ORDER BY l.id DESC LIMIT 200" % str(OPEN)))
    pur_held = rows(c.execute(PUR_SEL + "WHERE x.holder_id=? AND x.status IN ('open','returned') ORDER BY x.id", (u['id'],)))
    pur_mine = rows(c.execute(PUR_SEL + "WHERE x.requester_id=? AND x.status IN ('open','returned') ORDER BY x.id DESC",
                              (u['id'],)))
    return {'inbox': inbox, 'sent': sent, 'approvals': approvals, 'mine': mine, 'pay': pay, 'desk': desk,
            'pur_held': pur_held, 'pur_mine': pur_mine, 'today': today()}


@route('GET', '/api/counts')
def api_counts(h, c, u, b, q):
    n = c.execute('SELECT COUNT(*) FROM referrals WHERE to_id=? AND status IN %s' % str(OPEN), (u['id'],)).fetchone()[0]
    n += c.execute("SELECT COUNT(*) FROM steps s JOIN requests q ON q.id=s.request_id WHERE s.approver_id=? "
                   "AND s.status='pending' AND q.status='pending'", (u['id'],)).fetchone()[0]
    if u['role'] in ('finance',):
        n += c.execute("SELECT COUNT(*) FROM requests WHERE status='approved'").fetchone()[0]
    n += c.execute("SELECT COUNT(*) FROM purchases WHERE holder_id=? AND status IN ('open','returned')",
                   (u['id'],)).fetchone()[0]
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
    no_site(c, u)
    w, p = letter_filter(u, q)
    return rows(c.execute(
        'SELECT l.*, pr.name project, cu.full_name creator, %s holders FROM letters l '
        'LEFT JOIN projects pr ON pr.id=l.project_id LEFT JOIN users cu ON cu.id=l.created_by WHERE %s '
        'ORDER BY l.id DESC LIMIT %d' % (HOLDERS_SQL % ('letter', 'l'), w, int(q.get('limit') or 500)), p))


@route('GET', '/api/letters.csv')
def api_letters_csv(h, c, u, b, q):
    no_site(c, u)
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
    no_site(c, u)
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
    need(is_mgr(u) or u['role'] == 'secretariat' or L['created_by'] == u['id'])
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
    kind = urllib.parse.unquote(h.headers.get('X-Kind') or '')
    kind = kind if kind in ATT_KINDS else ''
    c.execute('INSERT INTO attachments(doc_type,doc_id,name,path,size,uploaded_by,created_at,kind) VALUES(?,?,?,?,?,?,?,?)',
              (dt, did, name, rel, len(raw), u['id'], now(), kind))
    log(c, dt, did, u['id'], 'پیوست' + (' — ' + kind if kind else ''), name)
    if dt == 'purchase':
        c.execute('INSERT INTO purchase_flow(purchase_id,stage,action,label,user_id,note,at) VALUES(?,?,?,?,?,?,?)',
                  (did, (one(c.execute('SELECT stage FROM purchases WHERE id=?', (did,))) or {}).get('stage'), 'attach',
                   'پیوست ' + (kind or 'فایل'), u['id'], name, now()))
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
    no_site(c, u)
    w, p = req_filter(u, q)
    return rows(c.execute(
        "SELECT q.*, pr.name project, ru.full_name requester, (SELECT au.full_name FROM steps s JOIN users au ON "
        "au.id=s.approver_id WHERE s.request_id=q.id AND s.status='pending') waiting_for FROM requests q "
        "LEFT JOIN projects pr ON pr.id=q.project_id LEFT JOIN users ru ON ru.id=q.requester_id WHERE %s "
        "ORDER BY q.id DESC LIMIT %d" % (w, int(q.get('limit') or 500)), p))


@route('GET', '/api/requests.csv')
def api_requests_csv(h, c, u, b, q):
    no_site(c, u)
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
    no_site(c, u)
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
    need((R['requester_id'] == u['id'] or is_mgr(u)) and R['status'] in ('pending', 'returned'),
         'لغو فقط پیش از تأیید نهایی و توسط درخواست‌کننده ممکن است')
    c.execute("UPDATE requests SET status='cancelled', closed_at=? WHERE id=?", (now(), R['id']))
    c.execute("UPDATE steps SET status='skipped' WHERE request_id=? AND status IN ('waiting','pending')", (R['id'],))
    log(c, 'request', R['id'], u['id'], 'لغو درخواست', b.get('note') or '')
    return {'ok': True}


@route('POST', r'/api/requests/(\d+)/pay')
def api_request_pay(h, c, u, b, q, rid):
    R = get_doc(c, u, 'request', int(rid))
    need(u['role'] == 'finance' or is_mgr(u), 'ثبت پرداخت فقط توسط مالی یا هیات مدیره')
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


# ---------- پروژه‌ها و ارکان
@route('GET', r'/api/projects/(\d+)')
def api_project_get(h, c, u, b, q, pid):
    pr = one(c.execute('SELECT * FROM projects WHERE id=?', (int(pid),)))
    need(pr, 'پروژه پیدا نشد', 404)
    mem = rows(c.execute('SELECT m.role_key, m.user_id, us.full_name FROM project_members m LEFT JOIN users us '
                         'ON us.id=m.user_id WHERE m.project_id=?', (pr['id'],)))
    return {'project': pr, 'members': mem}


@route('POST', r'/api/projects/(\d+)/update')
def api_project_update(h, c, u, b, q, pid):
    need(is_mgr(u), 'ویرایش پروژه و ارکان آن فقط توسط هیات مدیره و مدیر سیستم انجام می‌شود')
    pr = one(c.execute('SELECT * FROM projects WHERE id=?', (int(pid),)))
    need(pr, 'پروژه پیدا نشد', 404)
    name = (b.get('name') or '').strip(); need(name, 'نام پروژه الزامی است', 400)
    need(not c.execute('SELECT 1 FROM projects WHERE name=? AND active=1 AND id!=?', (name, pr['id'])).fetchone(),
         'پروژه دیگری با این نام وجود دارد', 400)
    c.execute('UPDATE projects SET name=?, code=? WHERE id=?', (name, (b.get('code') or '').strip(), pr['id']))
    mem = b.get('members') or {}
    for key, label in PROJECT_ROLES:
        v = int(mem.get(key) or 0)
        if v:
            need(c.execute('SELECT 1 FROM users WHERE id=? AND active=1', (v,)).fetchone(), 'کاربر «%s» نامعتبر است' % label, 400)
        if key == 'pm':
            sync_pm(c, pr['id'], v)
        elif v:
            c.execute('INSERT OR REPLACE INTO project_members(project_id,role_key,user_id) VALUES(?,?,?)', (pr['id'], key, v))
        else:
            c.execute('DELETE FROM project_members WHERE project_id=? AND role_key=?', (pr['id'], key))
    log(c, 'project', pr['id'], u['id'], 'ویرایش پروژه و ارکان', name)
    return {'ok': True}


# ---------- درخواست کالا
def cancel_reasons(c):
    return [x.strip() for x in (settings(c).get('cancel_reasons') or DEFAULT_CANCEL_REASONS).splitlines() if x.strip()]


def pmembers(c, pid):
    return {r[0]: r[1] for r in c.execute('SELECT role_key,user_id FROM project_members WHERE project_id=?', (pid,))}


def stage_holder(c, P, stage):
    """کاربری که درخواست در این مرحله به کارتابل او می‌رود."""
    if stage == 'returned':
        return P['requester_id']
    labels = dict(PROJECT_ROLES)
    if stage == 'unit_approval':
        kind, key = 'member', UNIT_HEAD[P['unit']]
    else:
        kind, key = STAGE_HOLDER[stage]
    if kind == 'member':
        uid = pmembers(c, P['project_id']).get(key)
        need(uid, '«%s» برای این پروژه تعریف نشده؛ هیات مدیره باید ارکان پروژه را در صفحه پروژه تکمیل کند'
             % labels[key], 400)
        if stage == 'pm_approve' and uid == P['requester_id']:
            # خودتأییدی ممنوع: درخواستِ خودِ مدیر پروژه به مدیرعامل می‌رود
            uid = int(settings(c).get('ceo_user') or 0)
            need(uid, 'درخواست‌کننده خود مدیر پروژه است و مدیرعامل در تنظیمات تعیین نشده', 400)
    else:
        uid = int(settings(c).get(key) or 0)
        need(uid, '«%s» در «مدیریت سامانه ← تنظیمات» تعیین نشده' % HQ_ROLES[key], 400)
    need(c.execute('SELECT 1 FROM users WHERE id=? AND active=1', (uid,)).fetchone(),
         'کاربر مسئول مرحله «%s» غیرفعال است' % P_STAGES[stage], 400)
    return uid


def next_site_stage(c, P, stage, u, notes):
    """مرحله‌هایی که بررسی‌کننده‌شان خودِ درخواست‌کننده است رد می‌شوند (تأیید به سطح بالاتر می‌رسد)."""
    mem = pmembers(c, P['project_id'])
    while stage in ('tech_review', 'supervisor_approve'):
        key = 'tech' if stage == 'tech_review' else 'supervisor'
        if mem.get(key) != P['requester_id']:
            break
        notes.append('%s رد شد؛ درخواست‌کننده خود %s است' % (P_STAGES[stage], dict(PROJECT_ROLES)[key]))
        stage = 'supervisor_approve' if stage == 'tech_review' else 'pm_approve'
    return stage


def pflow(c, pid, u, stage, action, label, note=''):
    c.execute('INSERT INTO purchase_flow(purchase_id,stage,action,label,user_id,note,at) VALUES(?,?,?,?,?,?,?)',
              (pid, stage, action, label, u['id'], note, now()))
    log(c, 'purchase', pid, u['id'], label, note)


def to_int(v):
    d = re.sub(r'[^\d]', '', str(v or '').translate(FA2EN))
    return int(d) if d else None


def pur_fields(c, u, b, pid_fixed=None):
    pid = pid_fixed or int(b.get('project_id') or 0)
    pr = one(c.execute("SELECT * FROM projects WHERE id=? AND active=1 AND code!='HQ'", (pid,)))
    need(pr, 'پروژه را انتخاب کنید', 400)
    if not pid_fixed:
        need(is_mgr(u) or is_member(c, u, pid), 'فقط ارکان پروژه «%s» می‌توانند برای آن درخواست کالا ثبت کنند' % pr['name'])
    unit = b.get('unit')
    need(unit in dict(UNITS), 'واحد درخواست‌کننده را انتخاب کنید', 400)
    cat = b.get('category') or 'general'
    need(cat in dict(CATEGORIES), 'دسته کالا نامعتبر', 400)
    urg = b.get('urgency') or 'normal'
    need(urg in dict(URGENCIES), 'فوریت نامعتبر', 400)
    nd = (b.get('need_date') or '').translate(FA2EN).strip()
    need(re.match(r'^\d{4}/\d{1,2}/\d{1,2}$', nd), 'تاریخ نیاز را به شکل ۱۴۰۵/۰۸/۱۵ وارد کنید', 400)
    items = []
    for it in b.get('items') or []:
        t = (it.get('title') or '').strip()
        if not t:
            continue
        qty = (str(it.get('qty') or '')).translate(FA2EN).strip()
        need(qty, 'مقدار کالای «%s» وارد نشده' % t, 400)
        items.append((t, qty, (it.get('unit') or '').strip(), (it.get('spec') or '').strip(), (it.get('note') or '').strip()))
    need(items, 'حداقل یک ردیف کالا با شرح و مقدار وارد کنید', 400)
    return dict(project_id=pid, unit=unit, warehouse=(b.get('warehouse') or '').strip(), category=cat, urgency=urg,
                need_date=nd, purpose=(b.get('purpose') or '').strip(), requester_id=u['id']), items


def save_items(c, pid, items):
    c.execute('DELETE FROM purchase_items WHERE purchase_id=?', (pid,))
    for i, it in enumerate(items, 1):
        c.execute('INSERT INTO purchase_items(purchase_id,row_no,title,qty,unit,spec,note) VALUES(?,?,?,?,?,?,?)',
                  (pid, i) + it)


def save_version(c, pid, u, note=''):
    """عکس کامل درخواست در نسخه فعلی؛ نسخه‌های قبلی حذف نمی‌شوند."""
    P = one(c.execute('SELECT * FROM purchases WHERE id=?', (pid,)))
    items = rows(c.execute('SELECT row_no,title,qty,unit,spec,note FROM purchase_items WHERE purchase_id=? ORDER BY row_no',
                           (pid,)))
    data = {k: P[k] for k in ('unit', 'warehouse', 'category', 'urgency', 'need_date', 'purpose')}
    data['items'] = items
    c.execute('INSERT INTO purchase_versions(purchase_id,version,data,user_id,note,at) VALUES(?,?,?,?,?,?)',
              (pid, P['version'], json.dumps(data, ensure_ascii=False), u['id'], note, now()))


def write_fields(c, pid, f, items):
    c.execute('UPDATE purchases SET unit=?, warehouse=?, category=?, urgency=?, need_date=?, purpose=? WHERE id=?',
              (f['unit'], f['warehouse'], f['category'], f['urgency'], f['need_date'], f['purpose'], pid))
    save_items(c, pid, items)


def start_stage(c, u, f):
    """مرحله اول: اگر درخواست‌کننده معاون فنی، معاون اجرایی یا سرپرست کارگاه باشد، ثبتش همان تأیید رئیس واحد است."""
    mem = pmembers(c, f['project_id'])
    if u['id'] in {mem.get(k) for k in HEAD_ROLES}:
        return 'warehouse_check', 'ثبت و تأیید رئیس واحد و ارسال استعلام به انبار کارگاه'
    return 'unit_approval', 'ثبت و ارسال برای تأیید رئیس واحد'


def cc_exec(c, u, pid, f):
    """درخواستی که معاون فنی صادر می‌کند، رونوشت به معاون اجرایی می‌رود."""
    mem = pmembers(c, f['project_id'])
    if mem.get('tech') == u['id'] and mem.get('exec') and mem.get('exec') != u['id']:
        c.execute('INSERT INTO referrals(doc_type,doc_id,from_id,to_id,action,instruction,created_at) VALUES(?,?,?,?,?,?,?)',
                  ('purchase', pid, u['id'], mem['exec'], 'جهت اطلاع', 'رونوشت درخواست کالای معاون فنی', now()))


def pur_filter(c, u, q):
    w, p = ['1=1'], []
    if not is_broad(c, u):
        w.append('(x.requester_id=? OR x.holder_id=? OR x.project_id IN (SELECT project_id FROM project_members '
                 'WHERE user_id=?) OR EXISTS(SELECT 1 FROM purchase_flow f WHERE f.purchase_id=x.id AND f.user_id=?))')
        p += [u['id']] * 4
    for k in ('project_id', 'holder_id'):
        if q.get(k):
            w.append('x.%s=?' % k); p.append(int(q[k]))
    for k in ('unit', 'stage', 'status', 'category', 'urgency'):
        if q.get(k):
            vals = q[k].split(',')
            w.append('x.%s IN (%s)' % (k, ','.join('?' * len(vals)))); p += vals
    if q.get('q'):
        w.append('(x.number LIKE ? OR x.purpose LIKE ? OR x.supplier LIKE ? OR EXISTS(SELECT 1 FROM purchase_items i '
                 'WHERE i.purchase_id=x.id AND (i.title LIKE ? OR i.spec LIKE ?)))')
        p += ['%' + q['q'] + '%'] * 5
    if q.get('from'):
        w.append('substr(x.created_at,1,10)>=?'); p.append(q['from'])
    if q.get('to'):
        w.append('substr(x.created_at,1,10)<=?'); p.append(q['to'])
    return ' AND '.join(w), p


@route('GET', '/api/purchases')
def api_purchases(h, c, u, b, q):
    w, p = pur_filter(c, u, q)
    return rows(c.execute(PUR_SEL + 'WHERE %s ORDER BY x.id DESC LIMIT %d' % (w, int(q.get('limit') or 500)), p))


PUR_CSV_HEAD = ['شماره', 'تاریخ', 'پروژه', 'کد پروژه', 'واحد درخواست‌کننده', 'انبار محل درخواست', 'دسته', 'فوریت',
                'تاریخ نیاز', 'جهت استفاده', 'درخواست‌کننده', 'نسخه', 'ردیف', 'شرح کالا', 'مقدار', 'واحد', 'مشخصات فنی',
                'توضیحات', 'وضعیت', 'مرحله', 'در دست', 'تأمین‌کننده', 'مبلغ خرید (ریال)', 'مبلغ پرداختی (ریال)',
                'تاریخ پرداخت', 'سند سپیدار', 'کد بایگانی', 'علت لغو']


def pur_csv(c, lst, fname):
    out = io.StringIO(); wr = csv.writer(out)
    wr.writerow(PUR_CSV_HEAD)
    units, cats, urgs = dict(UNITS), dict(CATEGORIES), dict(URGENCIES)
    for P in lst:
        its = rows(c.execute('SELECT * FROM purchase_items WHERE purchase_id=? ORDER BY row_no', (P['id'],))) or [{}]
        for it in its:
            wr.writerow([P['number'], P['req_date'], P['project'] or '', P['project_code'] or '', units.get(P['unit'], ''),
                         P['warehouse'], cats.get(P['category'], ''), urgs.get(P['urgency'], ''), P['need_date'],
                         P['purpose'], P['requester'] or '', P['version'], it.get('row_no', ''), it.get('title', ''),
                         it.get('qty', ''), it.get('unit', ''), it.get('spec', ''), it.get('note', ''),
                         P_STATUS.get(P['status'], P['status']), P_STAGES.get(P['stage'], P['stage']), P['holder'] or '',
                         P['supplier'], P['amount'] or '', P['paid_amount'] or '', P['paid_at'], P['sepidar_no'],
                         P['archive_code'], (P['cancel_reason'] or '') + (' — ' + P['cancel_note'] if P['cancel_note'] else '')])
    return ('csv', fname, out.getvalue())


@route('GET', '/api/purchases.csv')
def api_purchases_csv(h, c, u, b, q):
    q['limit'] = 100000
    return pur_csv(c, api_purchases(h, c, u, b, q), 'material-requests.csv')


@route('GET', r'/api/purchases/(\d+)\.csv')
def api_purchase_csv(h, c, u, b, q, pid):
    get_doc(c, u, 'purchase', int(pid))
    return pur_csv(c, rows(c.execute(PUR_SEL + 'WHERE x.id=?', (int(pid),))), 'material-request-%s.csv' % pid)


@route('POST', '/api/purchases')
def api_purchase_new(h, c, u, b, q):
    f, items = pur_fields(c, u, b)
    stage, label = start_stage(c, u, f)
    holder = stage_holder(c, f, stage)
    y = jyear()
    seq = c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM purchases WHERE year=?', (y,)).fetchone()[0]
    number = 'ک %d/%04d' % (y, seq)
    cur = c.execute('INSERT INTO purchases(year,seq,number,project_id,unit,warehouse,category,urgency,need_date,purpose,'
                    'requester_id,req_date,stage,status,holder_id,version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)',
                    (y, seq, number, f['project_id'], f['unit'], f['warehouse'], f['category'], f['urgency'],
                     f['need_date'], f['purpose'], u['id'], jstr(today()), stage, 'open', holder, now()))
    pid = cur.lastrowid
    save_items(c, pid, items)
    save_version(c, pid, u, 'نسخه اصلی')
    pflow(c, pid, u, 'draft', 'submit', label, number)
    cc_exec(c, u, pid, f)
    return {'id': pid, 'number': number}


def can_edit(u, P):
    if P['status'] not in ('open', 'returned') or P['stage'] not in EDIT_STAGES:
        return False
    return P['holder_id'] == u['id'] or (P['requester_id'] == u['id'] and P['stage'] in ('unit_approval', 'returned'))


def can_cancel(u, P):
    if P['status'] not in ('open', 'returned') or P['stage'] not in CANCEL_STAGES:
        return False
    return (P['holder_id'] == u['id'] or is_mgr(u)
            or (P['requester_id'] == u['id'] and P['stage'] in ('unit_approval', 'returned')))


@route('GET', r'/api/purchases/(\d+)')
def api_purchase_get(h, c, u, b, q, pid):
    get_doc(c, u, 'purchase', int(pid))
    P = one(c.execute(PUR_SEL + 'WHERE x.id=?', (int(pid),)))
    items = rows(c.execute('SELECT * FROM purchase_items WHERE purchase_id=? ORDER BY row_no', (P['id'],)))
    flow = rows(c.execute('SELECT f.*, us.full_name user_name FROM purchase_flow f LEFT JOIN users us ON us.id=f.user_id '
                          'WHERE f.purchase_id=? ORDER BY f.id', (P['id'],)))
    vers = rows(c.execute('SELECT v.*, us.full_name user_name FROM purchase_versions v LEFT JOIN users us ON us.id=v.user_id '
                          'WHERE v.purchase_id=? ORDER BY v.version', (P['id'],)))
    for v in vers:
        v['data'] = json.loads(v['data'] or '{}')
    att, refs, _ = doc_extras(c, 'purchase', P['id'])
    for r in refs:  # رونوشت «جهت اطلاع» با دیدن بسته می‌شود
        if r['to_id'] == u['id'] and r['status'] == 'new':
            ns = 'done' if r['action'] == 'جهت اطلاع' else 'seen'
            c.execute('UPDATE referrals SET status=?, seen_at=?, done_at=? WHERE id=?',
                      (ns, now(), now() if ns == 'done' else None, r['id']))
            r['status'] = ns
    acts = list(P_FLOW.get(P['stage'], {})) if P['holder_id'] == u['id'] and P['status'] == 'open' else []
    return {'doc': P, 'items': items, 'flow': flow, 'versions': vers, 'attachments': att, 'referrals': refs,
            'actions': acts, 'can_edit': can_edit(u, P), 'can_cancel': can_cancel(u, P),
            'can_attach': P['status'] in ('open', 'returned') or P['holder_id'] == u['id'] or is_broad(c, u)
            or P['requester_id'] == u['id']}


@route('POST', r'/api/purchases/(\d+)/act')
def api_purchase_act(h, c, u, b, q, pid):
    P = get_doc(c, u, 'purchase', int(pid))
    need(P['status'] == 'open' and P['holder_id'] == u['id'], 'این درخواست در کارتابل شما نیست')
    acts = P_FLOW.get(P['stage']) or {}
    a = b.get('action')
    need(a in acts, 'اقدام نامعتبر', 400)
    nxt, label = acts[a]
    note = (b.get('note') or '').strip()
    notes = []
    if a == 'return':
        need(note, 'علت برگشت را بنویسید', 400)
    if a == 'not_in_stock':
        nxt = 'tech_review' if P['category'] == 'main' else 'supervisor_approve'
    if nxt in ('tech_review', 'supervisor_approve', 'pm_approve'):
        nxt = next_site_stage(c, P, nxt, u, notes)
    if a == 'not_in_stock':
        label += ' — ارسال به ' + ('معاون فنی (مصالح اصلی)' if nxt == 'tech_review' else P_STAGES[nxt])
    if a == 'purchased':
        c.execute('UPDATE purchases SET supplier=?, amount=? WHERE id=?',
                  ((b.get('supplier') or '').strip(), to_int(b.get('amount')), P['id']))
    elif a == 'paid':
        amt = to_int(b.get('paid_amount'))
        need(amt is not None, 'مبلغ پرداختی را وارد کنید', 400)
        c.execute('UPDATE purchases SET paid_amount=?, paid_at=?, sepidar_no=? WHERE id=?',
                  (amt, (b.get('paid_at') or jstr(today())).translate(FA2EN), (b.get('sepidar_no') or '').strip(), P['id']))
    elif a == 'archived':
        c.execute('UPDATE purchases SET archive_code=? WHERE id=?', ((b.get('archive_code') or '').strip(), P['id']))
    if nxt == 'done':
        st, stage, holder = ('delivered' if a == 'in_stock' else 'closed'), 'done', None
    elif nxt == 'returned':
        st, stage, holder = 'returned', 'returned', P['requester_id']
    else:
        st, stage, holder = 'open', nxt, stage_holder(c, P, nxt)
    c.execute('UPDATE purchases SET status=?, stage=?, holder_id=?, closed_at=? WHERE id=?',
              (st, stage, holder, now() if stage == 'done' else None, P['id']))
    pflow(c, P['id'], u, P['stage'], a, label, '؛ '.join([note] + notes if note else notes))
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/edit')
def api_purchase_edit(h, c, u, b, q, pid):
    P = get_doc(c, u, 'purchase', int(pid))
    need(can_edit(u, P), 'ویرایش فقط تا پیش از رسیدن درخواست به مدیر پروژه و توسط کسی که درخواست در کارتابل اوست ممکن است')
    f, items = pur_fields(c, u, b, pid_fixed=P['project_id'])
    write_fields(c, P['id'], f, items)
    c.execute('UPDATE purchases SET version=version+1 WHERE id=?', (P['id'],))
    note = (b.get('note') or '').strip()
    save_version(c, P['id'], u, note)
    pflow(c, P['id'], u, P['stage'], 'edit', 'ویرایش — نسخه %d' % (P['version'] + 1), note)
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/resubmit')
def api_purchase_resubmit(h, c, u, b, q, pid):
    P = get_doc(c, u, 'purchase', int(pid))
    need(P['requester_id'] == u['id'] and P['status'] == 'returned', 'فقط درخواست‌کننده، پس از برگشت درخواست')
    f, items = pur_fields(c, u, b, pid_fixed=P['project_id'])
    stage, label = start_stage(c, u, f)
    holder = stage_holder(c, dict(f, project_id=P['project_id']), stage)
    write_fields(c, P['id'], f, items)
    c.execute("UPDATE purchases SET version=version+1, stage=?, status='open', holder_id=? WHERE id=?",
              (stage, holder, P['id']))
    note = (b.get('note') or '').strip()
    save_version(c, P['id'], u, note)
    pflow(c, P['id'], u, 'returned', 'resubmit', 'اصلاح (نسخه %d) و ارسال مجدد — ' % (P['version'] + 1) + label, note)
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/cancel')
def api_purchase_cancel(h, c, u, b, q, pid):
    P = get_doc(c, u, 'purchase', int(pid))
    need(can_cancel(u, P), 'لغو فقط تا پیش از تأیید مدیر پروژه و توسط کسی که درخواست در کارتابل اوست ممکن است')
    reason = (b.get('reason') or '').strip()
    need(reason in cancel_reasons(c), 'علت لغو را انتخاب کنید', 400)
    note = (b.get('note') or '').strip()
    need(reason != 'سایر' or note, 'برای علت «سایر» توضیح لازم است', 400)
    c.execute("UPDATE purchases SET status='cancelled', stage='done', holder_id=NULL, closed_at=?, cancel_reason=?, "
              'cancel_note=? WHERE id=?', (now(), reason, note, P['id']))
    pflow(c, P['id'], u, P['stage'], 'cancel', 'لغو و بایگانی — ' + reason, note)
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
    # مدت ماندن درخواست کالا در هر مرحله (از ورود تا اقدام)
    dur, prev = {}, {}
    for f in c.execute("SELECT purchase_id, stage, at FROM purchase_flow WHERE action!='attach' ORDER BY purchase_id, id"):
        pv = prev.get(f['purchase_id'])
        if pv and f['stage'] in P_STAGES:
            h_ = (datetime.datetime.fromisoformat(f['at']) - datetime.datetime.fromisoformat(pv)).total_seconds() / 3600
            dur.setdefault(f['stage'], []).append(h_)
        prev[f['purchase_id']] = f['at']
    stage_time = [{'stage': k, 'label': P_STAGES[k], 'n': len(v), 'avg_hours': round(sum(v) / len(v), 1)}
                  for k, v in dur.items() if k != 'draft']
    cancels = rows(c.execute("SELECT cancel_reason reason, COUNT(*) n FROM purchases WHERE status='cancelled' "
                             "GROUP BY cancel_reason ORDER BY n DESC"))
    return {'people': people, 'overdue': overdue, 'letters': letters, 'requests': reqs, 'by_project': by_project,
            'no_sepidar': no_sep, 'year': y, 'stage_time': stage_time, 'cancels': cancels}


@route('POST', '/api/projects')
def api_project_new(h, c, u, b, q):
    need(is_mgr(u), 'تعریف پروژه جدید فقط توسط هیات مدیره و مدیر سیستم انجام می‌شود')
    name = (b.get('name') or '').strip(); need(name, 'نام پروژه الزامی است', 400)
    need(not c.execute('SELECT 1 FROM projects WHERE name=? AND active=1', (name,)).fetchone(), 'این پروژه قبلاً تعریف شده', 400)
    mid = int(b['manager_id']) if b.get('manager_id') else None
    cur = c.execute('INSERT INTO projects(name,code,manager_id) VALUES(?,?,?)', (name, (b.get('code') or '').strip(), mid))
    sync_pm(c, cur.lastrowid, mid)
    log(c, 'project', cur.lastrowid, u['id'], 'تعریف پروژه', name)
    return one(c.execute('SELECT id,name,code,manager_id FROM projects WHERE id=?', (cur.lastrowid,)))


# ---------- مدیریت
@route('GET', '/api/admin')
def api_admin(h, c, u, b, q):
    need(is_mgr(u))
    return {'users': rows(c.execute('SELECT id,username,full_name,title,role,active,must_change FROM users ORDER BY id')),
            'projects': rows(c.execute('SELECT * FROM projects ORDER BY id')), 'settings': settings(c),
            'backups': sorted(os.listdir(BACK))[-10:]}


@route('POST', '/api/admin/user')
def api_admin_user(h, c, u, b, q):
    need(is_mgr(u))
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
    need(is_mgr(u))
    hh, s = hash_pw('1234')
    c.execute('UPDATE users SET pw_hash=?, salt=?, must_change=1 WHERE id=?', (hh, s, int(b['id'])))
    c.execute('DELETE FROM sessions WHERE user_id=?', (int(b['id']),))
    return {'ok': True}


@route('POST', '/api/admin/project')
def api_admin_project(h, c, u, b, q):
    need(is_mgr(u))
    need((b.get('name') or '').strip(), 'نام پروژه الزامی است', 400)
    mid = int(b['manager_id']) if b.get('manager_id') else None
    if b.get('id'):
        c.execute('UPDATE projects SET name=?,code=?,manager_id=?,active=? WHERE id=?',
                  (b['name'].strip(), b.get('code') or '', mid, 1 if b.get('active', True) else 0, int(b['id'])))
        sync_pm(c, int(b['id']), mid)
    else:
        cur = c.execute('INSERT INTO projects(name,code,manager_id) VALUES(?,?,?)', (b['name'].strip(), b.get('code') or '', mid))
        sync_pm(c, cur.lastrowid, mid)
    return {'ok': True}


@route('POST', '/api/admin/settings')
def api_admin_settings(h, c, u, b, q):
    need(is_mgr(u))
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
        if method == 'GET' and path == '/logo.png':  # آرم شرکت (اختیاری): فایل logo.png کنار app.py
            lp = os.path.join(BASE, 'logo.png')
            if os.path.exists(lp):
                with open(lp, 'rb') as f:
                    return self.send(200, f.read(), 'image/png')
            return self.send(404, b'', 'image/png')
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


def running_version(port):
    """اگر همین سامانه از قبل روی پورت اجرا شده باشد، نام نسخه‌اش را برمی‌گرداند."""
    import http.client
    try:
        cn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        cn.request('GET', '/api/counts')
        sv = cn.getresponse().getheader('Server') or ''
        cn.close()
        return sv.split()[0] if sv.startswith('OmranZistOA') else ''
    except OSError:
        return ''


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
    other = running_version(PORT)
    if other:
        print('سامانه از قبل روی پورت %d در حال اجراست (%s).' % (PORT, other))
        print('برای اجرای نسخه جدید، اول آن را متوقف کنید: فایل 4-restart.bat را با Run as administrator اجرا کنید.')
        return
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
