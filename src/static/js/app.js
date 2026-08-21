document.addEventListener('DOMContentLoaded', () => {
  // Initialize dropzones if data-input-id exists
  document.querySelectorAll('.drop-zone').forEach(zone => {
    const inputId = zone.dataset.inputId;
    if (inputId) {
      const callbackName = zone.dataset.callback;
      const callback = callbackName && typeof window[callbackName] === 'function' ? window[callbackName] : null;
      initDropZone(zone.id, inputId, callback);
    }
  });
});

// Toast Notifications
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container') || createToastContainer();
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `<div class="toast-body">${message}</div>`;
  
  container.appendChild(toast);
  
  setTimeout(() => {
    toast.style.animation = 'fadeOut 0.3s forwards';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

function createToastContainer() {
  const container = document.createElement('div');
  container.id = 'toast-container';
  container.className = 'toast-container';
  document.body.appendChild(container);
  return container;
}

// Modals
function openModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.add('active');
  }
}

function closeModal(modalId) {
  const modal = document.getElementById(modalId);
  if (modal) {
    modal.classList.remove('active');
  }
}

// Close modals when clicking overlay
document.addEventListener('click', (e) => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.remove('active');
  }
});

// Drop Zone Logic
function initDropZone(dropZoneId, fileInputId, previewCallback) {
  const dropZone = document.getElementById(dropZoneId);
  const fileInput = document.getElementById(fileInputId);
  
  if (!dropZone || !fileInput) return;

  if (previewCallback) {
    dropZone._callback = previewCallback;
  }

  // Prevent multiple bindings
  if (dropZone._initialized) return;
  dropZone._initialized = true;

  dropZone.addEventListener('click', (e) => {
    if (e.target !== fileInput) {
      fileInput.click();
    }
  });

  dropZone.addEventListener('dragover', (e) => {
    e.preventDefault();
    dropZone.classList.add('dragover');
  });

  ['dragleave', 'dragend'].forEach(type => {
    dropZone.addEventListener(type, () => {
      dropZone.classList.remove('dragover');
    });
  });

  dropZone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropZone.classList.remove('dragover');
    
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      handleFileSelection(fileInput.files[0], dropZone, dropZone._callback || previewCallback);
    }
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files.length) {
      handleFileSelection(fileInput.files[0], dropZone, dropZone._callback || previewCallback);
    }
  });
}

function handleFileSelection(file, dropZone, previewCallback) {
  const textEl = dropZone.querySelector('.drop-zone-text');
  if (textEl) {
    textEl.textContent = `Selected: ${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
  }
  const cb = dropZone._callback || previewCallback;
  if (typeof cb === 'function') {
    cb(file);
  }
}

// AJAX Form Submissions
async function submitForm(formId, url, method = 'POST', successCallback) {
  const form = document.getElementById(formId);
  if (!form) return;

  const formData = new FormData(form);
  const submitBtn = form.querySelector('[type="submit"]');
  const originalText = submitBtn ? submitBtn.textContent : 'Submit';

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Processing...';
  }

  try {
    let fetchOptions = {
      method: method,
      body: formData
    };

    const response = await fetch(url, fetchOptions);
    const result = await response.json();

    if (response.ok && (result.success !== false)) {
      showToast(result.message || 'Success!', 'success');
      if (successCallback) successCallback(result);
    } else {
      showToast(result.error || result.message || 'An error occurred', 'error');
    }
  } catch (err) {
    showToast('Network error or server unavailable', 'error');
    console.error(err);
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = originalText;
    }
  }
}

// CSV Preview Logic
function previewCsv(file) {
  if (!file) return;

  const batchSizeInput = document.getElementById('batch_size');
  const batchSize = batchSizeInput ? batchSizeInput.value : 50;

  const formData = new FormData();
  formData.append('file', file);
  formData.append('batch_size', batchSize);

  fetch('/api/upload/preview', {
    method: 'POST',
    body: formData
  })
  .then(res => res.json())
  .then(data => {
    if (data.error) {
      showToast(data.error, 'error');
      return;
    }
    
    const previewSection = document.getElementById('preview-section');
    if (previewSection) {
      previewSection.style.display = 'block';
      const boardCount = data.boards ? data.boards.length : 0;
      document.getElementById('preview-stats').innerHTML = 
        `📊 <strong>${data.row_count} total pins</strong> across <strong>${boardCount} board(s)</strong>. ` +
        `Will be automatically split into <span style="color: var(--primary); font-weight: bold;">${data.batch_count} batch(es)</span> (max ${data.batch_size} pins per batch).`;
        
      const tbody = document.getElementById('preview-tbody');
      if (tbody && data.sample_rows) {
        tbody.innerHTML = '';
        data.sample_rows.forEach(row => {
          const title = row['Title'] || row['title'] || '';
          const board = row['Pinterest Board'] || row['board_name'] || row['board'] || '';
          const link = row['Link'] || row['link'] || '';
          const mediaUrl = row['Media URL'] || row['image_url'] || row['media_url'] || '';

          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td><strong>${escapeHtml(title)}</strong></td>
            <td><span class="status-badge badge-pending">${escapeHtml(board)}</span></td>
            <td><a href="${escapeHtml(link)}" target="_blank" style="font-size: 0.85rem;">${escapeHtml(link)}</a></td>
            <td>${mediaUrl ? '<a href="'+escapeHtml(mediaUrl)+'" target="_blank" class="btn btn-secondary btn-sm">View Media</a>' : '-'}</td>
          `;
          tbody.appendChild(tr);
        });
      }
    }
  })
  .catch(err => {
    console.error(err);
    showToast('Failed to generate preview', 'error');
  });
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Cron Parser
function parseCron(cronStr) {
  if (!cronStr || cronStr.trim() === '') return 'Not scheduled';
  const parts = cronStr.trim().split(/\s+/);
  if (parts.length !== 5) return `Invalid format (needs 5 fields): ${cronStr}`;
  
  const [min, hour, dom, mon, dow] = parts;
  if (min === '0' && hour === '9' && dom === '*' && mon === '*' && dow === '*') return 'Every day at 9:00 AM';
  if (min === '0' && (hour === '0,12' || hour === '*/12') && dom === '*' && mon === '*' && dow === '*') return 'Every 12 hours';
  if (min === '0' && hour === '0' && dom === '*/2' && mon === '*' && dow === '*') return 'Every other day at midnight';
  if (min === '0' && hour === '9' && dom === '*' && mon === '*' && dow === '1-5') return 'Weekdays (Mon-Fri) at 9:00 AM';
  if (min === '0' && hour === '*/1') return 'Every hour on the hour';
  
  return `Cron schedule: ${cronStr}`;
}

function updateCronPreview() {
  const input = document.getElementById('cron_expression');
  const preview = document.getElementById('cron_preview');
  if (input && preview) {
    preview.textContent = parseCron(input.value);
  }
}

// Auto-refresh Dashboard stats
function initDashboardRefresh() {
  if (window.location.pathname === '/') {
    setInterval(() => {
      fetch('/api/dashboard/stats')
        .then(res => res.json())
        .then(data => {
          if (data) {
            const elTotal = document.getElementById('stat-total-accounts');
            const elUploads = document.getElementById('stat-today-uploads');
            const elPending = document.getElementById('stat-pending-batches');
            const elFailed = document.getElementById('stat-failed-batches');
            if (elTotal) elTotal.textContent = data.total_accounts;
            if (elUploads) elUploads.textContent = data.todays_uploads;
            if (elPending) elPending.textContent = data.pending_batches;
            if (elFailed) elFailed.textContent = data.failed_batches;
          }
        })
        .catch(err => console.error('Dashboard auto-refresh failed:', err));
    }, 30000);
  }
}

document.addEventListener('DOMContentLoaded', initDashboardRefresh);
