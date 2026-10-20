const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{webcrypto}=require('node:crypto');
const html=fs.readFileSync(require('node:path').join(__dirname,'..','index.html'),'utf8');
const core=html.match(/<script id="trip-core">([\s\S]*?)<\/script>/)[1];
const C=vm.runInNewContext(core+';TripCore',{crypto:webcrypto}),clone=x=>JSON.parse(JSON.stringify(x));
function fixture(n=4){const s=C.createTrip(n,1);s.members.forEach((m,i)=>m.name='家庭'+i);return s;}
function gift(s,amount='1300',source=0,targets=s.members.map((_,i)=>i).filter(i=>i!==source)){
 const e=C.sponsorship(s.members[source].id,targets.map(i=>s.members[i].id));e.amount=amount;s.sponsorships.push(e);return e;
}
function meal(s,amount='2600'){const e=s.days[0].expenses[0];Object.assign(e,{amount,payerId:s.members[0].id,participantIds:s.members.map(m=>m.id),splitMode:'groups'});return e;}
function sum(s){return C.summarize(s);}

test('1300元贊助13個其他家庭，每組100；家庭7人或2人都是1組，贊助者自己零抵減',()=>{
 const s=fixture(14);s.members[1].count=7;s.members[2].count=2;const e=gift(s),r=C.calculateSponsorship(s,e),summary=sum(s);
 assert.equal(r.groupCount,13);assert.equal(r.shares[s.members[0].id],0);assert.ok(s.members.slice(1).every(m=>r.shares[m.id]===1000));
 assert.equal(summary.people[0].sponsored,13000);assert.equal(summary.people[0].sponsorshipNet,13000);assert.equal(summary.people[0].paid,0);assert.equal(summary.people[0].advanced,0);
 assert.equal(summary.people.reduce((n,p)=>n+p.sponsorshipNet,0),0);assert.equal(summary.sponsorshipTotal,13000);
 const thirteen=fixture(13),source=gift(thirteen),t=C.calculateSponsorship(thirteen,source);assert.equal(t.groupCount,12);assert.deepEqual(clone(thirteen.members.slice(1).map(m=>t.shares[m.id])),[1084,1084,1084,1084,1083,1083,1083,1083,1083,1083,1083,1083]);
});
test('100元三組33.4／33.3／33.3按固定名單順序，勾選陣列順序不影響尾差',()=>{
 const s=fixture(),e=gift(s,'100',0,[3,1,2]),r=C.calculateSponsorship(s,e);assert.deepEqual(clone(s.members.map(m=>r.shares[m.id])),[0,334,333,333]);assert.equal(Object.values(r.shares).reduce((n,v)=>n+v,0),1000);
});
test('只讓勾選的家庭受惠，交通自理與當日司機也可受惠；不動原交通優惠及代墊',()=>{
 const s=fixture();s.members[1].count=4;s.members[2].excludeTransport=true;s.days[0].useCar=true;s.days[0].drivers=[s.members[1].id];
 const rent=C.tripExpense('rental');Object.assign(rent,{amount:'5250',discount:'400',payerId:s.members[1].id,rides:[{dayId:s.days[0].id,participantIds:s.members.map(m=>m.id),halfDayIds:[s.members[3].id]}]});s.carExpenses.push(rent);
 const before=sum(s);gift(s,'200',0,[1,2]);const after=sum(s);
 for(const key of ['total','cashExpense','income','allocated','difference'])assert.equal(after[key],before[key]);assert.equal(JSON.stringify(after.transport),JSON.stringify(before.transport));
 assert.equal(after.people[1].sponsorshipCredit,1000);assert.equal(after.people[2].sponsorshipCredit,1000);assert.equal(after.people[3].sponsorshipCredit,0);
 assert.equal(after.people[1].advanced,52500);assert.equal(after.cashExpense,52500);assert.equal(after.income,4000);assert.equal(after.total,48500);
});
test('承諾贊助只加應負擔，真正交款仍由已繳款抵扣；退款及個人加減不重複',()=>{
 const s=fixture(3);meal(s,'600');gift(s,'100');let r=sum(s);assert.equal(r.people[0].total,3000);assert.equal(r.people[1].total,1500);assert.equal(r.people[2].total,1500);assert.equal(r.people[0].paid,0);
 const entry=C.ledgerEntry(s.members[0].id);Object.assign(entry,{type:'payment',direction:'minus',amount:'100'});s.ledger.push(entry);r=sum(s);assert.equal(r.people[0].paid,1000);assert.equal(r.people[0].receive,4000);
 const refund=C.ledgerEntry(s.members[0].id);Object.assign(refund,{type:'refund',direction:'plus',amount:'20'});s.ledger.push(refund);r=sum(s);assert.equal(r.people[0].receive,3800);assert.equal(r.cashExpense,6000);assert.equal(r.allocated,6000);assert.equal(r.sponsorshipTotal,1000);
});
test('多筆贊助可互相受惠，各筆抵減守恆；總應負擔加總不增加支出',()=>{
 const s=fixture(4);meal(s,'1000');gift(s,'100',0,[1,2,3]);gift(s,'200',1,[0,2]);const r=sum(s);
 assert.equal(r.sponsorshipTotal,3000);assert.equal(r.people.reduce((n,p)=>n+p.total,0),10000);assert.equal(r.people.reduce((n,p)=>n+p.sponsorshipNet,0),0);
 for(const result of r.sponsorshipResults)assert.equal(Object.values(result.shares).reduce((n,v)=>n+v,0),result.allocated);
});
test('空白姓名不受惠，改名和家庭人數不改ID；原贊助紀錄與參與保留',()=>{
 const s=fixture(),e=gift(s,'100',0,[1,2]);s.members[1].name='';let r=C.calculateSponsorship(s,e);assert.equal(r.groupCount,1);assert.equal(r.shares[s.members[2].id],1000);assert.equal(e.beneficiaryIds.length,2);
 s.members[1].name='新姓名';s.members[1].count=7;r=C.calculateSponsorship(s,e);assert.equal(r.groupCount,2);assert.equal(r.shares[s.members[1].id],500);
 s.members[0].name='';r=C.calculateSponsorship(s,e);assert.equal(r.blocked,true);assert.equal(r.allocated,0);assert.equal(sum(s).people.reduce((n,p)=>n+p.sponsorshipNet,0),0);
});
test('空白及零元安全，正金額無受惠者／未指定贊助者保留待處理，不部分扣帳',()=>{
 const s=fixture(),e=gift(s,'',0,[]);for(const value of ['','0']){e.amount=value;const r=C.calculateSponsorship(s,e);assert.equal(r.blocked,false);assert.equal(r.allocated,0);}
 e.amount='100';let r=sum(s);assert.ok(r.issues.some(i=>i.code==='no-beneficiaries'));assert.equal(r.sponsorshipTotal,0);assert.equal(r.people[0].sponsored,0);
 e.sponsorId=null;e.beneficiaryIds=[s.members[2].id];r=sum(s);assert.ok(r.issues.some(i=>i.code==='missing-sponsor'));assert.equal(r.sponsorshipTotal,0);assert.equal(e.amount,'100');
 for(const value of ['-1','1.23','NaN','Infinity','100000000.1']){e.amount=value;const result=C.calculateSponsorship(s,e);assert.equal(result.blocked,true);assert.equal(result.allocated,0);assert.ok(Object.values(result.shares).every(Number.isFinite));}
});
test('抵減超過原負擔時依既有規則應收回，不靜默截斷贊助金',()=>{
 const s=fixture(2);meal(s,'100');gift(s,'200');const r=sum(s);assert.equal(r.people[1].total,-1500);assert.equal(r.people[1].receive,1500);assert.equal(r.people[0].total,2500);assert.equal(r.people[0].pay,1500);assert.equal(r.people.reduce((n,p)=>n+p.total,0),1000);
});
test('JSON往返保留多筆贊助與總覽；v9及更舊備份無贊助時財務不變',()=>{
 const s=fixture();meal(s);gift(s);const restored=C.decode(C.encode(s));assert.deepEqual(clone(restored),clone(s));assert.deepEqual(clone(sum(restored)),clone(sum(s)));assert.equal(C.VERSION,10);
 for(const version of [1,2,3,4,5,6,7,8,9]){const raw=clone(s);raw.version=version;delete raw.sponsorships;const result=C.validateState(raw);assert.deepEqual(clone(result.sponsorships),[]);assert.equal(sum(result).sponsorshipTotal,0);assert.equal(sum(result).cashExpense,26000);}
});
test('非法JSON贊助、未知ID、自身受惠、重複ID／家庭與超限不覆蓋原資料',()=>{
 const s=fixture();gift(s);const backup=C.encode(s);const changes=[x=>x.sponsorships=null,x=>x.sponsorships[0].sponsorId='unknown',x=>x.sponsorships[0].beneficiaryIds.push(x.members[0].id),x=>x.sponsorships[0].beneficiaryIds.push(x.members[1].id),x=>x.sponsorships[0].beneficiaryIds=['unknown'],x=>x.sponsorships[0].amount=100,x=>x.sponsorships[0].id=x.members[0].id,x=>x.sponsorships=Array.from({length:101},()=>x.sponsorships[0])];
 for(const change of changes){const bad=JSON.parse(backup);change(bad);assert.throws(()=>C.validateState(bad));assert.equal(C.encode(s),backup);}
});
test('13組與200組、0.1元及最大金額，各筆和整體贊助均以安全整數守恆',()=>{
 for(const count of [14,200])for(const amount of ['0.1','100','1300','100000000']){const s=fixture(count),e=gift(s,amount),r=C.calculateSponsorship(s,e),summary=sum(s);assert.equal(Object.values(r.shares).reduce((n,v)=>n+v,0),C.parseAmount(amount).units);assert.ok(Object.values(r.shares).every(Number.isSafeInteger));assert.equal(summary.people.reduce((n,p)=>n+p.sponsorshipNet,0),0);}
});
// Execute the shipped modal functions with inert elements; no browser or UI actions.
function editorHarness(s,readonly=false){
 const elements=new Map(),node=()=>({innerHTML:'',textContent:'',hidden:false,setAttribute(){},querySelectorAll(){return [];},showModal(){},focus(){}});
 const $=id=>{if(!elements.has(id))elements.set(id,node());return elements.get(id);};let commits=0,closed=0;
 const code=html.slice(html.indexOf('  function openSponsorship('),html.indexOf('  function renderPersonal()'));
 const context=vm.createContext({state:s,readOnly:readonly,C,$,esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),money:n=>(n/10).toFixed(1),payerOptions:e=>`<option value="${e.payerId}"></option>`,toggleIn:(list,id,on)=>on?[...new Set([...list,id])]:list.filter(x=>x!==id),commit(){commits++;},closeExpense(){closed++;},expenseEditor:null,vehicleEditor:null,sponsorshipEditor:null});
 vm.runInContext(code,context);return {context,elements,run:code=>vm.runInContext(code,context),commits:()=>commits,closed:()=>closed};
}
test('真實表單預設勾選其他家庭、自己禁選；切換贊助者移除本人且保留其他選擇',()=>{
 const s=fixture(4),h=editorHarness(s);h.run('openSponsorship()');assert.deepEqual(clone(h.context.sponsorshipEditor.draft.beneficiaryIds),clone(s.members.slice(1).map(m=>m.id)));
 const markup=h.elements.get('sponsorship-beneficiaries').innerHTML;assert.equal((markup.match(/type="checkbox"/g)||[]).length,4);assert.match(markup,new RegExp('data-person="'+s.members[0].id+'"[^>]*disabled'));
 h.context.next=s.members[1].id;h.run("handleSponsorshipDraft({dataset:{field:'sponsorship-sponsor'},value:next})");assert.ok(!h.context.sponsorshipEditor.draft.beneficiaryIds.includes(s.members[1].id));assert.ok(h.context.sponsorshipEditor.draft.beneficiaryIds.includes(s.members[2].id));assert.ok(h.context.sponsorshipEditor.draft.beneficiaryIds.includes(s.members[0].id));assert.equal(s.sponsorships.length,0);
});
test('真實贊助儲存套用草稿；非法超限保留草稿，唯讀函式不可修改或儲存',()=>{
 const s=fixture(),h=editorHarness(s);h.run('openSponsorship()');h.run("sponsorshipEditor.draft.amount='100';saveSponsorship()");assert.equal(h.context.state.sponsorships[0].amount,'100');assert.equal(h.commits(),1);assert.equal(h.closed(),1);
 const ro=editorHarness(h.context.state,true);ro.context.sponsorshipEditor={draft:clone(h.context.state.sponsorships[0]),isNew:false};ro.run("handleSponsorshipDraft({dataset:{field:'sponsorship-amount'},value:'900'});saveSponsorship()");assert.equal(ro.context.state.sponsorships[0].amount,'100');assert.equal(ro.commits(),0);
 const full=fixture();for(let i=0;i<100;i++)gift(full,'1');const f=editorHarness(full);f.context.sponsorshipEditor={draft:C.sponsorship(full.members[0].id,[full.members[1].id]),isNew:true};f.run('saveSponsorship()');assert.equal(full.sponsorships.length,100);assert.equal(f.commits(),0);assert.equal(f.closed(),0);assert.match(f.elements.get('sponsorship-issues').textContent,/最多100筆/);
});
test('刪除成員需確認，贊助者移除後保留待處理金額，受惠者移除後參照仍可匯出',()=>{
 const s=fixture();gift(s,'100');const a=html.indexOf("    if(action==='delete-member')"),b=html.indexOf("    if(action==='add-day')",a),code='function remove(){'+html.slice(a,b)+'}';
 const ctx=vm.createContext({state:s,C,action:'delete-member',button:{dataset:{person:s.members[1].id}},memberName:m=>m.name,confirm:()=>false,commit(){C.encode(s);}});vm.runInContext(code+';remove()',ctx);assert.equal(s.members.length,4);
 ctx.confirm=()=>true;vm.runInContext('remove()',ctx);assert.equal(s.members.length,3);assert.equal(s.sponsorships[0].beneficiaryIds.length,2);assert.equal(C.calculateSponsorship(s,s.sponsorships[0]).groupCount,2);
 ctx.button.dataset.person=s.members[0].id;vm.runInContext('remove()',ctx);assert.equal(s.sponsorships[0].sponsorId,null);assert.equal(s.sponsorships[0].amount,'100');assert.equal(sum(s).sponsorshipTotal,0);assert.ok(sum(s).issues.some(i=>i.code==='missing-sponsor'));assert.equal(C.decode(C.encode(s)).sponsorships[0].amount,'100');
});
test('列表直接提供每筆贊助的編輯及刪除入口，唯讀列表只可查看',()=>{
 const s=fixture();const e=gift(s);const code=html.slice(html.indexOf('  function renderSponsorships('),html.indexOf('  function openSponsorship('));
 const context=vm.createContext({state:s,C,readOnly:false,esc:String,money:n=>(n/10).toFixed(1)});vm.runInContext(code,context);
 const editable=vm.runInContext('renderSponsorships(C.summarize(state))',context);
 assert.match(editable,/<th>操作<\/th>/);assert.match(editable,new RegExp('data-action="delete-sponsorship" data-sponsorship="'+e.id+'"'));
 context.readOnly=true;const readonly=vm.runInContext('renderSponsorships(C.summarize(state))',context);assert.doesNotMatch(readonly,/data-action="delete-sponsorship"/);assert.match(readonly,/>查看<\/button>/);
});
test('贊助刪除取消保留原帳與草稿；確認刪除恢復分攤，已繳款退款及其他贊助不變',()=>{
 const s=fixture();meal(s,'1000');const first=gift(s,'100'),other=gift(s,'200',1,[0,2]);
 for(const [type,amount,direction] of [['payment','500','minus'],['refund','20','plus']]){const e=C.ledgerEntry(s.members[0].id);Object.assign(e,{type,amount,direction});s.ledger.push(e);}
 const h=editorHarness(s);h.context.sponsorshipEditor={draft:clone(first),isNew:false};h.context.entryId=first.id;h.context.confirm=()=>false;
 const original=C.encode(s),draft=JSON.stringify(h.context.sponsorshipEditor);assert.equal(h.run('deleteSponsorship(entryId)'),false);assert.equal(C.encode(h.context.state),original);assert.equal(JSON.stringify(h.context.sponsorshipEditor),draft);assert.equal(h.commits(),0);assert.equal(h.closed(),0);
 const expected=clone(s);expected.sponsorships=expected.sponsorships.filter(e=>e.id!==first.id);h.context.confirm=()=>true;assert.equal(h.run('deleteSponsorship(entryId)'),true);
 assert.deepEqual(clone(C.summarize(h.context.state)),clone(C.summarize(expected)));assert.equal(h.context.state.sponsorships[0].id,other.id);assert.equal(h.commits(),1);assert.equal(h.closed(),1);assert.equal(h.context.state.ledger[0].amount,'500');assert.equal(h.context.state.ledger[1].amount,'20');
 assert.deepEqual(clone(C.decode(C.encode(h.context.state))),clone(expected));
});
test('從列表刪除不依賴編輯視窗，不關閉其他草稿；唯讀、重複及未知ID不可刪除',()=>{
 const s=fixture();const target=gift(s),other=gift(s,'50',1,[0]);const h=editorHarness(s);let prompts=0;h.context.confirm=()=>{prompts++;return true;};h.context.entryId=target.id;
 h.context.sponsorshipEditor={draft:clone(other),isNew:false};assert.equal(h.run('deleteSponsorship(entryId)'),true);assert.equal(h.closed(),0);assert.equal(h.context.sponsorshipEditor.draft.id,other.id);
 assert.equal(h.run('deleteSponsorship(entryId)'),false);assert.equal(h.run("deleteSponsorship('unknown')"),false);assert.equal(prompts,1);
 const ro=editorHarness(s,true);ro.context.confirm=()=>{throw Error('Readonly must not prompt');};ro.context.entryId=other.id;assert.equal(ro.run('deleteSponsorship(entryId)'),false);assert.equal(ro.commits(),0);
});
