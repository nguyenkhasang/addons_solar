/** @odoo-module **/
import { Component, useState, useRef, useEffect, onWillUnmount } from "@odoo/owl";

export class BmsPanel extends Component {
    static template = "smartsolar.BmsPanel";
    static props = ["batteries", "history", "theme"];
    setup() {
        this.state = useState({ now: Date.now(), metric: "soc" });
        this.canvas = useRef("history");
        this.metrics = [
            ["soc", "SOC (%)"], ["voltage", "Điện áp (V)"], ["current", "Dòng điện (A)"],
            ["power", "Công suất (W)"], ["cell_delta_voltage", "Chênh cell (mV)"],
            ["battery_temperature_1", "Nhiệt độ pin (°C)"],
        ];
        const timer = setInterval(() => { this.state.now = Date.now(); }, 1000);
        useEffect(() => {
            this.renderChart();
            return () => { this.chart?.destroy(); this.chart = null; };
        }, () => [this.props.history, this.state.metric, this.props.theme]);
        onWillUnmount(() => { clearInterval(timer); });
    }
    get cards() {
        return this.props.batteries.map(b => {
            const age = b.timestamp ? Math.max(0, (this.state.now - Date.parse(b.timestamp)) / 1000) : Infinity;
            return { ...b, status: age > b.offline_seconds ? "offline" : age >= b.stale_seconds ? "stale" : "online",
                age: Number.isFinite(age) ? Math.floor(age) + " giây trước" : "Chưa có dữ liệu" };
        });
    }
    fmt(value, digits = 2) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "—" : Number(value).toFixed(digits); }
    cellLabel(index) { return String(index + 1).padStart(2, "0"); }
    timestampLabel(timestamp) { return timestamp ? new Date(timestamp).toLocaleString("vi-VN") : "—"; }
    changeMetric(ev) { this.state.metric = ev.target.value; }
    renderChart() {
        if (!this.canvas.el || typeof Chart === "undefined") return;
        const history = this.props.history || [];
        const labels = [...new Set(history.flatMap(h => h.labels))].sort();
        const colors = ["#1E88E5", "#22C55E", "#8B5CF6"];
        const metric = this.state.metric;
        const text = this.props.theme === "dark" ? "#cbd5e1" : "#475569";
        this.chart = new Chart(this.canvas.el, {
            type: "line", data: { labels: labels.map(t => new Date(t).toLocaleString("vi-VN")),
                datasets: history.map((h, i) => {
                    const values = new Map(h.labels.map((t, j) => [t, h[metric][j]]));
                    return { label: h.name, data: labels.map(t => {
                        const v = values.get(t);
                        return v === null || v === undefined ? null : v * (metric === "cell_delta_voltage" ? 1000 : 1);
                    }), borderColor: colors[i % colors.length], pointRadius: 0, spanGaps: false };
                }) },
            options: { responsive: true, maintainAspectRatio: false, animation: false,
                plugins: { legend: { labels: { color: text } } },
                scales: { x: { ticks: { color: text, maxTicksLimit: 8 } }, y: { ticks: { color: text } } } },
        });
    }
}
