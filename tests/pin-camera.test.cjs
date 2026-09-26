const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const pinMath = require('../pin-math.js');
const source = fs.readFileSync(path.join(__dirname, '../content.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../content.css'), 'utf8');

// ---------- pin-math unit tests ----------

test('parseCameraFromUrl extracts lat/lng/zoom from Maps URLs', () => {
  const cam = pinMath.parseCameraFromUrl('https://www.google.com/maps/place/Foo/@43.6552,-79.3807,17z/data=!4m1!3e0');
  assert.deepEqual(cam, { lat: 43.6552, lng: -79.3807, zoom: 17 });
  const cam2 = pinMath.parseCameraFromUrl('https://www.google.com/maps/@31.23,121.47,12.5z');
  assert.deepEqual(cam2, { lat: 31.23, lng: 121.47, zoom: 12.5 });
  assert.equal(pinMath.parseCameraFromUrl('https://www.google.com/maps/search/coffee'), null);
  assert.equal(pinMath.parseCameraFromUrl(''), null);
});

test('projectToViewport centers the camera on the canvas rect', () => {
  const rect = { left: 0, top: 0, width: 1000, height: 800 };
  const cam = { lat: 0, lng: 0, zoom: 10 };
  const pt = pinMath.projectToViewport(0, 0, cam, rect);
  assert.ok(Math.abs(pt.x - 500) < 1e-6, `x=${pt.x}`);
  assert.ok(Math.abs(pt.y - 400) < 1e-6, `y=${pt.y}`);
});

test('projectToViewport moves pins east/north correctly and scales with zoom', () => {
  const rect = { left: 0, top: 0, width: 1000, height: 800 };
  const cam = { lat: 0, lng: 0, zoom: 10 };
  const east = pinMath.projectToViewport(0, 1, cam, rect);
  assert.ok(east.x > 500, '1deg east should be right of center');
  assert.ok(Math.abs(east.y - 400) < 1, 'latitude unchanged');
  const north = pinMath.projectToViewport(1, 0, cam, rect);
  assert.ok(north.y < 400, '1deg north should be above center');
  // One zoom level doubles world pixels -> doubles screen offset
  const camZ11 = { lat: 0, lng: 0, zoom: 11 };
  const eastZ11 = pinMath.projectToViewport(0, 1, camZ11, rect);
  assert.ok(Math.abs((eastZ11.x - 500) - 2 * (east.x - 500)) < 1e-6, 'zoom+1 doubles offset');
});

test('interpolateCamera eases between timestamped cameras and clamps', () => {
  const a = { lat: 0, lng: 0, zoom: 15, t: 1000 };
  const b = { lat: 1, lng: 2, zoom: 16, t: 2000 };
  const mid = pinMath.interpolateCamera(a, b, 1500);
  assert.ok(Math.abs(mid.lat - 0.5) < 1e-9);
  assert.ok(Math.abs(mid.lng - 1) < 1e-9);
  assert.ok(Math.abs(mid.zoom - 15.5) < 1e-9);
  const before = pinMath.interpolateCamera(a, b, 500);
  assert.deepEqual([before.lat, before.lng, before.zoom], [0, 0, 15]);
  const after = pinMath.interpolateCamera(a, b, 2500);
  assert.deepEqual([after.lat, after.lng, after.zoom], [1, 2, 16]);
  assert.equal(pinMath.interpolateCamera(null, b, 1500).lat, 1);
});

test('sameCamera compares parsed cameras exactly', () => {
  assert.ok(pinMath.sameCamera({ lat: 1, lng: 2, zoom: 3 }, { lat: 1, lng: 2, zoom: 3 }));
  assert.ok(!pinMath.sameCamera({ lat: 1, lng: 2, zoom: 3 }, { lat: 1, lng: 2, zoom: 4 }));
  assert.ok(!pinMath.sameCamera(null, { lat: 1, lng: 2, zoom: 3 }));
});

test('rebaseDragDelta keeps pins visually still on a pure pan camera update', () => {
  const rect = { left: 0, top: 0, width: 1000, height: 800 };
  const camOld = { lat: 43.0, lng: -79.0, zoom: 15 };
  const camNew = { lat: 43.001, lng: -79.002, zoom: 15 };
  const pin = { lat: 43.0005, lng: -79.001 };
  const drag = { dx: 30, dy: -12 };
  const beforeX = pinMath.projectToViewport(pin.lat, pin.lng, camOld, rect).x + drag.dx;
  pinMath.rebaseDragDelta(drag, camOld, camNew, rect);
  const afterX = pinMath.projectToViewport(pin.lat, pin.lng, camNew, rect).x + drag.dx;
  assert.ok(Math.abs(beforeX - afterX) < 1e-6, `jump=${beforeX - afterX}px`);
});

test('haversineKm and shouldSmoothPan gate nearby navigation', () => {
  assert.ok(pinMath.haversineKm(43.65, -79.38, 43.65, -79.38) < 1e-9);
  // ~1.1km apart
  const d = pinMath.haversineKm(43.65, -79.38, 43.66, -79.38);
  assert.ok(d > 1 && d < 1.3, `d=${d}`);
  const cam = { lat: 43.65, lng: -79.38, zoom: 17 };
  assert.ok(pinMath.shouldSmoothPan(cam, 43.651, -79.381, 1.5));
  assert.ok(!pinMath.shouldSmoothPan(cam, 43.7, -79.38, 1.5));
  assert.ok(!pinMath.shouldSmoothPan(null, 43.651, -79.381, 1.5));
  assert.ok(!pinMath.shouldSmoothPan(cam, NaN, -79.381, 1.5));
});

// ---------- content.js / content.css structural tests ----------

test('content.js implements the camera-interpolated pin engine', () => {
  assert.match(source, /function rebuildPinElements/);
  assert.match(source, /function onCameraUpdate/);
  assert.match(source, /function pollMapCamera/);
  assert.match(source, /function wakePinLoop/);
  assert.match(source, /function pinTick/);
  assert.match(source, /function smoothDragPanTo/);
  assert.match(source, /function anchorNavigateTo/);
  assert.match(source, /function requestCameraBridge/);
  assert.match(source, /GREENOIL_CAM/);
  assert.match(source, /rebaseDragDelta/);
  assert.match(source, /injectCameraBridge/);
});

test('content.js no longer references the removed laggy/undefined pin code', () => {
  for (const name of [
    'renderWaypointMapPins', 'initZeroLatencyDragTracking', 'getMapCamera',
    'projectToCanvasPixels', 'ensureWaypointPinsOverlay', 'overlayDragInitialized',
    'syncNativePinColors', 'readMapCamera', 'updateAllPinCoordinates', 'currentMapCamera',
  ]) {
    assert.ok(!source.includes(name), `should not reference ${name}`);
  }
});

test('renderMisPins renders shield pins instead of wiping the overlay', () => {
  const start = source.indexOf('function renderMisPins');
  const end = source.indexOf('function inPagePanToLocation');
  const body = source.slice(start, end);
  assert.ok(!body.includes('removeAnyPinOverlays'), 'must not wipe the pin overlay');
  assert.ok(body.includes('rebuildPinElements'), 'must rebuild pins incl. MIS');
  assert.ok(source.includes('greenoil-mis-map-pin'), 'MIS pins need a distinct style hook');
});

test('visibleMapRect subtracts the open side panel; pointInRect clips', () => {
  const canvas = { left: 0, top: 0, width: 1600, height: 900 };
  const panel = { left: 0, top: 0, width: 400, height: 900 };
  const vis = pinMath.visibleMapRect(canvas, panel);
  assert.deepEqual([vis.left, vis.width], [400, 1200], 'panel width removed from the left');
  assert.equal(vis.height, 900);
  // Camera at visible center -> projects to visible center, not canvas center
  const cam = { lat: 0, lng: 0, zoom: 10 };
  const pt = pinMath.projectToViewport(0, 0, cam, vis);
  assert.ok(Math.abs(pt.x - 1000) < 1e-6, `visible center x=${pt.x}`);
  // No panel -> rect unchanged
  const vis2 = pinMath.visibleMapRect(canvas, null);
  assert.deepEqual([vis2.left, vis2.width], [0, 1600]);
  const tiny = pinMath.visibleMapRect(canvas, { left: 0, top: 0, width: 10, height: 900 });
  assert.deepEqual([tiny.left, tiny.width], [0, 1600], 'tiny slivers are not panels');
  // Clipping
  assert.ok(pinMath.pointInRect(1000, 450, vis, 48));
  assert.ok(!pinMath.pointInRect(100, 450, vis, 48), 'behind panel -> clipped');
  assert.ok(!pinMath.pointInRect(2000, 450, vis, 48), 'off-canvas -> clipped');
  assert.ok(pinMath.pointInRect(390, 450, vis, 48), 'margin keeps edge pins');
});

test('CSS keeps pins below Google panels, click-through, with MIS styles', () => {
  assert.ok(!css.includes('.greenoil-heading-waypoint-badge'), 'no heading waypoint badge');
  assert.match(css, /#greenoil-waypoint-pins-overlay/);
  assert.match(css, /#greenoil-waypoint-pin-layer/);
  assert.match(css, /\.greenoil-waypoint-map-pin/);
  assert.match(css, /\.greenoil-mis-map-pin/);
  assert.ok(!css.includes('.greenoil-pin-tooltip'), 'name tooltip removed (was the black bar + backdrop-filter jank)');
  assert.ok(!css.includes('.go-zoomed-out'), 'zoom label-hiding rule removed with the tooltip');
  const blurs = (css.match(/backdrop-filter/g) || []).length;
  assert.equal(blurs, 1, 'only the static MIS modal may keep backdrop blur, not per-frame pins');
  assert.match(css, /\.greenoil-pin-shield/);
  assert.ok(!/z-index:\s*998/.test(css), 'overlay must not sit above Google panels');
  assert.match(css, /pointer-events:\s*none\s*!important/);
});

test('pin engine projects against the visible map rect and clips off-map pins', () => {
  assert.ok(source.includes('visibleMapRect()'), 'pinTick/smooth-pan must use the panel-aware rect');
  assert.ok(source.includes('pointInRect'), 'off-map pins must be clipped, not floated over Google UI');
  assert.ok(!source.includes('greenoil-pin-tooltip'), 'no tooltip markup in pin templates');
});
