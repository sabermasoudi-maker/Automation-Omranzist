# -*- coding: utf-8 -*-
"""آزمون‌های CR-PUR-02: اعلام وصول (مقدار جدا، کسری/اضافه، تحویل بخشی، تاریخ و حواله، قفل) و امنیت پیوست.
از نسخه ۳.۶ «تحویل بخشی» با انباردار است و تحویل‌گیرنده فقط تأیید نهایی دارد.
اجرا:  python -m unittest discover -s tests"""
import os, sys, shutil, tempfile, unittest, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app  # noqa: E402


class H:
    """جایگزین ساده‌ی هندلر HTTP برای صدا زدن مستقیم توابع مسیر."""
    def __init__(self, headers=None, raw=b''):
        self.headers, self.raw_body, self.token = headers or {}, raw, 'test'
        self.client_address, self.set_cookie = ('127.0.0.1', 0), None


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        app.DATA = self.tmp
        app.DB = os.path.join(self.tmp, 'oa.db')
        app.FILES = os.path.join(self.tmp, 'files')
        app.BACK = os.path.join(self.tmp, 'backups')
        app.init_db()
        self.c = app.db()

    def tearDown(self):
        self.c.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def u(self, un):
        return dict(app.one(self.c.execute('SELECT * FROM users WHERE username=?', (un,))))

    def call(self, method, path, un, body=None, q=None, h=None):
        for meth, rx, fn in app.ROUTES:
            m = rx.match(path)
            if meth == method and m:
                try:
                    r = fn(h or H(), self.c, self.u(un), body or {}, q or {}, *m.groups())
                    self.c.commit()
                    return r
                except app.ApiError:
                    self.c.rollback()
                    raise
        raise AssertionError('مسیر پیدا نشد: ' + path)

    def act(self, un, pid, action, **kw):
        return self.call('POST', '/api/purchases/%d/act' % pid, un, dict(action=action, **kw))

    def get(self, pid, un='admin'):
        return self.call('GET', '/api/purchases/%d' % pid, un)

    def attach(self, un, pid, kind, name='f.jpg'):
        h = H({'X-Filename': urllib.parse.quote(name), 'X-Kind': urllib.parse.quote(kind)}, b'data')
        return self.call('POST', '/api/attach', un, q={'doc_type': 'purchase', 'doc_id': str(pid)}, h=h)

    def to_delivery(self):
        """درخواست «خرید در کارگاه» با دو قلم (۱۰ عدد و ۵ کیسه) تا مرحله اعلام وصول."""
        r = self.call('POST', '/api/purchases', 'mk-zali', {
            'project_id': 2, 'unit': 'exec', 'category': 'general', 'warehouse': 'انبار', 'purpose': 'آزمون',
            'need_date': '1410/01/01', 'items': [{'title': 'پیچ', 'qty': '10', 'unit': 'عدد'},
                                                  {'title': 'سیمان', 'qty': '5', 'unit': 'کیسه'}]})
        pid = r['id']
        self.act('mk-zali', pid, 'submit')
        self.act('mk-sarparast', pid, 'approve')  # از نسخه ۴.۰ مستقیم سرپرست کارگاه
        self.attach('mk-poshtibani', pid, 'فاکتور', 'inv.pdf')
        self.buy(pid, 10, 5)
        self.assertEqual(self.get(pid)['doc']['stage'], 'delivery')
        return pid

    def buy(self, pid, q1, q2):
        its = self.get(pid)['items']
        return self.act('mk-poshtibani', pid, 'purchased', items=[
            {'id': its[0]['id'], 'qty': str(q1), 'unit': 'عدد', 'status': 'bought'},
            {'id': its[1]['id'], 'qty': str(q2), 'unit': 'کیسه', 'status': 'bought'}])

    def lines(self, pid, q1, q2, s1=None, s2=None, n1='', n2=''):
        """اقلام اعلام وصول؛ s1 و s2 وضعیت هر قلم (ok، short، extra، returned، partial) و n1 و n2 توضیح."""
        its = self.get(pid)['items']
        a, b = dict(id=its[0]['id'], qty=str(q1), note=n1), dict(id=its[1]['id'], qty=str(q2), note=n2)
        if s1: a['status'] = s1
        if s2: b['status'] = s2
        return [a, b]

    def receipts(self, pid):
        return self.get(pid)['receipts']


class T00Files(Base):
    def _file(self, pid, hq_only, deleted=False):
        os.makedirs(app.FILES, exist_ok=True)
        with open(os.path.join(app.FILES, 'x.pdf'), 'wb') as f:
            f.write(b'PDF')
        return self.c.execute('INSERT INTO attachments(doc_type,doc_id,name,path,size,uploaded_by,created_at,hq_only,deleted_at) '
                              "VALUES('purchase',?,'x.pdf','x.pdf',3,1,?,?,?)",
                              (pid, app.now(), hq_only, app.now() if deleted else None)).lastrowid

    def test_00a_site_user_blocked(self):
        pid = self.to_delivery(); aid = self._file(pid, 1)
        with self.assertRaises(app.ApiError) as e:
            app.file_payload(self.c, self.u('mk-zali'), aid, {})
        self.assertEqual(e.exception.code, 403)

    def test_00b_hq_user_gets_file(self):
        pid = self.to_delivery(); aid = self._file(pid, 1)
        data, ct, hdr = app.file_payload(self.c, self.u('support'), aid, {})
        self.assertEqual(data, b'PDF')

    def test_00c_deleted_hidden(self):
        pid = self.to_delivery(); aid = self._file(pid, 0, deleted=True)
        with self.assertRaises(app.ApiError) as e:
            app.file_payload(self.c, self.u('mk-zali'), aid, {})
        self.assertEqual(e.exception.code, 404)


class T06to10Receipt(Base):
    def test_06_separate_quantities(self):
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 10, 5))
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 10, 5))
        d = self.get(pid); R = d['receipts'][0]
        self.assertEqual(R['status'], 'closed'); self.assertEqual(R['code'], d['doc']['grn_no'])
        self.assertEqual([(l['wh_qty'], l['recv_qty']) for l in R['lines']], [('10', '10'), ('5', '5')])
        self.assertEqual(d['doc']['stage'], 'done')

    def test_07a_default_status(self):
        """بدون انتخاب وضعیت، مقدار کمتر «تأیید» است (بقیه بعداً) و مقدار بیشتر «اضافی» (۴.۶: توضیح اختیاری)."""
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 12, 5))
        self.assertEqual(self.receipts(pid)[0]['lines'][0]['wh_status'], 'extra')
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 8, 5))
        self.assertEqual(self.receipts(pid)[0]['lines'][0]['wh_status'], 'ok')
        self.assertEqual(self.get(pid, 'mk-anbar')['items'][0]['remaining_wh'], '2')

    def test_07b_status_rules(self):
        pid = self.to_delivery()
        for st, q, n in (('ok', 12, ''), ('extra', 8, 'x'), ('short', 12, 'x'), ('short', 10, 'x'),
                         ('wrong', 8, ''), ('wrong', 0, 'x'), ('returned', 8, 'x'), ('partial', 12, ''), ('partial', 10, '')):
            with self.assertRaises(app.ApiError, msg=st) as e:
                self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, q, 5, s1=st, n1=n))
            self.assertEqual(e.exception.code, 400)
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 8, 5, s1='short', n1='دو عدد کم بود'))
        ln = self.receipts(pid)[0]['lines'][0]
        self.assertEqual((ln['wh_status'], ln['wh_qty'], ln['wh_note'], ln['exp_qty']), ('short', '8', 'دو عدد کم بود', '10'))

    def test_07b2_ok_with_less_and_wrong(self):
        """«تأیید» با مقدار کمتر مجاز است؛ «اشتباه ارسال شده» می‌تواند همه کالا باشد و از باقی‌مانده انبار کم نمی‌شود."""
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 10, 3, s1='wrong', s2='ok', n1='مشخصات فنی رعایت نشده'))
        d = self.get(pid, 'mk-anbar')
        self.assertEqual([i['remaining_wh'] for i in d['items']], ['10', '2'])
        self.assertIn('wh_ok', d['actions'])
        with self.assertRaises(app.ApiError):  # همه صفر
            self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 0, 0))

    def test_07c_partial_cannot_exceed(self):
        pid = self.to_delivery()
        with self.assertRaises(app.ApiError) as e:
            self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 12, 5, s1='partial'))
        self.assertEqual(e.exception.code, 400)

    def test_07d_receiver_sees_same_column_and_has_no_other_buttons(self):
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 10, 5))
        self.assertEqual(self.get(pid, 'mk-zali')['actions'], ['recv_ok'])
        self.assertEqual(self.get(pid, 'mk-anbar')['actions'], [])  # همه ثبت شد
        with self.assertRaises(app.ApiError) as e:  # بیشتر از مورد انتظار ولی «تأیید» زده شده
            self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 11, 5, s1='ok'))
        self.assertEqual(e.exception.code, 400)

    def test_07e_receiver_resolves_warehouse_shortage(self):
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 8, 5, s1='short', n1='کم بود'))
        d = self.get(pid, 'mk-zali'); self.assertEqual(d['receipts'][0]['lines'][0]['wh_status'], 'short')
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 10, 5, s1='ok'))  # کالا را کامل تحویل گرفت
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], d['receipts'][0]['status']), ('done', 'closed'))
        self.assertEqual([(l['wh_qty'], l['wh_status'], l['recv_qty'], l['recv_status']) for l in d['receipts'][0]['lines']],
                         [('8', 'short', '10', 'ok'), ('5', 'ok', '5', 'ok')])

    def disc_to_support(self, st, q, n2=5, s2=None):
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, q, n2, s1=st, s2=s2, n1='علت', n2='علت' if s2 else ''))
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, q, n2, s1=st, s2=s2, n1='تأیید می‌کنم', n2='تأیید' if s2 else ''))
        return pid

    def decide(self, pid, *decs, un='mk-poshtibani'):
        ls = self.get(pid, un)['disc_lines']
        return self.act(un, pid, 'decide', decisions=[dict(line=l['id'], dec=d, note=n) for l, (d, n) in zip(
            [l for l in ls if not l['sup_dec']], decs)])

    def test_07f_receiver_confirms_discrepancy_to_support(self):
        """۴.۵: تأیید تحویل‌گیرنده نوبت را در برگه ثبت می‌کند و مغایرت برای تصمیم به پشتیبانی می‌رود."""
        for st, q, acc in (('short', 8, '8'), ('extra', 12, '10'), ('wrong', 7, '0')):
            pid = self.disc_to_support(st, q)
            d = self.get(pid)
            self.assertEqual((d['doc']['stage'], d['receipts'][0]['status']), ('disc_review', 'closed'), st)
            self.assertEqual(d['doc']['grn_no'], d['receipts'][0]['code'])
            self.assertFalse(d['sheet_final'])
            self.assertEqual([i['accepted'] for i in d['items']], [acc, '5'], st)
            self.assertEqual(d['doc']['holder_id'], self.u('mk-poshtibani')['id'])
            self.assertEqual(self.get(pid, 'mk-poshtibani')['actions'], ['decide'])
            self.assertEqual(list(d['disc_lines'][0]['options']), list(app.DECISIONS[st]))

    def test_07g_extra_accept_and_return(self):
        pid = self.disc_to_support('extra', 12)
        with self.assertRaises(app.ApiError):  # تصمیم نامعتبر
            self.decide(pid, ('accept_short', ''))
        self.decide(pid, ('accept_extra', 'لازم داریم'))
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], d['doc']['holder_id']), ('delivery', self.u('mk-anbar')['id']))
        self.assertEqual(self.get(pid, 'mk-anbar')['actions'], ['wh_fix'])
        self.assertEqual((d['items'][0]['bought_qty'], d['items'][0]['accepted']), ('12', '12'))
        self.act('mk-anbar', pid, 'wh_fix')
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], d['sheet_final']), ('done', True))
        # مرجوع کردن اضافه
        pid = self.disc_to_support('extra', 12)
        self.decide(pid, ('return_extra', ''))
        self.act('mk-anbar', pid, 'wh_fix', note='۲ عدد برگشت داده شد')
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], d['items'][0]['bought_qty'], d['items'][0]['accepted']), ('done', '10', '10'))
        self.assertEqual(len([r for r in d['receipts'] if r['status'] == 'closed']), 1)  # یک برگه

    def test_07h_short_accept_and_send(self):
        pid = self.disc_to_support('short', 8)
        self.decide(pid, ('accept_short', 'فروشنده موجودی ندارد'))
        self.act('mk-anbar', pid, 'wh_fix')
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], d['items'][0]['bought_qty']), ('done', '8'))
        # ارسال کسری: باقی‌مانده در همان برگه ثبت می‌شود
        pid = self.disc_to_support('short', 8)
        self.decide(pid, ('send_short', ''))
        self.act('mk-anbar', pid, 'wh_fix')
        d = self.get(pid, 'mk-anbar')
        self.assertEqual((d['doc']['stage'], d['actions'], d['items'][0]['remaining_wh']), ('delivery', ['wh_ok', 'followup'], '2'))
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 2, 0)[:1])
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 2, 0)[:1])
        d = self.get(pid); g = d['doc']['grn_no']
        self.assertEqual((d['doc']['stage'], [r['code'] for r in d['receipts']], d['items'][0]['accepted']), ('done', [g, g], '10'))

    def test_07i_wrong_return_and_keep(self):
        pid = self.disc_to_support('wrong', 10, 5)
        with self.assertRaises(app.ApiError):  # عدم امکان مرجوعی بدون علت
            self.decide(pid, ('keep_wrong', ''))
        self.decide(pid, ('return_wrong', ''))
        self.act('mk-anbar', pid, 'wh_fix')
        d = self.get(pid, 'mk-anbar')
        self.assertEqual((d['doc']['stage'], d['items'][0]['remaining_wh'], d['sheet_final']), ('delivery', '10', False))
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 10, 0)[:1])  # جایگزین رسید
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 10, 0)[:1])
        self.assertEqual(self.get(pid)['doc']['stage'], 'done')
        pid = self.disc_to_support('wrong', 10, 5)
        self.decide(pid, ('keep_wrong', 'فروشنده پس نمی‌گیرد'))
        self.act('mk-anbar', pid, 'wh_fix')
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], d['items'][0]['accepted']), ('done', '10'))
        self.assertTrue(any('امکان مرجوعی نیست' in n for n in d['items'][0]['rcpt_notes']))

    def test_07k_per_item_decisions(self):
        """۴.۶: پشتیبانی برای هر قلم جداگانه تصمیم می‌گیرد و انباردار هم‌زمان قلم تصمیم‌گرفته را اصلاح می‌کند."""
        pid = self.disc_to_support('extra', 12, 4, 'short')
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], len(d['disc_lines'])), ('disc_review', 2))
        self.decide(pid, ('return_extra', ''))  # فقط قلم اول
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], d['doc']['holder_id']), ('disc_review', self.u('mk-poshtibani')['id']))
        self.assertEqual(self.get(pid, 'mk-poshtibani')['actions'], ['decide'])
        self.assertEqual(self.get(pid, 'mk-anbar')['actions'], ['wh_fix'])
        self.assertIn(pid, [x['id'] for x in self.call('GET', '/api/cartable', 'mk-anbar')['pur_held']])
        self.act('mk-anbar', pid, 'wh_fix')
        self.assertEqual(self.get(pid, 'mk-anbar')['actions'], [])
        self.decide(pid, ('accept_short', ''))  # آخرین قلم: بازگشت به اعلام وصول
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], d['doc']['holder_id']), ('delivery', self.u('mk-anbar')['id']))
        self.act('mk-anbar', pid, 'wh_fix')
        self.assertEqual(self.get(pid)['doc']['stage'], 'done')

    def test_07j_five_cases_one_sheet(self):
        """پنج حالت در یک برگه: درست، اضافی، کسری، بخشی و اشتباه (پنج قلم)."""
        r = self.call('POST', '/api/purchases', 'mk-zali', {
            'project_id': 2, 'unit': 'exec', 'category': 'general', 'warehouse': 'انبار', 'purpose': 'آزمون',
            'need_date': '1410/01/01', 'items': [{'title': t, 'qty': '10', 'unit': 'عدد'} for t in 'الف ب پ ت ث'.split()]})
        pid = r['id']
        self.act('mk-zali', pid, 'submit'); self.act('mk-sarparast', pid, 'approve')
        self.attach('mk-poshtibani', pid, 'فاکتور', 'inv.pdf')
        its = self.get(pid)['items']
        self.act('mk-poshtibani', pid, 'purchased', items=[dict(id=i['id'], qty='10', unit='عدد', status='bought') for i in its])
        spec = [('ok', 10), ('extra', 12), ('short', 8), ('partial', 6), ('wrong', 10)]
        L = [dict(id=i['id'], qty=str(q), status=st, note='توضیح') for i, (st, q) in zip(its, spec)]
        self.act('mk-anbar', pid, 'wh_ok', items=L)
        self.act('mk-zali', pid, 'recv_ok', items=L)
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], len(d['disc_lines'])), ('disc_review', 3))
        self.assertEqual([i['accepted'] for i in d['items']], ['10', '10', '8', '6', '0'])
        self.decide(pid, ('return_extra', ''), ('send_short', ''), ('return_wrong', ''))
        self.act('mk-anbar', pid, 'wh_fix')
        d = self.get(pid, 'mk-anbar')
        self.assertEqual([i['remaining_wh'] for i in d['items']], ['0', '0', '2', '4', '10'])
        L = [dict(id=i['id'], qty=q) for i, q in zip(its[2:], ('2', '4', '10'))]
        self.act('mk-anbar', pid, 'wh_ok', items=L)
        self.act('mk-zali', pid, 'recv_ok', items=L)
        d = self.get(pid)
        self.assertEqual((d['doc']['stage'], d['sheet_final']), ('done', True))
        self.assertEqual([i['accepted'] for i in d['items']], ['10'] * 5)
        self.assertEqual({r['code'] for r in d['receipts']}, {d['doc']['grn_no']})

    def test_08a_partial(self):
        self.partial()

    def partial(self):
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 6, 5, s1='partial'))
        # ۳.۷: باقی‌مانده نزد انباردار می‌ماند و تحویل‌گیرنده هم‌زمان نوبت ثبت‌شده را تأیید می‌کند
        self.assertEqual(self.get(pid)['doc']['holder_id'], self.u('mk-anbar')['id'])
        self.assertIn('recv_ok', self.get(pid, 'mk-zali')['actions'])
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 6, 5, s1='partial'))  # نظر نهایی: تحویل بخشی
        d = self.get(pid)
        self.assertEqual(d['receipts'][0]['status'], 'closed'); self.assertEqual(d['doc']['stage'], 'delivery')
        self.assertEqual(d['doc']['holder_id'], self.u('mk-anbar')['id'])
        self.assertEqual([i['remaining_receive'] for i in d['items']], ['4', '0'])
        return pid

    def test_08e_warehouse_records_rest_before_receiver(self):
        """۱۰۰۰ عدد خریداری شده؛ ۳۰۰ رسید و پیش از تأیید تحویل‌گیرنده ۷۰۰ دیگر هم رسید (نسخه ۳.۷)."""
        pid = self.to_delivery()
        its = self.get(pid)['items']
        self.c.execute("UPDATE purchase_items SET bought_qty='1000' WHERE id=?", (its[0]['id'],)); self.c.commit()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 300, 5, s1='partial'))
        self.assertEqual(self.get(pid, 'mk-anbar')['items'][0]['remaining_wh'], '700')
        self.assertIn('wh_ok', self.get(pid, 'mk-anbar')['actions'])  # بدون انتظار برای تحویل‌گیرنده
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 700, 0)[:1])
        d = self.get(pid)
        self.assertEqual([r['status'] for r in d['receipts']], ['pending', 'pending'])
        self.assertEqual(d['doc']['holder_id'], self.u('mk-zali')['id'])  # انبار کارش تمام شد
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 300, 5, s1='partial'))   # نوبت ۱
        self.assertEqual(self.get(pid)['doc']['stage'], 'delivery')
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 700, 0)[:1])  # نوبت ۲
        d = self.get(pid); g = d['doc']['grn_no']
        self.assertEqual([r['code'] for r in d['receipts']], [g, g])
        self.assertEqual([i['recv_qty'] for i in d['items']], ['1000', '5'])
        self.assertEqual(d['doc']['stage'], 'done')

    def test_08b_second_receipt(self):
        pid = self.partial()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 4, 0)[:1])
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 4, 0)[:1])
        d = self.get(pid); g = d['doc']['grn_no']
        self.assertEqual([r['code'] for r in d['receipts']], [g, g])
        self.assertEqual([i['recv_qty'] for i in d['items']], ['10', '5'])
        self.assertEqual(d['doc']['stage'], 'done')

    def test_08c_shortage_closed_by_support(self):
        pid = self.partial()
        first = [(l['wh_qty'], l['recv_qty']) for l in self.receipts(pid)[0]['lines']]
        with self.assertRaises(app.ApiError):  # پیگیری بدون توضیح
            self.act('mk-anbar', pid, 'followup')
        self.act('mk-anbar', pid, 'followup', note='فروشنده بقیه را نمی‌فرستد')  # فقط وقتی پیگیری لازم است
        self.assertEqual(self.get(pid)['doc']['stage'], 'site_purchase')
        self.buy(pid, 6, 5)
        d = self.get(pid)
        self.assertEqual(d['doc']['stage'], 'done')
        self.assertEqual([(l['wh_qty'], l['recv_qty']) for l in d['receipts'][0]['lines']], first)
        self.assertEqual(len([r for r in d['receipts'] if r['status'] == 'closed']), 1)

    def test_08d_bought_below_received(self):
        pid = self.partial()
        self.act('mk-anbar', pid, 'followup', note='پیگیری')
        with self.assertRaises(app.ApiError) as e:
            self.buy(pid, 5, 5)
        self.assertEqual(e.exception.code, 400)

    def test_09_date_and_ref(self):
        pid = self.to_delivery()
        y, m, d = map(int, app.jtoday().split('/'))
        tomorrow = '%04d/%02d/%02d' % (y + 1, m, d)  # هر تاریخ پس از امروز
        with self.assertRaises(app.ApiError) as e:
            self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 10, 5), delivery_date=tomorrow)
        self.assertEqual(e.exception.code, 400)
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 10, 5), delivery_date=app.jtoday(), delivery_ref='۱۲۳')
        R = self.receipts(pid)[0]
        self.assertEqual(R['delivery_date'], app.jtoday()); self.assertEqual(R['delivery_ref'], '۱۲۳')

    def _done(self):
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 10, 5))
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 10, 5))
        return pid

    def test_10a_receipt_edit_locked(self):
        pid = self._done()
        with self.assertRaises(app.ApiError) as e:
            self.call('POST', '/api/purchases/%d/receipt' % pid, 'admin', {'items': []})
        self.assertEqual(e.exception.code, 403)

    def test_10b_edit_locked_for_admin(self):
        pid = self._done()
        before = self.get(pid)['items']
        with self.assertRaises(app.ApiError):
            self.call('POST', '/api/purchases/%d/edit' % pid, 'admin', {
                'unit': 'exec', 'category': 'general', 'purpose': 'x', 'need_date': '1410/01/01',
                'items': [{'title': 'دیگر', 'qty': '1', 'unit': 'عدد'}]})
        self.assertEqual([i['id'] for i in self.get(pid)['items']], [i['id'] for i in before])

    def test_10c_admin_deletes_with_receipt(self):
        """۴.۶: مدیر سیستم درخواست دارای اعلام وصول را هم کامل حذف می‌کند."""
        pid = self._done()
        with self.assertRaises(app.ApiError):  # فقط مدیر سیستم
            self.call('POST', '/api/admin/docs/purchase/%d/delete' % pid, 'mk-zali')
        self.call('POST', '/api/admin/docs/purchase/%d/delete' % pid, 'admin')
        self.assertIsNone(app.one(self.c.execute('SELECT id FROM purchases WHERE id=?', (pid,))))
        self.assertIsNone(app.one(self.c.execute('SELECT id FROM purchase_receipts WHERE purchase_id=?', (pid,))))

    def test_10d_notes_optional_and_zero_rows_stay(self):
        """۴.۶: توضیح کسری و اضافی اختیاری است؛ قلم با مقدار صفر نزد انباردار می‌ماند و به تحویل‌گیرنده نمی‌رود."""
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 8, 0, s1='short'))
        R = self.receipts(pid)[0]
        self.assertEqual([l['title'] for l in R['lines']], ['پیچ'])
        d = self.get(pid, 'mk-anbar')
        self.assertEqual(([i['remaining_wh'] for i in d['items']], d['actions']), (['2', '5'], ['wh_ok', 'followup']))
        self.assertEqual(self.get(pid, 'mk-zali')['actions'], ['recv_ok'])
        self.assertIn(pid, [x['id'] for x in self.call('GET', '/api/cartable', 'mk-zali')['pur_held']])
        # تحویل‌گیرنده موافق با «اشتباه ارسال شده» انباردار، بدون تکرار توضیح
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 10, 5, s1='wrong', n1='سایز اشتباه'))
        with self.assertRaises(app.ApiError):  # وضعیت دیگر از انباردار نیست؛ توضیح خودش لازم است
            self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 4, 5, s1='wrong', s2='wrong'))
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 10, 5, s1='wrong'))
        self.assertEqual(self.get(pid)['doc']['stage'], 'disc_review')


class THandover(Base):
    def test_handover_carries_through(self):
        """کار واگذارشده به پشتیبانی کارگاه، در مرحله خرید دفتر مرکزی هم با او می‌ماند (نسخه ۳.۶)."""
        r = self.call('POST', '/api/purchases', 'mk-zali', {
            'project_id': 2, 'unit': 'exec', 'category': 'main', 'warehouse': 'انبار', 'purpose': 'آزمون',
            'need_date': '1410/01/01', 'items': [{'title': 'پیچ', 'qty': '10', 'unit': 'عدد'}]})
        pid = r['id']
        for un, a in (('mk-zali', 'submit'), ('mk-sarparast', 'approve'), ('kasaeian', 'approve')):
            self.act(un, pid, a)
        self.assertEqual(self.get(pid)['doc']['stage'], 'hq_quotes')
        self.call('POST', '/api/purchases/%d/handover' % pid, 'support', {'note': 'لطفاً شما'})
        site = self.u('mk-poshtibani')['id']
        d = self.get(pid)['doc']
        self.assertEqual((d['holder_id'], d['support_side']), (site, 'site'))
        hq = self.call('GET', '/api/purchases', 'admin', q={'status': 'open', 'support': 'hq'})
        self.assertNotIn(pid, [x['id'] for x in hq])
        self.attach('mk-poshtibani', pid, 'پیش‌فاکتور', 'pf.pdf')
        self.act('mk-poshtibani', pid, 'quoted')
        self.act('kasaeian', pid, 'approve')
        self.assertEqual(self.get(pid)['doc']['holder_id'], site)  # خرید هم با پشتیبانی کارگاه


class TShortFlow(Base):
    """نسخه ۴.۰: مسیر کوتاه؛ مستقیم به سرپرست کارگاه، ارجاع سرپرست، بدون معاون فنی."""
    def new(self, cat, un='mk-zali', unit='exec'):
        pid = self.call('POST', '/api/purchases', un, {
            'project_id': 2, 'unit': unit, 'category': cat, 'warehouse': 'انبار', 'purpose': 'آزمون',
            'need_date': '1410/01/01', 'items': [{'title': 'پیچ', 'qty': '10', 'unit': 'عدد'}]})['id']
        self.act(un, pid, 'submit')
        return pid

    def test_goes_straight_to_supervisor(self):
        sup = self.u('mk-sarparast')['id']
        for cat, unit, un in (('general', 'exec', 'mk-zali'), ('main', 'tech', 'mk-bajelani'), ('main', 'exec', 'mk-ejraei')):
            d = self.get(self.new(cat, un, unit))['doc']
            self.assertEqual((d['stage'], d['holder_id']), ('supervisor_approve', sup))

    def test_main_skips_tech_review(self):
        pid = self.new('main', 'mk-bajelani', 'tech')
        self.act('mk-sarparast', pid, 'approve')
        self.assertEqual(self.get(pid)['doc']['stage'], 'pm_approve')

    def test_supervisor_own_request_is_self_approved(self):
        d = self.get(self.new('general', 'mk-sarparast'))['doc']
        self.assertEqual(d['stage'], 'site_purchase')

    def test_refer_and_return(self):
        pid = self.new('main')
        with self.assertRaises(app.ApiError):  # ارجاع بدون توضیح یا شخص
            self.act('mk-sarparast', pid, 'refer', refer_to=self.u('mk-fanni')['id'])
        with self.assertRaises(app.ApiError):  # ارجاع به مدیر سیستم
            self.act('mk-sarparast', pid, 'refer', refer_to=self.u('admin')['id'], note='کنترل')
        self.act('mk-sarparast', pid, 'refer', refer_to=self.u('mk-fanni')['id'], note='مشخصات را کنترل کنید')
        d = self.get(pid)['doc']
        self.assertEqual((d['stage'], d['holder_id']), ('referred', self.u('mk-fanni')['id']))
        self.attach('mk-fanni', pid, 'مشخصات فنی', 'spec.pdf')
        self.act('mk-fanni', pid, 'refer_done', note='کنترل شد')
        d = self.get(pid)['doc']
        self.assertEqual((d['stage'], d['holder_id']), ('supervisor_approve', self.u('mk-sarparast')['id']))

    def test_migration_of_legacy_stages(self):
        pid = self.new('main')
        self.c.execute("UPDATE purchases SET stage='tech_review', holder_id=? WHERE id=?", (self.u('mk-fanni')['id'], pid))
        self.c.execute("DELETE FROM settings WHERE key='mig_v40'"); self.c.commit(); self.c.close()
        app.init_db(); self.c = app.db()
        d = self.get(pid)['doc']
        self.assertEqual((d['stage'], d['holder_id']), ('supervisor_approve', self.u('mk-sarparast')['id']))


class TPurge(Base):
    """نسخه ۴.۲: پاک کردن سوابق گردش اسناد فقط با مدیر سیستم، رمز و تأیید؛ بدون آسیب به بقیه بخش‌ها."""
    def seed(self):
        pid = self.to_delivery()  # درخواست کالا با پیوست
        self.c.execute("INSERT INTO letters(kind,year,seq,number,subject,created_by,created_at,status) "
                       "VALUES('in',1405,1,'1405/1','نامه',1,?,'open')", (app.now(),))
        self.c.execute("INSERT INTO requests(year,seq,number,kind,title,requester_id,amount,status,created_at) "
                       "VALUES(1405,1,'R-1','تنخواه','تنخواه',1,100,'pending',?)", (app.now(),))
        self.c.commit()
        return pid

    def test_guards(self):
        self.seed()
        for un, body in (('admin', {}), ('admin', {'confirm': 'حذف', 'password': 'غلط'}),
                         ('admin', {'confirm': 'نه', 'password': '1234'}), ('ceo', {'confirm': 'حذف', 'password': '1234'})):
            with self.assertRaises(app.ApiError):
                self.call('POST', '/api/admin/purge_workflow', un, body)
        self.assertEqual(app.workflow_counts(self.c)['purchases'], 1)

    def test_purge_keeps_everything_else(self):
        self.seed()
        users = self.c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        members = self.c.execute('SELECT COUNT(*) FROM project_members').fetchone()[0]
        r = self.call('POST', '/api/admin/purge_workflow', 'admin', {'confirm': 'حذف', 'password': '1234'})
        self.assertEqual((r['letters'], r['purchases']), (1, 1))
        q = lambda t: self.c.execute('SELECT COUNT(*) FROM %s' % t).fetchone()[0]
        self.assertEqual((q('letters'), q('purchases'), q('purchase_items'), q('purchase_flow'), q('purchase_receipts')), (0, 0, 0, 0, 0))
        self.assertEqual((q('users'), q('project_members'), q('requests')), (users, members, 1))
        self.assertTrue(os.path.isfile(os.path.join(r['backup'], 'oa.db')))
        pid = self.to_delivery()  # شماره‌ها از نو
        self.assertEqual(self.get(pid)['doc']['number'], 'MR-0001')


class TDirectOrder(Base):
    """نسخه ۴.۳: درخواست کالای مدیر پروژه یا هیات مدیره دستور خرید است و به سرپرست کارگاه نمی‌رود."""
    def order(self, un, cat):
        pid = self.call('POST', '/api/purchases', un, {
            'project_id': 2, 'unit': 'exec', 'category': cat, 'warehouse': 'انبار', 'purpose': 'آزمون',
            'need_date': '1410/01/01', 'items': [{'title': 'پیچ', 'qty': '10', 'unit': 'عدد'}]})['id']
        self.act(un, pid, 'submit')
        return pid

    def test_pm_and_board_order_directly(self):
        sup = self.u('mk-sarparast')['id']
        for un in ('kasaeian', 'ceo'):
            d = self.get(self.order(un, 'general'))
            self.assertEqual((d['doc']['stage'], d['doc']['holder_id']), ('site_purchase', self.u('mk-poshtibani')['id']))
            self.assertEqual([r['to_id'] for r in d['referrals']], [sup])  # فقط رونوشت جهت اطلاع
            self.assertEqual(d['referrals'][0]['action'], 'جهت اطلاع')
            d = self.get(self.order(un, 'main'))
            self.assertEqual((d['doc']['stage'], d['doc']['holder_id']), ('hq_purchase', self.u('support')['id']))
            self.assertTrue(d['doc']['po_no'])
            self.assertEqual([r['to_id'] for r in d['referrals']], [sup])

    def test_normal_user_still_goes_to_supervisor(self):
        d = self.get(self.order('mk-zali', 'general'))['doc']
        self.assertEqual(d['stage'], 'supervisor_approve')

    def test_issuer_can_cancel_before_purchase(self):
        pid = self.order('kasaeian', 'main')
        self.assertTrue(self.get(pid, 'kasaeian')['can_cancel'])
        self.call('POST', '/api/purchases/%d/cancel' % pid, 'kasaeian', {'reason': 'نیاز نیست'})
        self.assertEqual(self.get(pid)['doc']['status'], 'cancelled')


class TAdminNoWorkflow(Base):
    """نسخه ۳.۹: مدیر سیستم در هیچ گردش کاری نقش ندارد."""
    def test_no_referral_role_or_finance(self):
        adm = self.u('admin')['id']
        with self.assertRaises(app.ApiError):  # ارجاع نامه به مدیر سیستم
            app.make_referrals(self.c, self.u('secretary'), 'letter', 1, [adm], 'اقدام', '', '')
        with self.assertRaises(app.ApiError):  # مدیر سیستم به‌عنوان مدیر مالی
            self.call('POST', '/api/admin/settings', 'admin', {'finance_manager': str(adm)})
        with self.assertRaises(app.ApiError):  # مدیر سیستم به‌عنوان انباردار پروژه
            self.call('POST', '/api/projects/2/update', 'admin', {'name': 'موادکاران', 'members': {'warehouse': adm}})
        self.assertFalse(app.is_finance(self.c, self.u('admin')))
        self.assertEqual(self.call('GET', '/api/cartable', 'admin')['pur_pay'], [])
        self.assertNotIn(adm, [x['id'] for x in self.call('GET', '/api/meta', 'admin')['wf_users']])

    def test_cleanup_of_old_assignments(self):
        adm = self.u('admin')['id']
        self.c.execute("UPDATE settings SET value=? WHERE key='support_manager'", (str(adm),))
        self.c.execute("INSERT OR REPLACE INTO project_members(project_id,role_key,user_id) VALUES(2,'warehouse',?)", (adm,))
        self.c.execute("INSERT INTO referrals(doc_type,doc_id,from_id,to_id,action,created_at) VALUES('request',1,2,?,'اقدام',?)",
                       (adm, app.now()))
        self.c.execute("DELETE FROM settings WHERE key='fix_v39'"); self.c.commit(); self.c.close()
        app.init_db(); self.c = app.db()
        self.assertEqual(app.settings(self.c).get('support_manager'), '')
        self.assertIsNone(self.c.execute("SELECT 1 FROM project_members WHERE user_id=?", (adm,)).fetchone())
        self.assertEqual(self.c.execute("SELECT status FROM referrals WHERE to_id=?", (adm,)).fetchone()[0], 'closed')


class TMigration(Base):
    def test_mig(self):
        c = self.c
        pr = lambda **k: c.execute('INSERT INTO purchases(number,project_id,requester_id,stage,status,grn_no,recv_by,recv_at,'
                                   'wh_by,wh_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                                   (k['n'], 2, 14, k['st'], k['ss'], k.get('g', ''), k.get('rb'), k.get('ra'),
                                    13, '2026-10-01 10:00:00')).lastrowid
        done = pr(n='MR-9001', st='done', ss='closed', g='GRN-0900', rb=14, ra='2026-10-01 11:00:00')
        wait = pr(n='MR-9002', st='delivery', ss='open')
        for pid in (done, wait):
            c.execute("INSERT INTO purchase_items(purchase_id,row_no,title,qty,unit,bought_qty,bought_status,recv_qty) "
                      "VALUES(?,1,'پیچ','10','عدد','10','bought','9')", (pid,))
        c.execute("DELETE FROM settings WHERE key='mig_v33'"); c.commit(); c.close()
        app.init_db(); app.init_db()  # دو بار: نباید تکرار شود
        self.c = c = app.db()
        rs = {r['purchase_id']: app.rows(c.execute('SELECT * FROM purchase_receipts WHERE purchase_id=?', (r['purchase_id'],)))
              for r in app.rows(c.execute('SELECT DISTINCT purchase_id FROM purchase_receipts'))}
        self.assertEqual(len(rs[done]), 1); self.assertEqual(rs[done][0]['status'], 'closed')
        self.assertEqual(rs[done][0]['code'], 'GRN-0900')
        self.assertEqual(len(rs[wait]), 1); self.assertEqual(rs[wait][0]['status'], 'pending')
        ln = app.one(c.execute('SELECT * FROM purchase_receipt_lines WHERE receipt_id=?', (rs[wait][0]['id'],)))
        self.assertEqual((ln['wh_qty'], ln['recv_qty']), ('9', ''))
        self.assertEqual(c.execute('SELECT recv_qty FROM purchase_items WHERE purchase_id=?', (wait,)).fetchone()[0], '')


if __name__ == '__main__':
    unittest.main()


class TSiteDone(Base):
    def test_site_staff_see_done_after_delivery(self):
        """۴.۶: پس از اعلام وصول خرید دفتر مرکزی، برای کارکنان کارگاه کار تمام است و مراحل مالی دیده نمی‌شود."""
        r = self.call('POST', '/api/purchases', 'mk-zali', {
            'project_id': 2, 'unit': 'exec', 'category': 'main', 'warehouse': 'انبار', 'purpose': 'آزمون',
            'need_date': '1410/01/01', 'items': [{'title': 'پیچ', 'qty': '10', 'unit': 'عدد'}]})
        pid = r['id']
        self.act('mk-zali', pid, 'submit'); self.act('mk-sarparast', pid, 'approve')
        P = self.get(pid)['doc']
        pm = self.u('kasaeian')['username']
        self.act(pm, pid, 'direct')
        sup = app.one(self.c.execute('SELECT u.username FROM purchases p JOIN users u ON u.id=p.holder_id WHERE p.id=?', (pid,)))['username']
        self.attach(sup, pid, 'فاکتور', 'inv.pdf')
        its = self.get(pid)['items']
        self.act(sup, pid, 'purchased', items=[{'id': its[0]['id'], 'qty': '10', 'unit': 'عدد', 'status': 'bought'}])
        self.act('mk-anbar', pid, 'wh_ok', items=[{'id': its[0]['id'], 'qty': '10'}])
        self.act('mk-zali', pid, 'recv_ok', items=[{'id': its[0]['id'], 'qty': '10'}])
        self.assertEqual(self.get(pid)['doc']['stage'], 'finance_settle')
        d = self.get(pid, 'mk-zali')
        self.assertEqual((d['doc']['status'], d['doc'].get('site_done')), ('site_done', 1))
        self.assertFalse(any(f['stage'] in app.POST_DELIVERY for f in d['flow']))
        self.assertIsNone(d['docs'])
        lst = {x['id']: x for x in self.call('GET', '/api/purchases', 'mk-sarparast', q={'project_id': '2'})}
        self.assertEqual(lst[pid]['status'], 'site_done')
        self.assertEqual(self.get(pid, 'admin')['doc']['status'], 'open')  # برای دفتر مرکزی در جریان است
