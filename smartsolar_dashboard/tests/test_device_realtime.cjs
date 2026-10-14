const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const context = {Component: class {}, BmsHistory: class {}, SystemOverview: class {},
    useState: x=>x, registry: {category:()=>({add(){}})}, console, _t:x=>x};
vm.createContext(context);
vm.runInContext(fs.readFileSync(root+'/static/src/components/system_overview/system_overview.js','utf8')
    .replace(/^import .*;$/mg,'').replace('export class SystemOverview','globalThis.SystemOverview = class SystemOverview'),context);
vm.runInContext(fs.readFileSync(root+'/static/src/components/dashboard/dashboard.js','utf8')
    .replace(/^import .*;$/mg,'').replace('export class SmartSolarDashboard','globalThis.SmartSolarDashboard = class SmartSolarDashboard'), context);
const devices = () => [
    {id:1,guid:'mppt',system_id:1,type:'charge_power',name:'MPPT',power:10,temperature:30,is_online:false,status:'offline',offline_minutes:5,last_sync:'2026-10-14 12:00:00'},
    {id:2,guid:'gti',system_id:1,type:'grid_tie_inverter',name:'GTI',power:20,temperature:31,is_online:false,status:'offline',offline_minutes:4,last_sync:'2026-10-14 12:00:00'},
];
function fixture() {
    const c=new context.SmartSolarDashboard();
    c.state={systemId:1,timeRange:'1h',data:{devices:devices(),kpi:{temperature_alert:45,total_devices:2,online_devices:0,offline_devices:2}}};
    c._deviceRealtime=new Map();c.notification={add(){throw Error('Unexpected notification');}};
    c._rt={};c._RT_MAX=120;c._overviewComponent=null;
    c._rtBuffer=Object.fromEntries(['labels','gt_output','gt_limiter','gt_pv','gt_charge','gt_observed_at',
        'cp_charge','cp_pv_v','cp_bat_v','cp_pv_input','bat_v','bat_v_min','bat_a','bat_net_a','bat_net_w',
        'eff_pv_in','eff_charge','eff_pct'].map(k=>[k,[]]));c._rtBuffer.temp_map={};
    return c;
}
const msg={device_id:1,device_guid:'mppt',system_id:1,device_type:'charge_power',pv_input_power:600,charge_power:500,temperature:32,label:'2026-10-14 12:00:01'};
let count=0;
function test(label,action){action();count++;console.log('PASS '+label);}
test('the regular bus append path also updates device rows',()=>{
    const c=fixture();c._appendRealtimePoint(msg);
    assert.equal(c.state.data.devices[0].power,600);assert.equal(c.state.data.devices[0].last_sync,msg.label);
});
test('device values and health update outside the Live chart range',()=>{
    const c=fixture();c._updateDeviceFromRealtime(msg);
    assert.equal(c.state.data.devices[0].power,600);
    assert.equal(c.state.data.devices[0].temperature,32);
    assert.equal(c.state.data.devices[0].last_sync,msg.label);
    assert.equal(c.state.data.devices[0].status,'online');
    assert.equal(c.state.data.devices[0].offline_minutes,null);
    assert.equal(c.state.data.devices[1].power,20);
    assert.equal(c.state.data.kpi.online_devices,1);assert.equal(c.state.data.kpi.offline_devices,1);
});
test('zero readings remain zero and high temperature raises a warning',()=>{
    const c=fixture();c._updateDeviceFromRealtime({...msg,pv_input_power:0,temperature:50});
    assert.equal(c.state.data.devices[0].power,0);assert.equal(c.state.data.devices[0].status,'warning');
    c._updateDeviceFromRealtime({...msg,pv_input_power:0,temperature:0,label:'2026-10-14 12:00:02'});
    assert.equal(c.state.data.devices[0].temperature,0);assert.equal(c.state.data.devices[0].status,'online');
});
test('inverter shows AC output and updates its own row',()=>{
    const c=fixture();c._updateDeviceFromRealtime({...msg,device_id:2,device_guid:'gti',device_type:'grid_tie_inverter',output_power:350});
    assert.equal(c.state.data.devices[1].power,350);assert.equal(c.state.data.devices[0].power,10);
});
test('foreign system, unknown id and wrong device type do not mutate rows',()=>{
    const c=fixture();const before=JSON.stringify(c.state.data);
    for(const change of [{system_id:2},{device_id:999},{device_type:'grid_tie_inverter'}]) c._updateDeviceFromRealtime({...msg,...change});
    assert.equal(JSON.stringify(c.state.data),before);
});
test('missing or invalid measurements keep their last known values',()=>{
    const c=fixture();c._updateDeviceFromRealtime({...msg,pv_input_power:NaN,temperature:null});
    assert.equal(c.state.data.devices[0].power,10);assert.equal(c.state.data.devices[0].temperature,30);
});
test('out-of-order frames cannot roll device readings back',()=>{
    const c=fixture();c._updateDeviceFromRealtime(msg);
    c._updateDeviceFromRealtime({...msg,pv_input_power:1,label:'2026-10-14 11:59:59'});
    assert.equal(c.state.data.devices[0].power,600);assert.equal(c.state.data.devices[0].last_sync,msg.label);
});
test('guid fallback rejects ambiguous devices and uses the system scope',()=>{
    const c=fixture();const payload={...msg,device_id:undefined};c._updateDeviceFromRealtime(payload);
    assert.equal(c.state.data.devices[0].power,600);
    c.state.systemId=null;c.state.data.devices.push({...devices()[0],id:3,system_id:2});
    c._updateDeviceFromRealtime({...payload,pv_input_power:700});
    assert.equal(c.state.data.devices[0].power,700);assert.equal(c.state.data.devices[2].power,10);
});
test('a newer server snapshot is not overwritten with an older bus cache',()=>{
    const c=fixture();c._updateDeviceFromRealtime(msg);
    c.state.data.devices=devices();c.state.data.devices[0].last_sync='2026-10-14 12:00:03';
    c.state.data.devices[0].power=800;c._updateDeviceFromRealtime(msg,true);
    assert.equal(c.state.data.devices[0].power,800);
});
test('rows sit below realtime power and above temperature with click preserved',()=>{
    const xml=fs.readFileSync(root+'/static/src/components/dashboard/dashboard.xml','utf8');
    assert(xml.indexOf('Công suất realtime') < xml.indexOf('ss_device_panel'));
    assert(xml.indexOf('ss_device_panel') < xml.indexOf('<!-- Nhiệt độ -->'));
    assert.equal((xml.match(/class="ss_device_panel"/g)||[]).length,1);
    assert(xml.includes('this.onDeviceClick(dev.id)'));assert(xml.includes('deviceSyncLabel(dev.last_sync)'));
});
(async()=>{
    const c=fixture();let resolve;
    context.rpc=()=>new Promise(r=>{resolve=r;});
    const pending=c._loadData();c._updateDeviceFromRealtime(msg);
    resolve({devices:devices(),kpi:{temperature_alert:45,total_devices:2,online_devices:0,offline_devices:2}});
    await pending;
    assert.equal(c.state.data.devices[0].power,600);assert.equal(c.state.data.devices[0].last_sync,msg.label);
    count++;console.log('PASS in-flight dashboard RPC cannot erase a newer bus reading');
    c._stopRealtime=()=>{};c._rt={};c._rtBuffer={temp_map:{}};c._refresh=async()=>{};c._startRealtime=async()=>{};
    await c.onSystemChange({target:{value:'2'}});assert.equal(c._deviceRealtime.size,0);
    count++;console.log('PASS changing system clears the per-device bus cache');
    console.log(count+' device realtime cases passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
