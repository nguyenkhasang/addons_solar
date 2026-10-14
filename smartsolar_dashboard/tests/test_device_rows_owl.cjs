const {JSDOM}=require('jsdom');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'../..');
const dom=new JSDOM('<main id="root"></main>',{runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;
w.eval(fs.readFileSync(root+'/../odoo-19.0/addons/web/static/lib/owl/owl.js','utf8'));
Object.assign(w,{Component:w.owl.Component,useState:w.owl.useState,SystemOverview:class{},BmsHistory:class{},
    registry:{category:()=>({add(){}})},_t:x=>x});
w.eval(fs.readFileSync(root+'/smartsolar_dashboard/static/src/components/dashboard/dashboard.js','utf8')
    .replace(/^import .*;$/mg,'').replace('export class SmartSolarDashboard','window.Dashboard = class SmartSolarDashboard')
    .replace('registry.category("actions").add("smartsolar_dashboard", SmartSolarDashboard);',''));
let clicked;
const data={devices:[{id:1,guid:'mppt',system_id:1,type:'charge_power',name:'MPPT',system_name:'Solar',power:10,temperature:30,status:'offline',is_online:false,last_sync:'2026-10-14 12:00:00'},
    {id:2,guid:'gti',system_id:1,type:'grid_tie_inverter',name:'GTI',system_name:'Solar',power:20,temperature:31,status:'offline',is_online:false,last_sync:'2026-10-14 12:00:00'}],kpi:{temperature_alert:45}};
class DeviceRows extends w.owl.Component {
    static template='DeviceRows';
    setup(){this.state=w.owl.useState({systemId:1,data});this._deviceRealtime=new Map();this.action={doAction:a=>{clicked=a.res_id;}};}
}
for(const name of ['_updateDeviceFromRealtime','deviceSyncLabel','fmtNumber','deviceTypeLabel','onDeviceClick'])
    DeviceRows.prototype[name]=w.Dashboard.prototype[name];
const xml=fs.readFileSync(root+'/smartsolar_dashboard/static/src/components/dashboard/dashboard.xml','utf8');
const panel=xml.slice(xml.indexOf('                    <div class="ss_device_panel"'),xml.indexOf('                    <!-- Nhiệt độ -->'));
const tick=async()=>{await new Promise(r=>w.requestAnimationFrame(()=>w.requestAnimationFrame(r)));};
(async()=>{
    const app=new w.owl.App(DeviceRows,{templates:'<templates><t t-name="DeviceRows">'+panel+'</t></templates>'});
    const card=await app.mount(w.document.getElementById('root'));await tick();
    const rows=()=>w.document.querySelectorAll('.ss_device_row');assert.equal(rows().length,2);
    card._updateDeviceFromRealtime({device_id:1,device_guid:'mppt',system_id:1,device_type:'charge_power',pv_input_power:600,temperature:50,label:'2026-10-14 12:00:01'});
    await tick();assert(rows()[0].textContent.includes('600'));assert(rows()[0].textContent.includes('12:00:01'));
    assert(rows()[0].classList.contains('status_warning'));assert(rows()[0].querySelector('.alert'));
    card._updateDeviceFromRealtime({device_id:2,device_guid:'gti',system_id:1,device_type:'grid_tie_inverter',output_power:0,temperature:0,label:'2026-10-14 12:00:02'});
    await tick();assert(rows()[1].textContent.includes('12:00:02'));assert(rows()[1].classList.contains('status_online'));
    assert.equal(card.state.data.devices[1].power,0);
    rows()[1].click();assert.equal(clicked,2);assert.equal(rows().length,2);
    app.destroy();dom.window.close();console.log('PASS Owl device rows: reactive bus values, zero, warning, time, compact markup and device click');
})().catch(e=>{console.error(e);dom.window.close();process.exit(1);});
