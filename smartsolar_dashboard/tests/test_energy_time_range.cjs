const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const context = {Component: class {}, registry: {category: () => ({add() {}})},
    SystemOverview: class {}, BmsPanel: class {}, BmsHistory: class {}, console, _t: x => x};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.resolve(__dirname,
    '../static/src/components/dashboard/dashboard.js'), 'utf8')
    .replace(/^import .*;$/mg, '')
    .replace('export class SmartSolarDashboard', 'globalThis.SmartSolarDashboard = class SmartSolarDashboard'), context);

(async () => {
    for (const buffered of [false, true]) {
        const c = new context.SmartSolarDashboard();
        const calls = [];
        c.state = {timeRange: '6h', systemId: 1,
            data: {kpi: {}, cutoff: '2026-10-15 00:00:00', energy_comparison: {labels: ['old week']}}};
        c._deviceRealtime = new Map();
        c._rtBuffer = {labels: buffered ? ['live sample'] : []};
        c._stopAutoRefresh = () => {};
        c._renderRealtimeFromBuffer = () => {};
        c._renderAllCharts = () => {};
        c.notification = {add() {}};
        context.rpc = async (url, args) => {
            calls.push({url, args});
            return url.endsWith('/overview')
                ? {kpi: {}, cutoff: '2026-10-16 06:00:00'}
                : {energy_comparison: {labels: ['selected window']}};
        };
        await c.onTimeRangeChange('realtime');
        assert.equal(calls.filter(call => call.url.endsWith('/data')).length, 1);
        const request = calls.find(call => call.url.endsWith('/data'));
        assert.equal(request.args.time_range, '2min');
        assert.equal(c.state.data.energy_comparison.labels[0], 'selected window');
        assert.equal(c.state.data.kpi !== undefined, true);
    }
    console.log('PASS Live reloads energy for its selected window with and without a populated buffer');
})().catch(error => {console.error(error); process.exitCode = 1;});
