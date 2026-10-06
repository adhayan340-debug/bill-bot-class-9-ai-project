const $ = (id) => document.getElementById(id);
const post = async (url, body = {}) => {
  const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok || data.ok === false) throw new Error(data.error || 'Request failed');
  return data;
};

function show(id, message, error = false) {
  const element = $(id);
  if (element) {
    element.textContent = message;
    element.style.color = error ? '#a84432' : '';
  }
}

function renderBill(billing) {
  if (!$('total')) return;
  $('total').textContent = Number(billing.total || 0).toFixed(2);
  $('billing-tag').textContent = billing.running ? 'LIVE' : 'OFFLINE';
  $('billing-tag').classList.toggle('live', billing.running);
  if ($('billing-message') && billing.error) show('billing-message', `Detection error: ${billing.error}`, true);
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

function renderTraining(training) {
  if (!$('training-tag')) return;
  const logs = training.logs || [];
  const logText = logs.join('\n');
  const target = Math.max(1, Number(training.total_epochs || $('epochs')?.value || 50));
  const matches = [...logText.matchAll(/^\s*(\d+)\s+[\d.]+\s+/gm)];
  const current = Number(training.current_epoch || (matches.length ? Number(matches[matches.length - 1][1]) : 0));
  const complete = training.return_code === 0 && !training.running;
  const percent = complete ? 100 : Number(training.progress_percent ?? Math.min(99, Math.round((current / target) * 100)));
  $('training-tag').textContent = training.running ? 'RUNNING' : complete ? 'COMPLETE' : training.return_code !== null ? 'STOPPED' : 'IDLE';
  $('training-tag').classList.toggle('live', training.running);
  if ($('training-progress-fill')) $('training-progress-fill').style.width = `${percent}%`;
  if ($('training-progress-percent')) $('training-progress-percent').textContent = `${percent}%`;
  if ($('training-progress-epoch')) $('training-progress-epoch').textContent = `${complete ? target : current} / ${target} epochs`;
  if ($('training-progress-label')) $('training-progress-label').textContent = training.running ? 'Training in progress' : complete ? 'Training complete' : current ? 'Training paused or stopped' : 'Ready to train';
  if ($('training-progress-time')) $('training-progress-time').textContent = training.started_at ? `Started ${training.started_at.replace('T', ' ')}` : 'Waiting';
  if (logs.length) $('training-log').textContent = logText;
}

async function refresh() {
  try {
    const data = await (await fetch('/api/stats')).json();
    if ($('network-status')) $('network-status').textContent = data.camera.connected ? 'Camera online' : 'Waiting for camera';
    if ($('camera-dot')) $('camera-dot').style.background = data.camera.connected ? '#267457' : '#f1c85b';
    if ($('capture-count')) $('capture-count').textContent = data.raw_images;
    if ($('labelled-count')) $('labelled-count').textContent = data.labelled_images;
    renderBill(data.billing);
    renderTraining(data.training);
  } catch (error) {
    if ($('network-status')) $('network-status').textContent = 'Dashboard error';
  }
}
if ($('connect-camera')) $('connect-camera').addEventListener('click', async () => {
  try { await post('/api/camera/start', {url: $('stream-url').value}); show('capture-message', 'Camera connection requested.'); }
  catch (error) { show('capture-message', error.message, true); }
});
if ($('capture-button')) $('capture-button').addEventListener('click', async () => {
  try { const data = await post('/api/capture'); show('capture-message', `Saved ${data.file}`); refresh(); }
  catch (error) { show('capture-message', error.message, true); }
});
if ($('capture-label-button')) $('capture-label-button').addEventListener('click', async () => {
  try {
    const product = $('product-label').value;
    if (!product) throw new Error('Choose a product label first. Add products in Prices if the list is empty.');
    const data = await post('/api/capture-label', {model: $('label-model').value, product});
    const warning = data.warning ? ` ${data.warning}` : '';
    show('capture-message', `Saved ${data.product} with ${data.boxes} training box${data.boxes === 1 ? '' : 'es'}.${warning}`);
    refresh();
  } catch (error) { show('capture-message', error.message, true); }
});

async function startTraining(name) {
  try {
    await post('/api/training/start', {data: $('data-path').value, model: $('base-model').value, epochs: $('epochs').value, batch: $('batch').value, image_size: 640, name});
    $('training-tag').textContent = 'RUNNING';
  } catch (error) { $('training-log').textContent = error.message; }
}
if ($('train-button')) $('train-button').addEventListener('click', () => startTraining('product_detector_saved'));
if ($('retrain-button')) $('retrain-button').addEventListener('click', () => startTraining('product_detector_retrain'));
if ($('start-billing')) $('start-billing').addEventListener('click', async () => {
  try { await post('/api/billing/start', {model: $('model-path').value, confidence: $('confidence').value}); $('bill-receipt').hidden = true; show('billing-message', 'Bill mode is live.'); refresh(); }
  catch (error) { show('billing-message', error.message, true); }
});
if ($('stop-billing')) $('stop-billing').addEventListener('click', async () => { await post('/api/billing/stop'); refresh(); });
if ($('reset-billing')) $('reset-billing').addEventListener('click', async () => { await post('/api/billing/reset'); $('bill-receipt').hidden = true; show('billing-message', 'New customer session ready.'); refresh(); });
if ($('finalize-billing')) $('finalize-billing').addEventListener('click', async () => {
  if (!window.confirm('Is the lid closed? Confirm to create the bill.')) return;
  try {
    const response = await post('/api/billing/finalize', {lid_closed: true});
    $('bill-receipt').textContent = response.receipt;
    $('bill-receipt').hidden = false;
    show('billing-message', 'Bill created.');
    refresh();
  } catch (error) {
    show('billing-message', error.message, true);
  }
});
if ($('confidence')) $('confidence').addEventListener('input', (event) => { $('confidence-output').textContent = event.target.value; });
if ($('save-prices')) $('save-prices').addEventListener('click', async () => {
  const prices = {};
  document.querySelectorAll('.price-input').forEach((input) => { prices[input.dataset.name] = Number(input.value); });
  try { await post('/api/prices', {prices}); show('prices-message', 'Price book saved.'); }
  catch (error) { show('prices-message', error.message, true); }
});
if ($('add-product')) $('add-product').addEventListener('click', () => {
  const name = $('new-product-name').value.trim();
  const price = Number($('new-product-price').value);
  if (!name || !Number.isFinite(price) || price < 0) {
    show('prices-message', 'Enter a product name and a valid price.', true);
    return;
  }
  if (document.querySelector(`.price-input[data-name="${CSS.escape(name)}"]`)) {
    show('prices-message', 'That product already exists.', true);
    return;
  }
  $('empty-prices')?.remove();
  const row = document.createElement('div');
  row.className = 'price-row';
  row.innerHTML = `<label>${name}<input class="price-input" data-name="${name}" type="number" min="0" step="0.01" value="${price}"></label>`;
  $('price-list').appendChild(row);
  $('new-product-name').value = '';
  $('new-product-price').value = '';
  show('prices-message', `${name} added. Save the price book.`);
});

const datasetState = {items: [], classes: [], selected: null, boxes: [], image: null, drag: null};
const annotationCanvas = $('annotation-canvas');

function datasetMessage(message, error = false) {
  if (!$('dataset-status')) return;
  $('dataset-status').textContent = message;
  $('dataset-status').classList.toggle('error-text', error);
}

function boxColor(index) {
  return ['#e45e49', '#187b62', '#d09419', '#376ac2', '#9c4c84'][index % 5];
}

function renderAnnotationCanvas(previewBox = null) {
  if (!annotationCanvas) return;
  const context = annotationCanvas.getContext('2d');
  context.clearRect(0, 0, annotationCanvas.width, annotationCanvas.height);
  if (!datasetState.image) return;
  context.drawImage(datasetState.image, 0, 0);
  const boxes = previewBox ? [...datasetState.boxes, previewBox] : datasetState.boxes;
  boxes.forEach((box, index) => {
    const color = boxColor(box.class_id);
    const width = box.x2 - box.x1;
    const height = box.y2 - box.y1;
    context.fillStyle = `${color}2b`;
    context.fillRect(box.x1, box.y1, width, height);
    context.strokeStyle = color;
    context.lineWidth = Math.max(2, annotationCanvas.width / 500);
    context.strokeRect(box.x1, box.y1, width, height);
    context.font = `600 ${Math.max(13, annotationCanvas.width / 65)}px Manrope, sans-serif`;
    context.fillStyle = color;
    context.fillText(String(index + 1), box.x1 + 4, Math.max(16, box.y1 - 5));
  });
}

function renderAnnotationBoxes() {
  const list = $('annotation-boxes');
  if (!list) return;
  list.replaceChildren();
  if (!datasetState.boxes.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = 'No boxes yet. Drag on the image to add one.';
    list.appendChild(empty);
    return;
  }
  datasetState.boxes.forEach((box, index) => {
    const row = document.createElement('div');
    row.className = 'annotation-row';
    const number = document.createElement('strong');
    number.textContent = `Box ${index + 1}`;
    const classSelect = document.createElement('select');
    classSelect.setAttribute('aria-label', `Class for box ${index + 1}`);
    datasetState.classes.forEach((item) => {
      const option = document.createElement('option');
      option.value = item.id;
      option.textContent = item.name;
      option.selected = Number(box.class_id) === item.id;
      classSelect.appendChild(option);
    });
    classSelect.addEventListener('change', () => {
      box.class_id = Number(classSelect.value);
      renderAnnotationCanvas();
    });
    const remove = document.createElement('button');
    remove.className = 'button ghost';
    remove.type = 'button';
    remove.textContent = 'Remove';
    remove.addEventListener('click', () => {
      datasetState.boxes.splice(index, 1);
      renderAnnotationBoxes();
      renderAnnotationCanvas();
    });
    row.append(number, classSelect, remove);
    list.appendChild(row);
  });
}

function renderDatasetItems() {
  const list = $('dataset-items');
  if (!list) return;
  list.replaceChildren();
  const filter = $('dataset-filter').value;
  const items = datasetState.items.filter((item) => filter === 'all' || item.split === filter);
  if (!items.length) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.textContent = 'No images in this view.';
    list.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    const row = document.createElement('div');
    row.className = `dataset-item${datasetState.selected?.filename === item.filename && datasetState.selected?.split === item.split ? ' selected' : ''}`;
    const select = document.createElement('button');
    select.className = 'dataset-item-select';
    select.type = 'button';
    select.addEventListener('click', () => selectDatasetItem(item));
    const thumbnail = document.createElement('img');
    thumbnail.src = item.url;
    thumbnail.alt = '';
    const details = document.createElement('span');
    details.className = 'dataset-item-details';
    const filename = document.createElement('strong');
    filename.textContent = item.filename;
    const state = document.createElement('small');
    state.textContent = item.split === 'raw' ? 'Raw capture' : item.annotated ? 'Labeled' : 'Needs labels';
    details.append(filename, state);
    select.append(thumbnail, details);
    row.appendChild(select);
    if (item.split === 'raw') {
      const add = document.createElement('button');
      add.className = 'button ghost dataset-import';
      add.type = 'button';
      add.textContent = 'Add';
      add.title = 'Copy this capture into the selected split';
      add.addEventListener('click', async () => {
        try {
          await post('/api/dataset/import-raw', {filename: item.filename, split: $('dataset-upload-split').value});
          datasetMessage(`Added ${item.filename} to ${$('dataset-upload-split').value}.`);
          await loadDataset();
        } catch (error) { datasetMessage(error.message, true); }
      });
      row.appendChild(add);
    }
    list.appendChild(row);
  });
}

async function selectDatasetItem(item) {
  datasetState.selected = item;
  datasetState.boxes = [];
  datasetState.image = null;
  $('annotation-filename').textContent = item.filename;
  $('annotation-split').textContent = item.split.toUpperCase();
  $('annotation-empty').hidden = true;
  $('dataset-save-labels').disabled = item.split === 'raw';
  $('dataset-delete-image').disabled = false;
  renderDatasetItems();
  const image = new Image();
  image.onload = async () => {
    datasetState.image = image;
    annotationCanvas.width = image.naturalWidth;
    annotationCanvas.height = image.naturalHeight;
    annotationCanvas.hidden = false;
    if (item.split !== 'raw') {
      try {
        const response = await fetch(`/api/dataset/annotations/${encodeURIComponent(item.split)}/${encodeURIComponent(item.filename)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Could not load labels');
        datasetState.boxes = data.boxes;
      } catch (error) { datasetMessage(error.message, true); }
    }
    renderAnnotationBoxes();
    renderAnnotationCanvas();
  };
  image.onerror = () => datasetMessage('Could not load this image.', true);
  image.src = item.url;
}

async function loadDataset() {
  if (!annotationCanvas) return;
  try {
    const response = await fetch('/api/dataset');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not load dataset');
    datasetState.items = data.items;
    datasetState.classes = data.classes;
    $('dataset-count').textContent = `${data.items.filter((item) => item.split !== 'raw').length} IMAGES`;
    renderDatasetItems();
    const current = datasetState.items.find((item) => item.split === datasetState.selected?.split && item.filename === datasetState.selected?.filename);
    if (current) await selectDatasetItem(current);
    else if (datasetState.items.length) await selectDatasetItem(datasetState.items[0]);
    else {
      datasetState.selected = null;
      datasetState.boxes = [];
      datasetState.image = null;
      annotationCanvas.hidden = true;
      $('annotation-empty').hidden = false;
      $('annotation-filename').textContent = 'Select an image';
      $('annotation-split').textContent = '';
      $('dataset-save-labels').disabled = true;
      $('dataset-delete-image').disabled = true;
      renderAnnotationBoxes();
    }
    datasetMessage(`${data.items.filter((item) => item.split !== 'raw').length} dataset images · ${data.classes.length} product classes`);
  } catch (error) { datasetMessage(error.message, true); }
}

if ($('dataset-filter')) $('dataset-filter').addEventListener('change', renderDatasetItems);
if ($('dataset-upload-button')) $('dataset-upload-button').addEventListener('click', async () => {
  const files = $('dataset-upload-files').files;
  if (!files.length) { datasetMessage('Choose one or more image files first.', true); return; }
  const form = new FormData();
  form.append('split', $('dataset-upload-split').value);
  [...files].forEach((file) => form.append('files', file));
  try {
    const response = await fetch('/api/dataset/upload', {method: 'POST', body: form});
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || 'Upload failed');
    $('dataset-upload-files').value = '';
    datasetMessage(`Added ${data.added.length} image${data.added.length === 1 ? '' : 's'}. Draw and save their labels.`);
    await loadDataset();
  } catch (error) { datasetMessage(error.message, true); }
});
if (annotationCanvas) {
  annotationCanvas.addEventListener('pointerdown', (event) => {
    if (!datasetState.image || datasetState.selected?.split === 'raw' || !datasetState.classes.length) return;
    const bounds = annotationCanvas.getBoundingClientRect();
    const scaleX = annotationCanvas.width / bounds.width;
    const scaleY = annotationCanvas.height / bounds.height;
    datasetState.drag = {x: (event.clientX - bounds.left) * scaleX, y: (event.clientY - bounds.top) * scaleY};
    annotationCanvas.setPointerCapture(event.pointerId);
  });
  annotationCanvas.addEventListener('pointermove', (event) => {
    if (!datasetState.drag) return;
    const bounds = annotationCanvas.getBoundingClientRect();
    const x = (event.clientX - bounds.left) * annotationCanvas.width / bounds.width;
    const y = (event.clientY - bounds.top) * annotationCanvas.height / bounds.height;
    renderAnnotationCanvas({class_id: Number($('dataset-class').value), x1: Math.min(datasetState.drag.x, x), y1: Math.min(datasetState.drag.y, y), x2: Math.max(datasetState.drag.x, x), y2: Math.max(datasetState.drag.y, y)});
  });
  annotationCanvas.addEventListener('pointerup', (event) => {
    if (!datasetState.drag) return;
    const bounds = annotationCanvas.getBoundingClientRect();
    const x = (event.clientX - bounds.left) * annotationCanvas.width / bounds.width;
    const y = (event.clientY - bounds.top) * annotationCanvas.height / bounds.height;
    const box = {class_id: Number($('dataset-class').value), x1: Math.min(datasetState.drag.x, x), y1: Math.min(datasetState.drag.y, y), x2: Math.max(datasetState.drag.x, x), y2: Math.max(datasetState.drag.y, y)};
    datasetState.drag = null;
    if (box.x2 - box.x1 >= 4 && box.y2 - box.y1 >= 4) {
      datasetState.boxes.push(box);
      renderAnnotationBoxes();
      datasetMessage('Box added. Save labels when the annotation is ready.');
    }
    renderAnnotationCanvas();
  });
}
if ($('dataset-save-labels')) $('dataset-save-labels').addEventListener('click', async () => {
  if (!datasetState.selected || datasetState.selected.split === 'raw') return;
  try {
    const data = await post('/api/dataset/annotations', {split: datasetState.selected.split, filename: datasetState.selected.filename, boxes: datasetState.boxes});
    datasetMessage(`Saved ${data.boxes} box${data.boxes === 1 ? '' : 'es'} for ${datasetState.selected.filename}.`);
    await loadDataset();
  } catch (error) { datasetMessage(error.message, true); }
});
if ($('dataset-delete-image')) $('dataset-delete-image').addEventListener('click', async () => {
  if (!datasetState.selected) return;
  const deletingRaw = datasetState.selected.split === 'raw';
  const confirmation = deletingRaw ? `Delete raw capture ${datasetState.selected.filename}?` : `Delete ${datasetState.selected.filename} and its label file?`;
  if (!window.confirm(confirmation)) return;
  try {
    const response = await fetch(`/api/dataset/${encodeURIComponent(datasetState.selected.split)}/${encodeURIComponent(datasetState.selected.filename)}`, {method: 'DELETE'});
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || 'Delete failed');
    datasetState.selected = null;
    await loadDataset();
    datasetMessage(deletingRaw ? 'Raw capture deleted.' : 'Image and paired label deleted.');
  } catch (error) { datasetMessage(error.message, true); }
});
if (annotationCanvas) loadDataset();

setInterval(refresh, 1500);
refresh();
