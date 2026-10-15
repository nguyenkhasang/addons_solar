const {JSDOM}=require('jsdom');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const dom=new JSDOM('<main id="root"></main>',{runScripts:'outside-only',pretendToBeVisual:true});
const w=dom.window;
w.eval(fs.readFileSync(root+'/../odoo-19.0/addons/web/static/lib/owl/owl.js','utf8'));
Object.assign(w,{Component:w.owl.Component,useState:w.owl.useState,useRef:w.owl.useRef,useEffect:w.owl.useEffect,
    onWillUnmount:w.owl.onWillUnmount,onMounted:w.owl.onMounted,onWillStart:w.owl.onWillStart,
    SystemOverview:class{},registry:{category:()=>({add(){}})},_t:x=>x});
const calls=[],charts=[],listeners=new Map();
w.rpc=(url,args)=>{
    calls.push({url,args});
    if(url.endsWith('/overview')) return Promise.resolve({kpi:{total_kwh:12},devices:[],bms:[],cutoff:'2026-10-15 01:00:00'});
    if(url.endsWith('/bms_history')) return Promise.resolve([]);
    return new Promise(resolve=>charts.push({resolve,args}));
};
w.Chart=class{update(){}destroy(){}};
w.eval(fs.readFileSync(root+'/smartsolar_dashboard/static/src/components/bms_history/bms_history.js','utf8')
    .replace(/^import .*;$/mg,'').replace('export class BmsHistory','window.BmsHistory = class BmsHistory'));
w.eval(fs.readFileSync(root+'/smartsolar_dashboard/static/src/components/dashboard/dashboard.js','utf8')
    .replace(/^import .*;$/mg,'').replace('export class SmartSolarDashboard','window.Dashboard = class SmartSolarDashboard')
    .replace('registry.category("actions").add("smartsolar_dashboard", SmartSolarDashboard);',''));
const tick=()=>new Promise(resolve=>w.requestAnimationFrame(()=>w.requestAnimationFrame(resolve)));
class Parent extends w.owl.Component {
    static template='Parent';static components={BmsHistory:w.BmsHistory};
    setup(){
        this.state=w.owl.useState({systemId:1,timeRange:'24h',data:null,lastRefresh:null,snapshotRefresh:null,chartsLoading:true,autoRefresh:false});
        this._deviceRealtime=new Map();this.notification={add(){}};
        this.busService={addChannel:async()=>{},deleteChannel:async()=>{},subscribe:(key,fn)=>listeners.set(key,fn),unsubscribe:key=>listeners.delete(key)};
        this._appendRealtimePoint=()=>{};this._renderAllCharts=()=>{};
        w.owl.onWillStart(()=>this._loadData());
        w.owl.onMounted(()=>{this._loadCharts();this._startRealtime();});
        w.owl.onWillUnmount(()=>{this._disposed=true;this._stopRealtime();});
    }
}
for(const name of ['_loadData','_loadCharts','_refresh','_startRealtime','_stopRealtime']) Parent.prototype[name]=w.Dashboard.prototype[name];
(async()=>{
    const template='<t t-name="Parent"><strong id="kpi" t-if="state.data"><t t-esc="state.data.kpi.total_kwh"/></strong><BmsHistory systemId="state.systemId" theme="\'dark\'" refreshToken="state.snapshotRefresh" autoRefresh="state.autoRefresh"/></t>';
    const bmsXml=fs.readFileSync(root+'/smartsolar_dashboard/static/src/components/bms_history/bms_history.xml','utf8').replace(/<\?xml.*?\?>/,'').replace(/<templates[^>]*>/,'').replace(/<\/templates>\s*$/,'');
    const app=new w.owl.App(Parent,{templates:'<templates>'+template+bmsXml+'</templates>'});
    const parent=await app.mount(w.document.getElementById('root'));await tick();
    assert.equal(w.document.querySelector('#kpi').textContent,'12');
    assert.equal(parent.state.chartsLoading,true);assert.equal(charts.length,1);
    const historyCount=()=>calls.filter(c=>c.url.endsWith('/bms_history')).length;
    assert.equal(historyCount(),1);
    const snapshot=parent.state.snapshotRefresh;
    for(let i=0;i<10;i++){listeners.get('smartsolar_data')({system_id:1});await tick();}
    assert.equal(parent.state.snapshotRefresh,snapshot);assert.equal(historyCount(),1);
    assert.equal(charts[0].args.include_overview,false);assert.equal(charts[0].args.end_at,'2026-10-15 01:00:00');
    charts.shift().resolve({energy_comparison:{labels:[]}});await tick();
    assert.equal(parent.state.chartsLoading,false);
    await parent._loadData();const old=parent._loadCharts();await tick();
    assert.equal(historyCount(),2); // only completed API refresh, not ten bus frames
    parent.state.systemId=2;await parent._loadData();const latest=parent._loadCharts();await tick();
    charts.shift().resolve({marker:'old'});await old;await tick();assert.notEqual(parent.state.data.marker,'old');
    charts.shift().resolve({marker:'new'});await latest;await tick();assert.equal(parent.state.data.marker,'new');
    const originalRpc=w.rpc;
    w.rpc=()=>Promise.reject(new Error('network unavailable'));
    parent.state.chartsLoading=true;
    assert.equal(await parent._loadData(),false);assert.equal(parent.state.chartsLoading,false);
    w.rpc=originalRpc;
    app.destroy();dom.window.close();console.log('PASS progressive KPI loading, fixed chart cutoff, no BMS RPC storm, stale chart rejection');
})().catch(error=>{console.error(error);process.exitCode=1;dom.window.close();});
