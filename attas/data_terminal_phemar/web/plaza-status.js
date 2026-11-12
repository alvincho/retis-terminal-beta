// Connection status is local UI state only. No authentication or network calls.
export function renderPlazaStatus(connected = false) {
  return `<details class="plaza-status${connected ? ' is-demo-connected' : ''}">
    <summary aria-label="Plaza connection status">
      <span class="plaza-status-dot" aria-hidden="true"></span>
      <span class="plaza-status-label">Plaza <span class="plaza-status-separator" aria-hidden="true">/</span> <strong>${connected ? 'Demo connected' : 'Not connected'}</strong></span>
      <svg class="plaza-status-chevron" width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </summary>
    <section class="plaza-status-popover" aria-label="Plaza connection details">
      <div class="plaza-status-heading">
        <span>PLAZA CONNECTION</span>
        <span class="plaza-status-badge">DEMO</span>
      </div>
      <h3>${connected ? 'Demo account active' : 'Working as a guest'}</h3>
      <dl class="plaza-status-facts">
        <div><dt>Account</dt><dd>${connected ? 'Demo Plaza account' : 'Guest'}</dd></div>
        <div><dt>Environment</dt><dd>UI prototype</dd></div>
        <div><dt>Authentication</dt><dd>Not performed</dd></div>
        <div><dt>Data transport</dt><dd>Disabled</dd></div>
      </dl>
      <p class="plaza-status-notice">Demo only. Account changes are simulated in this interface. No Plaza login or data connection is performed.</p>
      <div class="plaza-status-actions">
        <button type="button" class="plaza-status-primary" data-action="demo-plaza-toggle">${connected ? 'Disconnect demo account' : 'Connect demo account'}</button>
        <button type="button" class="plaza-status-secondary" data-action="plaza">Account settings <span aria-hidden="true">↗</span></button>
      </div>
    </section>
  </details>`;
}

export function closePlazaStatus() {
  document.querySelectorAll('details.plaza-status[open]').forEach((details) => {
    details.open = false;
  });
}

// ES modules initialize once. These listeners only act on this disclosure.
document.addEventListener('click', (event) => {
  if (!(event.target instanceof Element) || !event.target.closest('.plaza-status')) {
    closePlazaStatus();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  const opened = document.querySelector('details.plaza-status[open]');
  if (!opened) return;
  const focusWasInside = opened.contains(document.activeElement);
  closePlazaStatus();
  if (focusWasInside) opened.querySelector('summary')?.focus();
});
