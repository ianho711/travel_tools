const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {webcrypto}=require('node:crypto');
const html=fs.readFileSync(require('node:path').join(__dirname,'..','index.html'),'utf8');
const script=id=>html.match(new RegExp('<script id="'+id+'">([\\s\\S]*?)</script>'))[1];
const context=vm.createContext({crypto:webcrypto});
const C=vm.runInContext(script('trip-core')+';TripCore',context);
const Storage=vm.runInContext(script('trip-storage')+';TripStorage',context);
const clone=x=>JSON.parse(JSON.stringify(x));
function fixture(){const s=C.createTrip(4,2);s.members.forEach((m,i)=>m.name='旅伴'+i);return s;}
function backend(initial){const data=new Map(initial===undefined?[]:[['book',initial]]);let writes=0,fail=false;return {getItem:key=>data.get(key)??null,setItem(key,value){if(fail)throw Object.assign(new Error('Quota exceeded'),{name:'QuotaExceededError'});writes++;data.set(key,value);},get writes(){return writes;},set fail(value){fail=value;}};}
function locks(){const tails=new Map();return {request(key,_opts,work){const result=(tails.get(key)||Promise.resolve()).then(work);tails.set(key,result.catch(()=>{}));return result;}};}
function session(b,l=locks()){return Storage.create({storage:()=>b,locks:l,key:'book',encode:C.encode,revision:()=>C.id('rev')});}

test('A已儲存後，B舊分頁拒絕覆蓋，保留本頁費用草稿與A的修訂',async()=>{
 const original=fixture(),b=backend(C.encode(original)),l=locks(),a=session(b,l),stale=session(b,l);
 const as=C.decode(a.read()),bs=C.decode(stale.read());as.days[0].expenses[0].amount='100';bs.days[0].expenses[0].amount='200';
 assert.equal((await a.save(as)).ok,true);const saved=b.getItem('book');
 assert.equal((await stale.save(bs)).reason,'conflict');assert.equal(b.getItem('book'),saved);assert.equal(bs.days[0].expenses[0].amount,'200');
 assert.equal(C.decode(saved).days[0].expenses[0].amount,'100');assert.ok(JSON.parse(saved)._storageRevision);
 assert.equal(C.decode(C.encode(bs)).days[0].expenses[0].amount,'200');
});
test('兩分頁同時儲存經共用鎖只有一頁成功；同一頁連續儲存依序完成',async()=>{
 const b=backend(C.encode(fixture())),l=locks(),a=session(b,l),c=session(b,l);const x=C.decode(a.read()),y=C.decode(c.read());
 x.days[0].expenses[0].amount='10';y.days[0].expenses[0].amount='20';const result=await Promise.all([a.save(x),c.save(y)]);
 assert.equal(result.filter(r=>r.ok).length,1);assert.equal(result.filter(r=>r.reason==='conflict').length,1);assert.equal(b.writes,1);
 x.days[0].expenses[0].amount='30';const first=a.save(x);x.days[0].expenses[0].amount='40';const second=a.save(x);
 assert.ok((await first).ok);assert.ok((await second).ok);assert.equal(C.decode(b.getItem('book')).days[0].expenses[0].amount,'40');
});
test('相同帳目也產生新修訂，偵測舊版程式寫入與清空；衝突不自動解除',async()=>{
 const raw=C.encode(fixture()),b=backend(raw),l=locks(),a=session(b,l),c=session(b,l);a.read();c.read();await a.save(C.decode(raw));
 assert.equal(c.changed(),true);assert.equal((await c.save(C.decode(raw))).reason,'conflict');
 const legacy=session(b,l);legacy.read();b.setItem('book',raw);assert.equal((await legacy.save(C.decode(raw))).reason,'conflict');
 const cleared=session(b,l);cleared.read();b.setItem('book',null);assert.equal(cleared.changed(),true);
 b.setItem('book',raw);assert.equal((await cleared.save(C.decode(raw))).reason,'conflict');
});
test('新分頁重新讀取可繼續編輯，儲存格式與舊JSON相容且匯出不夾修訂資訊',async()=>{
 const s=fixture(),b=backend(),a=session(b);assert.equal(a.read(),null);assert.ok((await a.save(s)).ok);
 const next=session(b),decoded=C.decode(next.read());assert.deepEqual(clone(decoded),clone(s));decoded.members[0].name='新姓名';assert.ok((await next.save(decoded)).ok);
 const backup=C.encode(C.decode(b.getItem('book')));assert.equal('_storageRevision' in JSON.parse(backup),false);assert.deepEqual(clone(C.decode(backup)),clone(decoded));
 for(const version of [1,2,3,4,5,6,7,8,9]){const old=clone(s);old.version=version;assert.equal(C.decode(JSON.stringify(old)).members[0].id,s.members[0].id);}
});
test('驗證失敗與空間不足各有獨立原因，均不更改已存帳本；空間恢復後可重試',async()=>{
 const s=fixture(),b=backend(C.encode(s)),a=session(b);a.read();const original=b.getItem('book');const bad=clone(s);bad.members[0].count=0;
 assert.equal((await a.save(bad)).reason,'validation');assert.equal(b.writes,0);b.fail=true;
 assert.equal((await a.save(s)).reason,'storage');assert.equal(b.getItem('book'),original);b.fail=false;assert.ok((await a.save(s)).ok);
});
test('不支援Web Locks時安全停止自動寫入，原資料及可匯出的草稿保留',async()=>{
 const s=fixture(),b=backend(C.encode(s));
 const unsupported=session(b,{});unsupported.read();s.members[0].name='尚未存入';assert.equal((await unsupported.save(s)).reason,'unsupported');
 assert.equal(b.writes,0);assert.equal(C.decode(C.encode(s)).members[0].name,'尚未存入');
});
function vehicleFixture(n,missing='both',isNew=false){
 const state=fixture(),vehicle={id:C.id('v'),name:'原車輛'};if(!isNew)state.vehicles.push(vehicle);
 for(let i=0;i<n;i++){const e=C.tripExpense(i%2?'fuel':'rental');e.name='費用'+i;state.carExpenses.push(e);}
 const costs={};for(const category of ['rental','fuel']){
   const existing=!isNew&&(missing==='none'||missing==='fuel'&&category==='rental')?state.carExpenses.find(e=>e.category===category):null;
   if(existing)existing.vehicleId=vehicle.id;
   costs[category]=existing?clone(existing):{...C.tripExpense(category),vehicleId:vehicle.id};
 }
 return {state,editor:{vehicle:{...vehicle,name:'修改草稿'},costs,isNew}};
}
for(const [n,missing,isNew,success,expected] of [[98,'both',true,true,100],[99,'both',true,false,99],[100,'both',true,false,100],[99,'fuel',false,true,100],[100,'fuel',false,false,100],[100,'none',false,true,100],[99,'both',false,false,99]]){
 test(`車資${n}筆／缺${missing}／${isNew?'新增':'編輯'}：${success?'合法完成':'拒絕超額並保留'}，不污染state或草稿`,()=>{
  const {state,editor}=vehicleFixture(n,missing,isNew),before=JSON.stringify(state),draft=JSON.stringify(editor);
  if(success){const next=C.applyVehicleDraft(state,editor);assert.equal(next.carExpenses.length,expected);assert.equal(next.vehicles[0].name,'修改草稿');assert.deepEqual(clone(C.decode(C.encode(next))),clone(next));}
  else assert.throws(()=>C.applyVehicleDraft(state,editor),/超過100筆上限/);
  assert.equal(JSON.stringify(state),before);assert.equal(JSON.stringify(editor),draft);
 });
}
test('saveVehicle實際程式在超額時不關閉編輯器、不commit、不改正式state，並顯示原因',()=>{
 const {state,editor}=vehicleFixture(100,'fuel'),elements={};const code=html.slice(html.indexOf('  function saveVehicle(){'),html.indexOf('  function openExpense('));
 const harness=vm.createContext({C,state,vehicleEditor:editor,readOnly:false,$:id=>elements[id]||(elements[id]={}),notify:text=>elements.flash=text,closeExpense:()=>{throw Error('Must not close draft');},commit:()=>{throw Error('Must not commit');}});
 vm.runInContext(code+';saveVehicle();',harness);assert.match(elements['vehicle-preview'].textContent,/需新增1筆.*超過100筆/);assert.equal(harness.state,state);assert.equal(harness.vehicleEditor,editor);
});
test('save實際程式分別顯示資料錯誤及儲存空間錯誤，衝突絕不顯示成功',async()=>{
 const start=html.indexOf('  function showStorageConflict()'),end=html.indexOf("  globalThis.addEventListener('storage'",start),code=html.slice(start,end);
 for(const reason of ['validation','storage','conflict','unsupported']){
  const elements={'expense-dialog':{open:false}},harness=vm.createContext({state:fixture(),active:'overview',readOnly:false,saveSequence:0,storageOk:true,storageSession:{save:async()=>({ok:false,reason,error:Error('格式測試')})},$:id=>elements[id]||(elements[id]={hidden:true}),notify:text=>elements.flash=text});
  vm.runInContext(code,harness);await vm.runInContext('save()',harness);
  assert.doesNotMatch(elements['save-status'].textContent,/已自動儲存/);
  if(reason==='validation'){assert.match(elements.flash,/驗證失敗/);assert.doesNotMatch(elements.flash,/空間不足/);}
  if(reason==='storage')assert.match(elements.flash,/儲存空間不足/);
  if(reason==='conflict'){assert.equal(elements['storage-conflict'].hidden,false);assert.match(elements.flash,/未覆蓋/);}
 }
});
test('優惠只抵減計費乘客人天，輪替司機本人及交通自理者不分配免計費份額',()=>{
 const s=fixture();s.members[1].count=4;s.members[2].count=2;s.members[3].count=7;s.members[3].excludeTransport=true;
 s.days.forEach((d,i)=>{d.useCar=true;d.drivers=[s.members[i].id];});const e=C.tripExpense('rental');Object.assign(e,{amount:'5250',discount:'400',payerId:s.members[0].id,rides:s.days.map(d=>({dayId:d.id,participantIds:s.members.map(m=>m.id),halfDayIds:[s.members[2].id]}))});s.carExpenses.push(e);
 const net=C.calculateTripExpense(s,e),gross=C.calculateTripExpense(s,{...e,discount:'0'}),sum=C.summarize(s);
 assert.deepEqual(clone(s.members.map(m=>net.shares[m.id])),[4850,33950,9700,0]);assert.deepEqual(clone(s.members.map(m=>gross.shares[m.id]-net.shares[m.id])),[400,2800,800,0]);
 assert.equal(net.days[0].shares[s.members[0].id],0);assert.equal(net.days[1].billableCounts[s.members[1].id],3);
 assert.equal(sum.cashExpense,52500);assert.equal(sum.income,4000);assert.equal(sum.total,48500);assert.equal(sum.allocated,48500);assert.equal(sum.people[0].advanced,52500);assert.equal(sum.difference,0);
});
