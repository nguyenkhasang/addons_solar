/** @odoo-module **/

import { Component, onMounted, useState } from "@odoo/owl";

export class SystemOverview extends Component {
    static template = "smartsolar.SystemOverview";
    static FLOW_THRESHOLD_W = 10;
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
            bat_w: 0,
            bat_flow_w: 0,
            bat_v: 0,
            bat_a: 0,
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
        this.state.grid_w = Number(kpi.current_power_kw || 0) * 1000;
        this.state.home_w = this.state.grid_w;
        this.state.acout_w = this.state.grid_w;
    }

    updateFromRealtime(msg) {
        if (msg.device_type === "grid_tie_inverter") {
            this.state.acout_w = msg.output_power || 0;
            this.state.grid_w = msg.limiter_power || 0;
            this.state.home_w = (msg.output_power || 0) + (msg.limiter_power || 0);
        }
        if (msg.device_type === "charge_power") {
            this.state.solar_w = msg.charge_power || 0;
            this.state.solar_v = msg.pv_voltage || 0;
            this.state.solar_a = msg.pv_current || 0;
            this.state.bat_v = msg.bat_voltage || 0;
            this.state.bat_a = msg.bat_current || 0;
            this.state.bat_flow_w = (msg.bat_voltage || 0) * (msg.bat_current || 0);
            this.state.bat_w = Math.abs(this.state.bat_flow_w);
        }
    }

    fmtW(v) {
        return Math.round(v || 0).toLocaleString("vi-VN");
    }
    fmtV(v) {
        return (v || 0).toFixed(1);
    }
    fmtA(v) {
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
            { source: "battery", watts: Math.abs(this.state.bat_w || 0) },
        ].filter((flow) => flow.watts >= SystemOverview.FLOW_THRESHOLD_W);
        if (!flows.length) return "";
        flows.sort((a, b) => b.watts - a.watts);
        return flows[0].source;
    }
    get inverterActive() {
        return this.solarActive || this.gridActive || this.batActive || this.homeActive;
    }
}
