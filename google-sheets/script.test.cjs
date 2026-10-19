// These tests exercise Apps Script data handling with an in-memory Sheets adapter.
// Google authorization, service calls and the actual checkbox UI still need a Google account.
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'Code.gs'),'utf8');
const simple=value=>JSON.parse(JSON.stringify(value));
function column(letters){return [...letters].reduce((n,c)=>n*26+c.charCodeAt(0)-64,0);}
class Range {
  constructor(sheet,r,c,nr=1,nc=1){Object.assign(this,{sheet,r,c,nr,nc});}
  getValue(){return this.getValues()[0][0];}
  getValues(){return Array.from({length:this.nr},(_,i)=>Array.from({length:this.nc},(_,j)=>this.sheet.values.get(`${this.r+i},${this.c+j}`)??''));}
  setValue(v){return this.setValues([[v]]);}
  setValues(rows){rows.forEach((row,i)=>row.forEach((v,j)=>this.sheet.values.set(`${this.r+i},${this.c+j}`,v)));return this;}
  clearContent(){return this.setValues(Array.from({length:this.nr},()=>Array(this.nc).fill('')));}
  setDataValidation(rule){assert.equal(rule.checkbox,true);this.sheet.checkboxCalls+=this.nr*this.nc;return this;}
  getColumn(){return this.c;}
  getSheet(){return this.sheet;}
  getA1Notation(){return this.r===3&&this.c===3&&this.nr===1&&this.nc===1?'C3':'other';}
}
let nextSheetId=1;
class Sheet {
  constructor(def){this.name=def.name;this.sheetId=nextSheetId++;this.values=new Map();this.checkboxCalls=0;def.cells.filter(c=>c.kind!=='formula').forEach(c=>this.values.set(`${c.r},${c.c}`,c.v));}
  getName(){return this.name;}
  getSheetId(){return this.sheetId;}
  getRange(r,c,nr,nc){if(typeof r==='string'){const m=r.match(/^([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$/);return new Range(this,Number(m[2]),column(m[1]),m[4]?Number(m[4])-Number(m[2])+1:1,m[3]?column(m[3])-column(m[1])+1:1);}return new Range(this,r,c,nr,nc);}
  getLastRow(){return Math.max(0,...[...this.values].filter(([,v])=>v!=='').map(([k])=>Number(k.split(',')[0])));}
  getMaxRows(){return 1000;}
}
function harness(){
  let book,alerts=[];
  const ui={ButtonSet:{YES_NO:'yes-no'},Button:{YES:'yes',NO:'no'},alert:(_title,message)=>{alerts.push(message);return 'no';},createMenu:()=>({addItem(){return this;},addSeparator(){return this;},addToUi(){return this;}})};
  const SpreadsheetApp={getActive:()=>book,getUi:()=>ui,newDataValidation:()=>({checkbox:false,requireCheckbox(){this.checkbox=true;return this;},setAllowInvalid(){return this;},build(){return this;}})};
  const api=vm.runInNewContext(source+'\n({T:TravelSheets,read:readTravelModel_,setup:setupTravelSheet,select:selectTravelExpense,unselect:unselectTravelExpense,onEdit,backup:writeTravelBackup_});',{SpreadsheetApp});
  function load(model){
    const spec=api.T.build(model),sheets=spec.sheets.map(d=>new Sheet(d));
    book={getSheets:()=>sheets,getSheetByName:name=>sheets.find(s=>s.name===name),getSheetById:id=>sheets.find(s=>s.sheetId===id),active:sheets[2],getActiveSheet(){return this.active;},getActiveRange(){return this.active.getRange('D12');},toast(){}};
    return book;
  }
  return {api,load,alerts,get book(){return book;}};
}
function fixture(h){const m=h.api.T.create(3,2);m.members[0].name='A';m.members[1].name=' ';m.members[2].name='C';m.days[0].useCar=true;m.days[0].drivers=[m.members[0].id];Object.assign(m.days[0].expenses[0],{amount:'90',payerId:m.members[0].id,participantIds:m.members.map(p=>p.id)});Object.assign(m.days[0].expenses[3],{amount:'100',payerId:m.members[0].id,participantIds:m.members.map(p=>p.id)});return m;}

test('Google 範本預設 30 人／10 天，2410 個真實核取方塊驗證位置',()=>{
  const h=harness(),spec=h.api.T.build(h.api.T.create());let count=0;
  for(const sheet of spec.sheets)for(const a1 of sheet.checkboxes){const range=new Sheet({name:'',cells:[]}).getRange(a1);count+=range.nr*range.nc;}
  assert.equal(count,2410);assert.equal(spec.model.members[0].id,'P001');assert.equal(spec.model.members[29].id,'P030');
});
test('啟用核取方塊只改 validation，不覆寫任何金額、司機、代墊或參與資料；可重複執行',()=>{
  const h=harness(),model=fixture(h),book=h.load(model),before=simple(h.api.read(book));
  h.api.setup();assert.deepEqual(simple(h.api.read(book)),before);
  assert.equal(book.getSheets().reduce((sum,s)=>sum+s.checkboxCalls,0),50);
  h.api.setup();assert.deepEqual(simple(h.api.read(book)),before);
  assert.equal(book.getSheets().reduce((sum,s)=>sum+s.checkboxCalls,0),100);
});
test('改姓名後讀回固定 ID、司機、代墊及參與；日期分頁改名後仍以 sheet ID 找回',()=>{
  const h=harness(),model=fixture(h),book=h.load(model);h.api.setup();
  book.getSheetByName('成員名單').getRange('B5').setValue('更名');
  book.getSheetByName('第 1 天').name='自訂分頁';
  const read=h.api.read(book);
  assert.equal(read.members[0].name,'更名');assert.equal(read.members[0].id,model.members[0].id);
  assert.deepEqual(simple(read.days),simple(model.days));
});
test('選單全選／取消全選只改具名成員，空白姓名原本的勾選保留',()=>{
  const h=harness(),model=fixture(h),book=h.load(model),sheet=book.getSheetByName('第 1 天');
  h.api.unselect();assert.deepEqual(sheet.getRange('D12:D14').getValues(),[[false],[true],[false]]);
  h.api.select();assert.deepEqual(sheet.getRange('D12:D14').getValues(),[[true],[true],[true]]);
});
test('取消已有車資的用車會確認，取消操作後恢復用車；使用事件日期而非目前分頁',()=>{
  const h=harness(),model=fixture(h),book=h.load(model),sheet=book.getSheetByName('第 1 天');
  book.active=book.getSheetByName('第 2 天');sheet.getRange('C3').setValue(false);
  h.api.onEdit({source:book,range:sheet.getRange('C3'),value:'FALSE'});
  assert.equal(sheet.getRange('C3').getValue(),true);assert.equal(sheet.getRange('J6').getValue(),100);
  assert.equal(h.alerts.length,1);assert.match(h.alerts[0],/保留金額/);
});
test('在有其他資料的試算表初始化會停止，保留原內容',()=>{
  const h=harness(),book=h.load(fixture(h));book.getSheetByName('_旅伴設定').name='其他資料';
  const before=book.getSheets().map(s=>[s.name,[...s.values]]);
  assert.throws(()=>h.api.setup(),/空白試算表/);
  assert.deepEqual(book.getSheets().map(s=>[s.name,[...s.values]]),before);
});
test('匯入檢查未知參照、重複 ID、錯誤型別與試算表上限，不更動原資料',()=>{
  const h=harness(),model=fixture(h),original=JSON.stringify(model);
  for(const mutate of [m=>m.members[1].id=m.members[0].id,m=>m.days[0].drivers=['unknown'],m=>m.days[0].expenses[0].payerId='unknown',m=>m.days[0].expenses[0].category='bad',m=>m.days[0].expenses[0].amount=10,m=>m.days[0].useCar='true']){
    const bad=JSON.parse(original);mutate(bad);assert.throws(()=>h.api.T.validate(bad));assert.equal(JSON.stringify(model),original);
  }
  assert.throws(()=>h.api.T.build(h.api.T.create(101,1)),/100/);
  assert.throws(()=>h.api.T.build(h.api.T.create(3,32)),/31/);
});
test('備份分頁名稱若被其他資料使用，不會覆寫原內容',()=>{
  const h=harness(),model=fixture(h),book=h.load(model);
  const unrelated=new Sheet({name:'_上次資料備份',cells:[{r:1,c:1,v:'私人備註'}]});book.getSheets().push(unrelated);
  assert.throws(()=>h.api.backup(book,model),/未覆寫/);
  assert.equal(unrelated.getRange('A1').getValue(),'私人備註');
});
