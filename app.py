# -*- coding: utf-8 -*-
"""
سامانه اتوماسیون اداری و مالی — شرکت عمران زیست
فقط با پایتون ۳.۹ به بالا اجرا می‌شود و به هیچ کتابخانه بیرونی یا اینترنت نیاز ندارد.
اجرا:  python app.py      سپس در مرورگر:  http://<IP سرور>:8080
"""
import os, sys, json, sqlite3, hashlib, secrets, re, shutil, threading, csv, io, time, ssl, base64
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
VERSION = '4.6'

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
BASE_TEAM_ROLES = list(TEAM_ROLES)
# بالادستِ مستقیم هر سمت در کارگاه (تأیید درخواست و مکاتبات به سمت بالا)
SUPERIOR = {'exec_eng': 'exec', 'tech_eng': 'tech', 'support': 'supervisor', 'warehouse': 'supervisor',
            'exec': 'supervisor', 'tech': 'supervisor', 'supervisor': 'pm'}
HQ_ROLES = {'support_manager': 'مدیر پشتیبانی دفتر مرکزی', 'finance_manager': 'مدیر مالی دفتر مرکزی',
            }  # بایگانی درخواست کالا از نسخه ۲.۴ خودکار است (نقش منشی بایگانی حذف شد)

# گردش درخواست کالا: مرحله ← {اقدام: (مرحله بعد، شرح)}
P_STAGES = {'draft': 'پیش‌نویس درخواست‌کننده', 'unit_approval': 'تأیید رئیس واحد',
            'supervisor_review': 'بررسی سرپرست کارگاه',  # فقط برای درخواست‌های نسخه‌های قبل
            'warehouse_check': 'استعلام موجودی از انبار کارگاه',
            'supervisor_approve': 'تأیید سرپرست کارگاه', 'tech_review': 'بررسی معاون فنی',
            'referred': 'ارجاع سرپرست کارگاه — کنترل یا تکمیل مدارک',
            'site_purchase': 'خرید در کارگاه — پشتیبانی کارگاه', 'pm_approve': 'بررسی مدیر پروژه',
            'hq_quotes': 'استعلام و پیش‌فاکتور — پشتیبانی دفتر مرکزی',
            'price_approve': 'تأیید قیمت و فروشنده — مدیر پروژه / هیات مدیره',
            'hq_purchase': 'خرید — پشتیبانی دفتر مرکزی', 'delivery': 'اعلام وصول — تحویل‌گیرنده و انباردار',
            'disc_review': 'تصمیم درباره مغایرت وصول — پشتیبانی',
            'finance_settle': 'کنترل مدارک — امور مالی', 'finance_pay': 'پرداخت — مالی دفتر مرکزی',
            'discrepancy': 'تصمیم درباره مغایرت — مدیر پروژه / هیات مدیره',
            'invoice_fix': 'تکمیل مدارک (فاکتور) — پشتیبانی',
            'archive': 'بایگانی — دبیرخانه', 'returned': 'برگشت به درخواست‌کننده', 'done': 'پایان و بایگانی خودکار'}
_RET = ('returned', 'برگشت به درخواست‌کننده برای اصلاح')
# مرحله بعد با None یعنی سرور بر اساس موجودی، کلاس خرید یا وضعیت تحویل و پرداخت تعیینش می‌کند
P_FLOW = {
    'draft': {'submit': (None, '')},
    # از نسخه ۳.۰ استعلام موجودی انبار حذف شد؛ مرحله بعد را سرور تعیین می‌کند (معاون فنی یا سرپرست کارگاه)
    # از نسخه ۴.۰ درخواست مستقیم به سرپرست کارگاه می‌رود؛ مراحل رئیس واحد، استعلام انبار و بررسی معاون فنی فقط برای
    # درخواست‌های قدیمی در جریان نگه داشته شده‌اند (هنگام ارتقا به تأیید سرپرست کارگاه منتقل می‌شوند)
    'unit_approval': {'approve': (None, 'تأیید رئیس واحد'), 'return': _RET},
    'supervisor_review': {'inquire': ('warehouse_check', 'ارسال استعلام به انبار کارگاه'), 'return': _RET},
    'warehouse_check': {'stock': (None, 'ثبت موجودی انبار')},
    'supervisor_approve': {'approve': (None, 'تأیید سرپرست کارگاه'), 'return': _RET,
                           'refer': (None, 'ارجاع برای کنترل یا تکمیل مدارک')},
    'referred': {'refer_done': ('supervisor_approve', 'کنترل یا تکمیل انجام شد — بازگشت به سرپرست کارگاه')},
    'tech_review': {'approve': (None, 'تأیید معاون فنی'), 'return': _RET},
    'site_purchase': {'purchased': ('delivery', 'خرید در کارگاه انجام شد — ارسال برای اعلام وصول')},
    'pm_approve': {'approve': ('hq_quotes', 'تأیید مدیر پروژه — ارسال به پشتیبانی برای استعلام قیمت'),
                   # منبع و قیمت معلوم است: بدون استعلام و تأیید قیمت، مستقیم به خرید
                   'direct': ('hq_purchase', 'دستور خرید مستقیم — ارسال به پشتیبانی برای خرید'),
                   'return': _RET},
    'hq_quotes': {'quoted': ('price_approve', 'استعلام و پیش‌فاکتورها آماده شد — ارسال برای تأیید قیمت')},
    'price_approve': {'approve': ('hq_purchase', 'تأیید قیمت و فروشنده — ارسال برای خرید'),
                      'requote': ('hq_quotes', 'برگشت برای استعلام مجدد')},
    # خرید دفتر مرکزی: همزمان به انبار (اعلام وصول) و امور مالی (پرداخت) می‌رود
    'hq_purchase': {'purchased': ('delivery', 'خرید انجام شد — ارسال برای اعلام وصول و پرداخت مالی')},
    # ۴.۳: انباردار و تحویل‌گیرنده هر کدام برای هر قلم یکی از پنج وضعیت (تأیید، کسری، اضافی، مرجوعی، تحویل بخشی) را اعلام می‌کنند
    # ۴.۵: یک برگه اعلام وصول برای هر درخواست؛ اقلام دارای مغایرت پس از تأیید تحویل‌گیرنده برای تصمیم به پشتیبانی
    # می‌روند و پس از تصمیم، انباردار همان برگه را اصلاح (تأیید) می‌کند
    'delivery': {'wh_fix': (None, 'اصلاح برگه اعلام وصول طبق تصمیم پشتیبانی (انباردار)'),
                 'wh_ok': (None, 'ثبت انباردار'), 'recv_ok': (None, 'تأیید تحویل‌گیرنده'),
                 'followup': (None, 'ارجاع به پشتیبانی برای پیگیری تحویل')},
    'disc_review': {'decide': ('delivery', 'تصمیم پشتیبانی درباره مغایرت — بازگشت به انبار برای اصلاح برگه اعلام وصول'),
                    'wh_fix': (None, 'اصلاح برگه اعلام وصول طبق تصمیم پشتیبانی (انباردار)')},
    'finance_settle': {'docs_ok': ('done', 'مدارک کامل است (درخواست، اعلام وصول، فاکتور) — پایان و بایگانی خودکار'),
                       'need_docs': ('invoice_fix', 'برگشت به پشتیبانی برای بارگذاری فاکتور'),
                       'to_disc': ('discrepancy', 'مغایرت — ارسال برای تصمیم')},  # to_disc فقط برای درخواست‌های نسخه ۲.۱
    'discrepancy': {'accept': ('finance_settle', 'پذیرش مغایرت و ادامه'),
                    'fix': ('invoice_fix', 'برگشت به پشتیبانی برای اصلاح فاکتور یا خرید')},
    'invoice_fix': {'fixed': ('finance_settle', 'فاکتور بارگذاری شد — ارسال به مالی')},

}
# مدارکی که از مرحله استعلام قیمت به بعد پیوست می‌شوند و قیمت‌ها، برای کارکنان کارگاه نمایش داده نمی‌شوند
HQ_ONLY_STAGES = ('hq_quotes', 'price_approve', 'hq_purchase', 'finance_settle', 'finance_pay', 'archive',
                  'discrepancy', 'invoice_fix')
# کدگذاری اسناد: {نوع سند}-{سریال ۴ رقمی}، مثل MR-0012 (بدون کد پروژه؛ سریال سراسری برای هر نوع سند)
DOC_TYPES = {'MR': 'درخواست کالا', 'PO': 'سفارش خرید', 'GRN': 'اعلام وصول کالا', 'PAY': 'پرداخت'}
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
                'finance_settle': ('setting', 'finance_manager'),
                'discrepancy': ('member', 'pm'),
                }
UNIT_HEAD = {'tech': 'tech', 'exec': 'exec', 'support': 'supervisor', 'warehouse': 'supervisor'}  # رئیس هر واحد
HEAD_ROLES = ('tech', 'exec', 'supervisor')  # درخواست این افراد، خودش تأیید رئیس واحد است
# تا پیش از رسیدن به مدیر پروژه ویرایش و لغو ممکن است؛ مدیر پروژه فقط لغو (ابطال) می‌کند
# هر کس فقط تا وقتی درخواست در کارتابل خودش است ویرایش می‌کند؛ تا مدیر پروژه (خودش هم)
EDIT_STAGES = ('draft', 'unit_approval', 'supervisor_review', 'warehouse_check', 'tech_review', 'supervisor_approve',
               'pm_approve', 'returned')
CANCEL_STAGES = EDIT_STAGES
# دسته کالا تعیین‌کننده مسیر است: عمومی و مصرفی در کارگاه با پشتیبانی کارگاه؛ اصلی با روال کامل دفتر مرکزی
CATEGORIES = [('main', 'خرید از دفتر مرکزی'), ('general', 'خرید در کارگاه')]  # عنوان‌ها از نسخه ۳.۰
URGENCIES = [('normal', 'عادی'), ('emergency', 'اضطراری')]
# رویدادهای جانبی گردش که درخواست را جابه‌جا نمی‌کنند (پیوست، ویرایش، پرداخت، اصلاح اعلام وصول، فاکتور رسمی)
SIDE_ACTIONS = ('attach', 'edit', 'delatt', 'pay_done', 'grn_edit', 'official_inv', 'receipt_partial', 'receipt_full')
INV_KINDS = ('فاکتور', 'پیش‌فاکتور')
ATT_KINDS = ['پیش‌فاکتور', 'فاکتور', 'عکس وصول', 'مشخصات فنی', 'نقشه / متره', 'رسید', 'صورت‌جلسه', 'عکس', 'سایر']
DEFAULT_CANCEL_REASONS = 'نیاز نیست\nبودجه تأمین نیست\nتکراری است\nزمان‌بندی اجازه نمی‌دهد\nسایر'
P_STATUS = {'open': 'در جریان', 'returned': 'برگشت برای اصلاح', 'delivered': 'تحویل کامل از موجودی انبار',
            'closed': 'پایان یافت و بایگانی شد', 'site_done': 'تحویل شد — پایان کار کارگاه', 'rejected': 'رد شد', 'cancelled': 'لغو شد (بایگانی)'}
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
CREATE TABLE IF NOT EXISTS purchase_receipts(
  id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL, seq INTEGER NOT NULL, code TEXT DEFAULT '',
  status TEXT DEFAULT 'open',
  wh_by INTEGER, wh_at TEXT, recv_by INTEGER, recv_at TEXT,
  delivery_date TEXT DEFAULT '', delivery_ref TEXT DEFAULT '', note TEXT DEFAULT '', closed_at TEXT);
CREATE INDEX IF NOT EXISTS ix_pur_rcpt ON purchase_receipts(purchase_id);
CREATE TABLE IF NOT EXISTS purchase_receipt_lines(
  id INTEGER PRIMARY KEY, receipt_id INTEGER NOT NULL, item_id INTEGER NOT NULL,
  wh_qty TEXT DEFAULT '', recv_qty TEXT DEFAULT '', disc_note TEXT DEFAULT '', diff_note TEXT DEFAULT '',
  wh_status TEXT DEFAULT '', wh_note TEXT DEFAULT '', recv_status TEXT DEFAULT '', recv_note TEXT DEFAULT '', exp_qty TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_pur_rcpt_ln ON purchase_receipt_lines(receipt_id);
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

DEFAULT_SETTINGS = {'company': 'عمران زیست', 'ceo_threshold': '1000000000',
                    'ceo_user': '', 'office_approver': '', 'warehouse_user': '', 'default_due_days': '3',
                    'support_manager': '', 'finance_manager': '',
                    'cancel_reasons': DEFAULT_CANCEL_REASONS,
                    # خروج خودکار پس از چند دقیقه بی‌فعالیتی؛ اعلان‌ها روی موبایل و پورت HTTPS
                    'idle_minutes': '15', 'notify_enabled': '1', 'notify_interval': '30', 'https_port': '8443'}

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


def apply_custom_roles(c):
    """سمت‌هایی که مدیر سیستم تعریف کرده (نسخه ۲.۸): مثل مهندسان، چند نفر در هر سمت، با بالادست و واحد.
    در settings با کلید custom_roles به‌صورت [[کلید، عنوان، سمتِ بالادست، واحد], ...] نگه داشته می‌شوند."""
    try:
        roles = json.loads(settings(c).get('custom_roles') or '[]')
    except ValueError:
        roles = []
    for k, _, _ in TEAM_ROLES[len(BASE_TEAM_ROLES):]:
        SUPERIOR.pop(k, None); ROLE_UNIT.pop(k, None)
    TEAM_ROLES[:] = BASE_TEAM_ROLES + [(r[0], r[1], r[2]) for r in roles]
    for k, _, sup, unit in roles:
        SUPERIOR[k] = sup; ROLE_UNIT[k] = unit


def team_unit_sql():
    """واحد هر سمت زیرمجموعه (برای اینکه هر کس فقط درخواست‌های واحد خودش را ببیند)."""
    return 'CASE t.role_key %s ELSE NULL END' % ' '.join("WHEN '%s' THEN '%s'" % (k, ROLE_UNIT.get(k, ''))
                                                          for k, _, _ in TEAM_ROLES)


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
    # ۴.۶: نام شرکت در همه جا «عمران زیست»
    c.execute("UPDATE settings SET value='عمران زیست' WHERE key='company' AND value IN "
              "('شرکت گسترش فناوری عمران زیست','گسترش فناوری عمران زیست','شرکت گسترش فناوري عمران زيست','گسترش فناوري عمران زيست')")
    apply_custom_roles(c)
    if not settings(c).get('fix_v32'):  # ۳.۲: رونوشت‌های باز درخواست کالا که به پشتیبانی رفته بود، بسته می‌شوند
        sup = support_users(c)
        if sup:
            c.execute("UPDATE referrals SET status='closed', done_at=?, reply='رونوشت به پشتیبانی لازم نیست (نسخه ۳.۲)' "
                      "WHERE doc_type='purchase' AND action='جهت اطلاع' AND status IN ('new','seen','doing') AND to_id IN (%s)"
                      % ','.join('?' * len(sup)), (now(),) + tuple(sup))
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('fix_v32','1')")
    migrate(c)
    migrate_v13(c)
    migrate_v15(c)
    migrate_v16(c)
    migrate_v24(c)
    purge_v35(c)
    migrate_v33(c)
    if not settings(c).get('fix_v39'):  # ۳.۹: مدیر سیستم از همه نقش‌ها و ارجاع‌های گردش کار کنار گذاشته می‌شود
        adm = [r[0] for r in c.execute("SELECT id FROM users WHERE role='admin'")]
        if adm:
            qs = ','.join('?' * len(adm))
            c.execute("UPDATE referrals SET status='closed', done_at=?, reply='مدیر سیستم در گردش کار نقشی ندارد (نسخه ۳.۹)' "
                      "WHERE to_id IN (%s) AND status IN ('new','seen','doing')" % qs, [now()] + adm)
            c.execute('DELETE FROM project_members WHERE user_id IN (%s)' % qs, adm)
            c.execute('DELETE FROM project_team WHERE user_id IN (%s)' % qs, adm)
            c.execute('UPDATE projects SET manager_id=NULL WHERE manager_id IN (%s)' % qs, adm)
            for k in HQ_SETTING_USERS:
                if (settings(c).get(k) or '') in [str(a) for a in adm]:
                    c.execute("UPDATE settings SET value='' WHERE key=?", (k,))
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('fix_v39','1')")
    if not settings(c).get('mig_v40'):  # ۴.۰: درخواست‌های در جریان در مراحل حذف‌شده به تأیید سرپرست کارگاه می‌روند
        for P in rows(c.execute("SELECT * FROM purchases WHERE status='open' AND stage IN "
                                "('unit_approval','supervisor_review','warehouse_check','tech_review')")):
            sup = pmembers(c, P['project_id']).get('supervisor')
            if sup:
                c.execute("UPDATE purchases SET stage='supervisor_approve', holder_id=? WHERE id=?", (sup, P['id']))
                c.execute('INSERT INTO purchase_flow(purchase_id,stage,action,label,user_id,note,at) VALUES(?,?,?,?,?,?,?)',
                          (P['id'], P['stage'], 'approve', 'انتقال به تأیید سرپرست کارگاه (کوتاه‌شدن مسیر گردش، نسخه ۴.۰)',
                           None, '', now()))
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('mig_v40','1')")
    if not settings(c).get('mig_v37'):  # ۳.۷: نوبتی که انباردار ثبت کرده، «منتظر تحویل‌گیرنده» است
        c.execute("UPDATE purchase_receipts SET status='pending' WHERE status='open' AND wh_at IS NOT NULL")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('mig_v37','1')")
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
                          ('disc_ok_by', 'INTEGER'), ('disc_ok_at', 'TEXT'), ('disc_note', "TEXT DEFAULT ''"),
                          # ۲.۴: پیش‌فاکتور منتخب و کنترل مدارک مالی (فاکتور رسمی، مدارک ارزش افزوده)
                          ('chosen_att', 'INTEGER'), ('official_inv', 'INTEGER'), ('vat_docs', 'INTEGER'),
                          ('docs_by', 'INTEGER'), ('docs_at', 'TEXT'),
                          # ۲.۶: ارسال همزمان به مالی برای پرداخت پس از خرید
                          ('pay_req_at', 'TEXT'), ('pay_done_at', 'TEXT'), ('pay_done_by', 'INTEGER'),
                          ('pay_note', "TEXT DEFAULT ''"),
                          # ۳.۶: پشتیبانی‌ای که کار به او واگذار شده (کارگاه یا دفتر مرکزی) در مراحل بعدی هم کار را دارد
                          ('support_by', 'INTEGER'), ('support_side', "TEXT DEFAULT ''")],
            'purchase_payments': [('code', "TEXT DEFAULT ''")],
            'purchase_receipt_lines': [('wh_status', "TEXT DEFAULT ''"), ('wh_note', "TEXT DEFAULT ''"),
                                       ('recv_status', "TEXT DEFAULT ''"), ('recv_note', "TEXT DEFAULT ''"),
                                       ('exp_qty', "TEXT DEFAULT ''"),  # ۴.۳: وضعیت پنج‌گانه هر قلم
                                       # ۴.۵: مقدار پذیرفته‌شده، تصمیم پشتیبانی درباره مغایرت و اصلاح انباردار
                                       ('acc_qty', "TEXT DEFAULT ''"), ('need_dec', 'INTEGER DEFAULT 0'),
                                       ('sup_dec', "TEXT DEFAULT ''"), ('sup_note', "TEXT DEFAULT ''"),
                                       ('sup_by', 'INTEGER'), ('sup_at', 'TEXT'),
                                       ('fix_by', 'INTEGER'), ('fix_at', 'TEXT')],
            'purchase_items': [('stock_qty', "TEXT DEFAULT ''"), ('bought_qty', "TEXT DEFAULT ''"),
                               ('bought_unit', "TEXT DEFAULT ''"), ('bought_status', "TEXT DEFAULT ''"),
                               ('bought_note', "TEXT DEFAULT ''"), ('recv_qty', "TEXT DEFAULT ''"),
                               ('disc_note', "TEXT DEFAULT ''"),
                               ('wh_qty', "TEXT DEFAULT ''")],  # ۳.۵: جمع مقدار شمرده‌شده انبار در نوبت‌های بسته  # ۳.۰: مغایرت هر قلم در اعلام وصول
            'attachments': [('kind', "TEXT DEFAULT ''"), ('deleted_at', 'TEXT'), ('deleted_by', 'INTEGER'),
                            ('flow_id', 'INTEGER'), ('hq_only', 'INTEGER DEFAULT 0'), ('archived', 'INTEGER DEFAULT 0'),
                            ('receipt_id', 'INTEGER')],  # ۳.۵: عکس وصول هر نوبت اعلام وصول
            'letters': [('body', "TEXT DEFAULT ''"), ('signer_id', 'INTEGER'), ('signed_at', 'TEXT'),
                        ('sig_name', "TEXT DEFAULT ''"), ('sig_title', "TEXT DEFAULT ''"), ('sig_file', "TEXT DEFAULT ''"),
                        ('registered_at', 'TEXT'), ('main_to_id', 'INTEGER'), ('cc_text', "TEXT DEFAULT ''"),
                        ('cc_ids', "TEXT DEFAULT ''"), ('main_action', "TEXT DEFAULT ''"), ('dispatched', 'INTEGER DEFAULT 1')],
            'users': [('sig_path', "TEXT DEFAULT ''"), ('deleted', 'INTEGER DEFAULT 0'),
                      ('failed_logins', 'INTEGER DEFAULT 0'), ('locked_at', 'TEXT')],  # ۳.۴: قفل پس از ۵ ورود ناموفق
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
    for key, un in (('support_manager', 'support'), ('finance_manager', 'finance')):
        if un in ids:
            c.execute("UPDATE settings SET value=? WHERE key=? AND value=''", (str(ids[un]), key))
    for name in SEED_PROJECTS:
        if c.execute('SELECT 1 FROM projects WHERE name=?', (name,)).fetchone():
            continue
        mid = ids.get('kasaeian') if name == 'موادکاران' else None
        pid = c.execute('INSERT INTO projects(name,code,manager_id) VALUES(?,?,?)', (name, '', mid)).lastrowid
        sync_pm(c, pid, mid)
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('seed_v12','1')")


def migrate_v33(c):
    """CR-PUR-02: نوبت‌های اعلام وصول برای درخواست‌های موجود (یک بار)."""
    if settings(c).get('mig_v33'):
        return
    for P in rows(c.execute("SELECT * FROM purchases")):
        if c.execute('SELECT 1 FROM purchase_receipts WHERE purchase_id=?', (P['id'],)).fetchone():
            continue
        its = rows(c.execute("SELECT * FROM purchase_items WHERE purchase_id=? AND bought_status IN ('bought','partial')",
                             (P['id'],)))
        if P['grn_no'] and P['recv_at'] and P['wh_at']:  # اعلام وصول صادرشده: یک نوبت بسته
            rid = c.execute("INSERT INTO purchase_receipts(purchase_id,seq,code,status,wh_by,wh_at,recv_by,recv_at,note,closed_at) "
                            "VALUES(?,1,?,'closed',?,?,?,?,?,?)",
                            (P['id'], P['grn_no'], P['wh_by'], P['wh_at'], P['recv_by'], P['recv_at'],
                             'مهاجرت از نسخه ۳.۲؛ مقدار انبار و تحویل‌گیرنده جدا ثبت نشده بود',
                             max(P['wh_at'], P['recv_at']))).lastrowid
            for it in its:
                c.execute('INSERT INTO purchase_receipt_lines(receipt_id,item_id,wh_qty,recv_qty) VALUES(?,?,?,?)',
                          (rid, it['id'], it['recv_qty'] or '', it['recv_qty'] or ''))
                c.execute('UPDATE purchase_items SET wh_qty=recv_qty WHERE id=?', (it['id'],))
        elif P['stage'] == 'delivery' and P['status'] == 'open' and P['wh_at'] and not P['recv_at']:  # انبار تأیید کرده
            rid = c.execute("INSERT INTO purchase_receipts(purchase_id,seq,status,wh_by,wh_at) VALUES(?,1,'pending',?,?)",
                            (P['id'], P['wh_by'], P['wh_at'])).lastrowid
            for it in its:
                c.execute('INSERT INTO purchase_receipt_lines(receipt_id,item_id,wh_qty,recv_qty,disc_note) VALUES(?,?,?,?,?)',
                          (rid, it['id'], it['recv_qty'] or '', '', it['disc_note'] or ''))
                c.execute("UPDATE purchase_items SET recv_qty='' WHERE id=?", (it['id'],))
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('mig_v33','1')")


PURGE_TABLES = ('purchase_receipt_lines', 'purchase_receipts', 'purchase_invoice_lines', 'purchase_invoices',
                'purchase_payments', 'purchase_versions', 'purchase_flow', 'purchase_items', 'purchases', 'letters')


def workflow_counts(c):
    return {'letters': c.execute('SELECT COUNT(*) FROM letters').fetchone()[0],
            'purchases': c.execute('SELECT COUNT(*) FROM purchases').fetchone()[0]}


def purge_workflow(c, tag):
    """پاک کردن همه مکاتبات و درخواست‌های کالا (با گردش، اقلام، نسخه‌ها، پیوست‌ها، ارجاع‌ها و اعلام وصول‌ها).
    کاربران، پروژه‌ها و ارکان، سمت‌ها، تنظیمات، سربرگ‌ها و کلیشه‌های امضا و درخواست‌های مالی دست نمی‌خورند.
    پیش از پاک کردن، نسخه کامل پایگاه داده و فایل‌های پیوستِ پاک‌شده در data\\backups\\<tag>-<تاریخ> نگه داشته می‌شوند.
    خروجی: تعداد پاک‌شده‌ها و محل پشتیبان."""
    cnt = workflow_counts(c)
    keep = ''
    if cnt['letters'] + cnt['purchases']:
        keep = os.path.join(BACK, '%s-%s' % (tag, datetime.datetime.now().strftime('%Y%m%d-%H%M%S')))
        os.makedirs(keep, exist_ok=True)
        c.commit()
        bk = sqlite3.connect(os.path.join(keep, 'oa.db'))  # پشتیبان کامل پایگاه داده پیش از پاک کردن
        c.backup(bk); bk.close()
        for a in rows(c.execute("SELECT path FROM attachments WHERE doc_type IN ('letter','purchase')")):
            src = os.path.join(FILES, a['path'])
            if a['path'] and os.path.isfile(src):
                dst = os.path.join(keep, 'files', a['path'])
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
        for sub in (PUR_DIR, SIG_COPY_DIR):  # پوشه پیوست‌های درخواست کالا و کلیشه‌های امضای نامه‌ها
            src = os.path.join(FILES, sub)
            if os.path.isdir(src):
                os.makedirs(os.path.join(keep, 'files'), exist_ok=True)
                shutil.move(src, os.path.join(keep, 'files', sub))
        c.execute("DELETE FROM referrals WHERE doc_type IN ('letter','purchase')")
        c.execute("DELETE FROM attachments WHERE doc_type IN ('letter','purchase')")
        c.execute("DELETE FROM log WHERE doc_type IN ('letter','purchase')")
        for t in PURGE_TABLES:
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
                c.execute('DELETE FROM %s' % t)
        c.execute("DELETE FROM doc_serials WHERE dtype IN ('MR','PO','GRN','PAY')")  # شماره‌ها از نو (MR-0001)
    return cnt, keep


def purge_v35(c):
    """نسخه ۳.۵ (درخواست کاربر): پاک کردن یک‌باره سوابق، هنگام اولین اجرا."""
    if settings(c).get('purge_v35'):
        return
    cnt, keep = purge_workflow(c, 'pre-v35-purge')
    if keep:
        log(c, 'admin', 0, None, 'پاک کردن سوابق مکاتبات و درخواست‌های کالا (نسخه ۳.۵)', 'پشتیبان: ' + keep)
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('purge_v35','1')")


def migrate_v24(c):
    """نسخه ۲.۴: خروج خودکار ۱۵ دقیقه؛ بایگانی خودکار؛ مالی فقط کنترل مدارک (پرداخت و تسویه حذف شد)."""
    if settings(c).get('seed_v24'):
        return
    c.execute("UPDATE settings SET value='15' WHERE key='idle_minutes' AND value='5'")
    c.execute("UPDATE purchases SET stage='done', status='closed', holder_id=NULL, closed_at=COALESCE(closed_at,?) "
              "WHERE stage='archive' AND status='open'", (now(),))
    fin = int(settings(c).get('finance_manager') or 0) or None
    c.execute("UPDATE purchases SET stage='finance_settle', holder_id=? WHERE stage='finance_pay' AND status='open'", (fin,))
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('seed_v24','1')")


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


def is_board(u):
    """هیات مدیره. مدیر سیستم در هیچ گردش کاری نقش ندارد (نسخه ۳.۹) و فقط وظایف ذاتی خودش را دارد:
    کاربران و رمزها، پروژه‌ها و ارکان، سمت‌ها، تنظیمات، سربرگ و امضا، HTTPS و اعلان‌ها، حذف و اصلاح اسناد."""
    return u['role'] == 'manager'


def is_admin_id(c, uid):
    return bool(uid) and c.execute("SELECT 1 FROM users WHERE id=? AND role='admin'", (int(uid),)).fetchone() is not None


def need_not_admin(c, uid, what):
    need(not is_admin_id(c, uid), 'مدیر سیستم در گردش کار نقشی ندارد؛ برای «%s» کاربر دیگری انتخاب کنید' % what, 400)


def sees_all(u):
    return u['role'] in ('admin', 'manager', 'secretariat')


def can_report(c, u):
    """گزارش‌ها و همه درخواست‌ها و مکاتبات: هیات مدیره، مدیر سیستم، مدیر پشتیبانی و مدیر مالی دفتر مرکزی."""
    S = settings(c)
    return is_mgr(u) or str(u['id']) in (S.get('support_manager'), S.get('finance_manager'))


def is_broad(c, u):
    """کسانی که همه درخواست‌های کالای همه پروژه‌ها را می‌بینند."""
    return can_report(c, u) or u['role'] in ('finance', 'secretariat')


HQ_SETTING_USERS = ('support_manager', 'finance_manager', 'ceo_user', 'office_approver', 'warehouse_user')


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
    for t in to_ids:  # هیچ سند و ارجاعی به مدیر سیستم نمی‌رود
        need_not_admin(c, t, 'گیرنده ارجاع')
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


# محدودیت ورود ناموفق: حساب پس از ۵ ورود ناموفق پیاپی بسته می‌شود و فقط مدیر سیستم با رمز جدید بازش می‌کند (۳.۴)؛
# از یک نشانی IP هم ۲۰ ورود ناموفق ورود را ۱۵ دقیقه می‌بندد (حدس نام‌های کاربری مختلف)
LOGIN_FAILS, LOGIN_LOCK = {}, threading.Lock()
LOGIN_MAX_USER, LOGIN_MAX_IP, LOGIN_WINDOW = 5, 20, 15 * 60
LOCKED_MSG = 'حساب شما به دلیل %s ورود ناموفق بسته شده است. برای رمز جدید به مدیر سیستم مراجعه کنید.' % '۵'


def login_blocked(keys):
    """اگر یکی از کلیدها بیش از حد مجاز ناموفق بوده، چند ثانیه تا آزاد شدن مانده است."""
    t = time.time()
    with LOGIN_LOCK:
        wait = 0
        for k, lim in keys:
            fl = [x for x in LOGIN_FAILS.get(k, []) if t - x < LOGIN_WINDOW]
            LOGIN_FAILS[k] = fl
            if len(fl) >= lim:
                wait = max(wait, int(LOGIN_WINDOW - (t - fl[0])) + 1)
        return wait


def login_failed(keys):
    with LOGIN_LOCK:
        for k, _ in keys:
            LOGIN_FAILS.setdefault(k, []).append(time.time())


@route('POST', '/api/login')
def api_login(h, c, u, b, q):
    # نام کاربری به حروف کوچک و بزرگ حساس نیست
    un = (b.get('username') or '').strip()
    keys = [('ip:' + h.client_address[0], LOGIN_MAX_IP)]
    wait = login_blocked(keys)
    if wait:
        raise ApiError('به دلیل ورودهای ناموفق پیاپی از این دستگاه، ورود تا %s دقیقه دیگر ممکن نیست'
                       % fa_num(str((wait + 59) // 60)), 429)
    r = one(c.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1', (un,)))
    if r and r['locked_at']:
        raise ApiError(LOCKED_MSG, 423)
    if not r or not pw_ok(b.get('password') or '', r):
        login_failed(keys)
        log(c, 'user', r['id'] if r else 0, None, 'ورود ناموفق', '%s از %s' % (un[:50], h.client_address[0]))
        if r:
            n = (r['failed_logins'] or 0) + 1
            c.execute('UPDATE users SET failed_logins=?, locked_at=? WHERE id=?',
                      (n, now() if n >= LOGIN_MAX_USER else None, r['id']))
            if n >= LOGIN_MAX_USER:  # بسته شد؛ در «در دست اقدام» مدیر سیستم می‌آید
                c.execute('DELETE FROM sessions WHERE user_id=?', (r['id'],))
                log(c, 'user', r['id'], None, 'حساب بسته شد', '%d ورود ناموفق — ارجاع به مدیر سیستم' % n)
        c.commit()  # خطا تراکنش را برمی‌گرداند؛ شمارش و قفل باید بماند
        if r and n >= LOGIN_MAX_USER:
            raise ApiError(LOCKED_MSG, 423)
        left = LOGIN_MAX_USER - n if r else 0
        raise ApiError('نام کاربری یا رمز عبور نادرست است' + (' (%s بار دیگر تا بسته شدن حساب)' % fa_num(str(left))
                                                             if r and left <= 2 else ''), 401)
    c.execute('UPDATE users SET failed_logins=0 WHERE id=?', (r['id'],))
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
            'wf_users': rows(c.execute("SELECT id,full_name,title,role FROM users WHERE active=1 AND role!='admin' ORDER BY id")),
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
    if u['role'] == 'finance' or is_board(u):
        pay = rows(c.execute(
            "SELECT q.*, p.name project, ru.full_name requester FROM requests q LEFT JOIN projects p ON p.id=q.project_id "
            "LEFT JOIN users ru ON ru.id=q.requester_id WHERE q.status='approved' OR (q.status='paid' AND q.sepidar_no='') "
            "ORDER BY q.id"))
    desk = []
    if u['role'] == 'secretariat' or is_board(u):
        # اول: صادره‌های امضاشده (یا بی‌امضاکننده) که منتظر ثبت و شماره دبیرخانه‌اند
        desk = rows(c.execute(
            "SELECT l.*, 1 to_register FROM letters l WHERE l.status='draft' AND (l.signer_id IS NULL OR "
            "l.signed_at IS NOT NULL) ORDER BY l.id"))
        desk += rows(c.execute(
            "SELECT l.* FROM letters l WHERE l.status='open' AND NOT EXISTS (SELECT 1 FROM referrals r WHERE "
            "r.doc_type='letter' AND r.doc_id=l.id AND r.status IN %s) ORDER BY l.id DESC LIMIT 200" % str(OPEN)))
    pur_held = rows(c.execute(PUR_SEL + "WHERE " + PUR_HELD_SQL + " ORDER BY x.id", held_args(u)))
    pur_pay = rows(c.execute(PUR_SEL + "WHERE " + PUR_PAY_SQL + " ORDER BY x.id")) if is_finance(c, u) else []
    pur_mine = rows(c.execute(PUR_SEL + "WHERE x.requester_id=? AND x.status IN ('open','returned') ORDER BY x.id DESC",
                              (u['id'],)))
    locked = rows(c.execute('SELECT id, username, full_name, locked_at FROM users WHERE locked_at IS NOT NULL AND deleted=0 '
                            'ORDER BY locked_at')) if u['role'] == 'admin' else []
    return {'locked': locked, 'inbox': inbox, 'sent': sent, 'approvals': approvals, 'mine': mine, 'pay': pay, 'desk': desk,
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
    n += c.execute("SELECT COUNT(*) FROM purchases x WHERE " + PUR_HELD_SQL, held_args(u)).fetchone()[0]
    if u['role'] == 'admin':  # حساب‌های بسته‌شده پس از ورود ناموفق، منتظر رمز جدید مدیر سیستم
        n += c.execute('SELECT COUNT(*) FROM users WHERE locked_at IS NOT NULL AND deleted=0').fetchone()[0]
    if u['role'] == 'finance' or str(u['id']) == settings(c).get('finance_manager'):
        n += c.execute("SELECT COUNT(*) FROM purchases x WHERE " + PUR_PAYWAIT_SQL).fetchone()[0]
    return n


# درخواست‌های کالای در کارتابل: دارنده (جز درخواست‌کننده‌ای که تحویل را تأیید کرده) و انباردارِ منتظر تأیید تحویل
# مرحله‌های مدیر پروژه / هیات مدیره در کارتابل همه اعضای هیات مدیره و مدیر پروژه است (پارامتر چهارم: عضو هیات مدیره هست یا نه)
PUR_HELD_SQL = ("((x.holder_id=? AND x.status IN ('open','returned') AND NOT (x.stage='delivery' AND x.recv_at IS NOT NULL)) "
                "OR (x.stage='delivery' AND x.status='open' AND x.requester_id=? AND EXISTS(SELECT 1 FROM purchase_receipts r "
                "WHERE r.purchase_id=x.id AND r.status='pending')) "
                "OR (x.stage='disc_review' AND x.status='open' AND EXISTS(SELECT 1 FROM project_members m WHERE "
                "m.project_id=x.project_id AND m.role_key='warehouse' AND m.user_id=?) AND EXISTS(SELECT 1 FROM "
                "purchase_receipt_lines l JOIN purchase_receipts r ON r.id=l.receipt_id WHERE r.purchase_id=x.id AND "
                "l.need_dec=1 AND l.sup_dec!='' AND l.fix_at IS NULL)) "
                "OR (x.status='open' AND x.stage IN ('pm_approve','price_approve','discrepancy') AND x.requester_id!=? AND (?=1 "
                "OR EXISTS(SELECT 1 FROM project_members m WHERE m.project_id=x.project_id AND m.role_key='pm' AND m.user_id=?))))")


def held_args(u):
    return (u['id'], u['id'], u['id'], u['id'], 1 if u['role'] == 'manager' else 0, u['id'])
# منتظر پرداخت امور مالی: خرید دفتر مرکزی انجام شده و پرداختش ثبت نشده (همزمان با اعلام وصول، نسخه ۲.۶)
PUR_PAYWAIT_SQL = "(x.pay_req_at IS NOT NULL AND x.pay_done_at IS NULL AND x.status NOT IN ('cancelled','rejected'))"
# فاکتور رسمی (ارزش افزوده) هنگام کنترل مدارک دریافت نشده
PUR_NOINV_SQL = "(x.docs_at IS NOT NULL AND COALESCE(x.official_inv,0)=0)"
# منتظر پرداخت یا تسویه مالی (پس از تأیید قیمت یا خرید کارگاه)
# خریدهای کارگاه (عمومی و مصرفی) به مالی دفتر مرکزی نمی‌آیند
# منتظر کنترل مدارک امور مالی (پرداخت و تسویه در سامانه نیست)
PUR_PAY_SQL = "((x.status='open' AND x.stage='finance_settle') OR " + PUR_PAYWAIT_SQL + ")"


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
        need(u['role'] in ('secretariat', 'manager'), 'ثبت نامه وارده فقط توسط دبیرخانه انجام می‌شود')
    if f['signer_id']:
        need(c.execute('SELECT 1 FROM users WHERE id=? AND active=1', (f['signer_id'],)).fetchone(), 'امضاکننده نامعتبر', 400)
        if is_site_only(c, u):
            need(f['signer_id'] in colleagues_of(c, u['id']) + [u['id']], 'امضاکننده باید از همکاران پروژه باشد', 400)
    # صادره: شماره فقط هنگام ثبت در دبیرخانه؛ مگر دبیرخانه نامه امضاشده کاغذی را مستقیم ثبت کند
    draft = f['kind'] == 'out' and (f['signer_id'] or u['role'] != 'secretariat')
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
    need(u['role'] == 'secretariat', 'ثبت و شماره‌گذاری صادره فقط توسط دبیرخانه انجام می‌شود')
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
            'can_register': L['status'] == 'draft' and u['role'] == 'secretariat'
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
    need(u['role'] in ('secretariat', 'manager'), 'بایگانی توسط دبیرخانه یا هیات مدیره انجام می‌شود')
    open_n = c.execute("SELECT COUNT(*) FROM referrals WHERE doc_type='letter' AND doc_id=? AND status IN %s" % str(OPEN),
                       (L['id'],)).fetchone()[0]
    if open_n:
        need(is_board(u) and b.get('force'), 'این نامه هنوز %d ارجاع باز دارد؛ ابتدا باید انجام یا بسته شوند' % open_n, 400)
        c.execute("UPDATE referrals SET status='closed', done_at=?, reply=reply||' [بسته شد با بایگانی]' "
                  "WHERE doc_type='letter' AND doc_id=? AND status IN %s" % str(OPEN), (now(), L['id']))
    c.execute("UPDATE letters SET status='archived', archive_code=?, closed_at=? WHERE id=?",
              (b.get('archive_code') or '', now(), L['id']))
    log(c, 'letter', L['id'], u['id'], 'بایگانی', b.get('archive_code') or '')
    return {'ok': True}


@route('POST', r'/api/letters/(\d+)/reopen')
def api_letter_reopen(h, c, u, b, q, lid):
    L = get_doc(c, u, 'letter', int(lid))
    need(u['role'] in ('secretariat', 'manager'))
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
    r = c.execute('SELECT MAX(id) FROM purchase_flow WHERE purchase_id=? AND action NOT IN (%s)'
                  % ','.join('?' * len(SIDE_ACTIONS)), (P['id'],) + SIDE_ACTIONS).fetchone()
    return r[0] or 0


def can_del_att(c, u, P, a):
    """پیوست را فقط خودِ بارگذارنده حذف می‌کند، آن هم فقط در همان نوبتی که درخواست در کارتابلش است."""
    if a['uploaded_by'] != u['id'] or (a.get('flow_id') or 0) <= handover_id(c, P):
        return False
    if a.get('receipt_id') and c.execute("SELECT 1 FROM purchase_receipts WHERE id=? AND status='closed'",
                                         (a['receipt_id'],)).fetchone():
        return False  # عکس وصول نوبت صادرشده قفل است
    if P['holder_id'] == u['id'] and P['status'] in ('open', 'returned'):
        return True
    return a.get('kind') in INV_KINDS and post_purchase_support(c, u, P)  # فاکتوری که پشتیبانی پس از خرید گذاشته


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
    kind = urllib.parse.unquote(h.headers.get('X-Kind') or '')
    kind = kind if kind in ATT_KINDS else ''
    if dt == 'purchase':
        need(can_attach_pur(c, u, D), 'پیوست فقط وقتی ممکن است که درخواست در کارتابل شما باشد')
        if not can_attach_turn(c, u, D):  # پشتیبانی پس از خرید: فقط فاکتور یا پیش‌فاکتور
            need(kind in INV_KINDS, 'پس از خرید، پشتیبانی فقط فاکتور یا پیش‌فاکتور پیوست می‌کند', 400)
    name = urllib.parse.unquote(h.headers.get('X-Filename') or 'file')
    name = re.sub(r'[\\/:*?"<>|]', '_', os.path.basename(name))[:150] or 'file'
    raw = h.raw_body
    need(raw, 'فایل خالی است', 400)
    sub = pur_folder(D) if dt == 'purchase' else datetime.date.today().strftime('%Y-%m')
    os.makedirs(os.path.join(FILES, sub), exist_ok=True)
    rel = os.path.join(sub, '%s_%s' % (secrets.token_hex(6), name))
    with open(os.path.join(FILES, rel), 'wb') as f:
        f.write(raw)
    fid = None
    if dt == 'purchase':
        fid = c.execute('INSERT INTO purchase_flow(purchase_id,stage,action,label,user_id,note,at) VALUES(?,?,?,?,?,?,?)',
                        (did, D['stage'], 'attach', 'پیوست ' + (kind or 'فایل'), u['id'], name, now())).lastrowid
    # مدارک قیمت برای کارگاه دیده نمی‌شود؛ فاکتور خرید دفتر مرکزی هم که پس از خرید پیوست شود
    hq_only = 1 if dt == 'purchase' and (D['stage'] in HQ_ONLY_STAGES or (
        kind in INV_KINDS and not site_path(c, D) and is_bought(c, D))) else 0
    rid = None
    if dt == 'purchase' and kind == 'عکس وصول' and D['stage'] == 'delivery':  # عکس وصول به نوبت باز تعلق دارد
        rid, hq_only = open_receipt(c, D)['id'], 0
    c.execute('INSERT INTO attachments(doc_type,doc_id,name,path,size,uploaded_by,created_at,kind,flow_id,hq_only,receipt_id) '
              'VALUES(?,?,?,?,?,?,?,?,?,?,?)', (dt, did, name, rel, len(raw), u['id'], now(), kind, fid, hq_only, rid))
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
    need((R['requester_id'] == u['id'] or is_board(u)) and R['status'] in ('pending', 'returned'),
         'لغو فقط پیش از تأیید نهایی و توسط درخواست‌کننده ممکن است')
    c.execute("UPDATE requests SET status='cancelled', closed_at=? WHERE id=?", (now(), R['id']))
    c.execute("UPDATE steps SET status='skipped' WHERE request_id=? AND status IN ('waiting','pending')", (R['id'],))
    log(c, 'request', R['id'], u['id'], 'لغو درخواست', b.get('note') or '')
    return {'ok': True}


@route('POST', r'/api/requests/(\d+)/pay')
def api_request_pay(h, c, u, b, q, rid):
    R = get_doc(c, u, 'request', int(rid))
    need(u['role'] == 'finance' or is_board(u), 'ثبت پرداخت فقط توسط مالی یا هیات مدیره')
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
            need_not_admin(c, v, label)
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
                need_not_admin(c, v, label)
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
    if stage in ('invoice_fix', 'disc_review'):  # با همان پشتیبانی که خرید را انجام داده (کارگاه یا دفتر مرکزی)
        stage = purchase_stage_of(c, P)
    sb = P.get('support_by') if hasattr(P, 'get') else None
    if stage in ('site_purchase', 'hq_quotes', 'hq_purchase') and sb and c.execute(
            'SELECT 1 FROM users WHERE id=? AND active=1', (sb,)).fetchone():
        return sb  # کار به این پشتیبانی واگذار شده است (نسخه ۳.۶)
    if stage == 'unit_approval':
        # تأیید بالادست مستقیم درخواست‌کننده (مهندس ← معاونش، پشتیبانی و انبار ← سرپرست کارگاه)؛ وگرنه رئیس واحد
        kind, key = 'member', unit_head_key(c, P)
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


def round_start(c, P):
    """شروع نوبت فعلی: آخرین ارسال مجدد یا برگشت؛ تأییدهای پیش از آن دوباره لازم است."""
    return c.execute("SELECT COALESCE(MAX(id),0) FROM purchase_flow WHERE purchase_id=? AND action IN ('resubmit','return')",
                     (P['id'],)).fetchone()[0]


RETURN_STAGES = ('unit_approval', 'supervisor_review', 'warehouse_check', 'tech_review', 'supervisor_approve')


def return_targets(c, u, P):
    """کسانی که درخواست را می‌توان به آن‌ها برگرداند (نسخه ۲.۸): درخواست‌کننده و هر کسی که پیش‌تر در مراحل
    کارگاه روی آن اقدام کرده؛ درخواست به همان مرحله و کارتابل او برمی‌گردد."""
    out = {P['requester_id']: 'returned'}
    for f in c.execute("SELECT user_id, stage FROM purchase_flow WHERE purchase_id=? AND action IN "
                       "('approve','stock','inquire') ORDER BY id", (P['id'],)):
        if f[1] in RETURN_STAGES and f[1] != P['stage'] and f[0] != P['requester_id']:
            out[f[0]] = f[1]
    out.pop(u['id'], None)
    names = {r[0]: r[1] for r in c.execute('SELECT id, full_name FROM users WHERE active=1')}
    return [{'id': k, 'name': names[k], 'stage': v, 'label': 'درخواست‌کننده' if v == 'returned' else P_STAGES[v]}
            for k, v in out.items() if k in names]


def tech_already(c, P):
    """معاون فنی قبلاً در همین نوبت درخواست را ثبت یا تأیید کرده است (دوباره لازم نیست)."""
    t = pmembers(c, P['project_id']).get('tech')
    if not t:
        return False
    if t == P['requester_id']:
        return True
    last_ret = round_start(c, P)
    return c.execute("SELECT 1 FROM purchase_flow WHERE purchase_id=? AND user_id=? AND id>? AND "
                     "action IN ('approve','submit') AND stage IN ('draft','unit_approval')",
                     (P['id'], t, last_ret)).fetchone() is not None


def done_this_round(c, P, stage):
    """این مرحله در نوبت فعلی (پس از آخرین ارسال مجدد) تأیید شده است."""
    last_ret = round_start(c, P)
    return c.execute("SELECT 1 FROM purchase_flow WHERE purchase_id=? AND stage=? AND action='approve' AND id>?",
                     (P['id'], stage, last_ret)).fetchone() is not None


def is_general(P):
    """کالای عمومی و مصرفی: در کارگاه و با پشتیبانی کارگاه تأمین می‌شود و به پشتیبانی دفتر مرکزی نمی‌رود
    (مگر خود پشتیبانی کارگاه آن را واگذار کند)."""
    return (P['category'] or 'general') != 'main'


def after_stock(c, P, notes):
    """(برای درخواست‌های قدیمی) پس از استعلام انبار یا تأیید رئیس واحد: مستقیم به سرپرست کارگاه؛ بدون معاون فنی (۴.۰)."""
    return 'supervisor_approve'


def after_supervisor(c, P, notes):
    """پس از تأیید سرپرست کارگاه: خرید در کارگاه ← خرید پشتیبانی کارگاه؛ خرید از دفتر مرکزی ← مدیر پروژه.
    از نسخه ۴.۰ کنترل معاون فنی هم برای خرید از دفتر مرکزی لازم نیست."""
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
        if c.execute("SELECT 1 FROM attachments WHERE doc_type='purchase' AND doc_id=? AND kind=? AND deleted_at IS NULL "
                     "AND archived=0", (P['id'], kind)).fetchone():
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
        need(is_board(u) or is_member(c, u, pid), 'فقط ارکان پروژه «%s» می‌توانند برای آن درخواست کالا ثبت کنند' % pr['name'])
    unit = b.get('unit')
    need(unit in dict(UNITS), 'واحد درخواست‌کننده را انتخاب کنید', 400)
    cat = b.get('category') or ''
    need(cat in dict(CATEGORIES), 'دسته کالا (دسته خرید) را انتخاب کنید', 400)
    urg = b.get('urgency') or 'normal'
    need(urg in dict(URGENCIES), 'فوریت نامعتبر', 400)
    rd = jnorm(req_date) or jtoday()
    nd = need_jdate(b.get('need_date'), 'تاریخ نیاز', min_=rd, min_msg='تاریخ نیاز نمی‌تواند قبل از تاریخ درخواست (%s) باشد' % rd)
    need((b.get('purpose') or '').strip(), 'کادر «جهت استفاده» را پر کنید', 400)
    items = []
    for it in b.get('items') or []:
        t = (it.get('title') or '').strip()
        if not t:
            need(not any((it.get(k) or '').strip() for k in ('qty', 'unit', 'spec', 'note')),
                 'شرح کالا در ردیفی که مقدار یا واحد دارد خالی است', 400)
            continue
        qty = (str(it.get('qty') or '')).translate(FA2EN).replace('٫', '.').strip()
        need(qty, 'مقدار کالای «%s» وارد نشده' % t, 400)
        need(to_num(qty) is not None and to_num(qty) > 0, 'مقدار کالای «%s» باید عدد باشد (واحد را در ستون واحد بنویسید)' % t, 400)
        need((it.get('unit') or '').strip(), 'واحد کالای «%s» را وارد کنید' % t, 400)
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


def unit_head_key(c, P):
    """سمتِ تأییدکننده واحد: بالادست مستقیم درخواست‌کننده، وگرنه رئیس واحد."""
    own = [k for _, k in user_project_roles(c, P['requester_id'], P['project_id']) if k in SUPERIOR]
    return SUPERIOR[own[0]] if own else UNIT_HEAD[P['unit']]


def is_direct_orderer(c, u, f):
    """مدیر پروژه یا عضو هیات مدیره: درخواست کالای او دستور خرید است و از زیردستش (سرپرست کارگاه) تأیید نمی‌گیرد."""
    return u['role'] == 'manager' or u['id'] == pmembers(c, f['project_id']).get('pm')


def after_direct_order(c, u, P, stage):
    """پس از ثبت دستور خرید مستقیم: سفارش خرید برای خرید دفتر مرکزی، و فقط یک رونوشت جهت اطلاع به سرپرست کارگاه."""
    if stage == 'hq_purchase' and not P['po_no']:
        c.execute('UPDATE purchases SET po_no=? WHERE id=?', (next_code(c, P['project_id'], 'PO'), P['id']))
    sup = pmembers(c, P['project_id']).get('supervisor')
    if sup and sup != u['id'] and not c.execute(
            "SELECT 1 FROM referrals WHERE doc_type='purchase' AND doc_id=? AND to_id=? AND status IN ('new','seen')",
            (P['id'], sup)).fetchone():
        c.execute('INSERT INTO referrals(doc_type,doc_id,from_id,to_id,action,instruction,created_at) VALUES(?,?,?,?,?,?,?)',
                  ('purchase', P['id'], u['id'], sup, 'جهت اطلاع', 'رونوشت دستور خرید %s' % u['full_name'], now()))


def start_stage(c, u, f):
    """نسخه ۴.۰: هر درخواست کالا مستقیم به سرپرست کارگاه می‌رود (بدون تأیید معاون یا رئیس واحد).
    اگر خود سرپرست کارگاه درخواست‌دهنده باشد، ثبت او همان تأیید است."""
    mem = pmembers(c, f['project_id'])
    if is_direct_orderer(c, u, f):  # نسخه ۴.۳: مدیر پروژه یا هیات مدیره مستقیم به پشتیبانی دستور می‌دهند
        nxt = 'site_purchase' if is_general(f) else 'hq_purchase'
        return nxt, 'دستور خرید مستقیم — ارسال به ' + P_STAGES[nxt]
    if u['id'] == mem.get('supervisor'):
        nxt = after_supervisor(c, f, [])
        return nxt, 'ثبت و تأیید سرپرست کارگاه — ارسال به ' + P_STAGES[nxt]
    return 'supervisor_approve', 'ثبت و ارسال برای تأیید سرپرست کارگاه'


def support_users(c):
    """پشتیبانی دفتر مرکزی و پشتیبانی همه کارگاه‌ها."""
    ids = {r[0] for r in c.execute("SELECT user_id FROM project_members WHERE role_key='support'")}
    sm = int(settings(c).get('support_manager') or 0)
    return ids | ({sm} if sm else set())


def cc_exec(c, u, pid, f):
    """درخواستی که معاون فنی صادر یا تأیید می‌کند (از جمله درخواست مهندسان دفتر فنی)، رونوشت به معاون اجرایی می‌رود."""
    mem = pmembers(c, f['project_id'])
    if mem.get('exec') in support_users(c):  # رونوشت به پشتیبانی نمی‌رود (نسخه ۳.۲)
        return
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
                 "x.unit=" + team_unit_sql() + "))")
        p += [u['id']] * 5
    for k in ('project_id', 'holder_id'):
        if q.get(k):
            w.append('x.%s=?' % k); p.append(int(q[k]))
    if q.get('unsettled'):
        w.append(PUR_PAY_SQL)
    if q.get('support') == 'hq':  # کارهای پشتیبانی دفتر مرکزی، با در نظر گرفتن واگذاری (نسخه ۳.۶)
        w.append("((x.stage IN ('hq_quotes','hq_purchase') AND COALESCE(x.support_side,'')!='site') OR "
                 "(x.stage IN ('site_purchase','invoice_fix') AND x.support_side='hq'))")
    if q.get('pay_wait'):
        w.append(PUR_PAYWAIT_SQL)
    if q.get('no_official'):
        w.append(PUR_NOINV_SQL)
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


# ۴.۶: مراحل پس از اعلام وصول (کنترل مدارک مالی و تکمیل فاکتور) کار دفتر مرکزی است؛ برای کارکنان کارگاه کار تمام شده است
POST_DELIVERY = ('finance_settle', 'finance_pay', 'invoice_fix', 'discrepancy', 'archive')


def site_mask(P, uid):
    """نمایش درخواست برای کارمند کارگاه: پس از اعلام وصول «تحویل شد — پایان کار کارگاه» و بدون مراحل مالی."""
    if P['status'] == 'open' and P['stage'] in POST_DELIVERY and P.get('holder_id') != uid:
        P['status'], P['site_done'] = 'site_done', 1
    for k in ('pay_req_at', 'pay_done_at', 'docs_at'):
        P[k] = None
    return P


@route('GET', '/api/purchases')
def api_purchases(h, c, u, b, q):
    w, p = pur_filter(c, u, q)
    res = rows(c.execute(PUR_SEL + 'WHERE %s ORDER BY x.id DESC LIMIT %d' % (w, int(q.get('limit') or 500)), p))
    if is_site_only(c, u):  # قیمت‌ها برای کارکنان کارگاه نمایش داده نمی‌شود
        for P in res:
            for k in ('supplier', 'amount', 'proposed_supplier', 'proposed_amount', 'paid_amount', 'sepidar_no'):
                P[k] = None
            site_mask(P, u['id'])
    return res


PUR_CSV_HEAD = ['شماره', 'تاریخ', 'پروژه', 'واحد درخواست‌کننده', 'انبار محل درخواست', 'دسته', 'فوریت',
                'تاریخ نیاز', 'جهت استفاده', 'درخواست‌کننده', 'نسخه',
                'سفارش خرید', 'اعلام وصول', 'ردیف',
                'شرح کالا', 'مقدار', 'واحد', 'مشخصات فنی', 'توضیحات', 'تحویل از انبار', 'مانده برای خرید',
                'مقدار خریداری‌شده', 'واحد خرید', 'وضعیت خرید', 'توضیح خرید', 'مقدار تحویل‌گرفته', 'مقدار انبار',
                'وضعیت', 'مرحله', 'در دست', 'جمع پرداخت (ریال)',
                'کد بایگانی', 'علت لغو']


def grn_codes(c, P):
    """همه کدهای اعلام وصول درخواست (هر نوبت یک کد)."""
    cs = [r[0] for r in c.execute("SELECT code FROM purchase_receipts WHERE purchase_id=? AND status='closed' ORDER BY seq",
                                  (P['id'],))]
    return '، '.join(cs) or (P['grn_no'] or '')


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
                         P['po_no'] or '', grn_codes(c, P), it.get('row_no', ''), it.get('title', ''),
                         it.get('qty', ''), it.get('unit', ''), it.get('spec', ''), it.get('note', ''),
                         it.get('stock_qty', ''), fmt_num(remaining(it)) if it else '', it.get('bought_qty', ''),
                         it.get('bought_unit', ''), BUY_STATUS.get(it.get('bought_status') or '', ''), it.get('bought_note', ''),
                         it.get('recv_qty', ''), it.get('wh_qty', ''),
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


def can_cancel(c, u, P):
    if P['status'] not in ('open', 'returned'):
        return False
    if P['stage'] in ('site_purchase', 'hq_purchase') and not is_bought(c, P) and (
            is_board(u) or (P['requester_id'] == u['id'] and is_direct_orderer(c, u, P))):
        return True  # دستور خرید مستقیم را صادرکننده تا پیش از خرید لغو می‌کند
    if P['stage'] not in CANCEL_STAGES:
        return False
    if is_warehouse(c, u, P) and P['requester_id'] != u['id'] and not is_board(u):  # انباردار درخواست را لغو نمی‌کند
        return False
    return (P['holder_id'] == u['id'] or is_board(u)
            or (P['requester_id'] == u['id'] and P['stage'] in ('supervisor_approve', 'unit_approval', 'returned')))


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
    site = site_view(c, u, P)
    if site:  # قیمت‌ها و مدارک پس از مدیر پروژه برای کارکنان کارگاه نمایش داده نمی‌شود
        hid = {a['flow_id'] for a in att if a.get('hq_only') and a.get('flow_id')}
        hq_inv = not site_path(c, P)  # فاکتور خرید دفتر مرکزی که پس از خرید پیوست یا حذف شده
        att = [a for a in att if not a.get('hq_only')]
        flow = [f for f in flow if not (f['stage'] in HQ_ONLY_STAGES and f['action'] in ('attach', 'delatt'))
                and f['id'] not in hid and f['action'] not in ('pay_done', 'official_inv')
                and not (hq_inv and f['action'] == 'delatt' and f['label'].endswith(INV_KINDS))]
        for f in flow:
            if f['stage'] in HQ_ONLY_STAGES:
                f['note'] = ''
        for k in ('supplier', 'amount', 'proposed_supplier', 'proposed_amount', 'paid_amount', 'sepidar_no', 'pay_note'):
            P[k] = None
        site_mask(P, u['id'])
        if P.get('site_done'):  # ادامه مراحل (مالی) برای کارکنان کارگاه نمایش داده نمی‌شود
            flow = [f for f in flow if f['stage'] not in POST_DELIVERY]
    # نظر مدیر پروژه و هیات مدیره هنگام تأیید یا برگشت (برای پشتیبانی به رنگ قرمز)
    mgmt = []
    for f in ([] if site else flow):  # فقط متن خودِ تأییدکننده (نه یادداشت خودکار سامانه)
        if f['stage'] in GROUP_STAGES and f['action'] in ('approve', 'direct', 'requote', 'accept', 'fix', 'return'):
            txt = '؛ '.join(p for p in (f['note'] or '').split('؛ ') if p.strip() and not p.startswith('پیش‌فاکتور منتخب'))
            if txt:
                mgmt.append(dict(f, note=txt))
    for it in items:
        it['remaining'] = fmt_num(remaining(it))
        it['remaining_receive'] = fmt_num(remaining_receive(c, it))
        it['remaining_wh'] = fmt_num(wh_remaining(c, it))
    receipts = rows(c.execute('SELECT r.*, w.full_name wh_name, v.full_name recv_name FROM purchase_receipts r '
                              'LEFT JOIN users w ON w.id=r.wh_by LEFT JOIN users v ON v.id=r.recv_by '
                              'WHERE r.purchase_id=? ORDER BY r.seq', (P['id'],)))
    for R in receipts:
        R['lines'] = rows(c.execute('SELECT l.*, i.title, i.unit, i.spec, i.bought_qty, i.bought_unit FROM purchase_receipt_lines l '
                                    'JOIN purchase_items i ON i.id=l.item_id WHERE l.receipt_id=? ORDER BY i.row_no', (R['id'],)))
    # ۴.۵: برگه واحد اعلام وصول — جمع هر قلم و سابقه مغایرت‌ها
    for it in items:
        cl = [l for R in receipts if R['status'] == 'closed' for l in R['lines'] if l['item_id'] == it['id']]
        it['arrived'] = fmt_num(sum(to_num(l['recv_qty']) or 0 for l in cl)) if cl else ''
        it['accepted'] = fmt_num(sum(line_acc(l) for l in cl)) if cl else ''
        it['pending_qty'] = fmt_num(sum(to_num(l['wh_qty']) or 0 for R in receipts if R['status'] == 'pending'
                                        for l in R['lines'] if l['item_id'] == it['id']))
        it['rcpt_notes'] = []
        for R in receipts:
            for l in R['lines']:
                if l['item_id'] != it['id'] or R['status'] != 'closed' or l['recv_status'] in ('', 'ok'):
                    continue
                t = 'نوبت %s: %s %s%s' % (fa_num(str(R['seq'])), LINE_STATUS.get(l['recv_status'], ''), fa_num(l['recv_qty']),
                                          (' — ' + l['recv_note']) if l['recv_note'] else '')
                if l['need_dec']:
                    t += ' ← ' + ('پشتیبانی: ' + DEC_LABEL[l['sup_dec']] + ((' (' + l['sup_note'] + ')') if l['sup_note'] else '')
                                  + ('؛ برگه اصلاح شد' if l['fix_at'] else '؛ منتظر اصلاح انباردار')
                                  if l['sup_dec'] else 'منتظر تصمیم پشتیبانی')
                it['rcpt_notes'].append(t)
    dlines = disc_lines(c, P['id'])
    for l in dlines:
        l['options'] = DECISIONS.get(l['recv_status'], {})
    bought = is_bought(c, P)
    fin = is_finance(c, u) and not site
    mem = pmembers(c, P['project_id'])
    wh = one(c.execute('SELECT full_name FROM users WHERE id=?', (P['wh_by'] or mem.get('warehouse') or 0,))) or {}
    return {'docs': docs_check(c, P) if bought and not site else None, 'receipt': bool(bought), 'warehouse_name': wh.get('full_name', ''),
            'site_path': site_path(c, P), 'is_admin': u['role'] == 'admin',
            'doc': P, 'items': items, 'flow': flow, 'versions': vers, 'attachments': att, 'referrals': refs,
            'actions': allowed_actions(c, u, P), 'can_edit': can_edit(u, P), 'can_cancel': can_cancel(c, u, P), 'handover_to': handover_target(c, u, P), 'return_targets': return_targets(c, u, P) if 'return' in allowed_actions(c, u, P) else [],
            'can_attach': can_attach_pur(c, u, P), 'mgmt_notes': mgmt, 'in_group': P['stage'] in GROUP_STAGES,
            'buy_status': BUY_STATUS, 'site_view': site, 'receipts': receipts, 'disc_lines': dlines, 'dec_label': DEC_LABEL,
            'sheet_final': bool(P['grn_no'] and P['recv_at'] and P['wh_at']), 'jtoday': jtoday(), 'can_edit_grn': can_edit_grn(c, u, P),
            'support_attach': not can_attach_turn(c, u, P) and post_purchase_support(c, u, P),
            'can_pay': fin and bool(P['pay_req_at']) and not P['pay_done_at'] and P['status'] not in ('cancelled', 'rejected'),
            'can_official': fin and bool(P['docs_at']) and not P['official_inv'],
            'hq_only_stages': HQ_ONLY_STAGES}


def site_view(c, u, P):
    """کارمند کارگاه مدارک و قیمت‌های دفتر مرکزی را نمی‌بیند، مگر کار دفتر مرکزی به او واگذار شده باشد."""
    return is_site_only(c, u) and not ((P['holder_id'] == u['id'] and P['stage'] in HQ_ONLY_STAGES) or c.execute(
        'SELECT 1 FROM purchase_flow WHERE purchase_id=? AND user_id=? AND stage IN (%s) AND action NOT IN (%s)' % (
            ','.join('?' * len(HQ_ONLY_STAGES)), ','.join('?' * len(SIDE_ACTIONS))),
        (P['id'], u['id']) + HQ_ONLY_STAGES + SIDE_ACTIONS).fetchone())


purchase_site_view = site_view  # نام سند CR-PUR-02


def file_payload(c, u, aid, q):
    """فایل پیوست با بررسی دسترسی: دیدن سند، پیوست حذف‌شده و مدارک قیمت دفتر مرکزی (CHG-00)."""
    need(u, 'ابتدا وارد شوید', 401)
    a = one(c.execute('SELECT * FROM attachments WHERE id=?', (aid,)))
    need(a, 'فایل پیدا نشد', 404)
    D = get_doc(c, u, a['doc_type'], a['doc_id'])
    if a['deleted_at'] and u['role'] != 'admin':
        raise ApiError('فایل پیدا نشد', 404)
    if a['doc_type'] == 'purchase' and a['hq_only']:
        need(not purchase_site_view(c, u, D), 'این پیوست فقط برای دفتر مرکزی است')
    with open(os.path.join(FILES, a['path']), 'rb') as f:
        data = f.read()
    ct = mimetypes.guess_type(a['name'])[0] or 'application/octet-stream'
    disp = 'inline' if ('dl' not in q) else 'attachment'
    return data, ct, {'Content-Disposition': "%s; filename*=UTF-8''%s" % (disp, urllib.parse.quote(a['name']))}


def is_warehouse(c, u, P):
    return pmembers(c, P['project_id']).get('warehouse') == u['id']


def allowed_actions(c, u, P):
    """اقدام‌هایی که این کاربر اکنون روی درخواست می‌تواند انجام دهد."""
    if P['status'] != 'open':
        return []
    acts = list(P_FLOW.get(P['stage'], {}))
    if P['stage'] == 'delivery':  # تحویل دوطرفه: درخواست‌کننده و انبار، هر کدام جدا
        out = []
        wh = pmembers(c, P['project_id']).get('warehouse')
        its = rows(c.execute('SELECT * FROM purchase_items WHERE purchase_id=?', (P['id'],)))
        # ۳.۷: انباردار تا وقتی باقی‌مانده دارد ثبت می‌کند (حتی اگر نوبت قبلی هنوز منتظر تحویل‌گیرنده است)
        whu = is_warehouse(c, u, P) or (not wh and u['id'] == P['requester_id'])
        if whu and unfixed_lines(c, P['id']):  # ۴.۵: اصلاح برگه طبق تصمیم پشتیبانی
            out += ['wh_fix']
        if whu and any(wh_remaining(c, it) > 0 for it in its):
            out += ['wh_ok', 'followup']
        if u['id'] == P['requester_id'] and pending_receipts(c, P['id']):  # تأیید نهایی هر نوبت ثبت‌شده انبار
            out += ['recv_ok']
        return out
    if P['stage'] == 'disc_review':  # ۴.۶: پشتیبانی تصمیم می‌گیرد و انباردار هم‌زمان اقلام تصمیم‌گرفته را اصلاح می‌کند
        wh = pmembers(c, P['project_id']).get('warehouse')
        out = ['decide'] if P['holder_id'] == u['id'] and undecided_lines(c, P['id']) else []
        if (is_warehouse(c, u, P) or (not wh and u['id'] == P['requester_id'])) and unfixed_lines(c, P['id']):
            out.append('wh_fix')
        return out
    if P['stage'] in GROUP_STAGES:  # مدیر پروژه یا هر عضو هیات مدیره؛ تأیید یک نفر کافی است
        return acts if in_group(c, u, P) else []
    if P['stage'] == 'finance_settle' and P['holder_id'] == u['id']:  # پایان فقط با کامل بودن سه مدرک
        return ['docs_ok', 'need_docs'] if docs_check(c, P)['ok'] else ['need_docs']
    return acts if P['holder_id'] == u['id'] else []


# مرحله‌هایی که در کارتابل مدیر پروژه و همه اعضای هیات مدیره است و تأیید یکی از آن‌ها کار را جلو می‌برد
GROUP_STAGES = ('pm_approve', 'price_approve', 'discrepancy')


def in_group(c, u, P):
    if P['requester_id'] == u['id'] and P['holder_id'] != u['id']:  # خودتأییدی ممنوع
        return False
    return (u['role'] == 'manager' or P['holder_id'] == u['id']
            or pmembers(c, P['project_id']).get('pm') == u['id'])


def can_attach_turn(c, u, P):
    """پیوست در نوبت خودِ کاربر: درخواست در کارتابل اوست."""
    if P['status'] not in ('open', 'returned'):
        return False
    if P['holder_id'] == u['id'] or (P['stage'] == 'delivery' and is_warehouse(c, u, P)):
        return True
    return P['stage'] in GROUP_STAGES and in_group(c, u, P)


def can_attach_pur(c, u, P):
    return can_attach_turn(c, u, P) or post_purchase_support(c, u, P)


def is_bought(c, P):
    return c.execute("SELECT 1 FROM purchase_flow WHERE purchase_id=? AND action='purchased'", (P['id'],)).fetchone() is not None


def post_purchase_support(c, u, P):
    """پشتیبانی‌ای که خرید را انجام داده (کارگاه یا دفتر مرکزی)، در همه مراحل پس از خرید، حتی پس از بایگانی،
    فاکتور یا پیش‌فاکتور پیوست می‌کند."""
    if P['status'] not in ('open', 'returned', 'closed') or P['stage'] in ('site_purchase', 'hq_purchase') \
            or not is_bought(c, P):
        return False
    if P.get('support_by') == u['id'] or c.execute(
            "SELECT 1 FROM purchase_flow WHERE purchase_id=? AND action='purchased' AND user_id=?",
            (P['id'], u['id'])).fetchone():  # کسی که خرید را انجام داده یا کار به او واگذار شده
        return True
    if site_path(c, P):
        return pmembers(c, P['project_id']).get('support') == u['id']
    return str(u['id']) == settings(c).get('support_manager')


# مراحل پشتیبانی که پشتیبانی دفتر مرکزی و کارگاه می‌توانند به یکدیگر واگذار کنند (نسخه ۲.۹)
SUPPORT_STAGES = ('site_purchase', 'hq_quotes', 'hq_purchase', 'invoice_fix', 'disc_review')


def handover_target(c, u, P):
    """همکارِ پشتیبانی که کار را می‌توان به او واگذار کرد: پشتیبانی کارگاه ↔ پشتیبانی دفتر مرکزی."""
    if P['status'] != 'open' or P['stage'] not in SUPPORT_STAGES or P['holder_id'] != u['id']:
        return None
    site_sup = pmembers(c, P['project_id']).get('support')
    hq_sup = int(settings(c).get('support_manager') or 0)
    tid, lbl = (site_sup, 'پشتیبانی کارگاه') if u['id'] != site_sup else (hq_sup, 'پشتیبانی دفتر مرکزی')
    if not tid or tid == u['id']:
        return None
    r = c.execute('SELECT full_name FROM users WHERE id=? AND active=1', (tid,)).fetchone()
    return {'id': tid, 'name': r[0], 'label': lbl} if r else None


@route('POST', r'/api/purchases/(\d+)/handover')
def api_purchase_handover(h, c, u, b, q, pid):
    """واگذاری کارِ پشتیبانی (استعلام، خرید، تکمیل فاکتور) به پشتیبانی دیگر؛ مرحله همان می‌ماند."""
    P = get_doc(c, u, 'purchase', int(pid))
    t = handover_target(c, u, P)
    need(t, 'واگذاری فقط در مراحل پشتیبانی و توسط کسی که کار در کارتابل اوست ممکن است')
    # کار به‌طور کامل به گیرنده منتقل می‌شود: مراحل بعدی پشتیبانی هم با اوست
    c.execute('UPDATE purchases SET holder_id=?, support_by=?, support_side=? WHERE id=?',
              (t['id'], t['id'], 'site' if t['label'] == 'پشتیبانی کارگاه' else 'hq', P['id']))
    pflow(c, P['id'], u, P['stage'], 'handover', 'واگذاری به %s (%s)' % (t['name'], t['label']), (b.get('note') or '').strip())
    return {'ok': True}


def can_edit_grn(c, u, P):
    """نسخه ۳.۵: اعلام وصول پس از صدور برای هیچ‌کس قابل تغییر نیست (برای سازگاری نگه داشته شده است)."""
    return False


def is_finance(c, u):
    return u['role'] == 'finance' or str(u['id']) == settings(c).get('finance_manager')


def move(c, u, P, stage, label, note='', status='open'):
    """انتقال درخواست به مرحله بعد و ثبت در گردش."""
    holder = None if stage == 'done' else (P['requester_id'] if stage == 'returned' else
                                           delivery_holder(c, P) if stage == 'delivery' else stage_holder(c, P, stage))
    if stage == 'returned':
        status = 'returned'
    c.execute('UPDATE purchases SET status=?, stage=?, holder_id=?, closed_at=? WHERE id=?',
              (status, stage, holder, now() if stage == 'done' else None, P['id']))
    pflow(c, P['id'], u, P['stage'], label[0], label[1], note)


# ---------- نوبت‌های اعلام وصول (نسخه ۳.۵): مقدار جدای انباردار و تحویل‌گیرنده، تحویل بخشی، قفل پس از صدور
def received_sum(c, item_id):
    """جمع مقدار تحویل‌گرفته یک قلم در نوبت‌های بسته‌شده."""
    return sum(line_acc(l) for l in rows(c.execute(
        "SELECT l.* FROM purchase_receipt_lines l JOIN purchase_receipts r ON r.id=l.receipt_id "
        "WHERE l.item_id=? AND r.status='closed'", (item_id,))))


def line_acc(l):
    """مقدار پذیرفته‌شده یک ردیف بسته (۴.۵)؛ ردیف‌های نسخه‌های قبل: عدد تحویل‌گیرنده (مرجوعی صفر)."""
    if l.get('acc_qty') not in (None, ''):
        return to_num(l['acc_qty']) or 0
    return 0.0 if l.get('recv_status') == 'returned' else (to_num(l.get('recv_qty')) or 0)


def remaining_receive(c, it):
    """باقی‌مانده برای وصول = خریداری‌شده − تحویل‌شده؛ قلمی که «پذیرش با توضیح» خورده کامل است."""
    if (it.get('bought_status') or '') not in ('bought', 'partial'):
        return 0.0
    if c.execute("SELECT 1 FROM purchase_receipt_lines l JOIN purchase_receipts r ON r.id=l.receipt_id "
                 "WHERE l.item_id=? AND r.status='closed' AND l.diff_note!=''", (it['id'],)).fetchone():
        return 0.0
    return max(0.0, (to_num(it.get('bought_qty')) or 0) - received_sum(c, it['id']))


# وضعیت هر قلم در اعلام وصول (انباردار و تحویل‌گیرنده، هر دو) — نسخه ۴.۵:
# تأیید (کالا درست است؛ کمتر یعنی بقیه بعداً)، اضافی، کسری، تحویل بخشی (بقیه بعداً می‌رسد)، اشتباه ارسال شده
LINE_STATUS = {'ok': 'تأیید', 'extra': 'اضافی', 'short': 'کسری', 'partial': 'تحویل بخشی', 'wrong': 'اشتباه ارسال شده',
               'returned': 'مرجوعی'}  # «مرجوعی» فقط برای نمایش ردیف‌های نسخه ۴.۴
IN_STATUS = ('ok', 'extra', 'short', 'partial', 'wrong')
DISC_STATUS = ('extra', 'short', 'wrong')  # پس از تأیید تحویل‌گیرنده برای تصمیم به پشتیبانی می‌رود
# تصمیم پشتیبانی برای هر مغایرت؛ پس از تصمیم، انباردار همان برگه را اصلاح (تأیید) می‌کند
DECISIONS = {'extra': {'accept_extra': 'پذیرش اضافه', 'return_extra': 'مرجوع کردن اضافه'},
             'short': {'accept_short': 'تأیید مقدار (کسری پذیرفته شد)', 'send_short': 'ارسال کسری توسط فروشنده'},
             'wrong': {'return_wrong': 'مرجوع شود (جایگزین ارسال می‌شود)', 'keep_wrong': 'امکان مرجوعی نیست (پذیرفته شد)'}}
DEC_LABEL = {k: v for d in DECISIONS.values() for k, v in d.items()}


def accepted_qty(stt, qv, exp=None):
    """مقدار پذیرفته‌شده یک ردیف پیش از تصمیم پشتیبانی: اشتباه صفر، اضافی تا سقف مورد انتظار."""
    if stt in ('wrong', 'returned'):
        return 0.0
    if stt == 'extra' and exp is not None:
        return min(qv, exp)
    return qv


def check_line(it, exp, qv, stt, note):
    """بررسی وضعیت یک قلم نسبت به مقدار مورد انتظار؛ در صورت خطا ApiError."""
    t = it['title']
    need(stt in IN_STATUS, 'وضعیت «%s» را انتخاب کنید (تأیید، اضافی، کسری، تحویل بخشی یا اشتباه ارسال شده)' % t, 400)
    u_ = it['bought_unit'] or it['unit'] or ''
    if stt == 'ok':
        need(qv <= exp + 1e-9, 'مقدار «%s» از مورد انتظار (%s %s) بیشتر است؛ «اضافی» را انتخاب کنید' % (t, fmt_num(exp), u_), 400)
    elif stt == 'extra':
        need(qv > exp + 1e-9, 'برای «اضافی» مقدار «%s» باید از مورد انتظار (%s) بیشتر باشد' % (t, fmt_num(exp)), 400)
    elif stt == 'wrong':
        need(qv > 1e-9, 'مقدار کالای اشتباه «%s» را بنویسید' % t, 400)
    else:  # کسری، تحویل بخشی
        need(qv < exp - 1e-9, 'برای «%s» مقدار «%s» باید از مورد انتظار (%s) کمتر باشد' % (LINE_STATUS[stt], t, fmt_num(exp)), 400)
    if stt == 'wrong':  # ۴.۶: برای کسری و اضافی توضیح اختیاری است
        need(note, 'توضیح «%s» برای «%s» را بنویسید' % (LINE_STATUS[stt], t), 400)


def disc_lines(c, pid, where=''):
    """ردیف‌های بسته‌ای که مغایرت دارند (برای تصمیم پشتیبانی و اصلاح انباردار)."""
    return rows(c.execute("SELECT l.*, r.seq, i.title, i.unit, i.bought_unit FROM purchase_receipt_lines l "
                          "JOIN purchase_receipts r ON r.id=l.receipt_id JOIN purchase_items i ON i.id=l.item_id "
                          "WHERE r.purchase_id=? AND r.status='closed' AND l.need_dec=1 " + where +
                          " ORDER BY r.seq, i.row_no", (pid,)))


def undecided_lines(c, pid):
    return disc_lines(c, pid, "AND l.sup_dec=''")


def unfixed_lines(c, pid):
    return disc_lines(c, pid, "AND l.sup_dec!='' AND l.fix_at IS NULL")


def sheet_done(c, P):
    """برگه اعلام وصول قطعی می‌شود: همه اقلام رسیده، نوبتی منتظر تحویل‌گیرنده نیست، همه مغایرت‌ها تصمیم و اصلاح شده‌اند."""
    return bool(closed_receipts(c, P['id'])) and not pending_receipts(c, P['id']) and not undecided_lines(c, P['id']) \
        and not unfixed_lines(c, P['id']) and all(
            remaining_receive(c, it) <= 0 for it in rows(c.execute('SELECT * FROM purchase_items WHERE purchase_id=?', (P['id'],))))


def finalize_sheet(c, u, P, notes, by_wh=False):
    """برگه اعلام وصول قطعی شد؛ مرحله بعد (پایان برای خرید کارگاه، کنترل مدارک مالی برای دفتر مرکزی)."""
    c.execute("DELETE FROM purchase_receipt_lines WHERE receipt_id IN (SELECT id FROM purchase_receipts "
              "WHERE purchase_id=? AND status='open')", (P['id'],))
    c.execute("DELETE FROM purchase_receipts WHERE purchase_id=? AND status='open'", (P['id'],))
    L = closed_receipts(c, P['id'])[-1]
    wh_by, wh_at = (u['id'], now()) if by_wh else (L['wh_by'], L['wh_at'])
    c.execute('UPDATE purchases SET recv_by=?, recv_at=?, wh_by=?, wh_at=? WHERE id=?',
              (L['recv_by'], L['recv_at'], wh_by, wh_at, P['id']))
    P = dict(P, recv_at=L['recv_at'], wh_at=wh_at)
    notes.append('برگه اعلام وصول %s قطعی شد' % (P['grn_no'] or ''))
    nxt = after_delivery(c, P)
    if nxt == 'done':
        notes.append('پایان و بایگانی خودکار')
    return nxt


def pending_receipts(c, pid):
    """نوبت‌هایی که انباردار ثبت کرده و منتظر تأیید تحویل‌گیرنده‌اند (نسخه ۳.۷)."""
    return rows(c.execute("SELECT * FROM purchase_receipts WHERE purchase_id=? AND status='pending' ORDER BY seq", (pid,)))


def wh_remaining(c, it):
    """باقی‌مانده‌ای که انباردار هنوز باید ثبت کند = باقی‌مانده وصول − آنچه در نوبت‌های منتظر تحویل‌گیرنده ثبت شده."""
    left = remaining_receive(c, it)
    if left <= 0:
        return 0.0
    for l in c.execute("SELECT l.wh_qty, l.diff_note, l.wh_status FROM purchase_receipt_lines l JOIN purchase_receipts r "
                       "ON r.id=l.receipt_id WHERE l.item_id=? AND r.status='pending'", (it['id'],)):
        if l[1]:
            return 0.0  # پذیرش با توضیح: قلم برای انبار کامل است
        if l[2] not in ('returned', 'wrong'):  # کالای اشتباه یا مرجوعی دوباره باید برسد
            left -= to_num(l[0]) or 0
    return max(0.0, left)


def closed_receipts(c, pid):
    return rows(c.execute("SELECT * FROM purchase_receipts WHERE purchase_id=? AND status='closed' ORDER BY seq", (pid,)))


def open_receipt(c, P, create=True):
    """نوبت باز اعلام وصول؛ خطوطش با اقلامی که باقی‌مانده دارند هماهنگ می‌شود."""
    R = one(c.execute("SELECT * FROM purchase_receipts WHERE purchase_id=? AND status='open'", (P['id'],)))
    if not R:
        if not create:
            return None
        seq = c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM purchase_receipts WHERE purchase_id=?', (P['id'],)).fetchone()[0]
        c.execute('INSERT INTO purchase_receipts(purchase_id,seq) VALUES(?,?)', (P['id'], seq))
        R = one(c.execute("SELECT * FROM purchase_receipts WHERE purchase_id=? AND status='open'", (P['id'],)))
    have = {r[0] for r in c.execute('SELECT item_id FROM purchase_receipt_lines WHERE receipt_id=?', (R['id'],))}
    for it in rows(c.execute('SELECT * FROM purchase_items WHERE purchase_id=?', (P['id'],))):
        left = wh_remaining(c, it)
        if left > 0 and it['id'] not in have:
            c.execute('INSERT INTO purchase_receipt_lines(receipt_id,item_id) VALUES(?,?)', (R['id'], it['id']))
        elif left <= 0 and it['id'] in have:
            c.execute('DELETE FROM purchase_receipt_lines WHERE receipt_id=? AND item_id=?', (R['id'], it['id']))
    return R


def refresh_item_totals(c, pid):
    """جمع تجمعی مقدار انبار و تحویل‌گیرنده در اقلام (برای فرم چاپی و CSV)."""
    for it in rows(c.execute('SELECT id FROM purchase_items WHERE purchase_id=?', (pid,))):
        ls = rows(c.execute("SELECT l.* FROM purchase_receipt_lines l "
                            "JOIN purchase_receipts r ON r.id=l.receipt_id WHERE l.item_id=? AND r.status='closed'", (it['id'],)))
        # تحویل‌گیرنده = پذیرفته‌شده؛ انبار = رسیده به شمارش انباردار
        c.execute('UPDATE purchase_items SET recv_qty=?, wh_qty=? WHERE id=?',
                  (fmt_num(sum(line_acc(x) for x in ls)) if ls else '',
                   fmt_num(sum(to_num(x['wh_qty']) or 0 for x in ls)) if ls else '', it['id']))


def close_receipt(c, u, P, R):
    """۴.۵: تأیید تحویل‌گیرنده نوبت را در برگه اعلام وصول درخواست ثبت می‌کند. هر درخواست یک برگه و یک شماره دارد
    (شماره با اولین تأیید داده می‌شود)؛ برگه پس از تکمیل و اصلاح مغایرت‌ها قطعی می‌شود."""
    grn = P['grn_no'] or next_code(c, P['project_id'], 'GRN')
    c.execute('UPDATE purchases SET grn_no=? WHERE id=?', (grn, P['id']))
    c.execute("UPDATE purchase_receipts SET status='closed', closed_at=?, code=?, recv_by=?, recv_at=? WHERE id=?",
              (now(), grn, u['id'], now(), R['id']))
    refresh_item_totals(c, P['id'])
    return grn


def has_closed_receipt(c, pid):
    return c.execute("SELECT 1 FROM purchase_receipts WHERE purchase_id=? AND status='closed'", (pid,)).fetchone() is not None


def delivery_holder(c, P):
    """اعلام وصول: انباردار تا وقتی باقی‌مانده‌ای برای ثبت دارد؛ وگرنه تحویل‌گیرنده برای تأیید نوبت‌های منتظر (۳.۷).
    نوبت‌ها موازی‌اند: انباردار لازم نیست منتظر تأیید تحویل‌گیرنده بماند."""
    wh = pmembers(c, P['project_id']).get('warehouse')
    its = rows(c.execute('SELECT * FROM purchase_items WHERE purchase_id=?', (P['id'],)))
    if wh and (unfixed_lines(c, P['id']) or any(wh_remaining(c, it) > 0 for it in its)):
        return wh
    if pending_receipts(c, P['id']):
        return P['requester_id']
    return wh or P['requester_id']


def after_delivery(c, P):
    """پس از اعلام وصول: خرید کارگاه (عمومی و مصرفی) پایان و بایگانی خودکار؛ خرید دفتر مرکزی به کنترل مدارک مالی."""
    return 'done' if site_path(c, P) else 'finance_settle'


@route('POST', r'/api/purchases/(\d+)/act')
def api_purchase_act(h, c, u, b, q, pid):
    P = get_doc(c, u, 'purchase', int(pid))
    a = b.get('action')
    need(a in allowed_actions(c, u, P), 'این اقدام اکنون با شما نیست')
    nxt, label = P_FLOW[P['stage']][a]
    note = (b.get('note') or '').strip()
    notes = []
    if a in ('return', 'requote', 'accept', 'fix', 'followup', 'refer'):
        need(note, 'علت را بنویسید', 400)
    if a == 'direct':  # توضیح (منبع و قیمت) اختیاری است (نسخه ۲.۶)
        if not P['po_no']:  # صدور سفارش خرید
            c.execute('UPDATE purchases SET po_no=? WHERE id=?', (next_code(c, P['project_id'], 'PO'), P['id']))
    if a == 'approve' and P['stage'] == 'price_approve':  # انتخاب یک پیش‌فاکتور؛ بقیه بایگانی می‌شوند
        pfs = [r[0] for r in c.execute("SELECT id FROM attachments WHERE doc_type='purchase' AND doc_id=? AND "
                                       "kind='پیش‌فاکتور' AND deleted_at IS NULL AND archived=0", (P['id'],))]
        ch = int(b.get('chosen_att') or (pfs[0] if len(pfs) == 1 else 0))
        need(ch in pfs, 'یکی از پیش‌فاکتورها را انتخاب کنید', 400)
        c.execute("UPDATE attachments SET archived=1 WHERE doc_type='purchase' AND doc_id=? AND kind='پیش‌فاکتور' AND id!=?",
                  (P['id'], ch))
        c.execute('UPDATE purchases SET chosen_att=? WHERE id=?', (ch, P['id']))
        nm = c.execute('SELECT name FROM attachments WHERE id=?', (ch,)).fetchone()[0]
        notes.append('پیش‌فاکتور منتخب: %s%s' % (nm, (' — %s پیش‌فاکتور دیگر بایگانی شد' % fa_num(len(pfs) - 1)) if len(pfs) > 1 else ''))
        if not P['po_no']:  # صدور سفارش خرید
            c.execute('UPDATE purchases SET po_no=? WHERE id=?', (next_code(c, P['project_id'], 'PO'), P['id']))
    if a == 'purchased' and P['stage'] == 'site_purchase' and not P['po_no']:  # خرید کارگاه: سفارش همان خرید است
        c.execute('UPDATE purchases SET po_no=? WHERE id=?', (next_code(c, P['project_id'], 'PO'), P['id']))
    if a == 'accept':
        c.execute('UPDATE purchases SET disc_ok_by=?, disc_ok_at=?, disc_note=? WHERE id=?', (u['id'], now(), note, P['id']))
    its = rows(c.execute('SELECT * FROM purchase_items WHERE purchase_id=? ORDER BY row_no', (P['id'],)))
    posted = {int(x.get('id') or 0): x for x in (b.get('items') or [])}

    if a == 'refer':  # سرپرست کارگاه: ارجاع به هر کس برای کنترل یا تکمیل مدارک؛ کار به کارتابل او می‌رود
        to = int(b.get('refer_to') or 0)
        need(to, 'شخصی را که کار به او ارجاع می‌شود انتخاب کنید', 400)
        need(to != u['id'], 'ارجاع به خودتان معنا ندارد', 400)
        tu = one(c.execute('SELECT id, full_name FROM users WHERE id=? AND active=1', (to,)))
        need(tu, 'کاربر انتخاب‌شده نامعتبر است', 400)
        need_not_admin(c, to, 'ارجاع')
        c.execute("UPDATE purchases SET status='open', stage='referred', holder_id=? WHERE id=?", (to, P['id']))
        pflow(c, P['id'], u, P['stage'], 'refer', 'ارجاع به %s برای کنترل یا تکمیل مدارک' % tu['full_name'], note)
        return {'ok': True}
    rt = int(b.get('return_to') or 0)
    if a == 'return' and rt and rt != P['requester_id']:  # برگشت به یکی از اقدام‌کنندگان قبلی، نه درخواست‌کننده
        tg = next((t for t in return_targets(c, u, P) if t['id'] == rt), None)
        need(tg, 'درخواست را فقط به درخواست‌کننده یا کسانی که پیش‌تر روی آن اقدام کرده‌اند می‌توان برگرداند', 400)
        c.execute("UPDATE purchases SET status='open', stage=?, holder_id=? WHERE id=?", (tg['stage'], rt, P['id']))
        pflow(c, P['id'], u, P['stage'], 'return', 'برگشت به %s (%s)' % (tg['name'], tg['label']), note)
        return {'ok': True}
    if a == 'submit':
        nxt, label = start_stage(c, u, P)
        if is_direct_orderer(c, u, P):
            after_direct_order(c, u, P, nxt)
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
    elif a == 'approve' and P['stage'] == 'unit_approval':  # پس از رئیس واحد: معاون فنی (اصلی) یا سرپرست کارگاه
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
            rs = received_sum(c, it['id']) + sum(to_num(r[0]) or 0 for r in c.execute(  # بسته + منتظر تحویل‌گیرنده (بدون مرجوعی)
                "SELECT l.wh_qty FROM purchase_receipt_lines l JOIN purchase_receipts r ON r.id=l.receipt_id "
                "WHERE l.item_id=? AND r.status='pending' AND l.wh_status NOT IN ('returned','wrong')", (it['id'],)))
            need(((bq or 0) if stt != 'none' else 0) >= rs - 1e-9,
                 'مقدار خریداری‌شده «%s» از مقدار تحویل‌شده (%s) کمتر است' % (it['title'], fmt_num(rs)), 400)
            c.execute('UPDATE purchase_items SET bought_qty=?, bought_unit=?, bought_status=?, bought_note=? WHERE id=?',
                      (fmt_num(bq) if stt != 'none' else '0', (x.get('unit') or it['unit'] or '').strip(), stt,
                       (x.get('note') or '').strip(), it['id']))
        if P['stage'] == 'site_purchase':
            need(invoice_doc(c, P), 'فاکتور (یا پیش‌فاکتور) خرید را پیوست کنید', 400)
        elif not P['pay_req_at']:  # خرید دفتر مرکزی: همزمان با اعلام وصول، به امور مالی برای پرداخت
            c.execute('UPDATE purchases SET pay_req_at=? WHERE id=?', (now(), P['id']))
            notes.append('همزمان برای پرداخت به امور مالی ارسال شد')
        c.execute('UPDATE purchases SET recv_by=NULL, recv_at=NULL, wh_by=NULL, wh_at=NULL WHERE id=?', (P['id'],))
        c.execute("UPDATE purchase_items SET disc_note='' WHERE purchase_id=?", (P['id'],))  # مغایرت رفع شد
        c.execute("UPDATE purchase_receipt_lines SET disc_note='' WHERE receipt_id IN "
                  "(SELECT id FROM purchase_receipts WHERE purchase_id=? AND status='open')", (P['id'],))
        if sheet_done(c, P):  # کسری با کاهش مقدار خریداری‌شده بسته شد: همه اقلام پیش‌تر تحویل شده‌اند
            nxt = finalize_sheet(c, u, P, notes)
    elif a == 'followup':  # انبار: باقی‌مانده نرسیده و پیگیری لازم است ← پشتیبانی خریدار؛ نوبت باز می‌ماند
        c.execute('UPDATE purchases SET recv_by=NULL, recv_at=NULL, wh_by=NULL, wh_at=NULL WHERE id=?', (P['id'],))
        nxt = purchase_stage_of(c, P)
        left = [it['title'] + ' ' + fmt_num(wh_remaining(c, it)) for it in its if wh_remaining(c, it) > 0]
        if left:
            notes.append('باقی‌مانده: ' + '، '.join(left))
    elif a in ('wh_ok', 'recv_ok'):
        # نسخه ۴.۵: هر درخواست یک برگه اعلام وصول دارد. انباردار هر بار که کالا رسید، برای هر قلم مقدار رسیده و یکی از
        # پنج وضعیت (تأیید، اضافی، کسری، تحویل بخشی، اشتباه ارسال شده) را ثبت می‌کند؛ تحویل‌گیرنده همان ستون را می‌بیند،
        # در صورت لزوم تغییر می‌دهد و تأیید می‌کند. اقلام اضافی، کسری و اشتباه پس از تأیید او برای تصمیم به پشتیبانی
        # می‌روند و پس از تصمیم، انباردار همان برگه را اصلاح می‌کند. نوبت‌ها موازی‌اند.
        wh_act = a == 'wh_ok'
        pre = 'wh' if wh_act else 'recv'
        who = 'انباردار' if wh_act else 'تحویل‌گیرنده'
        if wh_act:
            R = open_receipt(c, P)
        else:
            pend = pending_receipts(c, P['id'])
            need(pend, 'نوبتی منتظر تأیید شما نیست', 400)
            R = pend[0]
        itm = {it['id']: it for it in its}
        lines = rows(c.execute('SELECT * FROM purchase_receipt_lines WHERE receipt_id=?', (R['id'],)))
        need(lines, 'قلمی برای اعلام وصول باقی نمانده است', 400)
        disc, part = [], []
        arrived = 0.0
        for ln in lines:
            it, x = itm[ln['item_id']], posted.get(ln['item_id']) or {}
            whq = to_num(ln['wh_qty']) if ln['wh_qty'] != '' else None
            exp = wh_remaining(c, it) if wh_act else (to_num(ln['exp_qty']) if ln['exp_qty'] != '' else remaining_receive(c, it))
            qv = to_num(x.get('qty')) if x.get('qty') not in (None, '') else (exp if wh_act or whq is None else whq)
            need(qv is not None and qv >= 0, 'مقدار «%s» باید عدد باشد' % it['title'], 400)
            stt = (x.get('status') or '').strip() or (ln['wh_status'] if not wh_act and ln['wh_status'] in IN_STATUS else
                                                      ('ok' if qv <= exp + 1e-9 else 'extra'))
            nt = (x.get('note') or '').strip()
            # ۴.۶: تحویل‌گیرنده‌ای که با وضعیت انباردار موافق است، لازم نیست توضیح او را دوباره بنویسد
            check_line(it, exp, qv, stt, nt or (ln['wh_note'] if not wh_act and stt == ln['wh_status'] else ''))
            txt = '%s: %s%s' % (who, LINE_STATUS[stt], (' — ' + nt) if nt else '')
            upd = {pre + '_qty': fmt_num(qv), pre + '_status': stt, pre + '_note': nt,
                   'disc_note': txt if stt in DISC_STATUS else ''}
            if wh_act:
                upd['exp_qty'] = fmt_num(exp)
            else:
                upd.update(acc_qty=fmt_num(accepted_qty(stt, qv, exp)), need_dec=1 if stt in DISC_STATUS else 0,
                           sup_dec='', sup_note='', sup_by=None, sup_at=None, fix_by=None, fix_at=None)
            c.execute('UPDATE purchase_receipt_lines SET %s WHERE id=?' % ', '.join('%s=?' % k for k in upd),
                      tuple(upd.values()) + (ln['id'],))
            c.execute('UPDATE purchase_items SET disc_note=? WHERE id=?', (upd['disc_note'], it['id']))
            if wh_act and qv <= 1e-9 and stt != 'extra':  # ۴.۶: قلمی که در این نوبت نرسیده نزد انباردار می‌ماند
                c.execute('DELETE FROM purchase_receipt_lines WHERE id=?', (ln['id'],))
                c.execute("UPDATE purchase_items SET disc_note='' WHERE id=?", (it['id'],))
                continue
            arrived += qv
            u_ = it['bought_unit'] or it['unit'] or ''
            if stt in DISC_STATUS:
                disc.append('%s (%s %s %s%s)' % (it['title'], LINE_STATUS[stt], fmt_num(qv), u_, (' — ' + nt) if nt else ''))
            elif qv < exp - 1e-9:
                part.append('%s: %s از %s' % (it['title'], fmt_num(qv), fmt_num(exp)))
        detail = '؛ '.join((['مغایرت: ' + '؛ '.join(disc)] if disc else []) +
                          (['تحویل بخشی: ' + '، '.join(part)] if part else []) + ([note] if note else []))
        if wh_act:  # نوبت ثبت شد و منتظر تأیید تحویل‌گیرنده است
            need(arrived > 1e-9, 'مقدار رسیده هیچ قلمی را ننوشته‌اید', 400)
            dd = need_jdate(b.get('delivery_date'), 'تاریخ تحویل', required=False, max_=jtoday(),
                            max_msg='تاریخ تحویل نمی‌تواند بعد از امروز باشد')
            ref = (b.get('delivery_ref') or '').strip()
            need(len(ref) <= 60, 'شماره حواله یا بارنامه حداکثر ۶۰ نویسه است', 400)
            c.execute("UPDATE purchase_receipts SET status='pending', wh_by=?, wh_at=?, delivery_date=?, delivery_ref=?, note=? "
                      "WHERE id=?", (u['id'], now(), dd, ref, 'مغایرت' if disc else ('تحویل بخشی' if part else ''), R['id']))
            hold = delivery_holder(c, P)
            c.execute('UPDATE purchases SET holder_id=? WHERE id=?', (hold, P['id']))
            pflow(c, P['id'], u, P['stage'], a, label + ' — نوبت %s برای تأیید تحویل‌گیرنده' % fa_num(str(R['seq']))
                  + ('؛ باقی‌مانده نزد انباردار' if hold != P['requester_id'] else ''), detail)
            return {'ok': True}
        grn = close_receipt(c, u, P, R)
        P = dict(P, grn_no=grn)
        msg = 'نوبت %s در برگه اعلام وصول %s ثبت شد' % (fa_num(str(R['seq'])), grn)
        if disc:  # اضافی، کسری یا اشتباه: برای تصمیم به پشتیبانی خریدار
            nxt = 'disc_review'
            label = 'تأیید تحویل‌گیرنده — %s؛ مغایرت برای تصمیم به پشتیبانی' % msg
            notes.append('مغایرت: ' + '؛ '.join(disc))
        elif sheet_done(c, P):
            nxt = finalize_sheet(c, u, P, notes)
            label = 'تأیید تحویل‌گیرنده — ' + msg
        else:  # تحویل بخشی: برگه تا تکمیل باز می‌ماند؛ باقی‌مانده نزد انباردار
            c.execute('UPDATE purchases SET holder_id=? WHERE id=?', (delivery_holder(c, P), P['id']))
            pflow(c, P['id'], u, P['stage'], a, label + ' — ' + msg + ' (برگه تا تکمیل باز است)', detail)
            return {'ok': True}
    elif a == 'decide':  # ۴.۵: تصمیم پشتیبانی برای هر مغایرت؛ سپس انباردار همان برگه را اصلاح می‌کند
        # ۴.۶: پشتیبانی برای هر قلم جداگانه تصمیم می‌گیرد؛ هر قلمِ تصمیم‌گرفته همان لحظه برای اصلاح نزد انباردار می‌رود
        dec = {int(x.get('line') or 0): x for x in (b.get('decisions') or []) if (x.get('dec') or '').strip()}
        und = undecided_lines(c, P['id'])
        need(any(l['id'] in dec for l in und), 'برای حداقل یک قلم تصمیم بگیرید', 400)
        done_ = []
        for l in und:
            if l['id'] not in dec:
                continue
            x = dec[l['id']]
            d_, nt = (x.get('dec') or '').strip(), (x.get('note') or '').strip()
            opts = DECISIONS.get(l['recv_status'], {})
            need(d_ in opts, 'برای «%s» (%s) یکی از گزینه‌ها را انتخاب کنید: %s'
                 % (l['title'], LINE_STATUS.get(l['recv_status'], ''), '، '.join(opts.values())), 400)
            need(d_ != 'keep_wrong' or nt, 'برای «%s» علت عدم امکان مرجوعی را بنویسید' % l['title'], 400)
            q, e = to_num(l['recv_qty']) or 0, to_num(l['exp_qty']) or 0
            it = one(c.execute('SELECT * FROM purchase_items WHERE id=?', (l['item_id'],)))
            bq = to_num(it['bought_qty']) or 0
            acc = {'accept_extra': q, 'return_extra': min(q, e), 'accept_short': q, 'send_short': q,
                   'return_wrong': 0.0, 'keep_wrong': min(q, e)}[d_]
            if d_ == 'accept_extra':  # مقدار خرید با اضافه پذیرفته‌شده اصلاح می‌شود
                bq += max(0.0, q - e)
            elif d_ == 'accept_short':  # مقدار خرید به مقدار رسیده کاهش می‌یابد
                bq = max(0.0, bq - max(0.0, e - q))
            if bq != (to_num(it['bought_qty']) or 0):
                c.execute('UPDATE purchase_items SET bought_qty=? WHERE id=?', (fmt_num(bq), it['id']))
            c.execute('UPDATE purchase_receipt_lines SET sup_dec=?, sup_note=?, sup_by=?, sup_at=?, acc_qty=? WHERE id=?',
                      (d_, nt, u['id'], now(), fmt_num(acc), l['id']))
            c.execute("UPDATE purchase_items SET disc_note='' WHERE id=?", (it['id'],))
            done_.append('%s: %s%s' % (l['title'], DEC_LABEL[d_], (' — ' + nt) if nt else ''))
        refresh_item_totals(c, P['id'])
        left = undecided_lines(c, P['id'])
        if left:  # بقیه اقلام هنوز با پشتیبانی است؛ قلم‌های تصمیم‌گرفته را انباردار هم‌زمان اصلاح می‌کند
            pflow(c, P['id'], u, P['stage'], a, 'تصمیم پشتیبانی برای %s — ارسال به انبار برای اصلاح برگه؛ %s قلم دیگر منتظر تصمیم'
                  % ('، '.join(x.split(':')[0] for x in done_), fa_num(str(len(left)))), '؛ '.join(done_ + ([note] if note else [])))
            return {'ok': True}
        notes.append('؛ '.join(done_))
    elif a == 'wh_fix':  # انباردار برگه را طبق تصمیم پشتیبانی اصلاح (تأیید) می‌کند
        fx = unfixed_lines(c, P['id'])
        c.execute('UPDATE purchase_receipt_lines SET fix_by=?, fix_at=? WHERE id IN (%s)' % ','.join('?' * len(fx)),
                  (u['id'], now()) + tuple(l['id'] for l in fx))
        detail = '؛ '.join('%s: %s' % (l['title'], DEC_LABEL[l['sup_dec']]) for l in fx)
        if P['stage'] == 'disc_review':  # پشتیبانی هنوز درباره اقلام دیگر تصمیم می‌گیرد
            pflow(c, P['id'], u, P['stage'], a, 'اصلاح برگه اعلام وصول توسط انباردار (اقلام تصمیم‌گرفته)',
                  '؛ '.join([detail] + ([note] if note else [])))
            return {'ok': True}
        if sheet_done(c, P):
            nxt = finalize_sheet(c, u, P, notes, by_wh=True)
            notes.insert(0, detail)
        else:
            c.execute('UPDATE purchases SET holder_id=? WHERE id=?', (delivery_holder(c, P), P['id']))
            pflow(c, P['id'], u, P['stage'], a, label + ' — برگه %s تا تکمیل باز است' % (P['grn_no'] or ''),
                  '؛ '.join([detail] + ([note] if note else [])))
            return {'ok': True}
    elif a == 'docs_ok':  # امور مالی: کنترل مدارک؛ پرداخت و تسویه در سامانه نیست
        # فاکتور رسمی و مدارک ارزش افزوده یک مدرک‌اند (نسخه ۲.۶)
        oi = str(b.get('official_inv', ''))
        need(oi in ('0', '1'), 'مشخص کنید فاکتور رسمی (ارزش افزوده) دریافت شده یا نه', 400)
        c.execute('UPDATE purchases SET official_inv=?, vat_docs=?, docs_by=?, docs_at=? WHERE id=?',
                  (int(oi), int(oi), u['id'], now(), P['id']))
        notes.append('فاکتور رسمی و ارزش افزوده: %s — بایگانی خودکار' % ('دریافت شد' if oi == '1' else
                                                                       'دریافت نشد (در فهرست فاکتورهای رسمی دریافت‌نشده)'))

    label = label + (' — ارسال به ' + P_STAGES[nxt] if a in ('stock', 'approve') and nxt not in ('done', 'returned') else '')
    move(c, u, P, nxt, (a, label), '؛ '.join([note] + notes if note else notes),
         'closed' if nxt == 'done' else 'open')
    # نسخه ۳.۲: هیچ رونوشتی از درخواست کالا به پشتیبانی (دفتر مرکزی یا کارگاه) نمی‌رود؛ نظر مدیر پروژه / هیات مدیره
    # در کارت اقدام پشتیبانی به رنگ قرمز دیده می‌شود
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/edit')
def api_purchase_edit(h, c, u, b, q, pid):
    P = get_doc(c, u, 'purchase', int(pid))
    need(can_edit(u, P), 'ویرایش فقط وقتی ممکن است که درخواست در کارتابل شما باشد، و فقط تا مرحله مدیر پروژه')
    # ویرایش اقلام را حذف و دوباره درج می‌کند و مقدارهای وصول را از بین می‌برد؛ حتی برای مدیر سیستم بسته است
    need(not has_closed_receipt(c, P['id']), 'درخواستی که اعلام وصول دارد قابل ویرایش نیست')
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
    stage, label = start_stage(c, u, dict(P, **f))
    holder = stage_holder(c, dict(P, **f), stage)
    if is_direct_orderer(c, u, P):
        after_direct_order(c, u, P, stage)
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
    need(can_cancel(c, u, P), 'لغو فقط تا پیش از تأیید مدیر پروژه و توسط کسی که درخواست در کارتابل اوست ممکن است')
    reason = (b.get('reason') or '').strip()
    need(reason in cancel_reasons(c), 'علت لغو را انتخاب کنید', 400)
    note = (b.get('note') or '').strip()
    need(reason != 'سایر' or note, 'برای علت «سایر» توضیح لازم است', 400)
    c.execute("UPDATE purchases SET status='cancelled', stage='done', holder_id=NULL, closed_at=?, cancel_reason=?, "
              'cancel_note=? WHERE id=?', (now(), reason, note, P['id']))
    pflow(c, P['id'], u, P['stage'], 'cancel', 'لغو و بایگانی — ' + reason, note)
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/receipt')
def api_purchase_receipt(h, c, u, b, q, pid):
    """ویرایش اعلام وصول: از نسخه ۳.۵ بسته است (اعلام وصول پس از صدور قابل تغییر نیست)."""
    P = get_doc(c, u, 'purchase', int(pid))
    raise ApiError('اعلام وصول پس از صدور قابل تغییر نیست', 403)
    posted = {int(x.get('id') or 0): x for x in (b.get('items') or [])}
    ch = []
    for it in rows(c.execute("SELECT * FROM purchase_items WHERE purchase_id=? AND bought_status IN ('bought','partial') "
                             'ORDER BY row_no', (P['id'],))):
        x = posted.get(it['id'])
        if x is None:
            continue
        v = to_num(x.get('qty'))
        need(v is not None and v >= 0, 'مقدار تحویل‌گرفته «%s» باید عدد باشد' % it['title'], 400)
        if fmt_num(v) != (it['recv_qty'] or ''):
            c.execute('UPDATE purchase_items SET recv_qty=? WHERE id=?', (fmt_num(v), it['id']))
            ch.append('%s: %s ← %s' % (it['title'], it['recv_qty'] or '—', fmt_num(v)))
    note = (b.get('note') or '').strip()
    need(ch or note, 'تغییری ثبت نشد', 400)
    who = 'انباردار' if is_warehouse(c, u, P) and u['id'] != P['requester_id'] else \
        ('تحویل‌گیرنده' if u['id'] == P['requester_id'] else 'مدیر سیستم')
    pflow(c, P['id'], u, P['stage'], 'grn_edit', 'ویرایش اعلام وصول (%s)' % who, fa_num('؛ '.join(ch + ([note] if note else []))))
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/paid')
def api_purchase_paid(h, c, u, b, q, pid):
    """امور مالی پرداخت خریدی را که پشتیبانی دفتر مرکزی انجام داده ثبت می‌کند (همزمان با اعلام وصول)."""
    P = get_doc(c, u, 'purchase', int(pid))
    need(is_finance(c, u), 'فقط امور مالی')
    need(P['pay_req_at'] and not P['pay_done_at'] and P['status'] not in ('cancelled', 'rejected'),
         'این خرید منتظر پرداخت نیست')
    note = (b.get('note') or '').strip()
    c.execute('UPDATE purchases SET pay_done_at=?, pay_done_by=?, pay_note=? WHERE id=?', (now(), u['id'], note, P['id']))
    pflow(c, P['id'], u, P['stage'], 'pay_done', 'پرداخت انجام شد — امور مالی', note)
    return {'ok': True}


@route('POST', r'/api/purchases/(\d+)/official_inv')
def api_purchase_official(h, c, u, b, q, pid):
    """فاکتور رسمی (ارزش افزوده) که هنگام کنترل مدارک دریافت نشده بود، بعداً رسید."""
    P = get_doc(c, u, 'purchase', int(pid))
    need(is_finance(c, u), 'فقط امور مالی')
    need(P['docs_at'] and not P['official_inv'], 'این خرید در فهرست فاکتورهای رسمی دریافت‌نشده نیست')
    c.execute('UPDATE purchases SET official_inv=1, vat_docs=1 WHERE id=?', (P['id'],))
    pflow(c, P['id'], u, P['stage'], 'official_inv', 'فاکتور رسمی و ارزش افزوده دریافت شد — امور مالی',
          (b.get('note') or '').strip())
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
    for f in c.execute("SELECT purchase_id, stage, at FROM purchase_flow WHERE action NOT IN (%s) ORDER BY purchase_id, id"
                       % ','.join("'%s'" % a for a in SIDE_ACTIONS)):
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
    users = rows(c.execute('SELECT id,username,full_name,title,role,active,must_change,locked_at,failed_logins FROM users '
                           'WHERE deleted=0 ORDER BY id'))
    for x in users:
        x['devices'] = devs.get(x['id'], 0)
    return {'users': users, 'projects': rows(c.execute('SELECT * FROM projects WHERE deleted=0 ORDER BY id')),
            'settings': settings(c), 'custom_roles': [list(r) + [ROLE_UNIT.get(r[0])] for r in TEAM_ROLES[len(BASE_TEAM_ROLES):]],
            'backups': sorted(os.listdir(BACK))[-10:], 'https': https_status(c),
            'is_admin': u['role'] == 'admin', 'workflow_counts': workflow_counts(c)}


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


@route('POST', '/api/admin/setpw')
def api_admin_setpw(h, c, u, b, q):
    """مدیر سیستم برای کاربر (از جمله حساب بسته‌شده) رمز جدید می‌گذارد و حساب را باز می‌کند؛ کاربر در اولین ورود عوضش می‌کند."""
    need_admin(u)
    t = one(c.execute('SELECT id, full_name FROM users WHERE id=? AND deleted=0', (int(b.get('id') or 0),)))
    need(t, 'کاربر پیدا نشد', 404)
    pw = b.get('password') or ''
    need(len(pw) >= 6, 'رمز جدید حداقل ۶ کاراکتر است', 400)
    hh, s = hash_pw(pw)
    c.execute('UPDATE users SET pw_hash=?, salt=?, must_change=1, failed_logins=0, locked_at=NULL WHERE id=?', (hh, s, t['id']))
    c.execute('DELETE FROM sessions WHERE user_id=?', (t['id'],))
    log(c, 'user', t['id'], u['id'], 'رمز جدید و بازکردن حساب توسط مدیر سیستم', t['full_name'])
    return {'ok': True}


@route('POST', '/api/admin/reset')
def api_admin_reset(h, c, u, b, q):
    need(is_mgr(u))
    hh, s = hash_pw('1234')
    c.execute('UPDATE users SET pw_hash=?, salt=?, must_change=1 WHERE id=?', (hh, s, int(b['id'])))
    if u['role'] == 'admin':  # باز کردن حساب بسته‌شده فقط با مدیر سیستم
        c.execute('UPDATE users SET failed_logins=0, locked_at=NULL WHERE id=?', (int(b['id']),))
    c.execute('DELETE FROM sessions WHERE user_id=?', (int(b['id']),))
    c.execute('DELETE FROM notify_devices WHERE user_id=?', (int(b['id']),))
    return {'ok': True}


@route('POST', '/api/admin/project')
def api_admin_project(h, c, u, b, q):
    need(is_mgr(u))
    need((b.get('name') or '').strip(), 'نام پروژه الزامی است', 400)
    mid = int(b['manager_id']) if b.get('manager_id') else None
    need_not_admin(c, mid, 'مدیر پروژه')
    if b.get('id'):
        c.execute('UPDATE projects SET name=?,manager_id=?,active=? WHERE id=?',
                  (b['name'].strip(), mid, 1 if b.get('active', True) else 0, int(b['id'])))
        sync_pm(c, int(b['id']), mid)
    else:
        cur = c.execute('INSERT INTO projects(name,code,manager_id) VALUES(?,?,?)', (b['name'].strip(), b.get('code') or '', mid))
        sync_pm(c, cur.lastrowid, mid)
    return {'ok': True}


@route('POST', '/api/admin/purge_workflow')
def api_admin_purge_workflow(h, c, u, b, q):
    """پاک کردن همه مکاتبات و درخواست‌های کالا (نسخه ۴.۲). فقط مدیر سیستم، با رمز خودش و نوشتن «حذف»؛ پیش از آن
    پشتیبان کامل گرفته می‌شود. کاربران و بقیه بخش‌ها دست نمی‌خورند."""
    need_admin(u)
    need((b.get('confirm') or '').strip() == 'حذف', 'برای تأیید، کلمه «حذف» را بنویسید', 400)
    r = one(c.execute('SELECT * FROM users WHERE id=?', (u['id'],)))
    need(pw_ok(b.get('password') or '', r), 'رمز عبور شما نادرست است', 400)
    cnt, keep = purge_workflow(c, 'purge')
    log(c, 'admin', 0, u['id'], 'پاک کردن سوابق مکاتبات و درخواست‌های کالا',
        '%d نامه، %d درخواست کالا — پشتیبان: %s' % (cnt['letters'], cnt['purchases'], keep or '-'))
    return {'ok': True, 'letters': cnt['letters'], 'purchases': cnt['purchases'], 'backup': keep}


@route('POST', '/api/admin/roles')
def api_admin_roles(h, c, u, b, q):
    """تعریف، ویرایش و حذف سمت‌های کارگاه (ارکان زیرمجموعه) توسط مدیر سیستم و هیات مدیره."""
    need(is_mgr(u), 'تعریف سمت فقط توسط مدیر سیستم و هیات مدیره')
    roles = json.loads(settings(c).get('custom_roles') or '[]')
    if b.get('delete'):
        k = b['delete']
        need(any(r[0] == k for r in roles), 'سمت پیدا نشد', 404)
        need(not c.execute('SELECT 1 FROM project_team WHERE role_key=?', (k,)).fetchone(),
             'این سمت در پروژه‌ها نفر دارد؛ اول نفرات را در صفحه پروژه از این سمت بردارید', 400)
        roles = [r for r in roles if r[0] != k]
    else:
        title = (b.get('title') or '').strip()
        need(title, 'عنوان سمت را بنویسید', 400)
        need(b.get('superior') in dict(PROJECT_ROLES), 'بالادست سمت را انتخاب کنید', 400)
        need(b.get('unit') in dict(UNITS), 'واحد سمت را انتخاب کنید', 400)
        names = {t for k, t, _ in TEAM_ROLES if k != b.get('key')} | {t for _, t in PROJECT_ROLES}
        need(title not in names, 'سمتی با این عنوان وجود دارد', 400)
        if b.get('key'):
            need(any(r[0] == b['key'] for r in roles), 'سمت پیدا نشد', 404)
            roles = [[r[0], title, b['superior'], b['unit']] if r[0] == b['key'] else r for r in roles]
        else:
            n = max([int(r[0][1:]) for r in roles if r[0][1:].isdigit()] or [0]) + 1
            roles.append(['c%d' % n, title, b['superior'], b['unit']])
    c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('custom_roles',?)", (json.dumps(roles, ensure_ascii=False),))
    apply_custom_roles(c)
    log(c, 'admin', 0, u['id'], 'تعریف سمت‌های کارگاه', json.dumps(roles, ensure_ascii=False))
    return {'ok': True, 'roles': roles}


@route('POST', '/api/admin/settings')
def api_admin_settings(h, c, u, b, q):
    need(is_mgr(u))
    for k in ('idle_minutes', 'notify_interval', 'https_port'):
        if k in b:
            need(str(b[k]).isdigit(), 'عدد نامعتبر در تنظیمات', 400)
            need(k != 'notify_interval' or int(b[k]) >= 15, 'فاصله بررسی اعلان حداقل ۱۵ ثانیه است', 400)
    for k in HQ_SETTING_USERS:
        if b.get(k):
            need_not_admin(c, b[k], HQ_ROLES.get(k) or 'سمت گردش کار')
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
    # ۴.۶: درخواست دارای اعلام وصول هم با برگه اعلام وصولش حذف می‌شود
    c.execute('DELETE FROM referrals WHERE doc_type=? AND doc_id=?', (dt, did))
    c.execute('DELETE FROM attachments WHERE doc_type=? AND doc_id=?', (dt, did))  # فایل‌ها در پوشه data\files می‌مانند
    if dt == 'purchase':
        for t in ('purchase_items', 'purchase_flow', 'purchase_versions', 'purchase_payments'):
            c.execute('DELETE FROM %s WHERE purchase_id=?' % t, (did,))
        c.execute('DELETE FROM purchase_receipt_lines WHERE receipt_id IN (SELECT id FROM purchase_receipts WHERE purchase_id=?)', (did,))
        c.execute('DELETE FROM purchase_receipts WHERE purchase_id=?', (did,))
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
    """گواهی و کلید HTTPS در data\\https: یا cert.pem و key.pem (بارگذاری در مدیریت سامانه)، یا فایل‌های
    Let's Encrypt که برنامه win-acme با گزینه PEM در همین پوشه می‌سازد (‎*-chain.pem و ‎*-key.pem)؛ جدیدترین به کار می‌رود."""
    cert, key = os.path.join(HTTPS_DIR, 'cert.pem'), os.path.join(HTTPS_DIR, 'key.pem')
    try:
        names = os.listdir(HTTPS_DIR)
    except OSError:
        return cert, key
    newest = lambda fs: max(fs, key=os.path.getmtime) if fs else None
    le_cert = newest([os.path.join(HTTPS_DIR, n) for n in names if n.endswith('-chain.pem') and not n.endswith('-chain-only.pem')])
    le_key = newest([os.path.join(HTTPS_DIR, n) for n in names if n.endswith('-key.pem')])
    if le_cert and le_key and (not os.path.exists(cert) or os.path.getmtime(le_cert) >= os.path.getmtime(cert)):
        return le_cert, le_key
    return cert, key


def https_status(c):
    cert, key = https_files()
    info = {'cert': os.path.exists(cert), 'key': os.path.exists(key), 'port': int(settings(c).get('https_port') or 8443),
            'running': HTTPS_RUNNING[0], 'file': os.path.basename(cert) if os.path.exists(cert) else ''}
    if info['cert']:  # تاریخ انقضا و نام دامنه گواهی (برای نمایش در مدیریت سامانه)
        try:
            d = ssl._ssl._test_decode_cert(cert)
            info['until'] = d.get('notAfter', '')
            info['names'] = [v for k, v in d.get('subjectAltName', ()) if k == 'DNS'] or \
                [v for t in d.get('subject', ()) for k, v in t if k == 'commonName']
        except Exception:
            pass
    return info


HTTPS_RUNNING = [0]  # پورت HTTPS در حال اجرا (۰ = خاموش)
HTTPS_CTX = [None, 0]  # [زمینه SSL در حال اجرا، زمان آخرین بارگذاری گواهی]


def https_reload():
    """گواهی تمدیدشده بدون راه‌اندازی مجدد سرور به کار می‌رود (اتصال‌های جدید با گواهی جدید)."""
    ctx = HTTPS_CTX[0]
    if not ctx:
        return
    cert, key = https_files()
    try:
        mt = max(os.path.getmtime(cert), os.path.getmtime(key))
        if mt > HTTPS_CTX[1]:
            ctx.load_cert_chain(cert, key)
            HTTPS_CTX[1] = mt
            print('گواهی HTTPS دوباره بارگذاری شد')
    except (OSError, ssl.SSLError) as e:
        print('بارگذاری دوباره گواهی HTTPS ناموفق بود:', e)


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
    https_reload()
    return {'ok': True, 'status': https_status(c)}


@route('POST', '/api/admin/https/delete')
def api_admin_https_delete(h, c, u, b, q):
    need_admin(u)
    for p in https_files():
        if os.path.exists(p):
            os.remove(p)
    return {'ok': True}


# آیکن برنامه (آرم C&E) برای صفحه اصلی گوشی، اعلان‌ها و زبانه مرورگر؛ داخل برنامه است و فایل جدا لازم ندارد
ICON = base64.b64decode("""
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""")
FAVICON = base64.b64decode("""
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""")
OLD_PAGE = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>به‌روزرسانی ناقص</title></head><body style="font-family:Tahoma,sans-serif;background:#f4f5f7;padding:20px;line-height:2.2">
<div style="max-width:640px;margin:8vh auto;background:#fff;border:2px solid #b42318;border-radius:10px;padding:18px 22px">
<h2 style="color:#b42318;margin-top:0">فایل index.html با برنامه سرور هم‌نسخه نیست</h2>
برنامه‌ای که الان روی سرور <b>در حال اجراست</b> نسخه <b dir="ltr">%(srv)s</b> است، ولی فایل صفحه (index.html) نسخه <b dir="ltr">%(page)s</b> است.<br>
%(steps)s</div></body></html>"""
OLD_STEPS_PAGE_NEWER = """<b>راه‌حل:</b> فایل <b>app.py</b> نسخه %(page)s را هم کنار index.html (در همان پوشه) بگذارید و جایگزین کنید، سپس روی کامپیوتر سرور فایل <b>4-restart.bat</b> را با «Run as administrator» اجرا کنید (یا کامپیوتر سرور را یک بار ری‌استارت کنید). <b>تا سرور دوباره راه‌اندازی نشود، همین پیام می‌ماند</b>، حتی اگر app.py جدید را جایگزین کرده باشید.<br>
بعد از راه‌اندازی، این صفحه را با <b>Ctrl+F5</b> دوباره باز کنید."""
OLD_STEPS_PAGE_OLDER = """<b>راه‌حل:</b> فایل <b>index.html</b> نسخه %(srv)s را در همان پوشه‌ای که app.py هست جایگزین کنید و این صفحه را با <b>Ctrl+F5</b> دوباره باز کنید (نیازی به راه‌اندازی مجدد سرور نیست)."""


def version_page(page_bytes):
    """صفحه راهنمای ناهم‌نسخه‌بودن app.py (در حال اجرا) و index.html؛ جهت ناهم‌خوانی تشخیص داده می‌شود."""
    m = re.search(rb"PAGE_VERSION='([^']*)'", page_bytes)
    pv = m.group(1).decode() if m else '؟'
    key = lambda v: tuple(int(x) if x.isdigit() else 0 for x in v.split('.'))
    newer = m is not None and key(pv) > key(VERSION)
    d = {'srv': VERSION, 'page': pv}
    d['steps'] = (OLD_STEPS_PAGE_NEWER if newer or not m else OLD_STEPS_PAGE_OLDER) % d
    return (OLD_PAGE % d)
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
                return self.send(200, version_page(page).encode('utf-8'), 'text/html; charset=utf-8')
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
            return self.send(200, ICON, 'image/png')
        if method == 'GET' and path in ('/favicon.png', '/favicon.ico'):
            return self.send(200, FAVICON, 'image/png')
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
                data, ct, hdr = file_payload(c, u, int(m.group(1)), q)
                return self.send(200, data, ct, hdr)
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
        c.execute("UPDATE users SET pw_hash=?, salt=?, must_change=1, active=1, failed_logins=0, locked_at=NULL "
                  "WHERE username='admin'", (hh, ss))
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
    HTTPS_CTX[0], HTTPS_CTX[1] = ctx, max(os.path.getmtime(cert), os.path.getmtime(key))
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def watch():  # تمدید خودکار گواهی (مثلاً win-acme هر ۶۰ روز)
        while True:
            time.sleep(600)
            https_reload()
    threading.Thread(target=watch, daemon=True).start()
    print('HTTPS هم روی پورت %d فعال است  —  آدرس برای گوشی: https://<نام یا IP این کامپیوتر>:%d' % (port, port))


if __name__ == '__main__':
    main()
