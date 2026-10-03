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
  if (field === 'midi') note.midi = Math.min(127, Math.max(0, Math.round(value)));
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
  const duration = Math.max(.15, Number(state.score.input.duration_seconds) || state.audioDuration || 1);
  const width = canvas.clientWidth || 1800;
  const height = canvas.clientHeight || 576;
  layer.innerHTML = '';
  state.score.notes.forEach((note, index) => {
    const block = document.createElement('div');
    block.className = `spectrogram-note ${note.confidence != null && Number(note.confidence) < .45 ? 'low' : ''} ${state.selectedNote === index ? 'selected' : ''}`;
    block.dataset.noteIndex = index;
    const left = Math.max(0, Number(note.onset) / duration * width);
    const right = Math.min(width, Number(note.offset) / duration * width);
    block.style.left = `${left}px`;
    block.style.width = `${Math.max(7, right - left)}px`;
    block.style.top = `${Math.max(0, Math.min(height - 12, (84 - Number(note.midi) - .5) * 12))}px`;
    block.textContent = midiLabel(Number(note.midi));
    block.title = `#${index + 1} ${midiLabel(Number(note.midi))} · ${Number(note.onset).toFixed(2)}–${Number(note.offset).toFixed(2)} 秒；拖动编辑`;
    block.addEventListener('pointerdown', beginNoteDrag);
    block.addEventListener('click', () => {
      if (state.noteDrag?.moved) return;
      state.selectedNote = index;
      renderSpectrogram(); renderNotation(); renderNotesTable();
      $('#audioPlayer').currentTime = Math.max(0, Number(note.onset));
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
  const width = $('#spectrogramCanvas').clientWidth || 1800;
  const seconds = dx / width * duration;
  const semitones = Math.round(-dy / 12);
  const note = state.score.notes[drag.index];
  if (drag.mode === 'start') note.onset = Math.max(0, Math.min(drag.offset - .03, drag.onset + seconds));
  else if (drag.mode === 'end') note.offset = Math.max(drag.onset + .03, drag.offset + seconds);
  else {
    const delta = Math.min(Math.max(seconds, -drag.onset), duration - drag.offset);
    note.onset = drag.onset + delta;
    note.offset = drag.offset + delta;
    note.midi = Math.max(36, Math.min(84, drag.midi + semitones));
  }
  note.duration = note.offset - note.onset;
  note.name = midiLabel(note.midi);
  note.user_edited = true;
  drag.element.style.left = `${note.onset / duration * width}px`;
  drag.element.style.width = `${Math.max(7, (note.offset - note.onset) / duration * width)}px`;
  drag.element.style.top = `${Math.max(0, Math.min(564, (84 - note.midi - .5) * 12))}px`;
  drag.element.textContent = midiLabel(note.midi);
}

function finishNoteDrag() {
  const drag = state.noteDrag;
  if (!drag) return;
  drag.element.removeEventListener('pointermove', moveNoteDrag);
  state.noteDrag = null;
  if (drag.moved) {
    renderSpectrogram(); renderNotation(); renderNotesTable();
    showToast('音符已修改；记得保存修订。', 'success');
  }
}

async function loadSpectrogram() {
  const image = $('#spectrogramImage');
  state.spectrogramReady = false;
  image.onload = () => { state.spectrogramReady = true; renderSpectrogram(); };
  image.onerror = () => showToast('声谱图生成失败；仍可用下方表格编辑音符。', 'error');
  image.src = `/api/projects/${encodeURIComponent(state.project.id)}/spectrogram?v=${Date.now()}`;
}

function renderTapReadout() {
  const taps = state.score?.beat_taps || [];
  const display = $('#tapReadout');
  if (!display) return;
  if (!taps.length) { display.textContent = '尚未打拍 · 播放音频后按空格，或点击“打拍”'; return; }
  const intervals = taps.slice(1).map((tap, index) => tap - taps[index]).filter((gap) => gap >= .25 && gap <= 2.5);
  const bpm = intervals.length ? 60 / (intervals.reduce((sum, gap) => sum + gap, 0) / intervals.length) : null;
  display.textContent = `已记录 ${taps.length} 个拍点${bpm ? ` · 估算 ${bpm.toFixed(1)} BPM` : ' · 至少再打一次以估算 BPM'}`;
  if (bpm && Number.isFinite(bpm)) { state.score.tempo_bpm = Number(bpm.toFixed(2)); state.score.tempo_mode = 'tap'; }
}

function recordBeatTap() {
  if (!state.project || !state.score) return;
  const player = $('#audioPlayer');
  if (player.paused) return showToast('请先播放音频，再按空格或点击“打拍”记录拍点。');
  const current = Number(player.currentTime.toFixed(4));
  const taps = state.score.beat_taps || (state.score.beat_taps = []);
  if (taps.length && current - taps[taps.length - 1] < .2) return;
  taps.push(current);
  renderTapReadout();
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
    `拍号：${score.time_signature?.value || '4/4'}（候选）`,
    `调性：${score.key?.label || '未确定'}（候选）`,
    score.engine.audio_sent_to_cloud ? '音频已外发' : '原始音频未外发',
  ].filter(Boolean).map((text) => `<span>${escapeHtml(text)}</span>`).join('');
  const engineLabel = score.engine.name === 'basic-pitch'
    ? 'Basic Pitch 候选音符'
    : score.engine.name === 'rosvot-rmvpe'
      ? 'ROSVOT + RMVPE 歌声转谱'
      : 'pYIN 本地回退引擎';
  $('#statusStrip').textContent = `${engineLabel} 已生成可编辑候选谱。${score.disclaimer || ''}`;
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
  $('#audioPlayer').addEventListener('timeupdate', () => {
    if (!state.score) return;
    const duration = Math.max(.15, Number(state.score.input.duration_seconds) || state.audioDuration || 1);
    const playhead = $('#spectrogramPlayhead');
    playhead.style.display = 'block';
    playhead.style.left = `${$('#spectrogramCanvas').clientWidth * ($('#audioPlayer').currentTime / duration)}px`;
  });
  $('#tapTempoButton').addEventListener('click', recordBeatTap);
  $('#clearTapsButton').addEventListener('click', () => {
    if (!state.score) return;
    state.score.beat_taps = [];
    state.score.tempo_mode = 'estimated';
    renderTapReadout();
    showToast('打拍记录已清空；保存修订后生效。');
  });
  document.addEventListener('keydown', (event) => {
    const target = event.target;
    const editing = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement || target?.isContentEditable;
    if (event.code === 'Space' && !editing && state.project) {
      event.preventDefault();
      recordBeatTap();
    }
  });
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
