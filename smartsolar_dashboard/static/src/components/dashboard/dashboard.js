/** @odoo-module **/

import { Component, useState, useRef, onWillStart, onMounted, onWillUnmount } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { rpc } from "@web/core/network/rpc";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { SystemOverview } from "../system_overview/system_overview";

const TIME_RANGES = [
    { key: "realtime", label: "⬤ Live" },
    { key: "1h", label: "1H" },
    { key: "6h", label: "6H" },
    { key: "12h", label: "12H" },
    { key: "24h", label: "24H" },
    { key: "1week", label: "1 Tuần" },
    { key: "1month", label: "1 Tháng" },
    { key: "3month", label: "3 Tháng" },
    { key: "6month", label: "6 Tháng" },
    { key: "1year", label: "1 Năm" },
    { key: "5year", label: "5 Năm" },
];

const COLORS = {
    primary: "#FFB800",
    accent: "#1E88E5",
    success: "#22C55E",
    danger: "#EF4444",
    warning: "#F59E0B",
    info: "#06B6D4",
    purple: "#8B5CF6",
    pink: "#EC4899",
    grid: "rgba(148, 163, 184, 0.18)",
};

export class SmartSolarDashboard extends Component {
    static template = "smartsolar.Dashboard";
    static props = ["*"];
    static components = { SystemOverview };

    setup() {
        this.notification = useService("notification");
        this.action = useService("action");
        this.busService = useService("bus_service");

        this.state = useState({
            loading: true,
            timeRange: "24h",
            systemId: null,
            theme: localStorage.getItem("smartsolar_theme") || "light",
            data: null,
            lastRefresh: null,
            autoRefresh: true,
            realtimeActive: false,
        });

        this.timeRanges = TIME_RANGES;
        this.charts = {};
        this.refreshTimer = null;
        this._realtimeChannel = null;
        this._realtimeCallback = null;
        this._overviewComponent = null;
        this._rt = {};
        // Buffer realtime chạy nền — luôn tích lũy dù không ở chế độ Live
        this._rtBuffer = {
            labels: [],
            gt_output: [], gt_limiter: [], gt_pv: [], gt_charge: [],
            cp_charge: [], cp_pv_v: [], cp_bat_v: [], cp_pv_input: [],
            bat_v: [], bat_v_min: [], bat_a: [],
            eff_pv_in: [], eff_charge: [], eff_pct: [],
            temp_map: {}, // { device_guid: { label, data[] } }
        };
        this._RT_MAX = 120;

        this.refs = {
            chargePower: useRef("chargePowerChart"),
            gridTie: useRef("gridTieChart"),
            energy: useRef("energyChart"),
            temperature: useRef("temperatureChart"),
            battery: useRef("batteryChart"),
            pvEfficiency: useRef("pvEfficiencyChart"),
            distribution: useRef("distributionChart"),
            heatmap: useRef("heatmapChart"),
            monthlyComparison: useRef("monthlyComparisonChart"),
            energyFlow: useRef("energyFlowChart"),
        };

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this._loadData();
        });

        onMounted(() => {
            this._renderAllCharts();
            this._startAutoRefresh();
            this._applyTheme();
            this._startRealtime();
        });

        onWillUnmount(() => {
            this._stopAutoRefresh();
            this._stopRealtime();
            this._destroyCharts();
        });
    }

    _onOverviewUpdate(component) {
        this._overviewComponent = component;
    }

    // ------------------------------------------------------------------
    // Data loading
    // ------------------------------------------------------------------
    async _loadData() {
        this.state.loading = true;
        try {
            const data = await rpc("/smartsolar/dashboard/data", {
                time_range: this.state.timeRange,
                system_id: this.state.systemId,
            });
            if (data && data.error) {
                this.notification.add(data.message || _t("Lỗi tải dữ liệu"), { type: "danger" });
                return;
            }
            this.state.data = data;
            this.state.lastRefresh = new Date();
        } catch (err) {
            this.notification.add(_t("Không thể tải dữ liệu dashboard."), { type: "danger" });
            console.error(err);
        } finally {
            this.state.loading = false;
        }
    }

    async _refresh() {
        await this._loadData();
        this._renderAllCharts();
    }

    // ------------------------------------------------------------------
    // Auto refresh
    // ------------------------------------------------------------------
    _startAutoRefresh() {
        this._stopAutoRefresh();
        if (this.state.timeRange === "realtime") return;
        const interval = (this.state.data?.kpi?.refresh_interval || 60) * 1000;
        if (!this.state.autoRefresh || interval <= 0) return;
        this.refreshTimer = setInterval(() => this._refresh(), interval);
    }

    _stopAutoRefresh() {
        if (this.refreshTimer) {
            clearInterval(this.refreshTimer);
            this.refreshTimer = null;
        }
    }

    toggleAutoRefresh() {
        this.state.autoRefresh = !this.state.autoRefresh;
        if (this.state.autoRefresh) {
            this._startAutoRefresh();
        } else {
            this._stopAutoRefresh();
        }
    }

    // ------------------------------------------------------------------
    // Theme
    // ------------------------------------------------------------------
    toggleTheme() {
        this.state.theme = this.state.theme === "light" ? "dark" : "light";
        localStorage.setItem("smartsolar_theme", this.state.theme);
        this._applyTheme();
        this._renderAllCharts();
    }

    _applyTheme() {
        const root = document.querySelector(".o_smartsolar_dashboard");
        if (root) {
            root.classList.toggle("dark", this.state.theme === "dark");
        }
    }

    // ------------------------------------------------------------------
    // Filters
    // ------------------------------------------------------------------
    async onTimeRangeChange(key) {
        if (this.state.timeRange === key) return;
        this.state.timeRange = key;
        if (key === "realtime") {
            this._stopAutoRefresh();
            // Nếu buffer đã có data → render từ buffer ngay, không cần load DB
            if (this._rtBuffer.labels.length > 0) {
                this._renderRealtimeFromBuffer();
            } else {
                // Lần đầu vào Live → load 2 phút gần nhất từ DB
                const data = await rpc("/smartsolar/dashboard/data", {
                    time_range: "2min",
                    system_id: this.state.systemId,
                });
                if (data) {
                    this.state.data = data;
                    this.state.lastRefresh = new Date();
                    this._renderAllCharts();
                }
            }
        } else {
            await this._refresh();
            this._startAutoRefresh();
        }
    }

    async onSystemChange(ev) {
        const val = ev.target.value;
        this.state.systemId = val ? parseInt(val, 10) : null;
        await this._refresh();
    }

    onManualRefresh() {
        if (this.state.timeRange === "realtime") return;
        this._refresh();
    }

    // ------------------------------------------------------------------
    // Realtime (bus subscription — always active)
    // ------------------------------------------------------------------
    async _startRealtime() {
        this.state.realtimeActive = true;

        const channel = this.state.systemId
            ? `smartsolar.realtime.${this.state.systemId}`
            : "smartsolar.realtime.all";

        this._realtimeChannel = channel;
        this._realtimeCallback = (payload) => {
            if (this._overviewComponent) {
                this._overviewComponent.updateFromRealtime(payload);
            }
            if (this.state.timeRange === "realtime") {
                this._appendRealtimePoint(payload);
                this.state.lastRefresh = new Date();
            }
        };
        await this.busService.addChannel(channel);
        this.busService.subscribe("smartsolar_data", this._realtimeCallback);
    }

    _stopRealtime() {
        this.state.realtimeActive = false;
        if (this._realtimeCallback) {
            this.busService.unsubscribe("smartsolar_data", this._realtimeCallback);
            this._realtimeCallback = null;
        }
        if (this._realtimeChannel) {
            this.busService.deleteChannel(this._realtimeChannel);
            this._realtimeChannel = null;
        }
    }

    _appendRealtimePoint(msg) {
        const MAX = this._RT_MAX;
        const label = msg.label || "";
        const buf = this._rtBuffer;

        const _push = (arr, val) => { arr.push(val); if (arr.length > MAX) arr.shift(); };

        // Cập nhật realtime cache cho Live Status
        if (msg.device_type === "grid_tie_inverter") {
            this._rt.grid_out_w = msg.output_power || 0;
            this._rt.grid_in_w = msg.limiter_power || 0;
        }
        if (msg.device_type === "charge_power") {
            this._rt.pv_w = msg.pv_input_power || 0;
            this._rt.charge_w = msg.charge_power || 0;
            this._rt.bat_v = msg.bat_voltage || 0;
            this._rt.bat_a = msg.bat_current || 0;
        }

        // Tích lũy vào buffer nền — luôn chạy bất kể time range
        _push(buf.labels, label);

        if (msg.device_type === "grid_tie_inverter") {
            _push(buf.gt_output, msg.output_power || 0);
            _push(buf.gt_limiter, msg.limiter_power || 0);
            _push(buf.gt_pv, this._rt.pv_w || 0);
            _push(buf.gt_charge, this._rt.charge_w || 0);
        }
        if (msg.device_type === "charge_power") {
            _push(buf.cp_charge, msg.charge_power || 0);
            _push(buf.cp_pv_v, msg.pv_voltage || 0);
            _push(buf.cp_bat_v, msg.bat_voltage || 0);
            _push(buf.cp_pv_input, msg.pv_input_power || 0);
            _push(buf.bat_v, msg.bat_voltage || 0);
            _push(buf.bat_v_min, msg.bat_voltage || 0);
            _push(buf.bat_a, msg.bat_current || 0);
            const eff = (msg.pv_input_power || 0) > 0
                ? Math.min((msg.charge_power || 0) / msg.pv_input_power * 100, 100) : 0;
            _push(buf.eff_pv_in, msg.pv_input_power || 0);
            _push(buf.eff_charge, msg.charge_power || 0);
            _push(buf.eff_pct, Math.round(eff * 10) / 10);
        }
        // Temperature buffer theo device
        const guid = msg.device_guid || "unknown";
        if (!buf.temp_map[guid]) buf.temp_map[guid] = { label: guid, data: [] };
        _push(buf.temp_map[guid].data, msg.temperature || 0);

        // Nếu đang ở chế độ Live → cập nhật chart trực tiếp
        if (this.state.timeRange === "realtime") {
            this._appendToCharts(msg, label);
            this.state.lastRefresh = new Date();
        }

        // Cập nhật SystemOverview luôn
        if (this._overviewComponent) {
            this._overviewComponent.updateFromRealtime(msg);
        }
    }

    _appendToCharts(msg, label) {
        const MAX = this._RT_MAX;
        const _addLabel = (chart) => {
            if (!chart) return;
            chart.data.labels.push(this._fmtLabel(label));
            if (chart.data.labels.length > MAX) chart.data.labels.shift();
        };
        const _addDataset = (chart, idx, val) => {
            if (!chart?.data?.datasets?.[idx]) return;
            chart.data.datasets[idx].data.push(val);
            if (chart.data.datasets[idx].data.length > MAX) chart.data.datasets[idx].data.shift();
        };

        if (msg.device_type === "grid_tie_inverter") {
            _addLabel(this.charts.gridTie);
            _addDataset(this.charts.gridTie, 0, msg.output_power);
            _addDataset(this.charts.gridTie, 1, msg.limiter_power);
            _addDataset(this.charts.gridTie, 2, this._rt.pv_w || 0);
            _addDataset(this.charts.gridTie, 3, this._rt.charge_w || 0);
            this.charts.gridTie?.update("quiet");
        }
        if (msg.device_type === "charge_power") {
            this._rt.pv_w = msg.pv_input_power || 0;
            this._rt.charge_w = msg.charge_power || 0;
            _addDataset(this.charts.gridTie, 2, this._rt.pv_w);
            _addDataset(this.charts.gridTie, 3, this._rt.charge_w);
            this.charts.gridTie?.update("quiet");

            _addLabel(this.charts.chargePower);
            _addDataset(this.charts.chargePower, 0, msg.charge_power);
            _addDataset(this.charts.chargePower, 1, msg.pv_voltage);
            _addDataset(this.charts.chargePower, 2, msg.bat_voltage);
            this.charts.chargePower?.update("quiet");

            _addLabel(this.charts.battery);
            _addDataset(this.charts.battery, 0, msg.bat_voltage);
            _addDataset(this.charts.battery, 1, msg.bat_voltage);
            _addDataset(this.charts.battery, 2, msg.bat_current);
            this.charts.battery?.update("quiet");

            const eff = (msg.pv_input_power || 0) > 0
                ? Math.min(msg.charge_power / msg.pv_input_power * 100, 100) : 0;
            _addLabel(this.charts.pvEfficiency);
            _addDataset(this.charts.pvEfficiency, 0, msg.pv_input_power);
            _addDataset(this.charts.pvEfficiency, 1, msg.charge_power);
            _addDataset(this.charts.pvEfficiency, 2, Math.round(eff * 10) / 10);
            this.charts.pvEfficiency?.update("quiet");
        }
        if (this.charts.temperature) {
            const tChart = this.charts.temperature;
            tChart.data.labels.push(this._fmtLabel(label));
            if (tChart.data.labels.length > MAX) tChart.data.labels.shift();
            const ds = tChart.data.datasets.find((d) => d.label === (msg.device_guid || ""));
            if (ds) { ds.data.push(msg.temperature); if (ds.data.length > MAX) ds.data.shift(); }
            tChart.update("quiet");
        }
    }

    // ------------------------------------------------------------------
    // Chart rendering
    // ------------------------------------------------------------------
    _destroyCharts() {
        Object.values(this.charts).forEach((c) => c && c.destroy && c.destroy());
        this.charts = {};
    }

    _renderAllCharts() {
        if (!this.state.data) return;
        this._destroyCharts();
        this._renderChargePowerChart();
        this._renderGridTieChart();
        this._renderEnergyChart();
        this._renderTemperatureChart();
        this._renderBatteryChart();
        this._renderPvEfficiencyChart();
        this._renderDistributionChart();
        this._renderHeatmapChart();
        this._renderMonthlyComparisonChart();
        this._renderEnergyFlowChart();
    }

    _renderRealtimeFromBuffer() {
        const buf = this._rtBuffer;
        const fmtLabels = buf.labels.map(this._fmtLabel.bind(this));

        // Tạo data giả cho state.data để _render* methods có thể dùng
        // Thay vì gọi _render*, inject trực tiếp vào chart sau khi render
        this._destroyCharts();

        // Inject data từ buffer vào state.data tạm để render
        const saved = this.state.data;
        this.state.data = {
            ...saved,
            charge_power: {
                ...saved.charge_power,
                labels: buf.labels,
                avg_power: buf.cp_charge,
                pv_voltage: buf.cp_pv_v,
                bat_voltage: buf.cp_bat_v,
                pv_input_power: buf.cp_pv_input,
            },
            grid_tie: {
                ...saved.grid_tie,
                labels: buf.labels,
                output_power: buf.gt_output,
                limiter_power: buf.gt_limiter,
            },
            battery: {
                ...saved.battery,
                labels: buf.labels,
                bat_voltage: buf.bat_v,
                bat_voltage_min: buf.bat_v_min,
                bat_current: buf.bat_a,
            },
            pv_efficiency: {
                ...saved.pv_efficiency,
                labels: buf.labels,
                pv_input_power: buf.eff_pv_in,
                charge_power: buf.eff_charge,
                efficiency: buf.eff_pct,
            },
            temperature: {
                labels: buf.labels,
                series: Object.values(buf.temp_map),
                source: 'realtime',
            },
        };

        this._renderChargePowerChart();
        this._renderGridTieChart();
        this._renderTemperatureChart();
        this._renderBatteryChart();
        this._renderPvEfficiencyChart();
        this._renderEnergyChart();
        this._renderDistributionChart();

        this.state.data = saved;
    }

    _commonChartOptions(extra = {}) {
        const isDark = this.state.theme === "dark";
        const tickColor = isDark ? "#cbd5e1" : "#475569";
        const isRealtime = this.state.timeRange === "realtime";
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: isRealtime ? false : { duration: 300 },
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: {
                    position: "top",
                    labels: { color: tickColor, usePointStyle: true, padding: 14, font: { size: 12 } },
                },
                tooltip: {
                    backgroundColor: isDark ? "#1e293b" : "#0f172a",
                    titleColor: "#fff",
                    bodyColor: "#fff",
                    padding: 10,
                    cornerRadius: 8,
                    displayColors: true,
                },
            },
            scales: {
                x: {
                    ticks: { color: tickColor, maxRotation: 0, autoSkip: true, maxTicksLimit: 10 },
                    grid: { color: COLORS.grid },
                },
                y: {
                    ticks: { color: tickColor },
                    grid: { color: COLORS.grid },
                },
            },
            ...extra,
        };
    }

    _renderChargePowerChart() {
        const canvas = this.refs.chargePower.el;
        if (!canvas) return;
        const cp = this.state.data.charge_power;
        if (!cp || !cp.labels.length) { this._showEmpty(canvas); return; }
        const ctx = canvas.getContext("2d");
        const gradient = ctx.createLinearGradient(0, 0, 0, 280);
        gradient.addColorStop(0, "rgba(255, 184, 0, 0.45)");
        gradient.addColorStop(1, "rgba(255, 184, 0, 0.02)");
        this.charts.chargePower = new Chart(ctx, {
            type: "line",
            data: {
                labels: cp.labels.map(this._fmtLabel.bind(this)),
                datasets: [
                    { label: "Công suất sạc (W)", data: cp.avg_power, borderColor: COLORS.primary, backgroundColor: gradient, borderWidth: 2, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y" },
                    { label: "Điện áp PV (V)", data: cp.pv_voltage, borderColor: COLORS.accent, borderWidth: 1.6, borderDash: [4, 4], fill: false, tension: 0.3, pointRadius: 0, yAxisID: "y1" },
                    { label: "Điện áp Pin (V)", data: cp.bat_voltage, borderColor: COLORS.success, borderWidth: 1.6, borderDash: [2, 4], fill: false, tension: 0.3, pointRadius: 0, yAxisID: "y1" },
                ],
            },
            options: this._commonChartOptions({
                scales: {
                    x: this._commonChartOptions().scales.x,
                    y: { ...this._commonChartOptions().scales.y, position: "left", title: { display: true, text: "Công suất (W)", color: this.state.theme === "dark" ? "#cbd5e1" : "#475569" } },
                    y1: { position: "right", ticks: { color: this.state.theme === "dark" ? "#cbd5e1" : "#475569" }, grid: { drawOnChartArea: false }, title: { display: true, text: "Điện áp (V)", color: this.state.theme === "dark" ? "#cbd5e1" : "#475569" } },
                },
            }),
        });
    }

    _renderGridTieChart() {
        const canvas = this.refs.gridTie.el;
        if (!canvas) return;
        const gt = this.state.data.grid_tie;
        if (!gt || !gt.labels.length) { this._showEmpty(canvas); return; }
        const ctx = canvas.getContext("2d");
        const gradient = ctx.createLinearGradient(0, 0, 0, 280);
        gradient.addColorStop(0, "rgba(30, 136, 229, 0.40)");
        gradient.addColorStop(1, "rgba(30, 136, 229, 0.02)");
        const cp = this.state.data.charge_power;
        this.charts.gridTie = new Chart(ctx, {
            type: "line",
            data: {
                labels: gt.labels.map(this._fmtLabel.bind(this)),
                datasets: [
                    { label: "Công suất hòa lưới (W)", data: gt.output_power, borderColor: COLORS.accent, backgroundColor: gradient, borderWidth: 2, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y" },
                    { label: "Công suất lấy lưới (W)", data: gt.limiter_power, borderColor: COLORS.purple, borderWidth: 1.6, borderDash: [4, 4], fill: false, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y" },
                    { label: "Công suất dàn PV (W)", data: cp?.pv_input_power || (cp?.pv_voltage || []).map((v, i) => Math.round((v || 0) * (cp?.pv_current?.[i] || 0) * 10) / 10), borderColor: COLORS.primary, borderWidth: 1.6, borderDash: [4, 2], fill: false, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y" },
                    { label: "Công suất sạc pin (W)", data: cp?.avg_power || [], borderColor: COLORS.success, borderWidth: 1.6, borderDash: [2, 4], fill: false, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y" },
                ],
            },
            options: this._commonChartOptions({
                scales: {
                    x: this._commonChartOptions().scales.x,
                    y: { ...this._commonChartOptions().scales.y, position: "left", title: { display: true, text: "Công suất (W)", color: this.state.theme === "dark" ? "#cbd5e1" : "#475569" } },
                },
            }),
        });
    }

    _renderEnergyChart() {
        const canvas = this.refs.energy.el;
        if (!canvas) return;
        const e = this.state.data.energy_comparison;
        if (!e || !e.labels.length) { this._showEmpty(canvas); return; }
        const ctx = canvas.getContext("2d");
        this.charts.energy = new Chart(ctx, {
            type: "bar",
            data: {
                labels: e.labels,
                datasets: [{
                    label: "Năng lượng sản xuất (kWh)",
                    data: e.energy_kwh,
                    backgroundColor: e.energy_kwh.map((_, i) => i === e.energy_kwh.length - 1 ? COLORS.primary : "rgba(255, 184, 0, 0.55)"),
                    borderRadius: 6,
                    borderSkipped: false,
                }],
            },
            options: this._commonChartOptions(),
        });
    }

    _renderTemperatureChart() {
        const canvas = this.refs.temperature.el;
        if (!canvas) return;
        const t = this.state.data.temperature;
        if (!t || !t.labels.length) { this._showEmpty(canvas); return; }
        const palette = [COLORS.danger, COLORS.warning, COLORS.success, COLORS.accent, COLORS.purple, COLORS.pink];
        const datasets = (t.series || []).map((s, i) => ({
            label: s.label, data: s.data,
            borderColor: palette[i % palette.length],
            backgroundColor: palette[i % palette.length] + "33",
            borderWidth: 1.8, fill: false, tension: 0.35, pointRadius: 0, pointHoverRadius: 4,
        }));
        const ctx = canvas.getContext("2d");
        this.charts.temperature = new Chart(ctx, {
            type: "line",
            data: { labels: t.labels.map(this._fmtLabel.bind(this)), datasets },
            options: this._commonChartOptions(),
        });
    }

    _renderBatteryChart() {
        const canvas = this.refs.battery.el;
        if (!canvas) return;
        const b = this.state.data.battery;
        if (!b || !b.labels.length) { this._showEmpty(canvas); return; }
        const ctx = canvas.getContext("2d");
        const isDark = this.state.theme === "dark";
        const tickColor = isDark ? "#cbd5e1" : "#475569";
        const gradientV = ctx.createLinearGradient(0, 0, 0, 280);
        gradientV.addColorStop(0, "rgba(34, 197, 94, 0.35)");
        gradientV.addColorStop(1, "rgba(34, 197, 94, 0.02)");
        this.charts.battery = new Chart(ctx, {
            type: "line",
            data: {
                labels: b.labels.map(this._fmtLabel.bind(this)),
                datasets: [
                    { label: "Điện áp pin TB (V)", data: b.bat_voltage, borderColor: COLORS.success, backgroundColor: gradientV, borderWidth: 2, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y" },
                    { label: "Điện áp pin Min (V)", data: b.bat_voltage_min, borderColor: COLORS.danger, borderWidth: 1.4, borderDash: [3, 4], fill: false, tension: 0.35, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y" },
                    { label: "Dòng sạc TB (A)", data: b.bat_current, borderColor: COLORS.warning, borderWidth: 1.6, borderDash: [4, 4], fill: false, tension: 0.35, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y1" },
                ],
            },
            options: this._commonChartOptions({
                scales: {
                    x: this._commonChartOptions().scales.x,
                    y: { ...this._commonChartOptions().scales.y, position: "left", title: { display: true, text: "Điện áp (V)", color: tickColor } },
                    y1: { position: "right", ticks: { color: tickColor }, grid: { drawOnChartArea: false }, title: { display: true, text: "Dòng điện (A)", color: tickColor } },
                },
            }),
        });
    }

    _renderPvEfficiencyChart() {
        const canvas = this.refs.pvEfficiency.el;
        if (!canvas) return;
        const pv = this.state.data.pv_efficiency;
        if (!pv || !pv.labels.length) { this._showEmpty(canvas); return; }
        const ctx = canvas.getContext("2d");
        const isDark = this.state.theme === "dark";
        const tickColor = isDark ? "#cbd5e1" : "#475569";
        const gradientIn = ctx.createLinearGradient(0, 0, 0, 280);
        gradientIn.addColorStop(0, "rgba(255, 184, 0, 0.40)");
        gradientIn.addColorStop(1, "rgba(255, 184, 0, 0.02)");
        const gradientOut = ctx.createLinearGradient(0, 0, 0, 280);
        gradientOut.addColorStop(0, "rgba(30, 136, 229, 0.30)");
        gradientOut.addColorStop(1, "rgba(30, 136, 229, 0.02)");
        this.charts.pvEfficiency = new Chart(ctx, {
            type: "line",
            data: {
                labels: pv.labels.map(this._fmtLabel.bind(this)),
                datasets: [
                    { label: "Công suất PV vào (W)", data: pv.pv_input_power, borderColor: COLORS.primary, backgroundColor: gradientIn, borderWidth: 2, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y" },
                    { label: "Công suất sạc ra (W)", data: pv.charge_power, borderColor: COLORS.accent, backgroundColor: gradientOut, borderWidth: 2, fill: true, tension: 0.35, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y" },
                    { label: "Hiệu suất (%)", data: pv.efficiency, borderColor: COLORS.success, borderWidth: 1.6, borderDash: [4, 4], fill: false, tension: 0.3, pointRadius: 0, pointHoverRadius: 4, yAxisID: "y1" },
                ],
            },
            options: this._commonChartOptions({
                scales: {
                    x: this._commonChartOptions().scales.x,
                    y: { ...this._commonChartOptions().scales.y, position: "left", title: { display: true, text: "Công suất (W)", color: tickColor } },
                    y1: { position: "right", min: 0, max: 100, ticks: { color: tickColor, callback: (v) => v + "%" }, grid: { drawOnChartArea: false }, title: { display: true, text: "Hiệu suất (%)", color: tickColor } },
                },
            }),
        });
    }

    _renderDistributionChart() {
        const canvas = this.refs.distribution.el;
        if (!canvas) return;
        const d = this.state.data.distribution;
        if (!d || !d.data.length || d.data.every((v) => !v)) { this._showEmpty(canvas); return; }
        const ctx = canvas.getContext("2d");
        this.charts.distribution = new Chart(ctx, {
            type: "doughnut",
            data: {
                labels: d.labels,
                datasets: [{
                    data: d.data,
                    backgroundColor: [COLORS.accent, COLORS.primary, COLORS.danger],
                    borderWidth: 0,
                    hoverOffset: 8,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "65%",
                plugins: {
                    legend: { position: "bottom", labels: { color: this.state.theme === "dark" ? "#cbd5e1" : "#475569", usePointStyle: true, padding: 14, font: { size: 11 } } },
                    tooltip: { callbacks: { label: (c) => ` ${c.label}: ${c.parsed.toFixed(1)} kWh` } },
                },
            },
        });
    }

    _renderHeatmapChart() {
        const canvas = this.refs.heatmap?.el;
        if (!canvas) return;
        const hm = this.state.data.heatmap;
        if (!hm || !hm.days.length) { this._showEmpty(canvas); return; }

        // Set canvas resolution to match CSS size (fix blurry/small rendering)
        const rect = canvas.parentElement.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        const cssW = rect.width || 800;
        const cssH = 360;
        canvas.width = Math.floor(cssW * dpr);
        canvas.height = Math.floor(cssH * dpr);
        canvas.style.width = cssW + "px";
        canvas.style.height = cssH + "px";

        const ctx = canvas.getContext("2d");
        ctx.scale(dpr, dpr);

        const isDark = this.state.theme === "dark";
        const bg = isDark ? "#131c2e" : "#f8fafc";
        const textColor = isDark ? "#94a3b8" : "#64748b";
        const labelW = 40;
        const paddingTop = 22;
        const paddingBottom = 24;
        const availH = cssH - paddingTop - paddingBottom;
        const cellW = Math.floor((cssW - labelW) / 24);
        const cellH = Math.max(10, Math.floor(availH / Math.max(hm.days.length, 1)));
        const maxVal = hm.max_val || 1;

        ctx.clearRect(0, 0, cssW, cssH);
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, cssW, cssH);

        // Hour labels
        ctx.fillStyle = textColor;
        ctx.font = "10px Inter, system-ui, sans-serif";
        ctx.textAlign = "center";
        for (let h = 0; h < 24; h += 3) {
            ctx.fillText(`${h}h`, labelW + h * cellW + cellW / 2, 14);
        }

        // Day rows
        hm.days.forEach((day, di) => {
            const y = paddingTop + di * cellH;
            ctx.fillStyle = textColor;
            ctx.textAlign = "right";
            ctx.font = "9px Inter, system-ui, sans-serif";
            ctx.fillText(day.slice(5), labelW - 4, y + cellH - 2);

            hm.hours.forEach((h, hi) => {
                const val = (hm.values[di] || [])[hi] || 0;
                const intensity = Math.min(val / maxVal, 1);
                const r = Math.round(30 + intensity * 225);
                const g = Math.round(200 - intensity * 100);
                const b = 0;
                ctx.fillStyle = val > 0
                    ? `rgba(${r},${g},${b},${0.3 + intensity * 0.7})`
                    : (isDark ? "rgba(30,42,60,0.5)" : "rgba(226,232,240,0.5)");
                ctx.fillRect(labelW + hi * cellW + 1, y + 1, cellW - 2, cellH - 2);
            });
        });

        // Legend bar
        const lgY = cssH - 14;
        const lgX = cssW - 120;
        for (let i = 0; i <= 10; i++) {
            const t = i / 10;
            const r = Math.round(30 + t * 225);
            const g = Math.round(200 - t * 100);
            ctx.fillStyle = `rgb(${r},${g},0)`;
            ctx.fillRect(lgX + i * 10, lgY, 10, 8);
        }
        ctx.fillStyle = textColor;
        ctx.font = "9px Inter";
        ctx.textAlign = "left";
        ctx.fillText("0W", lgX - 2, lgY + 8);
        ctx.textAlign = "right";
        ctx.fillText(`${maxVal}W`, lgX + 112, lgY + 8);
    }

    _renderMonthlyComparisonChart() {
        const canvas = this.refs.monthlyComparison?.el;
        if (!canvas) return;
        const mc = this.state.data.monthly_comparison;
        if (!mc || !mc.labels.length) { this._showEmpty(canvas); return; }
        const ctx = canvas.getContext("2d");
        this.charts.monthlyComparison = new Chart(ctx, {
            type: "bar",
            data: {
                labels: mc.labels,
                datasets: [
                    { label: mc.this_year_label, data: mc.this_year, backgroundColor: COLORS.primary + "cc", borderRadius: 4, borderSkipped: false },
                    { label: mc.last_year_label, data: mc.last_year, backgroundColor: COLORS.info + "88", borderRadius: 4, borderSkipped: false },
                ],
            },
            options: this._commonChartOptions({
                plugins: {
                    ...this._commonChartOptions().plugins,
                    tooltip: { ...this._commonChartOptions().plugins.tooltip, callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y.toFixed(1)} kWh` } },
                },
            }),
        });
    }

    _renderEnergyFlowChart() {
        const canvas = this.refs.energyFlow?.el;
        if (!canvas) return;
        const ef = this.state.data.energy_flow;
        if (!ef || !ef.labels.length) { this._showEmpty(canvas); return; }
        const ctx = canvas.getContext("2d");
        this.charts.energyFlow = new Chart(ctx, {
            type: "bar",
            data: {
                labels: ef.labels,
                datasets: [
                    { label: "PV tự dùng (kWh)", data: ef.self_use, backgroundColor: COLORS.success + "cc", stack: "energy", borderSkipped: false },
                    { label: "Xuất lưới (kWh)", data: ef.export, backgroundColor: COLORS.primary + "cc", stack: "energy", borderSkipped: false },
                    { label: "Lấy lưới (kWh)", data: ef.import_grid, backgroundColor: COLORS.danger + "88", stack: "energy", borderSkipped: false },
                ],
            },
            options: this._commonChartOptions({
                scales: {
                    x: { ...this._commonChartOptions().scales.x, stacked: true },
                    y: { ...this._commonChartOptions().scales.y, stacked: true, title: { display: true, text: "kWh", color: this.state.theme === "dark" ? "#cbd5e1" : "#475569" } },
                },
            }),
        });
    }

    _showEmpty(canvas) {
        const ctx = canvas.getContext("2d");
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "#94a3b8";
        ctx.font = "13px Inter, system-ui, sans-serif";
        ctx.textAlign = "center";
        ctx.fillText(_t("Chưa có dữ liệu trong khoảng thời gian này"), canvas.width / 2, canvas.height / 2);
    }

    // ------------------------------------------------------------------
    // Helpers / formatters
    // ------------------------------------------------------------------
    _fmtLabel(s) {
        if (!s) return "";
        const range = this.state.timeRange;
        if (range === "realtime") return s.slice(11, 16);
        if (["3month", "6month", "1year", "5year"].includes(range)) return s.length > 10 ? s.slice(0, 10) : s;
        if (range === "1month" || range === "1week") return s.slice(5, 16);
        return s.slice(11, 16);
    }

    fmtNumber(v, digits = 2) {
        if (v === null || v === undefined) return "—";
        return Number(v).toLocaleString("vi-VN", { minimumFractionDigits: 0, maximumFractionDigits: digits });
    }

    fmtMoney(v) {
        if (v === null || v === undefined) return "—";
        return Number(v).toLocaleString("vi-VN", { maximumFractionDigits: 0 });
    }

    get refreshLabel() {
        if (!this.state.lastRefresh) return "—";
        return this.state.lastRefresh.toLocaleTimeString("vi-VN");
    }

    get hasData() {
        return !!this.state.data;
    }

    get dataFreshClass() {
        if (!this.state.lastRefresh) return "stale";
        const ageSec = (new Date() - this.state.lastRefresh) / 1000;
        return ageSec < 120 ? "fresh" : "stale";
    }

    get dataFreshLabel() {
        if (!this.state.lastRefresh) return "Chưa tải";
        const ageSec = (new Date() - this.state.lastRefresh) / 1000;
        return ageSec < 120 ? "Mới nhất" : "Cũ";
    }

    onDeviceClick(deviceId) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "smartsolar.device",
            res_id: deviceId,
            views: [[false, "form"]],
            target: "current",
        });
    }

    onOpenSystems() {
        this.action.doAction("smartsolar.action_smartsolar_system");
    }

    deviceTypeLabel(type) {
        if (type === "grid_tie_inverter") return "Grid Tie";
        if (type === "charge_power") return "Charge";
        return type || "";
    }

    // ------------------------------------------------------------------
    // Live Status computed properties
    // ------------------------------------------------------------------
    get onlinePercent() {
        const total = this.state.data?.kpi?.total_devices || 0;
        const online = this.state.data?.kpi?.online_devices || 0;
        return total > 0 ? Math.round(online / total * 100) : 0;
    }

    get selfConsumptionPct() { return this.state.data?.kpi?.self_consumption_pct || 0; }
    get gridDependencyPct() { return this.state.data?.kpi?.grid_dependency_pct || 0; }
    get yieldKwhPerKwp() { return this.state.data?.kpi?.yield_kwh_per_kwp || 0; }
    get co2Trees() { return this.state.data?.kpi?.co2_trees || 0; }

    // ------------------------------------------------------------------
    // Environment (thời tiết) — chỉ hiển thị khi module môi trường có cài
    // ------------------------------------------------------------------
    get weather() { return this.state.data?.environment || { available: false }; }
    get hasEnvironment() { return !!this.weather.available; }

    get weatherIcon() {
        // Ánh xạ mã thời tiết WMO -> icon FontAwesome.
        const code = this.weather.weather_code;
        if (code === 0) return "fa-sun-o";
        if (code >= 1 && code <= 2) return "fa-cloud";
        if (code === 3) return "fa-cloud";
        if (code >= 45 && code <= 48) return "fa-align-justify";   // sương mù
        if (code >= 51 && code <= 67) return "fa-tint";            // mưa phùn/mưa
        if (code >= 71 && code <= 77) return "fa-snowflake-o";     // tuyết
        if (code >= 80 && code <= 82) return "fa-umbrella";        // mưa rào
        if (code >= 95) return "fa-bolt";                          // dông
        return "fa-thermometer-half";
    }

    // ------------------------------------------------------------------

    get performanceAlertLevel() {
        const dev = this.state.data?.kpi?.perf_deviation_pct || 0;
        if (this.state.data?.kpi?.perf_alert) return dev < -40 ? "critical" : "warning";
        return "none";
    }

    get peakPowerLabel() {
        const h = this.state.data?.kpi?.peak_hour;
        const w = this.state.data?.kpi?.peak_power_w || 0;
        if (h === null || h === undefined) return "—";
        return `${String(h).padStart(2, '0')}:00 (${Math.round(w)}W)`;
    }

    get hotDeviceCount() {
        const alert = this.state.data?.kpi?.temperature_alert || 60;
        return (this.state.data?.devices || []).filter(d => d.temperature >= alert).length;
    }

    get totalDevicePowerW() {
        return (this.state.data?.devices || []).reduce((s, d) => s + (d.power || 0), 0);
    }

    get maxDeviceTemp() {
        const temps = (this.state.data?.devices || []).map(d => d.temperature || 0);
        return temps.length ? Math.max(...temps) : 0;
    }

    get avgDeviceTemp() {
        const devs = (this.state.data?.devices || []).filter(d => d.temperature > 0);
        if (!devs.length) return 0;
        return devs.reduce((s, d) => s + d.temperature, 0) / devs.length;
    }

    get hottestDeviceName() {
        const devs = this.state.data?.devices || [];
        if (!devs.length) return "—";
        return devs.reduce((a, b) => (a.temperature || 0) >= (b.temperature || 0) ? a : b).name || "—";
    }

    get offlineDeviceNames() {
        const offline = (this.state.data?.devices || []).filter(d => d.status === "offline");
        if (!offline.length) return "Không có";
        return offline.map(d => d.name).join(", ");
    }

    get connectionLabel() {
        return this.state.realtimeActive ? "TRỰC TIẾP" : "Offline";
    }

    get currentRangeLabel() {
        const r = this.timeRanges.find(t => t.key === this.state.timeRange);
        return r ? r.label : this.state.timeRange;
    }

    get selectedSystemName() {
        if (!this.state.systemId) return "Tất cả hệ thống";
        const sys = (this.state.data?.systems || []).find(s => s.id === this.state.systemId);
        return sys ? sys.name : "—";
    }

    // Realtime energy values (cập nhật từ bus)
    get realtimeGridOutW() { return this._rt?.grid_out_w || 0; }
    get realtimeGridInW() { return this._rt?.grid_in_w || 0; }
    get realtimePvW() { return this._rt?.pv_w || 0; }
    get realtimeChargeW() { return this._rt?.charge_w || 0; }
    get realtimeBatV() { return this._rt?.bat_v || 0; }
    get realtimeBatA() { return this._rt?.bat_a || 0; }
    get realtimeBatStatus() {
        const a = this._rt?.bat_a || 0;
        if (a > 0.5) return "⬆ Đang sạc";
        if (a < -0.5) return "⬇ Đang xả";
        return "— Chờ";
    }
}

registry.category("actions").add("smartsolar_dashboard", SmartSolarDashboard);
