const {JSDOM}=require('jsdom');
const fs=require('node:fs'), path=require('node:path'), assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const dom=new JSDOM('<main id="root"></main>',{runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;
w.eval(fs.readFileSync(root+'/../odoo-19.0/addons/web/static/lib/owl/owl.js','utf8'));
Object.assign(w,{Component:w.owl.Component,useState:w.owl.useState,SystemOverview:class{},BmsHistory:class{},
    registry:{category:()=>({add(){}})},_t:x=>x});
w.eval(fs.readFileSync(root+'/smartsolar_dashboard/static/src/components/dashboard/dashboard.js','utf8')
    .replace(/^import .*;$/mg,'').replace('export class SmartSolarDashboard','window.Dashboard = class SmartSolarDashboard')
    .replace('registry.category("actions").add("smartsolar_dashboard", SmartSolarDashboard);',''));
class Panel extends w.owl.Component {
    static template='Panel';
    setup(){this.state=w.owl.useState({data:{kpi:{total_kwh:null,total_limiter_kwh:null,total_pv_kwh:null,
        revenue_total:null,currency_symbol:'₫',total_systems:1,lifetime:{initialized:false}}}});}
    get hasData(){return true;}
    onOpenSystems(){}
}
for(const name of ['fmtNumber','fmtMoney']) Panel.prototype[name]=w.Dashboard.prototype[name];
const xml=fs.readFileSync(root+'/smartsolar_dashboard/static/src/components/dashboard/dashboard.xml','utf8');
const panel=xml.slice(xml.indexOf('            <div class="ss_lifetime_grid"'),xml.indexOf('            <div class="ss_time_bar"'));
const tick=()=>new Promise(r=>w.requestAnimationFrame(()=>w.requestAnimationFrame(r)));
(async()=>{
    const app=new w.owl.App(Panel,{templates:'<templates><t t-name="Panel">'+panel+'</t></templates>'});
    const card=await app.mount(w.document.getElementById('root'));await tick();
    assert(w.document.body.textContent.includes('Đang tổng hợp lịch sử'));
    assert.equal(w.document.querySelector('.ss_lifetime_item strong').textContent,'— kWh');
    Object.assign(card.state.data.kpi,{total_kwh:10,total_limiter_kwh:0,revenue_total:20000,
        lifetime:{initialized:true,first_day:'2026-10-01',missing_days:2,partial_days:3,estimated_days:2,updated_at:'2026-10-14 15:00:00'}});
    await tick();
    const text=w.document.body.textContent;
    assert(text.includes('Tổng điện cấp tải'));assert(text.includes('Tổng tiết kiệm'));
    assert(text.includes('Thiếu 2 ngày dữ liệu'));assert(text.includes('3 ngày có dữ liệu một phần'));
    assert(text.includes('Điện năng có phần ước tính từ công suất'));assert(text.includes('2026-10-01'));assert(!text.includes('Đang tổng hợp lịch sử'));
    assert(!text.includes('NaN'));assert(text.includes('0 kWh'));
    app.destroy();dom.window.close();console.log('PASS lifetime Owl: null, valid zero, totals, loading, date, missing/partial history');
})().catch(e=>{console.error(e);process.exitCode=1;dom.window.close();});
