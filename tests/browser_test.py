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
            expect(page).to_have_title('旅伴帳本｜旅遊費用分攤')
            expect(page.get_by_role('tab')).to_have_count(11)
            expect(page.locator('#total-expense')).to_have_text('0.0元')
            expect(page.locator('#roster-summary')).to_contain_text('0 位已填姓名 / 30 個名額')
            record('初始 30 人／10 天，無假支出，頁面無外部依賴')

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
                page.locator('#tab-' + day_id).click()

            def field(kind, eid):
                return page.locator(f'[data-field="{kind}"][data-expense="{eid}"]')

            def part(eid, pid):
                return page.locator(f'[data-field="participation"][data-expense="{eid}"][data-person="{pid}"]')

            def share(eid, pid, value):
                expect(page.locator(f'[data-share="{eid}-{pid}"]')).to_have_text(value)

            def all_button(eid, select=True):
                return page.locator(f'[data-action="{"select-all" if select else "select-none"}"][data-expense="{eid}"]')

            def set_fee(eid, amount, participants, payer):
                field('amount', eid).fill(amount)
                field('payer', eid).select_option(payer)
                for pid in participants:
                    part(eid, pid).check()

            def driver(day_id, pid):
                return page.locator(f'#driver-{day_id}-{pid}')

            roster()
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

            tab(d1['id'])
            page.locator('[data-field="use-car"]').check()
            driver(d1['id'], a).check()
            set_fee(e1['fuel'], '100', [a, b, c], a)
            set_fee(e1['rental'], '120', [a, b, c], a)
            share(e1['fuel'], a, '0.0')
            share(e1['rental'], a, '0.0')
            share(e1['fuel'], b, '50.0')
            share(e1['breakfast'], a, '33.4')
            driver(d1['id'], a).uncheck()
            expect(page.locator('#expense-' + e1['fuel'])).to_contain_text('尚未設定具名司機')
            driver(d1['id'], b).check()
            share(e1['fuel'], a, '50.0')
            share(e1['fuel'], b, '0.0')
            driver(d1['id'], a).check()
            share(e1['fuel'], c, '100.0')
            driver(d1['id'], b).uncheck()
            record('變更司機、多位司機、缺司機提示、司機勾選車資免付且餐費照付')

            tab(d2['id'])
            page.locator('[data-field="use-car"]').check()
            driver(d2['id'], b).check()
            set_fee(e2['fuel'], '60', [a, b, c], b)
            share(e2['fuel'], a, '30.0')
            share(e2['fuel'], b, '0.0')
            tab(d1['id'])
            expect(driver(d1['id'], a)).to_be_checked()
            expect(driver(d1['id'], b)).not_to_be_checked()
            share(e1['fuel'], a, '0.0')
            tab(d2['id'])
            expect(driver(d2['id'], b)).to_be_checked()
            expect(part(e2['breakfast'], a)).not_to_be_checked()
            record('實際切換日期，A／B 每日司機與跨日用餐紀錄各自獨立')

            tab(d1['id'])
            dialog(lambda: page.locator('[data-field="use-car"]').click(), accept=False)
            expect(page.locator('[data-field="use-car"]')).to_be_checked()
            message = dialog(lambda: page.locator('[data-field="use-car"]').click())
            assert '仍會保留' in message
            expect(field('amount', e1['fuel'])).to_have_value('100')
            share(e1['fuel'], b, '0.0')
            expect(page.locator('#expense-' + e1['fuel'])).to_contain_text('當日未用車')
            tab('overview')
            expect(page.locator('#total-difference')).to_have_text('220.0元')
            tab(d1['id'])
            page.locator('[data-field="use-car"]').check()
            share(e1['fuel'], b, '50.0')
            record('取消用車須確認，可取消操作；費用不遺失、差額顯示，重新用車可恢復')

            field('amount', e1['breakfast']).fill('100.1')
            share(e1['breakfast'], a, '33.4')
            share(e1['breakfast'], b, '33.4')
            all_button(e1['lunch'], False).click()
            expect(page.locator('#expense-' + e1['lunch'])).to_contain_text('沒有可分攤者')
            share(e1['lunch'], b, '0.0')
            part(e1['lunch'], b).check()
            part(e1['lunch'], c).check()
            field('amount', e1['other']).fill('0')
            expect(page.locator('#expense-' + e1['other'] + ' .expense-status')).to_be_empty()
            field('amount', e1['other']).fill('-1')
            expect(field('amount', e1['other'])).to_have_attribute('aria-invalid', 'true')
            field('amount', e1['other']).fill('')
            record('修改金額即時計算，正金額無分攤者、0 元、空白與不合法金額正確處理')

            roster()
            page.locator('#name-' + last).fill('第30位旅伴')
            roster(False)
            all_button(e1['dinner'], False).click()
            part(e1['dinner'], last).check()
            field('payer', e1['dinner']).select_option(last)
            share(e1['dinner'], last, '70.0')
            record('第 30 位成員可真正點選、分攤及代墊')

            # Clearing a name must preserve its selected IDs; bulk operations touch named members only.
            roster()
            page.locator('#name-' + c).fill('   ')
            roster(False)
            expect(part(e1['breakfast'], c)).to_be_disabled()
            all_button(e1['breakfast'], False).click()
            expense = next(e for e in state()['days'][0]['expenses'] if e['id'] == e1['breakfast'])
            assert expense['participantIds'] == [c]
            all_button(e1['breakfast']).click()
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
            expect(driver(d1['id'], a)).to_be_checked()
            assert next(e for e in state()['days'][0]['expenses'] if e['id'] == e1['breakfast'])['payerId'] == a
            expect(part(e1['breakfast'], a)).to_be_checked()
            record('改姓名同步顯示，分攤、代墊、參與及司機 ID 不變')

            # Both sticky axes are verified from browser layout measurements.
            scroll = page.locator('#daily-scroll')
            scroll.evaluate('(el) => { el.scrollLeft=0; el.scrollTop=0; }')
            x = page.locator('tbody .name-cell').first.bounding_box()['x']
            y = page.locator('#expense-' + e1['breakfast']).bounding_box()['y']
            scroll.evaluate('(el) => { el.scrollLeft=450; el.scrollTop=550; }')
            assert abs(page.locator('tbody .name-cell').first.bounding_box()['x'] - x) < 2
            assert abs(page.locator('#expense-' + e1['breakfast']).bounding_box()['y'] - y) < 2
            scroll.evaluate('(el) => { el.scrollLeft=0; el.scrollTop=0; }')
            record('捲動時姓名欄固定在左，費用標題固定在上')

            page.get_by_role('button', name='＋ 新增費用', exact=True).click()
            added = state()['days'][0]['expenses'][-1]['id']
            field('expense-name', added).fill('加點早餐')
            field('category', added).select_option('breakfast')
            set_fee(added, '30', [a, b], b)
            share(added, a, '15.0')
            expect(part(e1['breakfast'], last)).to_be_checked()
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
            assert len(state()['days']) == 11
            dialog(lambda: page.get_by_role('button', name='刪除此天', exact=True).click())
            assert len(state()['days']) == 10
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
            field('amount', e1['breakfast']).fill('200')
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
            field('amount', e1['breakfast']).fill('999')
            editor_state = state()
            shared = context.new_page()
            shared.goto(url.replace('index.html', 'shared-view.html'))
            expect(shared).to_have_title('旅伴帳本｜唯讀旅遊費用')
            expect(shared.locator('.tools')).to_be_hidden()
            expect(shared.locator('#roster')).to_be_hidden()
            expect(shared.locator('[data-action="add-day"]')).to_be_hidden()
            assert shared.locator('.overview-table').inner_text() == overview
            expect(shared.locator('#save-status')).to_contain_text('唯讀快照 · 更新於')
            shared.locator('#tab-' + d1['id']).click()
            expect(shared.locator('#amount-' + e1['breakfast'])).to_have_value('100.1')
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
            assert len(state()['members']) == 30 and len(state()['days']) == 10
            record('載入範例有有效帳目且無待處理，重設確實恢復空白 30 人／10 天')

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
