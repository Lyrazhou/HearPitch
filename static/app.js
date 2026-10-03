const state = {
  project: null,
  score: null,
  profiles: [],
  selectedNote: null,
  activeJob: null,
  jobStartedAt: null,
  jobTimer: null,
  audioDuration: 0,
  spectrogramReady: false,
  noteDrag: null,
  captureActive: false,
  timeZoom: 1,
  pitchZoom: 1,
  audioContext: null,
  activePreview: null,
  lastPreviewMidi: null,
  lastPreviewAt: 0,
  panDrag: null,
  suppressNoteClick: false,
};

const PRESETS = {
  conservative: { sensitivity: 40, min_note_duration_ms: 180, min_confidence: 70, merge_gap_ms: 90, hint: '保守：优先减少呼吸声、颤音和杂音造成的碎音；弱音可能被忽略。' },
  balanced: { sensitivity: 65, min_note_duration_ms: 140, min_confidence: 60, merge_gap_ms: 80, hint: '平衡：兼顾普通清唱、单旋律独奏的检出率和误识别控制。' },
  sensitive: { sensitivity: 82, min_note_duration_ms: 80, min_confidence: 45, merge_gap_ms: 55, hint: '敏感：更容易保留弱音、短音与快速旋律，但需要更仔细回听校正。' },
};

const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#039;', '"': '&quot;'
}[character]));

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : null;
  if (!response.ok) throw new Error(payload?.detail || `请求失败（${response.status}）`);
  return payload;
}

function showToast(message, kind = '') {
  const toast = $('#toast');
  toast.textContent = message;
  toast.className = `toast ${kind}`.trim();
  toast.classList.remove('hidden');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.add('hidden'), 4300);
}

function formatSeconds(value) {
  const seconds = Number(value || 0);
  const minutes = Math.floor(seconds / 60);
  const rest = (seconds % 60).toFixed(1).padStart(4, '0');
  return minutes ? `${minutes}:${rest}` : `${seconds.toFixed(1)} 秒`;
}

function midiLabel(midi) {
  const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  return `${names[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;
}

function constrainMidi(midi) {
  if (!$('#strictKey')?.checked) return midi;
  const key = $('#keySelect')?.value || state.score?.key?.tonic || 'C';
  const mode = $('#keyMode')?.value || state.score?.key?.mode || 'major';
  const root = keyRoot({ tonic: key });
  const scale = mode === 'minor' ? [0, 2, 3, 5, 7, 8, 10] : [0, 2, 4, 5, 7, 9, 11];
  const candidates = [];
  for (let octave = 0; octave <= 10; octave += 1) for (const degree of scale) candidates.push(12 * octave + root + degree);
  return candidates.reduce((best, value) => Math.abs(value - midi) < Math.abs(best - midi) ? value : best, candidates[0]);
}

function isMidiInSelectedKey(midi) {
  const tonic = $('#keySelect')?.value || state.score?.key?.tonic;
  if (!tonic) return true;
  const mode = $('#keyMode')?.value || state.score?.key?.mode || 'major';
  const scale = mode === 'minor' ? [0, 2, 3, 5, 7, 8, 10] : [0, 2, 4, 5, 7, 9, 11];
  return scale.includes(((midi - keyRoot({ tonic })) % 12 + 12) % 12);
}

function previewMidi(midi, seconds = .35) {
  try {
    const AudioCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtor) return;
    if (!state.audioContext) state.audioContext = new AudioCtor();
    const context = state.audioContext;
    if (context.state === 'suspended') context.resume();
    const now = context.currentTime;
    if (state.activePreview) { try { state.activePreview.stop(now); } catch (_) {} }
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = 'triangle';
    oscillator.frequency.value = 440 * Math.pow(2, (midi - 69) / 12);
    gain.gain.setValueAtTime(.0001, now);
    gain.gain.exponentialRampToValueAtTime(.22, now + .012);
    gain.gain.setTargetAtTime(.0001, now + Math.max(.04, seconds - .05), .025);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start(now);
    oscillator.stop(now + Math.max(.08, seconds));
    state.activePreview = oscillator;
    state.lastPreviewMidi = midi;
    $('#canvasSelection').textContent = `试听 ${midiLabel(midi)} · MIDI ${midi}`;
  } catch (_) {
    // Audio preview is optional on browsers that cannot create Web Audio.
  }
}

function keyRoot(key) {
  const names = { C: 0, 'C#': 1, Db: 1, D: 2, 'D#': 3, Eb: 3, E: 4, F: 5, 'F#': 6, Gb: 6, G: 7, 'G#': 8, Ab: 8, A: 9, 'A#': 10, Bb: 10, B: 11 };
  return names[key?.tonic] ?? 0;
}

function jianpu(midi, key) {
  const scale = key?.mode === 'minor' ? [0, 2, 3, 5, 7, 8, 10] : [0, 2, 4, 5, 7, 9, 11];
  const relative = ((Number(midi) - keyRoot(key)) % 12 + 12) % 12;
  let degree = scale.indexOf(relative);
  let accidental = '';
  if (degree < 0) {
    let nearest = 0;
    let distance = 99;
    scale.forEach((pitch, index) => {
      const signed = relative - pitch;
      const wrapped = signed > 6 ? signed - 12 : (signed < -6 ? signed + 12 : signed);
      if (Math.abs(wrapped) < distance) { nearest = index; distance = Math.abs(wrapped); accidental = wrapped > 0 ? '#' : 'b'; }
    });
    degree = nearest;
  }
  const octaveDelta = Math.floor((Number(midi) - (60 + keyRoot(key))) / 12);
  const top = octaveDelta > 0 ? '·'.repeat(octaveDelta) : '';
  const bottom = octaveDelta < 0 ? '·'.repeat(Math.abs(octaveDelta)) : '';
  return { text: `${accidental}${degree + 1}`, top, bottom };
}

function updateNoteFromRow(index, field, rawValue) {
  if (!state.score) return;
  const note = state.score.notes[index];
  const value = Number(rawValue);
  if (!Number.isFinite(value)) return;
  if (field === 'midi') { note.midi = constrainMidi(Math.min(127, Math.max(0, Math.round(value)))); note.name = midiLabel(note.midi); previewMidi(note.midi, .2); }
  if (field === 'onset') note.onset = Math.max(0, Number(value.toFixed(4)));
  if (field === 'offset') note.offset = Math.max(note.onset + 0.03, Number(value.toFixed(4)));
  note.duration = Number((note.offset - note.onset).toFixed(4));
  note.user_edited = true;
  renderNotation();
  renderNotesTable();
  renderSpectrogram();
}

function renderNotation() {
  const notation = $('#notation');
  if (!state.score) return;
  notation.innerHTML = '';
  const notes = state.score.notes || [];
  notes.forEach((note, index) => {
    if (index && index % 8 === 0) {
      const bar = document.createElement('span');
      bar.className = 'barline';
      notation.appendChild(bar);
    }
    const degree = jianpu(note.midi, state.score.key);
    const glyph = document.createElement('button');
    glyph.className = `note-glyph ${note.confidence != null && Number(note.confidence) < .45 ? 'low' : ''} ${state.selectedNote === index ? 'selected' : ''}`;
    const confidenceLabel = note.confidence == null ? '模型未提供逐音置信度' : `置信度 ${Math.round(Number(note.confidence) * 100)}%`;
    glyph.title = `#${index + 1} · ${note.name || 'MIDI ' + note.midi} · ${note.onset}s–${note.offset}s · ${confidenceLabel}`;
    glyph.innerHTML = `<span class="note-octave">${degree.top}</span><span class="note-number">${degree.text}</span><span class="note-duration">${degree.bottom || '—'} ${note.duration.toFixed(2)}s</span>`;
    glyph.addEventListener('click', () => {
      state.selectedNote = index;
      previewMidi(Number(note.midi), Math.max(.18, Math.min(1.2, Number(note.duration) || .35)));
      renderSpectrogram();
      renderNotation();
      document.querySelector(`tr[data-note-index="${index}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    notation.appendChild(glyph);
  });
}

function renderSpectrogram() {
  if (!state.score || !state.spectrogramReady) return;
  const canvas = $('#spectrogramCanvas');
  const layer = $('#spectrogramNotes');
  const image = $('#spectrogramImage');
  const duration = Math.max(.15, Number(state.score.input.duration_seconds) || state.audioDuration || 1);
  const viewportWidth = Math.max(600, $('#spectrogramScroll').clientWidth - 2);
  const width = Math.round(viewportWidth * state.timeZoom);
  const semitoneHeight = 12 * state.pitchZoom;
  const height = Math.round(48 * semitoneHeight);
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  image.style.width = `${width}px`;
  image.style.height = `${height}px`;
  layer.innerHTML = '';
  const grid = $('#spectrogramGrid');
  grid.style.backgroundSize = `100% ${semitoneHeight}px, 100% ${semitoneHeight * 12}px`;
  const beatLayer = $('#spectrogramBeats');
  beatLayer.innerHTML = '';
  renderBeatMarkers();
  state.score.notes.forEach((note, index) => {
    const block = document.createElement('div');
    block.className = `spectrogram-note ${note.confidence != null && Number(note.confidence) < .45 ? 'low' : ''} ${!isMidiInSelectedKey(Number(note.midi)) ? 'out-of-key' : ''} ${state.selectedNote === index ? 'selected' : ''}`;
    block.dataset.noteIndex = index;
    const left = Math.max(0, Number(note.onset) / duration * width);
    const right = Math.min(width, Number(note.offset) / duration * width);
    block.style.left = `${left}px`;
    block.style.width = `${Math.max(7, right - left)}px`;
    block.style.top = `${Math.max(0, Math.min(height - semitoneHeight, (84 - Number(note.midi) - .5) * semitoneHeight))}px`;
    block.textContent = midiLabel(Number(note.midi));
    block.title = `#${index + 1} ${midiLabel(Number(note.midi))} · ${Number(note.onset).toFixed(2)}–${Number(note.offset).toFixed(2)} 秒；拖动编辑`;
    block.addEventListener('pointerdown', beginNoteDrag);
    block.addEventListener('click', () => {
      if (state.suppressNoteClick) { state.suppressNoteClick = false; return; }
      state.selectedNote = index;
      renderSpectrogram(); renderNotation(); renderNotesTable();
      $('#audioPlayer').currentTime = Math.max(0, Number(note.onset));
      previewMidi(Number(note.midi), Math.max(.18, Math.min(1.2, Number(note.duration) || .35)));
    });
    layer.appendChild(block);
  });
  renderTapReadout();
}

function beginNoteDrag(event) {
  event.preventDefault();
  const element = event.currentTarget;
  const index = Number(element.dataset.noteIndex);
  const note = state.score.notes[index];
  const rect = element.getBoundingClientRect();
  const localX = event.clientX - rect.left;
  const mode = localX <= 9 ? 'start' : localX >= rect.width - 9 ? 'end' : 'move';
  state.selectedNote = index;
  state.noteDrag = {
    element, index, mode, startX: event.clientX, startY: event.clientY,
    onset: Number(note.onset), offset: Number(note.offset), midi: Number(note.midi), moved: false,
  };
  element.setPointerCapture(event.pointerId);
  element.addEventListener('pointermove', moveNoteDrag);
  element.addEventListener('pointerup', finishNoteDrag, { once: true });
  element.addEventListener('pointercancel', finishNoteDrag, { once: true });
}

function moveNoteDrag(event) {
  const drag = state.noteDrag;
  if (!drag) return;
  const dx = event.clientX - drag.startX;
  const dy = event.clientY - drag.startY;
  if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
  if (!drag.moved) return;
  const duration = Math.max(.15, Number(state.score.input.duration_seconds) || 1);
  const width = $('#spectrogramCanvas').clientWidth || 900;
  const seconds = dx / width * duration;
  const semitones = Math.round(-dy / (12 * state.pitchZoom));
  const note = state.score.notes[drag.index];
  if (drag.mode === 'start') note.onset = Math.max(0, Math.min(drag.offset - .03, drag.onset + seconds));
  else if (drag.mode === 'end') note.offset = Math.max(drag.onset + .03, drag.offset + seconds);
  else {
    const delta = Math.min(Math.max(seconds, -drag.onset), duration - drag.offset);
    note.onset = drag.onset + delta;
    note.offset = drag.offset + delta;
    note.midi = constrainMidi(Math.max(36, Math.min(84, drag.midi + semitones)));
  }
  note.duration = note.offset - note.onset;
  note.name = midiLabel(note.midi);
  note.user_edited = true;
  drag.element.style.left = `${note.onset / duration * width}px`;
  drag.element.style.width = `${Math.max(7, (note.offset - note.onset) / duration * width)}px`;
  const semitoneHeight = 12 * state.pitchZoom;
  drag.element.style.top = `${Math.max(0, Math.min($('#spectrogramCanvas').clientHeight - semitoneHeight, (84 - note.midi - .5) * semitoneHeight))}px`;
  drag.element.textContent = midiLabel(note.midi);
  if (drag.midi !== note.midi && performance.now() - state.lastPreviewAt > 90) {
    previewMidi(note.midi, .12);
    state.lastPreviewAt = performance.now();
  }
}

function finishNoteDrag() {
  const drag = state.noteDrag;
  if (!drag) return;
  drag.element.removeEventListener('pointermove', moveNoteDrag);
  state.noteDrag = null;
  if (drag.moved) {
    state.suppressNoteClick = true;
    renderSpectrogram(); renderNotation(); renderNotesTable();
    showToast('音符已修改；记得保存修订。', 'success');
  }
}

async function loadSpectrogram() {
  const image = $('#spectrogramImage');
  state.spectrogramReady = false;
  image.onload = () => {
    state.spectrogramReady = true;
    renderSpectrogram();
    const canvas = $('#spectrogramCanvas');
    image.style.width = `${canvas.clientWidth}px`;
    image.style.height = `${canvas.clientHeight}px`;
  };
  image.onerror = () => showToast('声谱图生成失败；仍可用下方表格编辑音符。', 'error');
  image.src = `/api/projects/${encodeURIComponent(state.project.id)}/spectrogram?v=${Date.now()}`;
}

function renderTapReadout() {
  const taps = state.score?.beat_markers?.map((marker) => Number(marker.raw_time ?? marker.time)) || state.score?.beat_taps || [];
  const display = $('#tapReadout');
  if (!display) return;
  if (!taps.length) {
    display.textContent = state.captureActive
      ? `打拍已启动：${$('#beatMode')?.value === 'digits' ? '数字键标注' : '空格记拍'} · Esc 结束 · 尚无拍点`
      : '未进入打拍模式。拍点和小节线会直接显示在声谱图上。';
    renderBeatMarkers();
    return;
  }
  const intervals = taps.slice(1).map((tap, index) => tap - taps[index]).filter((gap) => gap >= .25 && gap <= 2.5);
  const rawBpm = intervals.length ? 60 / (intervals.reduce((sum, gap) => sum + gap, 0) / intervals.length) : null;
  const bpm = rawBpm && rawBpm >= 30 && rawBpm <= 300 ? rawBpm : null;
  display.textContent = `${state.captureActive ? `打拍已启动：${$('#beatMode')?.value === 'digits' ? '数字键标注' : '空格记拍'} · Esc 结束 · ` : ''}已记录 ${taps.length} 个拍点${bpm ? ` · 估算 ${bpm.toFixed(1)} BPM` : ' · 至少再打一次以估算 BPM'}`;
  if (bpm && Number.isFinite(bpm)) { state.score.tempo_bpm = Number(bpm.toFixed(2)); state.score.tempo_mode = 'tap'; }
  renderBeatMarkers();
}

function renderBeatMarkers() {
  if (!state.score || !state.spectrogramReady) return;
  const beatLayer = $('#spectrogramBeats');
  const duration = Math.max(.15, Number(state.score.input.duration_seconds) || 1);
  const width = $('#spectrogramCanvas').clientWidth || 900;
  const markers = state.score.beat_markers || [];
  const beats = beatLayer;
  beats.innerHTML = '';
  markers.forEach((marker) => {
    const line = document.createElement('div');
    line.className = `beat-marker ${marker.beat === 1 ? 'bar-marker' : ''}`;
    line.dataset.markerIndex = String(markers.indexOf(marker));
    line.style.left = `${Number(marker.time) / duration * width}px`;
    line.innerHTML = `<span>${marker.beat === 1 ? `小节 ${marker.bar} · ` : ''}${marker.beat}</span>`;
    line.addEventListener('pointerdown', (event) => {
      event.preventDefault(); event.stopPropagation();
  const originalX = event.clientX;
  const originalTime = Number(marker.time);
      marker.raw_time ??= originalTime;
      const move = (moveEvent) => {
        const delta = (moveEvent.clientX - originalX) / width * duration;
        marker.time = Math.max(0, originalTime + delta);
        line.style.left = `${marker.time / duration * width}px`;
      };
      const finish = () => {
        line.removeEventListener('pointermove', move);
        line.removeEventListener('pointerup', finish);
        line.removeEventListener('pointercancel', finish);
        state.score.beat_taps = markers.map((item) => Number(item.raw_time ?? item.time));
        renderTapReadout();
      };
      line.addEventListener('pointermove', move);
      line.addEventListener('pointerup', finish, { once: true });
      line.addEventListener('pointercancel', finish, { once: true });
    });
    line.addEventListener('dblclick', (event) => {
      event.preventDefault(); event.stopPropagation();
      const index = markers.indexOf(marker);
      markers.splice(index, 1);
      state.score.beat_taps = markers.map((item) => Number(item.raw_time ?? item.time));
      renderTapReadout(); renderSpectrogram();
    });
    beatLayer.appendChild(line);
  });
}

function parseMeterSequence() {
  const values = ($('#meterSequence')?.value || '4').split(/[,+\s]+/).map(Number).filter((n) => Number.isInteger(n) && n >= 1 && n <= 32);
  return values.length ? values : [4];
}

function recordBeatTap(numberedBeat = null) {
  if (!state.project || !state.score) return;
  const player = $('#audioPlayer');
  const current = Number(player.currentTime.toFixed(4));
  const taps = state.score.beat_taps || (state.score.beat_taps = []);
  if (taps.length && current - taps[taps.length - 1] < .2) return;
  taps.push(current);
  const markers = state.score.beat_markers || (state.score.beat_markers = []);
  const sequence = parseMeterSequence();
  let bar = markers.length ? markers[markers.length - 1].bar : 1;
  let beat = markers.length ? markers[markers.length - 1].beat + 1 : 1;
  if (numberedBeat != null) {
    beat = numberedBeat;
    if (numberedBeat === 1 && markers.length) bar += 1;
    else if (!markers.length) bar = 1;
    else bar = markers[markers.length - 1].bar;
  } else if (markers.length && beat > sequence[(bar - 1) % sequence.length]) {
    bar += 1;
    beat = 1;
  }
  markers.push({ time: current, beat, bar });
  state.score.meter_sequence = sequence;
  state.score.time_signature = { ...(state.score.time_signature || {}), value: `${sequence[0]}/4`, sequence };
  renderTapReadout();
  renderSpectrogram();
}

function toggleBeatCapture() {
  state.captureActive = !state.captureActive;
  $('#tapTempoButton').textContent = state.captureActive ? '结束打拍（Esc）' : '开始打拍';
  $('#tapTempoButton').classList.toggle('capture-on', state.captureActive);
  renderTapReadout();
  if (state.captureActive) showToast('打拍模式已启动；焦点无需停在按钮上，Esc 或按钮可结束。', 'success');
}

function quantizeAll(kind) {
  if (!state.score) return;
  const subdivision = $('#rhythmQuantize').value;
  if (subdivision === 'off') return showToast('请先选择 1/8、1/16 或 1/32 量化网格。');
  const beatSeconds = 60 / Math.max(30, Math.min(300, Number(state.score.tempo_bpm) || 100));
  const grid = beatSeconds * 16 / Number(subdivision);
  const anchor = state.score.beat_markers?.find((marker) => marker.beat === 1)?.time ?? 0;
  if (kind === 'beats') {
    state.score.beat_markers = (state.score.beat_markers || []).map((marker) => ({ ...marker, raw_time: marker.raw_time ?? marker.time, time: Math.max(0, anchor + Math.round((marker.time - anchor) / grid) * grid) }));
    state.score.beat_taps = state.score.beat_markers.map((marker) => Number(marker.raw_time ?? marker.time));
  } else {
    state.score.notes.forEach((note) => {
      note.onset = Math.max(0, anchor + Math.round((note.onset - anchor) / grid) * grid);
      note.offset = Math.max(note.onset + grid, anchor + Math.round((note.offset - anchor) / grid) * grid);
      note.duration = note.offset - note.onset;
      note.user_edited = true;
    });
  }
  state.score.rhythm_quantize = Number(subdivision);
  renderSpectrogram(); renderNotesTable(); renderNotation();
  showToast(`${kind === 'beats' ? '拍点' : '音符'}已吸附到 1/${subdivision} 网格；请保存修订。`, 'success');
}

function setZoom(axis, value) {
  const scroll = $('#spectrogramScroll');
  const oldLeft = scroll.scrollLeft;
  const oldWidth = $('#spectrogramCanvas').clientWidth || 1;
  if (axis === 'time') state.timeZoom = Math.max(1, Math.min(8, Number(value)));
  else state.pitchZoom = Math.max(1, Math.min(3, Number(value)));
  renderSpectrogram();
  $('#timeZoom').value = state.timeZoom;
  $('#pitchZoom').value = state.pitchZoom;
  if (axis === 'time') {
    const ratio = scroll.clientWidth ? (oldLeft + scroll.clientWidth / 2) / oldWidth : 0;
    scroll.scrollLeft = Math.max(0, ratio * $('#spectrogramCanvas').clientWidth - scroll.clientWidth / 2);
  }
}

function bindCanvasNavigation() {
  const scroll = $('#spectrogramScroll');
  scroll.addEventListener('wheel', (event) => {
    if (event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    const control = event.shiftKey ? $('#pitchZoom') : $('#timeZoom');
    setZoom(event.shiftKey ? 'pitch' : 'time', Number(control.value) + (event.deltaY < 0 ? .25 : -.25));
  }, { passive: false });
  scroll.addEventListener('pointerdown', (event) => {
    if (event.target.closest('.spectrogram-note') || event.target.closest('.beat-marker')) return;
    state.panDrag = { x: event.clientX, y: event.clientY, left: scroll.scrollLeft, top: scroll.scrollTop };
    scroll.setPointerCapture(event.pointerId);
  });
  scroll.addEventListener('click', (event) => {
    if (event.detail !== 1 || event.target.closest('.spectrogram-note') || event.target.closest('.beat-marker')) return;
    const rect = $('#spectrogramCanvas').getBoundingClientRect();
    const duration = Number(state.score?.input?.duration_seconds) || 1;
    $('#audioPlayer').currentTime = Math.max(0, Math.min(duration, (event.clientX - rect.left) / rect.width * duration));
  });
  scroll.addEventListener('pointermove', (event) => {
    if (!state.panDrag) return;
    scroll.scrollLeft = state.panDrag.left - (event.clientX - state.panDrag.x);
    scroll.scrollTop = state.panDrag.top - (event.clientY - state.panDrag.y);
  });
  ['pointerup', 'pointercancel'].forEach((name) => scroll.addEventListener(name, () => { state.panDrag = null; }));
  $('#spectrogramCanvas').addEventListener('dblclick', (event) => {
    const rect = $('#spectrogramCanvas').getBoundingClientRect();
    const duration = Number(state.score?.input?.duration_seconds) || 1;
    $('#audioPlayer').currentTime = Math.max(0, Math.min(duration, (event.clientX - rect.left) / rect.width * duration));
  });
}

function handleEditorKeydown(event) {
  const target = event.target;
  const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || (target instanceof HTMLSelectElement && target.id !== 'beatMode') || target?.isContentEditable;
  if (editing) return;
  if (state.captureActive && event.code === 'Escape') {
    event.preventDefault();
    state.captureActive = false;
    $('#tapTempoButton').textContent = '开始打拍';
    $('#tapTempoButton').classList.remove('capture-on');
    renderTapReadout();
    return;
  }
  if (state.captureActive && event.code === 'Space' && $('#beatMode').value === 'space') {
    event.preventDefault(); event.stopPropagation(); recordBeatTap(); return;
  }
  if (state.captureActive && $('#beatMode').value === 'digits' && /^[1-9]$/.test(event.key)) {
    event.preventDefault(); event.stopPropagation(); recordBeatTap(Number(event.key)); return;
  }
  if (state.selectedNote == null || !state.score) return;
  const note = state.score.notes[state.selectedNote];
  if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
    event.preventDefault(); note.midi = constrainMidi(Math.max(36, Math.min(84, note.midi + (event.key === 'ArrowUp' ? 1 : -1))));
    note.name = midiLabel(note.midi); note.user_edited = true; previewMidi(note.midi, .2);
  } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault();
    const step = event.shiftKey ? .1 : .02;
    const delta = (event.key === 'ArrowRight' ? 1 : -1) * step;
    note.onset = Math.max(0, note.onset + delta); note.offset = Math.max(note.onset + .03, note.offset + delta); note.duration = note.offset - note.onset; note.user_edited = true;
  } else if (event.key.toLowerCase() === 'd') {
    state.score.notes.splice(state.selectedNote, 1); state.selectedNote = null;
  } else return;
  renderSpectrogram(); renderNotation(); renderNotesTable();
}

function renderNotesTable() {
  const body = $('#notesTable');
  if (!state.score) return;
  body.innerHTML = state.score.notes.map((note, index) => {
    const degree = jianpu(note.midi, state.score.key);
    const confidence = note.confidence == null ? null : Math.round(Number(note.confidence) * 100);
    return `<tr data-note-index="${index}" class="${state.selectedNote === index ? 'selected-row' : ''}">
      <td>${index + 1}</td>
      <td><input aria-label="第 ${index + 1} 个音符起点" type="number" step="0.01" min="0" data-index="${index}" data-field="onset" value="${Number(note.onset).toFixed(2)}"></td>
      <td><input aria-label="第 ${index + 1} 个音符终点" type="number" step="0.01" min="0" data-index="${index}" data-field="offset" value="${Number(note.offset).toFixed(2)}"></td>
      <td><input aria-label="第 ${index + 1} 个音符 MIDI" type="number" step="1" min="0" max="127" data-index="${index}" data-field="midi" value="${note.midi}"></td>
      <td>${escapeHtml(note.name || '—')}</td><td>${degree.top}${degree.text}${degree.bottom}</td>
      <td><span class="confidence ${confidence != null && confidence < 45 ? 'low' : ''}">${confidence == null ? '—' : `${confidence}%`}</span></td>
      <td><button class="remove-note" data-remove-index="${index}">删除</button></td>
    </tr>`;
  }).join('');
  body.querySelectorAll('input[data-index]').forEach((input) => input.addEventListener('change', (event) => {
    updateNoteFromRow(Number(event.target.dataset.index), event.target.dataset.field, event.target.value);
  }));
  body.querySelectorAll('[data-remove-index]').forEach((button) => button.addEventListener('click', () => {
    const index = Number(button.dataset.removeIndex);
    if (state.score.notes.length <= 1) return showToast('谱面至少需要保留一个音符。', 'error');
    state.score.notes.splice(index, 1);
    state.selectedNote = null;
    renderNotation();
    renderNotesTable();
    renderSpectrogram();
  }));
}

function renderQuality() {
  const notices = state.score?.quality?.notices || [];
  $('#qualityNotices').innerHTML = notices.map((item) => `<div class="notice ${escapeHtml(item.level)}">${escapeHtml(item.text)}</div>`).join('') || '<div class="notice">暂无质量提示。</div>';
  const findings = state.score?.rule_findings || [];
  $('#ruleFindings').innerHTML = findings.length ? findings.slice(0, 8).map((item) => `<div class="finding ${escapeHtml(item.severity)}"><strong>${escapeHtml((item.note_ids || []).join('、'))}</strong>${escapeHtml(item.reason)}</div>`).join('') : '<div class="finding low">未发现需要优先复听的规则异常。</div>';
}

function renderMetrics() {
  const score = state.score;
  const metrics = [
    ['候选音符', `${score.notes.length} 个`],
    ['音频时长', formatSeconds(score.input.duration_seconds)],
    ['速度候选', score.tempo_bpm ? `${Number(score.tempo_bpm).toFixed(1)} BPM` : '未确定'],
    ['调性候选', `${score.key?.label || '未确定'} · ${(Number(score.key?.confidence) > 0 ? Math.round(Number(score.key?.confidence) * 100) : '—')}`],
  ];
  $('#metrics').innerHTML = metrics.map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join('');
}

function renderScoreContext() {
  const score = state.score;
  const settings = score.transcription_settings || {};
  const settingsLabel = settings.label ? `识别：${settings.label} · 灵敏度 ${settings.sensitivity ?? '—'}` : null;
  $('#scoreContext').innerHTML = [
    `引擎：${score.engine.name}`,
    settingsLabel,
    `拍号：${score.meter_sequence?.length ? score.meter_sequence.join('+') + '/4（用户标记）' : (score.time_signature?.value || '4/4（默认候选）')}`,
    `调性：${score.key?.label || '未指定'}${score.key?.status === 'user_set' ? '（用户设定）' : '（算法候选）'}`,
    score.engine.audio_sent_to_cloud ? '音频已外发' : '原始音频未外发',
  ].filter(Boolean).map((text) => `<span>${escapeHtml(text)}</span>`).join('');
  const engineLabel = score.engine.name === 'basic-pitch'
    ? 'Basic Pitch 候选音符'
    : score.engine.name === 'rosvot-rmvpe'
      ? 'ROSVOT + RMVPE 歌声转谱'
      : 'pYIN 本地回退引擎';
  $('#statusStrip').textContent = `${engineLabel} 已生成可编辑候选谱。${score.disclaimer || ''}`;
}

function initialiseMusicControls() {
  const keySelect = $('#keySelect');
  if (!keySelect.options.length) {
    keySelect.innerHTML = '<option value="">自动/不指定</option>' + ['C','C#','D','Eb','E','F','F#','G','Ab','A','Bb','B'].map((key) => `<option value="${key}">${key}</option>`).join('');
  }
  const key = state.score.key || {};
  keySelect.value = key.tonic || '';
  $('#keyMode').value = key.mode || 'major';
  $('#keySelect').dataset.initialKey = keySelect.value;
  $('#keyMode').dataset.initialMode = $('#keyMode').value;
  const sequence = state.score.meter_sequence || state.score.time_signature?.sequence || [4];
  $('#meterSequence').value = sequence.join(',');
  $('#rhythmQuantize').value = String(state.score.rhythm_quantize ?? 16);
  $('#rhythmQuantize').dataset.initialValue = $('#rhythmQuantize').value;
  renderTapReadout();
}

async function refreshRosvotStatus() {
  const label = $('#rosvotStatus');
  if (!label) return;
  try {
    const status = await request('/api/engines/rosvot-rmvpe');
    label.textContent = status.installed && status.cuda_available
      ? 'ROSVOT + RMVPE 已就绪：使用本机 CUDA 推理。'
      : `${status.message} 当前仅供歌声音符转录；支持长音频分段处理。`;
    label.classList.toggle('rosvot-ready', Boolean(status.installed && status.cuda_available));
  } catch (_) {
    label.textContent = '可选 ROSVOT 模型状态暂不可用。';
  }
}

function bindExports() {
  const id = state.project.id;
  $('#downloadMidi').href = `/api/projects/${encodeURIComponent(id)}/exports/midi`;
  $('#downloadXml').href = `/api/projects/${encodeURIComponent(id)}/exports/musicxml`;
  $('#downloadJson').href = `/api/projects/${encodeURIComponent(id)}/exports/json`;
}

async function loadProject(projectId) {
  try {
    const result = await request(`/api/projects/${encodeURIComponent(projectId)}`);
    renderProject(result);
    showToast('已打开本机保存的项目。', 'success');
  } catch (error) {
    showToast(error.message, 'error');
  }
}

async function loadRecentProjects() {
  try {
    const result = await request('/api/projects');
    const recent = result.projects.slice(0, 3);
    const container = $('#recentProjects');
    if (!recent.length) {
      container.classList.add('hidden');
      return;
    }
    container.classList.remove('hidden');
    container.innerHTML = `<strong>最近本机项目</strong>${recent.map((project) => `<button type="button" class="recent-project-row" data-open-project="${escapeHtml(project.id)}"><span><strong>${escapeHtml(project.title || '未命名项目')}</strong></span><span>打开 →</span></button>`).join('')}`;
    container.querySelectorAll('[data-open-project]').forEach((button) => button.addEventListener('click', () => loadProject(button.dataset.openProject)));
  } catch (_) {
    // A new transcription remains available even if historical discovery fails.
  }
}

function renderProject(data) {
  state.project = data.project;
  state.score = structuredClone(data.score);
  state.selectedNote = null;
  $('#workspace').classList.remove('hidden');
  $('#emptyState').classList.add('hidden');
  $('#projectTitle').textContent = state.score.title;
  $('#projectMeta').textContent = `${state.score.input.original_filename} · ${state.project.id} · ${state.score.engine.mode}`;
  $('#audioPlayer').src = `/api/projects/${encodeURIComponent(state.project.id)}/audio`;
  state.audioDuration = Number(state.score.input.duration_seconds) || 0;
  renderScoreContext();
  renderMetrics();
  renderNotation();
  renderNotesTable();
  renderQuality();
  initialiseMusicControls();
  bindExports();
  loadSpectrogram();
  loadRecentProjects();
  $('#workspace').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function readTranscriptionSettings() {
  return {
    preset: $('#presetInput').value,
    engine: $('#engineInput').value,
    sensitivity: Number($('#sensitivityInput').value),
    min_note_duration_ms: Number($('#minDurationInput').value),
    min_confidence: Number($('#minConfidenceInput').value),
    merge_gap_ms: Number($('#mergeGapInput').value),
  };
}

function applyPreset(presetName) {
  const preset = PRESETS[presetName];
  if (!preset) return;
  $('#sensitivityInput').value = preset.sensitivity;
  $('#minDurationInput').value = preset.min_note_duration_ms;
  $('#minConfidenceInput').value = preset.min_confidence;
  $('#mergeGapInput').value = preset.merge_gap_ms;
  updateSensitivityCopy(preset.hint);
}

function updateSensitivityCopy(overrideHint = '') {
  const value = Number($('#sensitivityInput').value);
  $('#sensitivityValue').textContent = value;
  const hint = overrideHint || (value <= 45
    ? '偏保守：会过滤更多短促或不稳定的声音，以减少杂音碎音。'
    : value >= 78
      ? '偏敏感：会保留更多弱音与短音，但误识别和碎音的风险也会升高。'
      : '平衡：适合大多数清唱、口哨与单旋律独奏的起始设置。');
  $('#sensitivityHint').textContent = hint;
}

function markCustomPreset() {
  $('#presetInput').value = 'custom';
  updateSensitivityCopy();
}

function updateJobPanel(job) {
  state.activeJob = job;
  const panel = $('#jobPanel');
  panel.classList.remove('hidden');
  $('#jobPhase').textContent = job.status === 'failed' ? (job.error || '转谱未完成。') : (job.message || '正在本机处理…');
  $('#jobPercent').textContent = `${Math.max(0, Math.min(100, Number(job.progress || 0)))}%`;
  $('#jobProgressBar').style.width = `${Math.max(0, Math.min(100, Number(job.progress || 0)))}%`;
  $('#jobLog').innerHTML = (job.logs || []).slice(-8).map((item) => `<li>${escapeHtml(item.message)}</li>`).join('');
  const started = job.started_at || job.created_at;
  if (started) state.jobStartedAt = new Date(started);
  updateJobElapsed();
}

function updateJobElapsed() {
  if (!state.activeJob || !state.jobStartedAt) return;
  const finished = state.activeJob.finished_at ? new Date(state.activeJob.finished_at) : new Date();
  const seconds = Math.max(0, Math.floor((finished - state.jobStartedAt) / 1000));
  $('#jobElapsed').textContent = `已运行 ${seconds} 秒${state.activeJob.status === 'failed' ? ' · 可查看失败信息后重试' : ''}`;
}

function stopJobPolling() {
  if (state.jobTimer) clearInterval(state.jobTimer);
  state.jobTimer = null;
}

async function refreshJob(jobId) {
  try {
    const result = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
    const job = result.job;
    updateJobPanel(job);
    if (job.status === 'completed') {
      stopJobPolling();
      const project = await request(`/api/projects/${encodeURIComponent(job.project.id)}`);
      renderProject(project);
      $('#transcribeButton').disabled = false;
      $('#transcribeButton').innerHTML = '开始本机转谱 <span>→</span>';
      showToast('候选谱已生成。请先回听并校正低置信度音符。', 'success');
    } else if (job.status === 'failed') {
      stopJobPolling();
      $('#transcribeButton').disabled = false;
      $('#transcribeButton').innerHTML = '开始本机转谱 <span>→</span>';
      showToast(job.error || '转谱未完成，请查看活动日志。', 'error');
    }
  } catch (error) {
    stopJobPolling();
    $('#transcribeButton').disabled = false;
    $('#transcribeButton').innerHTML = '开始本机转谱 <span>→</span>';
    showToast(`无法读取本机任务状态：${error.message}`, 'error');
  }
}

async function uploadAudio(event) {
  event.preventDefault();
  const audio = $('#audioInput').files[0];
  if (!audio) return showToast('请先选择音频文件。', 'error');
  const button = $('#transcribeButton');
  button.disabled = true;
  button.textContent = '正在创建本机任务…';
  stopJobPolling();
  try {
    const formData = new FormData();
    formData.set('audio', audio);
    formData.set('title', $('#titleInput').value.trim());
    formData.set('long_audio_mode', $('#longAudioMode').checked ? 'true' : 'false');
    Object.entries(readTranscriptionSettings()).forEach(([key, value]) => formData.set(key, String(value)));
    const result = await request('/api/projects', { method: 'POST', body: formData });
    updateJobPanel(result.job);
    button.textContent = '正在本机转谱…';
    await refreshJob(result.job.id);
    if (state.activeJob?.status === 'queued' || state.activeJob?.status === 'running') {
      state.jobTimer = setInterval(() => refreshJob(result.job.id), 900);
    }
  } catch (error) {
    button.disabled = false;
    button.innerHTML = '开始本机转谱 <span>→</span>';
    showToast(error.message, 'error');
  }
}

async function saveProject() {
  if (!state.project || !state.score) return;
  const sequence = parseMeterSequence();
  state.score.meter_sequence = sequence;
  state.score.time_signature = { ...(state.score.time_signature || {}), value: `${sequence[0]}/4`, sequence, status: 'user_set' };
  const tonic = $('#keySelect').value;
  if (tonic) state.score.key = { ...(state.score.key || {}), tonic, mode: $('#keyMode').value, label: `${tonic} ${$('#keyMode').value}`, status: 'user_set' };
  else if (state.score.key) state.score.key.status = 'auto_suggestion';
  state.score.rhythm_quantize = $('#rhythmQuantize').value === 'off' ? 'off' : Number($('#rhythmQuantize').value);
  state.score.pitch_quantize = $('#strictKey').checked ? 'key_strict' : (state.score.pitch_quantize || 'semitone');
  const button = $('#saveButton');
  button.disabled = true;
  button.textContent = '保存中…';
  try {
    const result = await request(`/api/projects/${encodeURIComponent(state.project.id)}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ score: state.score }),
    });
    const spectrogramWasReady = state.spectrogramReady;
    renderProject(result);
    if (spectrogramWasReady) state.spectrogramReady = true;
    showToast('修订已保存，并已更新 MIDI、MusicXML 和 JSON 导出。', 'success');
  } catch (error) {
    showToast(error.message, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '保存修订';
  }
}

function renderProfiles() {
  const select = $('#reviewProfile');
  const chosen = select.value;
  select.innerHTML = '<option value="">选择 AI 配置…</option>' + state.profiles.map((profile) => `<option value="${escapeHtml(profile.id)}">${escapeHtml(profile.name)} · ${escapeHtml(profile.model)}</option>`).join('');
  select.value = state.profiles.some((profile) => profile.id === chosen) ? chosen : '';
  const list = $('#profileList');
  list.innerHTML = state.profiles.length ? state.profiles.map((profile) => `<div class="profile-row"><div><strong>${escapeHtml(profile.name)}</strong><span>${escapeHtml(profile.base_url)} · ${escapeHtml(profile.model)} · ${escapeHtml(profile.credential?.message || '凭据状态未知')}</span></div><button type="button" data-edit-profile="${escapeHtml(profile.id)}">编辑</button></div>`).join('') : '<p class="form-note">尚无配置。无需 API Key 也可使用基础转谱。</p>';
  list.querySelectorAll('[data-edit-profile]').forEach((button) => button.addEventListener('click', () => fillProfile(button.dataset.editProfile)));
}

async function loadProfiles() {
  try {
    const result = await request('/api/profiles');
    state.profiles = result.profiles;
    renderProfiles();
  } catch (error) { showToast(error.message, 'error'); }
}

function clearProfileForm() {
  $('#profileId').value = '';
  $('#profileName').value = '';
  $('#profileBaseUrl').value = '';
  $('#profileModel').value = '';
  $('#profileTemperature').value = '0.2';
  $('#profileApiKey').value = '';
  $('#deleteProfileButton').style.visibility = 'hidden';
  renderCredentialMessage({ status: 'missing', message: '新增配置可先只保存 Base URL 和模型；填入 API Key 后会保存到 Windows Credential Manager。' });
}

function renderCredentialMessage(credential) {
  const element = $('#profileCredentialMessage');
  const status = credential?.status || 'missing';
  element.className = `credential-message ${status}`;
  element.textContent = credential?.message || 'API Key 仅保存在 Windows Credential Manager，不写入 profiles.json。';
}

function fillProfile(id) {
  const profile = state.profiles.find((item) => item.id === id);
  if (!profile) return;
  $('#profileId').value = profile.id;
  $('#profileName').value = profile.name;
  $('#profileBaseUrl').value = profile.base_url;
  $('#profileModel').value = profile.model;
  $('#profileTemperature').value = profile.temperature;
  $('#profileApiKey').value = '';
  $('#deleteProfileButton').style.visibility = 'visible';
  renderCredentialMessage(profile.credential);
}

async function saveProfile(event) {
  event.preventDefault();
  const button = $('#saveProfileButton');
  button.disabled = true;
  button.textContent = '正在安全保存…';
  try {
    const payload = {
      id: $('#profileId').value || null,
      name: $('#profileName').value,
      base_url: $('#profileBaseUrl').value,
      model: $('#profileModel').value,
      temperature: Number($('#profileTemperature').value),
      api_key: $('#profileApiKey').value || null,
    };
    const result = await request('/api/profiles', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    await loadProfiles();
    const currentId = result.profile.id;
    if (currentId) $('#reviewProfile').value = currentId;
    $('#profileApiKey').value = '';
    renderCredentialMessage(result.profile.credential);
    $('#settingsDialog').close();
    showToast(result.profile.credential?.status === 'stored'
      ? 'AI 配置和 API Key 已安全保存；API Key 未写入 profiles.json。'
      : 'AI 配置已保存；尚未保存 API Key。', 'success');
  } catch (error) {
    renderCredentialMessage({ status: 'unavailable', message: `保存失败：${error.message}` });
    showToast(`AI 配置未保存：${error.message}`, 'error');
  } finally {
    button.disabled = false;
    button.textContent = '安全保存配置';
  }
}

async function deleteCurrentProfile() {
  const id = $('#profileId').value;
  if (!id) return;
  if (!confirm('删除此 AI 配置及其系统凭据？此操作不会影响已有谱面。')) return;
  try {
    await request(`/api/profiles/${encodeURIComponent(id)}`, { method: 'DELETE' });
    clearProfileForm();
    await loadProfiles();
    showToast('AI 配置已删除。', 'success');
  } catch (error) { showToast(error.message, 'error'); }
}

function renderReview(review) {
  const findings = review.findings || [];
  $('#reviewResult').classList.remove('hidden');
  $('#reviewResult').innerHTML = `<h4>${escapeHtml(review.summary || 'AI 校对建议')}</h4>
    <p>配置：${escapeHtml(review.profile_name)} · 已发送 ${review.note_count_sent} 个音符事件 · ${review.audio_uploaded ? '音频已上传' : '未上传原始音频'}</p>
    ${findings.length ? `<ul>${findings.slice(0, 12).map((item) => `<li><strong>${escapeHtml((item.note_ids || []).join('、'))}</strong>：${escapeHtml(item.reason || '')}<br>建议：${escapeHtml(item.proposed_change || item.suggested_action || '人工确认')}</li>`).join('')}</ul>` : '<p>AI 未列出具体异常。仍建议以原音回听为准。</p>'}
    ${review.limitations?.length ? `<p>限制：${escapeHtml(review.limitations.join('；'))}</p>` : ''}`;
}

async function runReview() {
  if (!state.project) return;
  const profileId = $('#reviewProfile').value;
  if (!profileId) return showToast('请先在设置中保存并选择 AI 配置。', 'error');
  const button = $('#runReviewButton');
  button.disabled = true;
  button.textContent = '正在生成建议…';
  try {
    const result = await request(`/api/projects/${encodeURIComponent(state.project.id)}/review`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile_id: profileId, instruction: $('#reviewInstruction').value }),
    });
    renderReview(result.review);
    showToast('AI 校对建议已生成；不会自动改写谱面。', 'success');
  } catch (error) { showToast(error.message, 'error'); }
  finally { button.disabled = false; button.textContent = '生成校对建议'; }
}

function initialiseUpload() {
  const input = $('#audioInput');
  const zone = $('#dropZone');
  input.addEventListener('change', () => { $('#fileLabel').textContent = input.files[0]?.name || '选择或拖入音频文件'; });
  ['dragenter', 'dragover'].forEach((eventName) => zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.add('dragging'); }));
  ['dragleave', 'drop'].forEach((eventName) => zone.addEventListener(eventName, (event) => { event.preventDefault(); zone.classList.remove('dragging'); }));
  zone.addEventListener('drop', (event) => {
    const file = event.dataTransfer.files[0];
    if (!file) return;
    const transfer = new DataTransfer(); transfer.items.add(file); input.files = transfer.files;
    $('#fileLabel').textContent = file.name;
  });
}

function bindUI() {
  $('#uploadForm').addEventListener('submit', uploadAudio);
  $('#presetInput').addEventListener('change', (event) => applyPreset(event.target.value));
  $('#engineInput').addEventListener('change', () => {
    if ($('#engineInput').value === 'rosvot-rmvpe') refreshRosvotStatus();
  });
  $('#sensitivityInput').addEventListener('input', markCustomPreset);
  ['#minDurationInput', '#minConfidenceInput', '#mergeGapInput'].forEach((selector) => {
    $(selector).addEventListener('input', markCustomPreset);
  });
  $('#saveButton').addEventListener('click', saveProject);
  bindCanvasNavigation();
  $('#timeZoom').addEventListener('input', (event) => setZoom('time', event.target.value));
  $('#pitchZoom').addEventListener('input', (event) => setZoom('pitch', event.target.value));
  $('#timeZoomIn').addEventListener('click', () => setZoom('time', Number($('#timeZoom').value) + .5));
  $('#timeZoomOut').addEventListener('click', () => setZoom('time', Number($('#timeZoom').value) - .5));
  $('#pitchZoomIn').addEventListener('click', () => setZoom('pitch', Number($('#pitchZoom').value) + .25));
  $('#pitchZoomOut').addEventListener('click', () => setZoom('pitch', Number($('#pitchZoom').value) - .25));
  $('#fitTimeline').addEventListener('click', () => { state.timeZoom = 1; state.pitchZoom = 1; setZoom('time', 1); setZoom('pitch', 1); $('#spectrogramScroll').scrollLeft = 0; $('#spectrogramScroll').scrollTop = 0; });
  $('#tapTempoButton').addEventListener('click', toggleBeatCapture);
  $('#beatMode').addEventListener('change', (event) => {
    $('#tapTempoButton').textContent = state.captureActive ? '结束打拍（Esc）' : (event.target.value === 'digits' ? '开始数字拍号模式' : '开始打拍（空格）');
  });
  $('#meterSequence').addEventListener('change', () => { state.score.meter_sequence = parseMeterSequence(); renderSpectrogram(); });
  $('#quantizeBeats').addEventListener('click', () => quantizeAll('beats'));
  $('#quantizeNotes').addEventListener('click', () => quantizeAll('notes'));
  $('#keySelect').addEventListener('change', () => {
    if (!state.score) return;
    if (!$('#keySelect').value) state.score.key = { ...(state.score.key || {}), tonic: null, status: 'auto_suggestion' };
    else state.score.key = { ...(state.score.key || {}), tonic: $('#keySelect').value, mode: $('#keyMode').value, label: `${$('#keySelect').value} ${$('#keyMode').value}`, status: 'user_set' };
    renderNotation(); renderSpectrogram(); renderScoreContext();
  });
  $('#keyMode').addEventListener('change', () => {
    if (state.score?.key) { state.score.key.mode = $('#keyMode').value; state.score.key.status = 'user_set'; state.score.key.label = `${state.score.key.tonic || 'Auto'} ${$('#keyMode').value}`; renderNotation(); renderSpectrogram(); renderScoreContext(); }
  });
  $('#audioPlayer').addEventListener('timeupdate', () => {
    if (!state.score) return;
    const duration = Math.max(.15, Number(state.score.input.duration_seconds) || state.audioDuration || 1);
    const playhead = $('#spectrogramPlayhead');
    playhead.style.display = 'block';
    playhead.style.left = `${$('#spectrogramCanvas').clientWidth * ($('#audioPlayer').currentTime / duration)}px`;
  });
  $('#audioPlayer').addEventListener('loadedmetadata', () => {
    state.audioDuration = Number($('#audioPlayer').duration) || state.audioDuration;
    renderSpectrogram();
  });
  $('#clearTapsButton').addEventListener('click', () => {
    if (!state.score) return;
    state.score.beat_taps = [];
    state.score.beat_markers = [];
    state.score.tempo_mode = 'estimated';
    renderTapReadout();
    renderSpectrogram();
    showToast('打拍记录已清空；保存修订后生效。');
  });
  document.addEventListener('keydown', handleEditorKeydown, true);
  $('#exportButton').addEventListener('click', () => $('#exportPopover').classList.toggle('hidden'));
  $('#aiReviewButton').addEventListener('click', () => {
    document.querySelector('.ai-panel')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    $('#reviewProfile').focus();
  });
  $('#runReviewButton').addEventListener('click', runReview);
  $('#addNoteButton').addEventListener('click', () => {
    const notes = state.score?.notes;
    if (!notes?.length) return;
    const last = notes[notes.length - 1];
    const onset = Number(last.offset) + .08;
    notes.push({ id: `user-${Date.now()}`, onset, offset: onset + .35, raw_onset: onset, raw_offset: onset + .35, duration: .35, midi: last.midi, name: last.name, velocity: 88, confidence: .5, source: 'user', user_edited: true });
    renderNotation(); renderNotesTable(); renderSpectrogram();
  });
  $('#settingsButton').addEventListener('click', () => { clearProfileForm(); renderProfiles(); $('#settingsDialog').showModal(); });
  $('#settingsCloseButton').addEventListener('click', () => $('#settingsDialog').close());
  $('#settingsForm').addEventListener('submit', saveProfile);
  $('#deleteProfileButton').addEventListener('click', deleteCurrentProfile);
  initialiseUpload();
  applyPreset($('#presetInput').value);
  refreshRosvotStatus();
}

(async () => {
  bindUI();
  await loadProfiles();
  await loadRecentProjects();
})();
