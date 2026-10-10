const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const sourcePath = process.argv[2] || path.resolve(__dirname, '../static/src/components/system_overview/system_overview.js');
const code = fs.readFileSync(sourcePath, 'utf8').replace(/^import .*;$/mg, '').replace('export class SystemOverview', 'globalThis.SystemOverview = class SystemOverview');
const context = { Component: class {}, useState: value => value, onMounted: callback => callback(), onWillUnmount: () => {}, setInterval: () => 1, clearInterval: () => {} };
vm.createContext(context); vm.runInContext(code, context);
function overview() { const component = new context.SystemOverview(); component.props = {}; component.setup(); return component; }
function inverter(component, output = 184, grid = 5) { component.updateFromRealtime({ device_type: 'grid_tie_inverter', output_power: output, limiter_power: grid }); }
function charger(component, values = {}) { component.updateFromRealtime({ device_type: 'charge_power', charge_power: 795.68204, bat_voltage: 54.78, bat_current: 13.219, ...values }); }
let tested = 0;
function check(name, run) { run(); tested++; process.stdout.write('PASS ' + name + '\n'); }
check('screenshot: MPPT output supplies inverter and battery separately', () => {
    const c = overview(); inverter(c); charger(c);
    assert(Math.abs(c.state.charger_w - 724.13682) < 1e-8);
    assert(Math.abs(c.state.bat_flow_w - 540.13682) < 1e-8);
    assert(Math.abs(c.state.bat_a - 9.860109894) < 1e-8);
    assert.equal(c.state.home_w, 189);
    assert.equal(c.fmtW(c.state.bat_w), '540');
    assert.equal(c.fmtA(c.state.bat_a), '9.9');
    assert(Math.abs(c.state.solar_w + c.state.grid_w - c.state.home_w - c.state.bat_flow_w - c.controllerDifferenceW) < 1e-8);
});
check('night: zero MPPT output still permits battery discharge', () => {
    const c = overview(); inverter(c, 400, 20); charger(c, {charge_power: 0, bat_voltage: 50, bat_current: 0});
    assert.equal(c.state.bat_flow_w, -400); assert.equal(c.state.bat_a, -8); assert(c.batReverse); assert.equal(c.inverterSource, 'battery');
});
check('charging battery is not classified as an inverter source', () => {
    const c = overview(); inverter(c, 0, 0); charger(c, {charge_power: 100, bat_voltage: 50, bat_current: 4});
    assert.equal(c.inverterSource, 'solar'); assert(!c.batReverse);
});
check('inverter load change recomputes net battery flow', () => {
    const c = overview(); charger(c); inverter(c, 800); assert(c.batReverse);
    inverter(c, 200); assert(!c.batReverse); assert(Math.abs(c.state.bat_flow_w - 524.13682) < 1e-8);
});
check('missing inverter data is unknown instead of full charger output', () => {
    const c = overview(); charger(c); assert.equal(c.state.bat_flow_w, null); assert.equal(c.fmtW(c.state.bat_w), '—');
});
check('missing charger data is unknown instead of false battery discharge', () => {
    const c = overview(); inverter(c); assert.equal(c.state.bat_flow_w, null);
});
check('fallback without current is explicitly a PV balance estimate', () => {
    const c = overview(); inverter(c); charger(c, {bat_current: null}); assert.equal(c.state.charger_w, null);
    assert.equal(c.state.bat_flow_w, 611.68204); assert(c.batteryNote.includes('bộ sạc'));
});
check('invalid current cannot propagate NaN', () => {
    const c = overview(); inverter(c); charger(c, {bat_current: 'not-a-number'}); assert(Number.isFinite(c.state.bat_flow_w));
});
check('unknown battery voltage does not invent net current', () => {
    const c = overview(); inverter(c); charger(c, {bat_voltage: 0}); assert.equal(c.state.bat_a, null); assert.equal(c.fmtA(c.state.bat_a), '—');
});
check('initial KPI is inverter output, not grid import', () => {
    const c = overview(); c.props.kpi = {current_power_kw: 0.184}; c._syncFromProps();
    assert.equal(c.state.acout_w, 184); assert.equal(c.state.grid_w, 0); assert.equal(c.state.bat_flow_w, null);
});
check('misaligned samples warn without inventing a calibration offset', () => {
    const c = overview();
    c.updateFromRealtime({device_type: 'grid_tie_inverter', output_power: 211, label: '2026-10-07 08:21:53'});
    c.updateFromRealtime({device_type: 'charge_power', charge_power: 978.79815, bat_voltage: 55.11, bat_current: 16.161, label: '2026-10-07 08:21:52'});
    assert(!c.samplesMisaligned);
    assert(Math.abs(c.state.bat_flow_w - 679.63271) < 1e-8);
    assert(c.sampleTimesNote.includes('08:21:52'));
    c.updateFromRealtime({device_type: 'grid_tie_inverter', output_power: 211, label: '2026-10-07 08:23:02'});
    assert(c.samplesMisaligned);
    assert.equal(c.state.bat_flow_w, null);
});
process.stdout.write(`${tested} overview regression cases passed\n`);
