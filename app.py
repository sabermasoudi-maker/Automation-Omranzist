# -*- coding: utf-8 -*-
"""
سامانه اتوماسیون اداری و مالی — شرکت عمران زیست
فقط با پایتون ۳.۹ به بالا اجرا می‌شود و به هیچ کتابخانه بیرونی یا اینترنت نیاز ندارد.
اجرا:  python app.py      سپس در مرورگر:  http://<IP سرور>:8080
"""
import os, sys, json, sqlite3, hashlib, secrets, re, shutil, threading, csv, io, time, ssl, zlib, struct
import mimetypes, urllib.parse, datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http import cookies

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'data')
FILES = os.path.join(DATA, 'files')
BACK = os.path.join(DATA, 'backups')
DB = os.path.join(DATA, 'oa.db')
PORT_FILE = os.path.join(BASE, 'port.txt')
HTTPS_DIR = os.path.join(DATA, 'https')  # گواهی HTTPS (cert.pem و key.pem) که مدیر سیستم بارگذاری می‌کند


def read_port():
    try:
        return int(open(PORT_FILE).read().strip())
    except Exception:
        return int(os.environ.get('OA_PORT', '8080'))


PORT = read_port()
CANDIDATE_PORTS = [8080, 8090, 8888, 9090, 5080, 7080, 18080]
MAX_UPLOAD = 60 * 1024 * 1024
VERSION = '2.3'

FA2EN = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
AR2FA = str.maketrans('يكة', 'یکه')  # ی و ک عربی (صفحه‌کلید عربی) در جستجو


def search_words(q):
    """کلمات جستجو، هر کدام به شکل الگوی LIKE؛ همه کلمات باید پیدا شوند."""
    return ['%' + t + '%' for t in (q.get('q') or '').split() if t][:8]
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
# کارکنان زیرمجموعه (چند نفر در هر سمت): (کلید، عنوان، سمتِ بالادست)
TEAM_ROLES = [('exec_eng', 'مهندس اجرایی', 'exec'), ('tech_eng', 'مهندس دفتر فنی', 'tech')]
ROLE_UNIT = {'pm': 'exec', 'supervisor': 'exec', 'exec': 'exec', 'tech': 'tech', 'support': 'support',
             'warehouse': 'warehouse', 'exec_eng': 'exec', 'tech_eng': 'tech'}
# بالادستِ مستقیم هر سمت در کارگاه (تأیید درخواست و مکاتبات به سمت بالا)
SUPERIOR = {'exec_eng': 'exec', 'tech_eng': 'tech', 'support': 'supervisor', 'warehouse': 'supervisor',
            'exec': 'supervisor', 'tech': 'supervisor', 'supervisor': 'pm'}
HQ_ROLES = {'support_manager': 'مدیر پشتیبانی دفتر مرکزی', 'finance_manager': 'مدیر مالی دفتر مرکزی',
            'archive_user': 'منشی (بایگانی)'}

# گردش درخواست کالا: مرحله ← {اقدام: (مرحله بعد، شرح)}
P_STAGES = {'draft': 'پیش‌نویس درخواست‌کننده', 'unit_approval': 'تأیید رئیس واحد',
            'supervisor_review': 'بررسی سرپرست کارگاه',  # فقط برای درخواست‌های نسخه‌های قبل
            'warehouse_check': 'استعلام موجودی از انبار کارگاه',
            'supervisor_approve': 'تأیید سرپرست کارگاه', 'tech_review': 'بررسی معاون فنی',
            'site_purchase': 'خرید در کارگاه — پشتیبانی کارگاه', 'pm_approve': 'بررسی مدیر پروژه',
            'hq_quotes': 'استعلام و پیش‌فاکتور — پشتیبانی دفتر مرکزی',
            'price_approve': 'تأیید قیمت و فروشنده — مدیر پروژه / هیات مدیره',
            'hq_purchase': 'خرید — پشتیبانی دفتر مرکزی', 'delivery': 'اعلام وصول — تحویل‌گیرنده و انباردار',
            'finance_settle': 'تطبیق مدارک و تسویه — مالی دفتر مرکزی', 'finance_pay': 'پرداخت — مالی دفتر مرکزی',
            'discrepancy': 'تصمیم درباره مغایرت — مدیر پروژه / هیات مدیره',
            'invoice_fix': 'تکمیل مدارک (فاکتور) — پشتیبانی',
            'archive': 'بایگانی — دبیرخانه', 'returned': 'برگشت به درخواست‌کننده', 'done': 'پایان'}
_RET = ('returned', 'برگشت به درخواست‌کننده برای اصلاح')
# مرحله بعد با None یعنی سرور بر اساس موجودی، کلاس خرید یا وضعیت تحویل و پرداخت تعیینش می‌کند
P_FLOW = {
    'draft': {'submit': (None, '')},
    'unit_approval': {'approve': ('warehouse_check', 'تأیید رئیس واحد و ارسال استعلام به انبار کارگاه'), 'return': _RET},
    'supervisor_review': {'inquire': ('warehouse_check', 'ارسال استعلام به انبار کارگاه'), 'return': _RET},
    'warehouse_check': {'stock': (None, 'ثبت موجودی انبار')},
    'supervisor_approve': {'approve': (None, 'تأیید سرپرست کارگاه'), 'return': _RET},
    'tech_review': {'approve': (None, 'تأیید معاون فنی'), 'return': _RET},
    'site_purchase': {'purchased': ('delivery', 'خرید در کارگاه انجام شد — ارسال برای اعلام وصول')},
    'pm_approve': {'approve': ('hq_quotes', 'تأیید مدیر پروژه — ارسال به پشتیبانی برای استعلام قیمت'), 'return': _RET},
    'hq_quotes': {'quoted': ('price_approve', 'استعلام و پیش‌فاکتورها آماده شد — ارسال برای تأیید قیمت')},
    'price_approve': {'approve': ('hq_purchase', 'تأیید قیمت و فروشنده — ارسال برای خرید'),
                      'requote': ('hq_quotes', 'برگشت برای استعلام مجدد')},
    'hq_purchase': {'purchased': ('delivery', 'خرید انجام شد — ارسال برای اعلام وصول')},
    'delivery': {'recv_ok': (None, 'تأیید تحویل توسط درخواست‌کننده'), 'recv_bad': (None, 'اعلام مغایرت توسط درخواست‌کننده'),
                 'wh_ok': (None, 'تأیید دریافت توسط انبار'), 'wh_bad': (None, 'اعلام مغایرت توسط انبار')},
    'finance_settle': {'settled': ('archive', 'مدارک کامل است (درخواست، اعلام وصول، فاکتور)؛ تسویه کامل شد — ارسال به دبیرخانه برای بایگانی'),
                       'need_docs': ('invoice_fix', 'برگشت به پشتیبانی برای بارگذاری فاکتور'),
                       'to_disc': ('discrepancy', 'مغایرت — ارسال برای تصمیم')},  # to_disc فقط برای درخواست‌های نسخه ۲.۱
    'discrepancy': {'accept': ('finance_settle', 'پذیرش مغایرت و ادامه پرداخت'),
                    'fix': ('invoice_fix', 'برگشت به پشتیبانی برای اصلاح فاکتور یا خرید')},
    'invoice_fix': {'fixed': ('finance_settle', 'فاکتور بارگذاری شد — ارسال به مالی')},
    'finance_pay': {'paid': ('archive', 'پرداخت شد — ارسال به دبیرخانه برای بایگانی')},
    'archive': {'archived': ('done', 'بایگانی شد')},
}
# مدارکی که از مرحله استعلام قیمت به بعد پیوست می‌شوند و قیمت‌ها، برای کارکنان کارگاه نمایش داده نمی‌شوند
HQ_ONLY_STAGES = ('hq_quotes', 'price_approve', 'hq_purchase', 'finance_settle', 'finance_pay', 'archive',
                  'discrepancy', 'invoice_fix')
PAY_STAGES = ('hq_purchase', 'site_purchase', 'delivery', 'finance_settle', 'finance_pay', 'archive',
              'discrepancy', 'invoice_fix')
# کدگذاری اسناد: {نوع سند}-{سریال ۴ رقمی}، مثل MR-0012 (بدون کد پروژه؛ سریال سراسری برای هر نوع سند)
DOC_TYPES = {'MR': 'درخواست کالا', 'PO': 'سفارش خرید', 'GRN': 'اعلام وصول کالا', 'PAY': 'پرداخت'}
PAY_METHODS = ['نقد / حواله بانکی', 'چک', 'تنخواه', 'سایر']
BUY_STATUS = {'bought': 'خریداری شد', 'partial': 'بخشی خریداری شد', 'none': 'خریداری نشد'}
LH_DIR, SIG_DIR, SIG_COPY_DIR = 'سربرگ', 'کلیشه امضا', os.path.join('کلیشه امضا', 'نامه‌ها')
# جای شماره، تاریخ، پیوست، نام پروژه و متن روی سربرگ (میلی‌متر از لبه‌های A4)؛ برای هر سربرگ قابل تنظیم است
LH_DEFAULT = {'no': {'top': 22, 'left': 20}, 'date': {'top': 29, 'left': 20}, 'att': {'top': 36, 'left': 20},
              'proj': {'top': 42, 'right': 20}, 'body': {'top': 58, 'right': 22, 'left': 22, 'bottom': 30}, 'font': 13}
PUR_DIR = 'درخواست کالا'  # پوشه پیوست‌های درخواست کالا در data\files؛ هر درخواست یک زیرپوشه به شماره خودش
STAGE_HOLDER = {'supervisor_review': ('member', 'supervisor'), 'warehouse_check': ('member', 'warehouse'),
                'tech_review': ('member', 'tech'), 'supervisor_approve': ('member', 'supervisor'),
                'site_purchase': ('member', 'support'), 'pm_approve': ('member', 'pm'),
                'price_approve': ('member', 'pm'),
                'hq_quotes': ('setting', 'support_manager'), 'hq_purchase': ('setting', 'support_manager'),
                'finance_settle': ('setting', 'finance_manager'), 'finance_pay': ('setting', 'finance_manager'),
                'discrepancy': ('member', 'pm'),
                'archive': ('setting', 'archive_user')}
UNIT_HEAD = {'tech': 'tech', 'exec': 'exec', 'support': 'supervisor', 'warehouse': 'supervisor'}  # رئیس هر واحد
HEAD_ROLES = ('tech', 'exec', 'supervisor')  # درخواست این افراد، خودش تأیید رئیس واحد است
# تا پیش از رسیدن به مدیر پروژه ویرایش و لغو ممکن است؛ مدیر پروژه فقط لغو (ابطال) می‌کند
# هر کس فقط تا وقتی درخواست در کارتابل خودش است ویرایش می‌کند؛ تا مدیر پروژه (خودش هم)
EDIT_STAGES = ('draft', 'unit_approval', 'supervisor_review', 'warehouse_check', 'tech_review', 'supervisor_approve',
               'pm_approve', 'returned')
CANCEL_STAGES = EDIT_STAGES
# دسته کالا تعیین‌کننده مسیر است: عمومی و مصرفی در کارگاه با پشتیبانی کارگاه؛ اصلی با روال کامل دفتر مرکزی
CATEGORIES = [('main', 'مصالح، تجهیزات و ابزار اصلی'), ('general', 'عمومی و مصرفی')]
URGENCIES = [('normal', 'عادی'), ('emergency', 'اضطراری')]
ATT_KINDS = ['پیش‌فاکتور', 'فاکتور', 'مشخصات فنی', 'نقشه / متره', 'رسید', 'صورت‌جلسه', 'عکس', 'سایر']
DEFAULT_CANCEL_REASONS = 'نیاز نیست\nبودجه تأمین نیست\nتکراری است\nزمان‌بندی اجازه نمی‌دهد\nسایر'
P_STATUS = {'open': 'در جریان', 'returned': 'برگشت برای اصلاح', 'delivered': 'تحویل کامل از موجودی انبار',
            'closed': 'تحویل، تسویه و بایگانی شد', 'rejected': 'رد شد', 'cancelled': 'لغو شد (بایگانی)'}
SEED_PROJECTS = ['موادکاران', 'پروژه بدون نام ۱', 'پروژه بدون نام ۲']
# حساب‌های سمت‌های پروژه موادکاران (موقت؛ مدیر سیستم بعداً نام، شخص یا حساب را عوض می‌کند)
# چارت سازمانی کارگاه موادکاران (۱۴۰۵/۰۷): (نام کاربری، نام، سمت، کلید سمت)
MK_CHART = [('mk-sarparast', 'هادی شمیعی', 'سرپرست کارگاه موادکاران', 'supervisor'),
            ('mk-ejraei', 'بهروز بحرینی', 'معاون اجرایی موادکاران', 'exec'),
            ('mk-zali', 'ارسلان زالی', 'مهندس اجرایی موادکاران', 'exec_eng'),
            ('mk-fanni', 'سعید حاج ابراهیمی', 'معاون فنی موادکاران', 'tech'),
            ('mk-bajelani', 'دینا باجلانی', 'مهندس دفتر فنی موادکاران', 'tech_eng'),
            ('mk-masoudi', 'علیرضا مسعودی', 'مهندس دفتر فنی موادکاران', 'tech_eng'),
            ('mk-hosseini', 'سارینا حسینی', 'مهندس دفتر فنی موادکاران', 'tech_eng'),
            ('mk-poshtibani', 'جمشید رستمیان', 'پشتیبانی کارگاه موادکاران', 'support'),
            ('mk-anbar', 'حسن علی‌اصغری', 'انباردار موادکاران', 'warehouse')]
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
CREATE TABLE IF NOT EXISTS letterheads(scope TEXT PRIMARY KEY, path TEXT, layout TEXT DEFAULT '', updated_at TEXT);
CREATE TABLE IF NOT EXISTS purchase_payments(id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL, amount INTEGER,
  paid_at TEXT, method TEXT DEFAULT '', cheque_no TEXT DEFAULT '', cheque_date TEXT DEFAULT '', sepidar_no TEXT DEFAULT '',
  note TEXT DEFAULT '', user_id INTEGER, at TEXT);
CREATE INDEX IF NOT EXISTS ix_pur_pay ON purchase_payments(purchase_id);
CREATE TABLE IF NOT EXISTS doc_serials(project_id INTEGER NOT NULL, dtype TEXT NOT NULL, last INTEGER DEFAULT 0,
  PRIMARY KEY(project_id, dtype));
CREATE TABLE IF NOT EXISTS purchase_invoices(id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL, code TEXT,
  inv_no TEXT DEFAULT '', inv_date TEXT DEFAULT '', supplier TEXT DEFAULT '', extra INTEGER DEFAULT 0, total INTEGER,
  note TEXT DEFAULT '', user_id INTEGER, at TEXT);
CREATE TABLE IF NOT EXISTS purchase_invoice_lines(id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL, item_id INTEGER,
  qty TEXT DEFAULT '', unit_price INTEGER);
CREATE INDEX IF NOT EXISTS ix_pur_inv ON purchase_invoices(purchase_id);
CREATE TABLE IF NOT EXISTS project_team(project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role_key TEXT NOT NULL,
  PRIMARY KEY(project_id, user_id));
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
CREATE TABLE IF NOT EXISTS notify_devices(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT, last_seen TEXT,
  agent TEXT DEFAULT '');
"""

DEFAULT_SETTINGS = {'company': 'شرکت گسترش فناوری عمران زیست', 'ceo_threshold': '1000000000',
                    'ceo_user': '', 'office_approver': '', 'warehouse_user': '', 'default_due_days': '3',
                    'support_manager': '', 'finance_manager': '', 'archive_user': '',
                    'cancel_reasons': DEFAULT_CANCEL_REASONS,
                    # خروج خودکار پس از چند دقیقه بی‌فعالیتی؛ اعلان‌ها روی موبایل و پورت HTTPS
                    'idle_minutes': '5', 'notify_enabled': '1', 'notify_interval': '30', 'https_port': '8443'}

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


J_YEARS = (1405, 1410)  # سال‌های مجاز در تقویم شمسی سامانه


def jnorm(s):
    """تاریخ شمسی به شکل استاندارد ۱۴۰۵/۰۷/۰۵ (با ارقام لاتین)؛ نامعتبر = ''"""
    m = re.match(r'^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$', (s or '').translate(FA2EN).strip())
    if not m:
        return ''
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= (31 if mo <= 6 else 30)):
        return ''
    return '%04d/%02d/%02d' % (y, mo, d)


def jtoday():
    return jstr(today())[:10]


def need_jdate(v, label, required=True, min_=None, max_=None, min_msg='', max_msg=''):
    """بررسی تاریخ شمسی: قالب، سال‌های مجاز و حداقل/حداکثر؛ مقدار استاندارد را برمی‌گرداند."""
    raw = (v or '').strip()
    if not raw:
        need(not required, '%s را وارد کنید' % label, 400)
        return ''
    d = jnorm(raw)
    need(d, '%s نامعتبر است؛ از تقویم انتخاب کنید یا به شکل ۱۴۰۵/۰۸/۱۵ بنویسید' % label, 400)
    need(J_YEARS[0] <= int(d[:4]) <= J_YEARS[1], fa_num('%s باید بین سال‌های %d تا %d باشد' % ((label,) + J_YEARS)), 400)
    if min_:
        need(d >= min_, fa_num(min_msg or '%s نمی‌تواند قبل از %s باشد' % (label, min_)), 400)
    if max_:
        need(d <= max_, fa_num(max_msg or '%s نمی‌تواند بعد از %s باشد' % (label, max_)), 400)
    return d


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
    migrate_v15(c)
    migrate_v16(c)
    # نام کامل دو مهندس دفتر فنی موادکاران (فقط اگر هنوز نام قبلی ثبت است)
    for un, old, new in (('mk-bajelani', 'خانم مهندس باجلانی', 'دینا باجلانی'),
                         ('mk-hosseini', 'خانم مهندس حسینی', 'سارینا حسینی')):
        c.execute('UPDATE users SET full_name=? WHERE username=? AND full_name=?', (new, un, old))
    c.commit()
    c.close()


def add_columns(c):
    """ستون‌های اضافه‌شده در نسخه‌های بعدی (افزودنی؛ داده‌ای حذف نمی‌شود)."""
    want = {'purchases': [('category', "TEXT DEFAULT 'general'"), ('urgency', "TEXT DEFAULT 'normal'"),
                          ('need_date', "TEXT DEFAULT ''"), ('version', 'INTEGER DEFAULT 1'),
                          ('cancel_reason', "TEXT DEFAULT ''"), ('cancel_note', "TEXT DEFAULT ''"),
                          # ۲.۰: برآورد و کلاس خرید، پیشنهاد قیمت، تحویل دوطرفه، تسویه
                          ('estimate', 'INTEGER'), ('pclass', "TEXT DEFAULT ''"),
                          ('proposed_supplier', "TEXT DEFAULT ''"), ('proposed_amount', 'INTEGER'),
                          ('recv_by', 'INTEGER'), ('recv_at', 'TEXT'), ('wh_by', 'INTEGER'), ('wh_at', 'TEXT'),
                          ('settled_at', 'TEXT'), ('settled_by', 'INTEGER'),
                          # ۲.۱: کدگذاری اسناد و تطبیق سه‌طرفه
                          ('po_no', "TEXT DEFAULT ''"), ('grn_no', "TEXT DEFAULT ''"),
                          ('disc_ok_by', 'INTEGER'), ('disc_ok_at', 'TEXT'), ('disc_note', "TEXT DEFAULT ''")],
            'purchase_payments': [('code', "TEXT DEFAULT ''")],
            'purchase_items': [('stock_qty', "TEXT DEFAULT ''"), ('bought_qty', "TEXT DEFAULT ''"),
                               ('bought_unit', "TEXT DEFAULT ''"), ('bought_status', "TEXT DEFAULT ''"),
                               ('bought_note', "TEXT DEFAULT ''"), ('recv_qty', "TEXT DEFAULT ''")],
            'attachments': [('kind', "TEXT DEFAULT ''"), ('deleted_at', 'TEXT'), ('deleted_by', 'INTEGER'),
                            ('flow_id', 'INTEGER'), ('hq_only', 'INTEGER DEFAULT 0')],
            'letters': [('body', "TEXT DEFAULT ''"), ('signer_id', 'INTEGER'), ('signed_at', 'TEXT'),
                        ('sig_name', "TEXT DEFAULT ''"), ('sig_title', "TEXT DEFAULT ''"), ('sig_file', "TEXT DEFAULT ''"),
                        ('registered_at', 'TEXT'), ('main_to_id', 'INTEGER'), ('cc_text', "TEXT DEFAULT ''"),
                        ('cc_ids', "TEXT DEFAULT ''"), ('main_action', "TEXT DEFAULT ''"), ('dispatched', 'INTEGER DEFAULT 1')],
            'users': [('sig_path', "TEXT DEFAULT ''"), ('deleted', 'INTEGER DEFAULT 0')],
            'projects': [('deleted', 'INTEGER DEFAULT 0')],
            'sessions': [('last_seen', 'TEXT')]}
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


def migrate_v15(c):
    """چارت سازمانی موادکاران: نام واقعی افراد روی حساب‌های سمتی و افزودن مهندسان اجرایی و دفتر فنی."""
    if settings(c).get('seed_v15'):
        return
    pr = c.execute("SELECT id FROM projects WHERE name='موادکاران'").fetchone()
    if pr:
        placeholders = {un: fn for _, un, fn in SEED_MK_POSTS}
        for un, fn, title, key in MK_CHART:
            r = one(c.execute('SELECT id, full_name FROM users WHERE username=?', (un,)))
            if r:
                uid = r['id']
                if r['full_name'] == placeholders.get(un):  # فقط اگر مدیر سیستم هنوز نامش را عوض نکرده
                    c.execute('UPDATE users SET full_name=?, title=? WHERE id=?', (fn, title, uid))
            else:
                hh, ss = hash_pw('1234')
                uid = c.execute('INSERT INTO users(username,full_name,title,role,pw_hash,salt) VALUES(?,?,?,?,?,?)',
                                (un, fn, title, 'staff', hh, ss)).lastrowid
            if key in dict((k, 1) for k, _, _ in TEAM_ROLES):
                c.execute('INSERT OR IGNORE INTO project_team(project_id,user_id,role_key) VALUES(?,?,?)', (pr[0], uid, key))
            elif not c.execute('SELECT 1 FROM project_members WHERE project_id=? AND role_key=?', (pr[0], key)).fetchone():
                c.execute('INSERT INTO project_members(project_id,role_key,user_id) VALUES(?,?,?)', (pr[0], key, uid))
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('seed_v15','1')")


def migrate_v16(c):
    """پیوست‌های درخواست‌های کالای قبلی به پوشه جداگانه هر درخواست منتقل می‌شوند."""
    if settings(c).get('seed_v16'):
        return
    for a in rows(c.execute("SELECT a.id, a.path, p.number FROM attachments a JOIN purchases p ON p.id=a.doc_id "
                            "WHERE a.doc_type='purchase'")):
        sub = pur_folder(a)
        if os.path.dirname(a['path']) == sub:
            continue
        src = os.path.join(FILES, a['path'])
        dst_rel = os.path.join(sub, os.path.basename(a['path']))
        if os.path.exists(src):
            os.makedirs(os.path.join(FILES, sub), exist_ok=True)
            shutil.move(src, os.path.join(FILES, dst_rel))
            c.execute('UPDATE attachments SET path=? WHERE id=?', (dst_rel, a['id']))
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('seed_v16','1')")


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


def fa_num(v):
    return str(v or '').translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))


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


def can_report(c, u):
    """گزارش‌ها و همه درخواست‌ها و مکاتبات: هیات مدیره، مدیر سیستم، مدیر پشتیبانی و مدیر مالی دفتر مرکزی."""
    S = settings(c)
    return is_mgr(u) or str(u['id']) in (S.get('support_manager'), S.get('finance_manager'))


def is_broad(c, u):
    """کسانی که همه درخواست‌های کالای همه پروژه‌ها را می‌بینند."""
    return can_report(c, u) or u['role'] in ('finance', 'secretariat')


HQ_SETTING_USERS = ('support_manager', 'finance_manager', 'archive_user', 'ceo_user', 'office_approver', 'warehouse_user')


def is_site_only(c, u):
    """کاربر کارگاهی: کارمندی که عضو ارکان پروژه است و سمتی در دفتر مرکزی ندارد؛ بخش‌های دفتر مرکزی را نمی‌بیند."""
    if u['role'] != 'staff':
        return False
    S = settings(c)
    if str(u['id']) in {S.get(k) for k in HQ_SETTING_USERS}:
        return False
    return bool(user_project_roles(c, u['id']))


def user_project_roles(c, uid, pid=None):
    """سمت‌های کاربر در پروژه‌ها: [(project_id, role_key)] از ارکان و کارکنان زیرمجموعه."""
    q = ('SELECT project_id, role_key FROM project_members WHERE user_id=? UNION '
         'SELECT project_id, role_key FROM project_team WHERE user_id=?')
    rs = [(r[0], r[1]) for r in c.execute(q, (uid, uid))]
    return [r for r in rs if pid is None or r[0] == pid]


def superiors_of(c, uid):
    """بالادست‌های مستقیم کاربر در پروژه‌هایش."""
    out = []
    for pid, key in user_project_roles(c, uid):
        sup = SUPERIOR.get(key)
        r = sup and c.execute('SELECT user_id FROM project_members WHERE project_id=? AND role_key=?', (pid, sup)).fetchone()
        if r and r[0] != uid and r[0] not in out:
            out.append(r[0])
    return out


def colleagues_of(c, uid):
    """همکاران پروژه‌های کاربر (ارکان و کارکنان)، برای مکاتبات داخلی کارگاه."""
    pids = {p for p, _ in user_project_roles(c, uid)}
    ids = set()
    for pid in pids:
        ids |= {r[0] for r in c.execute('SELECT user_id FROM project_members WHERE project_id=? UNION '
                                        'SELECT user_id FROM project_team WHERE project_id=?', (pid, pid))}
    ids.discard(uid)
    return sorted(ids)


def no_site(c, u):
    need(not is_site_only(c, u), 'بخش‌های دفتر مرکزی برای کاربران کارگاه در دسترس نیست')


def is_member(c, u, pid):
    return bool(user_project_roles(c, u['id'], pid))


def is_lead(c, u, pid):
    """ارکان اصلی پروژه (نه کارکنان زیرمجموعه) که همه درخواست‌های پروژه را می‌بینند."""
    return c.execute('SELECT 1 FROM project_members WHERE project_id=? AND user_id=?', (pid, u['id'])).fetchone() is not None


# ------------------------------------------------------------------ دسترسی به سند
def participant(c, u, dt, did):
    return c.execute('SELECT 1 FROM referrals WHERE doc_type=? AND doc_id=? AND (to_id=? OR from_id=?) LIMIT 1',
                     (dt, did, u['id'], u['id'])).fetchone() is not None


def can_view_letter(c, u, L):
    part = L['created_by'] == u['id'] or participant(c, u, 'letter', L['id'])
    if can_report(c, u):
        return True
    if L['confidential'] and not is_mgr(u):
        return part
    return sees_all(u) or part


def can_view_request(c, u, R):
    if u['role'] in ('admin', 'manager', 'finance') or R['requester_id'] == u['id'] or can_report(c, u):
        return True
    if c.execute('SELECT 1 FROM steps WHERE request_id=? AND approver_id=?', (R['id'], u['id'])).fetchone():
        return True
    return participant(c, u, 'request', R['id'])


def can_view_purchase(c, u, P):
    if is_broad(c, u) or u['id'] in (P['requester_id'], P['holder_id']) or is_lead(c, u, P['project_id']):
        return True
    if any(ROLE_UNIT.get(k) == P['unit'] for _, k in user_project_roles(c, u['id'], P['project_id'])):
        return True  # مهندسان فقط درخواست‌های واحد خودشان را می‌بینند
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
                         'WHERE doc_type=? AND doc_id=? AND a.deleted_at IS NULL ORDER BY a.id', (dt, did)))
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
    if is_site_only(c, u):  # کارکنان کارگاه فقط با همکاران پروژه (از جمله مدیر پروژه) مکاتبه می‌کنند
        allowed = set(colleagues_of(c, u['id']))
        need(all(t in allowed for t in to_ids), 'کارکنان کارگاه فقط به همکاران پروژه خود ارجاع می‌دهند', 400)
    need(action in ACTIONS, 'نوع اقدام نامعتبر', 400)
    need(not due or due >= today(), 'مهلت انجام نمی‌تواند قبل از امروز باشد', 400)
    names = []
    for t in to_ids:
        tu = one(c.execute('SELECT id,full_name FROM users WHERE id=? AND active=1', (t,)))
        need(tu, 'کاربر گیرنده نامعتبر', 400)
        c.execute('INSERT INTO referrals(doc_type,doc_id,parent_id,from_id,to_id,action,instruction,due_date,created_at)'
                  ' VALUES(?,?,?,?,?,?,?,?,?)', (dt, did, parent_id, u['id'], t, action, instruction or '', due or '', now()))
        names.append(tu['full_name'])
    log(c, dt, did, u['id'], 'ارجاع', '%s ← %s%s' % (action, '، '.join(names), (' : ' + instruction) if instruction else ''))
    if dt == 'letter':
        c.execute("UPDATE letters SET status='open', closed_at=NULL WHERE id=? AND status NOT IN ('archived','draft')", (did,))


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


def pw_ok(pw, r):
    """رمز درست است اگر همان‌طور، یا با Caps Lock روشن (حروف برعکس)، یا با حرف اول بزرگ‌شده خودکار گوشی تایپ شده باشد."""
    tries = {pw, pw.swapcase(), pw[:1].swapcase() + pw[1:]}
    return any(hash_pw(t, r['salt'])[0] == r['pw_hash'] for t in tries)


@route('POST', '/api/login')
def api_login(h, c, u, b, q):
    # نام کاربری به حروف کوچک و بزرگ حساس نیست
    r = one(c.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1',
                      ((b.get('username') or '').strip(),)))
    if not r or not pw_ok(b.get('password') or '', r):
        raise ApiError('نام کاربری یا رمز عبور نادرست است', 401)
    tok = secrets.token_hex(24)
    c.execute('INSERT INTO sessions(token,user_id,created_at,last_seen) VALUES(?,?,?,?)', (tok, r['id'], now(), now()))
    h.set_cookie = 'sid=%s; Path=/; HttpOnly; SameSite=Lax' % tok  # کوکی جلسه؛ با بستن مرورگر یا بی‌فعالیتی از بین می‌رود
    return {'ok': True}


@route('POST', '/api/ping')
def api_ping(h, c, u, b, q):
    """کاربر در صفحه فعال است (تایپ یا کلیک)؛ جلسه تمدید می‌شود."""
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
    return {'me': u, 'version': VERSION, 'today': today(), 'jtoday': jtoday(), 'j_years': J_YEARS, 'company': S.get('company'),
            'idle_minutes': int(S.get('idle_minutes') or 0), 'notify_enabled': S.get('notify_enabled') == '1',
            'notify_interval': max(15, int(S.get('notify_interval') or 30)), 'is_admin': u['role'] == 'admin',
            'https_port': HTTPS_RUNNING[0],
            'users': rows(c.execute('SELECT id,full_name,title,role FROM users WHERE active=1 ORDER BY id')),
            'projects': rows(c.execute('SELECT id,name,code,manager_id FROM projects WHERE active=1 ORDER BY id')),
            'roles': ROLES, 'letter_kinds': LETTER_KINDS, 'actions': ACTIONS, 'sources': SOURCES,
            'request_kinds': REQUEST_KINDS, 'default_due_days': int(S.get('default_due_days') or 3),
            'ceo_threshold': int(S.get('ceo_threshold') or 0),
            'project_roles': PROJECT_ROLES, 'units': UNITS, 'role_unit': ROLE_UNIT, 'p_stages': P_STAGES,
            'p_status': P_STATUS, 'hq_roles': HQ_ROLES, 'broad': is_broad(c, u), 'site_only': is_site_only(c, u), 'can_report': can_report(c, u),
            'categories': CATEGORIES, 'urgencies': URGENCIES, 'att_kinds': ATT_KINDS,
            'cancel_reasons': cancel_reasons(c),
            'my_roles': [{'project_id': p_, 'role_key': k} for p_, k in user_project_roles(c, u['id'])],
            'team_roles': TEAM_ROLES, 'superiors': superiors_of(c, u['id']), 'colleagues': colleagues_of(c, u['id'])}


PUR_SEL = ("SELECT x.*, pr.name project, pr.code project_code, ru.full_name requester, hu.full_name holder, "
           "(SELECT group_concat(title, '، ') FROM purchase_items WHERE purchase_id=x.id) items_text FROM purchases x "
           "LEFT JOIN projects pr ON pr.id=x.project_id LEFT JOIN users ru ON ru.id=x.requester_id "
           "LEFT JOIN users hu ON hu.id=x.holder_id ")


DOC_LABEL_SQL = """CASE r.doc_type WHEN 'letter' THEN (SELECT COALESCE(number,'پیش‌نویس')||' — '||subject FROM letters WHERE id=r.doc_id)
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
        "WHERE r.from_id=? AND r.status IN %s AND r.action!='برگشت' ORDER BY r.id DESC LIMIT 300"
        % (DOC_LABEL_SQL, str(OPEN)), (u['id'],)))
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
        # اول: صادره‌های امضاشده (یا بی‌امضاکننده) که منتظر ثبت و شماره دبیرخانه‌اند
        desk = rows(c.execute(
            "SELECT l.*, 1 to_register FROM letters l WHERE l.status='draft' AND (l.signer_id IS NULL OR "
            "l.signed_at IS NOT NULL) ORDER BY l.id"))
        desk += rows(c.execute(
            "SELECT l.* FROM letters l WHERE l.status='open' AND NOT EXISTS (SELECT 1 FROM referrals r WHERE "
            "r.doc_type='letter' AND r.doc_id=l.id AND r.status IN %s) ORDER BY l.id DESC LIMIT 200" % str(OPEN)))
    pur_held = rows(c.execute(PUR_SEL + "WHERE " + PUR_HELD_SQL + " ORDER BY x.id", (u['id'], u['id'])))
    pur_pay = rows(c.execute(PUR_SEL + "WHERE " + PUR_PAY_SQL + " ORDER BY x.id")) if is_finance(c, u) else []
    pur_mine = rows(c.execute(PUR_SEL + "WHERE x.requester_id=? AND x.status IN ('open','returned') ORDER BY x.id DESC",
                              (u['id'],)))
    return {'inbox': inbox, 'sent': sent, 'approvals': approvals, 'mine': mine, 'pay': pay, 'desk': desk,
            'pur_held': pur_held, 'pur_mine': pur_mine, 'pur_pay': pur_pay, 'today': today()}


@route('GET', '/api/counts')
def api_counts(h, c, u, b, q):
    return {'n': cart_count(c, u)}


def cart_count(c, u):
    """تعداد کارهای منتظر اقدام کاربر (برای نشان کارتابل و اعلان‌ها)."""
    n = c.execute('SELECT COUNT(*) FROM referrals WHERE to_id=? AND status IN %s' % str(OPEN), (u['id'],)).fetchone()[0]
    n += c.execute("SELECT COUNT(*) FROM steps s JOIN requests q ON q.id=s.request_id WHERE s.approver_id=? "
                   "AND s.status='pending' AND q.status='pending'", (u['id'],)).fetchone()[0]
    if u['role'] in ('finance',):
        n += c.execute("SELECT COUNT(*) FROM requests WHERE status='approved'").fetchone()[0]
    n += c.execute("SELECT COUNT(*) FROM purchases x WHERE " + PUR_HELD_SQL, (u['id'], u['id'])).fetchone()[0]
    if is_finance(c, u):
        n += c.execute("SELECT COUNT(*) FROM purchases x WHERE " + PUR_PAY_SQL).fetchone()[0]
    return n


# درخواست‌های کالای در کارتابل: دارنده (جز درخواست‌کننده‌ای که تحویل را تأیید کرده) و انباردارِ منتظر تأیید تحویل
PUR_HELD_SQL = ("((x.holder_id=? AND x.status IN ('open','returned') AND NOT (x.stage='delivery' AND x.recv_at IS NOT NULL)) "
                "OR (x.stage='delivery' AND x.status='open' AND x.wh_at IS NULL AND EXISTS(SELECT 1 FROM project_members m "
                "WHERE m.project_id=x.project_id AND m.role_key='warehouse' AND m.user_id=?)))")
# منتظر پرداخت یا تسویه مالی (پس از تأیید قیمت یا خرید کارگاه)
# خریدهای کارگاه (عمومی و مصرفی) به مالی دفتر مرکزی نمی‌آیند
PUR_PAY_SQL = ("(x.status='open' AND x.settled_at IS NULL AND x.stage IN ('hq_purchase','delivery',"
               "'finance_settle','finance_pay','discrepancy','invoice_fix') AND NOT EXISTS(SELECT 1 FROM purchase_flow sf "
               "WHERE sf.purchase_id=x.id AND sf.stage='site_purchase'))")


# ---------- نامه‌ها
HOLDERS_SQL = ("(SELECT group_concat(u2.full_name, '، ') FROM referrals r2 JOIN users u2 ON u2.id=r2.to_id "
               "WHERE r2.doc_type='%s' AND r2.doc_id=%s.id AND r2.status IN ('new','seen','doing'))")


def letter_filter(u, q, see_all=False):
    w, p = ['1=1'], []
    part = ("(l.created_by=? OR EXISTS(SELECT 1 FROM referrals r WHERE r.doc_type='letter' AND r.doc_id=l.id "
            "AND (r.to_id=? OR r.from_id=?)))")
    if see_all:  # گزارش‌گیران: همه مکاتبات
        pass
    elif not sees_all(u):
        w.append(part); p += [u['id']] * 3
    elif not is_mgr(u):
        w.append('(l.confidential=0 OR ' + part + ')'); p += [u['id']] * 3
    for t in search_words(q):  # هر کلمه باید جایی از نامه، ارجاع‌ها یا نام پیوست‌ها باشد
        w.append("(l.subject LIKE ? OR l.number LIKE ? OR l.counterpart LIKE ? OR l.summary LIKE ? OR l.their_number LIKE ?"
                 " OR l.archive_code LIKE ? OR EXISTS(SELECT 1 FROM referrals r WHERE r.doc_type='letter' AND r.doc_id=l.id "
                 "AND (r.instruction LIKE ? OR r.reply LIKE ?)) OR EXISTS(SELECT 1 FROM attachments a WHERE "
                 "a.doc_type='letter' AND a.doc_id=l.id AND a.deleted_at IS NULL AND a.name LIKE ?))")
        p += [t] * 9
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
    w, p = letter_filter(u, q, can_report(c, u))
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
    dates = {}
    for k, label in (('their_date', 'تاریخ نامه طرف مقابل'), ('letter_date', 'تاریخ نامه')):
        v = (b.get(k) or '').strip()
        if v:
            dates[k] = jnorm(v)
            need(dates[k], '%s نامعتبر است؛ از تقویم انتخاب کنید' % label, 400)
    need(dates.get('their_date', '') <= jtoday(), 'تاریخ نامه طرف مقابل نمی‌تواند بعد از امروز باشد', 400)
    return dict(kind=kind, subject=subject, counterpart=b.get('counterpart') or '', their_number=b.get('their_number') or '',
                their_date=dates.get('their_date', ''), letter_date=dates.get('letter_date', ''),
                project_id=int(b['project_id']) if b.get('project_id') else None,
                priority=b.get('priority') if b.get('priority') in ('normal', 'urgent', 'very_urgent') else 'normal',
                confidential=1 if b.get('confidential') else 0, summary=b.get('summary') or '', source=b.get('source') or '',
                body=(b.get('body') or '').strip(), signer_id=int(b['signer_id']) if b.get('signer_id') else None)


def next_letter_number(c, kind):
    y = jyear()
    seq = c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM letters WHERE year=? AND kind=?', (y, kind)).fetchone()[0]
    return y, seq, '%s %d/%04d' % (LETTER_PREFIX[kind], y, seq)


def ask_signature(c, u, lid, signer_id):
    """نامه برای امضا به کارتابل امضاکننده می‌رود (اگر خودِ نویسنده نباشد)."""
    if signer_id and signer_id != u['id'] and not c.execute(
            "SELECT 1 FROM referrals WHERE doc_type='letter' AND doc_id=? AND to_id=? AND action='امضا' AND status IN %s"
            % str(OPEN), (lid, signer_id)).fetchone():
        make_referrals(c, u, 'letter', lid, [signer_id], 'امضا', 'لطفاً نامه را بررسی و امضا کنید', '')


def dispatch_letter(c, u, lid, instruction='', due=''):
    """ارسال نامه به مخاطب اصلی (اقدام) و رونوشت‌ها (جهت اطلاع)."""
    L = one(c.execute('SELECT * FROM letters WHERE id=?', (lid,)))
    if L['main_to_id'] and L['main_to_id'] != u['id']:
        make_referrals(c, u, 'letter', lid, [L['main_to_id']], L['main_action'] or 'اقدام', instruction or '', due or '')
    cc = [int(x) for x in (L['cc_ids'] or '').split(',') if x.strip() and int(x) != u['id']]
    if cc:
        make_referrals(c, u, 'letter', lid, cc, 'جهت اطلاع', 'رونوشت', '')
    c.execute('UPDATE letters SET dispatched=1 WHERE id=?', (lid,))


@route('POST', '/api/letters')
def api_letter_new(h, c, u, b, q):
    f = letter_fields(b)
    if f['kind'] == 'in':
        need(u['role'] in ('admin', 'secretariat', 'manager'), 'ثبت نامه وارده فقط توسط دبیرخانه انجام می‌شود')
    if f['signer_id']:
        need(c.execute('SELECT 1 FROM users WHERE id=? AND active=1', (f['signer_id'],)).fetchone(), 'امضاکننده نامعتبر', 400)
        if is_site_only(c, u):
            need(f['signer_id'] in colleagues_of(c, u['id']) + [u['id']], 'امضاکننده باید از همکاران پروژه باشد', 400)
    # صادره: شماره فقط هنگام ثبت در دبیرخانه؛ مگر دبیرخانه نامه امضاشده کاغذی را مستقیم ثبت کند
    draft = f['kind'] == 'out' and (f['signer_id'] or u['role'] not in ('admin', 'secretariat'))
    y, seq, number = (None, None, None) if draft else next_letter_number(c, f['kind'])
    cur = c.execute('INSERT INTO letters(kind,year,seq,number,subject,counterpart,their_number,their_date,letter_date,'
                    'project_id,priority,confidential,summary,source,created_by,created_at,body,signer_id,status,'
                    'registered_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (f['kind'], y, seq, number, f['subject'], f['counterpart'], f['their_number'], f['their_date'],
                     f['letter_date'], f['project_id'], f['priority'], f['confidential'], f['summary'], f['source'],
                     u['id'], now(), f['body'], f['signer_id'], 'draft' if draft else 'open', None if draft else now()))
    lid = cur.lastrowid
    log(c, 'letter', lid, u['id'], 'ثبت پیش‌نویس صادره' if draft else 'ثبت', number or '')
    ask_signature(c, u, lid, f['signer_id'])
    # مخاطب اصلی (برای اقدام) و رونوشت‌ها (جهت اطلاع)؛ اگر نامه امضا لازم دارد، پس از امضا فرستاده می‌شود
    main_to = int(b['main_to_id']) if b.get('main_to_id') else None
    cc_ids = [int(x) for x in (b.get('cc_ids') or []) if str(x).strip() and int(x) != main_to]
    if is_site_only(c, u):
        allowed = set(colleagues_of(c, u['id']))
        need(all(t in allowed for t in ([main_to] if main_to else []) + cc_ids),
             'کارکنان کارگاه فقط به همکاران پروژه خود نامه می‌دهند', 400)
    wait_sign = bool(f['signer_id'] and f['signer_id'] != u['id'])
    c.execute('UPDATE letters SET main_to_id=?, cc_text=?, cc_ids=?, main_action=?, dispatched=? WHERE id=?',
              (main_to, (b.get('cc_text') or '').strip(), ','.join(map(str, cc_ids)), b.get('action') or 'اقدام',
               0 if wait_sign else 1, lid))
    if not wait_sign:
        dispatch_letter(c, u, lid, b.get('instruction'), b.get('due'))
    if b.get('to_ids'):
        make_referrals(c, u, 'letter', lid, b['to_ids'], b.get('action') or 'اقدام', b.get('instruction'), b.get('due'))
    return {'id': lid, 'number': number or ''}


@route('POST', r'/api/letters/(\d+)/sign')
def api_letter_sign(h, c, u, b, q, lid):
    """فقط خودِ امضاکننده، با تأیید رمز عبور، امضا می‌کند؛ کلیشه امضا در همان لحظه کنار نامه نگه داشته می‌شود."""
    L = get_doc(c, u, 'letter', int(lid))
    need(L['signer_id'] == u['id'], 'امضای این نامه با شما نیست')
    need(not L['signed_at'], 'این نامه قبلاً امضا شده است', 400)
    need((L['body'] or '').strip(), 'متن نامه خالی است', 400)
    me = one(c.execute('SELECT * FROM users WHERE id=?', (u['id'],)))
    need(pw_ok(b.get('password') or '', me), 'رمز عبور نادرست است', 400)
    need(me['sig_path'] and os.path.exists(os.path.join(FILES, me['sig_path'])),
         'کلیشه امضای شما در «مدیریت سامانه» بارگذاری نشده است', 400)
    os.makedirs(os.path.join(FILES, SIG_COPY_DIR), exist_ok=True)
    rel = os.path.join(SIG_COPY_DIR, 'letter-%d%s' % (L['id'], os.path.splitext(me['sig_path'])[1]))
    shutil.copyfile(os.path.join(FILES, me['sig_path']), os.path.join(FILES, rel))
    c.execute('UPDATE letters SET signed_at=?, sig_name=?, sig_title=?, sig_file=? WHERE id=?',
              (now(), me['full_name'], me['title'] or '', rel, L['id']))
    c.execute("UPDATE referrals SET status='done', done_at=?, reply='امضا شد' WHERE doc_type='letter' AND doc_id=? "
              "AND to_id=? AND action='امضا' AND status IN %s" % str(OPEN), (now(), L['id'], u['id']))
    log(c, 'letter', L['id'], u['id'], 'امضا', me['full_name'])
    if not L['dispatched']:  # حالا که امضا شد، به مخاطب اصلی و رونوشت‌ها می‌رود
        dispatch_letter(c, u, L['id'])
    return {'ok': True}


@route('POST', r'/api/letters/(\d+)/register')
def api_letter_register(h, c, u, b, q, lid):
    """ثبت صادره در دبیرخانه و دریافت شماره."""
    L = get_doc(c, u, 'letter', int(lid))
    need(u['role'] in ('admin', 'secretariat'), 'ثبت و شماره‌گذاری صادره فقط توسط دبیرخانه انجام می‌شود')
    need(L['status'] == 'draft', 'این نامه قبلاً ثبت شده است', 400)
    need(not L['signer_id'] or L['signed_at'], 'نامه هنوز امضا نشده است', 400)
    y, seq, number = next_letter_number(c, L['kind'])
    c.execute("UPDATE letters SET year=?, seq=?, number=?, status='open', registered_at=?, "
              "letter_date=CASE WHEN letter_date='' OR letter_date IS NULL THEN ? ELSE letter_date END WHERE id=?",
              (y, seq, number, now(), jstr(today()), L['id']))
    log(c, 'letter', L['id'], u['id'], 'ثبت در دبیرخانه و شماره', number)
    return {'ok': True, 'number': number}


def letterhead_for(c, L):
    """سربرگ نامه: سربرگ همان پروژه، وگرنه سربرگ پیش‌فرض کارگاه‌ها؛ نامه بدون پروژه روی سربرگ دفتر مرکزی."""
    pr = one(c.execute('SELECT * FROM projects WHERE id=?', (L['project_id'],))) if L['project_id'] else None
    scopes = ['project:%d' % pr['id'], 'site'] if pr and pr['code'] != 'HQ' else ['hq']
    for sc in scopes:
        r = one(c.execute('SELECT * FROM letterheads WHERE scope=? AND path IS NOT NULL', (sc,)))
        if r:
            return r, (pr['name'] if sc != 'hq' else '')
    return None, (pr['name'] if pr and pr['code'] != 'HQ' else '')


@route('GET', r'/api/letters/(\d+)/sheet')
def api_letter_sheet(h, c, u, b, q, lid):
    L = get_doc(c, u, 'letter', int(lid))
    lh, pname = letterhead_for(c, L)
    natt = c.execute("SELECT COUNT(*) FROM attachments WHERE doc_type='letter' AND doc_id=? AND deleted_at IS NULL",
                     (L['id'],)).fetchone()[0]
    who = lambda i: one(c.execute('SELECT full_name, title FROM users WHERE id=?', (i,))) or {}
    to = who(L['main_to_id']) if L['main_to_id'] else {}
    cc = [('%s — %s' % (x.get('full_name', ''), x.get('title', ''))).strip(' —') for x in
          (who(int(i)) for i in (L['cc_ids'] or '').split(',') if i.strip())]
    cc += [t.strip() for t in (L['cc_text'] or '').splitlines() if t.strip()]
    return {'letter': dict({k: L[k] for k in ('id', 'kind', 'number', 'subject', 'counterpart', 'letter_date', 'body',
                                              'signed_at', 'sig_name', 'sig_title', 'status')},
                           to_name=to.get('full_name', ''), to_title=to.get('title', ''), cc=cc),
            'letterhead': {'scope': lh['scope'], 'v': lh['updated_at']} if lh else None,
            'layout': (json.loads(lh['layout'] or '{}') if lh else {}) or LH_DEFAULT, 'project': pname, 'attachments': natt}


@route('GET', r'/api/letters/(\d+)/sig')
def api_letter_sig(h, c, u, b, q, lid):
    L = get_doc(c, u, 'letter', int(lid))
    need(L['signed_at'] and L['sig_file'], 'امضا ندارد', 404)
    return img_file(L['sig_file'])


@route('GET', r'/api/letterheads/([\w:]+)/img')
def api_letterhead_img(h, c, u, b, q, scope):
    r = one(c.execute('SELECT * FROM letterheads WHERE scope=?', (scope,)))
    need(r and r['path'], 'سربرگ پیدا نشد', 404)
    return img_file(r['path'])


def img_file(rel):
    p = os.path.join(FILES, rel)
    need(os.path.exists(p), 'فایل پیدا نشد', 404)
    with open(p, 'rb') as f:
        data = f.read()
    return ('file', 'image/png' if data[:4] == b'\x89PNG' else 'image/jpeg', data)


def save_image(h, sub, stem):
    raw = h.raw_body
    need(raw and (raw[:4] == b'\x89PNG' or raw[:3] == b'\xff\xd8\xff'), 'فقط تصویر PNG یا JPG قابل بارگذاری است', 400)
    os.makedirs(os.path.join(FILES, sub), exist_ok=True)
    rel = os.path.join(sub, '%s-%s%s' % (stem, secrets.token_hex(4), '.png' if raw[:4] == b'\x89PNG' else '.jpg'))
    with open(os.path.join(FILES, rel), 'wb') as f:
        f.write(raw)
    return rel


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
    L['signer'] = (one(c.execute('SELECT full_name FROM users WHERE id=?', (L['signer_id'],))) or {}).get('full_name')
    L.pop('sig_file', None)
    me = one(c.execute('SELECT sig_path FROM users WHERE id=?', (u['id'],)))
    return {'doc': L, 'attachments': att, 'referrals': refs, 'log': lg,
            'can_sign': L['signer_id'] == u['id'] and not L['signed_at'],
            'my_stamp': bool(me and me['sig_path']),
            'can_register': L['status'] == 'draft' and u['role'] in ('admin', 'secretariat')
            and (not L['signer_id'] or bool(L['signed_at']))}


@route('POST', r'/api/letters/(\d+)/update')
def api_letter_update(h, c, u, b, q, lid):
    L = get_doc(c, u, 'letter', int(lid))
    need(is_mgr(u) or u['role'] == 'secretariat' or L['created_by'] == u['id'])
    f = letter_fields(dict(b, kind=L['kind']))
    changed = f['body'] != (L['body'] or '') or f['signer_id'] != L['signer_id'] or f['subject'] != L['subject'] \
        or f['counterpart'] != L['counterpart'] or f['project_id'] != L['project_id']
    if L['kind'] == 'out' and L['status'] != 'draft' and u['role'] != 'admin':
        need(not changed, 'متن، گیرنده و امضاکننده صادره پس از ثبت در دبیرخانه قابل تغییر نیست', 400)
    c.execute('UPDATE letters SET subject=?,counterpart=?,their_number=?,their_date=?,letter_date=?,project_id=?,priority=?,'
              'confidential=?,summary=?,source=?,body=?,signer_id=? WHERE id=?', (f['subject'], f['counterpart'],
              f['their_number'], f['their_date'], f['letter_date'], f['project_id'], f['priority'], f['confidential'],
              f['summary'], f['source'], f['body'], f['signer_id'], L['id']))
    log(c, 'letter', L['id'], u['id'], 'ویرایش مشخصات')
    if changed and L['signed_at']:  # متن امضاشده تغییر کرد: امضا برداشته می‌شود و باید دوباره امضا شود
        c.execute("UPDATE letters SET signed_at=NULL, sig_name='', sig_title='', sig_file='' WHERE id=?", (L['id'],))
        log(c, 'letter', L['id'], u['id'], 'برداشتن امضا به دلیل تغییر نامه')
    if f['signer_id'] and (changed or not L['signer_id']):
        ask_signature(c, u, L['id'], f['signer_id'])
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
        if st == 'returned' and R['from_id'] and R['from_id'] != u['id']:
            # برگشت: از کارتابل برگشت‌دهنده خارج و به کارتابل ارجاع‌دهنده برمی‌گردد
            c.execute('INSERT INTO referrals(doc_type,doc_id,parent_id,from_id,to_id,action,instruction,created_at) '
                      'VALUES(?,?,?,?,?,?,?,?)', (R['doc_type'], R['doc_id'], R['id'], u['id'], R['from_id'], 'برگشت',
                                                  (b.get('reply') or '').strip(), now()))
    else:
        c.execute("UPDATE referrals SET status='doing' WHERE id=?", (R['id'],))
    lbl = {'doing': 'در حال اقدام', 'done': 'انجام شد', 'returned': 'برگشت داده شد'}[st]
    log(c, R['doc_type'], R['doc_id'], u['id'], lbl, b.get('reply') or '')
    return {'ok': True}


# ---------- پیوست‌ها
def pur_folder(P):
    """پوشه پیوست‌های یک درخواست کالا، مثل data\\files\\درخواست کالا\\ک-1405-0012"""
    return os.path.join(PUR_DIR, re.sub(r'[\\/:*?"<>|\s]+', '-', P['number']).strip('-'))


def handover_id(c, P):
    """شماره آخرین رویداد گردش که درخواست را به کارتابل دارنده فعلی رساند."""
    r = c.execute("SELECT MAX(id) FROM purchase_flow WHERE purchase_id=? AND action NOT IN ('attach','edit','delatt')",
                  (P['id'],)).fetchone()
    return r[0] or 0


def can_del_att(c, u, P, a):
    """پیوست را فقط خودِ بارگذارنده حذف می‌کند، آن هم فقط در همان نوبتی که درخواست در کارتابلش است."""
    return (a['uploaded_by'] == u['id'] and P['holder_id'] == u['id'] and P['status'] in ('open', 'returned')
            and (a.get('flow_id') or 0) > handover_id(c, P))


@route('POST', r'/api/attachments/(\d+)/delete')
def api_att_delete(h, c, u, b, q, aid):
    a = one(c.execute('SELECT * FROM attachments WHERE id=? AND deleted_at IS NULL', (int(aid),)))
    need(a and a['doc_type'] == 'purchase', 'پیوست پیدا نشد', 404)
    P = get_doc(c, u, 'purchase', a['doc_id'])
    need(can_del_att(c, u, P, a),
         'فقط پیوستی را که خودتان در همین نوبت گذاشته‌اید و تا وقتی درخواست در کارتابل شماست می‌توانید حذف کنید')
    c.execute('UPDATE attachments SET deleted_at=?, deleted_by=? WHERE id=?', (now(), u['id'], a['id']))
    c.execute('INSERT INTO purchase_flow(purchase_id,stage,action,label,user_id,note,at) VALUES(?,?,?,?,?,?,?)',
              (P['id'], P['stage'], 'delatt', 'حذف پیوست ' + (a['kind'] or ''), u['id'], a['name'], now()))
    log(c, 'purchase', P['id'], u['id'], 'حذف پیوست', a['name'])
    return {'ok': True}


@route('POST', '/api/attach')
def api_attach(h, c, u, b, q):
    dt, did = q.get('doc_type'), int(q.get('doc_id') or 0)
    D = get_doc(c, u, dt, did)
    if dt == 'purchase':
        need(can_attach_pur(c, u, D), 'پیوست فقط وقتی ممکن است که درخواست در کارتابل شما باشد')
    name = urllib.parse.unquote(h.headers.get('X-Filename') or 'file')
    name = re.sub(r'[\\/:*?"<>|]', '_', os.path.basename(name))[:150] or 'file'
    raw = h.raw_body
    need(raw, 'فایل خالی است', 400)
    sub = pur_folder(D) if dt == 'purchase' else datetime.date.today().strftime('%Y-%m')
    os.makedirs(os.path.join(FILES, sub), exist_ok=True)
    rel = os.path.join(sub, '%s_%s' % (secrets.token_hex(6), name))
    with open(os.path.join(FILES, rel), 'wb') as f:
        f.write(raw)
    kind = urllib.parse.unquote(h.headers.get('X-Kind') or '')
    kind = kind if kind in ATT_KINDS else ''
    fid = None
    if dt == 'purchase':
        fid = c.execute('INSERT INTO purchase_flow(purchase_id,stage,action,label,user_id,note,at) VALUES(?,?,?,?,?,?,?)',
                        (did, D['stage'], 'attach', 'پیوست ' + (kind or 'فایل'), u['id'], name, now())).lastrowid
    hq_only = 1 if dt == 'purchase' and D['stage'] in HQ_ONLY_STAGES else 0  # مدارک قیمت برای کارگاه دیده نمی‌شود
    c.execute('INSERT INTO attachments(doc_type,doc_id,name,path,size,uploaded_by,created_at,kind,flow_id,hq_only) '
              'VALUES(?,?,?,?,?,?,?,?,?,?)', (dt, did, name, rel, len(raw), u['id'], now(), kind, fid, hq_only))
    log(c, dt, did, u['id'], 'پیوست' + (' — ' + kind if kind else ''), name)
    return {'ok': True}


# ---------- درخواست‌های مالی
def req_filter(u, q, see_all=False):
    w, p = ['1=1'], []
    if not see_all and u['role'] not in ('admin', 'manager', 'finance'):
        w.append("(q.requester_id=? OR EXISTS(SELECT 1 FROM steps s WHERE s.request_id=q.id AND s.approver_id=?) OR "
                 "EXISTS(SELECT 1 FROM referrals r WHERE r.doc_type='request' AND r.doc_id=q.id AND (r.to_id=? OR r.from_id=?)))")
        p += [u['id']] * 4
    for t in search_words(q):
        w.append('(q.title LIKE ? OR q.number LIKE ? OR q.payee LIKE ? OR q.description LIKE ? OR q.sepidar_no LIKE ?)')
        p += [t] * 5
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
    w, p = req_filter(u, q, can_report(c, u))
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
        pd = need_jdate(b.get('paid_at') or jtoday(), 'تاریخ پرداخت', max_=jtoday(),
                        max_msg='تاریخ پرداخت نمی‌تواند بعد از امروز باشد')
        c.execute('UPDATE requests SET status=?, paid_amount=?, paid_at=?, pay_note=?, sepidar_no=?, closed_at=? WHERE id=?',
                  ('closed' if sep else 'paid', amt, pd, b.get('pay_note') or '', sep,
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
    team = rows(c.execute('SELECT t.role_key, t.user_id, us.full_name FROM project_team t LEFT JOIN users us '
                          'ON us.id=t.user_id WHERE t.project_id=? ORDER BY us.full_name', (pr['id'],)))
    return {'project': pr, 'members': mem, 'team': team}


@route('POST', r'/api/projects/(\d+)/update')
def api_project_update(h, c, u, b, q, pid):
    need(is_mgr(u), 'ویرایش پروژه و ارکان آن فقط توسط هیات مدیره و مدیر سیستم انجام می‌شود')
    pr = one(c.execute('SELECT * FROM projects WHERE id=?', (int(pid),)))
    need(pr, 'پروژه پیدا نشد', 404)
    name = (b.get('name') or '').strip(); need(name, 'نام پروژه الزامی است', 400)
    need(not c.execute('SELECT 1 FROM projects WHERE name=? AND active=1 AND id!=?', (name, pr['id'])).fetchone(),
         'پروژه دیگری با این نام وجود دارد', 400)
    c.execute('UPDATE projects SET name=? WHERE id=?', (name, pr['id']))  # کد پروژه دیگر استفاده نمی‌شود
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
    if 'team' in b:
        c.execute('DELETE FROM project_team WHERE project_id=?', (pr['id'],))
        for key, label, _ in TEAM_ROLES:
            for v in (b['team'] or {}).get(key) or []:
                v = int(v)
                need(c.execute('SELECT 1 FROM users WHERE id=? AND active=1', (v,)).fetchone(), 'کاربر «%s» نامعتبر است' % label, 400)
                c.execute('INSERT OR REPLACE INTO project_team(project_id,user_id,role_key) VALUES(?,?,?)', (pr['id'], v, key))
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
    if stage == 'invoice_fix':  # اصلاح با همان پشتیبانی که خرید را انجام داده (کارگاه یا دفتر مرکزی)
        stage = purchase_stage_of(c, P)
    if stage == 'unit_approval':
        # تأیید بالادست مستقیم درخواست‌کننده (مهندس ← معاونش، پشتیبانی و انبار ← سرپرست کارگاه)؛ وگرنه رئیس واحد
        own = [k for _, k in user_project_roles(c, P['requester_id'], P['project_id']) if k in SUPERIOR]
        kind, key = 'member', (SUPERIOR[own[0]] if own else UNIT_HEAD[P['unit']])
    else:
        kind, key = STAGE_HOLDER[stage]
    if kind == 'member':
        uid = pmembers(c, P['project_id']).get(key)
        need(uid, '«%s» برای این پروژه تعریف نشده؛ هیات مدیره باید ارکان پروژه را در صفحه پروژه تکمیل کند'
             % labels[key], 400)
        if stage in ('pm_approve', 'price_approve') and uid == P['requester_id']:
            # خودتأییدی ممنوع: درخواستِ خودِ مدیر پروژه به مدیرعامل می‌رود
            uid = int(settings(c).get('ceo_user') or 0)
            need(uid, 'درخواست‌کننده خود مدیر پروژه است و مدیرعامل در تنظیمات تعیین نشده', 400)
    else:
        uid = int(settings(c).get(key) or 0)
        need(uid, '«%s» در «مدیریت سامانه ← تنظیمات» تعیین نشده' % HQ_ROLES[key], 400)
    need(c.execute('SELECT 1 FROM users WHERE id=? AND active=1', (uid,)).fetchone(),
         'کاربر مسئول مرحله «%s» غیرفعال است' % P_STAGES[stage], 400)
    return uid


def to_num(v):
    """عدد مقدار (با ارقام فارسی و ممیز فارسی)؛ نامعتبر = None"""
    s = str(v if v is not None else '').translate(FA2EN).replace('٫', '.').replace(',', '').strip()
    try:
        return float(s) if s else None
    except ValueError:
        return None


def fmt_num(x):
    return ('%g' % x) if x is not None else ''


def tech_already(c, P):
    """معاون فنی قبلاً در همین نوبت درخواست را ثبت یا تأیید کرده است (دوباره لازم نیست)."""
    t = pmembers(c, P['project_id']).get('tech')
    if not t:
        return False
    if t == P['requester_id']:
        return True
    last_ret = c.execute("SELECT COALESCE(MAX(id),0) FROM purchase_flow WHERE purchase_id=? AND action='resubmit'",
                         (P['id'],)).fetchone()[0]
    return c.execute("SELECT 1 FROM purchase_flow WHERE purchase_id=? AND user_id=? AND id>? AND "
                     "action IN ('approve','submit') AND stage IN ('draft','unit_approval')",
                     (P['id'], t, last_ret)).fetchone() is not None


def done_this_round(c, P, stage):
    """این مرحله در نوبت فعلی (پس از آخرین ارسال مجدد) تأیید شده است."""
    last_ret = c.execute("SELECT COALESCE(MAX(id),0) FROM purchase_flow WHERE purchase_id=? AND action='resubmit'",
                         (P['id'],)).fetchone()[0]
    return c.execute("SELECT 1 FROM purchase_flow WHERE purchase_id=? AND stage=? AND action='approve' AND id>?",
                     (P['id'], stage, last_ret)).fetchone() is not None


def is_general(P):
    """کالای عمومی و مصرفی: در کارگاه و با پشتیبانی کارگاه تأمین می‌شود (بدون دفتر مرکزی)."""
    return (P['category'] or 'general') != 'main'


def after_stock(c, P, notes):
    """پس از استعلام انبار: عمومی و مصرفی ← سرپرست کارگاه؛ اصلی ← معاون فنی، سپس سرپرست کارگاه."""
    if is_general(P):
        return 'supervisor_approve'
    if tech_already(c, P):
        notes.append('بررسی معاون فنی لازم نبود؛ معاون فنی قبلاً درخواست را ثبت یا تأیید کرده است')
        return 'supervisor_approve'
    return 'tech_review'


def after_supervisor(c, P, notes):
    """پس از تأیید سرپرست کارگاه: عمومی و مصرفی ← خرید در کارگاه؛ اصلی ← مدیر پروژه و دفتر مرکزی.
    درخواست اصلی‌ای که پیش از نسخه ۲.۲ به سرپرست رسیده و معاون فنی هنوز بررسی‌اش نکرده، اول به معاون فنی می‌رود."""
    if not is_general(P) and not done_this_round(c, P, 'tech_review') and not tech_already(c, P) and not c.execute(
            "SELECT 1 FROM purchase_flow WHERE purchase_id=? AND stage='tech_review'", (P['id'],)).fetchone():
        return 'tech_review'
    return after_tech(P)


def after_tech(P):
    return 'site_purchase' if is_general(P) else 'pm_approve'


def next_code(c, pid, dtype):
    """کد سند بعدی: {نوع سند}-{سریال ۴ رقمی}، مثل MR-0042 (سریال سراسری هر نوع سند)."""
    c.execute('INSERT OR IGNORE INTO doc_serials(project_id,dtype,last) VALUES(0,?,0)', (dtype,))
    c.execute('UPDATE doc_serials SET last=last+1 WHERE project_id=0 AND dtype=?', (dtype,))
    n = c.execute('SELECT last FROM doc_serials WHERE project_id=0 AND dtype=?', (dtype,)).fetchone()[0]
    return '%s-%04d' % (dtype, n)


def invoice_doc(c, P):
    """فاکتور خرید: فاکتور اصلی؛ تا وقتی بارگذاری نشده، پیش‌فاکتور به‌جای فاکتور حساب می‌شود."""
    for kind in ('فاکتور', 'پیش‌فاکتور'):
        if c.execute("SELECT 1 FROM attachments WHERE doc_type='purchase' AND doc_id=? AND kind=? AND deleted_at IS NULL",
                     (P['id'], kind)).fetchone():
            return kind
    return ''


def docs_check(c, P):
    """تطبیق سه‌طرفه = وجود سه مدرک: درخواست کالا، اعلام وصول (تأیید تحویل‌گیرنده و انباردار) و فاکتور.
    مقدار، مبلغ و شماره فاکتور کنترل نمی‌شود."""
    grn = bool(P['recv_at'] and P['wh_at'])
    inv = invoice_doc(c, P)
    missing = ([] if grn else ['اعلام وصول کالا (تأیید تحویل‌گیرنده و انباردار) کامل نشده']) + \
        ([] if inv else ['فاکتور (یا پیش‌فاکتور) بارگذاری نشده'])
    return {'mr': P['number'], 'grn': P['grn_no'] if grn else '', 'invoice': inv, 'missing': missing, 'ok': not missing}


def site_path(c, P):
    """این درخواست در کارگاه خریده شده (عمومی و مصرفی)؛ مالی دفتر مرکزی در آن نقشی ندارد."""
    return purchase_stage_of(c, P) == 'site_purchase'


def purchase_stage_of(c, P):
    """مرحله خریدِ این درخواست (برای برگشت از تحویل در صورت مغایرت)."""
    return 'site_purchase' if c.execute("SELECT 1 FROM purchase_flow WHERE purchase_id=? AND stage='site_purchase'",
                                        (P['id'],)).fetchone() else 'hq_purchase'


def pflow(c, pid, u, stage, action, label, note=''):
    c.execute('INSERT INTO purchase_flow(purchase_id,stage,action,label,user_id,note,at) VALUES(?,?,?,?,?,?,?)',
              (pid, stage, action, label, u['id'], note, now()))
    log(c, 'purchase', pid, u['id'], label, note)


def to_int(v):
    d = re.sub(r'[^\d]', '', str(v or '').translate(FA2EN))
    return int(d) if d else None


def pur_fields(c, u, b, pid_fixed=None, req_date=None):
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
    rd = jnorm(req_date) or jtoday()
    nd = need_jdate(b.get('need_date'), 'تاریخ نیاز', min_=rd, min_msg='تاریخ نیاز نمی‌تواند قبل از تاریخ درخواست (%s) باشد' % rd)
    need((b.get('purpose') or '').strip(), 'کادر «جهت استفاده» را پر کنید', 400)
    items = []
    for it in b.get('items') or []:
        t = (it.get('title') or '').strip()
        if not t:
            continue
        qty = (str(it.get('qty') or '')).translate(FA2EN).replace('٫', '.').strip()
        need(qty, 'مقدار کالای «%s» وارد نشده' % t, 400)
        need(to_num(qty) is not None and to_num(qty) > 0, 'مقدار کالای «%s» باید عدد باشد (واحد را در ستون واحد بنویسید)' % t, 400)
        items.append((t, qty, (it.get('unit') or '').strip(), (it.get('spec') or '').strip(), (it.get('note') or '').strip()))
    need(items, 'حداقل یک ردیف کالا با شرح و مقدار وارد کنید', 400)
    return dict(project_id=pid, unit=unit, warehouse=(b.get('warehouse') or '').strip(), category=cat, urgency=urg,
                need_date=nd, purpose=(b.get('purpose') or '').strip(), requester_id=u['id']), items


def save_items(c, pid, items):
    # موجودیِ ثبت‌شده انبار برای کالاهایی که در ویرایش باقی مانده‌اند حفظ می‌شود
    stock = {r['title']: r['stock_qty'] for r in c.execute('SELECT title, stock_qty FROM purchase_items WHERE purchase_id=?',
                                                           (pid,))}
    c.execute('DELETE FROM purchase_items WHERE purchase_id=?', (pid,))
    for i, it in enumerate(items, 1):
        st = stock.get(it[0]) or ''
        if st and (to_num(st) or 0) > (to_num(it[1]) or 0):
            st = it[1]
        c.execute('INSERT INTO purchase_items(purchase_id,row_no,title,qty,unit,spec,note,stock_qty) VALUES(?,?,?,?,?,?,?,?)',
                  (pid, i) + it + (st,))


def remaining(it):
    """مقدار مانده برای خرید = مقدار درخواست − تحویل از انبار"""
    return max(0.0, (to_num(it['qty']) or 0) - (to_num(it['stock_qty']) or 0))


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
    """درخواستی که معاون فنی صادر یا تأیید می‌کند (از جمله درخواست مهندسان دفتر فنی)، رونوشت به معاون اجرایی می‌رود."""
    mem = pmembers(c, f['project_id'])
    if mem.get('tech') == u['id'] and mem.get('exec') and mem.get('exec') != u['id'] and not c.execute(
            "SELECT 1 FROM referrals WHERE doc_type='purchase' AND doc_id=? AND to_id=?", (pid, mem['exec'])).fetchone():
        c.execute('INSERT INTO referrals(doc_type,doc_id,from_id,to_id,action,instruction,created_at) VALUES(?,?,?,?,?,?,?)',
                  ('purchase', pid, u['id'], mem['exec'], 'جهت اطلاع', 'رونوشت درخواست کالای معاون فنی', now()))


def pur_filter(c, u, q):
    w, p = ["(x.stage!='draft' OR x.requester_id=?)"], [u['id']]  # پیش‌نویس فقط برای خود درخواست‌کننده
    if not is_broad(c, u):
        w.append('(x.requester_id=? OR x.holder_id=? OR x.project_id IN (SELECT project_id FROM project_members '
                 'WHERE user_id=?) OR EXISTS(SELECT 1 FROM purchase_flow f WHERE f.purchase_id=x.id AND f.user_id=?) '
                 "OR EXISTS(SELECT 1 FROM project_team t WHERE t.project_id=x.project_id AND t.user_id=? AND "
                 "x.unit=CASE t.role_key WHEN 'exec_eng' THEN 'exec' ELSE 'tech' END))")
        p += [u['id']] * 5
    for k in ('project_id', 'holder_id'):
        if q.get(k):
            w.append('x.%s=?' % k); p.append(int(q[k]))
    if q.get('unsettled'):
        w.append(PUR_PAY_SQL)
    for k in ('unit', 'stage', 'status', 'category', 'urgency'):
        if q.get(k):
            vals = q[k].split(',')
            w.append('x.%s IN (%s)' % (k, ','.join('?' * len(vals)))); p += vals
    for t in search_words(q):  # هر کلمه باید جایی از درخواست، اقلام، پروژه، افراد یا پیوست‌ها باشد
        w.append("(x.number LIKE ? OR x.purpose LIKE ? OR x.supplier LIKE ? OR x.warehouse LIKE ? OR x.sepidar_no LIKE ? "
                 "OR EXISTS(SELECT 1 FROM purchase_items i WHERE i.purchase_id=x.id AND (i.title LIKE ? OR i.spec LIKE ? "
                 "OR i.note LIKE ?)) OR EXISTS(SELECT 1 FROM projects pj WHERE pj.id=x.project_id AND (pj.name LIKE ? OR "
                 "pj.code LIKE ?)) OR EXISTS(SELECT 1 FROM users us WHERE us.id=x.requester_id AND us.full_name LIKE ?) "
                 "OR EXISTS(SELECT 1 FROM attachments a WHERE a.doc_type='purchase' AND a.doc_id=x.id AND "
                 "a.deleted_at IS NULL AND a.name LIKE ?))")
        p += [t] * 12
    if q.get('from'):
        w.append('substr(x.created_at,1,10)>=?'); p.append(q['from'])
    if q.get('to'):
        w.append('substr(x.created_at,1,10)<=?'); p.append(q['to'])
    return ' AND '.join(w), p


@route('GET', '/api/purchases')
def api_purchases(h, c, u, b, q):
    w, p = pur_filter(c, u, q)
    res = rows(c.execute(PUR_SEL + 'WHERE %s ORDER BY x.id DESC LIMIT %d' % (w, int(q.get('limit') or 500)), p))
    if is_site_only(c, u):  # قیمت‌ها برای کارکنان کارگاه نمایش داده نمی‌شود
        for P in res:
            for k in ('supplier', 'amount', 'proposed_supplier', 'proposed_amount', 'paid_amount', 'sepidar_no'):
                P[k] = None
    return res


PUR_CSV_HEAD = ['شماره', 'تاریخ', 'پروژه', 'واحد درخواست‌کننده', 'انبار محل درخواست', 'دسته', 'فوریت',
                'تاریخ نیاز', 'جهت استفاده', 'درخواست‌کننده', 'نسخه',
                'سفارش خرید', 'اعلام وصول', 'ردیف',
                'شرح کالا', 'مقدار', 'واحد', 'مشخصات فنی', 'توضیحات', 'تحویل از انبار', 'مانده برای خرید',
                'مقدار خریداری‌شده', 'واحد خرید', 'وضعیت خرید', 'توضیح خرید', 'مقدار تحویل‌گرفته',
                'وضعیت', 'مرحله', 'در دست', 'جمع پرداخت (ریال)',
                'کد بایگانی', 'علت لغو']


def pur_csv(c, lst, fname):
    out = io.StringIO(); wr = csv.writer(out)
    wr.writerow(PUR_CSV_HEAD)
    units, cats, urgs = dict(UNITS), dict(CATEGORIES), dict(URGENCIES)
    for P in lst:
        its = rows(c.execute('SELECT * FROM purchase_items WHERE purchase_id=? ORDER BY row_no', (P['id'],))) or [{}]
        for it in its:
            wr.writerow([P['number'], P['req_date'], P['project'] or '', units.get(P['unit'], ''),
                         P['warehouse'], cats.get(P['category'], ''), urgs.get(P['urgency'], ''), P['need_date'],
                         P['purpose'], P['requester'] or '', P['version'],
                         P['po_no'] or '', P['grn_no'] or '', it.get('row_no', ''), it.get('title', ''),
                         it.get('qty', ''), it.get('unit', ''), it.get('spec', ''), it.get('note', ''),
                         it.get('stock_qty', ''), fmt_num(remaining(it)) if it else '', it.get('bought_qty', ''),
                         it.get('bought_unit', ''), BUY_STATUS.get(it.get('bought_status') or '', ''), it.get('bought_note', ''),
                         it.get('recv_qty', ''),
                         P_STATUS.get(P['status'], P['status']), P_STAGES.get(P['stage'], P['stage']), P['holder'] or '',
                         P['paid_amount'] or '',
                         P['archive_code'], (P['cancel_reason'] or '') + (' — ' + P['cancel_note'] if P['cancel_note'] else '')])
    return ('csv', fname, out.getvalue())


@route('GET', '/api/search')
def api_search(h, c, u, b, q):
    """جستجوی یک‌جا در مکاتبات و درخواست‌ها؛ هر کس فقط آنچه اجازه دیدنش را دارد."""
    need(search_words(q), 'عبارت جستجو را بنویسید', 400)
    qq = {'q': q['q'], 'limit': 200}
    res = {'letters': api_letters(h, c, u, b, dict(qq)), 'purchases': api_purchases(h, c, u, b, dict(qq)), 'requests': []}
    if not is_site_only(c, u):
        res['requests'] = api_requests(h, c, u, b, dict(qq))
    return res


@route('GET', '/api/purchases.csv')
def api_purchases_csv(h, c, u, b, q):
    q['limit'] = 100000
    return pur_csv(c, api_purchases(h, c, u, b, q), 'material-requests.csv')


@route('GET', r'/api/purchases/(\d+)\.csv')
def api_purchase_csv(h, c, u, b, q, pid):
    get_doc(c, u, 'purchase', int(pid))
    lst = rows(c.execute(PUR_SEL + 'WHERE x.id=?', (int(pid),)))
    if is_site_only(c, u):
        for k in ('supplier', 'amount', 'paid_amount'):
            lst[0][k] = None
    return pur_csv(c, lst, 'material-request-%s.csv' % pid)


@route('POST', '/api/purchases')
def api_purchase_new(h, c, u, b, q):
    f, items = pur_fields(c, u, b)
    y = jyear()
    seq = c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM purchases WHERE year=?', (y,)).fetchone()[0]
    number = next_code(c, f['project_id'], 'MR')  # مثل MR-0001 (درخواست‌های قبلی شماره قدیمی خود را نگه می‌دارند)
    cur = c.execute('INSERT INTO purchases(year,seq,number,project_id,unit,warehouse,category,urgency,need_date,purpose,'
                    'requester_id,req_date,stage,status,holder_id,version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)',
                    (y, seq, number, f['project_id'], f['unit'], f['warehouse'], f['category'], f['urgency'],
                     f['need_date'], f['purpose'], u['id'], jstr(today()), 'draft', 'open', u['id'], now()))
    pid = cur.lastrowid
    save_items(c, pid, items)
    save_version(c, pid, u, 'نسخه اصلی')
    pflow(c, pid, u, 'new', 'create', 'ایجاد پیش‌نویس', number)
    return {'id': pid, 'number': number}


def can_edit(u, P):
    if u['role'] == 'admin':  # مدیر سیستم در هر مرحله، حتی پس از بایگانی
        return True
    if P['status'] not in ('open', 'returned') or P['stage'] not in EDIT_STAGES:
        return False
    return P['holder_id'] == u['id']  # فقط کسی که درخواست اکنون در کارتابل اوست


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
    for a in att:
        a['can_delete'] = can_del_att(c, u, P, a)
    for r in refs:  # رونوشت «جهت اطلاع» با دیدن بسته می‌شود
        if r['to_id'] == u['id'] and r['status'] == 'new':
            ns = 'done' if r['action'] == 'جهت اطلاع' else 'seen'
            c.execute('UPDATE referrals SET status=?, seen_at=?, done_at=? WHERE id=?',
                      (ns, now(), now() if ns == 'done' else None, r['id']))
            r['status'] = ns
    site = is_site_only(c, u)
    if site:  # قیمت‌ها و مدارک پس از مدیر پروژه برای کارکنان کارگاه نمایش داده نمی‌شود
        att = [a for a in att if not a.get('hq_only')]
        flow = [f for f in flow if not (f['stage'] in HQ_ONLY_STAGES and f['action'] in ('attach', 'delatt'))]
        for f in flow:
            if f['stage'] in HQ_ONLY_STAGES:
                f['note'] = ''
        for k in ('supplier', 'amount', 'proposed_supplier', 'proposed_amount', 'paid_amount', 'sepidar_no'):
            P[k] = None
    pays = [] if site else rows(c.execute('SELECT p.*, us.full_name user_name FROM purchase_payments p LEFT JOIN users us '
                                          'ON us.id=p.user_id WHERE purchase_id=? ORDER BY p.id', (P['id'],)))
    for it in items:
        it['remaining'] = fmt_num(remaining(it))
    bought = c.execute("SELECT 1 FROM purchase_flow WHERE purchase_id=? AND action='purchased'", (P['id'],)).fetchone()
    mem = pmembers(c, P['project_id'])
    wh = one(c.execute('SELECT full_name FROM users WHERE id=?', (P['wh_by'] or mem.get('warehouse') or 0,))) or {}
    return {'docs': docs_check(c, P) if bought else None, 'receipt': bool(bought), 'warehouse_name': wh.get('full_name', ''),
            'site_path': site_path(c, P), 'is_admin': u['role'] == 'admin',
            'doc': P, 'items': items, 'flow': flow, 'versions': vers, 'attachments': att, 'referrals': refs,
            'actions': allowed_actions(c, u, P), 'can_edit': can_edit(u, P), 'can_cancel': can_cancel(u, P),
            'can_attach': can_attach_pur(c, u, P), 'payments': pays, 'can_pay': can_pay(c, u, P),
            'pay_methods': PAY_METHODS, 'buy_status': BUY_STATUS, 'site_view': site,
            'hq_only_stages': HQ_ONLY_STAGES}


def is_warehouse(c, u, P):
    return pmembers(c, P['project_id']).get('warehouse') == u['id']


def allowed_actions(c, u, P):
    """اقدام‌هایی که این کاربر اکنون روی درخواست می‌تواند انجام دهد."""
    if P['status'] != 'open':
        return []
    acts = list(P_FLOW.get(P['stage'], {}))
    if P['stage'] == 'delivery':  # تحویل دوطرفه: درخواست‌کننده و انبار، هر کدام جدا
        out = []
        if P['holder_id'] == u['id'] and not P['recv_at']:
            out += ['recv_ok', 'recv_bad']
        if is_warehouse(c, u, P) and not P['wh_at']:
            out += ['wh_ok', 'wh_bad']
        return out
    if P['stage'] in ('price_approve', 'discrepancy') and is_mgr(u):  # مدیر پروژه یا هر عضو هیات مدیره
        return acts
    if P['stage'] == 'finance_settle' and P['holder_id'] == u['id']:  # تسویه فقط با کامل بودن سه مدرک
        return ['settled'] if docs_check(c, P)['ok'] else ['need_docs']
    return acts if P['holder_id'] == u['id'] else []


def can_attach_pur(c, u, P):
    if P['status'] not in ('open', 'returned'):
        return False
    if P['holder_id'] == u['id'] or (P['stage'] == 'delivery' and is_warehouse(c, u, P)):
        return True
    return P['stage'] == 'price_approve' and is_mgr(u)


def is_finance(c, u):
    return u['role'] == 'finance' or str(u['id']) == settings(c).get('finance_manager') or u['role'] == 'admin'


def can_pay(c, u, P):
    """پرداخت (یک یا چند نوبت، نقد یا چک، قبل یا بعد از تحویل) پس از تأیید قیمت یا خرید کارگاه."""
    return (is_finance(c, u) and P['status'] == 'open' and P['stage'] in PAY_STAGES and not P['settled_at']
            and P['stage'] != 'site_purchase' and not site_path(c, P))


def move(c, u, P, stage, label, note='', status='open'):
    """انتقال درخواست به مرحله بعد و ثبت در گردش."""
    holder = None if stage == 'done' else (P['requester_id'] if stage in ('returned', 'delivery') else stage_holder(c, P, stage))
    if stage == 'returned':
        status = 'returned'
    c.execute('UPDATE purchases SET status=?, stage=?, holder_id=?, closed_at=? WHERE id=?',
              (status, stage, holder, now() if stage == 'done' else None, P['id']))
    pflow(c, P['id'], u, P['stage'], label[0], label[1], note)


def after_delivery(c, P):
    """پس از اعلام وصول: خرید کارگاه (عمومی و مصرفی) مستقیم به بایگانی؛ خرید دفتر مرکزی به مالی (مگر تسویه شده باشد)."""
    return 'archive' if P['settled_at'] or site_path(c, P) else 'finance_settle'


@route('POST', r'/api/purchases/(\d+)/act')
def api_purchase_act(h, c, u, b, q, pid):
    P = get_doc(c, u, 'purchase', int(pid))
    a = b.get('action')
    need(a in allowed_actions(c, u, P), 'این اقدام اکنون با شما نیست')
    nxt, label = P_FLOW[P['stage']][a]
    note = (b.get('note') or '').strip()
    notes = []
    if a in ('return', 'requote', 'recv_bad', 'wh_bad', 'accept', 'fix'):
        need(note, 'علت را بنویسید', 400)
    if a == 'approve' and P['stage'] == 'price_approve' and not P['po_no']:  # صدور سفارش خرید
        c.execute('UPDATE purchases SET po_no=? WHERE id=?', (next_code(c, P['project_id'], 'PO'), P['id']))
    if a == 'purchased' and P['stage'] == 'site_purchase' and not P['po_no']:  # خرید کارگاه: سفارش همان خرید است
        c.execute('UPDATE purchases SET po_no=? WHERE id=?', (next_code(c, P['project_id'], 'PO'), P['id']))
    if a == 'accept':
        c.execute('UPDATE purchases SET disc_ok_by=?, disc_ok_at=?, disc_note=? WHERE id=?', (u['id'], now(), note, P['id']))
    its = rows(c.execute('SELECT * FROM purchase_items WHERE purchase_id=? ORDER BY row_no', (P['id'],)))
    posted = {int(x.get('id') or 0): x for x in (b.get('items') or [])}

    if a == 'submit':
        nxt, label = start_stage(c, u, P)
    elif a == 'stock':  # انبار موجودی هر قلم را ثبت می‌کند؛ مانده برای خرید می‌رود
        full = part = 0
        for it in its:
            s = to_num((posted.get(it['id']) or {}).get('stock')) or 0
            need(0 <= s <= (to_num(it['qty']) or 0), 'موجودیِ «%s» باید بین صفر و مقدار درخواست باشد' % it['title'], 400)
            c.execute('UPDATE purchase_items SET stock_qty=? WHERE id=?', (fmt_num(s) if s else '', it['id']))
            if s:
                full, part = (full + 1, part) if s >= (to_num(it['qty']) or 0) else (full, part + 1)
        left = sum(remaining(dict(it, stock_qty=fmt_num(to_num((posted.get(it['id']) or {}).get('stock')) or 0)))
                   for it in its)
        label = fa_num('ثبت موجودی انبار: %d قلم کامل و %d قلم بخشی از انبار تحویل شد' % (full, part)) if full or part \
            else 'ثبت موجودی انبار: هیچ قلمی موجود نیست'
        if left <= 0:
            move(c, u, P, 'done', ('stock', label + ' — همه اقلام از انبار تحویل شد'), note, 'delivered')
            return {'ok': True}
        nxt = after_stock(c, P, notes)
    elif a == 'approve' and P['stage'] == 'supervisor_approve':
        nxt = after_supervisor(c, P, notes)
    elif a == 'approve' and P['stage'] == 'tech_review':
        # درخواست قدیمی که سرپرست کارگاه پیش از معاون فنی تأییدش کرده بود، مستقیم به خرید یا مدیر پروژه می‌رود
        nxt = after_tech(P) if done_this_round(c, P, 'supervisor_approve') else 'supervisor_approve'
    elif a == 'quoted':  # استعلام: حداقل یک پیش‌فاکتور (نام فروشنده و مبلغ در سامانه ثبت نمی‌شود)
        need(c.execute("SELECT 1 FROM attachments WHERE doc_type='purchase' AND doc_id=? AND kind='پیش‌فاکتور' "
                       'AND deleted_at IS NULL', (P['id'],)).fetchone(), 'حداقل یک پیش‌فاکتور پیوست کنید', 400)
    elif a == 'purchased':  # اعلام نهایی پشتیبانی: مقدار، واحد و وضعیت خرید هر قلم
        for it in its:
            if remaining(it) <= 0:
                continue
            x = posted.get(it['id']) or {}
            stt = x.get('status')
            need(stt in BUY_STATUS, 'وضعیت خرید «%s» را تعیین کنید' % it['title'], 400)
            bq = to_num(x.get('qty'))
            if stt != 'none':
                need(bq and bq > 0, 'مقدار خریداری‌شده «%s» را وارد کنید' % it['title'], 400)
            need(stt == 'bought' or (x.get('note') or '').strip(), 'برای «%s» علت خرید ناقص یا نشدن را بنویسید' % it['title'], 400)
            c.execute('UPDATE purchase_items SET bought_qty=?, bought_unit=?, bought_status=?, bought_note=? WHERE id=?',
                      (fmt_num(bq) if stt != 'none' else '0', (x.get('unit') or it['unit'] or '').strip(), stt,
                       (x.get('note') or '').strip(), it['id']))
        if P['stage'] == 'site_purchase':
            need(invoice_doc(c, P), 'فاکتور (یا پیش‌فاکتور) خرید را پیوست کنید', 400)
        c.execute('UPDATE purchases SET recv_by=NULL, recv_at=NULL, wh_by=NULL, wh_at=NULL WHERE id=?', (P['id'],))
    elif a in ('recv_ok', 'wh_ok', 'recv_bad', 'wh_bad'):
        if a == 'recv_ok':
            for it in its:
                x = posted.get(it['id'])
                if x is not None:
                    c.execute('UPDATE purchase_items SET recv_qty=? WHERE id=?', (fmt_num(to_num(x.get('qty'))), it['id']))
            c.execute('UPDATE purchases SET recv_by=?, recv_at=? WHERE id=?', (u['id'], now(), P['id']))
            P = dict(P, recv_at=now())
        elif a == 'wh_ok':
            c.execute('UPDATE purchases SET wh_by=?, wh_at=? WHERE id=?', (u['id'], now(), P['id']))
            P = dict(P, wh_at=now())
        if a.endswith('_bad'):  # مغایرت: برمی‌گردد به خرید برای اصلاح
            c.execute('UPDATE purchases SET recv_by=NULL, recv_at=NULL, wh_by=NULL, wh_at=NULL WHERE id=?', (P['id'],))
            nxt = purchase_stage_of(c, P)
        elif P['recv_at'] and P['wh_at']:  # رسید تحویل کالا صادر می‌شود
            nxt = after_delivery(c, P)
            grn = P['grn_no'] or next_code(c, P['project_id'], 'GRN')
            c.execute('UPDATE purchases SET grn_no=? WHERE id=?', (grn, P['id']))
            notes.append('تحویل کالا کامل شد (درخواست‌کننده و انبار) — رسید %s' % grn)
        else:
            pflow(c, P['id'], u, P['stage'], a, label, note)
            return {'ok': True}
    elif a == 'settled':
        c.execute('UPDATE purchases SET settled_at=?, settled_by=? WHERE id=?', (now(), u['id'], P['id']))
    elif a == 'paid':  # مرحله قدیمی
        amt = to_int(b.get('paid_amount'))
        need(amt is not None, 'مبلغ پرداختی را وارد کنید', 400)
        c.execute('UPDATE purchases SET paid_amount=?, paid_at=?, sepidar_no=? WHERE id=?',
                  (amt, (b.get('paid_at') or jstr(today())).translate(FA2EN), (b.get('sepidar_no') or '').strip(), P['id']))
    elif a == 'archived':
        c.execute('UPDATE purchases SET archive_code=? WHERE id=?', ((b.get('archive_code') or '').strip(), P['id']))

    label = label + (' — ارسال به ' + P_STAGES[nxt] if a in ('stock', 'approve') and nxt not in ('done', 'returned') else '')
    move(c, u, P, nxt, (a, label), '؛ '.join([note] + notes if note else notes),
         'closed' if nxt == 'done' else 'open')
    if (P['stage'] == 'unit_approval' and a == 'approve') or a == 'submit':
        cc_exec(c, u, P['id'], P)
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/payment')
def api_purchase_payment(h, c, u, b, q, pid):
    """ثبت یک نوبت پرداخت (نقد، حواله یا چک)؛ پرداخت می‌تواند چند نوبت و قبل یا بعد از تحویل باشد."""
    P = get_doc(c, u, 'purchase', int(pid))
    need(can_pay(c, u, P), 'ثبت پرداخت برای این درخواست اکنون ممکن نیست')
    amt = to_int(b.get('amount'))
    need(amt, 'مبلغ پرداخت را وارد کنید', 400)
    pd = need_jdate(b.get('paid_at') or jtoday(), 'تاریخ پرداخت', max_=jtoday(),
                    max_msg='تاریخ پرداخت نمی‌تواند بعد از امروز باشد')
    method = b.get('method') if b.get('method') in PAY_METHODS else PAY_METHODS[0]
    if method == 'چک':
        need((b.get('cheque_no') or '').strip(), 'شماره چک را وارد کنید', 400)
        cd = need_jdate(b.get('cheque_date'), 'تاریخ سررسید چک', min_=pd, min_msg='سررسید چک نمی‌تواند قبل از تاریخ پرداخت باشد')
    else:
        cd = ''
    c.execute('INSERT INTO purchase_payments(code,purchase_id,amount,paid_at,method,cheque_no,cheque_date,sepidar_no,note,user_id,at) '
              'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (next_code(c, P['project_id'], 'PAY'), P['id'], amt, pd, method,
                                              (b.get('cheque_no') or '').strip(), cd,
                                              (b.get('sepidar_no') or '').strip(), (b.get('note') or '').strip(), u['id'], now()))
    tot = c.execute('SELECT SUM(amount) FROM purchase_payments WHERE purchase_id=?', (P['id'],)).fetchone()[0]
    c.execute('UPDATE purchases SET paid_amount=? WHERE id=?', (tot, P['id']))
    pflow(c, P['id'], u, 'finance_settle', 'payment', 'ثبت پرداخت (%s)' % method, '%s ریال' % format(amt, ','))
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/settle')
def api_purchase_settle(h, c, u, b, q, pid):
    """اعلام تسویه کامل؛ اگر تحویل هم انجام شده باشد، درخواست برای بایگانی به دبیرخانه می‌رود."""
    P = get_doc(c, u, 'purchase', int(pid))
    need(can_pay(c, u, P), 'تسویه برای این درخواست اکنون ممکن نیست')
    m = docs_check(c, P)
    need(m['ok'], 'تسویه کامل فقط وقتی ممکن است که درخواست کالا، اعلام وصول و فاکتور همه موجود باشند: ' +
         '؛ '.join(m['missing']), 400)
    c.execute('UPDATE purchases SET settled_at=?, settled_by=? WHERE id=?', (now(), u['id'], P['id']))
    if P['stage'] == 'finance_settle':
        move(c, u, P, 'archive', ('settled', 'تسویه کامل شد — ارسال به دبیرخانه برای بایگانی'), (b.get('note') or '').strip())
    else:
        pflow(c, P['id'], u, 'finance_settle', 'settle', 'اعلام تسویه کامل', (b.get('note') or '').strip())
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/edit')
def api_purchase_edit(h, c, u, b, q, pid):
    P = get_doc(c, u, 'purchase', int(pid))
    need(can_edit(u, P), 'ویرایش فقط وقتی ممکن است که درخواست در کارتابل شما باشد، و فقط تا مرحله مدیر پروژه')
    f, items = pur_fields(c, u, b, pid_fixed=P['project_id'], req_date=P['req_date'])
    write_fields(c, P['id'], f, items)
    note = (b.get('note') or '').strip()
    if P['stage'] == 'draft':  # پیش‌نویس هنوز ارسال نشده؛ نسخه جدید لازم نیست
        c.execute('DELETE FROM purchase_versions WHERE purchase_id=?', (P['id'],))
        save_version(c, P['id'], u, 'نسخه اصلی')
        return {'ok': True}
    c.execute('UPDATE purchases SET version=version+1 WHERE id=?', (P['id'],))
    save_version(c, P['id'], u, note)
    pflow(c, P['id'], u, P['stage'], 'edit', 'ویرایش — نسخه %d' % (P['version'] + 1), note)
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/resubmit')
def api_purchase_resubmit(h, c, u, b, q, pid):
    P = get_doc(c, u, 'purchase', int(pid))
    need(P['requester_id'] == u['id'] and P['status'] == 'returned', 'فقط درخواست‌کننده، پس از برگشت درخواست')
    f, items = pur_fields(c, u, b, pid_fixed=P['project_id'], req_date=P['req_date'])
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
    need(can_report(c, u), 'گزارش‌ها برای هیات مدیره، مدیر سیستم، مدیر پشتیبانی و مدیر مالی است')
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


# ---------- مدیریت: سربرگ‌ها و کلیشه‌های امضا
@route('GET', '/api/admin/letterheads')
def api_admin_letterheads(h, c, u, b, q):
    need(is_mgr(u))
    have = {r['scope']: r for r in rows(c.execute('SELECT scope, path, layout, updated_at FROM letterheads'))}
    items = [('hq', 'دفتر مرکزی'), ('site', 'پیش‌فرض همه کارگاه‌ها')] + \
        [('project:%d' % p['id'], 'پروژه ' + p['name']) for p in
         rows(c.execute("SELECT id, name FROM projects WHERE active=1 AND code!='HQ' ORDER BY id"))]
    out = []
    for sc, label in items:
        r = have.get(sc) or {}
        out.append({'scope': sc, 'label': label, 'has': bool(r.get('path')), 'updated_at': r.get('updated_at'),
                    'layout': json.loads(r.get('layout') or '{}') or LH_DEFAULT})
    sigs = rows(c.execute("SELECT id, full_name, title, sig_path != '' AND sig_path IS NOT NULL has_sig FROM users "
                          'WHERE active=1 ORDER BY id'))
    return {'letterheads': out, 'signatures': sigs, 'default_layout': LH_DEFAULT}


def lh_scope_ok(c, sc):
    need(sc in ('hq', 'site') or (re.match(r'^project:\d+$', sc) and c.execute(
        'SELECT 1 FROM projects WHERE id=?', (int(sc.split(':')[1]),)).fetchone()), 'سربرگ نامعتبر', 400)


@route('POST', r'/api/admin/letterheads/([\w:]+)/upload')
def api_admin_lh_upload(h, c, u, b, q, sc):
    need(is_mgr(u)); lh_scope_ok(c, sc)
    rel = save_image(h, LH_DIR, sc.replace(':', '-'))
    c.execute('INSERT INTO letterheads(scope,path,updated_at) VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET path=?, '
              'updated_at=?', (sc, rel, now(), rel, now()))
    log(c, 'letterhead', 0, u['id'], 'بارگذاری سربرگ', sc)
    return {'ok': True}


@route('POST', r'/api/admin/letterheads/([\w:]+)/layout')
def api_admin_lh_layout(h, c, u, b, q, sc):
    need(is_mgr(u)); lh_scope_ok(c, sc)
    lay = {}
    for k, v in LH_DEFAULT.items():
        if isinstance(v, dict):
            lay[k] = {kk: max(0.0, min(290.0, float(str((b.get(k) or {}).get(kk, vv)).translate(FA2EN) or vv)))
                      for kk, vv in v.items()}
        else:
            lay[k] = max(9.0, min(20.0, float(str(b.get(k, v)).translate(FA2EN) or v)))
    c.execute('INSERT INTO letterheads(scope,layout,updated_at) VALUES(?,?,?) ON CONFLICT(scope) DO UPDATE SET layout=?, '
              'updated_at=?', (sc, json.dumps(lay), now(), json.dumps(lay), now()))
    return {'ok': True}


@route('POST', r'/api/admin/letterheads/([\w:]+)/delete')
def api_admin_lh_delete(h, c, u, b, q, sc):
    need(is_mgr(u)); lh_scope_ok(c, sc)
    c.execute('DELETE FROM letterheads WHERE scope=?', (sc,))
    return {'ok': True}


@route('POST', r'/api/admin/users/(\d+)/sig')
def api_admin_sig_upload(h, c, u, b, q, uid):
    """کلیشه امضا فقط در مدیریت سامانه بارگذاری می‌شود؛ ولی امضای نامه فقط با حساب و رمز خودِ شخص ممکن است."""
    need(is_mgr(u))
    need(one(c.execute('SELECT 1 x FROM users WHERE id=?', (int(uid),))), 'کاربر پیدا نشد', 404)
    rel = save_image(h, SIG_DIR, 'user-%s' % uid)
    c.execute('UPDATE users SET sig_path=? WHERE id=?', (rel, int(uid)))
    log(c, 'user', int(uid), u['id'], 'بارگذاری کلیشه امضا')
    return {'ok': True}


@route('POST', r'/api/admin/users/(\d+)/sig/delete')
def api_admin_sig_delete(h, c, u, b, q, uid):
    need(is_mgr(u))
    c.execute("UPDATE users SET sig_path='' WHERE id=?", (int(uid),))
    log(c, 'user', int(uid), u['id'], 'حذف کلیشه امضا')
    return {'ok': True}


@route('GET', r'/api/admin/users/(\d+)/sig')
def api_admin_sig_view(h, c, u, b, q, uid):
    need(is_mgr(u))
    r = one(c.execute('SELECT sig_path FROM users WHERE id=?', (int(uid),)))
    need(r and r['sig_path'], 'کلیشه امضا ندارد', 404)
    return img_file(r['sig_path'])


# ---------- مدیریت
@route('GET', '/api/admin')
def api_admin(h, c, u, b, q):
    need(is_mgr(u))
    devs = {r[0]: r[1] for r in c.execute('SELECT user_id, COUNT(*) FROM notify_devices GROUP BY user_id')}
    users = rows(c.execute('SELECT id,username,full_name,title,role,active,must_change FROM users WHERE deleted=0 ORDER BY id'))
    for x in users:
        x['devices'] = devs.get(x['id'], 0)
    return {'users': users, 'projects': rows(c.execute('SELECT * FROM projects WHERE deleted=0 ORDER BY id')),
            'settings': settings(c), 'backups': sorted(os.listdir(BACK))[-10:], 'https': https_status(c),
            'is_admin': u['role'] == 'admin'}


@route('POST', '/api/admin/user')
def api_admin_user(h, c, u, b, q):
    need(is_mgr(u))
    need(b.get('role') in ROLES and (b.get('full_name') or '').strip() and (b.get('username') or '').strip(),
         'نام، نام کاربری و نقش الزامی است', 400)
    need(not c.execute('SELECT 1 FROM users WHERE username=? COLLATE NOCASE AND id!=?',
                       (b['username'].strip(), int(b.get('id') or 0))).fetchone(), 'این نام کاربری تکراری است', 400)
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
    c.execute('DELETE FROM notify_devices WHERE user_id=?', (int(b['id']),))
    return {'ok': True}


@route('POST', '/api/admin/project')
def api_admin_project(h, c, u, b, q):
    need(is_mgr(u))
    need((b.get('name') or '').strip(), 'نام پروژه الزامی است', 400)
    mid = int(b['manager_id']) if b.get('manager_id') else None
    if b.get('id'):
        c.execute('UPDATE projects SET name=?,manager_id=?,active=? WHERE id=?',
                  (b['name'].strip(), mid, 1 if b.get('active', True) else 0, int(b['id'])))
        sync_pm(c, int(b['id']), mid)
    else:
        cur = c.execute('INSERT INTO projects(name,code,manager_id) VALUES(?,?,?)', (b['name'].strip(), b.get('code') or '', mid))
        sync_pm(c, cur.lastrowid, mid)
    return {'ok': True}


@route('POST', '/api/admin/settings')
def api_admin_settings(h, c, u, b, q):
    need(is_mgr(u))
    for k in ('idle_minutes', 'notify_interval', 'https_port'):
        if k in b:
            need(str(b[k]).isdigit(), 'عدد نامعتبر در تنظیمات', 400)
            need(k != 'notify_interval' or int(b[k]) >= 15, 'فاصله بررسی اعلان حداقل ۱۵ ثانیه است', 400)
    for k in DEFAULT_SETTINGS:
        if k in b:
            c.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (k, str(b[k]).replace(',', '')
                      if k == 'ceo_threshold' else str(b[k])))
    return {'ok': True}


# ---------- اختیارات مدیر سیستم: حذف و اصلاح کاربر، پروژه و اسناد بایگانی
def need_admin(u):
    need(u['role'] == 'admin', 'این کار فقط توسط مدیر سیستم انجام می‌شود')


USER_REFS = [('letters', 'created_by'), ('letters', 'signer_id'), ('referrals', 'from_id'), ('referrals', 'to_id'),
             ('requests', 'requester_id'), ('steps', 'approver_id'), ('purchases', 'requester_id'),
             ('purchase_flow', 'user_id'), ('purchase_payments', 'user_id'), ('attachments', 'uploaded_by'), ('log', 'user_id')]


@route('POST', r'/api/admin/users/(\d+)/delete')
def api_admin_user_delete(h, c, u, b, q, uid):
    """حذف کاربر. اگر سابقه‌ای در اسناد دارد، حساب بسته و از فهرست‌ها حذف می‌شود ولی نامش در سوابق می‌ماند."""
    need_admin(u)
    uid = int(uid)
    need(uid != u['id'], 'حساب خودتان را نمی‌توانید حذف کنید', 400)
    r = one(c.execute('SELECT * FROM users WHERE id=? AND deleted=0', (uid,)))
    need(r, 'کاربر پیدا نشد', 404)
    busy = c.execute("SELECT COUNT(*) FROM purchases WHERE holder_id=? AND status IN ('open','returned')", (uid,)).fetchone()[0]
    busy += c.execute('SELECT COUNT(*) FROM referrals WHERE to_id=? AND status IN %s' % str(OPEN), (uid,)).fetchone()[0]
    busy += c.execute("SELECT COUNT(*) FROM steps WHERE approver_id=? AND status='pending'", (uid,)).fetchone()[0]
    need(not busy, fa_num('%s هنوز %d کار باز در کارتابل دارد؛ ابتدا سمت او را در پروژه‌ها به فرد دیگری بدهید یا کارهایش را '
         'ارجاع دهید، سپس حذف کنید' % (r['full_name'], busy)), 400)
    used = any(c.execute('SELECT 1 FROM %s WHERE %s=? LIMIT 1' % t, (uid,)).fetchone() for t in USER_REFS)
    for t in ('project_members', 'project_team', 'sessions', 'notify_devices'):
        c.execute('DELETE FROM %s WHERE user_id=?' % t, (uid,))
    c.execute('UPDATE projects SET manager_id=NULL WHERE manager_id=?', (uid,))
    for k in HQ_SETTING_USERS:
        c.execute("UPDATE settings SET value='' WHERE key=? AND value=?", (k, str(uid)))
    if used:
        c.execute("UPDATE users SET active=0, deleted=1, username=username||'~'||id WHERE id=?", (uid,))
    else:
        c.execute('DELETE FROM users WHERE id=?', (uid,))
    log(c, 'user', uid, u['id'], 'حذف کاربر', '%s (%s)' % (r['full_name'], r['username']))
    return {'ok': True, 'kept_history': used}


@route('POST', r'/api/admin/projects/(\d+)/delete')
def api_admin_project_delete(h, c, u, b, q, pid):
    """حذف پروژه. اگر سند دارد، از فهرست‌ها برداشته می‌شود و اسنادش با نام پروژه در بایگانی می‌ماند."""
    need_admin(u)
    pr = one(c.execute('SELECT * FROM projects WHERE id=? AND deleted=0', (int(pid),)))
    need(pr, 'پروژه پیدا نشد', 404)
    need(pr['code'] != 'HQ', 'دفتر مرکزی حذف نمی‌شود', 400)
    busy = c.execute("SELECT COUNT(*) FROM purchases WHERE project_id=? AND status IN ('open','returned')", (pr['id'],)).fetchone()[0]
    need(not busy, fa_num('این پروژه %d درخواست کالای در جریان دارد؛ ابتدا آن‌ها را لغو، تکمیل یا حذف کنید' % busy), 400)
    used = any(c.execute('SELECT 1 FROM %s WHERE project_id=? LIMIT 1' % t, (pr['id'],)).fetchone()
               for t in ('letters', 'requests', 'purchases'))
    for t in ('project_members', 'project_team'):
        c.execute('DELETE FROM %s WHERE project_id=?' % t, (pr['id'],))
    c.execute('DELETE FROM letterheads WHERE scope=?', ('project:%d' % pr['id'],))
    if used:
        c.execute('UPDATE projects SET active=0, deleted=1 WHERE id=?', (pr['id'],))
    else:
        c.execute('DELETE FROM projects WHERE id=?', (pr['id'],))
    log(c, 'project', pr['id'], u['id'], 'حذف پروژه', pr['name'])
    return {'ok': True, 'kept_history': used}


@route('POST', r'/api/admin/docs/(letter|purchase|request)/(\d+)/delete')
def api_admin_doc_delete(h, c, u, b, q, dt, did):
    """حذف کامل یک سند (نامه، درخواست کالا یا درخواست مالی) و گردش و پیوست‌هایش؛ فقط مدیر سیستم."""
    need_admin(u)
    did = int(did)
    tbl = {'letter': 'letters', 'purchase': 'purchases', 'request': 'requests'}[dt]
    D = one(c.execute('SELECT * FROM %s WHERE id=?' % tbl, (did,)))
    need(D, 'سند پیدا نشد', 404)
    c.execute('DELETE FROM referrals WHERE doc_type=? AND doc_id=?', (dt, did))
    c.execute('DELETE FROM attachments WHERE doc_type=? AND doc_id=?', (dt, did))  # فایل‌ها در پوشه data\files می‌مانند
    if dt == 'purchase':
        for t in ('purchase_items', 'purchase_flow', 'purchase_versions', 'purchase_payments'):
            c.execute('DELETE FROM %s WHERE purchase_id=?' % t, (did,))
        c.execute('DELETE FROM purchase_invoice_lines WHERE invoice_id IN (SELECT id FROM purchase_invoices WHERE purchase_id=?)', (did,))
        c.execute('DELETE FROM purchase_invoices WHERE purchase_id=?', (did,))
    if dt == 'request':
        c.execute('DELETE FROM steps WHERE request_id=?', (did,))
    c.execute('DELETE FROM %s WHERE id=?' % tbl, (did,))
    log(c, dt, did, u['id'], 'حذف سند توسط مدیر سیستم', '%s %s' % (D.get('number') or '', D.get('subject') or D.get('title') or ''))
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/archive_code')
def api_purchase_archive_code(h, c, u, b, q, pid):
    """اصلاح کد / محل بایگانی درخواست کالا (مدیر سیستم)."""
    need_admin(u)
    P = get_doc(c, u, 'purchase', int(pid))
    code = (b.get('archive_code') or '').strip()
    c.execute('UPDATE purchases SET archive_code=? WHERE id=?', (code, P['id']))
    pflow(c, P['id'], u, P['stage'], 'archive_code', 'اصلاح محل بایگانی توسط مدیر سیستم', code)
    return {'ok': True}


# ---------- اعلان‌ها روی موبایل و HTTPS
@route('POST', '/api/notify/register')
def api_notify_register(h, c, u, b, q):
    """این دستگاه برای اعلان ثبت می‌شود؛ با این شناسه حتی پس از خروج خودکار، فقط «تعداد کارهای جدید» دیده می‌شود."""
    need(settings(c).get('notify_enabled') == '1', 'اعلان‌ها توسط مدیر سیستم غیرفعال شده است', 400)
    old = (b.get('token') or '').strip()
    if old and c.execute('SELECT 1 FROM notify_devices WHERE token=? AND user_id=?', (old, u['id'])).fetchone():
        return {'token': old}
    tok = secrets.token_urlsafe(24)
    c.execute('INSERT INTO notify_devices(token,user_id,created_at,last_seen,agent) VALUES(?,?,?,?,?)',
              (tok, u['id'], now(), now(), (h.headers.get('User-Agent') or '')[:200]))
    return {'token': tok}


@route('POST', '/api/notify/unregister')
def api_notify_unregister(h, c, u, b, q):
    c.execute('DELETE FROM notify_devices WHERE token=? AND user_id=?', ((b.get('token') or ''), u['id']))
    return {'ok': True}


def notify_count(c, tok, u):
    """تعداد کارهای منتظر برای اعلان: با جلسه فعال، یا با شناسه دستگاه ثبت‌شده (فقط عدد؛ بدون جزئیات)."""
    if settings(c).get('notify_enabled') != '1':
        return {'n': 0, 'disabled': True}
    if not u and tok:
        r = one(c.execute('SELECT u.id,u.role FROM notify_devices d JOIN users u ON u.id=d.user_id '
                          'WHERE d.token=? AND u.active=1', (tok,)))
        need(r, 'این دستگاه برای اعلان ثبت نشده است', 401)
        c.execute('UPDATE notify_devices SET last_seen=? WHERE token=?', (now(), tok))
        u = r
    need(u, 'ابتدا وارد شوید', 401)
    return {'n': cart_count(c, u)}


@route('POST', '/api/admin/notify/revoke')
def api_admin_notify_revoke(h, c, u, b, q):
    need_admin(u)
    if b.get('user_id'):
        c.execute('DELETE FROM notify_devices WHERE user_id=?', (int(b['user_id']),))
    else:
        c.execute('DELETE FROM notify_devices')
    return {'ok': True}


def https_files():
    return os.path.join(HTTPS_DIR, 'cert.pem'), os.path.join(HTTPS_DIR, 'key.pem')


def https_status(c):
    cert, key = https_files()
    return {'cert': os.path.exists(cert), 'key': os.path.exists(key), 'port': int(settings(c).get('https_port') or 8443),
            'running': HTTPS_RUNNING[0]}


HTTPS_RUNNING = [0]  # پورت HTTPS در حال اجرا (۰ = خاموش)


@route('POST', r'/api/admin/https/(cert|key)')
def api_admin_https_upload(h, c, u, b, q, which):
    """بارگذاری گواهی (cert.pem) و کلید (key.pem) HTTPS؛ پس از راه‌اندازی مجدد سرور فعال می‌شود."""
    need_admin(u)
    raw = h.raw_body
    need(raw and b'-----BEGIN' in raw, 'فایل باید PEM باشد (با «-----BEGIN» شروع شود)', 400)
    os.makedirs(HTTPS_DIR, exist_ok=True)
    cert, key = https_files()
    with open(cert if which == 'cert' else key, 'wb') as f:
        f.write(raw)
    if os.path.exists(cert) and os.path.exists(key):
        try:
            ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(cert, key)
        except (ssl.SSLError, OSError) as e:
            raise ApiError('گواهی و کلید با هم نمی‌خوانند یا نامعتبرند: %s' % e)
    log(c, 'admin', 0, u['id'], 'بارگذاری ' + ('گواهی' if which == 'cert' else 'کلید') + ' HTTPS')
    return {'ok': True, 'status': https_status(c)}


@route('POST', '/api/admin/https/delete')
def api_admin_https_delete(h, c, u, b, q):
    need_admin(u)
    for p in https_files():
        if os.path.exists(p):
            os.remove(p)
    return {'ok': True}


def png_icon(size=192):
    """آیکن ساده برنامه (مربع آبی با قاب روشن) برای نصب روی صفحه اصلی گوشی."""
    bg, fg = (0x17, 0x34, 0x4d), (0xcf, 0xe3, 0xf3)
    m = size // 6
    raw = b''
    for y in range(size):
        row = bytearray(b'\x00')
        for x in range(size):
            edge = m <= x < size - m and m <= y < size - m and not (m + 8 <= x < size - m - 8 and m + 8 <= y < size - m - 8)
            row += bytes(fg if edge else bg)
        raw += bytes(row)
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


ICON = png_icon()
OLD_PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>به‌روزرسانی ناقص</title></head><body style="font-family:Tahoma,sans-serif;background:#f4f5f7;padding:20px;line-height:2.2">
<div style="max-width:640px;margin:8vh auto;background:#fff;border:2px solid #b42318;border-radius:10px;padding:18px 22px">
<h2 style="color:#b42318;margin-top:0">فایل index.html با برنامه سرور هم‌نسخه نیست</h2>
برنامه سرور (app.py) نسخه %s است، ولی فایل صفحه (index.html) کنار آن از نسخه دیگری است.<br>
۱. فایل <b>index.html</b> نسخه %s را از فایل ZIP در همان پوشه‌ای که app.py هست کپی و جایگزین کنید.<br>
۲. این صفحه را با <b>Ctrl+F5</b> دوباره باز کنید (نیازی به راه‌اندازی مجدد سرور نیست).</div></body></html>"""
SW_JS = """// سرویس‌ورکر اتوماسیون عمران زیست: نمایش اعلان و باز کردن کارتابل با لمس اعلان
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(ws => {
    for (const w of ws) { if ('focus' in w) { w.navigate && w.navigate('/#/cartable'); return w.focus(); } }
    return self.clients.openWindow('/#/cartable');
  }));
});
"""


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

    def user(self, c, path=''):
        """کاربر جلسه؛ پس از «idle_minutes» دقیقه بی‌فعالیتی جلسه باطل می‌شود و باید دوباره وارد شد."""
        ck = cookies.SimpleCookie(self.headers.get('Cookie') or '')
        self.token = ck['sid'].value if 'sid' in ck else ''
        self.expired = False
        if not self.token:
            return None
        r = one(c.execute('SELECT u.id,u.username,u.full_name,u.title,u.role,u.must_change,s.last_seen,s.created_at '
                          'FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND u.active=1', (self.token,)))
        if not r:
            return None
        last = r.pop('last_seen') or r.pop('created_at')
        r.pop('created_at', None)
        idle = int(settings(c).get('idle_minutes') or 0)
        try:
            gone = (datetime.datetime.now() - datetime.datetime.strptime(last[:19], '%Y-%m-%d %H:%M:%S')).total_seconds()
        except (TypeError, ValueError):
            gone = 0
        if idle and gone > idle * 60 + 30:  # ۳۰ ثانیه فرصت برای تأخیر شبکه
            c.execute('DELETE FROM sessions WHERE token=?', (self.token,)); c.commit()
            self.expired = True
            return None
        if path not in ('/api/counts', '/api/notify'):  # بررسی خودکار نشان و اعلان، جلسه را تمدید نمی‌کند
            c.execute('UPDATE sessions SET last_seen=? WHERE token=?', (now(), self.token)); c.commit()
        return r

    def do_GET(self):
        self.handle_req('GET')

    def do_POST(self):
        self.handle_req('POST')

    def handle_req(self, method):
        self.set_cookie = None
        url = urllib.parse.urlparse(self.path)
        path = url.path
        q = {k: v[0].translate(FA2EN).translate(AR2FA) for k, v in urllib.parse.parse_qs(url.query).items()}
        if method == 'GET' and path in ('/', '/index.html'):
            with open(os.path.join(BASE, 'index.html'), 'rb') as f:
                page = f.read()
            if ("PAGE_VERSION='%s'" % VERSION).encode() not in page:  # index.html با app.py هم‌نسخه نیست
                return self.send(200, OLD_PAGE % (VERSION, VERSION), 'text/html; charset=utf-8')
            return self.send(200, page, 'text/html; charset=utf-8')
        if method == 'GET' and path == '/logo.png':  # آرم شرکت (اختیاری): فایل logo.png کنار app.py
            lp = os.path.join(BASE, 'logo.png')
            if os.path.exists(lp):
                with open(lp, 'rb') as f:
                    return self.send(200, f.read(), 'image/png')
            return self.send(404, b'', 'image/png')
        if method == 'GET' and path == '/sw.js':  # سرویس‌ورکر اعلان‌ها (باید از ریشه سایت بیاید)
            return self.send(200, SW_JS, 'text/javascript; charset=utf-8', {'Service-Worker-Allowed': '/'})
        if method == 'GET' and path == '/icon.png':
            lp = os.path.join(BASE, 'logo.png')
            if os.path.exists(lp):
                with open(lp, 'rb') as f:
                    return self.send(200, f.read(), 'image/png')
            return self.send(200, ICON, 'image/png')
        if method == 'GET' and path == '/manifest.json':  # نصب روی صفحه اصلی گوشی
            return self.send(200, json.dumps({'name': 'اتوماسیون عمران زیست', 'short_name': 'اتوماسیون', 'start_url': '/#/cartable',
                                              'display': 'standalone', 'dir': 'rtl', 'lang': 'fa', 'background_color': '#f4f5f7',
                                              'theme_color': '#17344d', 'icons': [{'src': '/icon.png', 'sizes': '192x192',
                                                                                   'type': 'image/png'}]}, ensure_ascii=False),
                             'application/manifest+json; charset=utf-8')
        c = db()
        try:
            u = self.user(c, path)
            if path == '/api/notify' and method == 'GET':
                return self.send(200, json.dumps(notify_count(c, self.headers.get('X-Notify-Token') or q.get('t'), u)))
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
                        need(u, 'به دلیل %s دقیقه بی‌فعالیتی از سامانه خارج شدید؛ دوباره وارد شوید' % fa_num(
                            settings(c).get('idle_minutes')) if self.expired else 'ابتدا وارد شوید', 401)
                    res = fn(self, c, u, body, q, *mm.groups())
                    c.commit()
                    if isinstance(res, tuple) and res[0] == 'file':  # تصویر سربرگ یا امضا
                        return self.send(200, res[2], res[1], {'Cache-Control': 'private, no-store'})
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
    start_https()
    print('برای توقف، این پنجره را ببندید.')
    srv.serve_forever()


def start_https():
    """اگر مدیر سیستم گواهی HTTPS بارگذاری کرده باشد، سامانه روی پورت HTTPS هم اجرا می‌شود (برای اعلان روی گوشی)."""
    cert, key = https_files()
    if not (os.path.exists(cert) and os.path.exists(key)):
        return
    c = db(); port = int(settings(c).get('https_port') or 8443); c.close()
    try:
        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(cert, key)
        srv = ThreadingHTTPServer(('0.0.0.0', port), H)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    except (OSError, ssl.SSLError) as e:
        print('HTTPS راه‌اندازی نشد (%s)' % e)
        return
    HTTPS_RUNNING[0] = port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print('HTTPS هم روی پورت %d فعال است  —  آدرس برای گوشی: https://<نام یا IP این کامپیوتر>:%d' % (port, port))


if __name__ == '__main__':
    main()
