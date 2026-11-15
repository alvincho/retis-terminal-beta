/** Pictures in a confined view: decoded here from bytes the view hands over, never fetched from anywhere.
 *
 * The view keeps no browser object. It gets back an id for each picture it puts, and draws pictures on a canvas
 * by stretching each one over a grid of points it supplies (a mesh): every grid cell maps its part of the picture
 * onto two triangles, so a flat picture can follow a curved surface such as a globe. The view never reads pixels
 * back, and nothing here takes a URL. */
const MAGIC = {'image/png': [0x89, 0x50, 0x4e, 0x47], 'image/jpeg': [0xff, 0xd8, 0xff], 'image/webp': [0x52, 0x49, 0x46, 0x46]};
export const LIMITS = Object.freeze({bytes: 220000, side: 1024, count: 384, pixels: 24000000, items: 512, cells: 32,
  triangles: 16384, canvasPixels: 16000000, coordinate: 10000000});

const finite = value => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= LIMITS.coordinate;

export class ImageStore {
  constructor() { this.images = new Map(); this.next = 0; this.pixels = 0; this.closed = false; }
  /* {data: base64, type} -> {id, width, height}. The bytes must be what their type says. */
  async put(value) {
    if (!value || typeof value !== 'object' || typeof value.data !== 'string' || typeof value.type !== 'string')
      throw Error('Give a picture as {data, type}.');
    const magic = MAGIC[value.type];
    if (!magic) throw Error('Pictures are PNG, JPEG or WebP.');
    if (value.data.length > Math.ceil(LIMITS.bytes / 3) * 4) throw Error('A picture is at most 220 KB.');
    let bytes;
    try { bytes = Uint8Array.from(atob(value.data), c => c.charCodeAt(0)); } catch { throw Error('Picture data is not base64.'); }
    if (!magic.every((b, i) => bytes[i] === b) || value.type === 'image/webp' && String.fromCharCode(...bytes.slice(8, 12)) !== 'WEBP')
      throw Error('The picture data is not of its stated type.');
    const bitmap = await createImageBitmap(new Blob([bytes], {type: value.type}));
    if (bitmap.width > LIMITS.side || bitmap.height > LIMITS.side) { bitmap.close(); throw Error('A picture is at most 1024 × 1024 pixels.'); }
    if (this.closed) { bitmap.close(); throw Error('The view has closed.'); }
    const id = 'image-' + (++this.next);
    this.images.set(id, bitmap); this.pixels += bitmap.width * bitmap.height;
    // The least recently drawn go first once the view holds too many.
    for (const [old, kept] of this.images) {
      if (this.images.size <= LIMITS.count && this.pixels <= LIMITS.pixels) break;
      if (old !== id) this.forget(old, kept);
    }
    return {id, width: bitmap.width, height: bitmap.height};
  }
  used(id) { const bitmap = this.images.get(id); if (bitmap) { this.images.delete(id); this.images.set(id, bitmap); } return bitmap; }
  forget(id, bitmap = this.images.get(id)) { if (!bitmap) return; this.images.delete(id); this.pixels -= bitmap.width * bitmap.height; bitmap.close(); }
  drop(ids) { for (const id of ids) if (typeof id === 'string') this.forget(id); }
  clear() { this.closed = true; for (const bitmap of this.images.values()) bitmap.close(); this.images.clear(); this.pixels = 0; }
}

function checked(spec) {
  if (!spec || typeof spec !== 'object' || !Array.isArray(spec.items) || spec.items.length > LIMITS.items)
    throw Error(`Draw up to ${LIMITS.items} pictures at once.`);
  const space = spec.space;
  if (space !== undefined && !(Array.isArray(space) && space.length === 2 && space.every(v => finite(v) && v > 0)))
    throw Error('A drawing space is [width, height].');
  let triangles = 0;
  for (const item of spec.items) {
    const {image, cols, rows, points, source, alpha} = item || {};
    if (typeof image !== 'string' || !Number.isInteger(cols) || !Number.isInteger(rows) || cols < 1 || rows < 1
        || cols > LIMITS.cells || rows > LIMITS.cells || !Array.isArray(points) || points.length !== 2 * (cols + 1) * (rows + 1))
      throw Error('Each picture names its image and a grid of (cols + 1) × (rows + 1) points.');
    for (let i = 0; i < points.length; i += 2)
      if (!(points[i] === null && points[i + 1] === null || finite(points[i]) && finite(points[i + 1])))
        throw Error('A grid point is a pair of numbers, or null where the picture is not drawn.');
    if (source !== undefined && !(Array.isArray(source) && source.length === 4 && source.every(finite) && source[2] > 0 && source[3] > 0))
      throw Error('A picture part is [x, y, width, height].');
    if (alpha !== undefined && !(typeof alpha === 'number' && alpha >= 0 && alpha <= 1)) throw Error('Opacity is from 0 to 1.');
    triangles += 2 * cols * rows;
  }
  if (triangles > LIMITS.triangles) throw Error(`Draw up to ${LIMITS.triangles} triangles at once.`);
  return spec;
}

/* One triangle of a picture: (u, v) picture pixels onto (x, y) canvas pixels, clipped to the triangle widened by a
 * pixel so neighbouring triangles overlap their soft clip edges and meet without a hairline. */
function triangle(ctx, bitmap, x0, y0, x1, y1, x2, y2, u0, v0, u1, v1, u2, v2) {
  const den = u0 * (v1 - v2) + u1 * (v2 - v0) + u2 * (v0 - v1);
  if (Math.abs(den) < 1e-9) return;
  const a = (x0 * (v1 - v2) + x1 * (v2 - v0) + x2 * (v0 - v1)) / den, b = (y0 * (v1 - v2) + y1 * (v2 - v0) + y2 * (v0 - v1)) / den;
  const c = (x0 * (u2 - u1) + x1 * (u0 - u2) + x2 * (u1 - u0)) / den, d = (y0 * (u2 - u1) + y1 * (u0 - u2) + y2 * (u1 - u0)) / den;
  const e = (x0 * (u1 * v2 - u2 * v1) + x1 * (u2 * v0 - u0 * v2) + x2 * (u0 * v1 - u1 * v0)) / den;
  const f = (y0 * (u1 * v2 - u2 * v1) + y1 * (u2 * v0 - u0 * v2) + y2 * (u0 * v1 - u1 * v0)) / den;
  const cx = (x0 + x1 + x2) / 3, cy = (y0 + y1 + y2) / 3;
  const out = (x, y) => { const dx = x - cx, dy = y - cy, n = Math.hypot(dx, dy) || 1; return [x + dx / n, y + dy / n]; };
  const [p, q] = out(x0, y0), [r, s] = out(x1, y1), [t, w] = out(x2, y2);
  ctx.save(); ctx.beginPath(); ctx.moveTo(p, q); ctx.lineTo(r, s); ctx.lineTo(t, w); ctx.closePath(); ctx.clip();
  ctx.transform(a, b, c, d, e, f); ctx.drawImage(bitmap, 0, 0); ctx.restore();
}

/* Draws pictures on `canvas`. Points are in the canvas's own pixels, or in `space` [width, height] fitted into the
 * canvas the way an SVG viewBox is (centred, uniformly scaled), so a view drawing SVG over the canvas can use its
 * own units. {drawn, missing}: missing names pictures this view no longer holds, to put again. */
export function drawMesh(canvas, spec, store) {
  const {items, space, clear} = checked(spec);
  const ratio = Math.min(4, Math.max(1, Number(globalThis.devicePixelRatio) || 1));
  const cssWidth = canvas.clientWidth || 0, cssHeight = canvas.clientHeight || 0;
  let width = Math.max(1, Math.round(cssWidth * ratio)), height = Math.max(1, Math.round(cssHeight * ratio));
  if (width * height > LIMITS.canvasPixels) { const k = Math.sqrt(LIMITS.canvasPixels / (width * height)); width = Math.floor(width * k); height = Math.floor(height * k); }
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const ctx = canvas.getContext('2d');
  let scale = width / Math.max(1, cssWidth || width), x0 = 0, y0 = 0;
  if (space) {
    const fit = Math.min(width / space[0], height / space[1]);
    x0 = (width - space[0] * fit) / 2; y0 = (height - space[1] * fit) / 2; scale = fit;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (clear !== false) ctx.clearRect(0, 0, width, height);
  const missing = [];
  let drawn = 0;
  for (const item of items) {
    const bitmap = store.used(item.image);
    if (!bitmap) { if (!missing.includes(item.image)) missing.push(item.image); continue; }
    const [sx, sy, sw, sh] = item.source || [0, 0, bitmap.width, bitmap.height];
    const {cols, rows, points} = item, X = i => x0 + points[2 * i] * scale, Y = i => y0 + points[2 * i + 1] * scale;
    ctx.globalAlpha = item.alpha ?? 1;
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
      const a = r * (cols + 1) + c, b = a + 1, d = a + cols + 1, e = d + 1;
      const u0 = sx + sw * c / cols, u1 = sx + sw * (c + 1) / cols, v0 = sy + sh * r / rows, v1 = sy + sh * (r + 1) / rows;
      const has = i => points[2 * i] !== null;
      if (has(a) && has(b) && has(e)) triangle(ctx, bitmap, X(a), Y(a), X(b), Y(b), X(e), Y(e), u0, v0, u1, v0, u1, v1);
      if (has(a) && has(e) && has(d)) triangle(ctx, bitmap, X(a), Y(a), X(e), Y(e), X(d), Y(d), u0, v0, u1, v1, u0, v1);
    }
    drawn++;
  }
  ctx.globalAlpha = 1;
  return {drawn, missing};
}
