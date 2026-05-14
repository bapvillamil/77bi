/* 77BI BI — main.js
   Full BI dashboard: field panel, drag-drop builder, multi-widget canvas,
   AI query bar, insight panel.
*/

'use strict';

// Register Chart.js datalabels plugin globally
if (typeof ChartDataLabels !== 'undefined') {
  Chart.register(ChartDataLabels);
}

const CFG = window.APP_CONFIG || {};

// ── DOM refs ──────────────────────────────────────────────────────────────────
const queryInput     = document.getElementById('queryInput');
const btnSubmit      = document.getElementById('btnSubmit');
const btnReset       = document.getElementById('btnReset');
const intentChip     = document.getElementById('intentChip');
const statusDot      = document.getElementById('statusDot');
const statusLabel    = document.getElementById('statusLabel');
const loadingOverlay = document.getElementById('loadingOverlay');
const loadingText    = document.getElementById('loadingText');
const dashboardGrid  = document.getElementById('dashboardGrid');
const emptyCanvas    = document.getElementById('emptyCanvas');
const insightBody    = document.getElementById('insightBody');
const insightFooter  = document.getElementById('insightFooter');
const previewBody    = document.getElementById('previewBody');
const previewMeta    = document.getElementById('previewMeta');
const btnBuildChart  = document.getElementById('btnBuildChart');
const btnAddToDash   = document.getElementById('btnAddToDashboard');
const filtersArea    = document.getElementById('filtersArea');
const fieldSearch    = document.getElementById('fieldSearch');
const sidebarLoading = document.getElementById('sidebarLoading');

// ── State ─────────────────────────────────────────────────────────────────────
let allFields        = [];           // { name, type, filename }
let widgets          = [];           // dashboard widgets array
let previewChartInst = null;         // Chart.js instance in builder preview
let previewChartData = null;         // last built chart data
let activeInsightData = null;        // chart data for modal insight
let dragFieldName    = null;         // field currently being dragged
let chatHistory      = [];

const CHART_COLORS = [
  '#3B82F6','#10B981','#F59E0B','#EF4444',
  '#8B5CF6','#EC4899','#06B6D4','#84CC16',
  '#F97316','#6366F1',
];

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadFields();
  loadDashboard();
  initDropZones();
  initChartTypePicker();
  initSortable();
  setStatus('ready', 'Ready');
});

// ── Tab switching ─────────────────────────────────────────────────────────────
function switchTab(tab) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));

  if (tab === 'dashboard') {
    document.getElementById('tabDashboard').classList.add('active');
    document.getElementById('viewDashboard').classList.add('active');
  } else {
    document.getElementById('tabBuilder').classList.add('active');
    document.getElementById('viewBuilder').classList.add('active');
  }
}
window.switchTab = switchTab;

// ── Field loading ─────────────────────────────────────────────────────────────
async function loadFields() {
  sidebarLoading.classList.add('active');
  try {
    const res  = await fetch(CFG.apiSchema);
    const data = await res.json();
    allFields  = data.fields || [];
    renderFields(allFields);
  } catch (e) {
    console.error('Field load error:', e);
  } finally {
    sidebarLoading.classList.remove('active');
  }
}

function renderFields(fields) {
  const cats    = fields.filter(f => f.type === 'categorical');
  const nums    = fields.filter(f => f.type === 'numeric');
  const dates   = fields.filter(f => f.type === 'date');

  document.getElementById('fieldsCategorical').innerHTML = cats.map(fieldHTML).join('');
  document.getElementById('fieldsNumeric').innerHTML     = nums.map(fieldHTML).join('');
  document.getElementById('fieldsDate').innerHTML        = dates.map(fieldHTML).join('');

  // Bind drag events
  document.querySelectorAll('.field-item').forEach(el => {
    el.addEventListener('dragstart', onFieldDragStart);
    el.addEventListener('dragend',   onFieldDragEnd);
  });
}

function fieldHTML(f) {
  return `<div class="field-item" draggable="true" data-field="${escHtml(f.name)}" data-type="${f.type}">
    <span class="field-dot ${f.type}"></span>
    <span>${escHtml(f.name)}</span>
  </div>`;
}

// Field search filter
fieldSearch.addEventListener('input', () => {
  const q = fieldSearch.value.toLowerCase();
  const filtered = q ? allFields.filter(f => f.name.toLowerCase().includes(q)) : allFields;
  renderFields(filtered);
});

document.getElementById('btnRefreshFields').addEventListener('click', loadFields);

// ── Drag-and-drop: fields → drop zones ───────────────────────────────────────
function onFieldDragStart(e) {
  dragFieldName = e.currentTarget.dataset.field;
  e.dataTransfer.effectAllowed = 'copy';
  e.dataTransfer.setData('text/plain', dragFieldName);
  e.currentTarget.classList.add('dragging');

  // Create ghost element
  const ghost = document.createElement('div');
  ghost.className = 'drag-ghost';
  ghost.textContent = dragFieldName;
  ghost.id = 'dragGhost';
  document.body.appendChild(ghost);
  e.dataTransfer.setDragImage(ghost, 9999, 9999); // hide default
}

function onFieldDragEnd(e) {
  e.currentTarget.classList.remove('dragging');
  dragFieldName = null;
  const ghost = document.getElementById('dragGhost');
  if (ghost) ghost.remove();
}

// Move ghost with mouse
document.addEventListener('dragover', e => {
  const ghost = document.getElementById('dragGhost');
  if (ghost) {
    ghost.style.left = e.clientX + 12 + 'px';
    ghost.style.top  = e.clientY + 12 + 'px';
  }
});

function initDropZones() {
  document.querySelectorAll('.drop-zone').forEach(zone => {
    zone.addEventListener('dragover',  e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', ()  => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
      e.preventDefault();
      zone.classList.remove('drag-over');
      const field = e.dataTransfer.getData('text/plain') || dragFieldName;
      if (field) setDropZone(zone, field);
    });
  });
}

function setDropZone(zone, fieldName) {
  zone.classList.add('has-item');
  zone.innerHTML = `
    <div class="drop-tag">
      <span>${escHtml(fieldName)}</span>
      <button class="drop-tag-remove" onclick="clearDropZone(this.parentElement.parentElement)">×</button>
    </div>`;
  zone.dataset.value = fieldName;
}

function clearDropZone(zone) {
  zone.classList.remove('has-item');
  zone.dataset.value = '';
  zone.innerHTML = `<span class="drop-hint">${zone.id === 'dropXAxis' ? 'Drop a dimension here' : 'Drop a field here (optional)'}</span>`;
}
window.clearDropZone = clearDropZone;

// ── Chart type picker ─────────────────────────────────────────────────────────
// Zone config per chart type
const CHART_ZONES = {
  // type: { zones shown, labels overrides }
  bar:          { xAxis:'X Axis / Category', yAxis:'Value',  groupBy:true, showAgg:true, showToggle:true, scatter:false, target:false, kpiFmt:false },
  horizontalBar:{ xAxis:'Y Axis / Category', yAxis:'Value',  groupBy:true, showAgg:true, showToggle:true, scatter:false, target:false, kpiFmt:false },
  line:         { xAxis:'X Axis',            yAxis:'Value',  groupBy:true, showAgg:true, showToggle:true, scatter:false, target:false, kpiFmt:false },
  area:         { xAxis:'X Axis',            yAxis:'Value',  groupBy:true, showAgg:true, showToggle:true, scatter:false, target:false, kpiFmt:false },
  doughnut:     { xAxis:null,                yAxis:'Value',  groupBy:'Group / Color', showAgg:true, showToggle:true, scatter:false, target:false, kpiFmt:false },
  pie:          { xAxis:null,                yAxis:'Value',  groupBy:'Group / Color', showAgg:true, showToggle:true, scatter:false, target:false, kpiFmt:false },
  scatter:      { xAxis:'X Axis',            yAxis:null,     groupBy:'Color', showAgg:false, showToggle:false, scatter:true, target:false, kpiFmt:false },
  kpi:          { xAxis:null,                yAxis:'Value',  groupBy:false, showAgg:true, showToggle:false, scatter:false, target:true, kpiFmt:true },
};

function updateZonesForChartType(type) {
  const cfg = CHART_ZONES[type] || CHART_ZONES.bar;

  // X Axis zone
  const zX = document.getElementById('zoneXAxis');
  if (zX) {
    zX.style.display = cfg.xAxis ? '' : 'none';
    const lbl = document.getElementById('lblXAxis');
    if (lbl && cfg.xAxis) lbl.textContent = cfg.xAxis;
  }

  // Y Axis / Value zone
  const zY = document.getElementById('zoneYAxis');
  if (zY) {
    zY.style.display = cfg.yAxis ? '' : 'none';
    const lbl = document.getElementById('lblYAxis');
    if (lbl && cfg.yAxis) lbl.textContent = cfg.yAxis;
  }

  // Group By zone
  const zG = document.getElementById('zoneGroupBy');
  if (zG) {
    zG.style.display = cfg.groupBy ? '' : 'none';
    const lbl = document.getElementById('lblGroupBy');
    if (lbl && typeof cfg.groupBy === 'string') lbl.textContent = cfg.groupBy;
    else if (lbl) lbl.textContent = 'Group By / Color';
  }

  // Scatter-specific zones
  const zSY = document.getElementById('zoneScatterYAxis');
  const zSL = document.getElementById('zoneScatterLabel');
  if (zSY) zSY.style.display = cfg.scatter ? '' : 'none';
  if (zSL) zSL.style.display = cfg.scatter ? '' : 'none';

  // Target value zone (KPI only)
  const zT = document.getElementById('zoneTargetValue');
  if (zT) zT.style.display = cfg.target ? '' : 'none';

  // Aggregation
  const zA = document.getElementById('zoneAggregation');
  if (zA) zA.style.display = cfg.showAgg ? '' : 'none';

  // Show values toggle
  const zSV = document.getElementById('zoneShowValues');
  if (zSV) zSV.style.display = cfg.showToggle ? '' : 'none';

  // KPI format
  const zKF = document.getElementById('zoneKpiFormat');
  if (zKF) zKF.style.display = cfg.kpiFmt ? '' : 'none';

  // For donut/pie: X axis zone is hidden, but we use groupBy as the category source
  // so rename dropXAxis hint for clarity
  const dropX = document.getElementById('dropXAxis');
  if (dropX && !dropX.dataset.value) {
    const hint = dropX.dataset.hint || 'Drop a dimension here';
    const span = dropX.querySelector('.drop-hint');
    if (span) span.textContent = hint;
  }
}

function initChartTypePicker() {
  document.querySelectorAll('.chart-type-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.chart-type-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      updateZonesForChartType(btn.dataset.type);
    });
  });
  // Init zones for default (bar)
  updateZonesForChartType('bar');
}

function getSelectedChartType() {
  const btn = document.querySelector('.chart-type-btn.active');
  return btn ? btn.dataset.type : 'bar';
}

function getAggregation() {
  const sel = document.getElementById('aggSelect');
  return sel ? sel.value : 'count';
}

function getShowValues() {
  const tog = document.getElementById('showValuesToggle');
  return tog ? tog.checked : true;
}

// ── Filters ───────────────────────────────────────────────────────────────────
function addFilter() {
  const row = document.createElement('div');
  row.className = 'filter-row';

  const colOpts = allFields.map(f => `<option value="${escHtml(f.name)}">${escHtml(f.name)}</option>`).join('');
  row.innerHTML = `
    <select class="filter-select"><option value="">Column…</option>${colOpts}</select>
    <input class="filter-input" placeholder="Value…" />
    <button class="btn-icon small" onclick="this.parentElement.remove()">
      <svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M1 1l8 8M9 1L1 9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
    </button>`;
  filtersArea.appendChild(row);
}
window.addFilter = addFilter;

function getFilters() {
  const filters = {};
  filtersArea.querySelectorAll('.filter-row').forEach(row => {
    const col = row.querySelector('.filter-select').value;
    const val = row.querySelector('.filter-input').value.trim();
    if (col && val) filters[col] = val;
  });
  return filters;
}

// ── Builder: build chart ──────────────────────────────────────────────────────
btnBuildChart.addEventListener('click', buildChart);

async function buildChart() {
  const chartType  = getSelectedChartType();
  const cfg        = CHART_ZONES[chartType] || CHART_ZONES.bar;

  // Determine the "primary" field needed based on chart type
  const xAxis      = document.getElementById('dropXAxis').dataset.value   || '';
  const yAxis      = document.getElementById('dropYAxis').dataset.value   || '';
  const groupBy    = document.getElementById('dropGroupBy').dataset.value || '';
  const scatterY   = document.getElementById('dropScatterY')?.dataset.value || '';
  const scatterLbl = document.getElementById('dropScatterLabel')?.dataset.value || '';
  const targetVal  = document.getElementById('dropTargetValue')?.dataset.value || '';
  const filters    = getFilters();
  const kpiFmt     = document.getElementById('kpiFormatSelect')?.value || 'number';

  // Validation: require at least one meaningful field
  const needsXAxis   = cfg.xAxis !== null;    // bar/line/area/scatter/h-bar
  const needsGroupBy = cfg.xAxis === null;    // donut/pie — use groupBy as category
  const needsValue   = true;                  // always need a value/measure

  if (needsXAxis && !xAxis) {
    alert(`Please drop a field onto "${cfg.xAxis}" first.`);
    return;
  }
  if (needsGroupBy && !groupBy && !yAxis) {
    alert('Please drop a field onto "Group / Color" or "Value" first.');
    return;
  }
  if (chartType === 'kpi' && !yAxis && !xAxis) {
    alert('Please drop a field onto "Value" for the KPI card.');
    return;
  }
  if (chartType === 'scatter' && !xAxis) {
    alert('Please drop a field onto "X Axis" for the scatter chart.');
    return;
  }

  // For donut/pie, the x_axis sent to backend = groupBy (category source)
  const effectiveXAxis = (chartType === 'doughnut' || chartType === 'pie')
    ? (groupBy || yAxis)
    : (chartType === 'scatter' ? xAxis : xAxis);
  const effectiveYAxis = (chartType === 'scatter') ? scatterY : yAxis;
  const effectiveGroup = (chartType === 'doughnut' || chartType === 'pie') ? '' : groupBy;

  setLoading(true, 'Building chart…');
  try {
    const res = await fetch(CFG.apiBuildChart, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
      body: JSON.stringify({
        x_axis:      effectiveXAxis,
        y_axis:      effectiveYAxis,
        group_by:    effectiveGroup,
        chart_type:  chartType,
        filters,
        aggregation: getAggregation(),
        show_values: getShowValues(),
        kpi_format:  kpiFmt,
        target_value: targetVal,
      }),
    });
    const data = await res.json();

    if (data.error) { showError(data.error); return; }

    const customLabel = document.getElementById('widgetLabelInput')?.value.trim();
    const cd = {
      ...data.chart,
      // Normalise: API returns `type`, renderWidgetContent expects `chart_type`
      chart_type:   data.chart.type,
      scope:        data.scope || {},
      raw_rows:     [],
      summary:      {},
      show_values:  getShowValues(),
      kpi_format:   kpiFmt,
      // Apply custom label if provided
      result_label: customLabel || data.chart.result_label,
      y_axis_label: customLabel || data.chart.y_axis_label,
    };
    previewChartData = cd;

    if (chartType === 'kpi') {
      renderKPIPreview(cd);
    } else {
      renderPreviewChart(cd);
    }
    btnAddToDash.disabled = false;
    setStatus('ready', 'Preview ready');

  } catch (e) {
    showError(e.message);
  } finally {
    setLoading(false);
  }
}

function renderKPIPreview(cd) {
  const val       = cd.values?.[0] ?? 0;
  const label     = cd.result_label || cd.y_axis_label || 'Value';
  const formatted = formatKPIValue(val, cd.kpi_format || 'number');

  previewBody.innerHTML = `
    <div class="preview-chart-wrap" style="display:flex;align-items:center;justify-content:center;height:100%">
      <div class="kpi-widget">
        <div class="kpi-label-row" id="prev-klrow">
          <span class="kpi-widget-label" id="prev-klspan">${escHtml(label)}</span>
          <button class="kpi-edit-btn" id="prev-kledit" title="Edit label">
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
              <path d="M7.5 1.5l2 2L3 10H1V8L7.5 1.5z" stroke="currentColor" stroke-width="1.1"
                stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
          <input class="kpi-label-input" id="prev-klin" type="text"
            value="${escHtml(label)}" style="display:none"/>
        </div>
        <div class="kpi-widget-value">${formatted}</div>
      </div>
    </div>`;

  const spanEl  = document.getElementById('prev-klspan');
  const editBtn = document.getElementById('prev-kledit');
  const inputEl = document.getElementById('prev-klin');

  editBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    spanEl.style.display  = 'none';
    editBtn.style.display = 'none';
    inputEl.style.display = 'inline-block';
    inputEl.value         = spanEl.textContent;
    inputEl.focus();
    inputEl.select();
  });

  const savePreviewLabel = () => {
    const newLabel = inputEl.value.trim() || label;
    spanEl.textContent    = newLabel;
    spanEl.style.display  = '';
    editBtn.style.display = '';
    inputEl.style.display = 'none';
    // Sync to the Widget Label input and previewChartData
    const inp = document.getElementById('widgetLabelInput');
    if (inp) inp.value = newLabel;
    if (previewChartData) {
      previewChartData.result_label = newLabel;
      previewChartData.y_axis_label = newLabel;
    }
  };
  inputEl.addEventListener('blur',    savePreviewLabel);
  inputEl.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); inputEl.blur(); }
    if (e.key === 'Escape') { inputEl.value = label; inputEl.blur(); }
  });

  btnAddToDash.disabled = false;
}

function renderPreviewChart(chartData) {
  if (previewChartInst) { previewChartInst.destroy(); previewChartInst = null; }

  previewBody.innerHTML = `
    <div class="preview-chart-wrap" style="height:100%;display:flex;flex-direction:column">
      <div class="preview-chart-title">${escHtml(chartData.result_label || '')}</div>
      <div style="flex:1;position:relative;min-height:200px"><canvas id="previewCanvas"></canvas></div>
    </div>`;

  previewMeta.textContent = `${chartData.labels?.length || 0} data points`;

  const ctx = document.getElementById('previewCanvas').getContext('2d');
  previewChartInst = buildChartJS(ctx, chartData, false);
}

// ── Add preview to dashboard ──────────────────────────────────────────────────
btnAddToDash.addEventListener('click', async () => {
  if (!previewChartData) return;

  const size        = document.querySelector('input[name="widgetSize"]:checked')?.value || 'medium';
  const customLabel = document.getElementById('widgetLabelInput')?.value.trim();
  const title       = customLabel || previewChartData.result_label || 'Chart';

  setLoading(true, 'Adding to dashboard…');
  try {
    const res  = await fetch(CFG.apiAddWidget, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
      body: JSON.stringify({ chart_data: previewChartData, title, size }),
    });
    const data = await res.json();

    if (data.error) { showError(data.error); return; }

    widgets.push(data.widget);
    renderWidget(data.widget);
    updateEmptyState();
    switchTab('dashboard');
    setStatus('ready', `Dashboard: ${data.total} widgets`);

  } catch (e) {
    showError(e.message);
  } finally {
    setLoading(false);
  }
});

// ── Dashboard: load existing widgets ─────────────────────────────────────────
async function loadDashboard() {
  try {
    const res  = await fetch(CFG.apiDashboard);
    const data = await res.json();
    widgets    = data.widgets || [];
    widgets.forEach(renderWidget);
    updateEmptyState();
  } catch (e) {
    console.error('Dashboard load error:', e);
  }
}

// ── Widget rendering ──────────────────────────────────────────────────────────
function renderWidget(widget) {
  const card = document.createElement('div');
  card.className = `widget-card size-${widget.size || 'medium'}`;
  card.dataset.widgetId = widget.id;

  const cd    = widget.chart_data || {};
  const title = widget.title || cd.result_label || 'Chart';

  card.innerHTML = `
    <div class="widget-header">
      <input
        class="widget-title-input"
        id="wtitle-${widget.id}"
        value="${escHtml(title)}"
        title="Click to rename"
        spellcheck="false"
      />
      <div class="widget-actions">
        <button class="widget-btn" title="Rename widget" onclick="focusWidgetTitle('${widget.id}')">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M8 1.5l2.5 2.5L3 11.5H.5V9L8 1.5z" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="widget-btn" title="Analyze with AI" onclick="openInsightModal('${widget.id}')">
          <svg width="13" height="13" viewBox="0 0 13 13" fill="none"><circle cx="6.5" cy="6.5" r="5" stroke="currentColor" stroke-width="1.2"/><path d="M4.5 6.5l1.5 1.5 2.5-2.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="widget-btn" title="Remove widget" onclick="removeWidget('${widget.id}')">
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
        </button>
      </div>
    </div>
    <div class="widget-body" id="wb-${widget.id}"></div>`;

  // Insert before empty canvas placeholder
  if (emptyCanvas.parentNode === dashboardGrid) {
    dashboardGrid.insertBefore(card, emptyCanvas);
  } else {
    dashboardGrid.appendChild(card);
  }

  // Wire title rename — save on blur/Enter, update widget object
  const titleInput = document.getElementById(`wtitle-${widget.id}`);
  if (titleInput) {
    const saveTitle = () => {
      const newTitle = titleInput.value.trim() || title;
      titleInput.value = newTitle;
      // Update in-memory widget
      const w = widgets.find(w => w.id === widget.id);
      if (w) w.title = newTitle;
      // Persist to session
      updateWidgetTitle(widget.id, newTitle);
    };
    titleInput.addEventListener('blur', saveTitle);
    titleInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') { e.preventDefault(); titleInput.blur(); }
      if (e.key === 'Escape') { titleInput.value = title; titleInput.blur(); }
    });
  }

  // Render chart after DOM is fully painted (setTimeout > RAF for this case)
  const body = document.getElementById(`wb-${widget.id}`);
  setTimeout(() => renderWidgetContent(body, cd), 50);
}

function focusWidgetTitle(widgetId) {
  const inp = document.getElementById(`wtitle-${widgetId}`);
  if (inp) { inp.focus(); inp.select(); }
}
window.focusWidgetTitle = focusWidgetTitle;

async function updateWidgetTitle(widgetId, newTitle) {
  try {
    const w = widgets.find(w => w.id === widgetId);
    if (!w) return;
    w.title = newTitle;
    // Persist title + current order in one call
    const order  = widgets.map(w => w.id);
    const titles = Object.fromEntries(widgets.map(w => [w.id, w.title || '']));
    await fetch(CFG.apiReorder, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
      body: JSON.stringify({ order, titles }),
    });
    // Also update the input value so it reflects the saved state
    const inp = document.getElementById(`wtitle-${widgetId}`);
    if (inp) inp.value = newTitle;
  } catch(e) { /* silent */ }
}

function renderWidgetContent(container, cd) {
  if (!cd) {
    container.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text3);font-size:12px">No data</div>`;
    return;
  }

  // Normalise: accept both `chart_type` and `type` (API uses `type`, stored data uses `chart_type`)
  const chartType = cd.chart_type || cd.type;
  if (!chartType) {
    container.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text3);font-size:12px">No data</div>`;
    return;
  }
  // Ensure chart_type is always set for downstream code
  cd.chart_type = chartType;

  if (cd.chart_type === 'kpi') {
    const val       = cd.values?.[0] ?? 0;
    const label     = cd.y_axis_label || cd.result_label || 'Value';
    const formatted = formatKPIValue(val, cd.kpi_format || 'number');
    const uid       = Math.random().toString(36).slice(2);
    container.innerHTML = `
      <div class="kpi-widget">
        <div class="kpi-label-row" id="klrow-${uid}">
          <span class="kpi-widget-label" id="klspan-${uid}">${escHtml(label)}</span>
          <button class="kpi-edit-btn" id="kledit-${uid}" title="Edit label">
            <svg width="11" height="11" viewBox="0 0 11 11" fill="none">
              <path d="M7.5 1.5l2 2L3 10H1V8L7.5 1.5z" stroke="currentColor" stroke-width="1.1"
                stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </button>
          <input class="kpi-label-input" id="klin-${uid}" type="text"
            value="${escHtml(label)}" style="display:none"/>
        </div>
        <div class="kpi-widget-value">${formatted}</div>
      </div>`;

    // Wire the edit button — toggle between display and input mode
    const spanEl  = container.querySelector(`#klspan-${uid}`);
    const editBtn = container.querySelector(`#kledit-${uid}`);
    const inputEl = container.querySelector(`#klin-${uid}`);

    editBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      spanEl.style.display  = 'none';
      editBtn.style.display = 'none';
      inputEl.style.display = 'inline-block';
      inputEl.value         = spanEl.textContent;
      inputEl.focus();
      inputEl.select();
    });

    const saveLabel = () => {
      const newLabel = inputEl.value.trim() || label;
      spanEl.textContent    = newLabel;
      spanEl.style.display  = '';
      editBtn.style.display = '';
      inputEl.style.display = 'none';
      // Persist to widget data
      const card = container.closest('[data-widget-id]');
      if (card) {
        const widgetId = card.dataset.widgetId;
        const w = widgets.find(w => w.id === widgetId);
        if (w) {
          if (w.chart_data) { w.chart_data.y_axis_label = newLabel; w.chart_data.result_label = newLabel; }
          updateWidgetTitle(widgetId, w.title || newLabel);
        }
      }
    };
    inputEl.addEventListener('blur',    saveLabel);
    inputEl.addEventListener('keydown', e => {
      if (e.key === 'Enter')  { e.preventDefault(); inputEl.blur(); }
      if (e.key === 'Escape') { inputEl.value = label; inputEl.blur(); }
    });
    return;
  }

  // Determine fixed chart height from widget size class
  const card = container.closest('.widget-card');
  const sizeClass = card ? [...card.classList].find(c => c.startsWith('size-')) : 'size-medium';
  const heightMap = { 'size-small': 200, 'size-medium': 260, 'size-large': 320, 'size-full': 340 };
  const chartH = heightMap[sizeClass] || 260;

  container.style.cssText = 'padding:12px 12px 8px;overflow:hidden;display:block;box-sizing:border-box;';

  const wrap = document.createElement('div');
  wrap.style.cssText = `position:relative;width:100%;height:${chartH}px;`;

  const canvas = document.createElement('canvas');
  // Set canvas dimensions explicitly so Chart.js never gets zero
  canvas.width  = 400;   // will be overridden by responsive resize
  canvas.height = chartH;
  canvas.style.cssText = 'display:block;width:100%;height:100%;';

  wrap.appendChild(canvas);
  container.innerHTML = '';
  container.appendChild(wrap);

  // Small delay so the browser has committed the layout before Chart.js reads dimensions
  setTimeout(() => {
    buildChartJS(canvas.getContext('2d'), cd, true);
  }, 20);
}



// Format KPI value based on selected format
function formatKPIValue(val, fmt) {
  if (val === null || val === undefined) return '—';
  const num = parseFloat(val);
  if (isNaN(num)) return String(val);
  switch (fmt) {
    case 'number':       return num.toLocaleString('en-US', { maximumFractionDigits: 0 });
    case 'decimal':      return num.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 2 });
    case 'percentage':   return num.toFixed(1) + '%';
    case 'currency_usd': return '$' + num.toLocaleString('en-US', { maximumFractionDigits: 0 });
    case 'currency_php': return '₱' + num.toLocaleString('en-US', { maximumFractionDigits: 0 });
    case 'compact':
      if (Math.abs(num) >= 1_000_000) return (num / 1_000_000).toFixed(1) + 'M';
      if (Math.abs(num) >= 1_000)     return (num / 1_000).toFixed(1) + 'k';
      return num.toLocaleString('en-US', { maximumFractionDigits: 1 });
    default:             return num.toLocaleString('en-US');
  }
}

// ── Chart.js builder ──────────────────────────────────────────────────────────
function buildChartJS(ctx, cd, compact) {
  // Support both `type` (from API) and `chart_type` (from stored widget data)
  const { labels = [], values = [], x_axis_label, y_axis_label, datasets } = cd;
  const type = cd.type || cd.chart_type;
  const showValues = cd.show_values !== false; // default true

  const isHorizontal = type === 'horizontalBar';
  const chartType    = type === 'horizontalBar' ? 'bar'
                     : type === 'area'          ? 'line'
                     : type === 'pie'           ? 'pie'
                     : type || 'bar';
  const isLine  = type === 'line' || type === 'area';
  const isRound = type === 'doughnut' || type === 'pie';

  const builtDatasets = datasets ? datasets : [{
    label:           y_axis_label || 'Value',
    data:            values,
    backgroundColor: isLine ? 'rgba(59,130,246,0.1)' : labels.map((_, i) => CHART_COLORS[i % CHART_COLORS.length] + (isRound ? '' : 'CC')),
    borderColor:     isLine ? '#3B82F6'               : labels.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]),
    borderWidth:     isLine ? 2 : 1,
    fill:            type === 'area',
    tension:         0.4,
    pointBackgroundColor: '#3B82F6',
    pointRadius:     isLine ? (compact ? 2 : 3) : 0,
  }];

  const fontSize = compact ? 10 : 11;
  const hasPlugin = typeof ChartDataLabels !== 'undefined';

  // Data label config
  const datalabelsConfig = hasPlugin && showValues ? {
    display: true,
    color: isRound ? '#fff' : '#374151',
    font: { size: compact ? 9 : 11, weight: '600', family: 'Inter' },
    formatter: (val) => {
      if (val === null || val === undefined) return '';
      if (typeof val === 'number') {
        return val >= 1000 ? (val / 1000).toFixed(1) + 'k' : val.toLocaleString();
      }
      return val;
    },
    anchor: isRound ? 'center' : (isHorizontal ? 'end' : 'end'),
    align:  isRound ? 'center' : (isHorizontal ? 'right' : 'top'),
    clamp:  true,
    offset: isRound ? 0 : (compact ? 2 : 4),
    padding: { top: 1, bottom: 1 },
  } : { display: false };

  return new Chart(ctx, {
    type: chartType,
    data: { labels, datasets: builtDatasets },
    plugins: hasPlugin && showValues ? [ChartDataLabels] : [],
    options: {
      indexAxis:           isHorizontal ? 'y' : 'x',
      responsive:          true,
      maintainAspectRatio: false,
      animation:           { duration: compact ? 0 : 400 },
      layout: {
        padding: showValues && !isRound ? { top: compact ? 14 : 20, right: isHorizontal ? (compact ? 28 : 36) : 6 } : 0,
      },
      plugins: {
        legend: {
          display: isRound || (datasets && datasets.length > 1),
          position: 'bottom',
          labels: {
            color:    '#6B7280',
            font:     { size: fontSize, family: 'Inter' },
            boxWidth: 10,
            padding:  compact ? 8 : 12,
          },
        },
        tooltip: {
          backgroundColor: '#1F2937',
          titleColor:      '#F9FAFB',
          bodyColor:       '#D1D5DB',
          borderColor:     '#374151',
          borderWidth:     1,
          padding:         compact ? 8 : 10,
          titleFont:       { size: fontSize + 1 },
          bodyFont:        { size: fontSize },
        },
        datalabels: datalabelsConfig,
      },
      scales: isRound ? {} : {
        x: {
          grid:  { color: '#F3F4F6', drawBorder: false },
          ticks: { color: '#9CA3AF', font: { size: fontSize, family: 'Inter' }, maxRotation: 30 },
          title: { display: !compact && !!x_axis_label, text: x_axis_label || '', color: '#6B7280', font: { size: fontSize } },
        },
        y: {
          grid:  { color: '#F3F4F6', drawBorder: false },
          ticks: { color: '#9CA3AF', font: { size: fontSize, family: 'Inter' } },
          title: { display: !compact && !!y_axis_label, text: y_axis_label || '', color: '#6B7280', font: { size: fontSize } },
        },
      },
    },
  });
}

// ── Remove widget ─────────────────────────────────────────────────────────────
async function removeWidget(widgetId) {
  const card = document.querySelector(`[data-widget-id="${widgetId}"]`);
  if (card) card.remove();

  widgets = widgets.filter(w => w.id !== widgetId);
  updateEmptyState();

  try {
    await fetch(CFG.apiRemoveWidget, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
      body:    JSON.stringify({ id: widgetId }),
    });
  } catch (e) { console.error(e); }
}
window.removeWidget = removeWidget;

function updateEmptyState() {
  emptyCanvas.style.display = widgets.length === 0 ? '' : 'none';
}

// ── Dashboard layout ──────────────────────────────────────────────────────────
function changeLayout(val) {
  dashboardGrid.className = `dashboard-grid ${val}`;
}
window.changeLayout = changeLayout;

// ── Sortable dashboard widgets ────────────────────────────────────────────────
function initSortable() {
  Sortable.create(dashboardGrid, {
    animation:     150,
    ghostClass:    'sortable-ghost',
    dragClass:     'sortable-drag',
    handle:        '.widget-header',
    filter:        '#emptyCanvas',
    onEnd: async () => {
      const order = [...dashboardGrid.querySelectorAll('[data-widget-id]')]
        .map(el => el.dataset.widgetId);
      widgets = order.map(id => widgets.find(w => w.id === id)).filter(Boolean);

      try {
        await fetch(CFG.apiReorder, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
          body:    JSON.stringify({ order }),
        });
      } catch (e) { console.error(e); }
    },
  });
}

// ── AI Query ──────────────────────────────────────────────────────────────────
async function submitQuery() {
  const query = queryInput.value.trim();
  if (!query) return;

  setLoading(true, 'Generating Insight…');
  btnSubmit.disabled = true;

  try {
    const res  = await fetch(CFG.apiQuery, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
      body:    JSON.stringify({ query }),
    });
    const data = await res.json();

    if (data.error) { showError(data.error); return; }
    handleQueryResponse(data, query);

  } catch (e) {
    showError(e.message);
  } finally {
    setLoading(false);
    btnSubmit.disabled = false;
  }
}

function handleQueryResponse(data, query) {
  const intent = data.intent;
  setIntentChip(intent);

  if (intent === 'intent_1' || intent === 'intent_3') {
    if (data.chart) {
      const widgetData = {
        ...data.chart,
        scope:    data.scope || {},
        raw_rows: data.chart_data?.raw_rows || [],
        summary:  data.chart_data?.summary  || {},
      };
      addWidgetFromData(widgetData, data.chart.result_label || query, 'medium');
      switchTab('dashboard');
    }
  }

  if (intent === 'intent_2' || intent === 'intent_3') {
    if (data.insight) {
      renderInsight(query, data.insight);
      chatHistory.push({ role: 'user',      content: query });
      chatHistory.push({ role: 'assistant', content: data.insight });
      showInsightFooter();
    }
  }

  queryInput.value = '';
  setStatus('ready', intent === 'intent_1' ? 'Chart added' : intent === 'intent_2' ? 'Insight ready' : 'Chart + insight ready');
}

// Add a widget directly from data (no server round-trip for session)
async function addWidgetFromData(chartData, title, size) {
  try {
    const res  = await fetch(CFG.apiAddWidget, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
      body:    JSON.stringify({ chart_data: chartData, title, size }),
    });
    const data = await res.json();
    if (data.widget) {
      widgets.push(data.widget);
      renderWidget(data.widget);
      updateEmptyState();
      // Clear the label input for next chart
      const lbl = document.getElementById('widgetLabelInput');
      if (lbl) lbl.value = '';
    }
  } catch (e) { console.error(e); }
}

// Button to add widget from AI in dashboard toolbar
function addWidgetFromAI() {
  queryInput.focus();
}
window.addWidgetFromAI = addWidgetFromAI;

// ── Insight panel ─────────────────────────────────────────────────────────────
function renderInsight(query, text) {
  insightBody.innerHTML = `
    <div class="insight-block">
      <div class="insight-query-label">Query</div>
      <div class="insight-query-text">${escHtml(query)}</div>
      <div class="insight-text">${escHtml(text)}</div>
    </div>`;

  // Chat history
  if (chatHistory.length > 0) {
    const histHTML = chatHistory.slice(-6).map(t => `
      <div class="chat-turn">
        <div class="chat-role-label ${t.role}">${t.role === 'user' ? 'You' : 'Claude'}</div>
        <div class="chat-bubble">${escHtml(t.content)}</div>
      </div>`).join('');
    insightBody.innerHTML += `<div style="border-top:1px solid var(--border);margin-top:14px;padding-top:14px">${histHTML}</div>`;
  }

  insightBody.scrollTop = insightBody.scrollHeight;
}

function showInsightFooter() {
  insightFooter.style.display = 'flex';
}

// Follow-up
document.getElementById('btnFollowUp').addEventListener('click', sendFollowUp);
document.getElementById('followUpInput').addEventListener('keydown', e => {
  if (e.key === 'Enter') sendFollowUp();
});

async function sendFollowUp() {
  const q = document.getElementById('followUpInput').value.trim();
  if (!q) return;

  document.getElementById('followUpInput').value = '';
  setLoading(true, 'Generating insight…');

  try {
    const res  = await fetch(CFG.apiQuery, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
      body:    JSON.stringify({ query: q }),
    });
    const data = await res.json();
    if (data.error) { showError(data.error); return; }
    if (data.insight) {
      renderInsight(q, data.insight);
      chatHistory.push({ role: 'user',      content: q });
      chatHistory.push({ role: 'assistant', content: data.insight });
    }
  } catch (e) { showError(e.message); }
  finally     { setLoading(false); }
}

// ── Widget insight modal ──────────────────────────────────────────────────────
function openInsightModal(widgetId) {
  const widget = widgets.find(w => w.id === widgetId);
  if (!widget) return;

  activeInsightData = widget.chart_data;
  document.getElementById('modalTitle').textContent   = `Analyze: ${widget.title || 'Chart'}`;
  document.getElementById('modalInsightText').textContent = '';
  document.getElementById('modalFollowUp').value = '';
  document.getElementById('insightModal').style.display = 'flex';

  // Auto-prompt
  document.getElementById('modalFollowUp').value = 'Summarize the key insights from this chart.';
}
window.openInsightModal = openInsightModal;

function closeInsightModal() {
  document.getElementById('insightModal').style.display = 'none';
  activeInsightData = null;
}
window.closeInsightModal = closeInsightModal;

document.getElementById('btnModalAnalyze').addEventListener('click', async () => {
  const query = document.getElementById('modalFollowUp').value.trim();
  if (!query || !activeInsightData) return;

  document.getElementById('modalInsightText').textContent = 'Generating…';
  document.getElementById('btnModalAnalyze').disabled = true;

  try {
    const res  = await fetch(CFG.apiInsight, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CFG.csrfToken },
      body:    JSON.stringify({ query, chart_data: activeInsightData }),
    });
    const data = await res.json();
    document.getElementById('modalInsightText').textContent = data.insight || data.error || 'No insight returned.';
    document.getElementById('modalFollowUp').value = '';

    // Also push to side panel
    if (data.insight) {
      renderInsight(query, data.insight);
      showInsightFooter();
    }
  } catch (e) {
    document.getElementById('modalInsightText').textContent = 'Error: ' + e.message;
  } finally {
    document.getElementById('btnModalAnalyze').disabled = false;
  }
});

document.getElementById('modalFollowUp').addEventListener('keydown', e => {
  if (e.key === 'Enter') document.getElementById('btnModalAnalyze').click();
});

// Click outside modal to close
document.getElementById('insightModal').addEventListener('click', e => {
  if (e.target === document.getElementById('insightModal')) closeInsightModal();
});

// ── Status / Loading ──────────────────────────────────────────────────────────
function setStatus(state, label) {
  statusDot.className    = 'status-dot' + (state === 'loading' ? ' loading' : state === 'error' ? ' error' : '');
  statusLabel.textContent = label;
}

function setLoading(active, text = 'Processing…') {
  loadingText.textContent = text;
  loadingOverlay.classList.toggle('active', active);
  if (active) setStatus('loading', text);
}

function showError(msg) {
  setStatus('error', 'Error');
  setLoading(false);
  alert('Error: ' + msg);
}

function setIntentChip(intent) {
  const labels = { intent_1: '◈ chart only', intent_2: '◈ insight only', intent_3: '◈ chart + insight' };
  intentChip.textContent = labels[intent] || intent;
  intentChip.className   = `intent-chip visible intent-${intent.split('_')[1]}`;
}

// ── Reset ─────────────────────────────────────────────────────────────────────
btnReset.addEventListener('click', async () => {
  if (!confirm('Reset session? This clears all widgets and chat history.')) return;

  await fetch(CFG.apiClearSession, {
    method:  'POST',
    headers: { 'X-CSRFToken': CFG.csrfToken },
  });

  // Clear dashboard
  dashboardGrid.querySelectorAll('.widget-card').forEach(el => el.remove());
  widgets     = [];
  chatHistory = [];
  updateEmptyState();

  // Clear insight panel
  insightBody.innerHTML = `<div class="insight-empty"><p>Session cleared. Ask a new question to get started.</p></div>`;
  insightFooter.style.display = 'none';

  // Clear builder
  if (previewChartInst) { previewChartInst.destroy(); previewChartInst = null; }
  previewChartData  = null;
  btnAddToDash.disabled = true;
  previewBody.innerHTML = `<div class="preview-empty"><p>Drop fields and click <strong>Build Chart</strong></p></div>`;

  intentChip.className = 'intent-chip';
  setStatus('ready', 'Session reset');
});

// ── Event listeners ───────────────────────────────────────────────────────────
btnSubmit.addEventListener('click', submitQuery);
queryInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submitQuery(); }
});

// ── Utils ─────────────────────────────────────────────────────────────────────
function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Generate Dashboard ────────────────────────────────────────────────────────
function generateDashboard() {
  if (widgets.length === 0) {
    alert('Add at least one visualization to your dashboard first.');
    return;
  }
  // Open the dashboard view in a new tab
  window.open(CFG.dashboardViewUrl, '_blank');
}
window.generateDashboard = generateDashboard;
