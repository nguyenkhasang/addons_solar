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
    get lastReadings() {
        return this.state.history.filter(h => h.labels.length).map(h => {
            const index = h.labels.length - 1;
            const soc = h.soc?.[index], power = h.power?.[index];
            return { id: h.id, name: h.name,
                soc: soc == null ? '—' : `${soc.toFixed(1)}%`,
                power: power == null ? '—' : `${power > 0 ? '+' : ''}${power.toFixed(0)} W`,
                phase: power == null ? 'unknown' : power > 0 ? 'charge' : power < 0 ? 'discharge' : 'idle',
                phaseLabel: power == null ? 'Thiếu dữ liệu' : power > 0 ? 'Sạc' : power < 0 ? 'Xả' : 'Chờ',
                observedAt: new Date(h.labels[index]).toLocaleString('vi-VN', {
                    hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }),
            };
        });
    }
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
        const dark = this.props.theme === "dark";
        const text = dark ? "#94a3b8" : "#64748b";
        const colors = ["#34d399", "#a78bfa", "#facc15", "#f472b6"];
        const charge = dark ? "#38bdf8" : "#0284c7";
        const discharge = dark ? "#fb923c" : "#ea580c";
        const single = history.filter(h => h.labels.length).length === 1;
        const datasets = history.flatMap((h, i) => ["soc", "power"].map(metric => {
            const values = new Map(h.labels.map((t,j) => [t,h[metric]?.[j]]));
            const soc = metric === "soc";
            const color = colors[i % colors.length];
            return { label: `${single ? '' : `${h.name} · `}${soc ? 'SOC' : 'Sạc (+) / Xả (−)'}`, batteryName: h.name,
                data: labels.map(t => ({ x: Date.parse(t), y: values.get(t) ?? null })),
                yAxisID: soc ? "soc" : "power", borderColor: soc ? color : charge,
                backgroundColor: soc ? `${color}18` : charge,
                fill: soc ? 'origin' : { target: 'origin', above: dark ? '#38bdf81a' : '#0284c714', below: dark ? '#fb923c1a' : '#ea580c14' },
                segment: soc ? undefined : { borderColor: ctx =>
                    (ctx.p0.parsed.y + ctx.p1.parsed.y) / 2 < 0 ? discharge : charge },
                borderWidth: soc ? 2.8 : 1.8, pointRadius: h.labels.length === 1 ? 3 : 0,
                borderDash: !soc && !single ? [[], [6, 3], [2, 3], [8, 3, 2, 3]][i % 4] : [],
                pointHoverRadius: 4, pointHitRadius: 14, pointHoverBorderWidth: 2,
                tension: 0, spanGaps: false, order: soc ? 0 : 1 };
        }));
        const timestamps = labels.map(t => Date.parse(t));
        const powerMax = Math.max(50, ...history.flatMap(h => h.power || []).filter(v => v != null).map(Math.abs));
        const powerStep = 10 ** Math.floor(Math.log10(powerMax)) / 2;
        const powerExtent = Math.ceil(powerMax * 1.1 / powerStep) * powerStep;
        const options = { responsive: true, maintainAspectRatio: false, animation: false,
            layout: { padding: { top: 8, right: 4 } },
            interaction: { mode: "index", intersect: false },
            plugins: { legend: { position: "bottom", labels: {
                color: text, usePointStyle: true, pointStyle: 'line', boxWidth: 24,
                padding: 20, font: {size: 12} } },
                tooltip: { backgroundColor: dark ? '#0f172a' : '#ffffff',
                    titleColor: dark ? '#f1f5f9' : '#0f172a', bodyColor: dark ? '#cbd5e1' : '#475569',
                    borderColor: dark ? '#334155' : '#e2e8f0', borderWidth: 1,
                    padding: 12, cornerRadius: 10, caretPadding: 8,
                    callbacks: {
                        title: items => items.length ? new Date(items[0].parsed.x).toLocaleString("vi-VN") : "",
                        label: c => {
                            if (c.parsed.y == null) return `${c.dataset.label}: —`;
                            const soc = c.dataset.yAxisID === 'soc';
                            const phase = c.parsed.y > 0 ? 'Sạc' : c.parsed.y < 0 ? 'Xả' : 'Chờ';
                            return `${soc ? c.dataset.label : `${single ? '' : `${c.dataset.batteryName} · `}${phase}`}: ${c.parsed.y.toFixed(1)} ${soc ? '%' : 'W'}`;
                        },
                        labelColor: c => ({ borderColor: c.dataset.yAxisID === 'soc' ? c.dataset.borderColor : c.parsed.y < 0 ? discharge : charge,
                            backgroundColor: c.dataset.yAxisID === 'soc' ? c.dataset.borderColor : c.parsed.y < 0 ? discharge : charge }),
                    } } },
            scales: {
                x: { type: "linear", ...(timestamps.length ? {
                    min: timestamps[0] - (timestamps.length === 1 ? 300000 : 0),
                    max: timestamps[timestamps.length - 1] + (timestamps.length === 1 ? 300000 : 0),
                } : {}),
                    ticks: { color: text, maxTicksLimit: 7, maxRotation: 0, padding: 10,
                        callback: value => [new Date(value).toLocaleTimeString('vi-VN', {hour:'2-digit',minute:'2-digit'}),
                            new Date(value).toLocaleDateString('vi-VN', {day:'2-digit',month:'2-digit'})] },
                    border: { display: false }, grid: { display: false } },
                soc: { position: "left", min: 0, max: 100,
                    title: { display: false, text: "SOC (%)", color: text },
                    ticks: { color: text, stepSize: 20, padding: 10, callback: value => `${value}%` },
                    border: { display: false }, grid: { color: dark ? '#94a3b810' : '#64748b12', drawTicks: false } },
                power: { position: "right", min: -powerExtent, max: powerExtent, beginAtZero: true,
                    title: { display: false, text: "Công suất sạc/xả (W)", color: text },
                    ticks: { color: text, maxTicksLimit: 5, padding: 10,
                        callback: value => `${Number(value).toLocaleString('vi-VN')} W` },
                    border: { display: false }, grid: { drawTicks: false,
                        color: ctx => ctx.tick.value === 0 ? (dark ? '#94a3b840' : '#64748b40') : 'transparent' } },
            } };
        const data = { labels: labels.map(t => new Date(t).toLocaleString("vi-VN", {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"})), datasets };
        if (this.chart) { this.chart.data = data; this.chart.options = options; this.chart.update("none"); }
        else this.chart = new Chart(this.canvas.el, {type:"line",data,options});
    }
}
