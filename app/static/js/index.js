/* Home: provider presets, job form, recent builds. */

const form = document.getElementById('job-form');
const providerSelect = document.getElementById('provider');
const modelInput = document.getElementById('model');
const modelList = document.getElementById('model-options');
const baseUrlInput = document.getElementById('base_url');
const keyHint = document.getElementById('key-hint');
const errorBox = document.getElementById('form-error');
const submitButton = document.getElementById('submit-btn');
const jobsList = document.getElementById('jobs-list');
const activeCount = document.getElementById('active-count');
const buildHint = document.getElementById('build-hint');

function updateBuildHint() {
  const mode = document.querySelector('input[name="mode"]:checked');
  const label = mode && mode.value === 'unrestricted' ? 'not polite' : 'polite';
  buildHint.textContent = `${modelInput.value || 'model'} · ${label} · 1-page proof run`;
}

for (const input of document.querySelectorAll('input[name="mode"]')) {
  input.addEventListener('change', updateBuildHint);
}
modelInput.addEventListener('input', updateBuildHint);

const STATUS_LABEL = {
  queued: 'queued',
  discovering: 'probing',
  generating: 'generating',
  testing: 'running',
  done: 'done',
  failed: 'failed',
};

let presets = {};
let hasEnvKey = false;

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}

function hideError() {
  errorBox.hidden = true;
}

function fillModels(models) {
  modelList.replaceChildren();
  for (const name of models) {
    const option = document.createElement('option');
    option.value = name;
    modelList.append(option);
  }
}

function updateKeyHint() {
  const preset = presets[providerSelect.value] || {};
  if (hasEnvKey) {
    keyHint.textContent =
      'API key found in .env — leave the field empty to use it for this build.';
  } else if (preset.requires_key === false) {
    keyHint.textContent = 'No key required for this provider, for example a local model.';
  } else {
    keyHint.textContent = 'Held in memory for this job only. Never written to the database.';
  }
}

function applyPreset(name, { keepValues = false } = {}) {
  const preset = presets[name];
  if (!preset) return;
  if (!keepValues) {
    baseUrlInput.value = preset.base_url || '';
    modelInput.value = preset.models[0] || '';
  }
  fillModels(preset.models || []);
  updateKeyHint();
}

async function loadProviders() {
  const response = await fetch('/api/providers');
  const data = await response.json();
  presets = data.presets || {};

  providerSelect.replaceChildren();
  for (const [key, preset] of Object.entries(presets)) {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = preset.label;
    providerSelect.append(option);
  }
  providerSelect.value = data.defaults.provider;
  applyPreset(data.defaults.provider);
  baseUrlInput.value = data.defaults.base_url || baseUrlInput.value;
  modelInput.value = data.defaults.model || modelInput.value;
  updateBuildHint();

  hasEnvKey = Boolean(data.has_env_key);
  updateKeyHint();
}

providerSelect.addEventListener('change', () => {
  applyPreset(providerSelect.value);
  updateBuildHint();
});

function readProxies(raw) {
  const text = raw.trim();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function payloadFromForm() {
  const mode = document.querySelector('input[name="mode"]:checked');
  return {
    url: document.getElementById('url').value.trim(),
    provider: providerSelect.value,
    base_url: baseUrlInput.value.trim(),
    model: modelInput.value.trim(),
    api_key: document.getElementById('api_key').value.trim(),
    proxies: readProxies(document.getElementById('proxies').value),
    mode: mode ? mode.value : 'polite',
    fields: document.getElementById('fields').value.trim(),
    instructions: document.getElementById('instructions').value.trim(),
  };
}

function readError(payload, fallback) {
  if (!payload) return fallback;
  if (typeof payload.detail === 'string') return payload.detail;
  if (Array.isArray(payload.detail) && payload.detail.length) {
    const first = payload.detail[0];
    if (typeof first === 'string') return first;
    if (first && first.msg) return first.msg;
  }
  return fallback;
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  hideError();

  const payload = payloadFromForm();
  if (!payload.url) {
    showError('Paste the URL you want to scrape.');
    return;
  }
  if (!payload.model) {
    showError('Pick a model.');
    return;
  }

  submitButton.disabled = true;
  submitButton.textContent = 'Starting…';
  try {
    const response = await fetch('/api/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      showError(readError(data, 'Could not start the build.'));
      return;
    }
    const job = await response.json();
    window.location.assign(`/jobs/${job.id}`);
  } catch {
    showError('Network error — is the server still running?');
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = 'Generate scraper';
  }
});

function formatCost(value) {
  return value ? `$${Number(value).toFixed(4)}` : '$0.0000';
}

function formatTime(seconds) {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function renderJobs(jobs) {
  if (!jobs.length) {
    jobsList.innerHTML =
      '<p class="muted small empty">No builds yet. Start with a small public page.</p>';
    return;
  }
  jobsList.replaceChildren();
  for (const job of jobs) {
    const card = document.createElement('a');
    card.className = 'job-card';
    card.href = `/jobs/${job.id}`;

    const main = document.createElement('div');
    main.className = 'job-card-main';
    const url = document.createElement('div');
    url.className = 'job-card-url';
    url.textContent = job.url;
    const meta = document.createElement('div');
    meta.className = 'job-card-meta';
    const bits = [job.model, formatTime(job.created_at)];
    if (job.summary && job.summary.mapped_count) bits.push(`${job.summary.mapped_count} records`);
    if (job.status === 'failed') bits.push('failed');
    meta.textContent = bits.join(' · ');
    main.append(url, meta);

    const side = document.createElement('div');
    side.className = 'job-card-side';
    const pill = document.createElement('span');
    pill.className = `pill ${job.status}`;
    pill.textContent = STATUS_LABEL[job.status] || job.status;
    side.append(pill);

    card.append(main, side);
    jobsList.append(card);
  }
}

async function loadJobs() {
  try {
    const response = await fetch('/api/jobs?limit=8');
    const data = await response.json();
    renderJobs(data.jobs || []);
    activeCount.textContent = data.active ? `${data.active} running` : '';
  } catch {
    /* keep the page usable if the API hiccups */
  }
}

loadProviders().catch(() => {
  keyHint.textContent = 'Could not load provider presets — check the server log.';
});
loadJobs();
setInterval(loadJobs, 5000);
