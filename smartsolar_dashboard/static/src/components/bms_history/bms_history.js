/** @odoo-module **/
import { Component, useState, useRef, useEffect, onWillUnmount } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";

export class BmsHistory extends Component {
    static template = "smartsolar.BmsHistory";
    static props = ["systemId", "theme", "refreshToken"];
    setup() {
        this.state = useState({ range: "24h", history: [], loading: true, loaded: false, error: false });
        this.ranges = [{key:"6h",label:"6 giờ"},{key:"12h",label:"12 giờ"},{key:"24h",label:"24 giờ"},{key:"1week",label:"7 ngày"},{key:"1month",label:"30 ngày"}];
        this.canvas = useRef("chart");
        this.requestId = 0;
        useEffect(() => { this.load(); }, () => [this.props.systemId, this.props.refreshToken, this.state.range]);
        useEffect(() => { this.renderChart(); }, () => [this.state.history, this.props.theme]);
        onWillUnmount(() => { this.requestId++; this.destroyed = true; this.chart?.destroy(); });
    }
    get hasHistory() { return this.state.history.some(h => h.labels.length); }
    get historyDisabled() { return this.state.history.some(h => h.history_enabled === false); }
    get truncated() { return this.state.history.some(h => h.truncated); }
    changeRange(key) { this.state.range = key; }
    async load() {
        const id = ++this.requestId;
        this.state.loading = true;
        this.state.error = false;
        try {
            const history = await rpc("/smartsolar/dashboard/bms_history", { time_range: this.state.range, system_id: this.props.systemId });
            if (this.destroyed || id !== this.requestId) return;
            if (JSON.stringify(history) !== JSON.stringify(this.state.history)) this.state.history = history;
        } catch {
            if (this.destroyed || id !== this.requestId) return;
            this.state.history = [];
            this.state.error = true;
        } finally {
            if (!this.destroyed && id === this.requestId) {
                this.state.loading = false;
                this.state.loaded = true;
            }
        }
    }
    renderChart() {
        if (!this.canvas.el || typeof Chart === "undefined") return;
        const history = this.state.history;
        const times = new Set(history.flatMap(h => h.labels));
        for (const h of history) {
            const gap = Math.max(600, (h.sample_step_seconds || 300) * 2) * 1000;
            for (let i = 1; i < h.labels.length; i++) {
                const before = Date.parse(h.labels[i - 1]), after = Date.parse(h.labels[i]);
                if (after - before > gap) times.add(new Date((before + after) / 2).toISOString());
            }
        }
        const labels = [...times].sort();
        const colors = ["#22C55E", "#1E88E5", "#8B5CF6", "#F59E0B"];
        const datasets = history.flatMap((h, i) => ["soc", "voltage"].map(metric => {
            const values = new Map(h.labels.map((t,j) => [t,h[metric][j]]));
            return { label: `${h.name} · ${metric === "soc" ? "SOC (%)" : "Điện áp (V)"}`,
                data: labels.map(t => ({ x: Date.parse(t), y: values.get(t) ?? null })), yAxisID: metric === "soc" ? "soc" : "voltage",
                borderColor: colors[(i * 2 + (metric === "voltage" ? 1 : 0)) % colors.length],
                borderDash: metric === "voltage" ? [5,3] : [], borderWidth: 2, pointRadius: labels.length === 1 ? 3 : 0,
                pointHitRadius: 12, tension: 0, spanGaps: false };
        }));
        const text = this.props.theme === "dark" ? "#cbd5e1" : "#475569";
        const options = { responsive: true, maintainAspectRatio: false, animation: false,
            interaction: { mode: "index", intersect: false },
            plugins: { legend: { position: "bottom", labels: { color: text, boxWidth: 14, font: {size:11} } },
                tooltip: { callbacks: { title: items => items.length ? new Date(items[0].parsed.x).toLocaleString("vi-VN") : "", label: c => `${c.dataset.label}: ${c.parsed.y.toFixed(c.dataset.yAxisID === "soc" ? 1 : 2)}` } } },
            scales: { x: { type: "linear", ticks: { color: text, maxTicksLimit: 6, maxRotation: 0, callback: value => new Date(value).toLocaleString("vi-VN", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}) }, grid: { display: false } },
                soc: { position: "left", min: 0, max: 100, title: { display: true, text: "SOC (%)", color: text }, ticks: {color:text} },
                voltage: { position: "right", title: { display: true, text: "Điện áp (V)", color: text }, ticks: {color:text}, grid: {drawOnChartArea:false} } } };
        const data = { labels: labels.map(t => new Date(t).toLocaleString("vi-VN", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})), datasets };
        if (this.chart) { this.chart.data = data; this.chart.options = options; this.chart.update("none"); }
        else this.chart = new Chart(this.canvas.el, {type:"line",data,options});
    }
}
