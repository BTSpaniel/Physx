// SPDX-FileCopyrightText: 2026 Jake Wehmeier (BTSpaniel)
// SPDX-License-Identifier: MIT
const elements = Object.fromEntries(['reset', 'pause', 'stop', 'status', 'view', 'time', 'height', 'speed'].map(id => [id, document.getElementById(id)]));
const context = elements.view.getContext('2d');
let worker = null;
let generation = 0;
let closingTimer = null;
window.quickstartState = Object.freeze({status: 'LOADING', generation});

function display(state) {
  window.quickstartState = Object.freeze({...state, generation});
  const y = Number.isFinite(state.y) ? state.y : 3;
  elements.time.textContent = `${(state.time ?? 0).toFixed(2)} s`;
  elements.height.textContent = `${y.toFixed(3)} m`;
  elements.speed.textContent = `${(state.velocityY ?? 0).toFixed(3)} m/s`;
  elements.status.textContent = state.error ?? ({RUNNING: 'PhysX 5.11 · CPU WebAssembly · measured motion', PAUSED: 'Paused — the native scene is held.', CLOSED: 'Released — drop again to create a new scene.'}[state.status] ?? 'Loading the matched PhysX PE runtime…');
  elements.pause.disabled = !['RUNNING', 'PAUSED'].includes(state.status);
  elements.stop.disabled = !['RUNNING', 'PAUSED'].includes(state.status);
  elements.pause.textContent = state.status === 'PAUSED' ? 'Resume' : 'Pause';
  context.clearRect(0, 0, 900, 420);
  context.fillStyle = '#6f7c90'; context.fillRect(40, 365, 820, 8);
  context.fillStyle = '#f0d469'; context.beginPath(); context.arc(450, 365 - y * 100, 25, 0, 2 * Math.PI); context.fill();
  context.fillStyle = '#c4cbd5'; context.font = '16px system-ui'; context.fillText('Floor · y = 0 m', 50, 400);
}

function terminate() {
  clearTimeout(closingTimer); closingTimer = null;
  worker?.terminate(); worker = null;
}

function start() {
  terminate(); generation += 1; display({status: 'LOADING'});
  try {
    const owner = new Worker(new URL('./quickstart-worker.mjs', import.meta.url), {type: 'module'});
    worker = owner;
    owner.onmessage = ({data}) => {
      if (worker !== owner) return;
      display(data);
      if (['CLOSED', 'FAILED'].includes(data.status)) terminate();
    };
    owner.onerror = event => {if (worker === owner) {display({status: 'FAILED', error: event.message}); terminate();}};
  } catch (error) {display({status: 'FAILED', error: String(error)}); terminate();}
}
elements.reset.onclick = start;
elements.pause.onclick = () => worker?.postMessage({type: window.quickstartState.status === 'PAUSED' ? 'resume' : 'pause'});
elements.stop.onclick = () => {
  if (!worker) return;
  elements.pause.disabled = true; elements.stop.disabled = true;
  worker.postMessage({type: 'close'});
  closingTimer = setTimeout(() => {display({status: 'FAILED', error: 'Close timed out; the owning Worker was terminated.'}); terminate();}, 1500);
};
window.addEventListener('pagehide', terminate, {once: true});
start();
