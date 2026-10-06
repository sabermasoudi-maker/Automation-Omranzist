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
        self.act('mk-ejraei', pid, 'approve')
        self.act('mk-sarparast', pid, 'approve')
        self.attach('mk-poshtibani', pid, 'فاکتور', 'inv.pdf')
        self.buy(pid, 10, 5)
        self.assertEqual(self.get(pid)['doc']['stage'], 'delivery')
        return pid

    def buy(self, pid, q1, q2):
        its = self.get(pid)['items']
        return self.act('mk-poshtibani', pid, 'purchased', items=[
            {'id': its[0]['id'], 'qty': str(q1), 'unit': 'عدد', 'status': 'bought'},
            {'id': its[1]['id'], 'qty': str(q2), 'unit': 'کیسه', 'status': 'bought'}])

    def lines(self, pid, q1, q2, **extra):
        its = self.get(pid)['items']
        a = dict(id=its[0]['id'], qty=str(q1)); a.update(extra.get('first', {}))
        return [a, dict(id=its[1]['id'], qty=str(q2))]

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

    def test_07a_mismatch_needs_choice(self):
        pid = self.to_delivery()
        with self.assertRaises(app.ApiError) as e:
            self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 8, 5))
        self.assertEqual(e.exception.code, 400); self.assertIn('پیچ', e.exception.msg)

    def test_07b_accept_with_note(self):
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 8, 5, first={'diff_ok': True, 'diff_note': 'بسته ۸تایی'}))
        ln = self.receipts(pid)[0]['lines'][0]
        self.assertIn('بسته ۸تایی', ln['diff_note']); self.assertEqual(ln['wh_qty'], '8')
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 8, 5))  # همان عدد انباردار
        self.assertEqual(self.get(pid)['doc']['stage'], 'done')

    def test_07c_partial_cannot_exceed(self):
        pid = self.to_delivery()
        with self.assertRaises(app.ApiError) as e:
            self.act('mk-anbar', pid, 'wh_partial', items=self.lines(pid, 12, 5))
        self.assertEqual(e.exception.code, 400)

    def test_07d_receiver_has_no_partial_and_compares_to_warehouse(self):
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 10, 5))
        with self.assertRaises(app.ApiError) as e:
            self.act('mk-zali', pid, 'recv_partial', items=self.lines(pid, 6, 5))
        self.assertEqual(e.exception.code, 403)
        with self.assertRaises(app.ApiError) as e:  # با عدد انباردار فرق دارد و انتخابی نشده
            self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 9, 5))
        self.assertIn('انباردار', e.exception.msg)

    def test_08a_partial(self):
        self.partial()

    def partial(self):
        pid = self.to_delivery()
        self.act('mk-anbar', pid, 'wh_partial', items=self.lines(pid, 6, 5))
        # ۳.۷: باقی‌مانده نزد انباردار می‌ماند و تحویل‌گیرنده هم‌زمان نوبت ثبت‌شده را تأیید می‌کند
        self.assertEqual(self.get(pid)['doc']['holder_id'], self.u('mk-anbar')['id'])
        self.assertIn('recv_ok', self.get(pid, 'mk-zali')['actions'])
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 6, 5))  # تأیید نهایی نوبت بخشی
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
        self.act('mk-anbar', pid, 'wh_partial', items=self.lines(pid, 300, 5))
        self.assertEqual(self.get(pid, 'mk-anbar')['items'][0]['remaining_wh'], '700')
        self.assertIn('wh_ok', self.get(pid, 'mk-anbar')['actions'])  # بدون انتظار برای تحویل‌گیرنده
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 700, 0)[:1])
        d = self.get(pid)
        self.assertEqual([r['status'] for r in d['receipts']], ['pending', 'pending'])
        self.assertEqual(d['doc']['holder_id'], self.u('mk-zali')['id'])  # انبار کارش تمام شد
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 300, 5))   # نوبت ۱
        self.assertEqual(self.get(pid)['doc']['stage'], 'delivery')
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 700, 0)[:1])  # نوبت ۲
        d = self.get(pid); g = d['doc']['grn_no']
        self.assertEqual([r['code'] for r in d['receipts']], [g + '/1', g + '/2'])
        self.assertEqual([i['recv_qty'] for i in d['items']], ['1000', '5'])
        self.assertEqual(d['doc']['stage'], 'done')

    def test_08b_second_receipt(self):
        pid = self.partial()
        self.act('mk-anbar', pid, 'wh_ok', items=self.lines(pid, 4, 0)[:1])
        self.act('mk-zali', pid, 'recv_ok', items=self.lines(pid, 4, 0)[:1])
        d = self.get(pid); g = d['doc']['grn_no']
        self.assertEqual([r['code'] for r in d['receipts']], [g + '/1', g + '/2'])
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

    def test_10c_delete_locked(self):
        pid = self._done()
        with self.assertRaises(app.ApiError):
            self.call('POST', '/api/admin/docs/purchase/%d/delete' % pid, 'admin')


class THandover(Base):
    def test_handover_carries_through(self):
        """کار واگذارشده به پشتیبانی کارگاه، در مرحله خرید دفتر مرکزی هم با او می‌ماند (نسخه ۳.۶)."""
        r = self.call('POST', '/api/purchases', 'mk-zali', {
            'project_id': 2, 'unit': 'exec', 'category': 'main', 'warehouse': 'انبار', 'purpose': 'آزمون',
            'need_date': '1410/01/01', 'items': [{'title': 'پیچ', 'qty': '10', 'unit': 'عدد'}]})
        pid = r['id']
        for un, a in (('mk-zali', 'submit'), ('mk-ejraei', 'approve'), ('mk-fanni', 'approve'),
                      ('mk-sarparast', 'approve'), ('kasaeian', 'approve')):
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
