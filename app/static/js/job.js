/* Job page: SSE log, status, files, mapper preview. */

const root = document.querySelector('.job');
if (root) init(root);

function init(root) {
  const jobId = root.dataset.jobId;
  const statusPill = document.getElementById('status-pill');
  const jobModel = document.getElementById('job-model');
  const jobUrl = document.getElementById('job-url');
  const jobMeta = document.getElementById('job-meta');
  const timeline = [...document.querySelectorAll('#timeline li')];
  const errorBox = document.getElementById('error-box');
  const logEl = document.getElementById('log');
  const autoscroll = document.getElementById('autoscroll');
  const previewEl = document.getElementById('preview');
  const previewMeta = document.getElementById('preview-meta');
  const fillRates = document.getElementById('fill-rates');
  const tabsEl = document.getElementById('tabs');
  const fileView = document.getElementById('file-view');
  const filesMeta = document.getElementById('files-meta');
  const download = document.getElementById('download');
  const strategyChip = document.getElementById('strategy-chip');
  const statsEl = document.getElementById('stats');

  const READABLE = new Set([
    'scraper.py', 'mapper.py', 'runner.sh', 'README.md', 'requirements.txt', 'NOTES.md',
    'probe.json', 'agent_trace.json', 'raw.jsonl', 'mapped.jsonl',
  ]);
  const STATUS_LABEL = {
    queued: 'queued',
    discovering: 'probing',
    generating: 'generating',
    testing: 'running',
    done: 'done',
    failed: 'failed',
  };
  const STEP_ORDER = ['discovering', 'generating', 'testing', 'done'];

  let terminal = false;
  let selectedFile = null;
  let currentJob = null;

  function appendLog(event) {
    const line = document.createElement('span');
    line.className = `log-line ${event.level || 'info'}`;
    const ts = document.createElement('span');
    ts.className = 'ts';
    ts.textContent = new Date(event.ts * 1000).toLocaleTimeString();
    const text = document.createElement('span');
    text.textContent = event.message;
    line.append(ts, text);
    logEl.append(line);
    if (autoscroll.checked) logEl.scrollTop = logEl.scrollHeight;
  }

  const STRATEGY_LABEL = {
    http: 'plain HTTP',
    api: 'captured API',
    browser: 'headless browser',
  };

  // Which step a failed job died in: no model call means the probe stopped it.
  function failedStep(job) {
    if (!job || !job.attempts) return 0;
    const files = job.files || [];
    return files.some((file) => file.name === 'raw.jsonl') ? 2 : 1;
  }

  function setStatus(status, job) {
    statusPill.className = `pill ${status}`;
    statusPill.textContent = STATUS_LABEL[status] || status;
    const index = STEP_ORDER.indexOf(status);
    const failedAt = status === 'failed' ? failedStep(job) : -1;
    timeline.forEach((item, i) => {
      item.classList.remove('active', 'complete', 'failed');
      if (status === 'failed') {
        if (i < failedAt) item.classList.add('complete');
        else if (i === failedAt) item.classList.add('failed');
        return;
      }
      if (index === -1) return;
      if (i < index) item.classList.add('complete');
      else if (i === index) item.classList.add(status === 'done' ? 'complete' : 'active');
    });
  }

  function formatBytes(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function renderStats(job) {
    const summary = job.summary || {};
    if (job.status !== 'done' || !summary.mapped_count) {
      statsEl.hidden = true;
      return;
    }
    const tiles = [
      ['Records', `${summary.mapped_count}`, `${summary.raw_count} raw`],
      ['Source', summary.source || '—', summary.strategy ? `via ${STRATEGY_LABEL[summary.strategy]}` : ''],
      ['Time', `${summary.seconds}s`, summary.render_seconds ? `render ${summary.render_seconds}s` : `sandbox ${summary.sandbox_seconds}s`],
      ['Cost', `$${(job.cost_usd || 0).toFixed(4)}`, `${job.attempts} ${job.attempts === 1 ? 'round' : 'rounds'}`],
    ];
    statsEl.replaceChildren();
    for (const [label, value, note] of tiles) {
      const tile = document.createElement('div');
      tile.className = 'stat';
      const name = document.createElement('span');
      name.className = 'stat-label';
      name.textContent = label;
      const big = document.createElement('strong');
      big.className = 'stat-value';
      big.textContent = value;
      const small = document.createElement('span');
      small.className = 'stat-note';
      small.textContent = note;
      tile.append(name, big, small);
      statsEl.append(tile);
    }
    statsEl.hidden = false;
  }

  function renderHeader(job) {
    jobUrl.textContent = job.url;
    jobModel.textContent = job.model;
    const summary = job.summary || {};
    const bits = [];
    if (job.status !== 'done') {
      if (summary.seconds) bits.push(`${summary.seconds}s`);
      if (job.cost_usd) bits.push(`$${job.cost_usd.toFixed(4)}`);
      if (job.attempts > 1) bits.push(`${job.attempts} attempts`);
    }
    if (summary.mode) bits.push(summary.mode === 'unrestricted' ? 'not polite' : 'polite');
    jobMeta.textContent = bits.join(' · ');
    const kind = summary.strategy === 'browser'
      ? 'browser'
      : summary.strategy === 'api' || summary.source === 'captured api' ? 'api' : null;
    if (kind) {
      strategyChip.hidden = false;
      strategyChip.className = `chip chip-${kind}`;
      strategyChip.textContent = kind === 'browser' ? 'headless browser' : 'captured API';
    } else {
      strategyChip.hidden = true;
    }
    renderStats(job);
    setStatus(job.status, job);

    if (job.error) {
      errorBox.hidden = false;
      errorBox.textContent = job.error;
    } else {
      errorBox.hidden = true;
    }

    if (job.files && job.files.some((file) => file.name === 'scraper.py')) {
      download.setAttribute('href', `/api/jobs/${jobId}/download`);
      download.removeAttribute('aria-disabled');
    } else {
      download.setAttribute('href', '#');
      download.setAttribute('aria-disabled', 'true');
    }
  }

  function renderPreview(job) {
    const preview = job.preview;
    if (!preview || !preview.rows || !preview.rows.length) {
      if (job.status === 'done') {
        previewEl.innerHTML = '<p class="muted small empty">No rows in the preview.</p>';
      } else if (job.status === 'failed') {
        previewEl.innerHTML =
          '<p class="muted small empty">No preview: the build stopped before a successful run. ' +
          'The live log says why.</p>';
      }
      return;
    }
    previewMeta.textContent = `${preview.mapped_count} mapped / ${preview.raw_count} raw`;
    const columns = [...new Set(preview.rows.flatMap((row) => Object.keys(row)))].slice(0, 7);

    const table = document.createElement('table');
    const head = document.createElement('thead');
    const headRow = document.createElement('tr');
    for (const column of columns) {
      const th = document.createElement('th');
      th.textContent = column;
      headRow.append(th);
    }
    head.append(headRow);

    const body = document.createElement('tbody');
    for (const row of preview.rows) {
      const tr = document.createElement('tr');
      for (const column of columns) {
        const td = document.createElement('td');
        const value = row[column];
        const text = Array.isArray(value)
          ? value.join(', ').slice(0, 120)
          : value === null || value === undefined
            ? '—'
            : String(value).slice(0, 120);
        td.textContent = text;
        td.title = text;
        tr.append(td);
      }
      body.append(tr);
    }
    table.append(head, body);
    previewEl.replaceChildren(table);

    if (preview.fill_rates) {
      const rows = Object.entries(preview.fill_rates)
        .sort((a, b) => a[1] - b[1])
        .slice(0, 10);
      fillRates.replaceChildren();
      for (const [name, pct] of rows) {
        const row = document.createElement('div');
        row.className = 'fill-row';
        const label = document.createElement('span');
        label.className = 'name';
        label.textContent = name;
        const track = document.createElement('span');
        track.className = 'track';
        const fill = document.createElement('span');
        fill.className = `fill ${pct < 50 ? 'low' : pct < 90 ? 'mid' : ''}`;
        fill.style.width = `${pct}%`;
        track.append(fill);
        const value = document.createElement('span');
        value.className = 'pct';
        value.textContent = `${pct}%`;
        row.append(label, track, value);
        fillRates.append(row);
      }
    }
  }

  async function openFile(name) {
    selectedFile = name;
    for (const tab of tabsEl.children) {
      tab.classList.toggle('active', tab.dataset.name === name);
    }
    fileView.textContent = 'Loading…';
    try {
      const response = await fetch(`/api/jobs/${jobId}/files/${name}`);
      if (!response.ok) {
        fileView.textContent = 'Not available yet.';
        return;
      }
      fileView.textContent = await response.text();
      fileView.scrollTop = 0;
    } catch {
      fileView.textContent = 'Could not load that file.';
    }
  }

  function renderFiles(job) {
    const files = (job.files || []).filter((file) => READABLE.has(file.name));
    filesMeta.textContent = files.length
      ? `${files.length} files · ${formatBytes(files.reduce((sum, file) => sum + file.size, 0))}`
      : 'waiting for the first generation';
    if (!files.length) return;

    tabsEl.replaceChildren();
    for (const file of files) {
      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'tab';
      tab.dataset.name = file.name;
      tab.textContent = file.name;
      tab.addEventListener('click', () => openFile(file.name));
      tabsEl.append(tab);
    }

    const preferred = files.find((file) => file.name === 'scraper.py') || files[0];
    const target = selectedFile && files.some((file) => file.name === selectedFile)
      ? selectedFile
      : preferred.name;
    if (target !== selectedFile) openFile(target);
  }

  async function loadJob() {
    try {
      const response = await fetch(`/api/jobs/${jobId}`);
      if (!response.ok) return;
      const job = await response.json();
      currentJob = job;
      renderHeader(job);
      renderPreview(job);
      renderFiles(job);
      if (job.status === 'done' || job.status === 'failed') {
        terminal = true;
      }
    } catch {
      /* ignore transient errors */
    }
  }

  const source = new EventSource(`/api/jobs/${jobId}/events`);
  source.onmessage = (event) => {
    try {
      appendLog(JSON.parse(event.data));
    } catch {
      /* ignore malformed frames */
    }
  };
  source.addEventListener('end', () => {
    source.close();
    loadJob();
  });

  loadJob();
  const timer = setInterval(() => {
    loadJob();
    if (terminal) clearInterval(timer);
  }, 2500);
}
