const $ = (id) => document.getElementById(id);
const post = async (url, body = {}) => {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || 'Request failed');
  return data;
};

function notify(message, error = false) {
  $('capture-message').textContent = message;
  $('capture-message').style.color = error ? '#a84432' : '';
}

function renderPrices(prices) {
  const list = $('price-list');
  list.innerHTML = '';
  Object.entries(prices).forEach(([name, price]) => {
    const row = document.createElement('div');
    row.className = 'price-row';
    row.innerHTML = `<label>${name}<input class="price-input" data-name="${name}" type="number" min="0" step="0.01" value="${price}"></label>`;
    list.appendChild(row);
  });
  if (!Object.keys(prices).length) list.innerHTML = '<p class="muted">Add products to prices.json first.</p>';
}

document.querySelectorAll('.mode-button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.mode-button').forEach((item) => item.classList.remove('active'));
    button.classList.add('active');
    document.getElementById(button.dataset.target).scrollIntoView({behavior: 'smooth', block: 'start'});
  });
});

function renderBill(billing) {
  $('total').textContent = Number(billing.total || 0).toFixed(2);
  $('total-panel').textContent = Number(billing.total || 0).toFixed(2);
  $('billing-tag').textContent = billing.running ? 'LIVE' : 'OFFLINE';
  $('billing-tag').classList.toggle('live', billing.running);
  $('billing-tag-panel').textContent = billing.running ? 'LIVE' : 'OFFLINE';
  $('billing-tag-panel').classList.toggle('live', billing.running);
  const items = $('bill-items');
  items.innerHTML = '';
  if (!Object.keys(billing.items || {}).length) {
    items.innerHTML = '<p class="muted">No tracked items yet.</p>';
    return;
  }
  Object.entries(billing.items).forEach(([name, count]) => {
    const line = document.createElement('div');
    line.className = 'bill-line';
    line.innerHTML = `<span>${name}</span><strong>x${count}</strong>`;
    items.appendChild(line);
  });
}

async function refresh() {
  try {
    const data = await (await fetch('/api/stats')).json();
    $('network-status').textContent = data.camera.connected ? 'Camera online' : 'Waiting for camera';
    $('camera-dot').style.background = data.camera.connected ? '#267457' : '#f1c85b';
    $('capture-count').textContent = data.raw_images;
    $('capture-count-secondary').textContent = data.raw_images;
    renderBill(data.billing);
    renderPrices(data.prices);
    const training = data.training;
    $('training-tag').textContent = training.running ? 'RUNNING' : training.return_code === 0 ? 'COMPLETE' : 'IDLE';
    $('training-tag').classList.toggle('live', training.running);
    if (training.logs.length) $('training-log').textContent = training.logs.join('\n');
  } catch (error) {
    $('network-status').textContent = 'Dashboard error';
  }
}

$('connect-camera').addEventListener('click', async () => {
  try { await post('/api/camera/start', {url: $('stream-url').value}); notify('Camera connection requested.'); }
  catch (error) { notify(error.message, true); }
});
$('capture-button').addEventListener('click', async () => {
  try { const data = await post('/api/capture'); notify(`Saved ${data.file}`); refresh(); }
  catch (error) { notify(error.message, true); }
});

$('capture-label-button').addEventListener('click', async () => {
  try {
    const data = await post('/api/capture-label', {model: $('model-path').value});
    notify(data.labels.length ? `AI labelled: ${data.labels.join(', ')}` : 'Photo saved; no object passed the confidence threshold.');
    refresh();
  } catch (error) { notify(error.message, true); }
});

async function startTraining(name) {
  try {
    await post('/api/training/start', {data: $('data-path').value, model: $('base-model').value, epochs: $('epochs').value, batch: $('batch').value, image_size: 640, name});
    $('training-tag').textContent = 'RUNNING';
  } catch (error) { $('training-log').textContent = error.message; }
}
$('train-button').addEventListener('click', () => startTraining('product_detector_saved'));
$('retrain-button').addEventListener('click', () => startTraining('product_detector_retrain'));
$('start-billing').addEventListener('click', async () => {
  try { await post('/api/billing/start', {model: $('model-path').value, confidence: $('confidence').value}); refresh(); }
  catch (error) { notify(error.message, true); }
});
$('stop-billing').addEventListener('click', async () => { await post('/api/billing/stop'); refresh(); });
$('reset-billing').addEventListener('click', async () => { await post('/api/billing/reset'); refresh(); });
$('confidence').addEventListener('input', (event) => { $('confidence-output').textContent = event.target.value; });
$('save-prices').addEventListener('click', async () => {
  const prices = {};
  document.querySelectorAll('.price-input').forEach((input) => { prices[input.dataset.name] = Number(input.value); });
  try { await post('/api/prices', {prices}); notify('Price book saved.'); }
  catch (error) { notify(error.message, true); }
});
setInterval(refresh, 1500);
refresh();
