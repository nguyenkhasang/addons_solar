# SmartSolar Dashboard


## Battery estimate and BMS comparison

SYSTEM OVERVIEW currently receives controller output current, not a battery
shunt/BMS measurement. Its estimate is `V_mppt_out * I_mppt_out - P_inverter_ac`.
No fixed MPPT efficiency or wattage offset is applied by this component.
PV input minus controller output is a difference between reported telemetry,
not a verified loss measurement. Inverter losses, sensor calibration and
measurement timing are not resolved by this estimate.

On 2026-10-07, 31 recent samples reported controller output/PV input between
90.9856% and 91.0113% (mean 90.9999%). This suggests the upstream current might
be derived using fixed efficiency; the upstream firmware has not been inspected.
At 08:21:52 Vietnam, PV=978.79815 W, V=55.110 V, I=16.161 A;
controller output=890.63271 W, nearby inverter AC=211 W, estimate=679.63271 W.
A separately reported BMS reading of 710 W needs matched timestamp and BMS
voltage/current before numerical calibration. Do not apply a constant +30 W
or an assumed efficiency to make one point match. Prefer direct BMS net power
when a BMS telemetry source is integrated.


## Audit of battery widgets and charts

Live Status and System Overview share `SystemOverview.estimateBatteryFlow`.
PV input is displayed as PV, controller current as MPPT output current, and
battery power/current as signed estimates. Every incoming bus event updates
widgets, even when viewing a historical chart. Switching systems clears the
old live cache and replaces the bus channel. Missing or >10-second-skewed
battery counterparts remain null. Multi-device Live scopes require selecting
an appropriate supported scope; they are not silently reduced to one device.

Historical `battery_flow` is computed from retained raw rows, requiring a nearby
reading from every active configured charger and inverter in the system. V*I
is calculated before averaging. Controller powers are summed before subtracting
inverter AC once. Net current is returned only for a single MPPT/system scope.
Both signs are retained. `paired_count` vs `sample_count` describe which raw
observations were usable. Beyond raw retention the existing summaries cannot
recover this pairing or mean(V*I); net data and MPPT/PV output ratio stay unknown.
Existing PV/MPPT voltage/current history is retained with correct labels.

The MPPT/PV chart compares output V*I to PV input, not `charge_power/PV` (both of
which represented PV input). This is a telemetry ratio, not verified loss/BMS
measurement. No constant compensation or efficiency calibration is inserted.
Live chart buffers use a shared event timeline with nulls for missing samples.

Checks: `node smartsolar_dashboard/tests/test_system_overview.cjs`,
`node smartsolar_dashboard/tests/test_dashboard_battery.cjs`, and Odoo test tag
`/smartsolar_dashboard` on a disposable clone of the database.


## Energy distribution quality

The distribution chart validates counter availability, stagnant readings versus
nonzero branch power, and counter/branch consistency when observed power covers
at least 80% of the window. If a branch counter is missing, stalled or inconsistent,
both branches use trapezoidal power-derived kWh on the same retained time segments
(maximum 300-second gap). Do not mix measured and estimated branches. Complete
retained day power metadata supports old ranges; partial edges use retained raw.
Coverage and the estimated source remain visible. Values retain six decimal places
to avoid erasing the small grid slice in short windows. Missing data is unavailable.

Live distribution uses distinct actual inverter timestamps in the Live buffer,
not the previously loaded historical distribution. Cached power does not create
observations during outages. The grid-dependency KPI uses the same validation
for the local-day-to-now window and exposes its estimate/coverage explicitly.
The upstream meaning of energy_total/limiter_total still requires verification;
the chart does not treat an inconsistent counter mapping as verified measurement.
