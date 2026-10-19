"""Recalculate actual XLSX formulas in LibreOffice and compare with the tested web engine."""
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from openpyxl import load_workbook

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec_import = importlib.util.spec_from_file_location('export_template', HERE/'export_template.py')
exporter = importlib.util.module_from_spec(spec_import)
spec_import.loader.exec_module(exporter)

FIXTURES = r"""
const fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1]+'/index.html','utf8');
const C=vm.runInNewContext(html.match(/<script id="trip-core">([\s\S]*?)<\/script>/)[1]+'\nTripCore;');
const T=vm.runInNewContext(fs.readFileSync(process.argv[1]+'/google-sheets/Code.gs','utf8')+'\nTravelSheets;');
const cases=[];
const base=(n=3,days=2)=>{const s=C.createTrip(n,days);s.members.forEach((m,i)=>m.name='旅伴'+(i+1));return s;};
const fee=(s,di,cat,amount,parts,payer=0)=>{const e=s.days[di].expenses.find(e=>e.category===cat);Object.assign(e,{amount,participantIds:parts.map(i=>s.members[i].id),payerId:payer===null?null:s.members[payer].id});return e;};
const add=(name,s)=>cases.push({name,model:s,expected:C.summarize(s),spec:T.build(s)});
add('initial_empty_30_people_10_days',T.create());
let s=base();fee(s,0,'breakfast','100',[2,0,1]);add('100_divided_by_3',s);
s=base();fee(s,0,'breakfast','100',[0,1]);fee(s,0,'lunch','90',[1,2],1);fee(s,0,'dinner','70',[0,2],2);fee(s,1,'breakfast','20',[2],2);add('meals_and_dates_independent',s);
s=base();s.days.forEach((d,i)=>{d.useCar=true;d.drivers=[s.members[i].id];fee(s,i,'fuel','100',[0,1,2],i);fee(s,i,'rental','120',[0,1,2],i);fee(s,i,'breakfast','90',[0,1,2],i);fee(s,i,'parking','30',[0,1,2],i);});add('day_1_A_day_2_B_drivers_and_meals',s);
s=base(4);s.days[0].useCar=true;s.days[0].drivers=s.members.slice(0,2).map(m=>m.id);fee(s,0,'fuel','100',[0,1,2,3]);fee(s,0,'other','40',[0,1,2,3]);add('multiple_drivers',s);
s=base();s.days[0].drivers=[s.members[0].id];fee(s,0,'fuel','100',[0,1,2]);fee(s,0,'lunch','30',[0,1,2]);add('no_car_cost_preserved',s);
s=base();s.days[0].useCar=true;fee(s,0,'fuel','100',[0,1,2]);add('missing_driver',s);
s=base();fee(s,0,'lunch','100',[]);add('no_participants',s);
s=base();s.days[0].useCar=true;s.days[0].drivers=s.members.map(m=>m.id);fee(s,0,'fuel','100',[0,1,2]);add('all_drivers_zero_eligible',s);
s=base();fee(s,0,'lunch','0',[],null);fee(s,0,'fuel','',[],null);add('zero_and_empty_amounts',s);
s=base();s.members[0].name='   ';s.members[1].name='';fee(s,0,'lunch','100',[0,1,2],2);add('blank_names_excluded',s);
s=C.createTrip();s.members[0].name='A';s.members[29].name='第30位';fee(s,0,'dinner','100',[0,29],29);add('person_30',s);
s=C.example();s.members[0].name='更名後';add('renaming_preserves_ids_advance_and_shares',s);
s=base();fee(s,0,'breakfast','100',[0,1,2]);let e=C.expense('breakfast');Object.assign(e,{amount:'30',participantIds:[s.members[1].id],payerId:s.members[1].id});s.days[0].expenses.push(e);add('duplicate_category_separate_expenses',s);
s=base();fee(s,0,'lunch','-1',[0,1,2]);fee(s,0,'dinner','1.23',[0,1,2]);fee(s,0,'other','abc',[0,1,2]);add('invalid_amounts',s);
s=base();fee(s,0,'lunch','1.000000001',[0,1,2]);add('excess_precision_not_silently_accepted',s);
s=base();fee(s,0,'lunch',' ',[],null);add('whitespace_amount',s);
s=base();fee(s,0,'lunch','100',[0,1,2],null);add('missing_payer',s);
s=C.example();add('full_example_all_totals_balanced',C.decode(C.encode(s)));
s=base(3,4);s.days.forEach(d=>{d.expenses=[];for(let i=0;i<30;i++){const e=C.expense('other');Object.assign(e,{amount:'0.1',payerId:s.members[0].id,participantIds:s.members.map(m=>m.id)});d.expenses.push(e);}});add('120_small_integer_shares_no_float_accumulation',s);
process.stdout.write(JSON.stringify(cases));
"""


def units(value):
    assert value is not None, 'Missing numeric formula cache: the workbook was not recalculated'
    assert not isinstance(value, str), f'Unexpected spreadsheet error/text: {value}'
    raw = Decimal(str(value)) * 10
    assert raw == raw.to_integral(), f'Fractional unit: {value}'
    return int(raw)


def main():
    fixtures = json.loads(subprocess.run(['node','-e',FIXTURES,str(ROOT)],check=True,capture_output=True,text=True).stdout)
    work = Path(tempfile.mkdtemp(prefix='travel-sheets-formulas-'))
    source = work/'source'
    output = work/'calculated'
    source.mkdir(); output.mkdir()
    inputs=[]
    for fixture in fixtures:
        file=source/(fixture['name']+'.xlsx')
        exporter.export_spec(fixture['spec'],file)
        inputs.append(file)
    command=[shutil.which('soffice') or 'libreoffice','-env:UserInstallation='+ (work/'lo-profile').as_uri(),'--headless','--convert-to','xlsx','--outdir',str(output),*[str(f) for f in inputs]]
    result=subprocess.run(command,capture_output=True,text=True,timeout=600)
    assert result.returncode==0,(result.stdout,result.stderr)
    print(result.stdout,flush=True)
    results=[]
    for fixture in fixtures:
        file=output/(fixture['name']+'.xlsx')
        assert file.exists(),(fixture['name'],result.stderr)
        wb=load_workbook(file,data_only=True)
        formulas=load_workbook(file,data_only=False)
        for sh in formulas:
            for row in sh:
                for cell in row:
                    if cell.data_type=='f':
                        cached=wb[sh.title][cell.coordinate]
                        assert cached.data_type!='e',(fixture['name'],sh.title,cell.coordinate,cached.value,cell.value)
        summary=fixture['expected'];ov=wb['整趟總覽'];model=fixture['model'];sp=fixture['spec']
        assert units(ov['C3'].value)==summary['total'],fixture['name']
        assert units(ov['F3'].value)==summary['allocated'],fixture['name']
        assert units(ov['I3'].value)==summary['difference'],fixture['name']
        assert units(ov['F4'].value)==summary['unassignedAdvance'],fixture['name']
        people={p['id']:p for p in summary['people']}
        for i,member in enumerate(model['members']):
            r=8+i;p=people.get(member['id'])
            for c,key in enumerate(['meals','fuel','rental','other','total','advanced','pay','receive'],3):
                assert units(ov.cell(r,c).value)==(p[key] if p else 0),(fixture['name'],member['id'],key,ov.cell(r,c).value,p)
        for di,day in enumerate(model['days']):
            sheet=wb['第 '+str(di+1)+' 天']
            for ei,expense in enumerate(day['expenses']):
                result=next(x for x in summary['expenses'] if x['expenseId']==expense['id'])
                actual_shares=[]
                for i,member in enumerate(model['members']):
                    actual=units(sheet.cell(12+i,5+2*ei).value)
                    assert actual==result['shares'][member['id']],(fixture['name'],ei,i,actual,result)
                    actual_shares.append(actual)
                assert sum(actual_shares)==result['allocated'],fixture['name']
                status=sheet.cell(10,4+2*ei).value or ''
                assert bool(status)==bool(result['issues']),(fixture['name'],status,result['issues'])
        # Reopen must retain original fixed IDs and all boolean inputs / values.
        assert wb['成員名單']['A5'].value==model['members'][0]['id']
        assert wb['第 1 天']['A1'].value==model['days'][0]['id']
        results.append(fixture['name'])
        print('PASS:',fixture['name'],flush=True)
    # Deliver the recalculated blank template, including caches for first preview.
    target=HERE/'旅遊費用分攤_Google試算表範本.xlsx'
    shutil.copy2(output/'initial_empty_30_people_10_days.xlsx',target)
    report={'engine':subprocess.run([command[0],'--version'],capture_output=True,text=True).stdout.strip(),'passed':results,'count':len(results),'artifacts':str(work),'google_runtime_tested':False}
    (HERE/'FORMULA_TEST_RESULTS.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(f'\n{len(results)} formula workbooks passed; output: {target}; artifacts: {work}',flush=True)


if __name__=='__main__':
    main()
