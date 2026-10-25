// Data page labelling: thresholds, number formats and the per-record badges.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('classroom/static/metrics.js', 'utf8');
const h = (tag, attrs = {}, ...children) => ({tag, ...attrs, children: children.flat()});
const c = {h, num: v => Number.isFinite(Number(v)) ? Number(v) : 0, duration: s => Math.round(s) + ' 秒'};
vm.createContext(c);
vm.runInContext(source.slice(source.indexOf('const LV_OK'), source.indexOf('const MODE_SHORT')), c);
vm.runInContext(source.slice(source.indexOf('const sec ='), source.indexOf('function when')), c);
vm.runInContext(source.slice(source.indexOf('function longDuration'), source.indexOf('/* 数字滚动入场')), c);
vm.runInContext(source.slice(source.indexOf('function badges'), source.indexOf('function recordRow')), c);
const g = name => vm.runInContext(name, c);
for (const name of ['level', 'sec', 'secText', 'bytes']) c[name] = g(name);

// Same thresholds as the live bar label.
assert.equal(c.level(3.9), 'ok'); assert.equal(c.level(4), 'mid'); assert.equal(c.level(15), 'bad'); assert.equal(c.level(null), 'none');
assert.equal(c.sec(3.94), '3.9'); assert.equal(c.sec(14.6), '15'); assert.equal(c.secText(null), '—');
assert.equal(c.bytes(7.3 * 1024 ** 2), '7.3 MB'); assert.equal(c.bytes(10), '1 KB');
assert.equal(c.longDuration(42), '42 秒', 'short records keep seconds');
assert.equal(c.longDuration(6748), '1 小时 52 分');

const labels = r => c.badges(r).children.map(b => b.text);
assert.deepEqual(labels({legacy: true, first: {p50: 4}}), ['旧记录']);
assert.deepEqual(labels({legacy: false, live: true, first: null, audio_seconds: 42}), ['时长过短'], 'a short recent record is not called old');
assert.deepEqual(labels({legacy: false, live: true, first: null, audio_seconds: 600}), ['无句子计时']);
assert.deepEqual(labels({first: {p50: 2}, gaps: 0, recovered: 0, translation_errors: 0, behind_seconds: 0}), ['正常']);
assert.deepEqual(labels({first: {p50: 2}, gaps: 5, recovered: 1, translation_errors: 2, behind_seconds: 30, asr_lag: {max: 28}}),
  ['落后 28 秒', '翻译失败 2', '待核查 5', '恢复 1'], 'most serious first');
console.log('data page labels: thresholds, formats and badges');
