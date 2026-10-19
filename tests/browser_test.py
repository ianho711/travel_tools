"""Real Chromium UI tests; requires Playwright and a locally installed browser.

Run: python tests/browser_test.py [--browser /usr/bin/chromium]
The test starts its own loopback HTTP server and writes artifacts outside the repo.
"""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import tempfile
import threading

from playwright.sync_api import sync_playwright, expect
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
STORE = 'travel-split-v1'


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', default=shutil.which('chromium') or shutil.which('google-chrome'))
    args = parser.parse_args()
    artifacts = Path(tempfile.mkdtemp(prefix='travel-browser-'))
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/index.html'
    passed = []

    def record(message):
        passed.append(message)
        print('PASS:', message, flush=True)

    try:
        with sync_playwright() as pw:
            options = {'headless': True, 'args': ['--no-sandbox']}
            if args.browser:
                options['executable_path'] = args.browser
            browser = pw.chromium.launch(**options)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            page = context.new_page()
            errors, external_requests = [], []
            page.on('pageerror', lambda err: errors.append(str(err)))

            def route_request(route):
                if route.request.url.startswith(f'http://127.0.0.1:{server.server_port}/'):
                    route.continue_()
                else:
                    external_requests.append(route.request.url)
                    route.abort()

            context.route('**/*', route_request)
            page.goto(url)
            expect(page).to_have_title('旅伴帳本 v1.8.1｜旅遊費用分攤')
            expect(page.locator('#app-version')).to_have_text('v1.8.1')
            expect(page.get_by_role('tab')).to_have_count(7)
            expect(page.locator('#total-expense')).to_have_text('0.0元')
            expect(page.locator('#roster-summary')).to_contain_text('0 位已填姓名 / 25 個名額')
            record('初始 25 人／4 天，無假支出，頁面無外部依賴')
            page.locator('#tab-vehicles').click()
            assert not page.locator('#daily-transport-extra').evaluate('(el)=>el.open')
            page.reload()
            assert not page.locator('#daily-transport-extra').evaluate('(el)=>el.open')
            page.locator('#tab-overview').click()
            record('每日交通支出預設收合且標示選填，重新整理仍無須設定；租車與油錢只填逐車區')

            def state():
                return json.loads(page.evaluate('(key) => localStorage.getItem(key)', STORE))

            def roster(opened=True):
                if page.locator('#roster').evaluate('(el) => el.open') != opened:
                    page.locator('#roster summary').click()

            def dialog(action, accept=True):
                messages = []

                def respond(d):
                    messages.append(d.message)
                    d.accept() if accept else d.dismiss()

                page.once('dialog', respond)
                with page.expect_event('dialog'):
                    action()
                assert messages, 'Expected a confirmation dialog'
                return messages[0]

            def tab(day_id):
                control = page.locator('#tab-' + day_id)
                if control.get_attribute('aria-selected') != 'true':
                    control.click()

            def field(kind, eid):
                return page.locator(f'[data-field="{kind}"][data-expense="{eid}"]')

            def expense_view(eid):
                trip = state()
                if any(e['id'] == eid for e in trip['carExpenses']):
                    tab('vehicles'); return
                day = next(d for d in trip['days'] if any(e['id'] == eid for e in d['expenses']))
                cost = next(e for e in day['expenses'] if e['id'] == eid)
                if cost['category'] in ('fuel','rental','parking'):
                    tab('vehicles')
                    if not page.locator('#daily-transport-extra').evaluate('(el)=>el.open'):
                        page.locator('#daily-transport-extra > summary').click()
                    if page.locator('#transport-day').input_value() != day['id']:
                        page.locator('#transport-day').select_option(day['id'])
                else:
                    tab(day['id'])

            def part(eid, pid):
                expense_view(eid)
                details = page.locator('#traffic-parts-' + eid)
                if details.count() and not details.evaluate('(el)=>el.open'):
                    details.locator('summary').click()
                return page.locator(f'[data-field="participation"][data-expense="{eid}"][data-person="{pid}"]')

            def share(eid, pid, value):
                expense_view(eid)
                details = page.locator('#traffic-parts-' + eid)
                if details.count() and not details.evaluate('(el)=>el.open'):
                    details.locator('summary').click()
                expect(page.locator(f'[data-share="{eid}-{pid}"]')).to_have_text(value)

            def open_fee(eid):
                modal = page.locator('#expense-dialog')
                if modal.is_visible():
                    if modal.get_attribute('data-expense') == eid:
                        return
                    page.get_by_role('button', name='取消', exact=True).click()
                expense_view(eid)
                page.locator('#edit-' + eid).click()
                expect(modal).to_be_visible()

            def save_fee():
                page.get_by_role('button', name='儲存費用', exact=True).click()
                expect(page.locator('#expense-dialog')).to_be_hidden()

            def set_field(kind, eid, value):
                open_fee(eid)
                if kind in ('payer', 'category'):
                    field(kind, eid).select_option(value)
                else:
                    field(kind, eid).fill(value)
                save_fee()

            def bulk(eid, select=True):
                open_fee(eid)
                page.locator(f'#expense-dialog [data-action="{"select-all" if select else "select-none"}"]').click()
                save_fee()

            def set_fee(eid, amount, participants, payer):
                open_fee(eid)
                field('amount', eid).fill(amount)
                field('payer', eid).select_option(payer)
                save_fee()
                for pid in participants:
                    part(eid, pid).check()

            def expect_issue(eid, text):
                open_fee(eid)
                expect(page.locator('#expense-dialog-issues')).to_contain_text(text)
                page.get_by_role('button', name='取消', exact=True).click()

            def set_driver(day_id, pid, checked=True):
                previous = page.locator('[role="tab"][aria-selected="true"]').get_attribute('data-tab')
                tab('vehicles')
                details = page.locator('#drivers-' + day_id)
                if not details.evaluate('(el)=>el.open'):
                    details.locator('summary').click()
                page.locator(f'#driver-{day_id}-{pid}').set_checked(checked)
                tab(previous)

            def driver_checked(day_id, pid):
                previous = page.locator('[role="tab"][aria-selected="true"]').get_attribute('data-tab')
                tab('vehicles')
                checked = page.locator(f'#driver-{day_id}-{pid}').is_checked()
                tab(previous)
                return checked

            def set_use_car(day_id=None, checked=True, accept=None):
                previous = page.locator('[role="tab"][aria-selected="true"]').get_attribute('data-tab')
                day_id = day_id or previous
                tab('vehicles')
                control = page.locator('#use-car-' + day_id)
                message = ''
                if accept is None:
                    control.set_checked(checked)
                else:
                    message = dialog(lambda: control.click(), accept)
                tab(previous)
                return message

            roster()
            # Expand beyond the new default to retain explicit coverage of member 30.
            for _ in range(5):
                page.get_by_role('button',name='＋ 新增成員',exact=True).click()
            for i, name in enumerate(['阿安', '小晴', '阿哲']):
                page.locator('[data-field="member-name"]').nth(i).fill(name)
            roster(False)
            s = state()
            a, b, c = [m['id'] for m in s['members'][:3]]
            last = s['members'][29]['id']
            d1, d2 = s['days'][:2]
            e1 = {e['category']: e['id'] for e in d1['expenses']}
            e2 = {e['category']: e['id'] for e in d2['expenses']}
            tab(d1['id'])
            set_fee(e1['breakfast'], '100', [c, a, b], a)
            share(e1['breakfast'], a, '33.4')
            share(e1['breakfast'], b, '33.3')
            share(e1['breakfast'], c, '33.3')
            assert part(e1['breakfast'], a).evaluate('(el) => el.tagName === "INPUT" && el.type === "checkbox"')
            record('真實 checkbox 可操作，100 元／3 人顯示 33.4、33.3、33.3')

            set_fee(e1['lunch'], '90', [b, c], b)
            set_fee(e1['dinner'], '70', [a, c], c)
            share(e1['lunch'], a, '0.0')
            share(e1['lunch'], b, '45.0')
            share(e1['dinner'], a, '35.0')
            tab('overview')
            expect(page.locator('#total-expense')).to_have_text('260.0元')
            expect(page.locator(f'[data-summary="{a}-meals"]')).to_have_text('68.4')
            expect(page.locator('#total-difference')).to_have_text('0.0元')
            record('三餐勾選互相獨立，總覽立即反映餐費與代墊')

            with page.expect_download() as info:
                page.get_by_role('button',name='匯出試算表',exact=True).click()
            report_file=artifacts/'meal-report.xlsx'
            info.value.save_as(report_file)
            report=load_workbook(report_file,data_only=False)
            assert report['整趟總覽']['B3'].value==260
            assert report['整趟總覽']['A7'].value=='阿安'
            assert report['整趟總覽']['B7'].value==68.4
            assert report['整趟總覽']['M7'].value==31.6
            assert report['每餐參與名單']['D4'].value=='阿安、小晴、阿哲'
            assert report['每餐參與名單']['D5'].value=='小晴、阿哲'
            assert report['每餐參與名單']['D6'].value=='阿安、阿哲'
            daily_report=report['第 1 天']
            assert daily_report['B12'].value=='參與' and daily_report['C12'].value==33.4
            assert daily_report['D12'].value=='未參與' and daily_report['E12'].value==0
            assert daily_report['F12'].value=='參與' and daily_report['G12'].value==35
            assert not any(c.data_type=='f' for sh in report for row in sh for c in row)
            record('實際匯出 XLSX：三餐參與名單、沒吃零元、尾差及總覽正確，報表不需程式或公式')

            tab(d1['id'])
            set_use_car()
            set_driver(d1['id'], a)
            set_fee(e1['fuel'], '100', [a, b, c], a)
            set_fee(e1['rental'], '120', [a, b, c], a)
            share(e1['fuel'], a, '0.0')
            share(e1['rental'], a, '0.0')
            share(e1['fuel'], b, '50.0')
            share(e1['breakfast'], a, '33.4')
            set_driver(d1['id'], a, False)
            expect_issue(e1['fuel'], '尚未設定具名司機')
            set_driver(d1['id'], b)
            share(e1['fuel'], a, '50.0')
            share(e1['fuel'], b, '0.0')
            set_driver(d1['id'], a)
            share(e1['fuel'], c, '100.0')
            set_driver(d1['id'], b, False)
            record('變更司機、多位司機、缺司機提示、司機勾選車資免付且餐費照付')

            tab(d2['id'])
            set_use_car()
            set_driver(d2['id'], b)
            set_fee(e2['fuel'], '60', [a, b, c], b)
            share(e2['fuel'], a, '30.0')
            share(e2['fuel'], b, '0.0')
            tab(d1['id'])
            assert driver_checked(d1['id'], a)
            assert not driver_checked(d1['id'], b)
            share(e1['fuel'], a, '0.0')
            tab(d2['id'])
            assert driver_checked(d2['id'], b)
            expect(part(e2['breakfast'], a)).not_to_be_checked()
            record('實際切換日期，A／B 每日司機與跨日用餐紀錄各自獨立')

            tab(d1['id'])
            set_use_car(checked=False, accept=False)
            assert state()['days'][0]['useCar']
            message = set_use_car(checked=False, accept=True)
            assert '仍會保留' in message
            open_fee(e1['fuel'])
            expect(field('amount', e1['fuel'])).to_have_value('100')
            page.get_by_role('button', name='取消', exact=True).click()
            share(e1['fuel'], b, '0.0')
            expect_issue(e1['fuel'], '當日未用車')
            tab('overview')
            expect(page.locator('#total-difference')).to_have_text('220.0元')
            tab(d1['id'])
            set_use_car()
            share(e1['fuel'], b, '50.0')
            record('取消用車須確認，可取消操作；費用不遺失、差額顯示，重新用車可恢復')

            set_field('amount', e1['breakfast'], '100.1')
            share(e1['breakfast'], a, '33.4')
            share(e1['breakfast'], b, '33.4')
            bulk(e1['lunch'], False)
            expect_issue(e1['lunch'], '沒有可分攤者')
            share(e1['lunch'], b, '0.0')
            part(e1['lunch'], b).check()
            part(e1['lunch'], c).check()
            set_field('amount', e1['other'], '0')
            open_fee(e1['other'])
            expect(page.locator('#expense-dialog-issues')).to_be_hidden()
            page.get_by_role('button', name='取消', exact=True).click()
            set_field('amount', e1['other'], '-1')
            open_fee(e1['other'])
            expect(field('amount', e1['other'])).to_have_attribute('aria-invalid', 'true')
            page.get_by_role('button', name='取消', exact=True).click()
            set_field('amount', e1['other'], '')
            record('修改金額即時計算，正金額無分攤者、0 元、空白與不合法金額正確處理')

            roster()
            page.locator('#name-' + last).fill('第30位旅伴')
            roster(False)
            bulk(e1['dinner'], False)
            part(e1['dinner'], last).check()
            set_field('payer', e1['dinner'], last)
            share(e1['dinner'], last, '70.0')
            record('第 30 位成員可真正點選、分攤及代墊')

            # Clearing a name must preserve its selected IDs; bulk operations touch named members only.
            roster()
            page.locator('#name-' + c).fill('   ')
            roster(False)
            expect(part(e1['breakfast'], c)).to_be_disabled()
            bulk(e1['breakfast'], False)
            expense = next(e for e in state()['days'][0]['expenses'] if e['id'] == e1['breakfast'])
            assert expense['participantIds'] == [c]
            bulk(e1['breakfast'])
            expect(page.locator(f'[data-count="{e1["breakfast"]}"]')).to_have_text('3')
            share(e1['breakfast'], c, '0.0')
            roster()
            page.locator('#name-' + c).fill('阿哲')
            roster(False)
            expect(page.locator(f'[data-count="{e1["breakfast"]}"]')).to_have_text('4')
            record('空白姓名不計人數，全選與取消全選只操作具名成員，原紀錄可恢復')

            tab('overview')
            before = page.locator(f'[data-summary^="{a}-"]').all_text_contents()
            roster()
            page.locator('#name-' + a).fill('小安改名')
            roster(False)
            assert page.locator(f'[data-summary^="{a}-"]').all_text_contents() == before
            tab(d1['id'])
            assert driver_checked(d1['id'], a)
            assert next(e for e in state()['days'][0]['expenses'] if e['id'] == e1['breakfast'])['payerId'] == a
            expect(part(e1['breakfast'], a)).to_be_checked()
            record('改姓名同步顯示，分攤、代墊、參與及司機 ID 不變')

            # The matrix has no embedded expense form; only compact column titles stick.
            scroll = page.locator('#daily-scroll')
            assert page.locator('#daily-scroll input:not([type=checkbox]),#daily-scroll select').count() == 0
            scroll.evaluate('(el) => { el.scrollLeft=0; el.scrollTop=0; }')
            x = page.locator('#daily-scroll tbody .name-cell').first.bounding_box()['x']
            y = page.locator('#title-' + e1['breakfast']).bounding_box()['y']
            scroll.evaluate('(el) => { el.scrollLeft=450; el.scrollTop=550; }')
            assert abs(page.locator('#daily-scroll tbody .name-cell').first.bounding_box()['x'] - x) < 2
            assert abs(page.locator('#title-' + e1['breakfast']).bounding_box()['y'] - y) < 2
            assert page.locator('.daily-compact-row').bounding_box()['height'] <= 64
            page.set_viewport_size({'width': 1280, 'height': 720})
            scroll.evaluate('(el) => { el.scrollLeft=0; el.scrollTop=300; el.scrollIntoView({block:"center"}); }')
            visible_rows = scroll.evaluate("""el => {
                const box=el.getBoundingClientRect(), head=el.querySelector('.daily-compact-row').getBoundingClientRect();
                return [...el.querySelectorAll('tbody tr')].filter(row=>{
                    const r=row.getBoundingClientRect();return r.top>=head.bottom&&r.bottom<=box.bottom;
                }).length;
            }""")
            assert visible_rows >= 4, visible_rows
            page.screenshot(path=str(artifacts / 'laptop-daily-matrix.png'), full_page=True)
            record('表格無嵌入式費用表單，僅固定不足 64px 的精簡標題；720px 高度可完整看到至少四列人員')

            original = state()
            open_fee(e1['breakfast'])
            modal = page.locator('#expense-dialog')
            assert modal.evaluate('(el) => el.matches(":modal")')
            assert modal.bounding_box()['width'] >= 850
            assert field('expense-name', e1['breakfast']).bounding_box()['width'] >= 700
            field('amount', e1['breakfast']).fill('300')
            page.locator(f'#expense-dialog [data-field="draft-participation"][data-person="{a}"]').uncheck()
            assert state() == original, 'Unsaved dialog edits must not change persistent data'
            page.get_by_role('button', name='取消', exact=True).click()
            assert state() == original
            open_fee(e1['breakfast'])
            field('expense-name', e1['breakfast']).fill('未儲存的名稱')
            page.keyboard.press('Escape')
            expect(modal).to_be_hidden()
            assert state() == original
            open_fee(e1['breakfast'])
            amount_before = field('amount', e1['breakfast']).input_value()
            field('amount', e1['breakfast']).fill('-1')
            expect(page.locator('#expense-dialog-issues')).to_contain_text('金額須為非負數')
            save_fee()
            tab('overview')
            page.locator(f'[data-action="goto-expense"][data-expense="{e1["breakfast"]}"]').click()
            expect(modal).to_be_visible()
            expect(field('amount', e1['breakfast'])).to_be_focused()
            field('amount', e1['breakfast']).fill(amount_before)
            page.screenshot(path=str(artifacts / 'desktop-expense-dialog.png'), full_page=True)
            save_fee()
            assert state() == original
            page.set_viewport_size({'width': 1440, 'height': 1000})
            scroll.evaluate('(el) => { el.scrollLeft=0; el.scrollTop=0; }')
            record('寬版獨立視窗可編輯，儲存才套用，取消與 Esc 不改資料；待處理入口直接開啟並聚焦金額')

            page.get_by_role('button', name='＋ 新增餐費', exact=True).click()
            added = page.locator('#expense-dialog').get_attribute('data-expense')
            field('expense-name', added).fill('加點早餐')
            set_fee(added, '30', [a, b], b)
            share(added, a, '15.0')
            expect(part(e1['breakfast'], last)).to_be_checked()
            open_fee(added)
            dialog(lambda: page.locator(f'[data-action="delete-expense"][data-expense="{added}"]').click(), False)
            assert len(state()['days'][0]['expenses']) == 8
            dialog(lambda: page.locator(f'[data-action="delete-expense"][data-expense="{added}"]').click())
            assert len(state()['days'][0]['expenses']) == 7
            record('同類費用可新增多筆且分開勾選，有資料費用刪除可取消或確認')

            roster()
            page.get_by_role('button', name='＋ 新增成員', exact=True).click()
            added_person = state()['members'][-1]['id']
            page.locator('#name-' + added_person).fill('新增旅伴')
            delete_person = page.locator(f'[data-action="delete-member"][data-person="{added_person}"]')
            dialog(lambda: delete_person.click(), False)
            assert len(state()['members']) == 31
            dialog(lambda: delete_person.click())
            assert len(state()['members']) == 30
            roster(False)
            page.get_by_role('button', name='＋ 新增一天', exact=True).click()
            field_day = page.locator('[data-field="day-name"]')
            field_day.fill('額外一天')
            expect(page.get_by_role('tab', name='額外一天', exact=True)).to_have_attribute('aria-selected', 'true')
            dialog(lambda: page.get_by_role('button', name='刪除此天', exact=True).click(), False)
            assert len(state()['days']) == 5
            dialog(lambda: page.get_by_role('button', name='刪除此天', exact=True).click())
            assert len(state()['days']) == 4
            record('人數與天數可增減，日期名稱同步，刪除已有資料需確認')

            tab('overview')
            overview = page.locator('.overview-table').inner_text()
            before_export = state()
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出唯讀網頁', exact=True).click()
            readonly_file = artifacts / 'trip-view.html'
            info.value.save_as(readonly_file)
            assert 'id="trip-snapshot"' in readonly_file.read_text()
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出 JSON', exact=True).click()
            download = info.value
            backup = artifacts / 'backup.json'
            download.save_as(backup)
            assert json.loads(backup.read_text()) == before_export
            before_invalid = state()
            for payload in [b'{bad json', b'{"version":9}', json.dumps({**before_export, 'members': []}).encode()]:
                page.locator('#import-file').set_input_files({'name': 'invalid.json', 'mimeType': 'application/json', 'buffer': payload})
                expect(page.locator('#flash')).to_contain_text('匯入失敗，原資料未變更')
                assert state() == before_invalid
            record('真正下載 JSON 備份；不合法 JSON／版本／名單匯入失敗且不覆蓋原資料')

            tab(d1['id'])
            set_field('amount', e1['breakfast'], '200')
            modified = state()
            dialog(lambda: page.locator('#import-file').set_input_files(str(backup)), False)
            assert state() == modified
            dialog(lambda: page.locator('#import-file').set_input_files(str(backup)))
            expect(page.locator('#flash')).to_contain_text('已成功還原')
            assert state() == before_export
            assert page.locator('.overview-table').inner_text() == overview
            page.reload()
            assert state() == before_export
            assert page.locator('.overview-table').inner_text() == overview
            record('匯入可取消，成功還原後資料與總覽一致，重新整理仍保存')

            # Serve the exported file from memory, without placing any personal data in the repo.
            context.route('**/shared-view.html', lambda r: r.fulfill(path=str(readonly_file), content_type='text/html; charset=utf-8'))
            tab(d1['id'])
            set_field('amount', e1['breakfast'], '999')
            editor_state = state()
            shared = context.new_page()
            shared.goto(url.replace('index.html', 'shared-view.html'))
            expect(shared).to_have_title('旅伴帳本 v1.8.1｜唯讀旅遊費用')
            expect(shared.locator('.tools')).to_be_hidden()
            expect(shared.locator('#roster')).to_be_hidden()
            expect(shared.locator('[data-action="add-day"]')).to_be_hidden()
            assert shared.locator('.overview-table').inner_text() == overview
            expect(shared.locator('#save-status')).to_contain_text('唯讀快照 · 更新於')
            shared.locator('#tab-' + d1['id']).click()
            shared.locator('#edit-' + e1['breakfast']).click()
            expect(shared.locator('#amount-' + e1['breakfast'])).to_have_value('100.1')
            assert shared.locator('#expense-dialog input:not(:disabled),#expense-dialog select:not(:disabled)').count() == 0
            expect(shared.locator('#expense-dialog [data-action="save-expense"]')).to_have_count(0)
            shared.get_by_role('button', name='關閉', exact=True).click()
            assert shared.locator('#view input:not(:disabled),#view select:not(:disabled)').count() == 0
            expect(shared.locator('#part-' + e1['breakfast'] + '-' + a)).to_be_checked()
            expect(shared.locator('[data-action="add-expense"]')).to_be_hidden()
            shared.reload()
            assert state() == editor_state, 'Readonly viewer must never touch editor localStorage'
            # Returning to overview uses only the embedded snapshot, not the newer editor state.
            shared.locator('#tab-overview').click()
            assert shared.locator('.overview-table').inner_text() == overview
            shared.close()
            dialog(lambda: page.locator('#import-file').set_input_files(str(backup)))
            record('唯讀 HTML 快照可獨立開啟及切換日期，不能操作編輯欄位，不讀寫管理者帳本')

            dialog(lambda: page.get_by_role('button', name='重設全部', exact=True).click(), False)
            assert state() == before_export
            dialog(lambda: page.get_by_role('button', name='載入範例', exact=True).click(), False)
            assert state() == before_export
            page.screenshot(path=str(artifacts / 'desktop-overview.png'), full_page=True)
            tab(d1['id'])
            page.screenshot(path=str(artifacts / 'desktop-daily.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            part(e1['breakfast'], a).uncheck()
            expect(part(e1['breakfast'], a)).not_to_be_checked()
            part(e1['breakfast'], a).check()
            expect(part(e1['breakfast'], a)).to_be_checked()
            open_fee(e1['breakfast'])
            expect(page.locator('#expense-dialog')).to_be_visible()
            assert page.locator('#expense-dialog').bounding_box()['width'] == 390
            assert page.locator('#expense-dialog').bounding_box()['height'] <= 845
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            draft_a = page.locator(f'#expense-dialog [data-field="draft-participation"][data-person="{a}"]')
            draft_a.uncheck()
            expect(page.locator(f'[data-draft-share="{a}"]')).to_have_text('0.0 元')
            expect(page.locator('#expense-preview')).to_contain_text('分攤 3 人')
            assert part(e1['breakfast'], a).is_checked(), 'Matrix updates only after Save'
            page.screenshot(path=str(artifacts / 'mobile-expense-dialog.png'), full_page=True)
            save_fee()
            expect(part(e1['breakfast'], a)).not_to_be_checked()
            part(e1['breakfast'], a).check()
            record('390px 手機使用全螢幕編輯，視窗內 checkbox 即時預覽，儲存後才更新表格')
            page.screenshot(path=str(artifacts / 'mobile-daily.png'), full_page=True)
            tab('overview')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(artifacts / 'mobile-overview.png'), full_page=True)
            record('重設／載入範例需確認，390px 手機寬度無頁面溢出且 checkbox 可操作')

            # Prove sample loading/reset work rather than merely showing dialogs.
            dialog(lambda: page.get_by_role('button', name='載入範例', exact=True).click())
            expect(page.locator('#issue-count')).to_have_text('0項')
            assert len(state()['members']) == 4 and len(state()['days']) == 2
            dialog(lambda: page.get_by_role('button', name='重設全部', exact=True).click())
            expect(page.locator('#total-expense')).to_have_text('0.0元')
            assert len(state()['members']) == 25 and len(state()['days']) == 4
            record('載入範例有有效帳目且無待處理，重設確實恢復空白 25 人／4 天')

            # Whole-trip costs use ride-days; gasoline paid at return is never billed only to that day.
            roster()
            for i, name in enumerate(['司機甲', '旅伴乙', '旅伴丙']):
                page.locator('[data-field="member-name"]').nth(i).fill(name)
            roster(False)
            trip = state()
            va, vb, vc = [m['id'] for m in trip['members'][:3]]
            trip_days = [d['id'] for d in trip['days'][:4]]
            for did in trip_days:
                tab(did)
                set_use_car()
                set_driver(did, va)
            tab('vehicles')
            before_trip = state()
            page.get_by_role('button', name='＋ 額外油錢', exact=True).click()
            page.get_by_role('button', name='取消', exact=True).click()
            assert state() == before_trip
            page.get_by_role('button', name='＋ 額外油錢', exact=True).click()
            fuel_trip = page.locator('#expense-dialog').get_attribute('data-expense')
            field('expense-name', fuel_trip).fill('還車總加油')
            field('amount', fuel_trip).fill('600')
            field('payer', fuel_trip).select_option(va)
            expect(page.locator('#trip-method')).to_have_value('person-days')

            def ride(did, pid):
                return page.locator(f'#expense-dialog [data-field="trip-participation"][data-day="{did}"][data-person="{pid}"]')

            for i, did in enumerate(trip_days):
                ride(did, va).check()
                ride(did, vb).check()
                if i < 2:
                    ride(did, vc).check()
            expect(page.locator('#expense-preview')).to_contain_text('6 人天')
            expect(page.locator(f'[data-trip-total="{va}"]')).to_have_text('0.0')
            expect(page.locator(f'[data-trip-total="{vb}"]')).to_have_text('400.0')
            expect(page.locator(f'[data-trip-total="{vc}"]')).to_have_text('200.0')
            expect(page.locator(f'[data-trip-billable="{vb}"]')).to_have_text('4')
            expect(page.locator(f'[data-trip-billable="{vc}"]')).to_have_text('2')
            selected_day = page.locator(f'[data-field="trip-day"][data-day="{trip_days[1]}"]')
            selected_day.uncheck()
            selected_day.check()
            expect(ride(trip_days[1], vc)).to_be_checked()
            assert state() == before_trip
            page.screenshot(path=str(artifacts / 'desktop-trip-expense.png'), full_page=True)
            save_fee()
            expect(page.locator(f'[data-vehicle="{vb}-fuel"]')).to_have_text('400.0')
            expect(page.locator(f'[data-vehicle="{vc}-fuel"]')).to_have_text('200.0')
            tab('overview')
            expect(page.locator('#total-expense')).to_have_text('600.0元')
            expect(page.locator('#total-difference')).to_have_text('0.0元')
            assert all(e['amount'] == '' for d in state()['days'] for e in d['expenses'])
            record('整趟600元依4天／2天分成400／200；司機零元，日期重勾保留名單，取消不改帳，費用不重複列入每日')

            tab('vehicles')
            page.get_by_role('button', name='＋ 額外租車費', exact=True).click()
            rental_trip = page.locator('#expense-dialog').get_attribute('data-expense')
            field('expense-name', rental_trip).fill('整趟租車')
            field('amount', rental_trip).fill('1200')
            field('payer', rental_trip).select_option(va)
            page.get_by_role('button', name='沿用上一筆搭車名單', exact=True).click()
            expect(page.locator('#expense-preview')).to_contain_text('6 人天')
            expect(page.locator(f'[data-trip-total="{vb}"]')).to_have_text('800.0')
            save_fee()
            tab(trip_days[0])
            set_driver(trip_days[0], vb)
            tab(trip_days[1])
            set_driver(trip_days[1], va, False)
            set_driver(trip_days[1], vc)
            tab('vehicles')
            expect(page.locator(f'[data-vehicle="{va}-fuel"]')).to_have_text('120.0')
            expect(page.locator(f'[data-vehicle="{vb}-fuel"]')).to_have_text('360.0')
            expect(page.locator(f'[data-vehicle="{vc}-fuel"]')).to_have_text('120.0')
            expect(page.locator(f'[data-vehicle="{vb}-rental"]')).to_have_text('720.0')
            open_fee(fuel_trip)
            expect(page.locator(f'[data-trip-billable="{va}"]')).to_have_text('1')
            expect(page.locator(f'[data-trip-billable="{vb}"]')).to_have_text('3')
            expect(page.locator(f'[data-trip-billable="{vc}"]')).to_have_text('1')
            page.get_by_role('button', name='取消', exact=True).click()
            record('租車可沿用上一筆名單；修改每日司機與多位司機後，整趟油錢／租車費立即更新，只免當日')

            tab(trip_days[3])
            safe_trip = state()
            set_use_car(checked=False, accept=False)
            assert state() == safe_trip
            set_use_car(checked=False, accept=True)
            tab('overview')
            expect(page.locator('#total-expense')).to_have_text('1,800.0元')
            expect(page.locator('#total-difference')).to_have_text('1,800.0元')
            expect(page.locator('#pending-issues')).to_contain_text('此日未用車')
            tab(trip_days[3])
            set_use_car()
            tab('overview')
            expect(page.locator('#total-difference')).to_have_text('0.0元')
            record('整趟車資引用的日期取消用車須確認，費用保留並列待處理，恢復用車後重新分攤')

            tab('vehicles')
            before_trip_export = state()
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出 JSON', exact=True).click()
            trip_backup = artifacts / 'trip-car-backup.json'
            info.value.save_as(trip_backup)
            assert json.loads(trip_backup.read_text()) == before_trip_export
            malformed = json.loads(trip_backup.read_text())
            malformed['carExpenses'][0]['rides'][0]['dayId'] = 'unknown'
            page.locator('#import-file').set_input_files({'name':'bad-trip.json','mimeType':'application/json','buffer':json.dumps(malformed).encode()})
            expect(page.locator('#flash')).to_contain_text('匯入失敗，原資料未變更')
            assert state() == before_trip_export
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出試算表', exact=True).click()
            trip_xlsx = artifacts / 'trip-car-report.xlsx'
            info.value.save_as(trip_xlsx)
            workbook = load_workbook(trip_xlsx)
            assert workbook['整趟車資']['A4'].value == '還車總加油'
            assert workbook['整趟車資']['C4'].value == 600
            assert workbook['整趟車資']['C5'].value == 1200
            assert workbook['整趟總覽']['C8'].value == 360
            assert workbook['整趟總覽']['D8'].value == 720
            assert workbook['車資逐日核對']['H4'].value == 0
            assert workbook['車資逐日核對']['H7'].value == 120
            assert workbook['車資逐日核對']['I7'].value == 1
            page.reload()
            expect(page.locator('#tab-vehicles')).to_have_attribute('aria-selected', 'true')
            assert state() == before_trip_export
            dialog(lambda: page.get_by_role('button', name='重設全部', exact=True).click())
            dialog(lambda: page.locator('#import-file').set_input_files(str(trip_backup)))
            assert state() == before_trip_export
            record('整趟車資JSON下載、錯誤匯入不覆蓋、重新整理及還原一致；XLSX含計費天數與逐日核對')

            tab('vehicles')
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出唯讀網頁', exact=True).click()
            trip_view = artifacts / 'trip-car-view.html'
            info.value.save_as(trip_view)
            context.route('**/trip-car-view.html', lambda r:r.fulfill(path=str(trip_view),content_type='text/html; charset=utf-8'))
            trip_reader = context.new_page()
            trip_reader.goto(url.replace('index.html','trip-car-view.html'))
            trip_reader.locator('#tab-vehicles').click()
            expect(trip_reader.locator(f'[data-vehicle="{vb}-fuel"]')).to_have_text('360.0')
            trip_reader.locator('#edit-' + fuel_trip).click()
            expect(trip_reader.locator(f'[data-trip-billable="{vb}"]')).to_have_text('3')
            assert trip_reader.locator('#expense-dialog input:not(:disabled),#expense-dialog select:not(:disabled)').count() == 0
            expect(trip_reader.locator('#expense-dialog [data-action="save-expense"]')).to_have_count(0)
            trip_reader.get_by_role('button', name='關閉', exact=True).click()
            trip_reader.close()
            assert state() == before_trip_export
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            open_fee(fuel_trip)
            expect(ride(trip_days[0], vb)).to_be_checked()
            ride(trip_days[0], vb).uncheck()
            ride(trip_days[0], vb).check()
            page.screenshot(path=str(artifacts / 'mobile-trip-expense.png'),full_page=True)
            page.get_by_role('button', name='取消', exact=True).click()
            assert state() == before_trip_export
            page.set_viewport_size({'width':1440,'height':1000})
            record('整趟車資唯讀快照可查看且不能修改；手機跨日checkbox可操作，取消不影響資料')

            tab(trip_days[3])
            dialog(lambda: page.get_by_role('button', name='刪除此天', exact=True).click(), False)
            assert state() == before_trip_export
            dialog(lambda: page.get_by_role('button', name='刪除此天', exact=True).click())
            assert all(ride['dayId'] != trip_days[3] for e in state()['carExpenses'] for ride in e['rides'])
            tab('vehicles')
            expect(page.locator(f'[data-vehicle="{vb}-fuel"]')).to_have_text('300.0')
            roster()
            dialog(lambda: page.locator('[data-action="delete-member"]').first.click(), False)
            dialog(lambda: page.locator('[data-action="delete-member"]').first.click())
            assert all(e['payerId'] is None and all(va not in r['participantIds'] for r in e['rides']) for e in state()['carExpenses'])
            tab('overview')
            expect(page.locator('#pending-issues')).to_contain_text('代墊者')
            record('刪除已有日期或成員須確認，整趟乘車與代墊參照同步移除，剩餘日期重算且保留車資')
            dialog(lambda: page.get_by_role('button', name='重設全部', exact=True).click())

            # Fleet calculator: separate vehicles, one shared ride plan, and meal-only members.
            roster()
            for i, name in enumerate(['司機甲', '搭4天乙', '搭2天丙', '包車丁']):
                page.locator('[data-field="member-name"]').nth(i).fill(name)
            roster(False)
            fleet = state()
            fa, fb, fc, fd = [m['id'] for m in fleet['members'][:4]]
            fleet_days = [d['id'] for d in fleet['days'][:4]]
            for did in fleet_days:
                tab(did)
                set_use_car()
                set_driver(did, fa)
            tab(fleet_days[0])
            meal_id = fleet['days'][0]['expenses'][2]['id']
            parking_id = fleet['days'][0]['expenses'][5]['id']
            set_fee(meal_id, '100', [fa,fb,fc,fd], fa)
            set_fee(parking_id, '40', [fa,fb,fc,fd], fa)
            roster()
            page.locator(f'[data-field="exclude-transport"][data-person="{fd}"]').check()
            roster(False)
            share(meal_id, fd, '25.0')
            share(parking_id, fd, '0.0')
            expect(part(parking_id, fd)).to_be_disabled()
            tab('vehicles')
            vehicle_ids = []
            for name, rent, gas, gas_payer in [('大車1','7000','1200',fa),('大車2','6500','1200',fa),('小車','5250','800',fb)]:
                page.get_by_role('button', name='＋ 新增車輛', exact=True).click()
                page.locator('#vehicle-name').fill(name)
                page.locator('#vehicle-rental-amount').fill(rent)
                page.locator('#vehicle-fuel-amount').fill(gas)
                page.locator('#vehicle-rental-payer').select_option(fa)
                page.locator('#vehicle-fuel-payer').select_option(gas_payer)
                page.get_by_role('button', name='儲存車輛', exact=True).click()
                vehicle_ids.append(state()['vehicles'][-1]['id'])
            expect(page.locator('[data-transport="rental"]')).to_have_text('18,750.0元')
            expect(page.locator('[data-transport="fuel"]')).to_have_text('3,200.0元')
            expect(page.locator('[data-transport="total"]')).to_have_text('21,950.0元')
            expect(page.locator('[aria-label="各車交通費用"] tbody tr')).to_have_count(3)
            page.get_by_role('button', name='設定整趟搭車名單', exact=True).click()
            page.locator('#expense-dialog [data-action="select-all"]').click()
            for did in fleet_days[2:]:
                ride(did, fc).uncheck()
            expect(ride(fleet_days[0], fd)).to_be_disabled()
            expect(ride(fleet_days[0], fd)).not_to_be_checked()
            expect(page.locator(f'[data-trip-billable="{fd}"]')).to_have_text('0')
            expect(page.locator(f'[data-trip-total="{fd}"]')).to_have_text('0.0')
            expect(page.locator('#expense-preview')).to_contain_text('6 人天')
            page.get_by_role('button', name='儲存搭車名單', exact=True).click()
            assert len(state()['carExpenses']) == 6
            assert all(e['rides'] == state()['transportRides'] for e in state()['carExpenses'])
            expect(page.locator(f'[data-vehicle="{fd}-fuel"]')).to_have_text('0.0')
            expect(page.locator(f'[data-vehicle="{fd}-rental"]')).to_have_text('0.0')
            page.screenshot(path=str(artifacts / 'desktop-fleet.png'), full_page=True)
            record('三台車分別輸入三筆租金與三筆油錢，租金各不相同，合計21950；搭車名單只設定一次並套用全部六筆')
            tab('overview')
            expect(page.locator('#total-expense')).to_have_text('22,090.0元')
            expect(page.locator('#total-difference')).to_have_text('0.0元')
            expect(page.locator(f'[data-summary="{fd}-meals"]')).to_have_text('25.0')
            expect(page.locator(f'[data-summary="{fd}-total"]')).to_have_text('25.0')
            expect(page.locator(f'[data-summary="{fd}-fuel"]')).to_have_text('0.0')
            expect(page.locator(f'[data-summary="{fd}-rental"]')).to_have_text('0.0')
            expect(page.locator(f'[data-summary="{fb}-advanced"]')).to_have_text('800.0')
            record('包車／交通自理者餐費正常、停車和車資全排除，交通全選跳過，乘車人天為0；各車代墊正確')

            tab(fleet_days[0])
            assert page.locator('#view [data-field="driver"],#view [data-field="use-car"]').count() == 0
            assert page.locator('#title-' + parking_id).count() == 0
            assert page.locator('[data-category="fuel"],[data-category="rental"],[data-category="parking"]').count() == 0
            page.get_by_role('button',name='＋ 新增餐費',exact=True).click()
            assert page.locator('#expense-dialog [data-field="category"]').count() == 0
            page.get_by_role('button',name='取消',exact=True).click()
            tab('vehicles')
            expect(page.locator('[aria-label="交通用車與司機"] tbody tr')).to_have_count(4)
            record('每日明細與新增費用完全沒有交通或司機欄位；每日用車、多位司機與停車費都在交通區')
            snapshot_before_other = state()
            tab(fleet_days[0])
            page.get_by_role('button',name='＋ 新增其他費用',exact=True).click()
            other_id=page.locator('#expense-dialog').get_attribute('data-expense')
            assert page.locator('#expense-dialog [data-field="category"]').count() == 0
            field('expense-name',other_id).fill('門票')
            set_fee(other_id,'30',[fb],fa)
            assert next(e for e in state()['days'][0]['expenses'] if e['id']==other_id)['category']=='other'
            tab('personal')
            page.locator('#personal-person').select_option(fb)
            expect(page.locator('[data-personal-source="daily-other"]')).to_have_text('30.0')
            expect(page.locator('[data-personal-source="meals"]')).to_have_text('25.0')
            expect(page.locator('[data-personal="pay"]')).to_have_text('13,901.8 元')
            open_fee(other_id)
            dialog(lambda:page.get_by_role('button',name='刪除此費用',exact=True).click())
            assert state()==snapshot_before_other
            record('新增餐費與其他用不同按鈕，表單無類別欄位；其他支出獨立帶入結算，刪除後恢復原帳')
            tab('personal')
            page.locator('#personal-person').select_option(fb)
            expect(page.locator('[data-personal-source="transport"]')).to_have_text('14,646.8')
            expect(page.locator('[data-personal-source="transport-advanced"]')).to_have_text('800.0')
            expect(page.locator('[data-personal-source="meals"]')).to_have_text('25.0')
            expect(page.locator('[data-personal="total"]')).to_have_text('14,671.8 元')
            expect(page.locator('[data-personal="pay"]')).to_have_text('13,871.8 元')
            assert state()['ledger'] == []
            page.locator('#personal-person').select_option(fd)
            expect(page.locator('[data-personal-source="transport"]')).to_have_text('0.0')
            expect(page.locator('[data-personal="pay"]')).to_have_text('25.0 元')
            page.screenshot(path=str(artifacts/'personal-transport-settlement.png'),full_page=True)
            record('交通獨立結算後帶入個人：含停車、代墊只扣一次、交通自理只付餐費，無重複個人紀錄')

            tab('vehicles')
            saved_fleet = state()
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{vehicle_ids[2]}"]').click()
            page.locator('#vehicle-name').fill('小車改名')
            page.locator('#vehicle-fuel-amount').fill('900')
            page.get_by_role('button', name='取消', exact=True).click()
            assert state() == saved_fleet
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{vehicle_ids[2]}"]').click()
            page.locator('#vehicle-name').fill('小車改名')
            page.locator('#vehicle-fuel-amount').fill('900')
            page.get_by_role('button', name='儲存車輛', exact=True).click()
            expect(page.locator('[data-transport="total"]')).to_have_text('22,050.0元')
            assert [e['id'] for e in state()['carExpenses']] == [e['id'] for e in saved_fleet['carExpenses']]
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{vehicle_ids[2]}"]').click()
            page.locator('#vehicle-fuel-amount').fill('800')
            page.get_by_role('button', name='儲存車輛', exact=True).click()
            record('車名／油錢可修改且自動更新總車資，取消不寫入，固定車輛與費用ID保留')

            saved_fleet = state()
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出 JSON', exact=True).click()
            fleet_backup = artifacts / 'fleet-backup.json'
            info.value.save_as(fleet_backup)
            assert json.loads(fleet_backup.read_text()) == saved_fleet
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出試算表', exact=True).click()
            fleet_xlsx = artifacts / 'fleet-report.xlsx'
            info.value.save_as(fleet_xlsx)
            fleet_report = load_workbook(fleet_xlsx)
            assert fleet_report['各車交通費']['A4'].value == '大車1'
            assert fleet_report['各車交通費']['B4'].value == 7000
            assert fleet_report['各車交通費']['C4'].value == 1200
            assert fleet_report['各車交通費']['D4'].value == 8200
            assert fleet_report['各車交通費']['B7'].value == 18750
            assert fleet_report['各車交通費']['C7'].value == 3200
            assert fleet_report['各車交通費']['D7'].value == 21950
            assert fleet_report['整趟總覽']['C10'].value == 0
            assert fleet_report['整趟總覽']['B10'].value == 25
            page.reload()
            assert state() == saved_fleet
            dialog(lambda: page.get_by_role('button', name='重設全部', exact=True).click())
            dialog(lambda: page.locator('#import-file').set_input_files(str(fleet_backup)))
            assert state() == saved_fleet
            record('三車、共用名單及交通自理設定可保存／還原；實際XLSX含逐車明細與總租金、總油錢、總車資')

            tab('vehicles')
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出唯讀網頁', exact=True).click()
            fleet_view = artifacts / 'fleet-view.html'
            info.value.save_as(fleet_view)
            context.route('**/fleet-view.html', lambda r:r.fulfill(path=str(fleet_view),content_type='text/html; charset=utf-8'))
            fv = context.new_page()
            fv.goto(url.replace('index.html','fleet-view.html'))
            fv.locator('#tab-vehicles').click()
            expect(fv.locator('[data-transport="total"]')).to_have_text('21,950.0元')
            fv.locator(f'[data-action="edit-vehicle"][data-vehicle="{vehicle_ids[0]}"]').click()
            assert fv.locator('#expense-dialog input:not(:disabled),#expense-dialog select:not(:disabled)').count() == 0
            fv.get_by_role('button', name='關閉', exact=True).click()
            fv.get_by_role('button', name='查看整趟搭車名單', exact=True).click()
            expect(fv.locator(f'[data-trip-billable="{fd}"]')).to_have_text('0')
            assert fv.locator('#expense-dialog input:not(:disabled),#expense-dialog select:not(:disabled)').count() == 0
            fv.close()
            assert state() == saved_fleet
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{vehicle_ids[2]}"]').click()
            assert page.locator('#expense-dialog').bounding_box()['width'] == 390
            page.get_by_role('button', name='取消', exact=True).click()
            page.screenshot(path=str(artifacts / 'mobile-fleet.png'), full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{vehicle_ids[2]}"]').click()
            dialog(lambda: page.get_by_role('button', name='刪除此車', exact=True).click(), False)
            assert state() == saved_fleet
            dialog(lambda: page.get_by_role('button', name='刪除此車', exact=True).click())
            expect(page.locator('[data-transport="total"]')).to_have_text('15,900.0元')
            assert len(state()['vehicles']) == 2 and len(state()['carExpenses']) == 4
            assert state()['transportRides'] == saved_fleet['transportRides']
            record('逐車費用與名單在唯讀網頁可查看不能修改；手機可操作，刪車須確認且不影響其他車與名單')
            dialog(lambda: page.get_by_role('button', name='重設全部', exact=True).click())

            # New personal ledger: exercise real purpose, +/- controls and settlement transfers.
            roster()
            for i, name in enumerate(['甲旅伴', '乙旅伴', '丙旅伴']):
                page.locator('[data-field="member-name"]').nth(i).fill(name)
            roster(False)
            fresh = state()
            pa, pb, pc = [m['id'] for m in fresh['members'][:3]]
            first_day = fresh['days'][0]
            bill = first_day['expenses'][0]['id']
            tab(first_day['id'])
            set_fee(bill, '6740', [pa], pb)
            tab('personal')
            page.locator('#personal-person').select_option(pa)

            def add_entry(kind, description, amount, direction='plus', payer=None):
                page.get_by_role('button', name='＋ 新增個人紀錄', exact=True).click()
                entry_id = state()['ledger'][-1]['id']
                page.locator('#ledger-type-' + entry_id).select_option(kind)
                page.locator('#ledger-description-' + entry_id).fill(description)
                if kind == 'adjustment':
                    page.locator('#ledger-direction-' + entry_id).select_option(direction)
                page.locator('#ledger-amount-' + entry_id).fill(amount)
                if payer:
                    page.locator('#ledger-payer-' + entry_id).select_option(payer)
                return entry_id

            extra = add_entry('adjustment', '額外車資', '4000')
            expect(page.locator('[data-personal="pay"]')).to_have_text('10,740.0 元')
            page.locator('#ledger-direction-' + extra).select_option('minus')
            expect(page.locator('[data-personal="pay"]')).to_have_text('2,740.0 元')
            expect(page.locator('[data-personal="paid"]')).to_have_text('0.0 元')
            page.locator('#ledger-direction-' + extra).select_option('plus')
            page.locator('#ledger-amount-' + extra).fill('3540')
            expect(page.locator('[data-personal="pay"]')).to_have_text('10,280.0 元')
            dialog(lambda: page.locator(f'[data-action="delete-ledger"][data-ledger="{extra}"]').click(), False)
            assert len(state()['ledger']) == 1
            dialog(lambda: page.locator(f'[data-action="delete-ledger"][data-ledger="{extra}"]').click())
            expect(page.locator('[data-personal="pay"]')).to_have_text('6,740.0 元')
            record('個人用途、金額與正負可實際編輯，多筆紀錄與刪除確認正常，加減收不當作繳款')

            payment = add_entry('payment', '出發前先收', '10000')
            expect(page.locator('#ledger-direction-' + payment)).to_be_disabled()
            expect(page.locator('#ledger-direction-' + payment)).to_have_value('minus')
            expect(page.locator('[data-personal="receive"]')).to_have_text('3,260.0 元')
            expect(page.locator('#personal-refund-note')).to_contain_text('甲旅伴尚待收回 3,260.0 元')
            expect(page.locator('#personal-scroll')).to_contain_text('繳款人：甲旅伴 → 主辦人（預收團費）')
            assert not any(e['type']=='refund' for e in state()['ledger'])
            before_refund=state();page.reload();assert state()==before_refund
            expect(page.locator('#personal-refund-note')).to_contain_text('不要修改原本的已繳款')
            record('預繳10000但只負擔6740，清楚顯示繳款人與待退3260；刷新保留，未實際退款不會自動新增退款')
            refund = add_entry('refund', '已退回差額', '3260')
            expect(page.locator('[data-personal="receive"]')).to_have_text('0.0 元')
            expect(page.locator('[data-personal="pay"]')).to_have_text('0.0 元')
            expect(page.locator('#personal-refund-note')).to_have_count(0)
            expect(page.locator('#personal-scroll')).to_contain_text('主辦人 → 甲旅伴（已退款）')
            tab('overview')
            expect(page.locator('#total-expense')).to_have_text('6,740.0元')
            expect(page.locator('#collected-total')).to_contain_text('6,740.0 元')
            tab('personal')
            charge = add_entry('charge', '個人加購', '350', payer=pb)
            expect(page.locator('[data-personal="pay"]')).to_have_text('350.0 元')
            page.locator('#personal-person').select_option(pb)
            expect(page.locator('[data-personal="advanced"]')).to_have_text('7,090.0 元')
            expect(page.locator('.advance-list')).to_contain_text('350.0 元')
            # A payer may be viewing an advance for someone else's purchase: follow the owner.
            page.locator(f'.advance-list [data-ledger="{charge}"]').click()
            expect(page.locator('#personal-person')).to_have_value(pa)
            expect(page.locator('#ledger-amount-' + charge)).to_be_focused()
            record('先收款、應退差額、實際退款與個人加購分開計算；代墊自動彙總且來源可點選')

            bad = add_entry('adjustment', '待核對', '-1')
            expect(page.locator('#ledger-amount-' + bad)).to_have_attribute('aria-invalid', 'true')
            tab('overview')
            expect(page.locator('#pending-issues')).to_contain_text('待核對')
            page.locator(f'[data-action="goto-expense"][data-ledger="{bad}"]').click()
            expect(page.locator('#ledger-amount-' + bad)).to_be_focused()
            page.locator('#ledger-amount-' + bad).fill('0')
            roster()
            page.locator('[data-field="member-name"]').first.fill('改名甲')
            roster(False)
            expect(page.locator('#personal-person')).to_have_value(pa)
            expect(page.locator('[data-personal="pay"]')).to_have_text('350.0 元')
            saved_personal = state()
            page.reload()
            expect(page.locator('#tab-personal')).to_have_attribute('aria-selected', 'true')
            page.locator('#personal-person').select_option(pa)
            assert state() == saved_personal
            expect(page.locator('[data-personal="pay"]')).to_have_text('350.0 元')
            record('個人非法金額有提示與修正入口；改名與重新整理不遺失紀錄')

            # Backup / export includes every personal row and source advance.
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出 JSON', exact=True).click()
            personal_backup = artifacts / 'personal-backup.json'
            info.value.save_as(personal_backup)
            assert json.loads(personal_backup.read_text()) == saved_personal
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出試算表', exact=True).click()
            personal_xlsx = artifacts / 'personal-report.xlsx'
            info.value.save_as(personal_xlsx)
            exported = load_workbook(personal_xlsx)
            assert exported['個人紀錄']['A4'].value == '改名甲'
            assert exported['個人紀錄']['G4'].value == -10000
            assert exported['個人紀錄']['G5'].value == 3260
            assert exported['整趟總覽']['J7'].value == 10000
            assert exported['整趟總覽']['K7'].value == 3260
            assert exported['整趟總覽']['L7'].value == 350
            assert exported['代墊明細']['C4'].value == 6740
            dialog(lambda: page.get_by_role('button', name='重設全部', exact=True).click())
            dialog(lambda: page.locator('#import-file').set_input_files(str(personal_backup)))
            assert state() == saved_personal
            record('實際匯出及還原含個人紀錄的 JSON；XLSX 含正負、收退款、總覽與代墊來源')

            tab('personal')
            page.locator('#personal-person').select_option(pa)
            with page.expect_download() as info:
                page.get_by_role('button', name='匯出唯讀網頁', exact=True).click()
            personal_view = artifacts / 'personal-view.html'
            info.value.save_as(personal_view)
            context.route('**/personal-view.html', lambda r: r.fulfill(path=str(personal_view), content_type='text/html; charset=utf-8'))
            viewer = context.new_page()
            viewer.goto(url.replace('index.html', 'personal-view.html'))
            viewer.locator('#tab-personal').click()
            expect(viewer.locator('#personal-person')).to_be_enabled()
            viewer.locator('#personal-person').select_option(pb)
            expect(viewer.locator('[data-personal="advanced"]')).to_have_text('7,090.0 元')
            assert viewer.locator('#view input:not(:disabled),#view select:not(:disabled):not(#personal-person)').count() == 0
            expect(viewer.locator('[data-action="add-ledger"]')).to_be_hidden()
            viewer.close()
            assert state() == saved_personal
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(artifacts / 'mobile-personal.png'), full_page=True)
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.screenshot(path=str(artifacts / 'desktop-personal.png'), full_page=True)
            record('唯讀快照可切換查看不同成員但不能修改，個人明細在手機寬度無整頁溢出')

            # Deletion must clean up both ownership and other people's payer references.
            roster()
            dialog(lambda: page.locator('[data-action="delete-member"]').nth(1).click(), False)
            assert state() == saved_personal
            dialog(lambda: page.locator('[data-action="delete-member"]').nth(1).click())
            assert all(e['payerId'] != pb and e['personId'] != pb for e in state()['ledger'])
            assert next(e for e in state()['ledger'] if e['id'] == charge)['payerId'] is None
            tab('overview')
            expect(page.locator('#pending-issues')).to_contain_text('代墊者')
            record('刪成員需確認，個人紀錄與代墊參照一併處理，不留未知 ID')

            # Families are billed through a representative, but split by actual headcount.
            dialog(lambda:page.get_by_role('button',name='重設全部',exact=True).click())
            roster()
            family=state();fa,fb,fc=[m['id'] for m in family['members'][:3]]
            for pid,name,count in [(fa,'甲家庭',4),(fb,'乙家庭',2),(fc,'司機丙',1)]:
                page.locator('#name-'+pid).fill(name)
                page.locator('#count-'+pid).fill(str(count))
            expect(page.locator('#roster-summary')).to_contain_text('實際 7 人')
            for invalid in ['0','1.5','101','']:
                page.locator('#count-'+fa).fill(invalid)
                assert state()['members'][0]['count']==4
                expect(page.locator('#count-'+fa)).to_have_attribute('aria-invalid','true')
            page.locator('#count-'+fa).fill('4')
            roster(False)
            family=state();family_days=[d['id'] for d in family['days']]
            family_meal=next(e['id'] for e in family['days'][0]['expenses'] if e['category']=='breakfast')
            family_other=next(e['id'] for e in family['days'][0]['expenses'] if e['category']=='other')
            tab(family_days[0]);set_fee(family_meal,'700',[fa,fb,fc],fa);set_fee(family_other,'60',[fa,fb],fa)
            share(family_meal,fa,'400.0');share(family_meal,fb,'200.0');share(family_meal,fc,'100.0')
            expect(page.locator(f'[data-count="{family_meal}"]')).to_have_text('7')
            expect(page.locator('#daily-scroll')).to_contain_text('代表 4 人')
            record('代表人數真實輸入4／2／1共7人，餐費依實際人數分攤；無效或空白人數不覆蓋原值')
            for did in family_days:
                set_use_car(did);set_driver(did,fc)
            tab('vehicles')
            page.get_by_role('button',name='＋ 新增車輛',exact=True).click()
            page.locator('#vehicle-name').fill('家庭旅遊車')
            page.locator('#vehicle-rental-amount').fill('900');page.locator('#vehicle-fuel-amount').fill('300')
            page.locator('#vehicle-rental-payer').select_option(fa);page.locator('#vehicle-fuel-payer').select_option(fb)
            page.get_by_role('button',name='儲存車輛',exact=True).click()
            page.get_by_role('button',name='設定整趟搭車名單',exact=True).click()
            page.locator('#expense-dialog [data-action="select-all"]').click()
            for did in family_days[2:]:ride(did,fb).uncheck()
            expect(page.locator(f'[data-trip-billable="{fa}"]')).to_have_text('16')
            expect(page.locator(f'[data-trip-billable="{fb}"]')).to_have_text('4')
            expect(page.locator('#expense-preview')).to_contain_text('20 人天')
            page.get_by_role('button',name='儲存搭車名單',exact=True).click()
            expect(page.locator(f'[data-vehicle="{fa}-rental"]')).to_have_text('720.0')
            expect(page.locator(f'[data-vehicle="{fa}-fuel"]')).to_have_text('240.0')
            set_driver(family_days[0],fa)
            page.get_by_role('button',name='設定整趟搭車名單',exact=True).click()
            expect(page.locator(f'[data-trip-billable="{fa}"]')).to_have_text('15')
            expect(page.locator('#expense-dialog')).to_contain_text('司機免1人 · 家人照計')
            page.get_by_role('button',name='取消',exact=True).click()
            expect(page.locator(f'[data-transport-person="{fa}-total"]')).to_have_text('947.5')
            set_driver(family_days[0],fa,False)
            tab('personal');page.locator('#personal-person').select_option(fa)
            expect(page.locator('[data-personal-source="transport"]')).to_have_text('960.0')
            expect(page.locator('[data-personal="advanced"]')).to_have_text('1,660.0 元')
            expect(page.locator('[data-personal="receive"]')).to_have_text('260.0 元')
            roster();page.locator('#count-'+fa).fill('2');roster(False)
            expect(page.locator('[data-personal-source="meals"]')).to_have_text('280.0')
            expect(page.locator('[data-personal-source="transport"]')).to_have_text('800.0')
            expect(page.locator('[data-personal="receive"]')).to_have_text('550.0 元')
            roster();page.locator('#count-'+fa).fill('4');roster(False)
            record('家庭乘車人天按人數乘天數，家庭司機只免1人；修改人數即時重算且代墊不倍乘')
            family_payment=add_entry('payment','家庭已繳款','100')
            family_adjustment=add_entry('adjustment','家庭其他加收','10')
            expect(page.locator('[data-personal="paid"]')).to_have_text('100.0 元')
            expect(page.locator('[data-personal="adjustment"]')).to_have_text('10.0 元')
            expect(page.locator('[data-personal="receive"]')).to_have_text('350.0 元')
            expect(page.locator('#personal-family-note')).to_contain_text('代表 4 人')
            source_ids=[e['id'] for e in state()['carExpenses']]
            roster();page.locator('#name-'+fa).fill('甲家庭改名');roster(False)
            assert [e['id'] for e in state()['carExpenses']]==source_ids
            expect(page.locator('[data-personal="receive"]')).to_have_text('350.0 元')
            page.screenshot(path=str(artifacts/'family-personal.png'),full_page=True)
            record('家庭帳戶統一結算，代墊、繳款與加減只扣加一次；改名保留費用、人數與收付來源')
            family_saved=state()
            with page.expect_download() as info:page.get_by_role('button',name='匯出 JSON',exact=True).click()
            family_json=artifacts/'family-backup.json';info.value.save_as(family_json)
            assert json.loads(family_json.read_text())==family_saved
            with page.expect_download() as info:page.get_by_role('button',name='匯出試算表',exact=True).click()
            family_xlsx=artifacts/'family-report.xlsx';info.value.save_as(family_xlsx);report=load_workbook(family_xlsx)
            assert report['成員名單']['B4'].value==4 and report['成員名單']['B5'].value==2
            assert report['每餐參與名單']['E4'].value==7
            assert '代表4人' in report['每餐參與名單']['D4'].value
            assert report['車資逐日核對']['M4'].value==4
            assert report['車資逐日核對']['N4'].value==16
            assert report['車資逐日核對']['O4'].value==4
            assert report['個人結算']['M4'].value==350
            page.reload();assert state()==family_saved
            bad=json.loads(family_json.read_text());bad['members'][0]['count']=0
            bad_file=artifacts/'bad-family-count.json';bad_file.write_text(json.dumps(bad,ensure_ascii=False))
            page.locator('#import-file').set_input_files(str(bad_file));expect(page.locator('#flash')).to_contain_text('代表人數')
            assert state()==family_saved
            dialog(lambda:page.get_by_role('button',name='重設全部',exact=True).click())
            dialog(lambda:page.locator('#import-file').set_input_files(str(family_json)))
            assert state()==family_saved
            record('家庭人數可實際JSON備份還原及重新整理保存；錯誤人數拒絕覆蓋，XLSX有人數與人天核對')
            with page.expect_download() as info:page.get_by_role('button',name='匯出唯讀網頁',exact=True).click()
            family_view=artifacts/'family-view.html';info.value.save_as(family_view)
            context.route('**/family-view.html',lambda r:r.fulfill(path=str(family_view),content_type='text/html; charset=utf-8'))
            family_reader=context.new_page();family_reader.goto(url.replace('index.html','family-view.html'))
            family_reader.locator('#tab-personal').click();family_reader.locator('#personal-person').select_option(fa)
            expect(family_reader.locator('#personal-family-note')).to_contain_text('代表 4 人')
            expect(family_reader.locator('[data-personal="receive"]')).to_have_text('350.0 元');family_reader.close()
            page.set_viewport_size({'width':390,'height':844});roster()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.locator('#count-'+fa).fill('5');assert state()['members'][0]['count']==5
            page.locator('#count-'+fa).fill('4');assert state()==family_saved
            page.screenshot(path=str(artifacts/'family-mobile-roster.png'),full_page=True)
            roster(False);page.set_viewport_size({'width':1440,'height':1000})
            record('唯讀快照正確顯示家庭人數與結算；390px手機可編輯代表人數且沒有整頁溢出')

            # Rental incentives reduce shared costs; full actual advances remain unchanged.
            discount_before=state();discount_vehicle=discount_before['vehicles'][0]['id']
            rental_id=next(e['id'] for e in discount_before['carExpenses'] if e['category']=='rental')
            tab('vehicles');page.locator(f'[data-action="edit-vehicle"][data-vehicle="{discount_vehicle}"]').click()
            page.locator('#vehicle-rental-discount').fill('100');page.locator('#vehicle-rental-discount-note').fill('點數折抵')
            expect(page.locator('#vehicle-rental-net')).to_have_text('800.0')
            expect(page.locator('#vehicle-preview')).to_contain_text('本車合計 1,100.0 元')
            page.get_by_role('button',name='取消',exact=True).click();assert state()==discount_before
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{discount_vehicle}"]').click()
            page.locator('#vehicle-rental-discount').fill('100');page.locator('#vehicle-rental-discount-note').fill('點數折抵')
            page.get_by_role('button',name='儲存車輛',exact=True).click()
            expect(page.locator(f'[data-fleet="{discount_vehicle}-rental-gross"]')).to_have_text('900.0')
            expect(page.locator(f'[data-fleet="{discount_vehicle}-rental"]')).to_have_text('800.0')
            expect(page.locator(f'[data-fleet="{discount_vehicle}-discount"]')).to_contain_text('100.0')
            expect(page.locator('[data-transport="total"]')).to_have_text('1,100.0元')
            tab('overview');expect(page.locator('#total-expense')).to_have_text('1,860.0元')
            expect(page.locator('#total-difference')).to_have_text('0.0元')
            tab('personal');page.locator('#personal-person').select_option(fa)
            expect(page.locator('[data-personal-source="transport"]')).to_have_text('880.0')
            expect(page.locator('[data-personal="advanced"]')).to_have_text('1,660.0 元')
            expect(page.locator('[data-personal="receive"]')).to_have_text('430.0 元')
            assert state()['ledger']==discount_before['ledger']
            record('每台車可填原租金、優惠及說明，取消不寫入；優惠另列收入抵分攤，代墊仍保留原支付全額，家庭結算正確')
            page.locator(f'[data-action="goto-expense"][data-expense="{rental_id}"]').click()
            expect(page.locator('#discount-'+rental_id)).to_have_value('100')
            page.locator('#discount-'+rental_id).fill('50');save_fee()
            expect(page.locator(f'[data-fleet="{discount_vehicle}-rental"]')).to_have_text('850.0')
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{discount_vehicle}"]').click()
            page.locator('#vehicle-rental-discount').fill('1000')
            expect(page.locator('#vehicle-rental-net')).to_have_text('待修正')
            expect(page.locator('#vehicle-rental-discount')).to_have_attribute('aria-invalid','true')
            page.get_by_role('button',name='儲存車輛',exact=True).click()
            expect(page.locator(f'[data-fleet="{discount_vehicle}-rental"]')).to_have_text('待修正')
            tab('overview');expect(page.locator('#pending-issues')).to_contain_text('不可超過租金實付')
            page.locator(f'[data-action="goto-expense"][data-expense="{rental_id}"]').click()
            expect(page.locator('#discount-'+rental_id)).to_be_focused()
            page.locator('#discount-'+rental_id).fill('900');save_fee()
            expect(page.locator(f'[data-fleet="{discount_vehicle}-rental"]')).to_have_text('0.0')
            expect(page.locator('[data-transport="rental"]')).to_have_text('0.0元')
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{discount_vehicle}"]').click()
            page.locator('#vehicle-rental-discount').fill('100');page.get_by_role('button',name='儲存車輛',exact=True).click()
            record('租金來源視窗也能改優惠；超額折抵清楚提示且保留草稿，前往處理聚焦優惠，全額折抵為0')
            discount_saved=state()
            with page.expect_download() as info:page.get_by_role('button',name='匯出 JSON',exact=True).click()
            discount_json=artifacts/'rental-discount-backup.json';info.value.save_as(discount_json)
            assert json.loads(discount_json.read_text())==discount_saved
            with page.expect_download() as info:page.get_by_role('button',name='匯出試算表',exact=True).click()
            discount_xlsx=artifacts/'rental-discount-report.xlsx';info.value.save_as(discount_xlsx);report=load_workbook(discount_xlsx)
            assert report['各車交通費']['B4'].value==800 and report['各車交通費']['D4'].value==1100
            assert report['各車交通費']['H4'].value==900 and report['各車交通費']['I4'].value==100
            assert report['各車交通費']['J4'].value=='點數折抵'
            assert report['整趟車資']['G4'].value==900 and report['整趟車資']['H4'].value==100
            assert report['整趟總覽']['B3'].value==1860 and report['個人結算']['M4'].value==430
            page.reload();assert state()==discount_saved
            bad=json.loads(discount_json.read_text());next(e for e in bad['carExpenses'] if e['category']=='rental')['discount']=100
            bad_file=artifacts/'bad-discount-format.json';bad_file.write_text(json.dumps(bad,ensure_ascii=False))
            page.locator('#import-file').set_input_files(str(bad_file));expect(page.locator('#flash')).to_contain_text('租車優惠')
            assert state()==discount_saved
            dialog(lambda:page.get_by_role('button',name='重設全部',exact=True).click())
            dialog(lambda:page.locator('#import-file').set_input_files(str(discount_json)));assert state()==discount_saved
            tab('vehicles')
            with page.expect_download() as info:page.get_by_role('button',name='匯出唯讀網頁',exact=True).click()
            discount_view=artifacts/'rental-discount-view.html';info.value.save_as(discount_view)
            context.route('**/rental-discount-view.html',lambda r:r.fulfill(path=str(discount_view),content_type='text/html; charset=utf-8'))
            dr=context.new_page();dr.goto(url.replace('index.html','rental-discount-view.html'));dr.locator('#tab-vehicles').click()
            dr.locator(f'[data-action="edit-vehicle"][data-vehicle="{discount_vehicle}"]').click()
            expect(dr.locator('#vehicle-rental-discount')).to_have_value('100');expect(dr.locator('#vehicle-rental-discount')).to_be_disabled()
            expect(dr.locator('#vehicle-rental-net')).to_have_text('800.0');dr.close()
            page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{discount_vehicle}"]').click()
            page.locator('#vehicle-rental-discount').fill('120');expect(page.locator('#vehicle-rental-net')).to_have_text('780.0')
            page.screenshot(path=str(artifacts/'rental-discount-mobile.png'))
            page.get_by_role('button',name='取消',exact=True).click();assert state()==discount_saved
            page.set_viewport_size({'width':1440,'height':1000})
            record('優惠JSON保存還原及XLSX原價／優惠／實付正確，唯讀不能改；手機可填優惠，取消不寫入')

            # Actual per-person/day full/half selectors; shared across every vehicle receipt.
            half_day=family_days[-1]
            def duration(did,pid):
                return page.locator(f'#expense-dialog [data-field="trip-duration"][data-day="{did}"][data-person="{pid}"]')
            def open_plan():
                tab('vehicles');page.get_by_role('button',name='設定整趟搭車名單',exact=True).click()
            open_plan()
            expect(duration(half_day,fa)).to_have_value('2');expect(duration(half_day,fb)).to_be_disabled()
            duration(half_day,fa).select_option('1')
            expect(page.locator(f'[data-trip-billable="{fa}"]')).to_have_text('14')
            expect(page.locator(f'[data-trip-billable="{fb}"]')).to_have_text('4')
            expect(page.locator(f'[data-trip-total="{fa}"]')).to_have_text('855.7')
            assert state()==discount_saved
            ride(half_day,fa).uncheck();expect(duration(half_day,fa)).to_be_disabled()
            expect(duration(half_day,fa)).to_have_value('1');ride(half_day,fa).check()
            date_choice=page.locator(f'[data-field="trip-day"][data-day="{half_day}"]')
            date_choice.uncheck();date_choice.check();expect(duration(half_day,fa)).to_have_value('1')
            page.get_by_role('button',name='取消',exact=True).click();assert state()==discount_saved
            open_plan();duration(half_day,fa).select_option('1')
            page.screenshot(path=str(artifacts/'half-day-desktop.png'))
            page.get_by_role('button',name='儲存搭車名單',exact=True).click()
            half_saved=state()
            assert half_saved['transportRides'][-1]['halfDayIds']==[fa]
            assert all(e['rides'][-1]['halfDayIds']==[fa] for e in half_saved['carExpenses'])
            tab('personal');page.locator('#personal-person').select_option(fa)
            expect(page.locator('[data-personal-source="transport"]')).to_have_text('855.7')
            expect(page.locator('[data-personal="advanced"]')).to_have_text('1,660.0 元')
            expect(page.locator('[data-personal="receive"]')).to_have_text('454.3 元')
            expect(page.locator('[data-personal-source="meals"]')).to_have_text('400.0')
            record('逐人逐日全天／半天可操作，4人半天算2人天；取消不寫入，切換參與及日期保留時長，儲存同步各車租金油錢')
            set_driver(half_day,fa,True);open_plan()
            expect(page.locator(f'[data-trip-billable="{fa}"]')).to_have_text('13.5')
            expect(page.locator(f'[data-trip-billable="{fc}"]')).to_have_text('0')
            expect(page.locator(f'[data-trip-total="{fa}"]')).to_have_text('848.7')
            page.get_by_role('button',name='取消',exact=True).click();set_driver(half_day,fa,False)
            assert state()==half_saved
            record('家庭半天司機僅免本人半人天，家人仍計費；多位司機及司機輪替立即重算，優惠與代墊不倍乘')
            with page.expect_download() as info:page.get_by_role('button',name='匯出 JSON',exact=True).click()
            half_json=artifacts/'half-day-backup.json';info.value.save_as(half_json)
            assert json.loads(half_json.read_text())==half_saved
            with page.expect_download() as info:page.get_by_role('button',name='匯出試算表',exact=True).click()
            half_xlsx=artifacts/'half-day-report.xlsx';info.value.save_as(half_xlsx);report=load_workbook(half_xlsx)
            rows=[row for row in report['車資逐日核對'].iter_rows(min_row=4,values_only=True) if row[4]=='甲家庭改名']
            assert sum(row[15]==0.5 for row in rows)==2
            assert all(row[13]==14 for row in rows)
            assert all(row[16]==4 for row in rows if row[15]==0.5)
            assert report['個人結算']['M4'].value==454.3
            page.reload();assert state()==half_saved
            bad=json.loads(half_json.read_text());bad['transportRides'][-1]['halfDayIds']=['unknown']
            bad_file=artifacts/'bad-half-day.json';bad_file.write_text(json.dumps(bad,ensure_ascii=False))
            page.locator('#import-file').set_input_files(str(bad_file));expect(page.locator('#flash')).to_contain_text('參照錯誤')
            assert state()==half_saved
            dialog(lambda:page.get_by_role('button',name='重設全部',exact=True).click())
            dialog(lambda:page.locator('#import-file').set_input_files(str(half_json)));assert state()==half_saved
            roster();delete_half_person=page.locator(f'[data-action="delete-member"][data-person="{fa}"]')
            dialog(lambda:delete_half_person.click(),False);assert state()==half_saved
            dialog(lambda:delete_half_person.click())
            assert all(fa not in ride.get('halfDayIds',[]) for ride in state()['transportRides'])
            assert all(fa not in ride.get('halfDayIds',[]) for e in state()['carExpenses'] for ride in e['rides'])
            with page.expect_download() as info:page.get_by_role('button',name='匯出 JSON',exact=True).click()
            info.value.save_as(artifacts/'half-day-after-delete.json')
            dialog(lambda:page.locator('#import-file').set_input_files(str(half_json)));assert state()==half_saved
            roster(False)
            record('半天JSON實際下載還原及重新整理一致，無效參照不覆蓋；刪人確認並清除時長關聯，XLSX人天與結算正確')
            tab('vehicles')
            with page.expect_download() as info:page.get_by_role('button',name='匯出唯讀網頁',exact=True).click()
            half_view=artifacts/'half-day-view.html';info.value.save_as(half_view)
            context.route('**/half-day-view.html',lambda r:r.fulfill(path=str(half_view),content_type='text/html; charset=utf-8'))
            hr=context.new_page();hr.goto(url.replace('index.html','half-day-view.html'));hr.locator('#tab-vehicles').click()
            hr.get_by_role('button',name='查看整趟搭車名單',exact=True).click()
            hs=hr.locator(f'[data-field="trip-duration"][data-day="{half_day}"][data-person="{fa}"]')
            expect(hs).to_have_value('1');expect(hs).to_be_disabled();hr.close()
            page.set_viewport_size({'width':390,'height':844});open_plan()
            duration(half_day,fa).select_option('2');expect(page.locator(f'[data-trip-billable="{fa}"]')).to_have_text('16')
            duration(half_day,fa).select_option('1');expect(page.locator(f'[data-trip-billable="{fa}"]')).to_have_text('14')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(artifacts/'half-day-mobile.png'))
            page.get_by_role('button',name='儲存搭車名單',exact=True).click();page.reload();assert state()==half_saved
            page.set_viewport_size({'width':1440,'height':1000})
            record('唯讀網頁保留半天且不能修改；390px手機可選全天／半天、儲存及刷新，沒有整頁水平溢出')

            # A separate incentive must never reduce the representative's actual payment.
            tab('vehicles');page.locator(f'[data-action="edit-vehicle"][data-vehicle="{discount_vehicle}"]').click()
            page.locator('#vehicle-rental-amount').fill('5250');page.locator('#vehicle-rental-discount').fill('400')
            expect(page.locator('#vehicle-rental-net')).to_have_text('4,850.0')
            expect(page.locator('#vehicle-preview')).to_contain_text('租金代墊仍為 5,250.0 元')
            page.get_by_role('button',name='儲存車輛',exact=True).click()
            tab('personal');page.locator('#personal-person').select_option(fa)
            expect(page.locator('[data-personal="advanced"]')).to_have_text('6,010.0 元')
            expect(page.locator('[data-personal="receive"]')).to_have_text('1,654.3 元')
            page.locator(f'[data-action="goto-expense"][data-expense="{rental_id}"]').click()
            expect(page.locator('#expense-preview')).to_contain_text('代墊 5,250.0 元')
            page.locator('#discount-'+rental_id).fill('200');save_fee()
            tab('personal');expect(page.locator('[data-personal="advanced"]')).to_have_text('6,010.0 元')
            page.locator(f'[data-action="goto-expense"][data-expense="{rental_id}"]').click()
            page.locator('#discount-'+rental_id).fill('400');save_fee()
            tab('overview');expect(page.locator('#total-expense')).to_have_text('5,910.0元')
            expect(page.locator('#total-cash-expense')).to_have_text('6,310.0')
            expect(page.locator('#total-income')).to_have_text('400.0')
            expect(page.locator('#total-difference')).to_have_text('0.0元')
            assert state()['ledger']==half_saved['ledger']
            record('實付5250與優惠收入400分開記錄，修改收入不改代墊；家庭半天結算及團體收入核對正確')
            income_saved=state()
            with page.expect_download() as info:page.get_by_role('button',name='匯出 JSON',exact=True).click()
            income_json=artifacts/'rental-income-backup.json';info.value.save_as(income_json)
            page.reload();assert state()==income_saved
            dialog(lambda:page.get_by_role('button',name='重設全部',exact=True).click())
            dialog(lambda:page.locator('#import-file').set_input_files(str(income_json)));assert state()==income_saved
            with page.expect_download() as info:page.get_by_role('button',name='匯出試算表',exact=True).click()
            income_xlsx=artifacts/'rental-income-report.xlsx';info.value.save_as(income_xlsx);report=load_workbook(income_xlsx)
            assert report['整趟車資']['J4'].value==5250
            assert report['整趟總覽']['J3'].value==6310 and report['整趟總覽']['L3'].value==400
            assert report['個人結算']['H4'].value==6010 and report['個人結算']['M4'].value==1654.3
            assert 'v1.8.1' in report['整趟總覽']['A1'].value
            assert report['使用說明']['B3'].value=='v1.8.1'
            with page.expect_download() as info:page.get_by_role('button',name='匯出唯讀網頁',exact=True).click()
            income_view=artifacts/'rental-income-view.html';info.value.save_as(income_view)
            context.route('**/rental-income-view.html',lambda r:r.fulfill(path=str(income_view),content_type='text/html; charset=utf-8'))
            ir=context.new_page();ir.goto(url.replace('index.html','rental-income-view.html'))
            expect(ir).to_have_title('旅伴帳本 v1.8.1｜唯讀旅遊費用')
            expect(ir.locator('#app-version')).to_have_text('v1.8.1')
            expect(ir.locator('#total-income')).to_have_text('400.0')
            ir.locator('#tab-personal').click();ir.locator('#personal-person').select_option(fa)
            expect(ir.locator('[data-personal="advanced"]')).to_have_text('6,010.0 元');ir.close()
            record('收入與全額代墊可保存還原及匯出，頁面／分頁標題／唯讀網頁／XLSX一致顯示v1.8.1')

            # Names are visible at the original costs and the prepaid group-payment row.
            current=state();rent_cost=next(e for e in current['carExpenses'] if e['category']=='rental')
            gas_cost=next(e for e in current['carExpenses'] if e['category']=='fuel')
            tab('vehicles')
            expect(page.locator(f'[data-payer="{rent_cost["id"]}"]')).to_have_text('代墊：甲家庭改名')
            expect(page.locator(f'[data-payer="{gas_cost["id"]}"]')).to_have_text('代墊：乙家庭')
            old_name=current['members'][0]['name'];display_name='甲主辦人 & <同行>'
            roster();page.locator('#name-'+fa).fill(display_name);roster(False)
            expect(page.locator(f'[data-payer="{rent_cost["id"]}"]')).to_have_text('代墊：'+display_name)
            tab(family_days[0]);meal_id=next(e['id'] for e in state()['days'][0]['expenses'] if e['category']=='breakfast')
            expect(page.locator(f'[data-payer="{meal_id}"]')).to_have_text('代墊：'+display_name)
            assert page.locator('.daily-compact-row').bounding_box()['height']<=64
            tab('personal');page.locator('#personal-person').select_option(fa)
            expect(page.locator('#personal-advance-person')).to_contain_text('代墊者：'+display_name)
            expect(page.locator('#personal-advance-person')).to_contain_text('6,010.0 元')
            expect(page.locator('#personal-scroll')).to_contain_text('繳款人：'+display_name+' → 主辦人（預收團費）')
            assert page.locator('#personal-advances').evaluate('(el)=>Boolean(el.compareDocumentPosition(document.querySelector("#personal-scroll")) & Node.DOCUMENT_POSITION_FOLLOWING)')
            expect(page.locator('[data-personal="advanced"]')).to_have_text('6,010.0 元')
            expect(page.locator('[data-personal="receive"]')).to_have_text('1,654.3 元')
            page.screenshot(path=str(artifacts/'visible-payers-personal.png'),full_page=True)
            roster();page.locator('#name-'+fa).fill('');roster(False);tab('vehicles')
            expect(page.locator(f'[data-payer="{rent_cost["id"]}"]')).to_have_text('代墊：原代墊者未填姓名')
            roster();page.locator('#name-'+fa).fill(old_name);roster(False)
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{discount_vehicle}"]').click()
            page.locator('#vehicle-rental-payer').select_option('');page.get_by_role('button',name='儲存車輛',exact=True).click()
            expect(page.locator(f'[data-payer="{rent_cost["id"]}"]')).to_have_text('代墊：未指定代墊者')
            page.locator(f'[data-action="edit-vehicle"][data-vehicle="{discount_vehicle}"]').click()
            page.locator('#vehicle-rental-payer').select_option(fa);page.get_by_role('button',name='儲存車輛',exact=True).click()
            assert state()==current
            page.reload();expect(page.locator(f'[data-payer="{rent_cost["id"]}"]')).to_have_text('代墊：甲家庭改名')
            with page.expect_download() as info:page.get_by_role('button',name='匯出唯讀網頁',exact=True).click()
            payer_view=artifacts/'visible-payers-view.html';info.value.save_as(payer_view)
            context.route('**/visible-payers-view.html',lambda r:r.fulfill(path=str(payer_view),content_type='text/html; charset=utf-8'))
            pv=context.new_page();pv.goto(url.replace('index.html','visible-payers-view.html'));pv.locator('#tab-vehicles').click()
            expect(pv.locator(f'[data-payer="{rent_cost["id"]}"]')).to_have_text('代墊：甲家庭改名')
            pv.locator('#tab-personal').click();pv.locator('#personal-person').select_option(fa)
            expect(pv.locator('#personal-advance-person')).to_contain_text('6,010.0 元');pv.close()
            page.set_viewport_size({'width':390,'height':844});tab(family_days[0])
            expect(page.locator(f'[data-payer="{meal_id}"]')).to_have_text('代墊：甲家庭改名')
            assert page.locator('.daily-compact-row').bounding_box()['height']<=64
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
            page.screenshot(path=str(artifacts/'visible-payers-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1440,'height':1000})
            record('租金／油錢／每日費用直接顯示代墊者，個人代墊明細前移；改名安全同步、空白未指定清楚提示，唯讀及手機正確且標題仍不超過64px')

            assert not errors, errors
            assert not external_requests, external_requests
            record('整段操作無 JavaScript 例外，所有外部網路請求被攔截且實際為零')

            # A blocked storage environment must warn instead of claiming saved.
            unavailable = browser.new_context()
            unavailable.add_init_script("Storage.prototype.setItem = function() { throw new DOMException('Blocked', 'SecurityError'); };")
            p2 = unavailable.new_page()
            p2.goto(url)
            p2.locator('#roster summary').click()
            p2.locator('[data-field="member-name"]').first.fill('儲存測試')
            expect(p2.locator('#save-status')).to_have_text('儲存失敗，請立即匯出 JSON 備份')
            expect(p2.locator('#flash')).to_contain_text('修改尚在本頁')
            record('localStorage 被阻擋時不假稱已儲存，清楚提示匯出備份')
            unavailable.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    report = {'browser': 'Chromium', 'passed': passed, 'artifacts': str(artifacts), 'limitations': ['file:// navigation is blocked by the environment browser policy; tested using loopback HTTP instead.', 'Firefox, Safari and real mobile devices were not tested.']}
    (artifacts / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'\n{len(passed)} browser scenarios passed. Artifacts: {artifacts}', flush=True)


if __name__ == '__main__':
    main()
