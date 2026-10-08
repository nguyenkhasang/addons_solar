const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = process.argv[2] || path.resolve(__dirname, '..');
const context = {Component: class {}, useState: x=>x, registry:{category:()=>({add(){}})}, console, _t:x=>x};
vm.createContext(context);
for (const [file, name] of [ ['system_overview/system_overview.js','SystemOverview'], ['dashboard/dashboard.js','SmartSolarDashboard'] ]) {
    const code=fs.readFileSync(path.join(root,'static/src/components',file),'utf8')
        .replace(/^import .*;$/mg,'').replace(`export class ${name}`,`globalThis.${name} = class ${name}`);
    vm.runInContext(code,context);
}
const keys=['labels','gt_output','gt_limiter','gt_pv','gt_charge','gt_observed_at','cp_charge','cp_pv_v','cp_bat_v','cp_pv_input','bat_v','bat_v_min','bat_a','bat_net_a','bat_net_w','eff_pv_in','eff_charge','eff_pct'];
function dashboard() {
    const c=new context.SmartSolarDashboard();
    c.state={timeRange:'1h',theme:'dark',data:{devices:[{type:'charge_power'},{type:'grid_tie_inverter'}]}};
    c._rt={};c._rtBuffer=Object.fromEntries(keys.map(k=>[k,[]]));c._rtBuffer.temp_map={};
    c._RT_MAX=120;c.charts={};c._overviewComponent=null;c.refs={distribution:{el:null}};
    return c;
}
const charger={device_type:'charge_power',device_guid:'mppt',system_id:1,label:'2026-10-07 09:00:00',pv_input_power:600,charge_power:600,bat_voltage:50,bat_current:10,pv_voltage:100};
const inverter={device_type:'grid_tie_inverter',device_guid:'gti',system_id:1,label:'2026-10-07 09:00:01',output_power:200,limiter_power:5};
let count=0;
function test(name, action){action();count++;process.stdout.write('PASS '+name+'\n');}
test('widget net watts/current do not repeat PV or MPPT current',()=>{
    const c=dashboard();c._appendRealtimePoint(charger);c._appendRealtimePoint(inverter);
    assert.equal(c.realtimeChargeW,300);assert.equal(c.realtimeBatA,6);assert.equal(c.realtimeMpptA,10);
    assert.equal(c.realtimeBatteryFlow.gross,500);assert(c.realtimeBatStatus.includes('Sạc'));
    assert.equal(c._rtBuffer.eff_pct.at(-1),500/600*100);
});
test('positive MPPT current cannot hide net battery discharge',()=>{
    const c=dashboard();c._appendRealtimePoint(charger);c._appendRealtimePoint({...inverter,output_power:650});
    assert.equal(c.realtimeChargeW,-150);assert.equal(c.realtimeBatA,-3);assert(c.realtimeBatStatus.includes('Xả'));
});
test('missing counterpart remains unknown',()=>{
    const c=dashboard();c._appendRealtimePoint(charger);assert.equal(c.realtimeChargeW,null);assert.equal(c.realtimeBatA,null);
});
test('stale counterpart is not displayed as a measurement',()=>{
    const c=dashboard();c._appendRealtimePoint(charger);c._appendRealtimePoint({...inverter,label:'2026-10-07 09:02:00'});
    assert.equal(c.realtimeChargeW,null);assert(c.realtimeBatStatus.includes('Thiếu'));
});
test('different systems never form a battery balance',()=>{
    const c=dashboard();c._appendRealtimePoint(charger);c._appendRealtimePoint({...inverter,system_id:2});assert.equal(c.realtimeChargeW,null);
});
test('unsupported multiple-device live scope remains unknown',()=>{
    const c=dashboard();c.state.data.devices.push({type:'charge_power'});c._appendRealtimePoint(charger);c._appendRealtimePoint(inverter);
    assert.equal(c.realtimeChargeW,null);
});
test('all live power/current buffers remain aligned and bounded',()=>{
    const c=dashboard();for(let i=0;i<130;i++){c._appendRealtimePoint(charger);c._appendRealtimePoint(inverter);}
    for(const key of keys) assert.equal(c._rtBuffer[key].length,120,key);
});
test('historical alignment preserves missing and signed values',()=>{
    const c=dashboard();c.state.data.battery_flow={labels:['a','c'],net_power:[-138,500],net_current:[-2.5,9]};
    assert.deepEqual(Array.from(c._alignBatteryFlow(['a','b','c'],'net_power')),[-138,null,500]);
});
test('append updates exactly one label and point per dataset',()=>{
    const c=dashboard();c.state.timeRange='realtime';c._fmtLabel=x=>x;
    for(const [name,n] of Object.entries({gridTie:4,chargePower:3,battery:4,pvEfficiency:3})) c.charts[name]={data:{labels:[],datasets:Array.from({length:n},()=>({data:[]}))},update(){}};
    c._appendRealtimePoint(charger);c._appendRealtimePoint(inverter);
    for(const chart of Object.values(c.charts)){assert.equal(chart.data.labels.length,2);for(const ds of chart.data.datasets)assert.equal(ds.data.length,2);}
    assert.equal(c.charts.battery.data.datasets[2].data.at(-1),10);assert.equal(c.charts.battery.data.datasets[3].data.at(-1),6);
    assert.equal(c.charts.gridTie.data.datasets[3].data.at(-1),300);
});
test('historical chart uses estimated net curve and explicitly labels MPPT current',()=>{
    const c=dashboard();c.state.data.battery={labels:['a','b'],bat_voltage:[50,50],bat_voltage_min:[50,50],bat_current:[10,10]};
    c.state.data.battery_flow={labels:['a','b'],net_current:[6,-2],net_power:[300,-100]};
    c.refs={battery:{el:{getContext:()=>({createLinearGradient:()=>({addColorStop(){}})})}}};
    context.Chart=class {constructor(ctx,opts){this.data=opts.data;}};
    c._fmtLabel=x=>x;c._renderBatteryChart();
    assert(c.charts.battery.data.datasets[2].label.includes('MPPT'));
    assert(c.charts.battery.data.datasets[3].pointRadius > 0);
    assert.deepEqual(Array.from(c.charts.battery.data.datasets[3].data),[6,-2]);
});
test('second-bucket Live history aligns already paired MPPT and AC labels',()=>{
    const c=dashboard();c.state.data.battery_flow={bucket:'second', labels:['2026-10-07 09:00:00'],net_power:[-138]};
    assert.deepEqual(Array.from(c._alignBatteryFlow(['2026-10-07 09:00:01','2026-10-07 09:00:30'],'net_power')),[-138,null]);
});
test('Live pie uses live inverter samples instead of old counter distribution',()=>{
    const c=dashboard();c.state.timeRange='realtime';c._fmtLabel=x=>x;
    c.state.data.distribution={available:true,data:[0,100],estimated:false};
    c._appendRealtimePoint(inverter);
    c._appendRealtimePoint({...charger,label:'2026-10-07 09:01:00'});
    c._appendRealtimePoint({...inverter,label:'2026-10-07 09:01:01'});
    assert.equal(c.distributionInfo.estimated,true);
    assert(Math.abs(c.distributionInfo.data[0]-200/60000)<1e-12);
    assert(Math.abs(c.distributionInfo.percentages[0]-200/205*100)<1e-8);
    assert(c.distributionSubtitle.includes('Live'));
});
test('Live pie does not integrate repeated cached power over an outage',()=>{
    const c=dashboard();c.state.timeRange='realtime';c._fmtLabel=x=>x;
    c._appendRealtimePoint(inverter);
    c._appendRealtimePoint({...charger,label:'2026-10-07 09:10:00'});
    assert.equal(c.distributionInfo.available,false);
    c._appendRealtimePoint({...inverter,label:'2026-10-07 09:10:01'});
    assert.equal(c.distributionInfo.available,false);
});
test('Live pie reuses its chart, disables animation and skips unchanged readings',()=>{
    const c=dashboard();c.state.timeRange='realtime';c.refs.distribution.el={getContext:()=>({})};
    let snapshot={labels:['Inverter','Grid'],data:[0.2,0.01],available:true};
    c._liveDistributionFromBuffer=()=>snapshot;
    let created=0,destroyed=0;const updates=[];
    context.Chart=class {constructor(ctx,config){created++;this.data=config.data;this.options=config.options;}
        destroy(){destroyed++;}update(mode){updates.push(mode);}};
    c._renderDistributionChart();const chart=c.charts.distribution;
    assert.equal(chart.options.animation,false);
    c._renderDistributionChart();assert.equal(updates.length,0);
    snapshot={...snapshot,data:[0.21,0.012]};c._renderDistributionChart();
    assert.equal(c.charts.distribution,chart);assert.equal(created,1);assert.equal(destroyed,0);
    assert.deepEqual(updates,['none']);
    const label=chart.options.plugins.tooltip.callbacks.label({parsed:0.012,label:'Grid',dataset:chart.data.datasets[0],dataIndex:1});
    assert(label.includes('5.4%'));
});
(async()=>{
    const c=dashboard();c.state.timeRange='realtime';c.notification={add(){}};
    let range;
    context.rpc=async(route,args)=>{range=args.time_range;return c.state.data;};
    await c._loadData();assert.equal(range,'2min');count++;
    process.stdout.write('PASS changing system while Live requests two-minute history\n');
    process.stdout.write(`${count} dashboard battery cases passed\n`);
})().catch(error=>{console.error(error);process.exitCode=1;});
