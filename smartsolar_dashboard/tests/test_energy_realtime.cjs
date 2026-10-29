const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.resolve(__dirname, '../static/src/components/dashboard/dashboard.js'), 'utf8')
    .replace(/^import .*;$/mg, '')
    .replace('export class SmartSolarDashboard', 'globalThis.SmartSolarDashboard = class SmartSolarDashboard');

function fixture(systemId = 1) {
    let now = 0, nextId = 0;
    const timers = new Map(), requests = [], listeners = new Map();
    const context = {
        Component: class {}, SystemOverview: class {}, BmsPanel: class {}, BmsHistory: class {},
        registry: {category: () => ({add() {}})}, console, _t: x => x,
        Date: class extends Date {static now() {return now;}},
        setTimeout(fn, delay) {const id = ++nextId; timers.set(id, {fn, at: now + delay}); return id;},
        clearTimeout(id) {timers.delete(id);},
        rpc(route, args) {return new Promise((resolve, reject) => requests.push({route, args, resolve, reject}));},
    };
    vm.createContext(context);
    vm.runInContext(source, context);
    const c = new context.SmartSolarDashboard();
    const initial = {metrics: [{today: 1, month: 10}], saved: 20, payable: 30};
    c.state = {systemId, timeRange: '6h', snapshotRefresh: 'original',
        data: {kpi: {energy_widgets: initial, total_kwh: 100}, cutoff: '2026-10-29 01:00:00'}};
    c._dataGeneration = 1;
    c._appendRealtimePoint = () => {};
    c.notification = {add() {throw new Error('Unexpected notification');}};
    c.busService = {addChannel: async () => {}, deleteChannel() {},
        subscribe: (key, fn) => listeners.set(key, fn), unsubscribe: key => listeners.delete(key)};
    const settle = async () => {for (let i = 0; i < 8; i++) await Promise.resolve();};
    return {c, timers, requests, initial, listeners, settle,
        async advance(ms) {
            now += ms;
            for (const [id, timer] of [...timers]) {
                if (timer.at <= now) {timers.delete(id); timer.fn();}
            }
            await settle();
        }};
}
const snapshot = (cutoff = '2026-10-29 01:00:05', today = 2) => ({
    cutoff, energy_widgets: {metrics: [{today, month: 11}], saved: 25, payable: 35}});
let passed = 0;
async function test(name, run) {await run(); passed++; console.log('PASS ' + name);}
(async () => {
    await test('bus burst updates only energy cards without full refresh or BMS refresh', async () => {
        const f = fixture(); await f.c._startRealtime();
        for (let i = 0; i < 100; i++) f.listeners.get('smartsolar_data')({system_id: 1, device_type: 'charge_power'});
        assert.equal(f.timers.size, 1); await f.advance(4999); assert.equal(f.requests.length, 0);
        await f.advance(1); assert.equal(f.requests.length, 1);
        assert.equal(f.requests[0].route, '/smartsolar/dashboard/energy_widgets');
        assert.equal(f.requests[0].args.system_id, 1);
        const data = snapshot(); f.requests[0].resolve(data); await f.settle();
        assert.equal(f.c.state.data.kpi.energy_widgets, data.energy_widgets);
        assert.equal(f.c.state.data.kpi.total_kwh, 100);
        assert.equal(f.c.state.data.cutoff, '2026-10-29 01:00:00');
        assert.equal(f.c.state.snapshotRefresh, 'original'); assert.equal(f.timers.size, 0);
    });
    await test('no parallel requests and continuous traffic is throttled rather than starved', async () => {
        const f = fixture(); f.c._queueEnergyWidgets(); await f.advance(5000);
        for (let i = 0; i < 100; i++) f.c._queueEnergyWidgets();
        assert.equal(f.requests.length, 1); assert.equal(f.timers.size, 0);
        f.requests[0].resolve(snapshot()); await f.settle(); assert.equal(f.timers.size, 1);
        await f.advance(4999); assert.equal(f.requests.length, 1);
        await f.advance(1); assert.equal(f.requests.length, 2);
    });
    await test('network failure keeps confirmed values and retries only after new data', async () => {
        const f = fixture(); f.c._queueEnergyWidgets(); await f.advance(5000);
        f.requests[0].reject(new Error('offline')); await f.settle();
        assert.equal(f.c.state.data.kpi.energy_widgets, f.initial);
        await f.advance(60000); assert.equal(f.requests.length, 1);
        f.c._queueEnergyWidgets(); await f.advance(0); assert.equal(f.requests.length, 2);
        f.requests[1].resolve(snapshot()); await f.settle();
        assert.equal(f.c.state.data.kpi.energy_widgets.metrics[0].today, 2);
    });
    await test('system switch rejects old request without clearing a new request', async () => {
        const f = fixture(); f.c._queueEnergyWidgets(); await f.advance(5000);
        f.c.state.systemId = 2; f.c._stopRealtime(); f.c._queueEnergyWidgets(); await f.advance(5000);
        f.requests[0].resolve(snapshot()); await f.settle();
        assert.equal(f.c.state.data.kpi.energy_widgets, f.initial);
        f.requests[1].resolve(snapshot(undefined, 3)); await f.settle();
        assert.equal(f.c.state.data.kpi.energy_widgets.metrics[0].today, 3);
    });
    await test('older overview cannot replace newer energy snapshot', async () => {
        const f = fixture(); const overview = f.c._loadData();
        f.c._queueEnergyWidgets(); await f.advance(5000);
        const latest = snapshot(); f.requests[1].resolve(latest); await f.settle();
        f.requests[0].resolve({kpi: {energy_widgets: f.initial}, cutoff: '2026-10-29 01:00:01'});
        await overview; assert.equal(f.c.state.data.kpi.energy_widgets, latest.energy_widgets);
    });
    await test('overview refresh invalidates a pending energy response', async () => {
        const f = fixture(); f.c._queueEnergyWidgets(); await f.advance(5000);
        const overview = f.c._loadData();
        f.requests[1].resolve({kpi: {energy_widgets: f.initial}, cutoff: '2026-10-29 01:00:10'});
        await overview; f.requests[0].resolve(snapshot()); await f.settle();
        assert.equal(f.c.state.data.kpi.energy_widgets, f.initial);
    });
    await test('unrelated events are ignored and global scope accepts both device types', async () => {
        const f = fixture(); await f.c._startRealtime();
        f.listeners.get('smartsolar_data')({system_id: 2, device_type: 'charge_power'});
        f.listeners.get('smartsolar_data')({system_id: 1, device_type: 'other'});
        assert.equal(f.timers.size, 0);
        const all = fixture(null); await all.c._startRealtime();
        all.listeners.get('smartsolar_data')({system_id: 2, device_type: 'grid_tie_inverter'});
        all.listeners.get('smartsolar_data')({system_id: 3, device_type: 'charge_power'});
        await all.advance(5000); assert.equal(all.requests.length, 1);
        assert.equal(all.requests[0].args.system_id, null);
    });
    await test('day/month rollover accepts authoritative zero or unknown rather than forcing increases', async () => {
        const f = fixture(); f.c._queueEnergyWidgets(); await f.advance(5000);
        const data = snapshot('2026-10-31 17:00:01', 0);
        data.energy_widgets.metrics[0].month = 0; data.energy_widgets.saved = null;
        f.requests[0].resolve(data); await f.settle();
        assert.equal(f.c.state.data.kpi.energy_widgets.metrics[0].today, 0);
        assert.equal(f.c.state.data.kpi.energy_widgets.saved, null);
    });
    await test('unmount cancels timers and ignores pending responses', async () => {
        const f = fixture(); f.c._queueEnergyWidgets(); f.c._disposed = true; f.c._stopRealtime();
        await f.advance(10000); assert.equal(f.requests.length, 0);
        const pending = fixture(); pending.c._queueEnergyWidgets(); await pending.advance(5000);
        pending.c._disposed = true; pending.c._stopRealtime();
        pending.requests[0].resolve(snapshot()); await pending.settle();
        assert.equal(pending.c.state.data.kpi.energy_widgets, pending.initial);
    });
    console.log(`${passed} energy realtime cases passed`);
})().catch(error => {console.error(error); process.exitCode = 1;});
