/**
 * 旅伴帳本 Google 試算表版。
 * @OnlyCurrentDoc
 * 貼到指定試算表的「擴充功能 → Apps Script」，執行 setupTravelSheet。
 * 不使用 Drive API、不讀寫其他檔案、不發布網站。
 */
const TravelSheets = (() => {
  const LIMITS = {members:100, days:31, expenses:30};
  const CATEGORIES = {breakfast:'早餐',lunch:'午餐',dinner:'晚餐',fuel:'油錢',rental:'租車費',parking:'停車費',other:'其他'};
  const META = '_旅伴設定', CALC = '_計算';
  const id = prefix => prefix+'_'+Date.now().toString(36)+Math.random().toString(36).slice(2,11);
  const col = n => {let s='';for(;n;n=Math.floor((n-1)/26))s=String.fromCharCode(65+(n-1)%26)+s;return s;};
  const q = name => "'"+name.replace(/'/g,"''")+"'!";
  function expense(category='other') {return {id:id('e'),name:CATEGORIES[category],category,amount:'',payerId:null,participantIds:[]};}
  function day(n) {return {id:id('d'),name:'第 '+n+' 天',useCar:false,drivers:[],expenses:Object.keys(CATEGORIES).map(expense)};}
  function create(people=30,days=10) {return {version:1,members:Array.from({length:people},(_,i)=>({id:'P'+String(i+1).padStart(3,'0'),name:''})),days:Array.from({length:days},(_,i)=>day(i+1))};}
  function validate(input) {
    const fail=m=>{throw Error(m);}, seen=new Set();
    const str=(x,n,label)=>{if(typeof x!=='string'||x.length>n)fail(label+'格式錯誤');return x;};
    const rid=x=>{if(typeof x!=='string'||!/^[A-Za-z0-9_-]{1,80}$/.test(x)||seen.has(x))fail('ID 錯誤或重複');seen.add(x);return x;};
    if(!input||input.version!==1||!Array.isArray(input.members)||input.members.length<1||input.members.length>LIMITS.members)fail('須為版本 1、1–100 個名額');
    const members=input.members.map(m=>({id:rid(m.id),name:str(m.name,80,'姓名')})), ids=new Set(members.map(m=>m.id));
    const refs=a=>{if(!Array.isArray(a)||a.length>members.length||new Set(a).size!==a.length||a.some(x=>!ids.has(x)))fail('成員參照錯誤');return [...a];};
    if(!Array.isArray(input.days)||input.days.length<1||input.days.length>LIMITS.days)fail('天數須為 1–31 天');
    const days=input.days.map(d=>{
      if(typeof d.useCar!=='boolean'||!Array.isArray(d.expenses)||d.expenses.length>LIMITS.expenses)fail('日期設定錯誤，每天至多 30 筆費用');
      return {id:rid(d.id),name:str(d.name,80,'日期名稱'),useCar:d.useCar,drivers:refs(d.drivers),expenses:d.expenses.map(e=>{
        if(!Object.prototype.hasOwnProperty.call(CATEGORIES,e.category)||(e.payerId!==null&&!ids.has(e.payerId)))fail('類別或代墊者錯誤');
        return {id:rid(e.id),name:str(e.name,80,'費用名稱'),category:e.category,amount:str(e.amount,32,'金額'),payerId:e.payerId,participantIds:refs(e.participantIds)};
      })};
    });
    return {version:1,members,days};
  }
  function build(input) {
    const model=validate(input), n=model.members.length, first=12,last=first+n-1, hs=last+5, shareStart=hs+6;
    const sheets=[];
    function sheet(name,hidden=false) {
      const s={name,hidden,cells:[],merges:[],checkboxes:[],validations:[],editable:[],widths:{},freezeRows:0,freezeColumns:0,hiddenRows:[],hiddenColumns:[]};
      s.put=(r,c,v,kind='value')=>{s.cells.push({r,c,v,kind});};
      s.formula=(r,c,v)=>s.put(r,c,'='+v,'formula');
      s.text=(r,c,v)=>s.put(r,c,v,'text');
      sheets.push(s);return s;
    }
    const overview=sheet('整趟總覽'), roster=sheet('成員名單'), daily=[];
    roster.text(1,2,'統一成員名單');roster.text(2,2,'只修改姓名；固定 ID 請勿修改。增減成員請用「旅伴帳本」選單。');
    ['固定 ID','姓名','使用說明'].forEach((v,i)=>roster.text(4,i+1,v));
    model.members.forEach((m,i)=>{roster.text(5+i,1,m.id);roster.text(5+i,2,m.name);roster.text(5+i,3,'空白姓名不分攤');});
    roster.editable.push(`B5:B${n+4}`);roster.widths={A:150,B:180,C:440};roster.freezeRows=4;
    const rosterIds=`'成員名單'!$A$5:$A$${n+4}`, rosterNames=`'成員名單'!$B$5:$B$${n+4}`;
    const rosterName=pid=>`IFERROR(INDEX(${rosterNames},MATCH(${pid},${rosterIds},0))&"","")`;
    const dayUnitRefs=[],dayAllocatedRefs=[],statusRefs=[],detail=[];
    model.days.forEach((d,di)=>{
      const s=sheet('第 '+(di+1)+' 天'), sq=q(s.name);daily.push(s);
      s.text(1,1,d.id);s.text(2,2,d.name);s.text(3,2,'當日用車');s.put(3,3,d.useCar);s.checkboxes.push('C3');
      s.text(2,4,'全選／取消全選：選取費用欄後使用「旅伴帳本」選單。');
      s.text(3,4,'司機只免當日油錢與租車費；餐費、停車費與其他正常分攤。');
      ['名稱','類別','總金額（元）','代墊者 ID','分攤人數','每人金額範圍','待處理提醒'].forEach((v,i)=>s.text(i+4,2,v));
      s.text(11,1,'固定 ID');s.text(11,2,'姓名');s.text(11,3,'當日司機');
      s.widths={A:280,B:160,C:92};s.freezeRows=11;s.freezeColumns=3;s.hiddenColumns=['A'];s.hiddenRows=[[1,1],[hs,shareStart+n-1]];
      s.editable.push('B2','C3',`C${first}:C${last}`);s.checkboxes.push(`C${first}:C${last}`);
      s.formula(hs+3,3,`SUMPRODUCT(--(LEN(TRIM($B$${first}:$B$${last}))>0),--($C$${first}:$C$${last}=TRUE))`);
      model.members.forEach((m,i)=>{
        const r=first+i;s.text(r,1,m.id);s.formula(r,2,rosterName('$A'+r));s.put(r,3,d.drivers.includes(m.id));
        s.text(shareStart+i,1,m.id);
      });
      d.expenses.forEach((e,ei)=>{
        const ci=4+2*ei, c=col(ci), next=col(ci+1), amount=`${c}$6`, valid=`${c}$${hs+1}`, units=`${c}$${hs}`, count=`${c}$${hs+2}`, car=`${c}$${hs+4}`, drivers=`$C$${hs+3}`;
        const named=`--(LEN(TRIM($B$${first}:$B$${last}))>0)`, checked=`--(${c}$${first}:${c}$${last}=TRUE)`;
        s.text(1,ci,e.id);s.text(4,ci,e.name);s.text(5,ci,CATEGORIES[e.category]);s.put(6,ci,e.amount===''?'':(/^(?:\d+(?:\.\d?)?|\.\d)$/.test(e.amount.trim())?Number(e.amount):e.amount),typeof e.amount==='string'&&!/^(?:\d+(?:\.\d?)?|\.\d)$/.test(e.amount.trim())?'text':'value');
        s.text(6,ci+1,'元');s.text(7,ci,e.payerId||'');s.formula(7,ci+1,rosterName(c+'7'));
        s.formula(hs+1,ci,`IF(LEN(TRIM(${amount}&""))=0,TRUE,IFERROR(AND(ISNUMBER(${amount}),${amount}>=0,${amount}<=100000000,ROUND(${amount},1)=${amount}),FALSE))`);
        s.formula(hs,ci,`IFERROR(IF(${valid},ROUND(${amount}*10,0),0),0)`);
        s.formula(hs+4,ci,`OR(${c}$5="油錢",${c}$5="租車費")`);
        s.formula(hs+2,ci,`IF(${car},IF(AND($C$3=TRUE,${drivers}>0),SUMPRODUCT(${named},${checked},--($C$${first}:$C$${last}<>TRUE)),0),SUMPRODUCT(${named},${checked}))`);
        s.formula(8,ci,`IF(${valid},${count},0)`);s.text(8,ci+1,'人');
        s.formula(9,ci,`IF(OR(NOT(${valid}),${count}=0),"暫不分攤",TEXT(QUOTIENT(${units},MAX(1,${count}))/10,"0.0")&IF(MOD(${units},MAX(1,${count}))>0,"–"&TEXT((QUOTIENT(${units},MAX(1,${count}))+1)/10,"0.0"),"")&" 元／人")`);
        const noCar=`AND(${units}>0,${car},$C$3<>TRUE)`,missingDriver=`AND(${units}>0,${car},$C$3=TRUE,${drivers}=0)`;
        s.formula(10,ci,`IF(NOT(${valid}),"金額須為非負數且最多一位小數。 ","")&IF(${noCar},"未用車但有車資：保留金額，暫不分攤。 ","")&IF(${missingDriver},"請設定具名司機；車資暫不分攤。 ","")&IF(AND(${units}>0,${count}=0,NOT(${noCar}),NOT(${missingDriver})),"沒有可分攤者。 ","")&IF(AND(${units}>0,LEN(TRIM(${next}7))=0),"請指定具名代墊者。","")`);
        s.text(11,ci,'是否參與');s.text(11,ci+1,'分攤（元）');s.widths[c]=148;s.widths[next]=148;
        s.checkboxes.push(`${c}${first}:${c}${last}`);s.editable.push(`${c}4:${next}4`,`${c}5:${next}5`,`${c}6`,`${c}7`,`${c}${first}:${c}${last}`);
        s.validations.push({range:`${c}5:${next}5`,type:'list',values:Object.values(CATEGORIES)});
        s.validations.push({range:`${c}7`,type:'range',source:rosterIds});
        s.validations.push({range:`${c}6`,type:'amount',formula:`OR(LEN(TRIM(${c}6&""))=0,IFERROR(AND(ISNUMBER(${c}6),${c}6>=0,${c}6<=100000000,ROUND(${c}6,1)=${c}6),FALSE))`});
        [4,5,9,10].forEach(r=>s.merges.push(`${c}${r}:${next}${r}`));
        model.members.forEach((m,i)=>{
          const r=first+i,hr=shareStart+i;
          const prefix=`--(LEN(TRIM($B$${first}:$B${r}))>0),--(${c}$${first}:${c}${r}=TRUE)`;
          const rank=`IF(${car},SUMPRODUCT(${prefix},--($C$${first}:$C${r}<>TRUE)),SUMPRODUCT(${prefix}))`;
          s.put(r,ci,e.participantIds.includes(m.id));
          s.formula(hr,ci,`IF(AND(${valid},${count}>0,LEN(TRIM($B${r}))>0,${c}${r}=TRUE,IF(${car},$C${r}<>TRUE,TRUE)),QUOTIENT(${units},MAX(1,${count}))+IF(${rank}<=MOD(${units},MAX(1,${count})),1,0),0)`);
          s.formula(r,ci+1,`${c}${hr}/10`);
        });
        s.formula(hs+5,ci,`SUM(${c}${shareStart}:${c}${shareStart+n-1})`);
        s.formula(last+1,ci+1,`${c}${hs+5}/10`);
        dayUnitRefs.push(`${sq}${c}${hs}`);dayAllocatedRefs.push(`${sq}${c}${hs+5}`);statusRefs.push(`${sq}${c}10`);
        detail.push({day:`${sq}$B$2`,name:`${sq}${c}4`,amount:`${sq}${c}${hs}`,status:`${sq}${c}10`});
      });
      s.text(last+1,2,'已分攤合計（元）');
    });
    const help=sheet('使用說明');help.widths={A:30,B:960};
    const instructions=[
      '旅伴帳本｜Google 試算表版',
      '在指定 Google Drive 資料夾建立／匯入此試算表；你保留編輯權限，旅伴設為檢視者。',
      '原生核取方塊：擴充功能 → Apps Script，貼入 Code.gs，執行 setupTravelSheet，授權目前試算表。',
      '1. 在「成員名單」修改姓名；固定 ID 不要改動。空白或空格姓名不參與分攤。',
      '2. 每日填費用名稱、類別、金額、代墊者 ID。ID 旁會顯示姓名，改名不會破壞關聯。',
      '3. 每筆各自勾選參與；用車日設定當日司機，可有多人。司機只免當天油錢／租車費。',
      '4. 正金額無分攤者、車資無司機或未用車皆會顯示提醒，暫不分攤該筆，金額保留。',
      '5. 金額以 0.1 元整數計算，尾差依名單固定順序每人加 0.1 元。停車費歸入其他費用。',
      '6. 人數、天數、費用增減與全選，請用「旅伴帳本」選單，不要自行插刪欄列或改隱藏 ID。',
      '7. 結構變更前會保留上一份資料備份；亦可匯出 JSON，與原網頁的資料格式相容。',
      '8. 目前上限 100 名額、31 天、每天 30 筆，預設 30 名額與 10 天，沒有示例支出。',
      '9. Google 原生格式會自動儲存並讓檢視者看到最新內容；不是網頁的手動唯讀快照。',
      '請在你指定的 Google Drive 費用資料夾建立／匯入試算表。',
      '這份檔案不會自動上傳；需由你在帳戶內建立／匯入。核取方塊啟用前 TRUE/FALSE 只是布林值。'
    ];
    instructions.forEach((t,i)=>help.text(i+1,2,t));
    const calc=sheet(CALC,true);['日期 ID','成員 ID','餐費整數','油錢整數','租車整數','其他整數','總分攤整數','代墊整數'].forEach((v,i)=>calc.text(1,i+1,v));
    model.days.forEach((d,di)=>model.members.forEach((m,i)=>{
      const r=2+di*n+i, sq=q(daily[di].name), hr=shareStart+i;
      calc.text(r,1,d.id);calc.text(r,2,m.id);
      const terms=(cats)=>d.expenses.map((e,ei)=>{const c=col(4+ei*2);return `IF(OR(${cats.map(t=>`${sq}${c}$5="${t}"`).join(',')}),${sq}${c}${hr},0)`;});
      [['早餐','午餐','晚餐'],['油錢'],['租車費'],['停車費','其他']].forEach((cats,k)=>calc.formula(r,3+k,`SUM(${terms(cats).join(',')||'0'})`));
      calc.formula(r,7,`SUM(C${r}:F${r})`);
      const advances=d.expenses.map((e,ei)=>{const c=col(4+ei*2);return `IF(${sq}${c}$7=$B${r},${sq}${c}${hs},0)`;});
      calc.formula(r,8,`IF(LEN(TRIM('成員名單'!$B${5+i}))=0,0,SUM(${advances.join(',')||'0'}))`);
    }));
    const calcEnd=1+model.days.length*n, sumRefs=refs=>`SUM(${refs.join(',')||'0'})`, allTotal=sumRefs(dayUnitRefs), allAllocated=sumRefs(dayAllocatedRefs);
    overview.text(1,2,'整趟費用總覽');overview.text(2,2,'所有金額以元計，精確至 0.1 元；有待處理項目時請先補齊再結算。');
    overview.text(3,2,'全部支出');overview.formula(3,3,`${allTotal}/10`);overview.text(3,5,'已分攤總額');overview.formula(3,6,`${allAllocated}/10`);overview.text(3,8,'差額');overview.formula(3,9,`(${allTotal}-${allAllocated})/10`);
    overview.text(4,2,'待處理費用（筆）');overview.formula(4,3,sumRefs(statusRefs.map(r=>`IF(LEN(${r})>0,1,0)`)));overview.text(4,5,'未指定代墊');overview.formula(4,6,`(${allTotal}-SUM('_計算'!H2:H${calcEnd}))/10`);
    overview.text(5,2,'待處理費用的合法金額仍列支出與已代墊，但暫不分攤。請看下方核對清單。');
    ['固定 ID','姓名','餐費合計','油錢合計','租車費合計','其他費用合計','總分攤','已代墊','還要付','應收回'].forEach((v,i)=>overview.text(7,i+1,v));
    const sumPerson=(r,c)=>`SUMIF('_計算'!$B$2:$B$${calcEnd},$A${r},'_計算'!$${c}$2:$${c}$${calcEnd})`;
    model.members.forEach((m,i)=>{
      const r=8+i;overview.text(r,1,m.id);overview.formula(r,2,rosterName('$A'+r));
      ['C','D','E','F','G','H'].forEach((c,k)=>overview.formula(r,3+k,`${sumPerson(r,c)}/10`));
      overview.formula(r,9,`MAX(0,(${sumPerson(r,'G')}-${sumPerson(r,'H')})/10)`);
      overview.formula(r,10,`MAX(0,(${sumPerson(r,'H')}-${sumPerson(r,'G')})/10)`);
    });
    overview.text(n+8,2,'合計');
    for(let ci=3;ci<=8;ci++)overview.formula(n+8,ci,`SUM('_計算'!${col(ci)}2:${col(ci)}${calcEnd})/10`);
    overview.formula(n+8,9,`SUM(${model.members.map((m,i)=>`MAX(0,${sumPerson(8+i,'G')}-${sumPerson(8+i,'H')})`).join(',')})/10`);
    overview.formula(n+8,10,`SUM(${model.members.map((m,i)=>`MAX(0,${sumPerson(8+i,'H')}-${sumPerson(8+i,'G')})`).join(',')})/10`);
    overview.text(n+11,2,'費用核對清單（同筆多個提醒計為一筆待處理）');['日期','費用','總金額','待處理提醒'].forEach((v,i)=>overview.text(n+12,i+2,v));
    detail.forEach((x,i)=>{const r=n+13+i;overview.formula(r,2,x.day);overview.formula(r,3,x.name);overview.formula(r,4,x.amount+'/10');overview.formula(r,5,x.status);overview.merges.push(`E${r}:J${r}`);});
    overview.freezeRows=7;overview.freezeColumns=2;overview.widths={A:280,B:160,C:125,D:125,E:125,F:145,G:125,H:125,I:125,J:125};overview.hiddenColumns=['A'];
    const meta=sheet(META,true),metadata={schema:1,model,daySheets:daily.map(s=>({dayId:model.days[daily.indexOf(s)].id,name:s.name}))};
    meta.text(1,1,'TRAVEL_SHEETS_V1');const text=JSON.stringify(metadata);for(let i=0;i<text.length;i+=30000)meta.text(2+i/30000,1,text.slice(i,i+30000));
    return {model,sheets,n,first,last,hs,shareStart,metadata};
  }
  return {LIMITS,CATEGORIES,META,CALC,col,q,expense,day,create,validate,build};
})();

function onOpen() {
  SpreadsheetApp.getUi().createMenu('旅伴帳本')
    .addItem('啟用／修復核取方塊','setupTravelSheet')
    .addSeparator().addItem('新增成員','addTravelMember').addItem('刪除成員（輸入固定 ID）','deleteTravelMember')
    .addItem('新增一天','addTravelDay').addItem('刪除目前日期','deleteTravelDay')
    .addItem('新增本日費用','addTravelExpense').addItem('刪除選取費用','deleteTravelExpense')
    .addSeparator().addItem('本欄費用全選','selectTravelExpense').addItem('本欄費用取消全選','unselectTravelExpense')
    .addSeparator().addItem('匯出 JSON 備份','exportTravelBackup').addItem('匯入 JSON 備份','importTravelBackup')
    .addItem('還原上一次結構變更前資料','restoreTravelBackup').addItem('重設全部','resetTravelSheet').addToUi();
}

function travelMeta_(ss) {
  const sheet=ss.getSheetByName(TravelSheets.META);
  if(!sheet||sheet.getRange('A1').getValue()!=='TRAVEL_SHEETS_V1')return null;
  const meta=JSON.parse(sheet.getRange(2,1,Math.max(1,sheet.getLastRow()-1),1).getValues().map(r=>r[0]).join(''));
  if(meta.schema!==1)throw Error('不支援的設定版本');TravelSheets.validate(meta.model);return meta;
}
function travelDaySheet_(ss,entry) {
  const byId=entry.sheetId===undefined?null:ss.getSheetById(entry.sheetId);
  const sheet=(byId&&byId.getRange('A1').getValue()===entry.dayId)?byId:ss.getSheetByName(entry.name);
  if(!sheet||sheet.getRange('A1').getValue()!==entry.dayId)throw Error('找不到日期分頁，請勿刪除隱藏 ID。');
  return sheet;
}
function readTravelModel_(ss) {
  const meta=travelMeta_(ss);if(!meta)throw Error('請先執行 setupTravelSheet');
  const model=JSON.parse(JSON.stringify(meta.model));
  const roster=ss.getSheetByName('成員名單');if(!roster)throw Error('找不到成員名單，請勿改名此分頁。');
  const names=roster.getRange(5,1,model.members.length,2).getValues();
  model.members=names.map(r=>({id:String(r[0]),name:String(r[1])}));
  const reverse=Object.fromEntries(Object.entries(TravelSheets.CATEGORIES).map(([k,v])=>[v,k]));
  model.days=model.days.map(d=>{
    const sheet=travelDaySheet_(ss,meta.daySheets.find(x=>x.dayId===d.id));
    const columns=3+2*d.expenses.length,rows=sheet.getRange(1,1,12+model.members.length,Math.max(3,columns)).getValues();
    d.name=String(rows[1][1]);d.useCar=rows[2][2]===true;
    d.drivers=rows.slice(11,11+model.members.length).filter(r=>r[2]===true).map(r=>String(r[0]));
    d.expenses=d.expenses.map((e,i)=>{
      const c=3+i*2,category=reverse[rows[4][c]];
      if(!category)throw Error('費用類別必須使用下拉選單的類別。');
      return {id:String(rows[0][c]),name:String(rows[3][c]),category,amount:String(rows[5][c]),payerId:rows[6][c]===''?null:String(rows[6][c]),participantIds:rows.slice(11,11+model.members.length).filter(r=>r[c]===true).map(r=>String(r[0]))};
    });return d;
  });return TravelSheets.validate(model);
}
function writeTravelBackup_(ss,model) {
  let s=ss.getSheetByName('_上次資料備份');
  if(s&&s.getRange('A1').getValue()!=='TRAVEL_BACKUP_V1')throw Error('「_上次資料備份」已有其他資料，請先更名；未覆寫原內容。');
  if(!s)s=ss.insertSheet('_上次資料備份');s.clear();s.getRange('A1').setValue('TRAVEL_BACKUP_V1');
  const raw=JSON.stringify(model);const chunks=[];for(let i=0;i<raw.length;i+=30000)chunks.push([raw.slice(i,i+30000)]);
  if(s.getMaxRows()<chunks.length+1)s.insertRowsAfter(s.getMaxRows(),chunks.length+1-s.getMaxRows());
  s.getRange(2,1,chunks.length,1).setValues(chunks);s.hideSheet();
}
function applyTravelSpec_(ss,spec) {
  // Only these managed sheets are changed. All other tabs remain untouched.
  const old=travelMeta_(ss), oldDays=old?old.daySheets.map(x=>travelDaySheet_(ss,x)):[];
  const oldManaged=new Set(['整趟總覽','成員名單','使用說明',TravelSheets.CALC,TravelSheets.META,...oldDays.map(s=>s.getName())]);
  for(const s of ss.getSheets())if(spec.sheets.some(x=>x.name===s.getName())&&!oldManaged.has(s.getName())&&s.getLastRow()>0)throw Error('同名分頁已有其他資料，請使用新的空白試算表。');
  const nativeSheets=new Map();
  for(const def of spec.sheets) {
    let s=ss.getSheetByName(def.name);if(!s)s=ss.insertSheet(def.name);nativeSheets.set(def.name,s);
    const maxRow=Math.max(...def.cells.map(c=>c.r),12),maxCol=Math.max(...def.cells.map(c=>c.c),3);
    if(s.getMaxRows()<maxRow)s.insertRowsAfter(s.getMaxRows(),maxRow-s.getMaxRows());
    if(s.getMaxColumns()<maxCol)s.insertColumnsAfter(s.getMaxColumns(),maxCol-s.getMaxColumns());
    s.getProtections(SpreadsheetApp.ProtectionType.SHEET).filter(p=>p.getDescription()==='旅伴帳本公式保護').forEach(p=>p.remove());
    s.showRows(1,s.getMaxRows());s.showColumns(1,s.getMaxColumns());
    const originalRange=s.getRange(1,1,s.getMaxRows(),s.getMaxColumns());originalRange.breakApart();originalRange.clearDataValidations();s.clear();
    const values=Array.from({length:maxRow},()=>Array(maxCol).fill(''));
    def.cells.forEach(cell=>{values[cell.r-1][cell.c-1]=cell.kind==='text'&&String(cell.v).startsWith('=')?"'"+cell.v:cell.v;});
    s.getRange(1,1,maxRow,maxCol).setValues(values).setFontFamily('Noto Sans TC').setFontSize(11).setVerticalAlignment('middle');
    s.getRange(1,1,maxRow,maxCol).setNumberFormat('0.0');
    s.getRange(1,1,Math.min(11,maxRow),maxCol).setWrap(true);
    for(const merge of def.merges)s.getRange(merge).merge();
    const columnNumber=letters=>[...letters].reduce((n,ch)=>n*26+ch.charCodeAt(0)-64,0);
    const widths=Object.entries(def.widths).map(([c,w])=>[columnNumber(c),w]).sort((a,b)=>a[0]-b[0]);
    for(let i=0;i<widths.length;){let j=i+1;while(j<widths.length&&widths[j][0]===widths[j-1][0]+1&&widths[j][1]===widths[i][1])j++;s.setColumnWidths(widths[i][0],j-i,widths[i][1]);i=j;}
    s.setFrozenRows(def.freezeRows);s.setFrozenColumns(def.freezeColumns);
    const header=def.name==='整趟總覽'?7:def.name==='成員名單'?4:def.name.startsWith('第 ')?11:1;
    s.getRange(header,1,1,maxCol).setBackground('#176253').setFontColor('#ffffff').setFontWeight('bold');
    if(def.editable.length)s.getRangeList(def.editable).setBackground('#fff8d9');
    def.checkboxes.forEach(a1=>s.getRange(a1).setDataValidation(SpreadsheetApp.newDataValidation().requireCheckbox().setAllowInvalid(false).build()));
    def.validations.forEach(v=>{
      const b=SpreadsheetApp.newDataValidation().setAllowInvalid(false);
      if(v.type==='list')b.requireValueInList(v.values,true);
      else if(v.type==='range')b.requireValueInRange(ss.getSheetByName('成員名單').getRange(`A5:A${spec.n+4}`),true);
      else b.requireFormulaSatisfied('='+v.formula);
      s.getRange(v.range).setDataValidation(b.build());
    });
    def.hiddenColumns.forEach(c=>s.hideColumns(columnNumber(c)));
    def.hiddenRows.forEach(([a,b])=>s.hideRows(a,b-a+1));
    // Warn on formula / ID edits; actual viewer access is controlled by Google sharing.
    if(!def.hidden&&s.getProtections(SpreadsheetApp.ProtectionType.SHEET).length===0){const p=s.protect().setDescription('旅伴帳本公式保護').setWarningOnly(true);p.setUnprotectedRanges(def.editable.map(a=>s.getRange(a)));}
    if(s.getMaxRows()>maxRow+5)s.deleteRows(maxRow+6,s.getMaxRows()-maxRow-5);
    if(s.getMaxColumns()>maxCol)s.deleteColumns(maxCol+1,s.getMaxColumns()-maxCol);
  }
  spec.metadata.daySheets.forEach(e=>{e.sheetId=nativeSheets.get(e.name).getSheetId();});
  const metadata=nativeSheets.get(TravelSheets.META),text=JSON.stringify(spec.metadata),chunks=[];for(let i=0;i<text.length;i+=30000)chunks.push([text.slice(i,i+30000)]);
  metadata.clear();if(metadata.getMaxRows()<chunks.length+1)metadata.insertRowsAfter(metadata.getMaxRows(),chunks.length+1-metadata.getMaxRows());metadata.getRange('A1').setValue('TRAVEL_SHEETS_V1');metadata.getRange(2,1,chunks.length,1).setValues(chunks);
  ss.setActiveSheet(nativeSheets.get('整趟總覽'));
  oldDays.forEach(s=>{if(!nativeSheets.has(s.getName()))ss.deleteSheet(s);});
  spec.sheets.forEach(def=>{if(def.hidden)nativeSheets.get(def.name).hideSheet();});
  SpreadsheetApp.flush();
}
function setupTravelSheet() {
  const ss=SpreadsheetApp.getActive(),old=travelMeta_(ss);
  if(old) { // Restore native checkbox validations without replacing any current values.
    const model=readTravelModel_(ss),spec=TravelSheets.build(model);
    spec.model.days.forEach((d,i)=>{
      const sheet=travelDaySheet_(ss,old.daySheets.find(x=>x.dayId===d.id));
      spec.sheets.find(s=>s.name==='第 '+(i+1)+' 天').checkboxes.forEach(a=>sheet.getRange(a).setDataValidation(SpreadsheetApp.newDataValidation().requireCheckbox().setAllowInvalid(false).build()));
    });
    old.daySheets.forEach(e=>{e.sheetId=travelDaySheet_(ss,e).getSheetId();});
    const raw=JSON.stringify(old),parts=[];for(let i=0;i<raw.length;i+=30000)parts.push([raw.slice(i,i+30000)]);
    const ms=ss.getSheetByName(TravelSheets.META);if(ms.getMaxRows()<parts.length+1)ms.insertRowsAfter(ms.getMaxRows(),parts.length+1-ms.getMaxRows());ms.getRange(2,1,ms.getMaxRows()-1,1).clearContent();ms.getRange(2,1,parts.length,1).setValues(parts);
  } else {
    if(ss.getSheets().some(s=>s.getLastRow()>0))throw Error('請在新的空白試算表執行，或使用附帶的 XLSX 範本；既有資料未變更。');
    ss.setSpreadsheetLocale('zh_TW');ss.setSpreadsheetTimeZone('Asia/Taipei');
    applyTravelSpec_(ss,TravelSheets.build(TravelSheets.create()));
  }
  onOpen();ss.toast('原生核取方塊已啟用；公式自動計算，修改會由 Google 自動儲存。','旅伴帳本',8);
}
function travelPrompt_(title,message) {const ui=SpreadsheetApp.getUi(),r=ui.prompt(title,message,ui.ButtonSet.OK_CANCEL);return r.getSelectedButton()===ui.Button.OK?r.getResponseText():null;}
function travelConfirm_(message) {const ui=SpreadsheetApp.getUi();return ui.alert('旅伴帳本',message,ui.ButtonSet.YES_NO)===ui.Button.YES;}
function travelChange_(change) {
  const lock=LockService.getDocumentLock();if(!lock.tryLock(15000))throw Error('帳本正在更新，請稍後重試。');
  try {const ss=SpreadsheetApp.getActive(),original=readTravelModel_(ss),next=JSON.parse(JSON.stringify(original));if(change(next,ss)===false)return;const spec=TravelSheets.build(next);writeTravelBackup_(ss,original);applyTravelSpec_(ss,spec);}
  finally {lock.releaseLock();}
}
function activeTravelDay_(model,ss) {const meta=travelMeta_(ss),entry=meta.daySheets.find(e=>travelDaySheet_(ss,e).getSheetId()===ss.getActiveSheet().getSheetId());if(!entry)throw Error('請先選取每日明細分頁。');return model.days.find(d=>d.id===entry.dayId);}
function addTravelMember() {const name=travelPrompt_('新增成員','輸入姓名（可留空）');if(name===null)return;travelChange_(m=>{if(m.members.length>=TravelSheets.LIMITS.members)throw Error('最多 100 名額');m.members.push({id:'p_'+Date.now().toString(36),name});});}
function deleteTravelMember() {
  const ss=SpreadsheetApp.getActive(),original=readTravelModel_(ss);
  const pid=travelPrompt_('刪除成員','成員名單的 A 欄為固定 ID。輸入要刪除的固定 ID，例如 P001：');if(pid===null)return;
  const member=original.members.find(x=>x.id===pid.trim());if(!member)throw Error('找不到該 ID');
  if(!travelConfirm_('確定刪除「'+(member.name||member.id)+'」及所有日期的參與、司機與代墊關聯？將重新分攤。'))return;
  travelChange_(m=>{if(m.members.length===1)throw Error('至少保留一個名額');m.members=m.members.filter(x=>x.id!==member.id);m.days.forEach(d=>{d.drivers=d.drivers.filter(x=>x!==member.id);d.expenses.forEach(e=>{e.participantIds=e.participantIds.filter(x=>x!==member.id);if(e.payerId===member.id)e.payerId=null;});});});
}
function addTravelDay() {const name=travelPrompt_('新增一天','輸入日期名稱（留空使用預設名稱）');if(name===null)return;travelChange_(m=>{if(m.days.length>=TravelSheets.LIMITS.days)throw Error('最多 31 天');const d=TravelSheets.day(m.days.length+1);if(name)d.name=name;m.days.push(d);});}
function deleteTravelDay() {const ss=SpreadsheetApp.getActive(),m=readTravelModel_(ss),d=activeTravelDay_(m,ss);if(!travelConfirm_('確定刪除「'+d.name+'」及當日全部資料？'))return;travelChange_((m,ss)=>{if(m.days.length===1)throw Error('至少保留一天');const d=activeTravelDay_(m,ss);m.days=m.days.filter(x=>x.id!==d.id);});}
function addTravelExpense() {const name=travelPrompt_('新增費用','輸入名稱，可在欄位中調整類別');if(name===null)return;travelChange_((m,ss)=>{const d=activeTravelDay_(m,ss);if(d.expenses.length>=TravelSheets.LIMITS.expenses)throw Error('每天最多 30 筆');const e=TravelSheets.expense();if(name)e.name=name;d.expenses.push(e);});}
function deleteTravelExpense() {const ss=SpreadsheetApp.getActive(),m=readTravelModel_(ss),d=activeTravelDay_(m,ss),index=Math.floor((ss.getActiveRange().getColumn()-4)/2);if(index<0||!d.expenses[index])throw Error('請先選取要刪除的費用欄');if(!travelConfirm_('確定刪除「'+d.expenses[index].name+'」及金額、代墊與參與紀錄？'))return;travelChange_((m,ss)=>{activeTravelDay_(m,ss).expenses.splice(index,1);});}
function bulkTravelExpense_(checked) {const ss=SpreadsheetApp.getActive(),m=readTravelModel_(ss),d=activeTravelDay_(m,ss),index=Math.floor((ss.getActiveRange().getColumn()-4)/2);if(index<0||!d.expenses[index])throw Error('請先選取費用欄');const s=ss.getActiveSheet(),c=4+index*2,values=s.getRange(12,c,m.members.length,1).getValues();m.members.forEach((p,i)=>{if(p.name.trim())values[i][0]=checked;});s.getRange(12,c,m.members.length,1).setValues(values);}
function selectTravelExpense() {bulkTravelExpense_(true);}
function unselectTravelExpense() {bulkTravelExpense_(false);}
function exportTravelBackup() {const raw=JSON.stringify(readTravelModel_(SpreadsheetApp.getActive()),null,2);const escaped=raw.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');SpreadsheetApp.getUi().showModalDialog(HtmlService.createHtmlOutput('<p>複製全部內容另存為 .json，或按下載。此檔可匯回原網頁。</p><a download="travel-backup.json" href="data:application/json;charset=utf-8,'+encodeURIComponent(raw)+'">下載 JSON</a><textarea style="width:100%;height:350px">'+escaped+'</textarea>').setWidth(650).setHeight(450),'匯出備份');}
function importTravelBackup() {const raw=travelPrompt_('匯入 JSON','貼上 JSON 備份。格式會先檢查，失敗不改原資料。');if(raw===null)return;if(raw.length>10*1024*1024)throw Error('備份超過 10 MB');const next=TravelSheets.validate(JSON.parse(raw));if(!travelConfirm_('檢查通過，確定取代目前整趟資料？'))return;travelChange_(m=>{Object.assign(m,next);});}
function restoreTravelBackup() {const ss=SpreadsheetApp.getActive(),s=ss.getSheetByName('_上次資料備份');if(!s||s.getRange('A1').getValue()!=='TRAVEL_BACKUP_V1')throw Error('還沒有有效的結構變更備份');const next=TravelSheets.validate(JSON.parse(s.getRange(2,1,s.getLastRow()-1,1).getValues().map(r=>r[0]).join('')));if(!travelConfirm_('確定還原上次結構變更前的整趟資料？'))return;travelChange_(m=>Object.assign(m,next));}
function resetTravelSheet() {if(!travelConfirm_('確定重設全部為 30 個空白名額、10 天？目前資料會先保留為上次備份。'))return;travelChange_(m=>Object.assign(m,TravelSheets.create()));}
function onEdit(e) {
  if(!e||!e.range)return;const s=e.range.getSheet();
  if(e.range.getA1Notation()!=='C3'||s.getRange('A1').getValue()===''||e.value!=='FALSE')return;
  try {
    const m=readTravelModel_(e.source),d=m.days.find(d=>d.id===s.getRange('A1').getValue());if(!d)return;
    if(d.expenses.some(x=>['fuel','rental'].includes(x.category)&&x.amount.trim()!==''&&Number(x.amount)!==0)) {
      if(!travelConfirm_('當天已有車資。取消用車將保留金額與紀錄，暫不分攤並列為待處理。確定取消用車？'))e.range.setValue(true);
    }
  } catch(err) {e.source.toast(String(err.message),'請檢查費用設定',8);}
}
