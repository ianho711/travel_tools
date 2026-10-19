const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

// Run the exact engine shipped inside the offline HTML; no duplicate implementation.
const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const script = html.match(/<script id="trip-core">([\s\S]*?)<\/script>/)[1];
const C = vm.runInNewContext(script + '\nTripCore;', { crypto: webcrypto });
const json = value => JSON.stringify(value);
function fixture(count = 3, days = 2) {
  const state = C.createTrip(count, days);
  state.members.forEach((m, i) => { m.name = String.fromCharCode(65 + i); });
  return state;
}
function fill(s, di, category, amount, indices, payer = 0) {
  const e = s.days[di].expenses.find(e => e.category === category);
  Object.assign(e, { amount, participantIds: indices.map(i => s.members[i].id), payerId: s.members[payer].id });
  return e;
}
const calc = (s, di, e) => C.calculateExpense(s, s.days[di], e);
const shares = (s, r) => s.members.map(m => r.shares[m.id]);
const totalShares = r => Object.values(r.shares).reduce((a, b) => a + b, 0);

test('預設 30 個空白名額、10 天、每天 7 項費用，初始沒有假支出', () => {
  const s = C.createTrip();
  assert.equal(s.members.length, 30);
  assert.equal(s.days.length, 10);
  assert.ok(s.days.every(d => d.expenses.length === 7));
  assert.equal(C.summarize(s).total, 0);
  assert.equal(C.summarize(s).people.length, 0);
});

test('100 元由 3 人分攤為 33.4、33.3、33.3，尾差依固定名單順序', () => {
  const s = fixture();
  const e = fill(s, 0, 'breakfast', '100', [2, 0, 1]);
  const r = calc(s, 0, e);
  assert.equal(json(shares(s, r)), json([334, 333, 333]));
  assert.equal(totalShares(r), 1000);
});

test('三餐各自勾選，跨日及同類多筆的參與紀錄獨立', () => {
  const s = fixture();
  const b = fill(s, 0, 'breakfast', '100', [0, 1]);
  const l = fill(s, 0, 'lunch', '90', [1, 2]);
  const d = fill(s, 0, 'dinner', '70', [0, 2]);
  const tomorrow = fill(s, 1, 'breakfast', '20', [2]);
  assert.equal(json(shares(s, calc(s, 0, b))), json([500, 500, 0]));
  assert.equal(json(shares(s, calc(s, 0, l))), json([0, 450, 450]));
  assert.equal(json(shares(s, calc(s, 0, d))), json([350, 0, 350]));
  assert.equal(json(shares(s, calc(s, 1, tomorrow))), json([0, 0, 200]));
  const second = C.expense('breakfast');
  Object.assign(second, { amount: '5', participantIds: [s.members[1].id], payerId: s.members[0].id });
  s.days[0].expenses.push(second);
  assert.equal(json(shares(s, calc(s, 0, second))), json([0, 50, 0]));
  assert.equal(json(shares(s, calc(s, 0, b))), json([500, 500, 0]));
});

test('第一天 A、第二天 B 為司機，免分攤只作用當日', () => {
  const s = fixture();
  s.days.forEach((d, i) => { d.useCar = true; d.drivers = [s.members[i].id]; });
  const e1 = fill(s, 0, 'fuel', '100', [0, 1, 2]);
  const e2 = fill(s, 1, 'fuel', '100', [0, 1, 2]);
  assert.equal(json(shares(s, calc(s, 0, e1))), json([0, 500, 500]));
  assert.equal(json(shares(s, calc(s, 1, e2))), json([500, 0, 500]));
});

test('司機參與餐費、停車費與其他時照常分攤；勾選油錢與租車費仍為零', () => {
  const s = fixture();
  s.days[0].useCar = true;
  s.days[0].drivers = [s.members[0].id];
  for (const category of ['breakfast', 'lunch', 'dinner', 'parking', 'other']) {
    const e = fill(s, 0, category, '90', [0, 1, 2]);
    assert.equal(calc(s, 0, e).shares[s.members[0].id], 300);
  }
  for (const category of ['fuel', 'rental']) {
    const e = fill(s, 0, category, '90', [0, 1, 2]);
    assert.equal(json(shares(s, calc(s, 0, e))), json([0, 450, 450]));
  }
});

test('沒開車時車資保留、清楚列待處理、不分攤，不影響餐費', () => {
  const s = fixture();
  s.days[0].drivers = [s.members[0].id];
  const e = fill(s, 0, 'fuel', '80', [0, 1, 2]);
  const r = calc(s, 0, e);
  assert.ok(r.blocked);
  assert.ok(r.issues.some(i => i.code === 'no-car'));
  assert.equal(r.allocated, 0);
  assert.equal(e.amount, '80');
  assert.equal(C.summarize(s).difference, 800);
  const meal = fill(s, 0, 'lunch', '30', [0, 1, 2]);
  assert.equal(json(shares(s, calc(s, 0, meal))), json([100, 100, 100]));
});

test('有車資但未設定具名司機時提示並暫停該筆分攤', () => {
  const s = fixture(); s.days[0].useCar = true;
  const r = calc(s, 0, fill(s, 0, 'rental', '100', [0, 1, 2]));
  assert.equal(r.allocated, 0);
  assert.ok(r.issues.some(i => i.code === 'missing-driver'));
});

test('可有多位司機，全部排除於油錢與租車費分攤者之外', () => {
  const s = fixture(4); s.days[0].useCar = true;
  s.days[0].drivers = s.members.slice(0, 2).map(m => m.id);
  const r = calc(s, 0, fill(s, 0, 'fuel', '100', [0, 1, 2, 3]));
  assert.equal(json(shares(s, r)), json([0, 0, 500, 500]));
  assert.equal(r.eligible.length, 2);
});

test('正金額無參與者、或參與者全為司機，均不除以零且列待處理', () => {
  const s = fixture();
  let r = calc(s, 0, fill(s, 0, 'lunch', '100', []));
  assert.equal(r.allocated, 0);
  assert.ok(r.issues.some(i => i.code === 'no-participants'));
  s.days[0].useCar = true; s.days[0].drivers = s.members.map(m => m.id);
  r = calc(s, 0, fill(s, 0, 'fuel', '100', [0, 1, 2]));
  assert.equal(r.allocated, 0);
  assert.ok(r.issues.some(i => i.code === 'no-participants'));
  assert.ok(Object.values(r.shares).every(Number.isFinite));
});

test('0 元與空白金額正確處理，不產生假警告或 NaN', () => {
  const s = fixture();
  for (const raw of ['', ' ', '0', '0.0', '.0']) {
    const e = fill(s, 0, 'fuel', raw, []);
    e.payerId = null;
    const r = calc(s, 0, e);
    assert.equal(r.units, 0);
    assert.equal(r.allocated, 0);
    assert.equal(r.issues.length, 0);
  }
});

test('空白及全空格姓名不計人數，不作有效司機或代墊者，原參與 ID 保留', () => {
  const s = fixture(); s.members[0].name = '   ';
  const e = fill(s, 0, 'lunch', '100', [0, 1, 2]);
  assert.equal(json(shares(s, calc(s, 0, e))), json([0, 500, 500]));
  assert.ok(calc(s, 0, e).issues.some(i => i.code === 'missing-payer'));
  assert.equal(e.participantIds.length, 3);
  s.days[0].useCar = true; s.days[0].drivers = [s.members[0].id];
  assert.ok(calc(s, 0, fill(s, 0, 'fuel', '100', [0, 1, 2])).issues.some(i => i.code === 'missing-driver'));
});

test('第 30 人可正常參與、計費、代墊，不受位元遮罩或索引限制', () => {
  const s = C.createTrip();
  s.members[0].name = 'A'; s.members[29].name = '第 30 位';
  const e = fill(s, 0, 'dinner', '100', [0, 29], 29);
  const r = calc(s, 0, e);
  assert.equal(r.shares[s.members[29].id], 500);
  const p = C.summarize(s).people.find(p => p.id === s.members[29].id);
  assert.equal(p.advanced, 1000); assert.equal(p.receive, 500);
});

test('改姓名後固定 ID、司機、分攤、代墊及勾選不遺失，同名者不合併', () => {
  const s = fixture(); s.days[0].useCar = true; s.days[0].drivers = [s.members[0].id];
  const e = fill(s, 0, 'breakfast', '100', [0, 1, 2]);
  const before = C.summarize(s).people[0];
  const pid = s.members[0].id;
  s.members[0].name = s.members[1].name;
  const after = C.summarize(s).people[0];
  assert.equal(before.id, after.id); assert.equal(before.total, after.total); assert.equal(before.advanced, after.advanced);
  assert.equal(e.payerId, pid); assert.ok(e.participantIds.includes(pid)); assert.ok(s.days[0].drivers.includes(pid));
  assert.equal(C.summarize(s).people.length, 3);
});

test('JSON 匯出再匯入，全部資料與總覽完全一致', () => {
  const s = C.example();
  const restored = C.decode(C.encode(s));
  assert.equal(json(restored), json(s));
  assert.equal(json(C.summarize(restored)), json(C.summarize(s)));
});

test('所有有效費用個人分攤合計＝每筆總額＝全部支出；收付總額平衡', () => {
  const s = C.example(), summary = C.summarize(s);
  assert.equal(summary.issues.length, 0);
  for (const r of summary.expenses) {
    assert.equal(totalShares(r), r.units);
    assert.ok(Object.values(r.shares).every(n => Number.isSafeInteger(n) && n >= 0));
  }
  assert.equal(summary.total, summary.allocated);
  assert.equal(summary.difference, 0);
  assert.equal(summary.people.reduce((n, p) => n + p.advanced, 0), summary.total);
  assert.equal(summary.people.reduce((n, p) => n + p.pay, 0), summary.people.reduce((n, p) => n + p.receive, 0));
});

test('十進位解析使用 0.1 元整數單位，拒絕負數、超精度、非數字及溢位', () => {
  assert.equal(C.parseAmount('100.1').units, 1001);
  assert.equal(C.parseAmount('.5').units, 5);
  for (const raw of ['-1', '1.23', 'NaN', 'Infinity', '1e3', '1,000', '100000001', '999999999999999999999999999999']) {
    assert.equal(C.parseAmount(raw).ok, false, raw);
    const s = fixture();
    const r = calc(s, 0, fill(s, 0, 'lunch', raw, [0, 1]));
    assert.equal(r.allocated, 0); assert.ok(r.issues.some(i => i.code === 'invalid-amount'));
    assert.ok(Number.isFinite(C.summarize(s).total));
  }
});

test('大量 0.1 元分攤累加無浮點誤差，尾差分配穩定', () => {
  const s = fixture(); s.days[0].expenses = [];
  for (let i = 0; i < 100; i++) {
    const e = C.expense('other');
    Object.assign(e, { amount: '0.1', participantIds: s.members.map(m => m.id), payerId: s.members[0].id });
    s.days[0].expenses.push(e);
  }
  const r = C.summarize(s);
  assert.equal(r.total, 100); assert.equal(r.allocated, 100); assert.equal(r.people[0].total, 100);
});

test('金額與參與者的廣泛組合皆保持整數與逐筆守恆', () => {
  const s = fixture(30);
  for (const count of [1, 2, 3, 7, 29, 30]) for (const units of [0, 1, 2, 10, 999, 1000, 1001, 999999999]) {
    const e = fill(s, 0, 'other', `${Math.floor(units / 10)}.${units % 10}`, Array.from({length:count}, (_, i) => i));
    const r = calc(s, 0, e);
    assert.equal(totalShares(r), units);
    const values = r.eligible.map(id => r.shares[id]);
    assert.ok(Math.max(...values) - Math.min(...values) <= 1);
  }
});

test('匯入格式、版本、ID 重複、未知參照、錯誤型別及人數上限皆檢查，失敗不修改來源', () => {
  const original = C.example(), backup = C.encode(original);
  assert.throws(() => C.decode('{bad json'), /JSON/);
  const invalid = [
    s => { s.version = 9; },
    s => { s.members[1].id = s.members[0].id; },
    s => { s.days[1].id = s.days[0].id; },
    s => { s.days[0].expenses[1].id = s.days[0].expenses[0].id; },
    s => { s.days[0].drivers = ['unknown']; },
    s => { s.days[0].expenses[0].payerId = 'unknown'; },
    s => { s.days[0].expenses[0].participantIds = ['unknown']; },
    s => { s.days[0].expenses[0].amount = 100; },
    s => { s.days[0].expenses[0].category = '__proto__'; },
    s => { s.members = []; },
    s => { s.days = []; },
    s => { s.days[0].useCar = 'true'; },
    s => { s.members[0].name = 'x'.repeat(81); },
    s => { s.members = Array.from({length:201},(_,i)=>({id:'p'+i,name:''})); }
  ];
  for (const mutate of invalid) {
    const s = JSON.parse(backup); mutate(s);
    assert.throws(() => C.decode(JSON.stringify(s)));
    assert.equal(C.encode(original), backup);
  }
});

test('暫存的不合法金額可備份與還原，但不計入支出，持續顯示待處理', () => {
  const s = fixture(); fill(s, 0, 'lunch', '-1', [0, 1]);
  const restored = C.decode(C.encode(s));
  assert.equal(restored.days[0].expenses[1].amount, '-1');
  assert.equal(C.summarize(restored).total, 0);
  assert.ok(C.summarize(restored).issues.some(i => i.code === 'invalid-amount'));
});

function ledger(s, person, type, amount, payer = null, direction = type === 'payment' ? 'minus' : 'plus') {
  const e = C.ledgerEntry(s.members[person].id);
  Object.assign(e, { type, amount, direction, description: '測試用途', payerId: payer === null ? null : s.members[payer].id });
  s.ledger.push(e);
  return e;
}

test('其他用途分開填金額與正負，額外車資加收 4000 後應付 10280', () => {
  const s = fixture(2, 1);
  fill(s, 0, 'other', '6280', [0], 1);
  ledger(s, 0, 'adjustment', '4000');
  const r = C.summarize(s), p = r.people[0];
  assert.equal(p.total, 102800); assert.equal(p.pay, 102800);
  assert.equal(p.adjustment, 40000); assert.equal(p.paid, 0);
  assert.equal(r.adjusted, 40000);
  assert.equal(r.total, 62800); assert.equal(r.allocated, 62800);
});

test('個人加購只算本人，代墊 7000 自動扣一次：19250＋1500－7000＝13750', () => {
  const s = fixture(2, 1);
  fill(s, 0, 'lunch', '7000', [0], 0);
  fill(s, 0, 'dinner', '12250', [0], 1);
  ledger(s, 0, 'charge', '1500', 1);
  const r = C.summarize(s), p = r.people[0];
  assert.equal(p.personal, 15000); assert.equal(p.total, 207500);
  assert.equal(p.advanced, 70000); assert.equal(p.pay, 137500);
  assert.equal(r.advances.filter(a => a.personId === p.id).length, 1);
  assert.equal(r.total, 207500); assert.equal(r.allocated, r.total);
  assert.equal(r.issues.length, 0);
});

test('先收 10000，費用 6740 時應退 3260；實際退款後結清，支出不膨脹', () => {
  const s = fixture(2, 1);
  fill(s, 0, 'dinner', '6740', [0], 1);
  ledger(s, 0, 'payment', '10000');
  let r = C.summarize(s);
  assert.equal(r.people[0].receive, 32600); assert.equal(r.collected, 100000);
  ledger(s, 0, 'refund', '3260'); r = C.summarize(s);
  assert.equal(r.people[0].pay, 0); assert.equal(r.people[0].receive, 0);
  assert.equal(r.collected, 67400); assert.equal(r.total, 67400);
  assert.equal(r.allocated, 67400); assert.equal(r.issues.length, 0);
});

test('8356＋450＋700－代墊5250＝4256，保留精確金額而不自行取整', () => {
  const s = fixture(2, 1);
  fill(s, 0, 'dinner', '8356', [0], 1);
  fill(s, 0, 'other', '5250', [1], 0);
  ledger(s, 0, 'charge', '450', 1); ledger(s, 0, 'charge', '700', 1);
  const p = C.summarize(s).people[0];
  assert.equal(p.total, 95060); assert.equal(p.advanced, 52500); assert.equal(p.pay, 42560);
});

test('同人多筆加減獨立且按 0.1 元整數結算，減收不當作已繳款', () => {
  const s = fixture(2, 1);
  ledger(s, 0, 'adjustment', '100');
  ledger(s, 0, 'adjustment', '0.1', null, 'minus');
  ledger(s, 0, 'adjustment', '0.2', null, 'minus');
  ledger(s, 1, 'adjustment', '30', null, 'minus');
  const r = C.summarize(s);
  assert.equal(r.people[0].adjustment, 997); assert.equal(r.people[0].pay, 997);
  assert.equal(r.people[0].paid, 0);
  assert.equal(r.people[1].receive, 300); assert.equal(r.people[1].total, -300);
  assert.equal(r.adjusted, 697); assert.equal(r.total, 0); assert.equal(r.allocated, 0);
});

test('個人加購可由另一成員代墊，來源可核對，不重複計支出', () => {
  const s = fixture(3, 1);
  const e = ledger(s, 0, 'charge', '350', 2);
  const r = C.summarize(s);
  assert.equal(r.people[0].personal, 3500); assert.equal(r.people[0].pay, 3500);
  assert.equal(r.people[2].advanced, 3500); assert.equal(r.people[2].receive, 3500);
  assert.equal(r.advances[0].ledgerId, e.id); assert.equal(r.total, 3500);
  assert.equal(r.unassignedAdvance, 0);
});

test('個人紀錄遇空白姓名不消失、明確警告，補回姓名後金額恢復', () => {
  const s = fixture(2, 1);
  ledger(s, 0, 'charge', '350', 1);
  ledger(s, 0, 'payment', '100'); ledger(s, 0, 'adjustment', '10');
  s.members[0].name = ' ';
  let r = C.summarize(s);
  assert.equal(r.total, 3500); assert.equal(r.allocated, 0);
  assert.equal(r.collected, 0); assert.equal(r.adjusted, 0);
  assert.equal(r.issues.filter(i => i.code === 'missing-ledger-person').length, 3);
  const restored = C.decode(C.encode(s)); assert.equal(restored.ledger.length, 3);
  restored.members[0].name = '恢復姓名'; r = C.summarize(restored);
  assert.equal(r.people[0].total, 3600); assert.equal(r.people[0].pay, 2600);
  assert.equal(r.difference, 0);
});

test('個人紀錄非法金額不產生 NaN，零元空白不警告，缺加購代墊者提示', () => {
  for (const amount of ['-1', '1.23', 'NaN', 'Infinity']) {
    const s = fixture(); ledger(s, 0, 'adjustment', amount);
    const r = C.summarize(s);
    assert.equal(r.people[0].total, 0); assert.equal(r.issues[0].code, 'invalid-ledger-amount');
    assert.ok(Object.values(r.people[0]).filter(v => typeof v === 'number').every(Number.isSafeInteger));
  }
  const s = fixture(); ledger(s, 0, 'charge', ''); ledger(s, 0, 'payment', '0');
  assert.equal(C.summarize(s).issues.length, 0);
  ledger(s, 0, 'charge', '1');
  assert.equal(C.summarize(s).unassignedAdvance, 10);
  assert.ok(C.summarize(s).issues.some(i => i.code === 'missing-payer'));
});

test('改名保留個人加減、代墊和繳款，JSON 往返後全部一致', () => {
  const s = fixture(); ledger(s, 0, 'adjustment', '100', null, 'minus');
  ledger(s, 1, 'charge', '350', 0); ledger(s, 0, 'payment', '1000');
  const before = C.summarize(s); s.members[0].name = '新姓名';
  const restored = C.decode(C.encode(s)), after = C.summarize(restored);
  assert.equal(json(restored), json(s));
  assert.equal(after.people[0].advanced, before.people[0].advanced);
  assert.equal(after.people[0].receive, before.people[0].receive);
  assert.equal(json(after), json(C.summarize(s)));
});

test('舊版 version 1 備份自動升級，保留所有 ID 與原費用，不新增假紀錄', () => {
  const s = C.example(), original = C.summarize(s);
  const old = JSON.parse(C.encode(s)); old.version = 1; delete old.ledger;
  const restored = C.decode(JSON.stringify(old));
  assert.equal(restored.version, C.VERSION); assert.equal(restored.ledger.length, 0);
  assert.equal(json(restored.members), json(s.members)); assert.equal(json(restored.days), json(s.days));
  assert.equal(json(C.summarize(restored)), json(original));
});

test('個人紀錄未知 ID／類型／方向／重複 ID 皆拒絕，原備份不變', () => {
  const s = fixture(); ledger(s, 0, 'payment', '100');
  const backup = C.encode(s);
  for (const mutate of [
    s => { s.ledger[0].personId = 'unknown'; },
    s => { s.ledger[0].payerId = s.members[1].id; },
    s => { s.ledger[0].type = '__proto__'; },
    s => { s.ledger[0].direction = 'plus'; },
    s => { s.ledger[0].direction = 'wrong'; },
    s => { s.ledger[0].id = s.members[0].id; },
    s => { s.ledger[0].amount = 100; },
    s => { s.ledger = {}; },
    s => { delete s.ledger; }
  ]) {
    const bad = JSON.parse(backup); mutate(bad); assert.throws(() => C.validateState(bad));
    assert.equal(C.encode(s), backup);
  }
});

test('實際費用逐筆守恆，加減與收退款另列且資金核對恆等式成立', () => {
  const s = C.example();
  ledger(s, 0, 'charge', '350', 1); ledger(s, 1, 'payment', '1000');
  ledger(s, 1, 'refund', '100'); ledger(s, 2, 'adjustment', '6', null, 'minus');
  ledger(s, 3, 'adjustment', '4000');
  const r = C.summarize(s);
  assert.equal(r.total, r.allocated); assert.equal(r.difference, 0);
  assert.equal(r.people.reduce((n, p) => n + p.total - p.adjustment, 0), r.allocated);
  assert.equal(r.people.reduce((n, p) => n + p.advanced, 0), r.total);
  assert.equal(r.people.reduce((n, p) => n + p.pay - p.receive, 0), r.adjusted - r.collected);
  assert.ok(r.expenses.every(e => totalShares(e) === e.units));
});
