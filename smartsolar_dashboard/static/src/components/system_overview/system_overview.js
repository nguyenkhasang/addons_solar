/** @odoo-module **/

import { Component, onMounted, useState } from "@odoo/owl";

export class SystemOverview extends Component {
    static template = "smartsolar.SystemOverview";
    static FLOW_THRESHOLD_W = 5;
    static props = {
        kpi: { type: Object, optional: true },
        devices: { type: Array, optional: true },
        theme: { type: String, optional: true },
        onUpdate: { type: Function, optional: true },
    };

    setup() {
        this.state = useState({
            solar_w: 0,
            solar_v: 0,
            solar_a: 0,
            grid_w: 0,
            bat_w: null,
            bat_flow_w: null,
            bat_v: 0,
            bat_a: null,
            charger_a: null,
            charger_w: null,
            inverter_at: "",
            charger_at: "",
            inverter_seen: false,
            charger_seen: false,
            home_w: 0,
            acout_w: 0,
        });
        onMounted(() => {
            this._syncFromProps();
            if (this.props.onUpdate) {
                this.props.onUpdate(this);
            }
        });
    }

    _syncFromProps() {
        const kpi = this.props.kpi || {};
        this.state.acout_w = Number(kpi.current_power_kw || 0) * 1000;
        this.state.home_w = this.state.acout_w;
    }

    updateFromRealtime(msg) {
        if (msg.device_type === "grid_tie_inverter") {
            this.state.inverter_seen = true;
            this.state.inverter_at = msg.label || "";
            this.state.acout_w = msg.output_power || 0;
            this.state.grid_w = msg.limiter_power || 0;
            this.state.home_w = (msg.output_power || 0) + (msg.limiter_power || 0);
            this._updateBatteryFlow();
        }
        if (msg.device_type === "charge_power") {
            this.state.charger_seen = true;
            this.state.charger_at = msg.label || "";
            // charge_power is PV input (Vpv * Ipv), not MPPT DC output.
            this.state.solar_w = msg.pv_input_power ?? msg.charge_power ?? 0;
            this.state.solar_v = msg.pv_voltage || 0;
            this.state.solar_a = msg.pv_current || 0;
            this.state.bat_v = msg.bat_voltage || 0;
            const current = msg.bat_current;
            this.state.charger_a = current == null || !Number.isFinite(Number(current))
                ? null : Number(current);
            this._updateBatteryFlow();
        }
    }

    static estimateBatteryFlow(snapshot) {
        const voltage = Number(snapshot.bat_v);
        const current = snapshot.charger_a;
        const gross = voltage > 0 && current != null && Number.isFinite(Number(current))
            ? voltage * Number(current) : null;
        const parse = label => Date.parse((label || "").replace(" ", "T") + "+07:00");
        const chargerAt = parse(snapshot.charger_at), inverterAt = parse(snapshot.inverter_at);
        const aligned = !Number.isFinite(chargerAt) || !Number.isFinite(inverterAt)
            || Math.abs(chargerAt - inverterAt) <= 10000;
        const supported = snapshot.scope_supported !== false;
        const available = snapshot.charger_seen && snapshot.inverter_seen && aligned && supported;
        const net = available ? (gross ?? Number(snapshot.solar_w || 0)) - Number(snapshot.acout_w || 0) : null;
        return {gross, net, current: net !== null && voltage > 0 ? net / voltage : null, aligned, supported};
    }

    _updateBatteryFlow() {
        const counts = type => (this.props.devices || []).filter(device => device.type === type).length;
        const snapshot = {...this.state, scope_supported: counts('charge_power') <= 1 && counts('grid_tie_inverter') <= 1};
        const flow = SystemOverview.estimateBatteryFlow(snapshot);
        this.state.charger_w = flow.gross;
        this.state.bat_flow_w = flow.net;
        this.state.bat_w = flow.net === null ? null : Math.abs(flow.net);
        this.state.bat_a = flow.current;
    }

    get batteryLabel() {
        if (this.state.bat_flow_w === null) return "Chờ dữ liệu pin";
        return this.batReverse ? "Xả ròng · ước tính" : "Sạc ròng · ước tính";
    }
    get batteryNote() {
        if (this.state.bat_flow_w === null) return "Cần dữ liệu của cả MPPT và inverter.";
        return this.state.charger_w === null
            ? "Ước tính từ PV trừ AC OUT; chưa trừ tổn hao bộ sạc và inverter."
            : "Ước tính từ điện áp × dòng MPPT do thiết bị gửi, trừ AC OUT. Chưa đối chiếu BMS hoặc xác minh độ chính xác dòng MPPT; chưa tính tổn hao inverter.";
    }
    get sampleTimesNote() {
        const times = [];
        if (this.state.charger_at) times.push(`MPPT: ${this.state.charger_at}`);
        if (this.state.inverter_at) times.push(`Inverter: ${this.state.inverter_at}`);
        return times.join(" · ");
    }
    get samplesMisaligned() {
        // Bus labels are emitted in UTC+7; do not parse them in browser local time.
        const parse = label => Date.parse(label.replace(" ", "T") + "+07:00");
        const charger = parse(this.state.charger_at);
        const inverter = parse(this.state.inverter_at);
        return Number.isFinite(charger) && Number.isFinite(inverter)
            && Math.abs(charger - inverter) > 10000;
    }

    get controllerDifferenceW() {
        return this.state.charger_w === null ? null : this.state.solar_w - this.state.charger_w;
    }

    fmtW(v) {
        if (v == null) return "—";
        return Math.round(v || 0).toLocaleString("vi-VN");
    }
    fmtV(v) {
        if (v == null) return "—";
        return (v || 0).toFixed(1);
    }
    fmtA(v) {
        if (v == null) return "—";
        return (v || 0).toFixed(1);
    }
    isPowerActive(v) {
        return Math.abs(v || 0) >= SystemOverview.FLOW_THRESHOLD_W;
    }
    flowStyle(power) {
        const watts = Math.abs(power || 0);
        const ratio = Math.min(watts / 3000, 1);
        const duration = 1.55 - ratio * 0.9;
        const opacity = 0.35 + ratio * 0.65;
        const glow = 6 + ratio * 18;
        const particleSize = (4 + ratio * 3) * 2;
        const particleGap = 14 - ratio * 4;
        const particleGap2 = particleGap * 2;
        return [
            `--ov-flow-duration: ${duration.toFixed(2)}s`,
            `--ov-flow-delay: -${(duration / 2).toFixed(2)}s`,
            `--ov-flow-opacity: ${opacity.toFixed(2)}`,
            `--ov-flow-glow: ${glow.toFixed(0)}px`,
            `--ov-particle-size: ${particleSize.toFixed(1)}px`,
            `--ov-particle-gap: ${particleGap.toFixed(1)}px`,
            `--ov-particle-gap-neg: -${particleGap.toFixed(1)}px`,
            `--ov-particle-gap-2: ${particleGap2.toFixed(1)}px`,
            `--ov-particle-gap-2-neg: -${particleGap2.toFixed(1)}px`,
            `--ov-particle-tail-1: ${(particleSize * -0.22).toFixed(1)}px`,
            `--ov-particle-tail-2: ${(particleSize * -0.38).toFixed(1)}px`,
        ].join("; ");
    }

    get solarActive() { return this.isPowerActive(this.state.solar_w); }
    get gridActive() { return this.isPowerActive(this.state.grid_w); }
    get batActive() { return this.isPowerActive(this.state.bat_w); }
    get homeActive() { return this.isPowerActive(this.state.home_w); }
    get gridReverse() { return (this.state.grid_w || 0) < 0; }
    get batReverse() { return (this.state.bat_flow_w || 0) < 0; }
    get inverterSource() {
        const flows = [
            { source: "solar", watts: Math.abs(this.state.solar_w || 0) },
            { source: "grid", watts: Math.abs(this.state.grid_w || 0) },
            { source: "battery", watts: Math.max(0, -(this.state.bat_flow_w || 0)) },
        ].filter((flow) => flow.watts >= SystemOverview.FLOW_THRESHOLD_W);
        if (!flows.length) return "";
        flows.sort((a, b) => b.watts - a.watts);
        return flows[0].source;
    }
    get inverterActive() {
        return this.solarActive || this.gridActive || this.batActive || this.homeActive;
    }
}
